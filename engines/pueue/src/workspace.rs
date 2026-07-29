//! Cross-engine workspace configuration and resolution.

use std::{
    env, fs, io,
    path::{Path, PathBuf},
};

use serde::Deserialize;
use thiserror::Error;

pub const WORKSPACE_CONFIG_ENV: &str = "LOOMMUX_WORKSPACE_CONFIG";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceResolution {
    pub workspace: PathBuf,
    pub workspace_resolution: WorkspaceResolutionSource,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkspaceResolutionSource {
    LaunchCwd,
    ExplicitConfig,
}

impl WorkspaceResolutionSource {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::LaunchCwd => "launch_cwd",
            Self::ExplicitConfig => "explicit_config",
        }
    }
}

#[derive(Debug, Error)]
pub enum WorkspaceError {
    #[error("workspace configuration path must be absolute")]
    ConfigNotAbsolute,
    #[error("workspace configuration file does not exist")]
    ConfigNotFound,
    #[error("workspace configuration path is not a file")]
    ConfigNotFile,
    #[error("workspace configuration is not valid TOML")]
    ConfigParseFailed,
    #[error("workspace configuration version is unsupported")]
    ConfigVersionUnsupported,
    #[error("workspace configuration rule is invalid")]
    ConfigInvalidRule,
    #[error("resolved workspace does not exist")]
    WorkspaceNotFound,
    #[error("resolved workspace is not a directory")]
    WorkspaceNotDirectory,
    #[error("resolved workspace could not be canonicalized")]
    WorkspaceCanonicalizeFailed,
}

impl WorkspaceError {
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            Self::ConfigNotAbsolute => "workspace_config_not_absolute",
            Self::ConfigNotFound => "workspace_config_not_found",
            Self::ConfigNotFile => "workspace_config_not_file",
            Self::ConfigParseFailed => "workspace_config_parse_failed",
            Self::ConfigVersionUnsupported => "workspace_config_version_unsupported",
            Self::ConfigInvalidRule => "workspace_config_invalid_rule",
            Self::WorkspaceNotFound => "workspace_not_found",
            Self::WorkspaceNotDirectory => "workspace_not_directory",
            Self::WorkspaceCanonicalizeFailed => "workspace_canonicalize_failed",
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(tag = "strategy", rename_all = "snake_case", deny_unknown_fields)]
enum WorkspaceRule {
    LaunchCwd,
    NearestAncestorContainingMarker {
        marker: String,
        marker_type: MarkerType,
        fallback: Fallback,
    },
}

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum MarkerType {
    File,
    Directory,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Fallback {
    LaunchCwd,
}

/// Resolve workspace configuration from the process environment and launch cwd.
///
/// # Errors
///
/// Returns a stable [`WorkspaceError`] when the launch cwd, configuration file,
/// authored rule, or final canonical directory is invalid.
pub fn resolve_workspace_launch() -> Result<WorkspaceResolution, WorkspaceError> {
    let launch_cwd = match env::current_dir() {
        Ok(path) => path,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Err(WorkspaceError::WorkspaceNotFound);
        }
        Err(_) => return Err(WorkspaceError::WorkspaceCanonicalizeFailed),
    };
    let configured_path = env::var_os(WORKSPACE_CONFIG_ENV).map(PathBuf::from);
    resolve_workspace(&launch_cwd, configured_path.as_deref())
}

/// Resolve a workspace from explicit inputs shared by startup and contract tests.
///
/// # Errors
///
/// Returns a stable [`WorkspaceError`] for an invalid configuration path,
/// malformed or unsupported rule, or unusable final workspace directory.
pub fn resolve_workspace(
    launch_cwd: &Path,
    configured_path: Option<&Path>,
) -> Result<WorkspaceResolution, WorkspaceError> {
    let launch_cwd = canonicalize_workspace(launch_cwd)?;
    let Some(configured_path) = configured_path else {
        return Ok(WorkspaceResolution {
            workspace: launch_cwd,
            workspace_resolution: WorkspaceResolutionSource::LaunchCwd,
        });
    };
    let rule = load_workspace_rule(configured_path)?;
    let candidate = match rule {
        WorkspaceRule::LaunchCwd => launch_cwd.clone(),
        WorkspaceRule::NearestAncestorContainingMarker {
            marker,
            marker_type,
            fallback: Fallback::LaunchCwd,
        } => nearest_marker_parent(&launch_cwd, &marker, marker_type)
            .unwrap_or_else(|| launch_cwd.clone()),
    };
    Ok(WorkspaceResolution {
        workspace: canonicalize_workspace(&candidate)?,
        workspace_resolution: WorkspaceResolutionSource::ExplicitConfig,
    })
}

