# loommux

[![CI](https://github.com/MichengLiang/loommux/actions/workflows/ci.yml/badge.svg)](https://github.com/MichengLiang/loommux/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/loommux.svg)](https://pypi.org/project/loommux/)
[![Python](https://img.shields.io/pypi/pyversions/loommux.svg)](https://pypi.org/project/loommux/)
[![License](https://img.shields.io/pypi/l/loommux.svg)](LICENSE)

`loommux` is a Model Context Protocol (MCP) server that gives an MCP client a
single persistent IPython kernel. It keeps Python namespace state between
cells, assigns stable server-local execution numbers, and retains separately
readable stdout, stderr, display-result, traceback, and combined output logs.

It is intended for agent and tool integrations that need interactive Python
work without treating a long-running cell as a lost request.

## Install

`loommux` requires Python 3.13 or newer.

```bash
python -m pip install loommux
```

For development, [uv](https://docs.astral.sh/uv/) is the supported workflow:

```bash
git clone https://github.com/MichengLiang/loommux.git
cd loommux
uv sync --group dev
```

## Connect an MCP client

The default `loommux` command uses MCP stdio transport. Configure the command
with the directory that should become the IPython workspace as its working
directory. A generic MCP client configuration looks like this:

```json
{
  "mcpServers": {
    "loommux": {
      "command": "loommux",
      "cwd": "/absolute/path/to/your/workspace"
    }
  }
}
```

The server starts one kernel at process startup. The kernel's namespace and
in-memory execution records live until that server process stops. By default,
the launch directory is the workspace and the interpreter that launched
`loommux` launches the kernel.

For a custom workspace-discovery rule, copy
[`loommux_workspace.py.example`](loommux_workspace.py.example) to
`loommux_workspace.py` in the launch directory or one of its parents. See
[workspace configuration](docs/workspace-configuration.md) for the complete
contract.

`loommux-content` provides the same tools over streamable HTTP on port `8801`.
It listens on all interfaces; place it behind appropriate network controls and
do not expose a Python execution service directly to untrusted networks.

## Tool model

`run_python` submits a raw Python cell to the persistent kernel. Every accepted
cell receives a session-local positive integer `execution` value. Use it to
inspect a specific historical record, or omit it and let tools select the
current running record, then the most recent record.

| Tool | Purpose |
| --- | --- |
| `run_python` | Submit a raw cell and wait for its initial result. |
| `wait_python` | Wait for a selected execution without interrupting it. |
| `python_execution_status` | Inspect lifecycle metadata without returning output text. |
| `read_python_output` | Read a stream with stable line coordinates. |
| `search_python_output` | Search a stream by literal text or regular expression. |
| `interrupt_python` | Request interruption of the current execution. |
| `reset_python` | Restart the kernel while retaining execution history. |
| `python_status` | Inspect workspace and kernel state. |

The `combined` stream preserves IOPub arrival order across stdout, stderr,
display results, and tracebacks. For ordinary terminal output longer than 300
lines, `run_python` and `wait_python` retain the execution but omit the body;
use the read or search tools. Add this exact, no-value comment to the submitted
cell when the whole terminal combined output is the intended result:

```python
# loommux: full_output
build_report()
```

The marker applies only to that execution. It bypasses the 300-line delivery
threshold only after the execution is terminal; a running execution still
returns its status and `execution` coordinate.

## Development

Run the release checks from the repository root:

```bash
uv run pytest
uv run ruff check src tests
uv run basedpyright src
uv build
uv run twine check dist/*
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution workflow and the
[design documents](docs/) for the execution and output contracts.

## Security

Running arbitrary Python is the central purpose of this server. Give an MCP
client access only to workspaces and environments it is allowed to execute in.
For vulnerabilities in loommux itself, follow [SECURITY.md](SECURITY.md).

## License

Copyright 2026 MichengLiang.

Licensed under the [Apache License, Version 2.0](LICENSE).
