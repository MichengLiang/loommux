#![cfg(unix)]

mod support;

use std::{
    fs,
    path::Path,
    process::Command,
    time::{Duration, Instant},
};

use loommux_pueue::{
    adapter::{AdapterError, PueueMcpAdapter, WaitOutcome},
    execution::{BackendPresence, ExecutionStatus},
    output::{QueryMode, SearchRequest},
    pueue_gateway::PueueGateway,
    result::ResultMode,
    server::{
        ExecutionParameters, PueueServer, ReadOutputParameters, RunShellParameters,
        SearchOutputParameters, WaitParameters,
    },
    workspace::{WorkspaceResolution, WorkspaceResolutionSource},
};
use rmcp::{ServerHandler, handler::server::wrapper::Parameters};
use serde_json::Value;

use support::IsolatedPueue;

#[derive(Debug, Default)]
struct TestClock {
    elapsed: Duration,
}

impl TestClock {
    fn advance(&mut self, duration: Duration) {
        self.elapsed += duration;
    }

    fn elapsed(&self) -> Duration {
        self.elapsed
    }
}

#[tokio::test]
async fn isolated_daemon_runs_in_the_canonical_workspace_and_collects_output() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let mut adapter = PueueMcpAdapter::new(
        gateway,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );

    let (execution, _) = adapter
        .run_shell("printf 'hello from pueue\\n'; pwd")
        .await
        .unwrap();
    let outcome = adapter.wait(execution, 10.0).await.unwrap();
    let record = adapter.execution(execution).unwrap();

    assert_eq!(outcome, WaitOutcome::Terminal);
    assert_eq!(
        record.status,
        loommux_pueue::execution::ExecutionStatus::Completed
    );
    assert!(record.output().log().text().contains("hello from pueue"));
    assert!(
        record
            .output()
            .log()
            .text()
            .contains(workspace.to_str().unwrap())
    );
    assert!(record.output().finalized());
}

#[tokio::test]
async fn initial_wait_releases_the_adapter_for_concurrent_status_read_and_run() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let server = PueueServer::new(
        gateway,
        ResultMode::Structured,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );

    let submitted = structured(
        server
            .run_shell(Parameters(RunShellParameters {
                freeform: "# loommux: --wait 0.01\nsleep 1; printf 'long done\\n'".into(),
            }))
            .await,
    );
    assert_eq!(submitted["execution"], 1);
    assert_eq!(submitted["wait_outcome"], "deadline_elapsed");
    assert!(matches!(
        submitted["status"].as_str(),
        Some("queued" | "running")
    ));

    let waiting_server = server.clone();
    let waiting = tokio::spawn(async move {
        waiting_server
            .wait(Parameters(WaitParameters {
                execution: 1,
                timeout_seconds: 5.0,
            }))
            .await
    });
    tokio::time::sleep(Duration::from_millis(20)).await;

    let status = tokio::time::timeout(Duration::from_millis(500), server.status())
        .await
        .unwrap();
    assert_eq!(
        structured(status)["active_executions"],
        serde_json::json!([1])
    );
    let read = tokio::time::timeout(
        Duration::from_millis(500),
        server.read_output(Parameters(ReadOutputParameters {
            execution: 1,
            line_range: None,
            max_chars: None,
        })),
    )
    .await
    .unwrap();
    assert_eq!(structured(read)["execution"], 1);
    let quick = tokio::time::timeout(
        Duration::from_secs(1),
        server.run_shell(Parameters(RunShellParameters {
            freeform: "# loommux: --wait 0.01\nsleep 1; printf 'second done\\n'".into(),
        })),
    )
    .await
    .unwrap();
    assert_eq!(structured(quick)["execution"], 2);
    let active = structured(server.status().await);
    assert_eq!(active["active_executions"], serde_json::json!([1, 2]));
    assert_eq!(active["active_execution_count"], 2);

    let terminal = structured(waiting.await.unwrap());
    assert_eq!(terminal["status"], "completed");
    assert_eq!(terminal["exit_code"], 0);
    assert_eq!(terminal["wait_outcome"], "terminal");
    assert!(
        terminal["output_text"]
            .as_str()
            .unwrap()
            .contains("long done")
    );
}

