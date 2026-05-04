# IPython MCP 适配层概要设计、详细设计与黑盒验收规格

## 1. 文档对象与适用范围

本文档定义 `loommux` 项目中的 IPython MCP 适配层。该适配层面向 Codex 这类响应式 agent 宿主，提供一个通过 MCP 工具调用访问的有状态 IPython kernel。宿主通过工具调用显式设置工作区，适配层使用该工作区下的虚拟环境启动 Python kernel，并在同一个 kernel 中连续执行后续代码。

本文档只描述当前人工制品的对象边界、接口契约、运行语义、状态模型、错误表面、黑盒测试规格与验收条件。本文档不描述通用 Python 沙箱，不描述自动环境创建，不描述依赖安装策略，不描述多用户隔离，不描述多 kernel 会话，不描述远程部署拓扑。

目标读者是实现该适配层的开发者、测试者和维护者。开发者应按本文档实现，不应在接口、状态、超时、输出、环境选择或错误处理上自行补充未定义行为。

## 2. 术语

**MCP server** 指由 Codex 通过 stdio transport 拉起的 `loommux` 服务进程。它负责暴露工具、保存适配层状态、管理 IPython kernel 子进程、收集执行输出。

**Codex 宿主** 指调用 MCP 工具的客户端运行体。本文档只要求宿主能够按 MCP 协议调用工具，不要求宿主提供 MCP Roots。

**工作区 workspace** 指由 `set_workspace(path)` 显式设置的目录。该目录是 Python kernel 的项目根目录，也是虚拟环境解析的唯一基准目录。

**工作区 Python** 指 `<workspace>/.venv/bin/python`。适配层执行用户代码时只使用该解释器。

**kernel** 指由工作区 Python 启动的 IPython/Jupyter kernel 子进程。kernel 保存 Python 变量、导入、对象、当前目录、后台线程和执行状态。

**execution** 指一次 `run_python` 提交的代码执行对象。execution 有唯一 `execution_id`，并保存状态、输出、错误和时间信息。

**输出缓存 output buffer** 指 MCP server 在内存中为 execution 保存的 stdout、stderr、result text 和 error 数据。Codex 后续通过读取工具查看这些数据。

**运行中 running** 指代码已经提交给 kernel，且尚未收到该 execution 对应的 idle 完成消息。

**完成 completed** 指 execution 已经结束，且没有错误。

**错误 error** 指 execution 已经结束，且 IPython kernel 返回 Python 异常。

**中断请求 interrupt_sent** 指适配层已经向 kernel 发送 SIGINT。该状态不等于 execution 已经结束。

**重置 reset** 指终止当前 kernel，并使用当前 workspace 的工作区 Python 启动新的 kernel。

## 3. 概要设计

### 3.1 设计目标

IPython MCP 适配层的目标是把 Codex 的离散工具调用连接到一个持续存在的 Python 运行时。Codex 可以在一次调用中定义变量，在后续调用中继续使用该变量；可以启动一段较长的 Python 执行，在工具超时返回后继续查看输出、等待完成、中断执行或重置运行时。

该适配层的对象目标不是让 MCP server 替代 shell，也不是让 MCP server 管理项目环境。shell 适合一次性命令和文件系统操作；IPython MCP 适配层适合连续 Python 探索、状态保留和对长时间 Python 执行的分段观察。

### 3.2 核心边界

适配层只对以下对象负责：

1. 接收并保存显式设置的 workspace。
2. 使用 `<workspace>/.venv/bin/python` 启动 kernel。
3. 向 kernel 提交 Python code。
4. 收集 kernel 输出。
5. 维护 execution 状态。
6. 提供状态查询、输出读取、等待、中断和重置工具。

适配层不对以下对象负责：

