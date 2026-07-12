# Historical Design Notice

> Superseded by [Coding Agent Control Plane Design](coding-agent-control-plane-design.md). This retained design records the former implicit workspace and string-handle implementation; it is not a current public interface specification.

# IPython MCP Adapter 运行时契约

## 对象

loommux 是由 MCP host 启动的 server 进程。它在自身生命周期内管理一个
持久 IPython kernel，并把该 kernel 的执行、状态、输出、等待、中断和重置
投影为 MCP 工具。

`workspace` 是 kernel 子进程的工作目录。`python` 是启动 MCP server 的
Python 可执行文件。`execution` 是一次提交给 kernel 的 cell；它拥有稳定的
`execution_id`、终态和 output log。

## Workspace 选择

server 进程的 cwd 是 workspace 发现的起点。loommux 从该目录向上查找首个
`loommux_workspace.py`，并加载其中的 Python 配置。

配置可定义 `resolve_workspace(launch_cwd: Path) -> Path`，或定义
`WORKSPACE`。相对路径相对于 `launch_cwd` 解释。配置没有定义两者时，
workspace 等于 `launch_cwd`。

项目自带的 `loommux_workspace.py` 从 `launch_cwd` 向上查找 `.codex`，并
返回最近 `.codex` 的父目录；不存在该目录时返回 `launch_cwd`。该规则使
Codex stdio 子进程以 Codex 工作区根目录启动 kernel。

kernel 使用 `sys.executable` 启动。该值是已经成功加载 loommux 的 Python
进程的可执行文件；它保留虚拟环境解释器的符号链接路径。workspace 配置不
选择解释器，也不读取或改写 `PYTHONPATH`。

## 生命周期

MCP lifespan 开始时，server 解析 workspace 和 `sys.executable`，校验该
解释器可以导入 `ipykernel`，然后启动 kernel。任何 workspace、解释器或
kernel 启动失败都会使 server 初始化失败。

kernel 通过下列命令启动：

```text
<sys.executable> -m ipykernel_launcher -f <connection-file>
```

子进程 cwd 是已解析的 workspace。lifespan 结束时 server 关闭 kernel 和
monitor publisher。

`reset_python` 终止当前 kernel；若存在 running execution，该 execution
进入 `killed`；随后以相同 workspace 和 interpreter 启动新 kernel。

## 工具集合

| 工具 | 契约 |
| --- | --- |
| `run_python(freeform)` | 提交原始 Python cell，并等待默认 10 秒或 cell 中唯一有效 timeout directive 指定的时间。 |
| `python_status()` | 返回 workspace、interpreter、kernel pid、busy 状态和 execution 计数。 |
| `python_execution_status(execution_id=None)` | 返回 execution 的结构化状态和 canonical output log handle。 |
| `read_python_output(...)` | 按 stream、行范围和单行字符上限读取 output log。 |
| `search_python_output(...)` | 在 output log 中进行 literal、regex 或 auto 搜索。 |
| `wait_python(execution_id=None, timeout_seconds=30)` | 等待 selected execution 或在等待上限后返回其状态。 |
| `interrupt_python()` | 向当前 running execution 的 kernel 发送 interrupt。 |
| `reset_python()` | 重启当前 workspace 的 kernel。 |

工具集合不包含 workspace 或 interpreter 设置工具。workspace 是 server
进程启动边界的属性；`python_status` 是其可观察投影。

## Execution 状态

同一 kernel 一次只接受一个 running execution。busy 时 `run_python` 返回
`status="busy"`，不排队新 cell。每个成功提交的 execution 获得递增的
`exec-000001` 形式标识。

`run_python` 的等待上限只控制 MCP 调用何时返回。达到上限后 execution
继续运行，直到完成、报错、被 interrupt 或被 reset 杀死。`wait_python`、
`python_execution_status`、`read_python_output` 和 `search_python_output`
可以在后续调用中观察同一 execution。

## Output Log

每个 execution 持有 combined、stdout、stderr、result 和 traceback 五个
append-only line log。canonical handle 采用：

```text
python-output:<execution_id>
```

`/stdout`、`/stderr`、`/result` 和 `/traceback` 后缀选择分流日志。小型已
完成输出可直接作为 `run_python` 或 `wait_python` 的可见正文返回；running
execution 和超过行数上限的输出返回 handle，由日志工具继续读取。

展示文本和 structured content 的通道规则由
`docs/ipython-mcp-output-surface-design.md` 定义。

## 验证

workspace 发现测试覆盖 cwd 回退、父目录配置和 `.codex` 根目录发现。MCP
测试使用真实 IPython kernel 覆盖启动、持久状态、输出收集、超时、interrupt
和 reset。测试进程设置 15 秒上限，以失败形式报告 kernel 生命周期阻塞。
