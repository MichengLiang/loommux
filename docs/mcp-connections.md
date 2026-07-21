# MCP Connection Guide

`loommux` has two independent connection choices:

1. `--transport stdio` uses the MCP host's child process and standard input/output.
2. `--transport streamable-http` starts a remote MCP service at `http://HOST:PORT/PATH`.
3. `--result-mode structured` returns MCP `content` plus `structuredContent`.
4. `--result-mode content` returns only MCP `content` blocks.

The transport does not change the eight tools, one-kernel-per-process lifetime,
execution numbering, workspace resolution, text logs, or image content order.
The result mode changes only whether `structuredContent` accompanies the same
model-readable `content`.

## Install

```bash
python -m pip install loommux
```

## Child Process Stdio

Use stdio when an MCP host starts and supervises loommux as a child process.
The host's working directory is the default kernel workspace.

```json
{
  "mcpServers": {
    "loommux": {
      "command": "loommux",
      "args": ["--transport", "stdio", "--result-mode", "structured"],
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
      "args": ["--transport", "stdio", "--result-mode", "structured"],
      "cwd": "C:\\workspace"
    }
  }
}
```

For a host that must not receive `structuredContent`, use the same subprocess
transport with content-only results:

```json
{
  "mcpServers": {
    "loommux": {
      "command": "loommux",
      "args": ["--transport", "stdio", "--result-mode", "content"],
      "cwd": "/absolute/path/to/your/workspace"
    }
  }
}
```

## Streamable HTTP

Start a structured server bound only to the local machine:

```bash
cd /absolute/path/to/your/workspace
loommux --transport streamable-http --result-mode structured --host 127.0.0.1 --port 8801 --path /mcp
```

The remote MCP endpoint is:

```text
http://127.0.0.1:8801/mcp
```

Use content-only results on any chosen port independently of the transport:

```bash
loommux --transport streamable-http --result-mode content --host 127.0.0.1 --port 8802 --path /mcp
```

Its endpoint is `http://127.0.0.1:8802/mcp`. `--path /tools` would instead
produce `http://127.0.0.1:8802/tools`.

PowerShell starts the same loopback-only endpoint on native Windows:

```powershell
Set-Location C:\workspace
loommux.exe --transport streamable-http --result-mode structured --host 127.0.0.1 --port 8801 --path /mcp
```

`loommux-content` remains available as a compatibility shortcut. With no
arguments it starts content-only Streamable HTTP on `0.0.0.0:8801/mcp`; it also
accepts both flags, so `loommux-content --transport stdio --result-mode
structured` is valid. This executable retains its explicit Codex workspace
resolver convenience when `LOOMMUX_WORKSPACE_CONFIG` is not already set.

## Studio Clients

An MCP Studio, Inspector, or other client that supports Streamable HTTP connects
to the exact endpoint URL above. It does not need a separate loommux protocol
or server mode. Choose the result mode before starting the service, then enter
the resulting URL in the Studio's remote-MCP connection field.

Use `127.0.0.1` for a Studio running on the same machine. Binding to `0.0.0.0`
or another network-facing address exposes arbitrary Python execution; use it
only with explicit network controls and authentication outside loommux.
