# MCP Connection Guide

`loommux` defaults to content-only results in every startup form:

1. `loommux` uses the MCP host's child process and Studio stdio connection.
2. `loommux --server` starts a local Streamable HTTP service at `http://HOST:PORT/PATH`.
3. `--result-mode structured` is the explicit opt-in that returns MCP `content` plus `structuredContent`.

The connection choice does not change the eight tools, resource-local
execution numbering, workspace resolution, text logs, image content order, or
the default content-only policy. Stdio normally has one MCP participant; an
HTTP server can supervise the main kernels of many sessions, private or shared
by name, and their child kernels.

## Install

```bash
python -m pip install loommux
```

## Child Process Stdio

Use the no-argument command when an MCP Studio or host starts and supervises
loommux as a child process. The host's working directory is the default kernel
workspace.

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

On Windows, configure the MCP host with the installed `.exe` and escaped
absolute paths. Do not route the server through `cmd.exe`, PowerShell, or a
Unix compatibility shell:

```json
{
  "mcpServers": {
    "loommux": {
      "command": "C:\\workspace\\.venv\\Scripts\\loommux.exe",
      "cwd": "C:\\workspace"
    }
  }
}
```

The default result has no `structuredContent`. Use this explicit argument only
for a client that needs the raw status object:

```json
{
  "mcpServers": {
    "loommux": {
      "command": "loommux",
      "args": ["--result-mode", "structured"],
      "cwd": "/absolute/path/to/your/workspace"
    }
  }
}
```

## Streamable HTTP

Start a content-only server bound only to the local machine:

```bash
cd /absolute/path/to/your/workspace
loommux --server --host 127.0.0.1 --port 8801 --path /mcp
```

The remote MCP endpoint is:

```text
http://127.0.0.1:8801/mcp
```

Use `--result-mode structured` only as an explicit HTTP opt-in:

```bash
loommux --server --result-mode structured --host 127.0.0.1 --port 8802 --path /mcp
```

Its endpoint is `http://127.0.0.1:8802/mcp`. `--path /tools` would instead
produce `http://127.0.0.1:8802/tools`.

A host such as Claude Code keeps the session's standalone `GET` stream open
for as long as it runs and never pings. Start the server with a configuration
directory whose `config.py` sets `lease_mode = "connection"`, so its main kernel lives while the host is
connected, however idle, and, when the agent named it, for 24 hours after the
host closes, instead of expiring after 80 idle minutes:

```bash
loommux --server --host 127.0.0.1 --port 8801 --path /mcp --config .loommux
```

The lease modes and their alerts are described in
[Kernel Resource Daemon Design](kernel-resource-daemon-design.md#5-lease-policy).

The same HTTP application serves its operational console at `/` and JSON
control routes under `/api`. These routes do not appear in MCP `tools/list`.
For that reason the MCP path cannot be `/`, `/api`, or a child of `/api`.

Clients that need heartbeat leases or named sharing can use
`loommux.client.LeaseAwareClient`; it discovers the policy before MCP
initialization, pins its generation, and owns the standard ping task for the
connection lifetime.

PowerShell starts the same loopback-only endpoint on native Windows:

```powershell
Set-Location C:\workspace
loommux.exe --server --host 127.0.0.1 --port 8801 --path /mcp
```

## Studio Clients

An MCP Studio, Inspector, or other client that supports Streamable HTTP connects
to the exact endpoint URL above. It does not need a separate loommux protocol
or server mode. Choose the result mode before starting the service, then enter
the resulting URL in the Studio's remote-MCP connection field.

Use `127.0.0.1` for a Studio running on the same machine. Binding to `0.0.0.0`
or another network-facing address exposes arbitrary Python execution; use it
only with explicit network controls and authentication outside loommux.
