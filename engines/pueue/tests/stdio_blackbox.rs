#![cfg(unix)]

mod support;

use std::{
    fs,
    io::{BufRead, BufReader, Write},
    process::{Command, Stdio},
};

use serde_json::{Value, json};
use support::IsolatedPueue;

fn response(reader: &mut BufReader<std::process::ChildStdout>) -> Value {
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    assert!(!line.is_empty(), "server closed stdout before responding");
    serde_json::from_str(&line)
        .unwrap_or_else(|error| panic!("stdout was not pure JSON-RPC framing: {error}: {line:?}"))
}

fn request(stdin: &mut std::process::ChildStdin, value: &Value) {
    serde_json::to_writer(&mut *stdin, value).unwrap();
    stdin.write_all(b"\n").unwrap();
    stdin.flush().unwrap();
}

fn wait_for_backend(harness: &IsolatedPueue) {
    let output = Command::new("pueue")
        .arg("--config")
        .arg(harness.config())
        .args(["wait", "--all", "--quiet"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "independent Pueue wait failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

fn submit_persistent_task(
    harness: &IsolatedPueue,
) -> (
    std::process::Child,
    std::process::ChildStdin,
    BufReader<std::process::ChildStdout>,
) {
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_loommux-pueue"))
        .args(["--result-mode", "structured"])
        .env("PUEUE_CONFIG_PATH", harness.config())
        .current_dir(&workspace)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"lifecycle","version":"1"}}}),
    );
    let _ = response(&mut stdout);
    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
    );
    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"run_shell","arguments":{"freeform":"# loommux: --wait 0.05\nsleep 0.5; printf persisted > persisted.txt"}}}),
    );
    let submitted = response(&mut stdout);
    assert_eq!(
        submitted["result"]["structuredContent"]["wait_outcome"],
        "deadline_elapsed"
    );
    (child, stdin, stdout)
}

#[test]
fn stdio_discovers_exact_tools_and_keeps_structured_content_opt_in() {
    let harness = IsolatedPueue::start();
    let workspace = harness.workspace();
    fs::create_dir(&workspace).unwrap();
    let mut child = Command::new(env!("CARGO_BIN_EXE_loommux-pueue"))
        .args(["--result-mode", "structured"])
        .env("PUEUE_CONFIG_PATH", harness.config())
        .current_dir(&workspace)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());

    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"blackbox","version":"1"}}}),
    );
    let initialized = response(&mut stdout);
    assert_eq!(
        initialized["result"]["serverInfo"]["name"],
        "Loommux Pueue execution engine"
    );
    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
    );
    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}),
    );
    let listed = response(&mut stdout);
    let mut names = listed["result"]["tools"]
        .as_array()
        .unwrap()
        .iter()
        .map(|tool| tool["name"].as_str().unwrap())
        .collect::<Vec<_>>();
    names.sort_unstable();
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

    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"run_shell","arguments":{}}}),
    );
    let malformed = response(&mut stdout);
    assert!(malformed.get("error").is_some() || malformed["result"]["isError"] == true);
    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"status","arguments":{}}}),
    );
    let status_after_malformed = response(&mut stdout);
    assert_eq!(
        status_after_malformed["result"]["structuredContent"]["execution_count"],
        0
    );

    request(
        &mut stdin,
        &json!({"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"run_shell","arguments":{"freeform":"printf 'stdio-ok\\n'"}}}),
    );
    let run = response(&mut stdout);
    assert_eq!(run["result"]["isError"], false);
    assert_eq!(run["result"]["structuredContent"]["status"], "completed");
    assert!(
        run["result"]["structuredContent"]["output_text"]
            .as_str()
            .unwrap()
            .contains("stdio-ok")
    );
    assert!(
        run["result"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("completed")
    );

    drop(stdin);
    let _ = child.kill();
    let _ = child.wait();
}

#[test]
fn stdin_eof_and_sigterm_do_not_stop_accepted_tasks() {
    for terminate_with_signal in [false, true] {
        let harness = IsolatedPueue::start();
        let marker = harness.workspace().join("persisted.txt");
        let (mut child, stdin, stdout) = submit_persistent_task(&harness);
        drop(stdout);
        if terminate_with_signal {
            let status = Command::new("kill")
                .args(["-TERM", &child.id().to_string()])
                .status()
                .unwrap();
            assert!(status.success());
            drop(stdin);
        } else {
            drop(stdin);
        }
        let _ = child.wait().unwrap();
        wait_for_backend(&harness);
        assert_eq!(fs::read_to_string(&marker).unwrap(), "persisted");
    }
}
