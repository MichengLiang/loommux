//! Adapter-local execution namespace and tool orchestration.

use std::{
    collections::{BTreeMap, HashMap},
    env,
    time::Duration,
};

use chrono::Utc;
use pueue_lib::{
    log::get_log_path,
    message::{AddRequest, KillRequest, TaskSelection},
    state::State,
    task::{TaskResult, TaskStatus},
};
use thiserror::Error;

use crate::{
    deadline::{from_now, positive_duration},
    directive::{DirectiveError, PreparedRunShell, prepare_run_shell},
    execution::{BackendPresence, PueueExecution},
    execution::{ExecutionStatus, ObservationError},
    output::{OutputError, OutputRead, OutputSearch, SearchRequest},
    pueue_gateway::{Gateway, GatewayError, PueueGateway},
    workspace::WorkspaceResolution,
};

#[derive(Debug, Error, Clone, Copy, PartialEq, Eq)]
pub enum AdapterError {
    #[error("execution namespace is exhausted")]
    ExecutionOverflow,
    #[error("Pueue daemon is unavailable")]
    BackendUnavailable,
    #[error("Pueue exchange timed out")]
    BackendTimeout,
    #[error("Pueue protocol is incompatible")]
    BackendProtocolIncompatible,
    #[error("Pueue rejected the operation")]
    BackendRequestRejected,
    #[error("submission outcome is unknown")]
    SubmissionOutcomeUnknown,
    #[error("Pueue daemon returned an unexpected response")]
    UnexpectedBackendResponse,
    #[error("invalid shell submission")]
    InvalidDirective(DirectiveError),
    #[error("execution does not exist")]
    ExecutionNotFound,
    #[error("backend task is missing")]
    BackendTaskMissing,
    #[error("backend task identity changed")]
    BackendTaskModified,
    #[error("output refresh failed")]
    Output(OutputError),
    #[error("timeout is invalid")]
    InvalidTimeout,
    #[error("cancellation is blocked by backend edit")]
    CancelBlocked,
    #[error("cancellation was rejected")]
    CancelRejected,
    #[error("cancellation outcome is unknown")]
    CancelOutcomeUnknown,
}

impl AdapterError {
    #[must_use]
    pub const fn kind(self) -> &'static str {
        match self {
            Self::ExecutionOverflow => "execution_overflow",
            Self::BackendUnavailable => "backend_unavailable",
            Self::BackendTimeout => "backend_timeout",
            Self::BackendProtocolIncompatible => "backend_protocol_incompatible",
            Self::BackendRequestRejected => "backend_request_rejected",
            Self::SubmissionOutcomeUnknown => "submission_outcome_unknown",
            Self::UnexpectedBackendResponse => "unexpected_backend_response",
            Self::InvalidDirective(error) => error.kind(),
            Self::ExecutionNotFound => "execution_not_found",
            Self::BackendTaskMissing => "backend_task_missing",
            Self::BackendTaskModified => "backend_task_modified",
            Self::Output(error) => error.kind(),
            Self::InvalidTimeout => "invalid_timeout",
            Self::CancelBlocked => "cancel_blocked",
            Self::CancelRejected => "cancel_rejected",
            Self::CancelOutcomeUnknown => "cancel_outcome_unknown",
        }
    }
}

impl From<DirectiveError> for AdapterError {
    fn from(error: DirectiveError) -> Self {
        Self::InvalidDirective(error)
    }
}

impl From<GatewayError> for AdapterError {
    fn from(error: GatewayError) -> Self {
        match error {
            GatewayError::BackendUnavailable => Self::BackendUnavailable,
            GatewayError::BackendTimeout => Self::BackendTimeout,
            GatewayError::BackendProtocolIncompatible => Self::BackendProtocolIncompatible,
            GatewayError::BackendRequestRejected => Self::BackendRequestRejected,
            GatewayError::AddOutcomeUnknown => Self::SubmissionOutcomeUnknown,
            GatewayError::UnexpectedBackendResponse => Self::UnexpectedBackendResponse,
            GatewayError::RemoveOutcomeUnknown | GatewayError::KillOutcomeUnknown => {
                Self::CancelOutcomeUnknown
            }
        }
    }
}

impl From<OutputError> for AdapterError {
    fn from(error: OutputError) -> Self {
        Self::Output(error)
    }
}