1. 不自动发现 workspace。
2. 不自动创建 `.venv`。
3. 不自动安装 Python 包。
4. 不自动切换解释器。
5. 不自动修正用户代码改变的 kernel cwd。
6. 不提供 shell 命令包装。
7. 不实现 Python 沙箱。
8. 不支持 Windows。
9. 不支持多 kernel 会话。
10. 不维护持久化执行历史。

### 3.3 运行结构

运行结构如下：

```text
Codex 宿主
  └── stdio MCP server process: loommux IPython adapter
        └── IPython/Jupyter kernel process: <workspace>/.venv/bin/python -m ipykernel_launcher
```

MCP server 与 kernel 是两个进程。MCP server 是控制面和状态存储面；kernel 是 Python 代码执行面。MCP server 退出时应终止其管理的 kernel。`reset_python` 应终止当前 kernel 并重新启动新 kernel。

### 3.4 主要工具集合

适配层暴露以下 MCP 工具：

```text
set_workspace(path)
run_python(code, timeout_seconds=30)
python_status()
read_python_output(execution_id=None)
wait_python(execution_id=None, timeout_seconds=30)
interrupt_python()
reset_python()
```

工具集合不包含 `start_python`。设置 workspace 本身即启动 kernel。工具集合不包含输出截断参数。Codex 宿主负责处理工具返回截断；适配层负责返回已收集输出。

### 3.5 基本调用流程

首次使用：

```text
set_workspace("/home/t103o/workbench")
run_python("x = 41")
run_python("x + 1")
```

长任务使用：

```text
run_python("长时间运行的代码", timeout_seconds=30) -> status = running, execution_id = "exec-0001"
read_python_output("exec-0001")
wait_python("exec-0001", timeout_seconds=30)
interrupt_python()
wait_python("exec-0001", timeout_seconds=5)
```

状态清空：

```text
reset_python()
```

切换工作区：

```text
set_workspace("/another/workspace")
```

切换工作区会关闭旧 kernel 并启动新 kernel。

## 4. 详细设计

### 4.1 工作区设置

`set_workspace(path)` 是工作区设置的唯一入口。该工具接收路径字符串，解析为绝对路径，并使用解析后的目录作为 workspace。

路径解释规则：

1. 绝对路径保持其绝对含义。
2. 相对路径按 MCP server 进程当前 cwd 解析。
3. `~` 展开为当前用户 home。
4. 解析结果必须存在且必须是目录。

虚拟环境规则：

1. 工作区 Python 固定为 `<workspace>/.venv/bin/python`。
2. 如果该文件不存在，`set_workspace` 失败。
3. 如果该文件不可执行，`set_workspace` 失败。
4. 如果该 Python 无法 `import ipykernel`，`set_workspace` 失败。
5. 适配层不得尝试其它 Python 路径。
6. 适配层不得创建 `.venv`。
7. 适配层不得安装 `ipykernel`。

执行规则：

1. 如果当前没有 kernel，直接设置 workspace 并启动 kernel。
2. 如果当前已有 kernel，先终止旧 kernel，再设置 workspace 并启动新 kernel。
3. 如果新 kernel 启动失败，旧 kernel 不恢复。
4. 启动成功后，execution 历史清空。

返回示例：

```json
{
  "ok": true,
  "workspace": "/home/t103o/workbench",
  "python": "/home/t103o/workbench/.venv/bin/python",
  "kernel_started": true,
  "kernel_pid": 12345,
  "busy": false,
  "current_execution_id": null,
  "execution_count": 0
}
```

失败返回必须包含可定位原因。失败原因至少包括：

1. `workspace_not_found`
2. `workspace_not_directory`
3. `python_not_found`
4. `python_not_executable`
5. `ipykernel_missing`
6. `kernel_start_timeout`
7. `kernel_start_failed`

失败返回示例：

```json
{
  "ok": false,
  "status": "python_not_found",
  "message": "workspace Python does not exist",
  "workspace": "/home/t103o/workbench",
  "python": "/home/t103o/workbench/.venv/bin/python"
}
```

