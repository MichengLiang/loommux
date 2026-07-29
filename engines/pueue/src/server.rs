//! rmcp stdio server boundary and the seven-tool public surface.

use std::{sync::Arc, time::Duration};

use rmcp::{
    ServerHandler,
    handler::server::{router::tool::ToolRouter, wrapper::Parameters},
    model::{CallToolResult, Implementation, ServerCapabilities, ServerInfo},
    tool, tool_handler, tool_router,
};
use schemars::JsonSchema;
use serde::Deserialize;
use tokio::sync::Mutex;

use crate::{
    adapter::{AdapterError, PueueMcpAdapter, RefreshReport, WaitOutcome},
    deadline::{from_now, positive_duration},
    output::{OutputError, QueryMode, SearchRequest},
    pueue_gateway::PueueGateway,
    result::{
        AdapterStatus, OutputReadResult, OutputSearchResult, ResultMode, cancel_observation,
        error_result, execution_observation, tool_result,
    },
    workspace::WorkspaceResolution,
};

#[derive(Debug, Deserialize, JsonSchema)]
pub struct RunShellParameters {
    pub freeform: String,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ExecutionParameters {
    #[schemars(range(min = 1))]
    pub execution: u64,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ReadOutputParameters {
    #[schemars(range(min = 1))]
    pub execution: u64,
    pub line_range: Option<String>,
    #[schemars(range(min = 1))]
    pub max_chars: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SearchOutputParameters {
    pub query: String,
    #[schemars(range(min = 1))]
    pub execution: u64,
    #[serde(default)]
    pub query_mode: QueryMode,
    #[serde(default)]
    pub context_before: i64,
    #[serde(default)]
    pub context_after: i64,
    #[serde(default)]
    pub ignore_case: bool,
    #[schemars(range(min = 1))]
    pub max_chars: Option<usize>,
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct WaitParameters {
    #[schemars(range(min = 1))]
    pub execution: u64,
    #[serde(default = "default_wait_seconds")]
    pub timeout_seconds: f64,
}

const fn default_wait_seconds() -> f64 {
    30.0
}

#[derive(Debug, Clone)]
pub struct PueueServer {
    adapter: Arc<Mutex<PueueMcpAdapter>>,
    result_mode: ResultMode,
    tool_router: ToolRouter<Self>,
}

#[tool_router(router = tool_router)]
impl PueueServer {
    #[must_use]
    pub fn new(
        gateway: PueueGateway,
        result_mode: ResultMode,
        workspace: WorkspaceResolution,
    ) -> Self {
        Self {
            adapter: Arc::new(Mutex::new(PueueMcpAdapter::new(gateway, workspace))),
            result_mode,
            tool_router: Self::tool_router(),
        }
    }

    /// Submit one shell script and observe it for its authored initial wait.
    #[tool(
        name = "run_shell",
        description = "Submit one shell script to Pueue and return its adapter-local execution observation after a bounded initial wait."
    )]
    pub async fn run_shell(
        &self,
        Parameters(parameters): Parameters<RunShellParameters>,
    ) -> CallToolResult {
        let submitted = self
            .adapter
            .lock()
            .await
            .run_shell(&parameters.freeform)
            .await;
        match submitted {
            Ok((execution, prepared)) => self.wait_for(execution, prepared.initial_wait).await,
            Err(error) => self.render_error(error, None).await,
        }
    }

    /// Observe the adapter workspace, connection, and local execution collection.
    #[tool(
        name = "status",
        description = "Refresh and summarize this adapter's canonical workspace, daemon connection, and adapter-local execution collection without returning task logs."
    )]
    pub async fn status(&self) -> CallToolResult {
        let mut adapter = self.adapter.lock().await;
        let refresh = adapter.refresh_all().await;
        let (daemon_connected, daemon_error_kind, report) = match refresh {
            Ok(report) => (true, None, report),
            Err(error) => (false, Some(error.kind()), RefreshReport::default()),
        };
        let active_executions = adapter.active_executions();
        let value = AdapterStatus {
            ok: true,
            workspace: adapter.workspace().workspace.display().to_string(),
            workspace_resolution: adapter.workspace().workspace_resolution.as_str(),
            daemon_connected,
            daemon_profile: "default",
            daemon_error_kind,
            execution_count: adapter.execution_count(),
            active_execution_count: active_executions.len(),
            active_executions,
            recent_execution: adapter.recent_execution(),
            backend_missing_executions: report.backend_missing_executions,
            backend_modified_executions: report.backend_modified_executions,
        };
        tool_result(&value, self.result_mode, false)
    }