impl From<ObservationError> for AdapterError {
    fn from(error: ObservationError) -> Self {
        match error {
            ObservationError::BackendTaskMissing => Self::BackendTaskMissing,
            ObservationError::BackendTaskModified => Self::BackendTaskModified,
            ObservationError::UnexpectedBackendResponse => Self::UnexpectedBackendResponse,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaitOutcome {
    Terminal,
    DeadlineElapsed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CancelOutcome {
    Terminal,
    Requested,
    AlreadyTerminal,
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct RefreshReport {
    pub backend_missing_executions: Vec<u64>,
    pub backend_modified_executions: Vec<u64>,
}

#[derive(Debug)]
pub struct PueueMcpAdapter<G = PueueGateway> {
    next_execution: u64,
    recent_execution: Option<u64>,
    executions: BTreeMap<u64, PueueExecution>,
    gateway: G,
    workspace: WorkspaceResolution,
}

impl<G: Gateway> PueueMcpAdapter<G> {
    #[must_use]
    pub const fn new(gateway: G, workspace: WorkspaceResolution) -> Self {
        Self {
            next_execution: 1,
            recent_execution: None,
            executions: BTreeMap::new(),
            gateway,
            workspace,
        }
    }

    /// Submit a validated request and publish its local execution only after a
    /// typed `AddedTask` response establishes the private task binding.
    ///
    /// # Errors
    ///
    /// Returns a stable gateway error or `execution_overflow`; rejected and
    /// uncertain submissions do not consume an execution number.
    pub async fn run_shell(
        &mut self,
        source: &str,
    ) -> Result<(u64, PreparedRunShell), AdapterError> {
        let prepared = prepare_run_shell(source)?;
        let request = build_add_request(&prepared.script, &self.workspace);
        let execution = self
            .submit_prepared(request, prepared.full_output_requested)
            .await?;
        Ok((execution, prepared))
    }

    async fn submit_prepared(
        &mut self,
        request: AddRequest,
        full_output_requested: bool,
    ) -> Result<u64, AdapterError> {
        let execution = self.next_execution;
        let next_execution = execution
            .checked_add(1)
            .ok_or(AdapterError::ExecutionOverflow)?;
        let response = self.gateway.add(request.clone()).await?;
        let record = PueueExecution::accepted(
            execution,
            response.task_id,
            &request,
            full_output_requested,
            Utc::now(),
        );
        if self.executions.insert(execution, record).is_some() {
            return Err(AdapterError::ExecutionOverflow);
        }
        self.next_execution = next_execution;
        self.recent_execution = Some(execution);
        Ok(execution)
    }

    #[must_use]
    pub const fn workspace(&self) -> &WorkspaceResolution {
        &self.workspace
    }

    #[must_use]
    pub const fn recent_execution(&self) -> Option<u64> {
        self.recent_execution
    }

    #[must_use]
    pub fn execution(&self, execution: u64) -> Option<&PueueExecution> {
        self.executions.get(&execution)
    }

    #[must_use]
    pub fn active_executions(&self) -> Vec<u64> {
        self.executions
            .iter()
            .filter_map(|(execution, record)| {
                (!record.status.is_terminal()
                    && record.backend_presence == BackendPresence::Present)
                    .then_some(*execution)
            })
            .collect()
    }

    #[must_use]
    pub fn execution_count(&self) -> usize {
        self.executions.len()
    }

    /// Refresh all local nonterminal records from one daemon snapshot.
    ///
    /// # Errors
    ///
    /// Returns a gateway failure while preserving the last local snapshot.
    pub async fn refresh_all(&mut self) -> Result<RefreshReport, AdapterError> {
        let state = self.gateway.status().await?;
        let report = self.refresh_from_snapshot(&state)?;
        for record in self.executions.values_mut() {
            if !record.status.is_terminal() || record.output.finalized() {
                continue;
            }
            let Some(task) = state.tasks.get(&record.task_id) else {
                continue;
            };
            let log_expected = matches!(task.status, TaskStatus::Done { ref result, .. } if !matches!(result, TaskResult::FailedToSpawn(_) | TaskResult::DependencyFailed));
            let path = get_log_path(record.task_id, self.gateway.pueue_directory());
            match record.output.refresh(&path, true, log_expected) {
                Ok(()) => record.output_error_kind = None,
                Err(error) => record.output_error_kind = Some(error),
            }
        }
        Ok(report)
    }

    /// Apply one typed daemon snapshot to all nonterminal local records.
    ///
    /// # Errors
    ///
    /// Returns `unexpected_backend_response` when a typed status violates the
    /// narrower Pueue lifecycle invariant used by the public projection.
    pub fn refresh_from_snapshot(&mut self, state: &State) -> Result<RefreshReport, AdapterError> {
        let mut report = RefreshReport::default();
        let observed_at = Utc::now();
        for (execution, record) in &mut self.executions {
            if record.status.is_terminal() || record.backend_presence != BackendPresence::Present {
                continue;
            }
            let Some(task) = state.tasks.get(&record.task_id) else {
                record.mark_missing();
                report.backend_missing_executions.push(*execution);
                continue;
            };
            match record.refresh_from_task(task, observed_at) {
                Ok(()) => {}
                Err(ObservationError::BackendTaskModified) => {
                    report.backend_modified_executions.push(*execution);
                }
                Err(error) => return Err(error.into()),
            }
        }
        Ok(report)
    }

    /// Refresh one execution's identity, lifecycle, and append-only output.
    ///
    /// # Errors
    ///
    /// Returns stable execution, backend-presence, gateway, or output errors.
    pub async fn observe_execution(&mut self, execution: u64) -> Result<(), AdapterError> {
        let record = self
            .executions
            .get(&execution)
            .ok_or(AdapterError::ExecutionNotFound)?;
        if record.status.is_terminal() {
            return Ok(());
        }
        match record.backend_presence {
            BackendPresence::Missing => return Err(AdapterError::BackendTaskMissing),
            BackendPresence::Modified => return Err(AdapterError::BackendTaskModified),
            BackendPresence::Present => {}
            BackendPresence::RemovedByAdapter => return Ok(()),
        }
        let state = self.gateway.status().await?;
        let record = self
            .executions
            .get_mut(&execution)
            .ok_or(AdapterError::ExecutionNotFound)?;
        let Some(task) = state.tasks.get(&record.task_id) else {
            return Err(record.mark_missing().into());
        };
        record.refresh_from_task(task, Utc::now())?;
        let terminal = record.status.is_terminal();
        let log_expected = matches!(
            task.status,
            TaskStatus::Running { .. } | TaskStatus::Paused { .. }
        ) || matches!(task.status, TaskStatus::Done { ref result, .. } if !matches!(result, TaskResult::FailedToSpawn(_) | TaskResult::DependencyFailed));
        let log_path = get_log_path(record.task_id, self.gateway.pueue_directory());
        match record.output.refresh(&log_path, terminal, log_expected) {
            Ok(()) => record.output_error_kind = None,
            Err(error) => {
                record.output_error_kind = Some(error);
                if !terminal {
                    return Err(error.into());
                }
            }
        }
        Ok(())
    }

    /// Observe until terminal state or a monotonic deadline without limiting task runtime.
    ///
    /// # Errors
    ///
    /// Returns `invalid_timeout` when seconds cannot form a positive monotonic
    /// deadline and forwards stable observation failures.
    pub async fn wait(
        &mut self,
        execution: u64,
        timeout_seconds: f64,
    ) -> Result<WaitOutcome, AdapterError> {
        let duration = positive_duration(timeout_seconds).ok_or(AdapterError::InvalidTimeout)?;
        let deadline = from_now(duration).ok_or(AdapterError::InvalidTimeout)?;
        loop {
            self.observe_execution(execution).await?;
            if self
                .executions
                .get(&execution)
                .is_some_and(|record| record.status.is_terminal())
            {
                return Ok(WaitOutcome::Terminal);
            }
            let now = tokio::time::Instant::now();
            if now >= deadline {
                return Ok(WaitOutcome::DeadlineElapsed);
            }
            tokio::time::sleep((deadline - now).min(Duration::from_secs(2))).await;
        }
    }

    /// Apply state-sensitive control to exactly one private backend task binding.
    ///
    /// # Errors
    ///
    /// Returns stable observation, blocked, rejected, or unknown-outcome errors.
    pub async fn cancel(&mut self, execution: u64) -> Result<CancelOutcome, AdapterError> {
        if let Err(error) = self.observe_execution(execution).await {
            let remove_candidate = self.executions.get(&execution).is_some_and(|record| {
                matches!(
                    record.status,
                    ExecutionStatus::Queued | ExecutionStatus::Stashed
                )
            });
            // Remove destroys the only backend log copy; Kill leaves it available for a later refresh.
            if !matches!(error, AdapterError::Output(_)) || remove_candidate {
                return Err(error);
            }
        }
        let record = self
            .executions
            .get(&execution)
            .ok_or(AdapterError::ExecutionNotFound)?;
        if record.status.is_terminal() {
            return Ok(CancelOutcome::AlreadyTerminal);
        }
        let task_id = record.task_id;
        match record.status {
            ExecutionStatus::Queued | ExecutionStatus::Stashed => {
                match self.gateway.remove(vec![task_id]).await {
                    Ok(()) => {
                        self.executions
                            .get_mut(&execution)
                            .ok_or(AdapterError::ExecutionNotFound)?
                            .mark_cancelled(Utc::now());
                        return Ok(CancelOutcome::Terminal);
                    }
                    Err(GatewayError::BackendRequestRejected) => {}
                    Err(error) => return Err(error.into()),
                }
            }
            ExecutionStatus::Running | ExecutionStatus::Paused => {
                let request = KillRequest {
                    tasks: TaskSelection::TaskIds(vec![task_id]),
                    signal: None,
                };
                match self.gateway.kill(request).await {
                    Ok(()) => return Ok(CancelOutcome::Requested),
                    Err(GatewayError::BackendRequestRejected) => {}
                    Err(error) => return Err(error.into()),
                }
            }
            ExecutionStatus::Locked => return Err(AdapterError::CancelBlocked),
            ExecutionStatus::Completed
            | ExecutionStatus::Failed
            | ExecutionStatus::Killed
            | ExecutionStatus::Cancelled => return Ok(CancelOutcome::AlreadyTerminal),
        }
        match self.observe_execution(execution).await {
            Ok(()) | Err(AdapterError::Output(_)) => {
                if self
                    .executions
                    .get(&execution)
                    .is_some_and(|record| record.status.is_terminal())
                {
                    Ok(CancelOutcome::AlreadyTerminal)
                } else {
                    Err(AdapterError::CancelRejected)
                }
            }
            Err(AdapterError::BackendTaskMissing) => Err(AdapterError::CancelOutcomeUnknown),
            Err(error) => Err(error),
        }
    }

    /// Refresh and read the current combined transcript.
    ///
    /// # Errors
    ///
    /// Returns stable observation, range, or clipping errors.
    pub async fn read_output(
        &mut self,
        execution: u64,
        line_range: Option<&str>,
        max_chars: Option<usize>,
    ) -> Result<OutputRead, AdapterError> {
        self.observe_execution(execution).await?;
        self.executions
            .get(&execution)
            .ok_or(AdapterError::ExecutionNotFound)?
            .output()
            .log()
            .read(line_range, max_chars)
            .map_err(Into::into)
    }

    /// Refresh and search the current combined transcript.
    ///
    /// # Errors
    ///
    /// Returns stable observation, query, context, or clipping errors.
    pub async fn search_output(
        &mut self,
        execution: u64,
        request: &SearchRequest<'_>,
    ) -> Result<OutputSearch, AdapterError> {
        self.observe_execution(execution).await?;
        self.executions
            .get(&execution)
            .ok_or(AdapterError::ExecutionNotFound)?
            .output()
            .log()
            .search(request)
            .map_err(Into::into)
    }
}

fn build_add_request(script: &str, workspace: &WorkspaceResolution) -> AddRequest {
    let mut envs = env::vars().collect::<HashMap<_, _>>();
    for key in ["CLICOLOR", "CLICOLOR_FORCE", "FORCE_COLOR"] {
        envs.remove(key);
    }
    for (key, value) in [
        ("NO_COLOR", "1"),
        ("PY_COLORS", "0"),
        ("PAGER", "cat"),
        ("GIT_PAGER", "cat"),
        ("SYSTEMD_PAGER", "cat"),
    ] {
        envs.insert(key.into(), value.into());
    }
    AddRequest {
        command: script.into(),
        path: workspace.workspace.clone(),
        envs,
        start_immediately: false,
        stashed: false,
        group: "default".into(),
        enqueue_at: None,
        dependencies: Vec::new(),
        priority: None,
        label: None,
    }
}

#[cfg(test)]
mod tests {
    use std::{
        collections::{HashMap, VecDeque},
        fs,
        path::{Path, PathBuf},
        sync::{
            Arc, Mutex as StdMutex,
            atomic::{AtomicUsize, Ordering},
        },
        time::Duration,
    };

    use chrono::Local;
    use proptest::prelude::*;
    use pueue_lib::{
        log::get_log_path,
        message::{AddRequest, AddedTaskResponse, KillRequest, TaskSelection},
        state::State,
        task::{Task, TaskResult, TaskStatus},
    };

    use crate::{
        output::{OutputError, QueryMode, SearchRequest},
        pueue_gateway::{Gateway, GatewayError},
        result::execution_observation,
        workspace::{WorkspaceResolution, WorkspaceResolutionSource, resolve_workspace},
    };

    use super::{
        AdapterError, BackendPresence, CancelOutcome, ExecutionStatus, PueueMcpAdapter,
        WaitOutcome, build_add_request,
    };

    type FakeStatusResult = Result<Vec<(usize, TaskStatus)>, GatewayError>;
    type FakeTaskResult = Result<Vec<Task>, GatewayError>;

    #[derive(Debug, Clone)]
    struct FakeGateway {
        add_results: Arc<StdMutex<VecDeque<Result<AddedTaskResponse, GatewayError>>>>,
        add_calls: Arc<AtomicUsize>,
        requests: Arc<StdMutex<HashMap<usize, AddRequest>>>,
        status_results: Arc<StdMutex<VecDeque<FakeStatusResult>>>,
        task_results: Arc<StdMutex<VecDeque<FakeTaskResult>>>,
        remove_results: Arc<StdMutex<VecDeque<Result<(), GatewayError>>>>,
        kill_results: Arc<StdMutex<VecDeque<Result<(), GatewayError>>>>,
        remove_calls: Arc<StdMutex<Vec<Vec<usize>>>>,
        kill_calls: Arc<StdMutex<Vec<KillRequest>>>,
        pueue_directory: PathBuf,
    }

    impl FakeGateway {
        fn new(results: impl IntoIterator<Item = Result<usize, GatewayError>>) -> Self {
            Self {
                add_results: Arc::new(StdMutex::new(
                    results
                        .into_iter()
                        .map(|result| {
                            result.map(|task_id| AddedTaskResponse {
                                task_id,
                                enqueue_at: None,
                                group_is_paused: false,
                            })
                        })
                        .collect(),
                )),
                add_calls: Arc::new(AtomicUsize::new(0)),
                requests: Arc::new(StdMutex::new(HashMap::new())),
                status_results: Arc::new(StdMutex::new(VecDeque::new())),
                task_results: Arc::new(StdMutex::new(VecDeque::new())),
                remove_results: Arc::new(StdMutex::new(VecDeque::new())),
                kill_results: Arc::new(StdMutex::new(VecDeque::new())),
                remove_calls: Arc::new(StdMutex::new(Vec::new())),
                kill_calls: Arc::new(StdMutex::new(Vec::new())),
                pueue_directory: PathBuf::from("/unused"),
            }
        }

        fn with_pueue_directory(mut self, path: PathBuf) -> Self {
            self.pueue_directory = path;
            self
        }

        fn with_statuses(self, statuses: impl IntoIterator<Item = FakeStatusResult>) -> Self {
            *self.status_results.lock().unwrap() = statuses.into_iter().collect();
            self
        }

        fn task(&self, task_id: usize, status: TaskStatus) -> Task {
            let requests = self.requests.lock().unwrap();
            let request = requests.get(&task_id).unwrap();
            Task {
                id: task_id,
                created_at: Local::now(),
                original_command: request.command.clone(),
                command: request.command.clone(),
                path: request.path.clone(),
                envs: request.envs.clone(),
                group: request.group.clone(),
                dependencies: request.dependencies.clone(),
                priority: request.priority.unwrap_or_default(),
                label: request.label.clone(),
                status,
            }
        }

        fn push_tasks(&self, tasks: Vec<Task>) {
            self.task_results.lock().unwrap().push_back(Ok(tasks));
        }

        fn with_remove_results(
            self,
            results: impl IntoIterator<Item = Result<(), GatewayError>>,
        ) -> Self {
            *self.remove_results.lock().unwrap() = results.into_iter().collect();
            self
        }

        fn with_kill_results(
            self,
            results: impl IntoIterator<Item = Result<(), GatewayError>>,
        ) -> Self {
            *self.kill_results.lock().unwrap() = results.into_iter().collect();
            self
        }

        fn add_calls(&self) -> usize {
            self.add_calls.load(Ordering::SeqCst)
        }

        fn remove_calls(&self) -> Vec<Vec<usize>> {
            self.remove_calls.lock().unwrap().clone()
        }

        fn kill_calls(&self) -> Vec<KillRequest> {
            self.kill_calls.lock().unwrap().clone()
        }
    }

    impl Gateway for FakeGateway {
        fn pueue_directory(&self) -> &Path {
            &self.pueue_directory
        }

        async fn status(&self) -> Result<Box<State>, GatewayError> {
            if let Some(tasks) = self.task_results.lock().unwrap().pop_front() {
                let mut state = State::default();
                for task in tasks? {
                    state.tasks.insert(task.id, task);
                }
                return Ok(Box::new(state));
            }
            let requests = self.requests.lock().unwrap();
            let statuses = match self.status_results.lock().unwrap().pop_front() {
                Some(result) => result?,
                None => requests
                    .keys()
                    .map(|task_id| {
                        (
                            *task_id,
                            TaskStatus::Queued {
                                enqueued_at: Local::now(),
                            },
                        )
                    })
                    .collect(),
            };
            let mut state = State::default();
            for (task_id, status) in statuses {
                let request = requests.get(&task_id).unwrap();
                state.tasks.insert(
                    task_id,
                    Task {
                        id: task_id,
                        created_at: Local::now(),
                        original_command: request.command.clone(),
                        command: request.command.clone(),
                        path: request.path.clone(),
                        envs: request.envs.clone(),
                        group: request.group.clone(),
                        dependencies: request.dependencies.clone(),
                        priority: request.priority.unwrap_or_default(),
                        label: request.label.clone(),
                        status,
                    },
                );
            }
            Ok(Box::new(state))
        }

        async fn add(&self, request: AddRequest) -> Result<AddedTaskResponse, GatewayError> {
            self.add_calls.fetch_add(1, Ordering::SeqCst);
            let result = self
                .add_results
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or(Err(GatewayError::BackendRequestRejected));
            if let Ok(response) = &result {
                self.requests
                    .lock()
                    .unwrap()
                    .insert(response.task_id, request);
            }
            result
        }

        async fn remove(&self, task_ids: Vec<usize>) -> Result<(), GatewayError> {
            self.remove_calls.lock().unwrap().push(task_ids);
            self.remove_results
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or(Ok(()))
        }

        async fn kill(&self, request: KillRequest) -> Result<(), GatewayError> {
            self.kill_calls.lock().unwrap().push(request);
            self.kill_results
                .lock()
                .unwrap()
                .pop_front()
                .unwrap_or(Ok(()))
        }
    }

    fn workspace() -> WorkspaceResolution {
        WorkspaceResolution {
            workspace: PathBuf::from("/canonical/workspace"),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        }
    }

    fn request() -> AddRequest {
        AddRequest {
            command: "true".into(),
            path: PathBuf::from("/workspace"),
            envs: HashMap::new(),
            start_immediately: false,
            stashed: false,
            group: "default".into(),
            enqueue_at: None,
            dependencies: Vec::new(),
            priority: Some(0),
            label: None,
        }
    }

    #[test]
    fn request_fixture_contains_no_public_task_identity() {
        let serialized = serde_json::to_value(request()).unwrap();
        assert!(serialized.get("task_id").is_none());
        assert!(serialized.get("id").is_none());
        assert_eq!(
            AdapterError::from(OutputError::InvalidQuery),
            AdapterError::Output(OutputError::InvalidQuery)
        );
    }

    #[test]
    fn add_request_uses_canonical_workspace_and_fixed_phase_one_fields() {
        let workspace = workspace();
        let request = build_add_request("printf ok", &workspace);
        assert_eq!(request.command, "printf ok");
        assert_eq!(request.path, workspace.workspace);
        assert!(!request.start_immediately);
        assert!(!request.stashed);
        assert_eq!(request.group, "default");
        assert!(request.enqueue_at.is_none());
        assert!(request.dependencies.is_empty());
        assert!(request.priority.is_none());
        assert!(request.label.is_none());
        assert_eq!(
            request.envs.get("PATH").map(String::as_str),
            std::env::var("PATH").ok().as_deref()
        );
        assert_eq!(request.envs.get("NO_COLOR").map(String::as_str), Some("1"));
        assert_eq!(request.envs.get("PY_COLORS").map(String::as_str), Some("0"));
        assert_eq!(request.envs.get("PAGER").map(String::as_str), Some("cat"));
        assert_eq!(
            request.envs.get("GIT_PAGER").map(String::as_str),
            Some("cat")
        );
        assert_eq!(
            request.envs.get("SYSTEMD_PAGER").map(String::as_str),
            Some("cat")
        );
        for removed in ["CLICOLOR", "CLICOLOR_FORCE", "FORCE_COLOR"] {
            assert!(!request.envs.contains_key(removed));
        }
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_default_and_marker_workspaces_reach_the_add_request_canonically() {
        use std::os::unix::fs::symlink;

        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/adapter-tests")
            .join(format!("workspace-symlink-{}", std::process::id()));
        let project = root.join("project");
        let launch = project.join("nested/launch");
        let alias = root.join("launch-alias");
        fs::create_dir_all(&launch).unwrap();
        fs::write(project.join(".workspace-root"), []).unwrap();
        symlink(&launch, &alias).unwrap();

        let default_resolution = resolve_workspace(&alias, None).unwrap();
        assert_eq!(
            build_add_request("true", &default_resolution).path,
            fs::canonicalize(&launch).unwrap()
        );
        assert_eq!(
            default_resolution.workspace_resolution,
            WorkspaceResolutionSource::LaunchCwd
        );

        let config = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/workspace_contract/valid-marker.toml");
        let marker_resolution = resolve_workspace(&alias, Some(&config)).unwrap();
        assert_eq!(
            build_add_request("true", &marker_resolution).path,
            fs::canonicalize(&project).unwrap()
        );
        assert_eq!(
            marker_resolution.workspace_resolution,
            WorkspaceResolutionSource::ExplicitConfig
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn invalid_directive_has_no_backend_or_namespace_side_effect() {
        let gateway = FakeGateway::new([Err(GatewayError::BackendRequestRejected), Ok(91)]);
        let observer = gateway.clone();
        let mut adapter = PueueMcpAdapter::new(gateway, workspace());

        assert_eq!(
            adapter
                .run_shell("# loommux: --wait 0\nprintf nope")
                .await
                .unwrap_err(),
            AdapterError::InvalidDirective(crate::directive::DirectiveError::InvalidDirective)
        );
        assert_eq!(observer.add_calls(), 0);
        assert_eq!(adapter.execution_count(), 0);
        assert_eq!(adapter.recent_execution(), None);

        assert_eq!(
            adapter.run_shell("true").await.unwrap_err(),
            AdapterError::BackendRequestRejected
        );
        assert_eq!(observer.add_calls(), 1);
        assert_eq!(adapter.execution_count(), 0);
        assert_eq!(adapter.recent_execution(), None);
        assert_eq!(adapter.run_shell("true").await.unwrap().0, 1);
    }

    #[tokio::test]
    async fn unknown_submission_outcome_never_publishes_or_consumes_an_execution() {
        let gateway = FakeGateway::new([Err(GatewayError::AddOutcomeUnknown), Ok(101)]);
        let observer = gateway.clone();
        let mut adapter = PueueMcpAdapter::new(gateway, workspace());

        assert_eq!(
            adapter.run_shell("true").await.unwrap_err(),
            AdapterError::SubmissionOutcomeUnknown
        );
        assert_eq!(observer.add_calls(), 1);
        assert_eq!(adapter.execution_count(), 0);
        assert_eq!(adapter.recent_execution(), None);
        assert_eq!(adapter.run_shell("true").await.unwrap().0, 1);
    }

    #[tokio::test]
    async fn adapter_namespaces_are_independent_on_one_backend_identity_space() {
        let mut first = PueueMcpAdapter::new(FakeGateway::new([Ok(17)]), workspace());
        let mut second = PueueMcpAdapter::new(FakeGateway::new([Ok(42)]), workspace());

        assert_eq!(first.run_shell("true").await.unwrap().0, 1);
        assert_eq!(second.run_shell("true").await.unwrap().0, 1);
        assert_eq!(first.execution(1).unwrap().execution, 1);
        assert_eq!(second.execution(1).unwrap().execution, 1);
    }

    #[tokio::test]
    async fn modified_identity_stops_state_projection_before_log_refresh() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/adapter-tests")
            .join(format!("modified-identity-{}", std::process::id()));
        let log = get_log_path(42, &root);
        fs::create_dir_all(log.parent().unwrap()).unwrap();
        fs::write(&log, "replacement generation\n").unwrap();

        let gateway = FakeGateway::new([Ok(42)]).with_pueue_directory(root.clone());
        let observer = gateway.clone();
        let mut adapter = PueueMcpAdapter::new(gateway, workspace());
        let execution = adapter.run_shell("printf original").await.unwrap().0;
        let mut modified = observer.task(
            42,
            TaskStatus::Running {
                enqueued_at: Local::now(),
                start: Local::now(),
            },
        );
        modified.label = Some("externally-edited".into());
        observer.push_tasks(vec![modified]);

        let result = adapter.observe_execution(execution).await;
        fs::remove_dir_all(root).unwrap();

        assert_eq!(result.unwrap_err(), AdapterError::BackendTaskModified);
        let record = adapter.execution(execution).unwrap();
        assert_eq!(record.status, ExecutionStatus::Queued);
        assert_eq!(record.backend_presence, BackendPresence::Modified);
        assert!(record.output().log().text().is_empty());
    }

    #[tokio::test]
    async fn successful_output_refresh_clears_a_transient_error_category() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/adapter-tests")
            .join(format!("output-recovery-{}", std::process::id()));
        let log = get_log_path(71, &root);
        let now = Local::now();
        let running = || TaskStatus::Running {
            enqueued_at: now,
            start: now,
        };
        let gateway = FakeGateway::new([Ok(71)])
            .with_pueue_directory(root.clone())
            .with_statuses([
                Ok(vec![(71, running())]),
                Ok(vec![(71, running())]),
                Ok(vec![(
                    71,
                    TaskStatus::Done {
                        enqueued_at: now,
                        start: now,
                        end: now,
                        result: TaskResult::Success,
                    },
                )]),
            ]);
        let mut adapter = PueueMcpAdapter::new(gateway, workspace());
        let execution = adapter.run_shell("printf recovered").await.unwrap().0;

        assert_eq!(
            adapter.observe_execution(execution).await.unwrap_err(),
            AdapterError::Output(OutputError::BackendLogUnavailable)
        );
        assert_eq!(
            adapter.execution(execution).unwrap().output_error_kind,
            Some(OutputError::BackendLogUnavailable)
        );

        fs::create_dir_all(log.parent().unwrap()).unwrap();
        fs::write(&log, "recovered").unwrap();
        adapter.observe_execution(execution).await.unwrap();
        assert!(
            adapter
                .execution(execution)
                .unwrap()
                .output_error_kind
                .is_none()
        );
        adapter.observe_execution(execution).await.unwrap();
        fs::remove_dir_all(root).unwrap();

        let record = adapter.execution(execution).unwrap();
        assert_eq!(record.status, ExecutionStatus::Completed);
        assert!(record.output_error_kind.is_none());
        assert_eq!(record.output().log().text(), "recovered");
    }

    #[tokio::test]
    async fn four_adapter_namespaces_run_eight_execution_workloads_concurrently() {
        let mut adapters = Vec::new();
        for adapter_index in 0..4_usize {
            let task_ids = (0..8).map(|offset| Ok(adapter_index * 100 + offset + 1));
            let mut adapter = PueueMcpAdapter::new(FakeGateway::new(task_ids), workspace());
            for _ in 0..8 {
                adapter.run_shell("printf concurrent").await.unwrap();
            }
            adapters.push(Arc::new(tokio::sync::Mutex::new(adapter)));
        }

        let mut calls = Vec::new();
        for adapter in adapters {
            let status_adapter = Arc::clone(&adapter);
            calls.push(tokio::spawn(async move {
                status_adapter.lock().await.refresh_all().await.unwrap();
            }));
            let read_adapter = Arc::clone(&adapter);
            calls.push(tokio::spawn(async move {
                read_adapter
                    .lock()
                    .await
                    .read_output(1, None, None)
                    .await
                    .unwrap();
            }));
            let cancel_adapter = Arc::clone(&adapter);
            calls.push(tokio::spawn(async move {
                cancel_adapter.lock().await.cancel(2).await.unwrap();
            }));
            calls.push(tokio::spawn(async move {
                let mut adapter = adapter.lock().await;
                assert_eq!(
                    adapter.wait(3, 0.000_001).await.unwrap(),
                    WaitOutcome::DeadlineElapsed
                );
                assert_eq!(adapter.execution_count(), 8);
                assert_eq!(adapter.recent_execution(), Some(8));
            }));
        }
        for call in calls {
            call.await.unwrap();
        }
    }

    #[tokio::test]
    async fn cancel_uses_only_task_specific_remove_or_kill() {
        let now = Local::now();
        for status in [
            TaskStatus::Queued { enqueued_at: now },
            TaskStatus::Stashed { enqueue_at: None },
        ] {
            let gateway = FakeGateway::new([Ok(17)]).with_statuses([Ok(vec![(17, status)])]);
            let observer = gateway.clone();
            let mut adapter = PueueMcpAdapter::new(gateway, workspace());
            let execution = adapter.run_shell("true").await.unwrap().0;

            assert_eq!(
                adapter.cancel(execution).await.unwrap(),
                CancelOutcome::Terminal
            );
            assert_eq!(observer.remove_calls(), [vec![17]]);
            assert!(observer.kill_calls().is_empty());
            assert_eq!(
                adapter.execution(execution).unwrap().status,
                ExecutionStatus::Cancelled
            );
            assert!(adapter.execution(execution).unwrap().result.is_none());
        }

        for status in [
            TaskStatus::Running {
                enqueued_at: now,
                start: now,
            },
            TaskStatus::Paused {
                enqueued_at: now,
                start: now,
            },
        ] {
            let gateway = FakeGateway::new([Ok(23)]).with_statuses([Ok(vec![(23, status)])]);
            let observer = gateway.clone();
            let mut adapter = PueueMcpAdapter::new(gateway, workspace());
            let execution = adapter.run_shell("true").await.unwrap().0;

            assert_eq!(
                adapter.cancel(execution).await.unwrap(),
                CancelOutcome::Requested
            );
            assert!(observer.remove_calls().is_empty());
            let calls = observer.kill_calls();
            assert_eq!(calls.len(), 1);
            assert_eq!(calls[0].tasks, TaskSelection::TaskIds(vec![23]));
            assert!(calls[0].signal.is_none());
        }
    }

    #[tokio::test]
    async fn queued_cancel_freezes_available_output_and_blocks_remove_after_refresh_failure() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/adapter-tests")
            .join(format!("cancel-output-{}", std::process::id()));
        let retained_directory = root.join("retained");
        let retained_log = get_log_path(17, &retained_directory);
        fs::create_dir_all(retained_log.parent().unwrap()).unwrap();
        fs::write(&retained_log, "retained needle\n").unwrap();

        let now = Local::now();
        let gateway = FakeGateway::new([Ok(17)])
            .with_pueue_directory(retained_directory)
            .with_statuses([Ok(vec![(17, TaskStatus::Queued { enqueued_at: now })])]);
        let observer = gateway.clone();
        let mut adapter = PueueMcpAdapter::new(gateway, workspace());
        let execution = adapter.run_shell("true").await.unwrap().0;
        let cancel_result = adapter.cancel(execution).await;
        fs::remove_file(&retained_log).unwrap();
        let read_result = adapter.read_output(execution, None, None).await;
        let search_result = adapter
            .search_output(
                execution,
                &SearchRequest {
                    query: "needle",
                    query_mode: QueryMode::Literal,
                    context_before: 0,
                    context_after: 0,
                    ignore_case: false,
                    max_chars: None,
                },
            )
            .await;
        let wait_result = adapter.wait(execution, 1.0).await;

        let blocked_directory = root.join("blocked");
        let blocked_log = get_log_path(19, &blocked_directory);
        fs::create_dir_all(&blocked_log).unwrap();
        let blocked_gateway = FakeGateway::new([Ok(19)])
            .with_pueue_directory(blocked_directory)
            .with_statuses([Ok(vec![(19, TaskStatus::Queued { enqueued_at: now })])]);
        let blocked_observer = blocked_gateway.clone();
        let mut blocked_adapter = PueueMcpAdapter::new(blocked_gateway, workspace());
        let blocked_execution = blocked_adapter.run_shell("true").await.unwrap().0;
        let blocked_result = blocked_adapter.cancel(blocked_execution).await;

        fs::remove_dir_all(root).unwrap();

        assert_eq!(cancel_result.unwrap(), CancelOutcome::Terminal);
        assert_eq!(observer.remove_calls(), [vec![17]]);
        let record = adapter.execution(execution).unwrap();
        assert_eq!(record.status, ExecutionStatus::Cancelled);
        assert!(record.output().finalized());
        assert!(!execution_observation(record, None, false).output_complete);
        assert_eq!(read_result.unwrap().text, "retained needle");
        assert_eq!(search_result.unwrap().matches, 1);
        assert_eq!(wait_result.unwrap(), WaitOutcome::Terminal);

        assert_eq!(
            blocked_result.unwrap_err(),
            AdapterError::Output(OutputError::BackendLogUnavailable)
        );
        assert!(blocked_observer.remove_calls().is_empty());
        assert_eq!(
            blocked_adapter.execution(blocked_execution).unwrap().status,
            ExecutionStatus::Queued
        );
    }

    #[tokio::test]
    async fn stashed_cancel_keeps_frozen_read_search_and_wait_after_log_deletion() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/adapter-tests")
            .join(format!("stashed-cancel-output-{}", std::process::id()));
        let log = get_log_path(29, &root);
        fs::create_dir_all(log.parent().unwrap()).unwrap();
        fs::write(&log, "stashed needle\n").unwrap();
        let gateway = FakeGateway::new([Ok(29)])
            .with_pueue_directory(root.clone())
            .with_statuses([Ok(vec![(29, TaskStatus::Stashed { enqueue_at: None })])]);
        let observer = gateway.clone();
        let mut adapter = PueueMcpAdapter::new(gateway, workspace());
        let execution = adapter.run_shell("true").await.unwrap().0;

        assert_eq!(
            adapter.cancel(execution).await.unwrap(),
            CancelOutcome::Terminal
        );
        fs::remove_file(log).unwrap();
        adapter.refresh_all().await.unwrap();
        assert_eq!(
            adapter
                .read_output(execution, None, None)
                .await
                .unwrap()
                .text,
            "stashed needle"
        );
        assert_eq!(
            adapter
                .search_output(
                    execution,
                    &SearchRequest {
                        query: "needle",
                        query_mode: QueryMode::Literal,
                        context_before: 0,
                        context_after: 0,
                        ignore_case: false,
                        max_chars: None,
                    },
                )
                .await
                .unwrap()
                .matches,
            1
        );
        assert_eq!(
            adapter.wait(execution, 1.0).await.unwrap(),
            WaitOutcome::Terminal
        );
        let record = adapter.execution(execution).unwrap();
        assert_eq!(record.status, ExecutionStatus::Cancelled);
        assert!(!execution_observation(record, None, false).output_complete);
        assert_eq!(observer.remove_calls(), [vec![29]]);

        fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn locked_and_terminal_cancel_have_no_control_side_effect() {
        let now = Local::now();
        let locked = FakeGateway::new([Ok(31)]).with_statuses([Ok(vec![(
            31,
            TaskStatus::Locked {
                previous_status: Box::new(TaskStatus::Queued { enqueued_at: now }),
            },
        )])]);
        let locked_observer = locked.clone();
        let mut adapter = PueueMcpAdapter::new(locked, workspace());
        let execution = adapter.run_shell("true").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap_err(),
            AdapterError::CancelBlocked
        );
        assert!(locked_observer.remove_calls().is_empty());
        assert!(locked_observer.kill_calls().is_empty());

        let terminal = FakeGateway::new([Ok(37)]).with_statuses([Ok(vec![(
            37,
            TaskStatus::Done {
                enqueued_at: now,
                start: now,
                end: now,
                result: TaskResult::Success,
            },
        )])]);
        let terminal_observer = terminal.clone();
        let mut adapter = PueueMcpAdapter::new(terminal, workspace());
        let execution = adapter.run_shell("true").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap(),
            CancelOutcome::AlreadyTerminal
        );
        assert!(terminal_observer.remove_calls().is_empty());
        assert!(terminal_observer.kill_calls().is_empty());
    }

    #[tokio::test]
    async fn cancel_rejection_refreshes_races_without_replaying_control() {
        let now = Local::now();
        let terminal_race = FakeGateway::new([Ok(41)])
            .with_statuses([
                Ok(vec![(41, TaskStatus::Queued { enqueued_at: now })]),
                Ok(vec![(
                    41,
                    TaskStatus::Done {
                        enqueued_at: now,
                        start: now,
                        end: now,
                        result: TaskResult::Failed(9),
                    },
                )]),
            ])
            .with_remove_results([Err(GatewayError::BackendRequestRejected)]);
        let terminal_observer = terminal_race.clone();
        let mut adapter = PueueMcpAdapter::new(terminal_race, workspace());
        let execution = adapter.run_shell("false").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap(),
            CancelOutcome::AlreadyTerminal
        );
        assert_eq!(
            adapter.execution(execution).unwrap().status,
            ExecutionStatus::Failed
        );
        assert_eq!(terminal_observer.remove_calls(), [vec![41]]);

        let active_race = FakeGateway::new([Ok(43)])
            .with_statuses([
                Ok(vec![(43, TaskStatus::Queued { enqueued_at: now })]),
                Ok(vec![(
                    43,
                    TaskStatus::Running {
                        enqueued_at: now,
                        start: now,
                    },
                )]),
            ])
            .with_remove_results([Err(GatewayError::BackendRequestRejected)]);
        let active_observer = active_race.clone();
        let mut adapter = PueueMcpAdapter::new(active_race, workspace());
        let execution = adapter.run_shell("sleep 1").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap_err(),
            AdapterError::CancelRejected
        );
        assert_eq!(active_observer.remove_calls(), [vec![43]]);

        let failed_recovery = FakeGateway::new([Ok(45)])
            .with_statuses([
                Ok(vec![(45, TaskStatus::Queued { enqueued_at: now })]),
                Err(GatewayError::BackendTimeout),
            ])
            .with_remove_results([Err(GatewayError::BackendRequestRejected)]);
        let mut adapter = PueueMcpAdapter::new(failed_recovery, workspace());
        let execution = adapter.run_shell("sleep 1").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap_err(),
            AdapterError::BackendTimeout
        );
    }