### 4.2 Kernel 生命周期

kernel 生命周期由 MCP server 管理。kernel 只在以下场景启动：

1. `set_workspace(path)` 成功设置 workspace。
2. `reset_python()` 在已有 workspace 下重启 kernel。

kernel 只在以下场景终止：

1. `set_workspace(path)` 被再次调用。
2. `reset_python()` 被调用。
3. MCP server 正常退出。
4. MCP server 检测到 kernel 已死亡并在状态中报告。

`run_python` 不负责启动 kernel。没有 kernel 时调用 `run_python` 必须失败。

kernel 启动命令：

```text
<workspace>/.venv/bin/python -m ipykernel_launcher -f <connection_file>
```

kernel cwd：

```text
<workspace>
```

用户代码可以改变 kernel cwd。适配层不自动恢复。该变化属于持久 Python 运行时状态。

### 4.3 Execution 状态模型

每次 `run_python` 成功提交代码时创建 execution。execution 对象字段如下：

```text
execution_id: str
code: str
status: running | completed | error | interrupted | killed
stdout: str
stderr: str
result_text: str
error: object | null
submitted_at: float
updated_at: float
completed_at: float | null
kernel_pid: int
execution_count_at_submit: int | null
```

`execution_id` 必须稳定。建议格式为：

```text
exec-000001
exec-000002
exec-000003
```

状态转换规则：

1. `run_python` 提交成功后，execution 进入 `running`。
2. 收到 kernel `status: idle` 且没有 error 时，execution 进入 `completed`。
3. 收到 kernel `error` 后，execution 进入 `error`。
4. `interrupt_python` 发送 SIGINT 后，execution 不立即改变为 `interrupted`；只有当 kernel 对该 execution 返回 KeyboardInterrupt 或 idle 后，状态才更新。
5. `reset_python` 终止 kernel 时，当前 running execution 进入 `killed`。

一个 kernel 同一时间只允许一个 running execution。适配层不排队。

### 4.4 输出收集

MCP server 必须在 `run_python` 返回后继续收集 kernel 输出。该要求是长任务观察语义的核心。

实现上应存在一个 output collector。collector 持续读取 kernel IOPub 消息，并将消息归属到当前 execution。

消息映射规则：

1. `stream` 且 `name == "stdout"` 追加到 `stdout`。
2. `stream` 且 `name == "stderr"` 追加到 `stderr`。
3. `execute_result` 的 `data["text/plain"]` 追加到 `result_text`。
4. `display_data` 的 `data["text/plain"]` 追加到 `result_text`。
5. `error` 写入 `error` 字段，并将 traceback 保存为字符串列表。
6. `status idle` 标记 execution 完成。

适配层不截断 stdout、stderr、result_text。宿主的显示层或上下文层可以截断工具返回。适配层只负责返回当前缓存。

如果未来需要避免内存无限增长，应通过独立规格定义输出保留策略。当前规格不加入截断参数。

### 4.5 `run_python`

工具定义：

```text
run_python(code: str, timeout_seconds: float = 30)
```

输入规则：

1. `code` 必须是字符串。
2. 空字符串允许提交，由 IPython kernel 决定执行结果。
3. `timeout_seconds` 必须大于 0。
4. 不提供输出截断参数。

执行规则：

1. 如果 workspace 未设置，返回 `workspace_not_set`。
2. 如果 kernel 未启动，返回 `kernel_not_started`。
3. 如果 kernel 已有 running execution，返回 `busy`，且不提交新代码。
4. 如果 kernel 空闲，创建 execution 并提交 code。
5. 当前工具调用等待最多 `timeout_seconds` 秒。
6. 如果 execution 在等待期间完成，返回 `completed` 或 `error`。
7. 如果 execution 在等待期满时仍未完成，返回 `running`。
8. 返回 `running` 时，execution 继续在 kernel 中执行。

完成返回：

