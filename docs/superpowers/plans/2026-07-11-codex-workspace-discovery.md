# Codex Workspace Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let loommux automatically select the Codex workspace root and keep its integration suite fast enough for routine development.

**Architecture:** The user configuration file remains the extension boundary. A project-root configuration will use the existing `resolve_workspace(launch_cwd)` contract to locate the nearest ancestor containing `.codex`; without that marker it returns the launching cwd. Tests execute real kernels because their process and IOPub behavior is the production contract, while xdist parallelism removes unnecessary serial startup cost and pytest-timeout reports a stuck kernel deterministically.

**Tech Stack:** Python 3.13, pytest, pytest-xdist, pytest-timeout, FastMCP, IPython kernel.

---

### Task 1: Codex project-root configuration

**Files:**
- Create: `loommux_workspace.py`
- Test: `tests/test_workspace_config.py`

- [x] **Step 1: Write a failing configuration-discovery test**

```python
def test_project_config_uses_nearest_codex_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    nested = project / "src" / "feature"
    (project / ".codex").mkdir(parents=True)
    nested.mkdir()
    (project / "loommux_workspace.py").write_text(PROJECT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(nested)

    workspace, _python_path = resolve_workspace_launch()

    assert workspace == project
```

- [x] **Step 2: Run the focused test to verify it fails**

Run: `uv run pytest tests/test_workspace_config.py::test_project_config_uses_nearest_codex_ancestor -q --no-cov`

Expected: FAIL because `loommux_workspace.py` does not exist at the project root.

- [x] **Step 3: Add the project configuration**

```python
from pathlib import Path


def resolve_workspace(launch_cwd: Path) -> Path:
    for directory in (launch_cwd, *launch_cwd.parents):
        if (directory / ".codex").is_dir():
            return directory
    return launch_cwd
```

- [x] **Step 4: Run the configuration tests**

Run: `uv run pytest tests/test_workspace_config.py -q --no-cov`

Expected: PASS; the test also verifies that an absent `.codex` falls back to the MCP process cwd.

### Task 2: Bounded, parallel kernel tests

**Files:**
- Modify: `../../pyproject.toml`
- Modify: `pyproject.toml`

- [x] **Step 1: Add the timeout test dependency**

```toml
[dependency-groups]
dev = [
    "pytest-timeout",
]
```

Run `uv add --dev pytest-timeout` from the workbench root so the shared environment and lockfile remain aligned.

- [x] **Step 2: Configure process isolation and a test timeout**

```toml
[tool.pytest.ini_options]
timeout = 15
timeout_method = "thread"
addopts = [
    "-n",
    "4",
]
```

The real kernel tests keep one MCP server and kernel per test process. Four worker processes retain test isolation while overlapping the repeated startup cost. A 15-second timeout is substantially above the observed 1.68-second worst test and converts a kernel deadlock into a diagnostic failure.

- [x] **Step 3: Verify parallel execution and duration reduction**

Run: `uv run pytest --durations=25 -q`

Expected: PASS with 83 tests; the duration report must show kernel tests executing in workers and total runtime materially below the prior 56.88 seconds.

- [x] **Step 4: Run quality gates**

Run: `uv run ruff check src tests && uv run pytest`

Expected: both commands exit zero, with coverage at or above 90%.