    #[tokio::test]
    async fn kill_rejection_and_failed_spawn_remove_races_refresh_without_replay() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/adapter-tests")
            .join(format!("cancel-rejection-{}", std::process::id()));
        for task_id in [81, 83] {
            let log = get_log_path(task_id, &root);
            fs::create_dir_all(log.parent().unwrap()).unwrap();
            fs::write(log, []).unwrap();
        }
        let now = Local::now();

        let killed = FakeGateway::new([Ok(81)])
            .with_pueue_directory(root.clone())
            .with_statuses([
                Ok(vec![(
                    81,
                    TaskStatus::Running {
                        enqueued_at: now,
                        start: now,
                    },
                )]),
                Ok(vec![(
                    81,
                    TaskStatus::Done {
                        enqueued_at: now,
                        start: now,
                        end: now,
                        result: TaskResult::Killed,
                    },
                )]),
            ])
            .with_kill_results([Err(GatewayError::BackendRequestRejected)]);
        let killed_observer = killed.clone();
        let mut adapter = PueueMcpAdapter::new(killed, workspace());
        let execution = adapter.run_shell("sleep 60").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap(),
            CancelOutcome::AlreadyTerminal
        );
        assert_eq!(
            adapter.execution(execution).unwrap().status,
            ExecutionStatus::Killed
        );
        assert_eq!(killed_observer.kill_calls().len(), 1);

        let active = FakeGateway::new([Ok(83)])
            .with_pueue_directory(root.clone())
            .with_statuses([
                Ok(vec![(
                    83,
                    TaskStatus::Running {
                        enqueued_at: now,
                        start: now,
                    },
                )]),
                Ok(vec![(
                    83,
                    TaskStatus::Running {
                        enqueued_at: now,
                        start: now,
                    },
                )]),
            ])
            .with_kill_results([Err(GatewayError::BackendRequestRejected)]);
        let active_observer = active.clone();
        let mut adapter = PueueMcpAdapter::new(active, workspace());
        let execution = adapter.run_shell("sleep 60").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap_err(),
            AdapterError::CancelRejected
        );
        assert_eq!(active_observer.kill_calls().len(), 1);

        let failed_spawn = FakeGateway::new([Ok(85)])
            .with_pueue_directory(root.clone())
            .with_statuses([
                Ok(vec![(85, TaskStatus::Stashed { enqueue_at: None })]),
                Ok(vec![(
                    85,
                    TaskStatus::Done {
                        enqueued_at: now,
                        start: now,
                        end: now,
                        result: TaskResult::FailedToSpawn("private detail".into()),
                    },
                )]),
            ])
            .with_remove_results([Err(GatewayError::BackendRequestRejected)]);
        let failed_spawn_observer = failed_spawn.clone();
        let mut adapter = PueueMcpAdapter::new(failed_spawn, workspace());
        let execution = adapter.run_shell("missing-command").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap(),
            CancelOutcome::AlreadyTerminal
        );
        let record = adapter.execution(execution).unwrap();
        assert_eq!(record.status, ExecutionStatus::Failed);
        assert_eq!(
            record.result.as_ref().unwrap().failure_kind,
            Some(crate::execution::FailureKind::FailedToSpawn)
        );
        assert_eq!(failed_spawn_observer.remove_calls(), [vec![85]]);

        fs::remove_dir_all(root).unwrap();
    }

    #[tokio::test]
    async fn remove_success_does_not_infer_an_unobserved_failed_spawn_result() {
        let gateway = FakeGateway::new([Ok(87)])
            .with_statuses([Ok(vec![(87, TaskStatus::Stashed { enqueue_at: None })])]);
        let observer = gateway.clone();
        let mut adapter = PueueMcpAdapter::new(gateway, workspace());
        let execution = adapter.run_shell("missing-command").await.unwrap().0;

        assert_eq!(
            adapter.cancel(execution).await.unwrap(),
            CancelOutcome::Terminal
        );
        let record = adapter.execution(execution).unwrap();
        assert_eq!(record.status, ExecutionStatus::Cancelled);
        assert!(record.result.is_none());
        assert!(!execution_observation(record, None, false).output_complete);
        assert_eq!(observer.remove_calls(), [vec![87]]);
    }

    #[tokio::test]
    async fn uncertain_or_disappearing_cancel_is_unknown_and_not_replayed() {
        let now = Local::now();
        let response_loss = FakeGateway::new([Ok(47)])
            .with_statuses([Ok(vec![(47, TaskStatus::Queued { enqueued_at: now })])])
            .with_remove_results([Err(GatewayError::RemoveOutcomeUnknown)]);
        let loss_observer = response_loss.clone();
        let mut adapter = PueueMcpAdapter::new(response_loss, workspace());
        let execution = adapter.run_shell("true").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap_err(),
            AdapterError::CancelOutcomeUnknown
        );
        assert_eq!(loss_observer.remove_calls(), [vec![47]]);

        let kill_loss = FakeGateway::new([Ok(49)])
            .with_statuses([Ok(vec![(
                49,
                TaskStatus::Running {
                    enqueued_at: now,
                    start: now,
                },
            )])])
            .with_kill_results([Err(GatewayError::KillOutcomeUnknown)]);
        let kill_observer = kill_loss.clone();
        let mut adapter = PueueMcpAdapter::new(kill_loss, workspace());
        let execution = adapter.run_shell("sleep 1").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap_err(),
            AdapterError::CancelOutcomeUnknown
        );
        assert_eq!(kill_observer.kill_calls().len(), 1);

        let disappeared = FakeGateway::new([Ok(53)])
            .with_statuses([
                Ok(vec![(53, TaskStatus::Queued { enqueued_at: now })]),
                Ok(Vec::new()),
            ])
            .with_remove_results([Err(GatewayError::BackendRequestRejected)]);
        let disappeared_observer = disappeared.clone();
        let mut adapter = PueueMcpAdapter::new(disappeared, workspace());
        let execution = adapter.run_shell("true").await.unwrap().0;
        assert_eq!(
            adapter.cancel(execution).await.unwrap_err(),
            AdapterError::CancelOutcomeUnknown
        );
        assert_eq!(disappeared_observer.remove_calls(), [vec![53]]);
    }

    #[tokio::test(start_paused = true)]
    async fn wait_uses_a_monotonic_deadline_and_stops_on_terminal_observation() {
        let now = Local::now();
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/adapter-tests")
            .join(format!("wait-states-{}", std::process::id()));
        let paused_log = get_log_path(4, &root);
        fs::create_dir_all(paused_log.parent().unwrap()).unwrap();
        fs::write(&paused_log, "paused output\n").unwrap();
        let states = [
            (
                1,
                TaskStatus::Locked {
                    previous_status: Box::new(TaskStatus::Queued { enqueued_at: now }),
                },
                ExecutionStatus::Locked,
            ),
            (
                2,
                TaskStatus::Stashed { enqueue_at: None },
                ExecutionStatus::Stashed,
            ),
            (
                3,
                TaskStatus::Queued { enqueued_at: now },
                ExecutionStatus::Queued,
            ),
            (
                4,
                TaskStatus::Paused {
                    enqueued_at: now,
                    start: now,
                },
                ExecutionStatus::Paused,
            ),
        ];
        for (task_id, state, expected) in states {
            let gateway = FakeGateway::new([Ok(task_id)])
                .with_pueue_directory(root.clone())
                .with_statuses([
                    Ok(vec![(task_id, state.clone())]),
                    Ok(vec![(task_id, state)]),
                ]);
            let observer = gateway.clone();
            let mut adapter = PueueMcpAdapter::new(gateway, workspace());
            let execution = adapter.run_shell("sleep 60").await.unwrap().0;
            assert_eq!(
                adapter.wait(execution, 0.5).await.unwrap(),
                WaitOutcome::DeadlineElapsed
            );
            assert_eq!(adapter.execution(execution).unwrap().status, expected);
            assert!(observer.remove_calls().is_empty());
            assert!(observer.kill_calls().is_empty());
        }
        fs::remove_dir_all(root).unwrap();

        let queued = || TaskStatus::Queued { enqueued_at: now };
        let deadline_gateway = FakeGateway::new([Ok(59)]).with_statuses([
            Ok(vec![(59, queued())]),
            Ok(vec![(59, queued())]),
            Ok(vec![(59, queued())]),
            Ok(vec![(59, queued())]),
        ]);
        let mut deadline_adapter = PueueMcpAdapter::new(deadline_gateway, workspace());
        let execution = deadline_adapter.run_shell("sleep 60").await.unwrap().0;
        let started = tokio::time::Instant::now();
        assert_eq!(
            deadline_adapter.wait(execution, 5.0).await.unwrap(),
            WaitOutcome::DeadlineElapsed
        );
        assert_eq!(started.elapsed(), Duration::from_secs(5));

        let terminal_gateway = FakeGateway::new([Ok(61)]).with_statuses([
            Ok(vec![(61, queued())]),
            Ok(vec![(
                61,
                TaskStatus::Done {
                    enqueued_at: now,
                    start: now,
                    end: now,
                    result: TaskResult::Success,
                },
            )]),
        ]);
        let mut terminal_adapter = PueueMcpAdapter::new(terminal_gateway, workspace());
        let execution = terminal_adapter.run_shell("true").await.unwrap().0;
        assert_eq!(
            terminal_adapter.wait(execution, 30.0).await.unwrap(),
            WaitOutcome::Terminal
        );
        assert_eq!(
            terminal_adapter.execution(execution).unwrap().status,
            ExecutionStatus::Completed
        );
        assert_eq!(
            terminal_adapter.wait(execution, 0.0).await.unwrap_err(),
            AdapterError::InvalidTimeout
        );
        for invalid in [1e-300, 1e300] {
            assert_eq!(
                terminal_adapter.wait(execution, invalid).await.unwrap_err(),
                AdapterError::InvalidTimeout
            );
        }
    }

    proptest! {
        #[test]
        fn accepted_only_sequence_is_strictly_monotonic(
            outcomes in prop::collection::vec(any::<bool>(), 1..64),
        ) {
            let results = outcomes.iter().scan(100_usize, |task_id, accepted| {
                let result = if *accepted {
                    let current = *task_id;
                    *task_id += 3;
                    Ok(current)
                } else {
                    Err(GatewayError::BackendRequestRejected)
                };
                Some(result)
            }).collect::<Vec<_>>();
            let runtime = tokio::runtime::Runtime::new().unwrap();
            runtime.block_on(async {
                let mut adapter = PueueMcpAdapter::new(FakeGateway::new(results), workspace());
                let mut accepted_count = 0_u64;
                for accepted in outcomes {
                    let prior_recent = adapter.recent_execution();
                    match adapter.run_shell("true").await {
                        Ok((execution, _)) => {
                            accepted_count += 1;
                            prop_assert!(accepted);
                            prop_assert_eq!(execution, accepted_count);
                            prop_assert_eq!(adapter.recent_execution(), Some(execution));
                        }
                        Err(error) => {
                            prop_assert!(!accepted);
                            prop_assert_eq!(error, AdapterError::BackendRequestRejected);
                            prop_assert_eq!(adapter.recent_execution(), prior_recent);
                        }
                    }
                }
                prop_assert_eq!(adapter.execution_count(), usize::try_from(accepted_count).unwrap());
                Ok(())
            })?;
        }
    }

    #[tokio::test]
    async fn exactly_three_accepted_submissions_publish_one_two_three() {
        let mut adapter =
            PueueMcpAdapter::new(FakeGateway::new([Ok(101), Ok(104), Ok(110)]), workspace());
        let mut published = Vec::new();
        for _ in 0..3 {
            published.push(adapter.run_shell("true").await.unwrap().0);
        }
        assert_eq!(published, [1, 2, 3]);
        assert_eq!(adapter.recent_execution(), Some(3));
        assert_eq!(adapter.execution_count(), 3);
    }
}