```json
{
  "ok": true,
  "status": "completed",
  "execution_id": "exec-000001",
  "stdout": "",
  "stderr": "",
  "result_text": "42",
  "error": null,
  "kernel": {
    "busy": false,
    "kernel_pid": 12345,
    "execution_count": 2
  }
}
```

运行中返回：

```json
{
  "ok": true,
  "status": "running",
  "execution_id": "exec-000002",
  "stdout": "already produced output\n",
  "stderr": "",
  "result_text": "",
  "error": null,
  "kernel": {
    "busy": true,
    "kernel_pid": 12345,
    "execution_count": 3
  }
}
```

busy 返回：

```json
{
  "ok": false,
  "status": "busy",
  "current_execution_id": "exec-000002",
  "message": "kernel is already executing code"
}
```

### 4.6 `python_status`

工具定义：

```text
python_status()
```

该工具只读状态，不等待执行，不读取新增消息，不改变 kernel。

返回：

```json
{
  "ok": true,
  "workspace": "/home/t103o/workbench",
  "python": "/home/t103o/workbench/.venv/bin/python",
  "kernel_started": true,
  "kernel_pid": 12345,
  "busy": true,
  "current_execution_id": "exec-000002",
  "execution_count": 3,
  "last_execution_id": "exec-000002"
}
```

如果 workspace 未设置：

```json
{
  "ok": true,
  "workspace": null,
  "python": null,
  "kernel_started": false,
  "kernel_pid": null,
  "busy": false,
  "current_execution_id": null,
  "execution_count": 0,
  "last_execution_id": null
}
```

### 4.7 `read_python_output`

工具定义：

```text
read_python_output(execution_id: str | null = null)
```

选择规则：

1. 如果传入 `execution_id`，读取指定 execution。
2. 如果未传入 `execution_id` 且存在 current execution，读取 current execution。
3. 如果未传入 `execution_id` 且不存在 current execution，读取 last execution。
4. 如果没有可读取 execution，返回 `execution_not_found`。

该工具不等待。该工具返回当前缓存快照。

返回：

```json
{
  "ok": true,
  "execution_id": "exec-000002",
  "status": "running",
  "stdout": "partial output",
  "stderr": "",
  "result_text": "",
  "error": null
}
```

### 4.8 `wait_python`

工具定义：

```text
wait_python(execution_id: str | null = null, timeout_seconds: float = 30)
```

选择规则与 `read_python_output` 相同。

执行规则：

1. 如果 execution 已经结束，立即返回缓存快照。
2. 如果 execution 正在运行，等待最多 `timeout_seconds` 秒。
3. 如果等待期间完成，返回 `completed` 或 `error`。
4. 如果等待期满仍未完成，返回 `running`。
5. `wait_python` 不中断 execution。
6. `wait_python` 不提交新代码。

返回形状与 `read_python_output` 一致，并附带 kernel 状态：

```json
{
  "ok": true,
  "execution_id": "exec-000002",
  "status": "running",
  "stdout": "partial output",
  "stderr": "",
  "result_text": "",
  "error": null,
  "kernel": {
    "busy": true,
    "kernel_pid": 12345
  }
}
```

### 4.9 `interrupt_python`

工具定义：

```text
interrupt_python()
```

执行规则：

1. 如果 kernel 未启动，返回 `kernel_not_started`。
2. 如果 kernel 空闲，返回 `idle`。
3. 如果 kernel busy，向 kernel 进程发送 SIGINT。
4. 返回 `interrupt_sent`。
5. 该工具不等待 execution 完成。

返回：

```json
{
  "ok": true,
  "status": "interrupt_sent",
  "execution_id": "exec-000002",
  "kernel_pid": 12345
}
```

Codex 应随后调用 `wait_python` 或 `python_status` 判断执行是否结束。

### 4.10 `reset_python`

工具定义：

```text
reset_python()
```

执行规则：

