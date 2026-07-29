//! MCP content and structured-result projection.

use chrono::{DateTime, Utc};
use clap::ValueEnum;
use rmcp::model::{CallToolResult, ContentBlock};
use serde::Serialize;

use crate::{
    adapter::{AdapterError, CancelOutcome, WaitOutcome},
    execution::{BackendPreviousStatus, ExecutionStatus, PueueExecution},
    output::{OutputRead, OutputSearch},
};

const AUTOMATIC_OUTPUT_LINE_LIMIT: usize = 300;

fn rfc3339(value: DateTime<Utc>) -> String {
    value.to_rfc3339()
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, ValueEnum)]
pub enum ResultMode {
    #[default]
    Content,
    Structured,
}

#[derive(Debug, Serialize)]
pub struct ErrorResult {
    pub ok: bool,
    pub error_kind: &'static str,
    pub message: &'static str,
    pub execution: Option<u64>,
    pub status: Option<ExecutionStatus>,
}

#[derive(Debug, Serialize)]
pub struct AdapterStatus {
    pub ok: bool,
    pub workspace: String,
    pub workspace_resolution: &'static str,
    pub daemon_connected: bool,
    pub daemon_profile: &'static str,
    pub daemon_error_kind: Option<&'static str>,
    pub execution_count: usize,
    pub active_execution_count: usize,
    pub active_executions: Vec<u64>,
    pub recent_execution: Option<u64>,
    pub backend_missing_executions: Vec<u64>,
    pub backend_modified_executions: Vec<u64>,
}

#[derive(Debug, Serialize)]
pub struct ExecutionObservation {
    pub ok: bool,
    pub execution: u64,
    pub status: ExecutionStatus,
    pub submitted_at: String,
    pub updated_at: String,
    pub enqueue_at: Option<String>,
    pub enqueued_at: Option<String>,
    pub started_at: Option<String>,
    pub completed_at: Option<String>,
    pub backend_previous_status: Option<BackendPreviousStatus>,
    pub exit_code: Option<i32>,
    pub failure_kind: Option<crate::execution::FailureKind>,
    pub error_summary: Option<&'static str>,
    pub wait_outcome: Option<&'static str>,
    pub output_total_lines: usize,
    pub output_complete: bool,
    pub output_decode_replacements: u64,
    pub output_text: Option<String>,
    pub output_omitted_reason: Option<&'static str>,
    pub output_line_limit: Option<usize>,
    pub output_error_kind: Option<&'static str>,
}

#[derive(Debug, Serialize)]
pub struct OutputReadResult {
    pub ok: bool,
    pub execution: u64,
    pub status: ExecutionStatus,
    #[serde(flatten)]
    pub output: OutputRead,
    pub output_complete: bool,
    pub output_decode_replacements: u64,
}

#[derive(Debug, Serialize)]
#[allow(clippy::struct_excessive_bools)] // These booleans are independent fields in the frozen public schema.
pub struct OutputSearchResult {
    pub ok: bool,
    pub execution: u64,
    pub status: ExecutionStatus,
    pub ignore_case: bool,
    pub returned_lines: usize,
    pub partial_final_line: bool,
    pub output_complete: bool,
    pub output_decode_replacements: u64,
    #[serde(flatten)]
    pub output: OutputSearch,
}

#[derive(Debug, Serialize)]
pub struct CancelObservation {
    pub ok: bool,
    pub execution: u64,
    pub status: ExecutionStatus,
    pub cancel_outcome: &'static str,
    pub completed_at: Option<String>,
    pub output_total_lines: usize,
    pub output_complete: bool,
}