fn load_workspace_rule(configured_path: &Path) -> Result<WorkspaceRule, WorkspaceError> {
    if !configured_path.is_absolute() {
        return Err(WorkspaceError::ConfigNotAbsolute);
    }
    let metadata = fs::metadata(configured_path).map_err(|error| match error.kind() {
        io::ErrorKind::NotFound => WorkspaceError::ConfigNotFound,
        _ => WorkspaceError::ConfigParseFailed,
    })?;
    if !metadata.is_file() {
        return Err(WorkspaceError::ConfigNotFile);
    }
    let source =
        fs::read_to_string(configured_path).map_err(|_| WorkspaceError::ConfigParseFailed)?;
    let envelope: toml::Table =
        toml::from_str(&source).map_err(|_| WorkspaceError::ConfigParseFailed)?;
    let mut top_level_fields = envelope.keys().map(String::as_str).collect::<Vec<_>>();
    top_level_fields.sort_unstable();
    if top_level_fields != ["version", "workspace"] {
        return Err(WorkspaceError::ConfigInvalidRule);
    }
    if envelope.get("version").and_then(toml::Value::as_integer) != Some(1) {
        return Err(WorkspaceError::ConfigVersionUnsupported);
    }
    let workspace = envelope
        .get("workspace")
        .ok_or(WorkspaceError::ConfigInvalidRule)?;
    validate_rule_fields(workspace)?;
    let rule: WorkspaceRule = envelope
        .get("workspace")
        .cloned()
        .ok_or(WorkspaceError::ConfigInvalidRule)?
        .try_into()
        .map_err(|_| WorkspaceError::ConfigInvalidRule)?;
    if let WorkspaceRule::NearestAncestorContainingMarker { marker, .. } = &rule
        && !is_single_relative_name(marker)
    {
        return Err(WorkspaceError::ConfigInvalidRule);
    }
    Ok(rule)
}

fn validate_rule_fields(value: &toml::Value) -> Result<(), WorkspaceError> {
    let table = value.as_table().ok_or(WorkspaceError::ConfigInvalidRule)?;
    let strategy = table
        .get("strategy")
        .and_then(toml::Value::as_str)
        .ok_or(WorkspaceError::ConfigInvalidRule)?;
    let expected = match strategy {
        "launch_cwd" => ["strategy"].as_slice(),
        "nearest_ancestor_containing_marker" => {
            ["fallback", "marker", "marker_type", "strategy"].as_slice()
        }
        _ => return Err(WorkspaceError::ConfigInvalidRule),
    };
    let mut actual = table.keys().map(String::as_str).collect::<Vec<_>>();
    actual.sort_unstable();
    if actual != expected {
        return Err(WorkspaceError::ConfigInvalidRule);
    }
    Ok(())
}

fn nearest_marker_parent(
    launch_cwd: &Path,
    marker: &str,
    marker_type: MarkerType,
) -> Option<PathBuf> {
    launch_cwd.ancestors().find_map(|directory| {
        let metadata = fs::metadata(directory.join(marker)).ok()?;
        let matches = match marker_type {
            MarkerType::File => metadata.is_file(),
            MarkerType::Directory => metadata.is_dir(),
        };
        matches.then(|| directory.to_path_buf())
    })
}

fn is_single_relative_name(marker: &str) -> bool {
    !marker.is_empty() && marker != "." && marker != ".." && !marker.contains(['/', '\\'])
}