1. 如果 workspace 未设置，返回 `workspace_not_set`。
2. 如果 kernel 正在运行，终止 kernel。
3. 如果存在 running execution，将其状态标记为 `killed`。
4. 清空 current execution。
5. 使用当前 workspace 的工作区 Python 启动新 kernel。
6. 返回新 kernel 状态。

返回：

```json
{
  "ok": true,
  "status": "restarted",
  "workspace": "/home/t103o/workbench",
  "python": "/home/t103o/workbench/.venv/bin/python",
  "kernel_started": true,
  "kernel_pid": 23456,
  "busy": false,
  "current_execution_id": null,
  "execution_count": 0
}
```

### 4.11 进程终止

适配层只支持 Linux。终止 kernel 时应先发送正常终止信号，再在短等待后发送强制终止信号。

推荐顺序：

1. `SIGTERM`
2. 等待 3 秒。
3. 如果仍未退出，发送 `SIGKILL`。

`interrupt_python` 使用 `SIGINT`。

如果 kernel 启动了子进程或后台程序，重置语义应尽量终止 kernel 所在进程组。实现者应使用 Linux process group 管理 kernel，避免 reset 后留下由 kernel 派生的长期子进程。

## 5. 黑盒测试规格与验收

### 5.1 测试环境

测试在 Linux 下执行。项目使用 Python 3.13。测试工作区必须有：

```text
<workspace>/.venv/bin/python
```

该 Python 必须能导入：

```python
import ipykernel
```

测试通过 MCP client 调用工具，不检查内部类、内部锁、内部消息循环实现。除非测试 stdio 客户端边界，测试可以使用 FastMCP in-memory client 导入 server。

### 5.2 测试用例 WS-001：初始状态

目的：确认适配层启动后没有默认 workspace，也没有默认 kernel。

步骤：

1. 启动 MCP server。
2. 调用 `python_status()`。

期望：

1. `ok == true`。
2. `workspace == null`。
3. `python == null`。
4. `kernel_started == false`。
5. `busy == false`。
6. `current_execution_id == null`。

### 5.3 测试用例 WS-002：设置 workspace 并启动 kernel

目的：确认 `set_workspace` 同时完成 workspace 设置和 kernel 启动。

步骤：

1. 准备有效 workspace。
2. 调用 `set_workspace(path)`。

期望：

1. `ok == true`。
2. `workspace` 等于传入路径的绝对路径。
3. `python` 等于 `<workspace>/.venv/bin/python`。
4. `kernel_started == true`。
5. `kernel_pid` 是整数。
6. `busy == false`。

### 5.4 测试用例 WS-003：缺失 workspace

目的：确认不存在的 workspace 不会被创建。

步骤：

1. 调用 `set_workspace("/path/not/exist")`。

期望：

1. `ok == false`。
2. `status == "workspace_not_found"`。
3. 不启动 kernel。

### 5.5 测试用例 WS-004：缺失工作区 Python

目的：确认适配层只认 `<workspace>/.venv/bin/python`。

步骤：

1. 准备一个存在的空目录作为 workspace。
2. 调用 `set_workspace(path)`。

期望：

1. `ok == false`。
2. `status == "python_not_found"`。
3. 返回中包含期望 Python 路径。
4. 不尝试系统 Python。

### 5.6 测试用例 EXEC-001：状态保留

目的：确认多次 `run_python` 使用同一个 kernel。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 `run_python("x = 41")`。
3. 调用 `run_python("x + 1")`。

期望：

1. 第二次 `run_python` 返回 `ok == true`。
2. `status == "completed"`。
3. `result_text` 包含 `42`。
4. 两次执行的 `kernel_pid` 相同。

### 5.7 测试用例 EXEC-002：stdout 收集

目的：确认 stdout 被收集。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 `run_python("print('hello')")`。

期望：

1. `status == "completed"`。
2. `stdout` 包含 `hello`。
3. `stderr` 为空或不包含该文本。