#[tokio::test]
async fn two_adapters_share_one_daemon_without_sharing_execution_namespaces() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    run_pueue(&harness, &["pause", "--all"]);
    let resolution = || WorkspaceResolution {
        workspace: fs::canonicalize(&workspace).unwrap(),
        workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
    };
    let first = PueueServer::new(
        PueueGateway::connect_from_path(harness.config())
            .await
            .unwrap(),
        ResultMode::Structured,
        resolution(),
    );
    let second = PueueServer::new(
        PueueGateway::connect_from_path(harness.config())
            .await
            .unwrap(),
        ResultMode::Structured,
        resolution(),
    );

    for server in [&first, &second] {
        let submitted = structured(
            server
                .run_shell(Parameters(RunShellParameters {
                    freeform: "# loommux: --wait 0.01\nprintf isolated".into(),
                }))
                .await,
        );
        assert_eq!(submitted["execution"], 1);
        assert_eq!(submitted["wait_outcome"], "deadline_elapsed");
    }
    for server in [&first, &second] {
        let status = structured(server.status().await);
        assert_eq!(status["execution_count"], 1);
        assert_eq!(status["active_executions"], serde_json::json!([1]));
        assert!(!status.to_string().contains("task_id"));
        let cancelled = structured(
            server
                .cancel(Parameters(ExecutionParameters { execution: 1 }))
                .await,
        );
        assert_eq!(cancelled["status"], "cancelled");
    }
}

#[tokio::test]
async fn combined_transcript_preserves_backend_stdout_stderr_write_order() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let mut adapter = PueueMcpAdapter::new(
        gateway,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );

    let (execution, _) = adapter
        .run_shell(
            "printf 'out-1\\n'; printf 'err-1\\n' >&2; printf 'out-2\\n'; printf 'err-2\\n' >&2",
        )
        .await
        .unwrap();
    assert_eq!(
        adapter.wait(execution, 5.0).await.unwrap(),
        WaitOutcome::Terminal
    );
    assert_eq!(
        adapter.execution(execution).unwrap().output().log().text(),
        "out-1\nerr-1\nout-2\nerr-2\n"
    );
}

#[tokio::test]
async fn failed_exit_is_a_successful_observation_with_its_exact_code() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let server = PueueServer::new(
        PueueGateway::connect_from_path(harness.config())
            .await
            .unwrap(),
        ResultMode::Structured,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );
    let result = server
        .run_shell(Parameters(RunShellParameters {
            freeform: "printf 'expected failure\\n' >&2; exit 7".into(),
        }))
        .await;
    assert_eq!(result.is_error, Some(false));
    let value = structured(result);
    assert_eq!(value["ok"], true);
    assert_eq!(value["status"], "failed");
    assert_eq!(value["exit_code"], 7);
    assert_eq!(value["failure_kind"], "nonzero_exit");
}

#[tokio::test]
async fn content_and_structured_modes_project_the_same_public_semantics() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let mut projections = Vec::new();

    for mode in [ResultMode::Content, ResultMode::Structured] {
        let server = PueueServer::new(
            PueueGateway::connect_from_path(harness.config())
                .await
                .unwrap(),
            mode,
            WorkspaceResolution {
                workspace: fs::canonicalize(&workspace).unwrap(),
                workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
            },
        );
        let result = server
            .run_shell(Parameters(RunShellParameters {
                freeform: "printf 'same semantics\\n'".into(),
            }))
            .await;
        let content: Value =
            serde_json::from_str(result.content[0].as_text().unwrap().text.as_ref()).unwrap();
        if mode == ResultMode::Structured {
            assert_eq!(result.structured_content.as_ref(), Some(&content));
        } else {
            assert!(result.structured_content.is_none());
        }
        projections.push(serde_json::json!({
            "ok": content["ok"],
            "execution": content["execution"],
            "status": content["status"],
            "exit_code": content["exit_code"],
            "failure_kind": content["failure_kind"],
            "wait_outcome": content["wait_outcome"],
            "output_text": content["output_text"],
        }));
    }
    assert_eq!(projections[0], projections[1]);
}