    /// Refresh one explicit execution without returning its transcript body.
    #[tool(
        name = "execution_status",
        description = "Refresh one required adapter-local execution and return lifecycle, result, and output-size metadata without transcript text."
    )]
    pub async fn execution_status(
        &self,
        Parameters(parameters): Parameters<ExecutionParameters>,
    ) -> CallToolResult {
        let mut adapter = self.adapter.lock().await;
        match adapter.observe_execution(parameters.execution).await {
            Ok(()) => {
                let record =
                    match record_after_success(self.result_mode, &adapter, parameters.execution) {
                        Ok(record) => record,
                        Err(result) => return result,
                    };
                let value = execution_observation(record, None, false);
                tool_result(&value, self.result_mode, false)
            }
            Err(error) => render_error_locked(
                self.result_mode,
                &adapter,
                error,
                Some(parameters.execution),
            ),
        }
    }

    /// Read an inclusive range from one execution's current combined transcript.
    #[tool(
        name = "read_output",
        description = "Refresh and read the current combined transcript for one required execution using inclusive line coordinates; omission of line_range requests all current lines."
    )]
    pub async fn read_output(
        &self,
        Parameters(parameters): Parameters<ReadOutputParameters>,
    ) -> CallToolResult {
        let mut adapter = self.adapter.lock().await;
        let output = adapter
            .read_output(
                parameters.execution,
                parameters.line_range.as_deref(),
                parameters.max_chars,
            )
            .await;
        match output {
            Ok(output) => {
                let record =
                    match record_after_success(self.result_mode, &adapter, parameters.execution) {
                        Ok(record) => record,
                        Err(result) => return result,
                    };
                let value = OutputReadResult {
                    ok: true,
                    execution: parameters.execution,
                    status: record.status,
                    output,
                    output_complete: record.status.is_terminal()
                        && record.output().finalized()
                        && record.status != crate::execution::ExecutionStatus::Cancelled,
                    output_decode_replacements: record.output().decode_replacements(),
                };
                tool_result(&value, self.result_mode, false)
            }
            Err(error) => render_error_locked(
                self.result_mode,
                &adapter,
                error,
                Some(parameters.execution),
            ),
        }
    }

    /// Search one execution's current combined transcript with optional context.
    #[tool(
        name = "search_output",
        description = "Refresh and search one required execution's complete current transcript with literal, regex, or automatic query interpretation and stable line coordinates."
    )]
    pub async fn search_output(
        &self,
        Parameters(parameters): Parameters<SearchOutputParameters>,
    ) -> CallToolResult {
        if parameters.context_before < 0 || parameters.context_after < 0 {
            return self
                .render_error(
                    AdapterError::Output(OutputError::InvalidContext),
                    Some(parameters.execution),
                )
                .await;
        }
        let request = SearchRequest {
            query: &parameters.query,
            query_mode: parameters.query_mode,
            context_before: usize::try_from(parameters.context_before).unwrap_or_default(),
            context_after: usize::try_from(parameters.context_after).unwrap_or_default(),
            ignore_case: parameters.ignore_case,
            max_chars: parameters.max_chars,
        };
        let mut adapter = self.adapter.lock().await;
        match adapter.search_output(parameters.execution, &request).await {
            Ok(output) => {
                let record =
                    match record_after_success(self.result_mode, &adapter, parameters.execution) {
                        Ok(record) => record,
                        Err(result) => return result,
                    };
                let returned_lines = output.text.lines().count();
                let value = OutputSearchResult {
                    ok: true,
                    execution: parameters.execution,
                    status: record.status,
                    ignore_case: parameters.ignore_case,
                    returned_lines,
                    partial_final_line: record.output().log().partial_final_line(),
                    output_complete: record.status.is_terminal()
                        && record.output().finalized()
                        && record.status != crate::execution::ExecutionStatus::Cancelled,
                    output_decode_replacements: record.output().decode_replacements(),
                    output,
                };
                tool_result(&value, self.result_mode, false)
            }
            Err(error) => render_error_locked(
                self.result_mode,
                &adapter,
                error,
                Some(parameters.execution),
            ),
        }
    }

    /// Wait within a caller-owned observation budget without limiting task runtime.
    #[tool(
        name = "wait",
        description = "Observe one required execution until terminal state or a positive representable deadline; deadline expiry does not cancel the task."
    )]
    pub async fn wait(&self, Parameters(parameters): Parameters<WaitParameters>) -> CallToolResult {
        let Some(duration) = positive_duration(parameters.timeout_seconds) else {
            return self
                .render_error(AdapterError::InvalidTimeout, Some(parameters.execution))
                .await;
        };
        self.wait_for(parameters.execution, duration).await
    }

    /// Cancel exactly one execution using task-specific remove or kill semantics.
    #[tool(
        name = "cancel",
        description = "Request cancellation of one required adapter-local execution without affecting other tasks, groups, or the shared daemon."
    )]
    pub async fn cancel(
        &self,
        Parameters(parameters): Parameters<ExecutionParameters>,
    ) -> CallToolResult {
        let mut adapter = self.adapter.lock().await;
        match adapter.cancel(parameters.execution).await {
            Ok(outcome) => {
                let record =
                    match record_after_success(self.result_mode, &adapter, parameters.execution) {
                        Ok(record) => record,
                        Err(result) => return result,
                    };
                let value = cancel_observation(record, outcome);
                tool_result(&value, self.result_mode, false)
            }
            Err(error) => render_error_locked(
                self.result_mode,
                &adapter,
                error,
                Some(parameters.execution),
            ),
        }
    }

    async fn wait_for(&self, execution: u64, budget: Duration) -> CallToolResult {
        let Some(deadline) = from_now(budget) else {
            return self
                .render_error(AdapterError::InvalidTimeout, Some(execution))
                .await;
        };
        loop {
            let mut adapter = self.adapter.lock().await;
            if let Err(error) = adapter.observe_execution(execution).await {
                return render_error_locked(self.result_mode, &adapter, error, Some(execution));
            }
            let record = match record_after_success(self.result_mode, &adapter, execution) {
                Ok(record) => record,
                Err(result) => return result,
            };
            if record.status.is_terminal() {
                let value = execution_observation(record, Some(WaitOutcome::Terminal), true);
                return tool_result(&value, self.result_mode, false);
            }
            let now = tokio::time::Instant::now();
            if now >= deadline {
                let value = execution_observation(record, Some(WaitOutcome::DeadlineElapsed), true);
                return tool_result(&value, self.result_mode, false);
            }
            drop(adapter);
            tokio::time::sleep((deadline - now).min(Duration::from_secs(2))).await;
        }
    }

    async fn render_error(&self, error: AdapterError, execution: Option<u64>) -> CallToolResult {
        let adapter = self.adapter.lock().await;
        render_error_locked(self.result_mode, &adapter, error, execution)
    }
}