fn canonicalize_workspace(candidate: &Path) -> Result<PathBuf, WorkspaceError> {
    let workspace = fs::canonicalize(candidate).map_err(|error| match error.kind() {
        io::ErrorKind::NotFound => WorkspaceError::WorkspaceNotFound,
        _ => WorkspaceError::WorkspaceCanonicalizeFailed,
    })?;
    if !workspace.is_dir() {
        return Err(WorkspaceError::WorkspaceNotDirectory);
    }
    Ok(workspace)
}

#[cfg(test)]
mod tests {
    use std::{
        fs,
        path::PathBuf,
        sync::atomic::{AtomicUsize, Ordering},
    };

    use serde::Deserialize;

    use super::{WorkspaceError, canonicalize_workspace, resolve_workspace};

    #[derive(Deserialize)]
    struct ContractCase {
        config: String,
        workspace: Option<String>,
        resolution: Option<String>,
        error: Option<String>,
    }

    static NEXT_TEST_DIRECTORY: AtomicUsize = AtomicUsize::new(0);

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new() -> Self {
            let id = NEXT_TEST_DIRECTORY.fetch_add(1, Ordering::Relaxed);
            let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("target/workspace-tests")
                .join(format!("{}-{id}", std::process::id()));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir_all(&path).expect("test directory should be created");
            Self(path)
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn shared_contract_fixtures_parse_with_the_same_authored_surface() {
        let temp = TestDirectory::new();
        let project = temp.0.join("project");
        let launch = project.join("nested/launch");
        fs::create_dir_all(&launch).unwrap();
        fs::write(project.join(".workspace-root"), "").unwrap();
        let fixtures = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/workspace_contract");
        let cases: Vec<ContractCase> =
            serde_json::from_slice(&fs::read(fixtures.join("cases.json")).unwrap()).unwrap();
        for case in cases {
            let path = fixtures.join(case.config);
            let result = resolve_workspace(&launch, Some(&path));
            if let Some(expected_error) = case.error {
                assert_eq!(result.unwrap_err().kind(), expected_error);
            } else {
                let resolution = result.unwrap();
                let expected_workspace = match case.workspace.as_deref() {
                    Some("launch") => fs::canonicalize(&launch).unwrap(),
                    Some("project") => fs::canonicalize(&project).unwrap(),
                    _ => panic!("valid fixture must name its expected workspace"),
                };
                assert_eq!(resolution.workspace, expected_workspace);
                assert_eq!(
                    resolution.workspace_resolution.as_str(),
                    case.resolution.as_deref().unwrap()
                );
            }
        }
    }

    #[test]
    fn marker_resolution_is_nearest_canonical_and_type_sensitive() {
        let temp = TestDirectory::new();
        let project = temp.0.join("project");
        let nested = project.join("nested");
        let launch = nested.join("src");
        fs::create_dir_all(&launch).unwrap();
        fs::write(project.join(".workspace-root"), "").unwrap();
        fs::write(nested.join(".workspace-root"), "").unwrap();
        let config = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/workspace_contract/valid-marker.toml");

        let resolution = resolve_workspace(&launch, Some(&config)).unwrap();

        assert_eq!(resolution.workspace, fs::canonicalize(nested).unwrap());
        assert_eq!(resolution.workspace_resolution.as_str(), "explicit_config");
    }

    #[test]
    fn absent_config_uses_canonical_launch_cwd() {
        let temp = TestDirectory::new();
        let launch = temp.0.join("launch");
        fs::create_dir(&launch).unwrap();

        let resolution = resolve_workspace(&launch, None).unwrap();

        assert_eq!(resolution.workspace, fs::canonicalize(launch).unwrap());
        assert_eq!(resolution.workspace_resolution.as_str(), "launch_cwd");
    }

    #[test]
    fn config_path_and_workspace_failures_keep_stable_categories() {
        let temp = TestDirectory::new();
        let launch = temp.0.join("launch");
        fs::create_dir(&launch).unwrap();
        let directory = temp.0.join("config-directory");
        fs::create_dir(&directory).unwrap();
        let missing = temp.0.join("missing.toml");

        assert_eq!(
            resolve_workspace(&launch, Some(PathBuf::from("relative.toml").as_path()))
                .unwrap_err()
                .kind(),
            "workspace_config_not_absolute"
        );
        assert_eq!(
            resolve_workspace(&launch, Some(&missing))
                .unwrap_err()
                .kind(),
            "workspace_config_not_found"
        );
        assert_eq!(
            resolve_workspace(&launch, Some(&directory))
                .unwrap_err()
                .kind(),
            "workspace_config_not_file"
        );
        assert_eq!(
            resolve_workspace(&temp.0.join("missing-workspace"), None)
                .unwrap_err()
                .kind(),
            "workspace_not_found"
        );

        let kinds = [
            (
                WorkspaceError::ConfigNotAbsolute,
                "workspace_config_not_absolute",
            ),
            (WorkspaceError::ConfigNotFound, "workspace_config_not_found"),
            (WorkspaceError::ConfigNotFile, "workspace_config_not_file"),
            (
                WorkspaceError::ConfigParseFailed,
                "workspace_config_parse_failed",
            ),
            (
                WorkspaceError::ConfigVersionUnsupported,
                "workspace_config_version_unsupported",
            ),
            (
                WorkspaceError::ConfigInvalidRule,
                "workspace_config_invalid_rule",
            ),
            (WorkspaceError::WorkspaceNotFound, "workspace_not_found"),
            (
                WorkspaceError::WorkspaceNotDirectory,
                "workspace_not_directory",
            ),
            (
                WorkspaceError::WorkspaceCanonicalizeFailed,
                "workspace_canonicalize_failed",
            ),
        ];
        for (error, expected) in kinds {
            assert_eq!(error.kind(), expected);
        }
    }

    #[test]
    fn marker_type_mismatch_falls_back_and_root_is_valid() {
        let temp = TestDirectory::new();
        let project = temp.0.join("project");
        let launch = project.join("nested");
        fs::create_dir_all(&launch).unwrap();
        fs::create_dir(project.join(".workspace-root")).unwrap();
        let config = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../tests/fixtures/workspace_contract/valid-marker.toml");
        assert_eq!(
            resolve_workspace(&launch, Some(&config)).unwrap().workspace,
            fs::canonicalize(&launch).unwrap()
        );
        assert_eq!(
            resolve_workspace(PathBuf::from("/").as_path(), None)
                .unwrap()
                .workspace,
            PathBuf::from("/")
        );
    }

    #[test]
    fn ordinary_file_is_not_a_workspace() {
        let temp = TestDirectory::new();
        let file = temp.0.join("file");
        fs::write(&file, "not a directory").unwrap();
        assert!(matches!(
            canonicalize_workspace(&file),
            Err(WorkspaceError::WorkspaceNotDirectory)
        ));
    }

    #[cfg(unix)]
    #[test]
    fn unreadable_config_is_a_parse_failure() {
        use std::os::unix::fs::PermissionsExt;

        let temp = TestDirectory::new();
        let launch = temp.0.join("launch");
        fs::create_dir(&launch).unwrap();
        let config = temp.0.join("config.toml");
        fs::write(
            &config,
            "version = 1\n[workspace]\nstrategy = \"launch_cwd\"\n",
        )
        .unwrap();
        fs::set_permissions(&config, fs::Permissions::from_mode(0o0)).unwrap();
        let result = resolve_workspace(&launch, Some(&config));
        fs::set_permissions(&config, fs::Permissions::from_mode(0o600)).unwrap();
        assert!(matches!(result, Err(WorkspaceError::ConfigParseFailed)));
    }

    #[cfg(unix)]
    #[test]
    fn symlink_loop_is_a_canonicalization_failure() {
        use std::os::unix::fs::symlink;

        let temp = TestDirectory::new();
        let loop_path = temp.0.join("loop");
        symlink(&loop_path, &loop_path).unwrap();

        assert!(matches!(
            resolve_workspace(&loop_path, None),
            Err(WorkspaceError::WorkspaceCanonicalizeFailed)
        ));
    }
}
