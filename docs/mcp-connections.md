# MCP Connection Guide

`loommux` defaults to content-only results in every startup form:

1. `loommux` uses the MCP host's child process and Studio stdio connection.
2. `loommux --server` starts a local Streamable HTTP service at `http://HOST:PORT/PATH`.
3. `--result-mode structured` is the explicit opt-in that returns MCP `content` plus `structuredContent`.

The connection choice does not change the eight tools, one-kernel-per-process
lifetime, execution numbering, workspace resolution, text logs, image content
order, or the default content-only policy.

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
