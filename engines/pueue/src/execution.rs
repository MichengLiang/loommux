//! Execution records, task identity, state projection, and terminal freezing.

use std::{collections::HashMap, path::Path};

use chrono::{DateTime, Utc};
use pueue_lib::{
    message::AddRequest,
    task::{Task, TaskResult, TaskStatus},
};
use serde::Serialize;
use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::output::{OutputCollector, OutputError};

const PUEUE_RUNTIME_ENVIRONMENT: [&str; 2] = ["PUEUE_GROUP", "PUEUE_WORKER_ID"];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionStatus {
    Locked,
    Stashed,
    Queued,
    Running,
    Paused,
    Completed,
    Failed,
    Killed,
    Cancelled,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BackendPreviousStatus {
    Stashed,
    Queued,
}

impl ExecutionStatus {
    #[must_use]
    pub const fn is_terminal(self) -> bool {
        matches!(
            self,
            Self::Completed | Self::Failed | Self::Killed | Self::Cancelled
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BackendPresence {
    Present,
    Missing,
    Modified,
    RemovedByAdapter,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureKind {
    NonzeroExit,
    FailedToSpawn,
    BackendIoError,
    DependencyFailed,
    Killed,
}

#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
pub struct ExecutionResult {
    pub exit_code: Option<i32>,
    pub failure_kind: Option<FailureKind>,
    pub error_summary: Option<&'static str>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskIdentityFingerprint([u8; 32]);

impl TaskIdentityFingerprint {
    #[must_use]
    pub fn from_add_request(request: &AddRequest) -> Self {
        fingerprint(
            &request.command,
            &request.path,
            &request.envs,
            &request.group,
            &request.dependencies,
            request.priority.unwrap_or_default(),
            request.label.as_deref(),
        )
    }

    #[must_use]
    pub fn from_task(task: &Task) -> Self {
        fingerprint(
            &task.original_command,
            &task.path,
            &task.envs,
            &task.group,
            &task.dependencies,
            task.priority,
            task.label.as_deref(),
        )
    }
}

#[derive(Debug)]
pub struct PueueExecution {
    pub execution: u64,
    pub(crate) task_id: usize,
    task_identity: TaskIdentityFingerprint,
    pub submitted_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
    pub enqueue_at: Option<DateTime<Utc>>,
    pub enqueued_at: Option<DateTime<Utc>>,
    pub started_at: Option<DateTime<Utc>>,
    pub backend_previous_status: Option<BackendPreviousStatus>,
    pub status: ExecutionStatus,
    pub result: Option<ExecutionResult>,
    pub(crate) full_output_requested: bool,
    pub(crate) output: OutputCollector,
    pub backend_presence: BackendPresence,
    pub output_error_kind: Option<OutputError>,
}

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum ObservationError {
    #[error("backend task is missing")]
    BackendTaskMissing,
    #[error("backend task identity changed")]
    BackendTaskModified,
    #[error("backend task status is incompatible with the protocol contract")]
    UnexpectedBackendResponse,
}

impl ObservationError {
    #[must_use]
    pub const fn kind(self) -> &'static str {
        match self {
            Self::BackendTaskMissing => "backend_task_missing",
            Self::BackendTaskModified => "backend_task_modified",
            Self::UnexpectedBackendResponse => "unexpected_backend_response",
        }
    }
}

impl PueueExecution {
    #[must_use]
    pub fn accepted(
        execution: u64,
        task_id: usize,
        request: &AddRequest,
        full_output_requested: bool,
        now: DateTime<Utc>,
    ) -> Self {
        Self {
            execution,
            task_id,
            task_identity: TaskIdentityFingerprint::from_add_request(request),
            submitted_at: now,
            updated_at: now,
            completed_at: None,
            enqueue_at: None,
            enqueued_at: None,
            started_at: None,
            backend_previous_status: None,
            status: ExecutionStatus::Queued,
            result: None,
            full_output_requested,
            output: OutputCollector::default(),
            backend_presence: BackendPresence::Present,
            output_error_kind: None,
        }
    }

    pub fn mark_missing(&mut self) -> ObservationError {
        self.backend_presence = BackendPresence::Missing;
        ObservationError::BackendTaskMissing
    }

    #[must_use]
    pub const fn full_output_requested(&self) -> bool {
        self.full_output_requested
    }

    #[must_use]
    pub const fn output(&self) -> &OutputCollector {
        &self.output
    }

    /// Refresh a nonterminal record after proving the daemon task still has the
    /// identity captured at accepted submission.
    ///
    /// # Errors
    ///
    /// Returns `backend_task_modified` and permanently stops observation when
    /// identity fields differ.
    pub fn refresh_from_task(
        &mut self,
        task: &Task,
        observed_at: DateTime<Utc>,
    ) -> Result<(), ObservationError> {
        if self.status.is_terminal() || self.backend_presence != BackendPresence::Present {
            return Ok(());
        }
        if self.task_identity != TaskIdentityFingerprint::from_task(task) {
            self.backend_presence = BackendPresence::Modified;
            return Err(ObservationError::BackendTaskModified);
        }
        self.project_status(&task.status)?;
        self.updated_at = observed_at;
        Ok(())
    }

    pub fn mark_cancelled(&mut self, observed_at: DateTime<Utc>) {
        self.output.freeze();
        self.status = ExecutionStatus::Cancelled;
        self.updated_at = observed_at;
        self.completed_at = Some(observed_at);
        self.backend_presence = BackendPresence::RemovedByAdapter;
        self.result = None;
    }

    fn project_status(&mut self, status: &TaskStatus) -> Result<(), ObservationError> {
        let backend_previous_status = match status {
            TaskStatus::Locked { previous_status } => match previous_status.as_ref() {
                TaskStatus::Stashed { .. } => Some(BackendPreviousStatus::Stashed),
                TaskStatus::Queued { .. } => Some(BackendPreviousStatus::Queued),
                _ => return Err(ObservationError::UnexpectedBackendResponse),
            },
            _ => None,
        };
        self.enqueue_at = None;
        self.enqueued_at = None;
        self.started_at = None;
        self.completed_at = None;
        self.backend_previous_status = backend_previous_status;
        self.result = None;
        match status {
            TaskStatus::Locked { .. } => {
                self.status = ExecutionStatus::Locked;
            }
            TaskStatus::Stashed { enqueue_at } => {
                self.status = ExecutionStatus::Stashed;
                self.enqueue_at = enqueue_at.as_ref().map(chrono::DateTime::to_utc);
            }
            TaskStatus::Queued { enqueued_at } => {
                self.status = ExecutionStatus::Queued;
                self.enqueued_at = Some(enqueued_at.to_utc());
            }
            TaskStatus::Running { enqueued_at, start } => {
                self.status = ExecutionStatus::Running;
                self.enqueued_at = Some(enqueued_at.to_utc());
                self.started_at = Some(start.to_utc());
            }
            TaskStatus::Paused { enqueued_at, start } => {
                self.status = ExecutionStatus::Paused;
                self.enqueued_at = Some(enqueued_at.to_utc());
                self.started_at = Some(start.to_utc());
            }
            TaskStatus::Done {
                enqueued_at,
                start,
                end,
                result,
            } => {
                self.enqueued_at = Some(enqueued_at.to_utc());
                self.started_at = Some(start.to_utc());
                self.completed_at = Some(end.to_utc());
                let (execution_status, projected) = project_result(result);
                self.status = execution_status;
                self.result = Some(projected);
            }
        }
        Ok(())
    }
}

fn project_result(result: &TaskResult) -> (ExecutionStatus, ExecutionResult) {
    match result {
        TaskResult::Success => (
            ExecutionStatus::Completed,
            ExecutionResult {
                exit_code: Some(0),
                ..ExecutionResult::default()
            },
        ),
        TaskResult::Failed(code) => (
            ExecutionStatus::Failed,
            ExecutionResult {
                exit_code: Some(*code),
                failure_kind: Some(FailureKind::NonzeroExit),
                error_summary: None,
            },
        ),
        TaskResult::FailedToSpawn(_) => (
            ExecutionStatus::Failed,
            ExecutionResult {
                exit_code: None,
                failure_kind: Some(FailureKind::FailedToSpawn),
                error_summary: Some("Pueue could not spawn the task process"),
            },
        ),
        TaskResult::Errored => (
            ExecutionStatus::Failed,
            ExecutionResult {
                exit_code: None,
                failure_kind: Some(FailureKind::BackendIoError),
                error_summary: None,
            },
        ),
        TaskResult::DependencyFailed => (
            ExecutionStatus::Failed,
            ExecutionResult {
                exit_code: None,
                failure_kind: Some(FailureKind::DependencyFailed),
                error_summary: None,
            },
        ),
        TaskResult::Killed => (
            ExecutionStatus::Killed,
            ExecutionResult {
                exit_code: None,
                failure_kind: Some(FailureKind::Killed),
                error_summary: None,
            },
        ),
    }
}

fn fingerprint(
    command: &str,
    path: &Path,
    envs: &HashMap<String, String>,
    group: &str,
    dependencies: &[usize],
    priority: i32,
    label: Option<&str>,
) -> TaskIdentityFingerprint {
    let mut bytes = Vec::new();
    push_field(&mut bytes, b"loommux-task-identity-v1");
    push_field(&mut bytes, command.as_bytes());
    push_field(&mut bytes, path.as_os_str().as_encoded_bytes());
    push_field(&mut bytes, group.as_bytes());
    push_field(&mut bytes, &priority.to_be_bytes());
    match label {
        None => push_field(&mut bytes, &[0]),
        Some(label) => {
            push_field(&mut bytes, &[1]);
            push_field(&mut bytes, label.as_bytes());
        }
    }

    let mut dependencies = dependencies.to_vec();
    dependencies.sort_unstable();
    push_field(&mut bytes, &dependencies.len().to_be_bytes());
    for dependency in dependencies {
        push_field(&mut bytes, &dependency.to_be_bytes());
    }
    let mut envs = envs
        .iter()
        .filter(|(key, _)| !PUEUE_RUNTIME_ENVIRONMENT.contains(&key.as_str()))
        .collect::<Vec<_>>();
    envs.sort_unstable_by(|left, right| left.0.cmp(right.0));
    push_field(&mut bytes, &envs.len().to_be_bytes());
    for (key, value) in envs {
        push_field(&mut bytes, key.as_bytes());
        push_field(&mut bytes, value.as_bytes());
    }
    TaskIdentityFingerprint(Sha256::digest(bytes).into())
}

fn push_field(target: &mut Vec<u8>, value: &[u8]) {
    target.extend_from_slice(&value.len().to_be_bytes());
    target.extend_from_slice(value);
}

#[cfg(test)]
mod tests {
    use std::{collections::HashMap, path::PathBuf};

    use chrono::{Local, Utc};
    use pueue_lib::{
        message::AddRequest,
        task::{Task, TaskResult, TaskStatus},
    };

    use super::{
        BackendPresence, BackendPreviousStatus, ExecutionStatus, FailureKind, ObservationError,
        PueueExecution, TaskIdentityFingerprint,
    };

    fn request() -> AddRequest {
        AddRequest {
            command: "printf ok".into(),
            path: PathBuf::from("/workspace"),
            envs: HashMap::from([("B".into(), "2".into()), ("A".into(), "1".into())]),
            start_immediately: false,
            stashed: false,
            group: "default".into(),
            enqueue_at: None,
            dependencies: vec![3, 1],
            priority: Some(0),
            label: Some("label".into()),
        }
    }

    fn task(status: TaskStatus) -> Task {
        let request = request();
        Task {
            id: 42,
            created_at: Local::now(),
            original_command: request.command.clone(),
            command: request.command,
            path: request.path,
            envs: request.envs,
            group: request.group,
            dependencies: request.dependencies,
            priority: 0,
            label: request.label,
            status,
        }
    }

    #[test]
    fn fingerprint_is_order_independent_and_ignores_runtime_environment() {
        let request = request();
        let mut task = task(TaskStatus::Queued {
            enqueued_at: Local::now(),
        });
        task.envs.insert("PUEUE_GROUP".into(), "default".into());
        task.envs.insert("PUEUE_WORKER_ID".into(), "0".into());
        assert_eq!(
            TaskIdentityFingerprint::from_add_request(&request),
            TaskIdentityFingerprint::from_task(&task)
        );
    }

    #[test]
    fn all_done_results_project_exhaustively() {
        let cases = [
            (
                TaskResult::Success,
                ExecutionStatus::Completed,
                None,
                Some(0),
            ),
            (
                TaskResult::Failed(7),
                ExecutionStatus::Failed,
                Some(FailureKind::NonzeroExit),
                Some(7),
            ),
            (
                TaskResult::FailedToSpawn("private task 42".into()),
                ExecutionStatus::Failed,
                Some(FailureKind::FailedToSpawn),
                None,
            ),
            (
                TaskResult::Errored,
                ExecutionStatus::Failed,
                Some(FailureKind::BackendIoError),
                None,
            ),
            (
                TaskResult::DependencyFailed,
                ExecutionStatus::Failed,
                Some(FailureKind::DependencyFailed),
                None,
            ),
            (
                TaskResult::Killed,
                ExecutionStatus::Killed,
                Some(FailureKind::Killed),
                None,
            ),
        ];
        for (result, expected_status, expected_failure, expected_exit_code) in cases {
            let now = Local::now();
            let mut execution = PueueExecution::accepted(1, 42, &request(), false, Utc::now());
            execution
                .refresh_from_task(
                    &task(TaskStatus::Done {
                        enqueued_at: now,
                        start: now,
                        end: now,
                        result,
                    }),
                    Utc::now(),
                )
                .unwrap();
            assert_eq!(execution.status, expected_status);
            let result = execution.result.unwrap();
            assert_eq!(result.failure_kind, expected_failure);
            assert_eq!(result.exit_code, expected_exit_code);
        }
    }

    #[test]
    fn terminal_state_is_frozen_and_modified_identity_is_not_projected() {
        let mut execution = PueueExecution::accepted(1, 42, &request(), false, Utc::now());
        let now = Local::now();
        execution
            .refresh_from_task(
                &task(TaskStatus::Done {
                    enqueued_at: now,
                    start: now,
                    end: now,
                    result: TaskResult::Success,
                }),
                Utc::now(),
            )
            .unwrap();
        let terminal_updated_at = execution.updated_at;
        execution
            .refresh_from_task(
                &task(TaskStatus::Queued {
                    enqueued_at: Local::now(),
                }),
                Utc::now(),
            )
            .unwrap();
        assert_eq!(execution.status, ExecutionStatus::Completed);
        assert_eq!(execution.updated_at, terminal_updated_at);

        let mut active = PueueExecution::accepted(2, 43, &request(), false, Utc::now());
        let mut modified = task(TaskStatus::Running {
            enqueued_at: now,
            start: now,
        });
        modified.original_command = "other".into();
        assert_eq!(
            active
                .refresh_from_task(&modified, Utc::now())
                .unwrap_err()
                .kind(),
            "backend_task_modified"
        );
        assert_eq!(active.backend_presence, BackendPresence::Modified);
        assert_eq!(active.status, ExecutionStatus::Queued);
    }

    #[test]
    fn identity_fingerprint_rejects_every_authored_field_change() {
        let now = Local::now();
        let status = || TaskStatus::Queued { enqueued_at: now };
        let mut mutations = Vec::new();

        let mut command = task(status());
        command.original_command = "other".into();
        mutations.push(command);
        let mut path = task(status());
        path.path = PathBuf::from("/other");
        mutations.push(path);
        let mut environment = task(status());
        environment.envs.insert("OTHER".into(), "value".into());
        mutations.push(environment);
        let mut group = task(status());
        group.group = "other".into();
        mutations.push(group);
        let mut dependencies = task(status());
        dependencies.dependencies.push(99);
        mutations.push(dependencies);
        let mut priority = task(status());
        priority.priority = 1;
        mutations.push(priority);
        let mut label = task(status());
        label.label = Some("other".into());
        mutations.push(label);

        for modified in mutations {
            let mut execution = PueueExecution::accepted(1, 42, &request(), false, Utc::now());
            assert_eq!(
                execution.refresh_from_task(&modified, Utc::now()),
                Err(ObservationError::BackendTaskModified)
            );
            assert_eq!(execution.backend_presence, BackendPresence::Modified);
            assert_eq!(execution.status, ExecutionStatus::Queued);
        }

        let mut request_without_label = request();
        request_without_label.label = None;
        let mut empty_label = task(status());
        empty_label.label = Some(String::new());
        let mut execution =
            PueueExecution::accepted(1, 42, &request_without_label, false, Utc::now());
        assert_eq!(
            execution.refresh_from_task(&empty_label, Utc::now()),
            Err(ObservationError::BackendTaskModified)
        );
    }

    #[test]
    fn locked_previous_status_is_narrow_and_invalid_variants_are_rejected() {
        let mut queued = PueueExecution::accepted(1, 42, &request(), false, Utc::now());
        queued
            .refresh_from_task(
                &task(TaskStatus::Locked {
                    previous_status: Box::new(TaskStatus::Queued {
                        enqueued_at: Local::now(),
                    }),
                }),
                Utc::now(),
            )
            .unwrap();
        assert_eq!(
            queued.backend_previous_status,
            Some(BackendPreviousStatus::Queued)
        );

        let mut invalid = PueueExecution::accepted(2, 43, &request(), false, Utc::now());
        let prior_updated_at = invalid.updated_at;
        assert_eq!(
            invalid
                .refresh_from_task(
                    &task(TaskStatus::Locked {
                        previous_status: Box::new(TaskStatus::Running {
                            enqueued_at: Local::now(),
                            start: Local::now(),
                        }),
                    }),
                    Utc::now(),
                )
                .unwrap_err(),
            ObservationError::UnexpectedBackendResponse
        );
        assert_eq!(invalid.status, ExecutionStatus::Queued);
        assert_eq!(invalid.updated_at, prior_updated_at);
    }

    #[test]
    fn state_projection_clears_fields_that_do_not_apply_to_the_new_state() {
        let now = Local::now();
        let mut execution = PueueExecution::accepted(1, 42, &request(), false, Utc::now());
        execution
            .refresh_from_task(
                &task(TaskStatus::Stashed {
                    enqueue_at: Some(now),
                }),
                Utc::now(),
            )
            .unwrap();
        assert!(execution.enqueue_at.is_some());
        assert!(execution.enqueued_at.is_none());

        execution
            .refresh_from_task(
                &task(TaskStatus::Running {
                    enqueued_at: now,
                    start: now,
                }),
                Utc::now(),
            )
            .unwrap();
        assert_eq!(execution.status, ExecutionStatus::Running);
        assert!(execution.enqueue_at.is_none());
        assert!(execution.enqueued_at.is_some());
        assert!(execution.started_at.is_some());

        execution
            .refresh_from_task(
                &task(TaskStatus::Paused {
                    enqueued_at: now,
                    start: now,
                }),
                Utc::now(),
            )
            .unwrap();
        assert_eq!(execution.status, ExecutionStatus::Paused);
        assert!(execution.enqueue_at.is_none());
        assert!(execution.enqueued_at.is_some());
        assert!(execution.started_at.is_some());

        execution
            .refresh_from_task(&task(TaskStatus::Stashed { enqueue_at: None }), Utc::now())
            .unwrap();
        assert_eq!(execution.status, ExecutionStatus::Stashed);
        assert!(execution.enqueue_at.is_none());
        assert!(execution.enqueued_at.is_none());
        assert!(execution.started_at.is_none());
        assert!(execution.completed_at.is_none());
        assert!(execution.backend_previous_status.is_none());
        assert!(execution.result.is_none());

        execution
            .refresh_from_task(
                &task(TaskStatus::Locked {
                    previous_status: Box::new(TaskStatus::Queued { enqueued_at: now }),
                }),
                Utc::now(),
            )
            .unwrap();
        assert_eq!(execution.status, ExecutionStatus::Locked);
        assert_eq!(
            execution.backend_previous_status,
            Some(BackendPreviousStatus::Queued)
        );
        assert!(execution.enqueue_at.is_none());
        assert!(execution.enqueued_at.is_none());
        assert!(execution.started_at.is_none());
    }

    #[test]
    fn observation_error_kinds_and_execution_flags_are_stable() {
        let errors = [
            (ObservationError::BackendTaskMissing, "backend_task_missing"),
            (
                ObservationError::BackendTaskModified,
                "backend_task_modified",
            ),
            (
                ObservationError::UnexpectedBackendResponse,
                "unexpected_backend_response",
            ),
        ];
        for (error, expected) in errors {
            assert_eq!(error.kind(), expected);
        }

        let ordinary = PueueExecution::accepted(1, 42, &request(), false, Utc::now());
        let complete = PueueExecution::accepted(2, 43, &request(), true, Utc::now());
        assert!(!ordinary.full_output_requested());
        assert!(complete.full_output_requested());
    }
}
