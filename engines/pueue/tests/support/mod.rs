use std::{
    fs,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        OnceLock,
        atomic::{AtomicUsize, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use pueue_lib::settings::Settings;

static NEXT_HARNESS: AtomicUsize = AtomicUsize::new(0);
static VERSION_CHECK: OnceLock<()> = OnceLock::new();

pub struct IsolatedPueue {
    root: PathBuf,
    config: PathBuf,
    daemon: Child,
}

impl IsolatedPueue {
    pub fn start() -> Self {
        VERSION_CHECK.get_or_init(|| {
            for binary in ["pueue", "pueued"] {
                let output = Command::new(binary).arg("--version").output().unwrap();
                assert!(output.status.success(), "{binary} --version failed");
                assert_eq!(
                    String::from_utf8(output.stdout).unwrap().trim(),
                    format!("{binary} 4.0.4"),
                    "isolated integration must run against the frozen Pueue version"
                );
            }
        });
        let id = NEXT_HARNESS.fetch_add(1, Ordering::Relaxed);
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/i")
            .join(format!("{}-{id}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let mut settings = Settings::default();
        settings.shared.pueue_directory = Some(root.join("d"));
        settings.shared.runtime_directory = Some(root.clone());
        // Linux AF_UNIX paths are limited to roughly 108 bytes. These compact
        // names keep isolated tests valid in deeply nested clean worktrees.
        settings.shared.unix_socket_path = Some(root.join("s"));
        settings.shared.shared_secret_path = Some(root.join("k"));
        settings.shared.pid_path = Some(root.join("p"));
        fs::create_dir_all(root.join("d/task_logs")).unwrap();
        let config = root.join("pueue.yml");
        fs::write(&config, serde_json::to_vec_pretty(&settings).unwrap()).unwrap();
        let daemon = Self::spawn_daemon(&config);
        let harness = Self {
            root,
            config,
            daemon,
        };
        harness.wait_until_ready();
        harness
    }

    pub fn config(&self) -> &Path {
        &self.config
    }

    #[allow(dead_code)]
    pub fn workspace(&self) -> PathBuf {
        self.root.join("workspace")
    }

    // Each integration test is a separate crate, so cleanup-only accessors are
    // intentionally unused in the black-box crate that shares this support file.
    #[allow(dead_code)]
    pub fn root(&self) -> &Path {
        &self.root
    }

    #[allow(dead_code)]
    pub fn daemon_pid(&self) -> u32 {
        self.daemon.id()
    }

    #[allow(dead_code)]
    pub fn stop_daemon(&mut self) {
        let process_group = format!("-{}", self.daemon.id());
        let _ = Command::new("kill")
            .args(["-TERM", &process_group])
            .status();
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if self.daemon.try_wait().ok().flatten().is_some() {
                return;
            }
            thread::sleep(Duration::from_millis(20));
        }
        let _ = Command::new("kill")
            .args(["-KILL", &process_group])
            .status();
        let _ = self.daemon.kill();
        let _ = self.daemon.wait();
    }

    #[allow(dead_code)]
    pub fn restart_daemon(&mut self) {
        self.stop_daemon();
        // A stopped daemon may leave an AF_UNIX node behind briefly; retaining
        // it would let the file-based readiness gate accept the old endpoint.
        let _ = fs::remove_file(self.root.join("s"));
        let _ = fs::remove_file(self.root.join("p"));
        self.daemon = Self::spawn_daemon(&self.config);
        self.wait_until_ready();
    }

    fn spawn_daemon(config: &Path) -> Child {
        Command::new("setsid")
            .arg("pueued")
            .arg("--config")
            .arg(config)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("pueued 4.0.4 must be installed for isolated integration tests")
    }

    fn wait_until_ready(&self) {
        let deadline = Instant::now() + Duration::from_secs(10);
        let socket = self.root.join("s");
        let secret = self.root.join("k");
        while Instant::now() < deadline {
            if socket.exists() && secret.exists() {
                return;
            }
            thread::sleep(Duration::from_millis(25));
        }
        panic!("isolated pueued did not create its socket and secret");
    }
}

impl Drop for IsolatedPueue {
    fn drop(&mut self) {
        let _ = Command::new("pueue")
            .args(["--config"])
            .arg(&self.config)
            .args(["kill", "--all"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        self.stop_daemon();
        let _ = fs::remove_dir_all(&self.root);
        if let Some(parent) = self.root.parent() {
            let _ = fs::remove_dir(parent);
        }
    }
}