#[must_use]
pub fn execution_observation(
    record: &PueueExecution,
    wait_outcome: Option<WaitOutcome>,
    automatic_output: bool,
) -> ExecutionObservation {
    let result = record.result.as_ref();
    let output_total_lines = record.output().log().line_count();
    let output_complete = record.status.is_terminal()
        && record.output().finalized()
        && record.status != ExecutionStatus::Cancelled;
    let (exit_code, failure_kind, error_summary) = match result {
        Some(value) => (value.exit_code, value.failure_kind, value.error_summary),
        None => (None, None, None),
    };
    let can_deliver = automatic_output && output_complete;
    let omitted = can_deliver
        && output_total_lines > AUTOMATIC_OUTPUT_LINE_LIMIT
        && !record.full_output_requested();
    ExecutionObservation {
        ok: true,
        execution: record.execution,
        status: record.status,
        submitted_at: record.submitted_at.to_rfc3339(),
        updated_at: record.updated_at.to_rfc3339(),
        enqueue_at: record.enqueue_at.map(rfc3339),
        enqueued_at: record.enqueued_at.map(rfc3339),
        started_at: record.started_at.map(rfc3339),
        completed_at: record.completed_at.map(rfc3339),
        backend_previous_status: record.backend_previous_status,
        exit_code,
        failure_kind,
        error_summary,
        wait_outcome: match wait_outcome {
            Some(WaitOutcome::Terminal) => Some("terminal"),
            Some(WaitOutcome::DeadlineElapsed) => Some("deadline_elapsed"),
            None => None,
        },
        output_total_lines,
        output_complete,
        output_decode_replacements: record.output().decode_replacements(),
        output_text: if can_deliver && !omitted {
            Some(record.output().log().text().to_owned())
        } else {
            None
        },
        output_omitted_reason: omitted.then_some("line_limit_exceeded"),
        output_line_limit: omitted.then_some(AUTOMATIC_OUTPUT_LINE_LIMIT),
        output_error_kind: record
            .output_error_kind
            .map(crate::output::OutputError::kind),
    }
}

#[must_use]
pub fn cancel_observation(record: &PueueExecution, outcome: CancelOutcome) -> CancelObservation {
    CancelObservation {
        ok: true,
        execution: record.execution,
        status: record.status,
        cancel_outcome: match outcome {
            CancelOutcome::Terminal => "terminal",
            CancelOutcome::Requested => "requested",
            CancelOutcome::AlreadyTerminal => "already_terminal",
        },
        completed_at: record.completed_at.map(rfc3339),
        output_total_lines: record.output().log().line_count(),
        output_complete: record.status.is_terminal()
            && record.output().finalized()
            && record.status != ExecutionStatus::Cancelled,
    }
}

#[must_use]
pub fn error_result(
    error: AdapterError,
    execution: Option<u64>,
    status: Option<ExecutionStatus>,
) -> ErrorResult {
    ErrorResult {
        ok: false,
        error_kind: error.kind(),
        message: error_message(error),
        execution,
        status,
    }
}

pub fn tool_result<T: Serialize>(value: &T, mode: ResultMode, is_error: bool) -> CallToolResult {
    let structured = match serde_json::to_value(value) {
        Ok(value) => value,
        Err(_) => serde_json::json!({"ok": false, "error_kind": "result_serialization_failed"}),
    };
    let text = match serde_json::to_string_pretty(&structured) {
        Ok(value) => value,
        Err(_) => "result serialization failed".into(),
    };
    let mut result = CallToolResult::success(vec![ContentBlock::text(text)]);
    result.is_error = Some(is_error);
    if mode == ResultMode::Structured {
        result.structured_content = Some(structured);
    }
    result
}

const fn error_message(error: AdapterError) -> &'static str {
    match error {
        AdapterError::ExecutionOverflow => "The adapter execution namespace is exhausted",
        AdapterError::BackendUnavailable => "The Pueue daemon is unavailable",
        AdapterError::BackendTimeout => "The Pueue exchange timed out",
        AdapterError::BackendProtocolIncompatible => "The Pueue protocol is incompatible",
        AdapterError::BackendRequestRejected => "Pueue rejected the request",
        AdapterError::SubmissionOutcomeUnknown => "The task submission outcome is unknown",
        AdapterError::UnexpectedBackendResponse => "Pueue returned an unexpected response",
        AdapterError::InvalidDirective(_) => "The shell control directive is invalid",
        AdapterError::ExecutionNotFound => "The execution does not exist in this adapter",
        AdapterError::BackendTaskMissing => "The bound backend task is missing",
        AdapterError::BackendTaskModified => "The bound backend task identity changed",
        AdapterError::Output(_) => "The backend output could not be refreshed",
        AdapterError::InvalidTimeout => "The timeout must be a positive representable duration",
        AdapterError::CancelBlocked => "Cancellation is blocked while the task is locked",
        AdapterError::CancelRejected => "Pueue rejected the cancellation request",
        AdapterError::CancelOutcomeUnknown => "The cancellation outcome is unknown",
    }
}