#[tokio::test]
async fn automatic_output_line_limit_and_full_output_are_exercised_through_run_shell() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let server = PueueServer::new(
        PueueGateway::connect_from_path(harness.config())
            .await
            .unwrap(),
        ResultMode::Structured,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );
    let script = "i=1; while [ \"$i\" -le 301 ]; do printf 'line %s\\n' \"$i\"; i=$((i+1)); done";

    let ordinary = structured(
        server
            .run_shell(Parameters(RunShellParameters {
                freeform: script.into(),
            }))
            .await,
    );
    assert_eq!(ordinary["output_total_lines"], 301);
    assert!(ordinary["output_text"].is_null());
    assert_eq!(ordinary["output_omitted_reason"], "line_limit_exceeded");
    assert_eq!(ordinary["output_line_limit"], 300);
    let read = structured(
        server
            .read_output(Parameters(ReadOutputParameters {
                execution: 1,
                line_range: None,
                max_chars: None,
            }))
            .await,
    );
    assert_eq!(read["returned_lines"], 301);
    assert_eq!(read["text"].as_str().unwrap().lines().count(), 301);

    let full = structured(
        server
            .run_shell(Parameters(RunShellParameters {
                freeform: format!("# loommux: --full-output\n{script}"),
            }))
            .await,
    );
    assert_eq!(full["output_total_lines"], 301);
    assert_eq!(full["output_text"].as_str().unwrap().lines().count(), 301);
    assert!(full["output_omitted_reason"].is_null());
    assert!(full["output_line_limit"].is_null());
}

#[tokio::test]
async fn concurrent_status_exchanges_remain_typed_and_ordered() {
    let harness = IsolatedPueue::start();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let calls = (0..20).map(|_| {
        let gateway = gateway.clone();
        tokio::spawn(async move { gateway.status().await.unwrap().tasks.len() })
    });
    for call in calls {
        assert_eq!(call.await.unwrap(), 0);
    }
}

#[test]
fn isolated_harness_removes_daemon_socket_and_data_tree() {
    let harness = IsolatedPueue::start();
    let root = harness.root().to_owned();
    let harness_root = root.parent().unwrap().to_owned();
    let daemon_pid = harness.daemon_pid();
    assert!(root.join("s").exists());
    drop(harness);

    assert!(!root.exists());
    assert!(!harness_root.exists());
    assert!(!std::path::Path::new(&format!("/proc/{daemon_pid}")).exists());
}

#[test]
fn deterministic_clock_support_has_no_wall_clock_dependency() {
    let mut clock = TestClock::default();
    clock.advance(Duration::from_secs(2));
    clock.advance(Duration::from_millis(500));
    assert_eq!(clock.elapsed(), Duration::from_millis(2500));
}

fn structured(result: rmcp::model::CallToolResult) -> Value {
    result.structured_content.unwrap()
}

fn run_pueue(harness: &IsolatedPueue, arguments: &[&str]) {
    let output = Command::new("pueue")
        .arg("--config")
        .arg(harness.config())
        .args(arguments)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "pueue {arguments:?} failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn only_task_id(harness: &IsolatedPueue) -> usize {
    let output = Command::new("pueue")
        .arg("--config")
        .arg(harness.config())
        .args(["status", "--json"])
        .output()
        .unwrap();
    assert!(output.status.success());
    let state: Value = serde_json::from_slice(&output.stdout).unwrap();
    let tasks = state["tasks"].as_object().unwrap();
    assert_eq!(tasks.len(), 1);
    tasks.keys().next().unwrap().parse().unwrap()
}