### 5.8 测试用例 EXEC-003：stderr 收集

目的：确认 stderr 被收集。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 `run_python("import sys; print('bad', file=sys.stderr)")`。

期望：

1. `status == "completed"`。
2. `stderr` 包含 `bad`。

### 5.9 测试用例 EXEC-004：异常返回

目的：确认 Python 异常成为 execution error，而不是 MCP server 崩溃。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 `run_python("1 / 0")`。

期望：

1. `ok == false`。
2. `status == "error"`。
3. `error.ename == "ZeroDivisionError"`。
4. kernel 仍然存活。

### 5.10 测试用例 EXEC-005：超时返回 running

目的：确认 `run_python` 到达 timeout 后返回控制权，并让代码继续运行。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 `run_python("import time\\ntime.sleep(3)\\n42", timeout_seconds=0.5)`。

期望：

1. 返回 `ok == true`。
2. 返回 `status == "running"`。
3. 返回 `execution_id`。
4. `python_status().busy == true`。

### 5.11 测试用例 EXEC-006：等待运行中 execution 完成

目的：确认 `wait_python` 能等待之前 running 的 execution。

步骤：

1. 执行 EXEC-005 得到 `execution_id`。
2. 调用 `wait_python(execution_id, timeout_seconds=5)`。

期望：

1. 返回 `status == "completed"`。
2. `result_text` 包含 `42`。
3. `python_status().busy == false`。

### 5.12 测试用例 EXEC-007：读取运行中输出

目的：确认长任务运行期间输出可以被读取。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 `run_python("import time\\nfor i in range(3):\\n    print(i, flush=True)\\n    time.sleep(1)", timeout_seconds=0.5)`。
3. 调用 `read_python_output(execution_id)`。

期望：

1. 返回 `status == "running"` 或 `completed`。
2. `stdout` 包含至少一个已打印数字，除非 execution 在第一次 sleep 前尚未产生输出。
3. 后续 `wait_python` 完成后，`stdout` 包含 `0`、`1`、`2`。

### 5.13 测试用例 EXEC-008：busy 不排队

目的：确认 running execution 存在时，新 `run_python` 不排队。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用一个会运行数秒的 `run_python(..., timeout_seconds=0.5)`。
3. 在其 running 时再次调用 `run_python("123")`。

期望：

1. 第二次调用返回 `ok == false`。
2. `status == "busy"`。
3. 返回 current execution id。
4. 不执行 `"123"`。

### 5.14 测试用例 CTRL-001：interrupt 发送中断

目的：确认 `interrupt_python` 对 running execution 发送 SIGINT。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 `run_python("while True:\\n    pass", timeout_seconds=0.5)`。
3. 调用 `interrupt_python()`。
4. 调用 `wait_python(execution_id, timeout_seconds=5)`。

期望：

1. `interrupt_python` 返回 `status == "interrupt_sent"`。
2. `wait_python` 返回 `error` 或 `interrupted`。
3. 错误信息包含 KeyboardInterrupt，或状态明确为 interrupted。
4. kernel 仍可继续执行新代码。

### 5.15 测试用例 CTRL-002：reset 清空状态

目的：确认 reset 杀掉 kernel 并启动新 kernel。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 `run_python("x = 41")`。
3. 调用 `python_status()` 记录 `old_pid`。
4. 调用 `reset_python()`。
5. 调用 `python_status()` 记录 `new_pid`。
6. 调用 `run_python("'x' in globals()")`。

期望：

1. `reset_python` 返回 `status == "restarted"`。
2. `new_pid != old_pid`。
3. 最后一次执行返回 `False`。

### 5.16 测试用例 CTRL-003：reset 杀掉运行中 execution

目的：确认 reset 能终止正在运行的代码。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用长期运行代码并得到 running execution。
3. 调用 `reset_python()`。
4. 调用 `read_python_output(execution_id)`。