#[cfg(test)]
mod tests {
    use std::{collections::HashMap, fs, path::PathBuf};

    use chrono::Utc;
    use pueue_lib::message::AddRequest;
    use sha2::{Digest, Sha256};

    use crate::{
        adapter::{AdapterError, CancelOutcome, WaitOutcome},
        directive::DirectiveError,
        execution::{BackendPreviousStatus, ExecutionStatus, PueueExecution},
        output::{OutputError, OutputRead, OutputSearch, QueryMode},
    };

    use super::{
        AdapterStatus, CancelObservation, ExecutionObservation, OutputReadResult,
        OutputSearchResult, ResultMode, cancel_observation, error_result, execution_observation,
        tool_result,
    };

    #[test]
    fn content_is_the_default_result_mode() {
        assert_eq!(ResultMode::default(), ResultMode::Content);
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
            priority: None,
            label: None,
        }
    }

    #[test]
    fn automatic_delivery_omits_301_lines_unless_full_output_is_requested() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/result-tests");
        fs::create_dir_all(&root).unwrap();
        let path = root.join(format!("automatic-delivery-{}.log", std::process::id()));
        let transcript = "line\n".repeat(301);
        fs::write(&path, &transcript).unwrap();

        let mut ordinary = PueueExecution::accepted(1, 42, &request(), false, Utc::now());
        ordinary.status = ExecutionStatus::Completed;
        ordinary.output.refresh(&path, true, true).unwrap();
        let ordinary_result = execution_observation(&ordinary, None, true);

        let mut full = PueueExecution::accepted(2, 43, &request(), true, Utc::now());
        full.status = ExecutionStatus::Completed;
        full.output.refresh(&path, true, true).unwrap();
        let full_result = execution_observation(&full, None, true);

        fs::remove_file(path).unwrap();
        let _ = fs::remove_dir(root);

        assert_eq!(ordinary_result.output_total_lines, 301);
        assert!(ordinary_result.output_complete);
        assert!(ordinary_result.output_text.is_none());
        assert_eq!(
            ordinary_result.output_omitted_reason,
            Some("line_limit_exceeded")
        );
        assert_eq!(ordinary_result.output_line_limit, Some(300));

        assert_eq!(full_result.output_total_lines, 301);
        assert!(full_result.output_complete);
        assert_eq!(
            full_result.output_text.as_deref(),
            Some(transcript.as_str())
        );
        assert!(full_result.output_omitted_reason.is_none());
        assert!(full_result.output_line_limit.is_none());
    }

    #[test]
    fn every_adapter_error_and_control_outcome_has_a_direct_projection() {
        let errors = [
            AdapterError::ExecutionOverflow,
            AdapterError::BackendUnavailable,
            AdapterError::BackendTimeout,
            AdapterError::BackendProtocolIncompatible,
            AdapterError::BackendRequestRejected,
            AdapterError::SubmissionOutcomeUnknown,
            AdapterError::UnexpectedBackendResponse,
            AdapterError::InvalidDirective(DirectiveError::InvalidDirective),
            AdapterError::ExecutionNotFound,
            AdapterError::BackendTaskMissing,
            AdapterError::BackendTaskModified,
            AdapterError::Output(OutputError::BackendLogUnavailable),
            AdapterError::InvalidTimeout,
            AdapterError::CancelBlocked,
            AdapterError::CancelRejected,
            AdapterError::CancelOutcomeUnknown,
        ];
        for error in errors {
            let result = error_result(error, Some(1), None);
            assert!(!result.ok);
            assert_eq!(result.error_kind, error.kind());
            assert!(!result.message.is_empty());
        }

        let record = PueueExecution::accepted(1, 42, &request(), false, Utc::now());
        for (outcome, expected) in [
            (CancelOutcome::Terminal, "terminal"),
            (CancelOutcome::Requested, "requested"),
            (CancelOutcome::AlreadyTerminal, "already_terminal"),
        ] {
            assert_eq!(
                cancel_observation(&record, outcome).cancel_outcome,
                expected
            );
        }
        assert_eq!(
            execution_observation(&record, Some(WaitOutcome::DeadlineElapsed), true).wait_outcome,
            Some("deadline_elapsed")
        );
    }

    #[test]
    #[expect(
        clippy::too_many_lines,
        reason = "one ordered snapshot must cover every frozen public result object together"
    )]
    fn public_result_field_order_null_policy_and_sensitive_surface_are_frozen() {
        let values = [
            serde_json::to_string(&error_result(
                AdapterError::ExecutionNotFound,
                Some(7),
                None,
            ))
            .unwrap(),
            serde_json::to_string(&AdapterStatus {
                ok: true,
                workspace: "/workspace".into(),
                workspace_resolution: "explicit_config",
                daemon_connected: false,
                daemon_profile: "default",
                daemon_error_kind: Some("backend_unavailable"),
                execution_count: 1,
                active_execution_count: 1,
                active_executions: vec![7],
                recent_execution: Some(7),
                backend_missing_executions: Vec::new(),
                backend_modified_executions: Vec::new(),
            })
            .unwrap(),
            serde_json::to_string(&ExecutionObservation {
                ok: true,
                execution: 7,
                status: ExecutionStatus::Locked,
                submitted_at: "2026-01-01T00:00:00+00:00".into(),
                updated_at: "2026-01-01T00:00:01+00:00".into(),
                enqueue_at: None,
                enqueued_at: None,
                started_at: None,
                completed_at: None,
                backend_previous_status: Some(BackendPreviousStatus::Queued),
                exit_code: None,
                failure_kind: None,
                error_summary: None,
                wait_outcome: Some("deadline_elapsed"),
                output_total_lines: 0,
                output_complete: false,
                output_decode_replacements: 0,
                output_text: None,
                output_omitted_reason: None,
                output_line_limit: None,
                output_error_kind: None,
            })
            .unwrap(),
            serde_json::to_string(&OutputReadResult {
                ok: true,
                execution: 7,
                status: ExecutionStatus::Running,
                output: OutputRead {
                    line_range: None,
                    total_lines: 0,
                    returned_lines: 0,
                    omitted_before: 0,
                    omitted_after: 0,
                    partial_final_line: false,
                    text: String::new(),
                },
                output_complete: false,
                output_decode_replacements: 0,
            })
            .unwrap(),
            serde_json::to_string(&OutputSearchResult {
                ok: true,
                execution: 7,
                status: ExecutionStatus::Completed,
                ignore_case: true,
                returned_lines: 1,
                partial_final_line: false,
                output_complete: true,
                output_decode_replacements: 0,
                output: OutputSearch {
                    query: "needle".into(),
                    query_interpretation: QueryMode::Literal,
                    matched_lines: 1,
                    matches: 1,
                    context_before: 0,
                    context_after: 0,
                    total_lines: 1,
                    text: "M 1 | needle".into(),
                },
            })
            .unwrap(),
            serde_json::to_string(&CancelObservation {
                ok: true,
                execution: 7,
                status: ExecutionStatus::Cancelled,
                cancel_outcome: "terminal",
                completed_at: Some("2026-01-01T00:00:02+00:00".into()),
                output_total_lines: 0,
                output_complete: false,
            })
            .unwrap(),
        ];
        let snapshot = values.join("\n");
        assert_eq!(
            format!("{:x}", Sha256::digest(snapshot.as_bytes())),
            "85d1197363d23e29983259ec532fea33eb7221454cec369b1791e9187a51f990"
        );
        for sensitive in [
            "task_id",
            "shared_secret",
            "environment",
            "original_command",
        ] {
            assert!(!snapshot.contains(sensitive));
        }

        let content = tool_result(
            &error_result(AdapterError::InvalidTimeout, Some(7), None),
            ResultMode::Content,
            true,
        );
        assert!(content.structured_content.is_none());
        assert_eq!(content.is_error, Some(true));
        let structured = tool_result(
            &AdapterStatus {
                ok: true,
                workspace: "/workspace".into(),
                workspace_resolution: "launch_cwd",
                daemon_connected: true,
                daemon_profile: "default",
                daemon_error_kind: None,
                execution_count: 0,
                active_execution_count: 0,
                active_executions: Vec::new(),
                recent_execution: None,
                backend_missing_executions: Vec::new(),
                backend_modified_executions: Vec::new(),
            },
            ResultMode::Structured,
            false,
        );
        assert!(structured.structured_content.is_some());
        assert_eq!(structured.is_error, Some(false));
    }
}
