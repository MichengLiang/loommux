#![cfg(unix)]

mod support;

use std::{fs, process::Command};

use pueue_lib::settings::Settings;
use support::IsolatedPueue;

#[test]
fn missing_daemon_fails_before_tool_ready_with_stable_stderr_only_error() {
    let root = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("target/startup-tests")
        .join(std::process::id().to_string());
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(root.join("runtime")).unwrap();
    fs::create_dir_all(root.join("data")).unwrap();
    fs::write(root.join("secret"), b"isolated-test-secret").unwrap();

    let mut settings = Settings::default();
    settings.shared.pueue_directory = Some(root.join("data"));
    settings.shared.runtime_directory = Some(root.join("runtime"));
    settings.shared.unix_socket_path = Some(root.join("runtime/missing.sock"));
    settings.shared.shared_secret_path = Some(root.join("secret"));
    settings.shared.pid_path = Some(root.join("runtime/missing.pid"));
    let config = root.join("pueue.yml");
    fs::write(&config, serde_json::to_vec_pretty(&settings).unwrap()).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_loommux-pueue"))
        .env("PUEUE_CONFIG_PATH", &config)
        .current_dir(&root)
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert_eq!(
        String::from_utf8(output.stderr).unwrap(),
        "backend_unavailable: Pueue daemon is unavailable\n"
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn missing_shared_secret_fails_before_tool_ready_without_starting_another_daemon() {
    let harness = IsolatedPueue::start();
    let daemon_pid = harness.daemon_pid();
    fs::remove_file(harness.root().join("k")).unwrap();

    let output = Command::new(env!("CARGO_BIN_EXE_loommux-pueue"))
        .env("PUEUE_CONFIG_PATH", harness.config())
        .current_dir(harness.root())
        .output()
        .unwrap();

    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert_eq!(
        String::from_utf8(output.stderr).unwrap(),
        "pueue_secret_failed: Pueue shared secret could not be read\n"
    );
    assert!(std::path::Path::new(&format!("/proc/{daemon_pid}")).exists());
}