期望：

1. `reset_python` 成功。
2. 旧 execution 状态为 `killed`。
3. `python_status().busy == false`。
4. 新 kernel 可执行代码。

### 5.17 测试用例 API-001：无截断参数

目的：确认工具接口不暴露输出截断控制。

步骤：

1. 调用 `list_tools`。
2. 查看 `run_python` 参数 schema。

期望：

1. `run_python` 有 `code`。
2. `run_python` 有 `timeout_seconds`。
3. `run_python` 没有 `max_output_chars`。
4. `run_python` 没有其它截断参数。

### 5.18 测试用例 API-002：无 start_python 工具

目的：确认设置 workspace 即启动 kernel。

步骤：

1. 调用 `list_tools`。

期望：

1. 工具列表包含 `set_workspace`。
2. 工具列表不包含 `start_python`。

### 5.19 测试用例 API-003：stdio 客户端不导入 server

目的：确认客户端示例是真实进程通信，不是内存导入。

步骤：

1. 读取 `mcp_ipython_client.py` 源码。

期望：

1. 源码不包含 `from mcp_ipython import`。
2. 源码不包含 `import mcp_ipython`。
3. 源码使用 stdio transport 启动 server 文件。

### 5.20 验收条件

实现满足以下全部条件时视为通过验收：

1. 文档中的七个工具均存在，且不存在 `start_python`。
2. `set_workspace` 设置 workspace 并启动 kernel。
3. 适配层只使用 `<workspace>/.venv/bin/python` 执行用户代码。
4. 缺失 workspace、缺失 Python、缺失 ipykernel 时返回明确错误。
5. `run_python` 保留状态。
6. `run_python` 默认等待 30 秒。
7. `run_python` 超时返回 running，不自动中断。
8. running execution 可通过 `read_python_output` 查看输出。
9. running execution 可通过 `wait_python` 等待。
10. busy 时新 `run_python` 不排队。
11. `interrupt_python` 只发送中断，不清空状态。
12. `reset_python` 杀掉 kernel 并启动新 kernel。
13. reset 后旧变量不可见。
14. stdio 客户端不导入 server。
15. 全部黑盒测试通过。

## 6. 实现约束

实现语言为 Python。目标平台为 Linux。项目 Python 版本为 3.13。项目构建系统为 uv workspace。`loommux` 包当前没有运行时依赖；实现该适配层时，`loommux` 的项目依赖应显式声明 MCP server 和 kernel 管理所需依赖。

最低依赖集合：

```text
fastmcp
jupyter-client
ipykernel
```

`ipykernel` 同时也是工作区 Python 的运行前提。适配层可以检测该前提，但不得自动安装。

MCP server 进程退出时应清理 kernel。测试应避免遗留 kernel 进程。

## 7. 开发者不得自行变更的规则

开发者不得将 `set_workspace` 拆成“设置”和“启动”两个工具。

开发者不得在 `run_python` 中加入输出截断参数。

开发者不得让 `run_python` 超时后自动 interrupt。

开发者不得让 `run_python` 超时后自动 reset。

开发者不得让新 `run_python` 在 busy 时排队。

开发者不得从 server 文件路径推断 workspace。

开发者不得在 workspace 缺少 `.venv` 时选择系统 Python。

开发者不得自动运行 `uv sync`、`uv venv`、`uv add` 或 `pip install`。

开发者不得实现 Windows 分支。

开发者不得把 Python 执行放在 MCP server 主进程内。

## 8. 文档结论

IPython MCP 适配层的稳定对象是一个响应式 Python 运行时控制面。Codex 显式设置 workspace 后，适配层使用该 workspace 的 `.venv/bin/python` 启动一个持久 IPython kernel。Codex 可以提交代码、观察输出、等待执行、中断当前代码或重置 kernel。该对象的价值来自状态保留和分段观察，而不是环境管理、自动修复或通用进程控制。