async fn wait_for_file(path: &Path) -> u32 {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Ok(value) = fs::read_to_string(path) {
            return value.trim().parse().unwrap();
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for {}",
            path.display()
        );
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

fn process_state(pid: u32) -> Option<char> {
    let status = fs::read_to_string(format!("/proc/{pid}/status")).ok()?;
    status
        .lines()
        .find_map(|line| line.strip_prefix("State:\t"))
        .and_then(|value| value.chars().next())
}

async fn wait_for_status(
    adapter: &mut PueueMcpAdapter<PueueGateway>,
    execution: u64,
    expected: loommux_pueue::execution::ExecutionStatus,
) {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        adapter.observe_execution(execution).await.unwrap();
        if adapter.execution(execution).unwrap().status == expected {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "timed out waiting for {expected:?}"
        );
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

#[tokio::test]
async fn external_pause_resume_and_loommux_kill_cover_the_process_tree() {
    use loommux_pueue::{adapter::CancelOutcome, execution::ExecutionStatus};

    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let mut adapter = PueueMcpAdapter::new(
        gateway,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );
    let script = "echo $$ > leader.pid; sh -c 'echo $$ > child.pid; sleep 60 & echo $! > grandchild.pid; wait'";
    let (execution, _) = adapter.run_shell(script).await.unwrap();
    let leader = wait_for_file(&workspace.join("leader.pid")).await;
    let child = wait_for_file(&workspace.join("child.pid")).await;
    let grandchild = wait_for_file(&workspace.join("grandchild.pid")).await;
    wait_for_status(&mut adapter, execution, ExecutionStatus::Running).await;

    run_pueue(&harness, &["pause", "--all"]);
    wait_for_status(&mut adapter, execution, ExecutionStatus::Paused).await;
    for pid in [leader, child, grandchild] {
        assert_eq!(process_state(pid), Some('T'), "pid {pid} was not paused");
    }

    run_pueue(&harness, &["start", "--all"]);
    wait_for_status(&mut adapter, execution, ExecutionStatus::Running).await;
    for pid in [leader, child, grandchild] {
        assert_ne!(process_state(pid), Some('T'), "pid {pid} remained paused");
    }

    assert_eq!(
        adapter.cancel(execution).await.unwrap(),
        CancelOutcome::Requested
    );
    assert_eq!(
        adapter.wait(execution, 5.0).await.unwrap(),
        WaitOutcome::Terminal
    );
    assert_eq!(
        adapter.execution(execution).unwrap().status,
        ExecutionStatus::Killed
    );
    let deadline = Instant::now() + Duration::from_secs(5);
    while [leader, child, grandchild]
        .into_iter()
        .any(|pid| process_state(pid).is_some())
    {
        assert!(
            Instant::now() < deadline,
            "descendant process survived kill"
        );
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

#[tokio::test]
async fn external_stash_force_start_and_stashed_cancel_follow_typed_state() {
    use loommux_pueue::adapter::CancelOutcome;

    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let mut adapter = PueueMcpAdapter::new(
        gateway,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );

    run_pueue(&harness, &["pause", "--all"]);
    let (force_started, _) = adapter.run_shell("sleep 60").await.unwrap();
    run_pueue(&harness, &["stash", "--all"]);
    wait_for_status(&mut adapter, force_started, ExecutionStatus::Stashed).await;
    let task_id = only_task_id(&harness).to_string();
    run_pueue(&harness, &["start", &task_id]);
    wait_for_status(&mut adapter, force_started, ExecutionStatus::Running).await;
    assert_eq!(
        adapter.cancel(force_started).await.unwrap(),
        CancelOutcome::Requested
    );
    assert_eq!(
        adapter.wait(force_started, 5.0).await.unwrap(),
        WaitOutcome::Terminal
    );
    assert_eq!(
        adapter.execution(force_started).unwrap().status,
        ExecutionStatus::Killed
    );

    let (stashed_cancel, _) = adapter.run_shell("printf should-not-run").await.unwrap();
    run_pueue(&harness, &["stash", "--all"]);
    wait_for_status(&mut adapter, stashed_cancel, ExecutionStatus::Stashed).await;
    assert_eq!(
        adapter.cancel(stashed_cancel).await.unwrap(),
        CancelOutcome::Terminal
    );
    assert_eq!(
        adapter.execution(stashed_cancel).unwrap().status,
        ExecutionStatus::Cancelled
    );
}

#[tokio::test]
async fn external_remove_marks_a_nonterminal_execution_missing() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let mut adapter = PueueMcpAdapter::new(
        gateway,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );

    run_pueue(&harness, &["pause", "--all"]);
    let (execution, _) = adapter.run_shell("printf should-not-run").await.unwrap();
    let task_id = only_task_id(&harness).to_string();
    run_pueue(&harness, &["remove", &task_id]);

    assert_eq!(
        adapter.observe_execution(execution).await.unwrap_err(),
        AdapterError::BackendTaskMissing
    );
    let record = adapter.execution(execution).unwrap();
    assert_eq!(record.status, ExecutionStatus::Queued);
    assert_eq!(record.backend_presence, BackendPresence::Missing);
}

#[tokio::test]
async fn external_in_place_restart_does_not_reopen_a_terminal_execution() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let mut adapter = PueueMcpAdapter::new(
        gateway,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );

    let (execution, _) = adapter
        .run_shell("printf 'frozen generation\\n'")
        .await
        .unwrap();
    assert_eq!(
        adapter.wait(execution, 5.0).await.unwrap(),
        WaitOutcome::Terminal
    );
    let record = adapter.execution(execution).unwrap();
    let frozen_status = record.status;
    let frozen_updated_at = record.updated_at;
    let frozen_completed_at = record.completed_at;
    let frozen_text = record.output().log().text().to_owned();
    let task_id = only_task_id(&harness).to_string();

    run_pueue(&harness, &["restart", "--in-place", "--stashed", &task_id]);
    adapter.refresh_all().await.unwrap();
    assert_eq!(
        adapter
            .read_output(execution, None, None)
            .await
            .unwrap()
            .text,
        "frozen generation"
    );
    assert_eq!(
        adapter
            .search_output(
                execution,
                &SearchRequest {
                    query: "generation",
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
    assert_eq!(record.status, frozen_status);
    assert_eq!(record.updated_at, frozen_updated_at);
    assert_eq!(record.completed_at, frozen_completed_at);
    assert_eq!(record.output().log().text(), frozen_text);
}

#[tokio::test]
async fn status_remains_available_after_runtime_daemon_disconnect() {
    let mut harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let server = PueueServer::new(
        gateway,
        ResultMode::Structured,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );
    assert_eq!(structured(server.status().await)["daemon_connected"], true);

    harness.stop_daemon();
    let disconnected = structured(server.status().await);

    assert_eq!(disconnected["ok"], true);
    assert_eq!(disconnected["daemon_connected"], false);
    assert_eq!(disconnected["daemon_error_kind"], "backend_unavailable");

    let reconnect_failure = structured(server.status().await);
    assert_eq!(reconnect_failure["ok"], true);
    assert_eq!(reconnect_failure["daemon_connected"], false);
    assert_eq!(
        reconnect_failure["daemon_error_kind"],
        "backend_unavailable"
    );

    harness.restart_daemon();
    let reconnected = structured(server.status().await);
    assert_eq!(reconnected["ok"], true);
    assert_eq!(reconnected["daemon_connected"], true);
    assert!(reconnected["daemon_error_kind"].is_null());
}

#[tokio::test]
#[expect(
    clippy::too_many_lines,
    reason = "one sequential workflow keeps all seven handlers on the same adapter session"
)]
async fn direct_server_workflow_exercises_all_seven_tools_and_error_results() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let gateway = PueueGateway::connect_from_path(harness.config())
        .await
        .unwrap();
    let server = PueueServer::new(
        gateway,
        ResultMode::Structured,
        WorkspaceResolution {
            workspace: fs::canonicalize(&workspace).unwrap(),
            workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
        },
    );
    assert_eq!(
        server.get_info().server_info.name,
        "Loommux Pueue execution engine"
    );

    let run = structured(
        server
            .run_shell(Parameters(RunShellParameters {
                freeform: "printf 'alpha beta\\n'".into(),
            }))
            .await,
    );
    assert_eq!(run["execution"], 1);
    assert_eq!(run["status"], "completed");
    assert_eq!(run["exit_code"], 0);
    assert!(run["output_text"].as_str().unwrap().contains("alpha beta"));

    let status = structured(server.status().await);
    assert_eq!(status["execution_count"], 1);
    assert_eq!(status["recent_execution"], 1);
    assert_eq!(status["daemon_connected"], true);

    let observation = structured(
        server
            .execution_status(Parameters(ExecutionParameters { execution: 1 }))
            .await,
    );
    assert_eq!(observation["status"], "completed");
    assert!(observation["output_text"].is_null());

    let read = structured(
        server
            .read_output(Parameters(ReadOutputParameters {
                execution: 1,
                line_range: Some("1:1".into()),
                max_chars: Some(100),
            }))
            .await,
    );
    assert_eq!(read["returned_lines"], 1);
    assert!(read["text"].as_str().unwrap().contains("alpha beta"));

    let search = structured(
        server
            .search_output(Parameters(SearchOutputParameters {
                query: "beta".into(),
                execution: 1,
                query_mode: QueryMode::Literal,
                context_before: 0,
                context_after: 0,
                ignore_case: false,
                max_chars: None,
            }))
            .await,
    );
    assert_eq!(search["matches"], 1);

    let waited = structured(
        server
            .wait(Parameters(WaitParameters {
                execution: 1,
                timeout_seconds: 0.1,
            }))
            .await,
    );
    assert_eq!(waited["wait_outcome"], "terminal");

    let already_terminal = structured(
        server
            .cancel(Parameters(ExecutionParameters { execution: 1 }))
            .await,
    );
    assert_eq!(already_terminal["cancel_outcome"], "already_terminal");

    let invalid_run = structured(
        server
            .run_shell(Parameters(RunShellParameters {
                freeform: "# loommux: --wait 0\nprintf nope".into(),
            }))
            .await,
    );
    assert_eq!(invalid_run["error_kind"], "invalid_loommux_directive");
    let invalid_wait = structured(
        server
            .wait(Parameters(WaitParameters {
                execution: 1,
                timeout_seconds: f64::NAN,
            }))
            .await,
    );
    assert_eq!(invalid_wait["error_kind"], "invalid_timeout");
    let overflowing_wait = structured(
        server
            .wait(Parameters(WaitParameters {
                execution: 1,
                timeout_seconds: 1e300,
            }))
            .await,
    );
    assert_eq!(overflowing_wait["error_kind"], "invalid_timeout");
    let invalid_context = structured(
        server
            .search_output(Parameters(SearchOutputParameters {
                query: "x".into(),
                execution: 1,
                query_mode: QueryMode::Auto,
                context_before: -1,
                context_after: 0,
                ignore_case: false,
                max_chars: None,
            }))
            .await,
    );
    assert_eq!(invalid_context["error_kind"], "invalid_context");
    let missing = structured(
        server
            .execution_status(Parameters(ExecutionParameters { execution: 999 }))
            .await,
    );
    assert_eq!(missing["error_kind"], "execution_not_found");
}