fn render_error_locked(
    mode: ResultMode,
    adapter: &PueueMcpAdapter,
    error: AdapterError,
    execution: Option<u64>,
) -> CallToolResult {
    let status = execution
        .and_then(|value| adapter.execution(value))
        .map(|record| record.status);
    tool_result(&error_result(error, execution, status), mode, true)
}

fn record_after_success(
    mode: ResultMode,
    adapter: &PueueMcpAdapter,
    execution: u64,
) -> Result<&crate::execution::PueueExecution, CallToolResult> {
    match adapter.execution(execution) {
        Some(record) => Ok(record),
        None => Err(render_error_locked(
            mode,
            adapter,
            AdapterError::ExecutionNotFound,
            Some(execution),
        )),
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for PueueServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo::new(ServerCapabilities::builder().enable_tools().build()).with_server_info(
            Implementation::new("Loommux Pueue execution engine", env!("CARGO_PKG_VERSION")),
        )
    }
}

#[cfg(test)]
mod tests {
    use sha2::{Digest, Sha256};

    use super::PueueServer;

    #[test]
    fn tool_discovery_is_exact() {
        let tools = PueueServer::tool_router().list_all();
        let schema_snapshot = serde_json::to_vec(&tools).unwrap();
        assert_eq!(
            format!("{:x}", Sha256::digest(schema_snapshot)),
            "93ee0a02d19a4ee2aae8b8b1b2075c27f4adc8df7427f3809cb0fe98d5cc47c1",
            "tool names, descriptions, required fields, defaults, and input schemas are frozen"
        );
        let schemas = serde_json::to_value(&tools).unwrap();
        for name in [
            "cancel",
            "execution_status",
            "read_output",
            "search_output",
            "wait",
        ] {
            let schema = schemas
                .as_array()
                .unwrap()
                .iter()
                .find(|tool| tool["name"] == name)
                .unwrap();
            assert!(
                schema["inputSchema"]["required"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|field| field == "execution"),
                "{name} must require an explicit execution coordinate"
            );
        }
        let mut names = tools
            .into_iter()
            .map(|tool| tool.name.to_string())
            .collect::<Vec<_>>();
        names.sort();
        assert_eq!(
            names,
            [
                "cancel",
                "execution_status",
                "read_output",
                "run_shell",
                "search_output",
                "status",
                "wait"
            ]
        );
    }
}
