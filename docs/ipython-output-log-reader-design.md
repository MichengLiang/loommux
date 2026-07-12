# Historical Design Notice

> Superseded by [Coding Agent Control Plane Design](coding-agent-control-plane-design.md). The output-address grammar below is retired; current readers use integer `execution` plus `stream`.

# IPython Execution Output Log Reader 设计

## 1. 文档对象与设计灵感

本文档定义 `loommux` IPython MCP adapter 中 execution output 的日志化阅读模型。该模型把 execution 的结构化状态与 execution 产生的可读输出分离：状态工具返回当前运行时和 execution 的事实，日志工具读取或搜索 output log 的文本内容。

本文档是 output log reader 的机制说明。当前工具返回展示面、默认结构化字段、小输出直接返回、大输出省略和 `logs` map 省略规则，以 `docs/ipython-mcp-output-surface-design.md` 为事实源。本文档不得重新定义与该文档冲突的 pretty text 或默认返回规则。

该设计来自 `docutouch` 对 `pueue` 日志的处理方式。`wait_pueue` 返回任务终态、退出码和 `log_handle`；`read_file` 使用该 handle 按行读取日志；`search_text` 使用同一 handle 执行字面量或正则搜索。任务状态没有混入日志正文，日志正文也不承担任务状态查询。这种分离适合响应式 agent：agent 没有持续视觉流，只能通过离散工具调用观察世界。状态工具应保持小而确定；日志工具应提供可重入、可定位、可增量阅读的文本面。

IPython execution output 具有同样的阅读需求。长时间 Python 执行会持续产生 stdout、stderr、display result 和 traceback。Codex 需要在不同时间点读取前几行、尾部几行、某个行段，或搜索一个错误模式。该能力属于 output log，而不是 execution status。

## 2. 核心对象

### 2.1 Execution Status

**execution status** 是一次 `run_python` 提交的结构化状态对象。它回答 execution 是否存在、是否正在运行、是否完成、是否出错、由哪个 kernel 执行，并提供 canonical output log handle。

execution status 不包含完整 stdout、stderr、result text 或 traceback 正文。状态对象可以包含错误摘要，例如 `ename` 与 `evalue`，但 traceback 正文属于日志对象。

### 2.2 Execution Output Log

**execution output log** 是 execution 产生的 append-only 文本对象。它支持按行读取、尾部读取、单行读取、行内裁切、字面量搜索、正则搜索和上下文搜索。

output log 是文本资源。它可以被读取和搜索，但不解释 kernel 是否 busy，不返回 workspace，不返回 current execution，不执行 wait，不执行 interrupt。

### 2.3 Log Handle

**log handle** 是定位 execution output log 的字符串。它在状态工具返回中出现，并作为日志工具的输入。

handle 语法：

```text
python-output:<execution_id>
python-output:<execution_id>/stdout
python-output:<execution_id>/stderr
python-output:<execution_id>/result
python-output:<execution_id>/traceback
```

`python-output:<execution_id>` 表示 combined log。combined log 按 adapter 收到输出事件的顺序组成一条面向阅读的文本流。

`/stdout` 表示 stdout log。`/stderr` 表示 stderr log。`/result` 表示 IPython result 或 display text log。`/traceback` 表示异常 traceback log。

## 3. 工具集合变更

### 3.1 状态与控制工具

状态与控制工具负责运行时状态、execution 状态和控制动作：

```text
run_python(freeform)
python_status()
python_execution_status(execution_id=None)
wait_python(execution_id=None, timeout_seconds=30)
interrupt_python()
reset_python()
```

`run_python` 的当前 MCP tool surface 是 freeform Python cell 输入；timeout 覆盖使用 `docs/ipython-mcp-freeform-run-python-design.md` 定义的 canonical directive。`run_python` 与 `wait_python` 返回 execution status，并在返回体中包含 canonical `output_log`。它们不提供行范围、grep 或 max chars 参数。读取日志由日志工具完成。

默认保护规则：`run_python` 与 `wait_python` 只有在 execution 已结束且 combined output log 不超过 300 行时，才携带完整小输出正文。execution 仍为 `running`，或 combined output log 超过 300 行时，文本字段必须为空，返回体只保留 execution id、状态、错误摘要、canonical output log handle 和省略元数据。省略元数据包括 `output_omitted`、`output_omitted_reason`、`output_line_limit` 与 `output_total_lines`。完整日志通过 `read_python_output` 或 `search_python_output` 读取。

`python_execution_status` 返回某个 execution 的结构化状态。未传入 `execution_id` 时，选择规则与当前 `read_python_output` 一致：优先 current execution，其次 last execution。

### 3.2 日志工具

日志工具负责 output log 文本：

```text
read_python_output(execution_id=None, output_log=None, stream="combined", line_range=None, show_line_numbers=False, max_chars=None)
search_python_output(query, execution_id=None, output_log=None, stream="combined", query_mode="auto", context_before=0, context_after=0, ignore_case=False, max_chars=None)
```

`read_python_output` 按行读取 output log。它不等待 execution，不返回 kernel 状态，不返回完整 execution snapshot。

`search_python_output` 在 output log 中搜索文本。它支持字面量和正则搜索，并返回命中行与上下文行。

## 4. Selection 语义

日志工具通过以下顺序选择目标 log：

1. 如果传入 `output_log`，解析该 handle。
2. 如果未传入 `output_log` 但传入 `execution_id`，选择该 execution 的 selected stream。
3. 如果两者都未传入，选择 current execution 的 selected stream。
4. 如果不存在 current execution，选择 last execution 的 selected stream。
5. 如果没有可选 execution，返回 `execution_not_found`。

如果 `output_log` 的 scheme 不是 `python-output`，返回 `invalid_output_log`。

如果 `output_log` 指向不存在的 execution，返回 `execution_not_found`。

如果 `output_log` 的 stream 不在 `stdout | stderr | result | traceback` 中，返回 `invalid_output_log`。

如果 `output_log` 已包含 stream 后缀，该后缀决定 stream。显式传入冲突 stream 返回 `invalid_output_log`。

## 5. 状态返回形状

### 5.1 `run_python`

`run_python` 成功提交后返回 execution status：

```json
{
  "ok": true,
  "execution_id": "exec-000001",
  "status": "running",
  "output_log": "python-output:exec-000001",
  "output_omitted": true,
  "output_omitted_reason": "running",
  "output_line_limit": 300,
  "output_total_lines": 1,
  "stdout": "",
  "stderr": "",
  "result_text": "",
  "error": null,
  "kernel": {
    "busy": true,
    "kernel_pid": 12345,
    "execution_count": 1
  }
}
```

`status == "error"` 时，`error` 保存结构化摘要：

```json
{
  "ename": "ZeroDivisionError",
  "evalue": "division by zero",
  "traceback_log": "python-output:exec-000001/traceback"
}
```

traceback 正文通过 `/traceback` log 读取。

### 5.2 `python_execution_status`

```json
{
  "ok": true,
  "execution_id": "exec-000001",
  "status": "completed",
  "submitted_at": 1777911000.0,
  "updated_at": 1777911001.0,
  "completed_at": 1777911001.0,
  "kernel_pid": 12345,
  "execution_count_at_submit": 1,
  "output_log": "python-output:exec-000001",
  "output_total_lines": 3,
  "output_omitted_reason": null,
  "error": null
}
```

## 6. 日志读取语义

### 6.1 Tool Definition

```text
read_python_output(
  execution_id: str | null = null,
  output_log: str | null = null,
  stream: "combined" | "stdout" | "stderr" | "result" | "traceback" = "combined",
  line_range: str | null = null,
  show_line_numbers: bool = false,
  max_chars: int | null = null
)
```

### 6.2 Line Range

`line_range` 使用 `start:stop` surface。行号按 1-indexed line number 解释。端点可省略。负数端点按从日志尾部相对定位解释。`stop` 为包含端点。

示例：

```text
:10    前 10 行
-10:   后 10 行
20:40  第 20 行到第 40 行
20:    第 20 行到末尾
3:3    第 3 行
```

如果 `line_range` 为 `null`，读取完整 log。

如果 `line_range` 解析失败，返回 `invalid_line_range`。

如果解析后的范围为空，返回 `ok == true`、`text == ""`、`returned_lines == 0`。

### 6.3 Line Boundary

日志按 `\n` 分行。末尾未以 `\n` 结束的片段仍然是一行。运行中的 execution 可以持续追加到最后一行；该行为不得导致已经存在的完整行号漂移。

### 6.4 Max Chars

`max_chars` 控制当前返回中每一行的最大显示宽度。它不裁切整段返回体，不改变行号，不改变匹配结果。超过限制的行使用内联省略标记：

```text
...[N chars omitted]
```

`max_chars <= 0` 返回 `invalid_max_chars`。

### 6.5 Read Return

```json
{
  "ok": true,
  "output_log": "python-output:exec-000001",
  "execution_id": "exec-000001",
  "stream": "combined",
  "line_range": "-10:",
  "total_lines": 100,
  "returned_lines": 10,
  "omitted_before": 90,
  "omitted_after": 0,
  "text": "91 | ...\n92 | ..."
}
```

`show_line_numbers == false` 时，`text` 不含行号前缀。

## 7. 日志搜索语义

### 7.1 Tool Definition

```text
search_python_output(
  query: str,
  execution_id: str | null = null,
  output_log: str | null = null,
  stream: "combined" | "stdout" | "stderr" | "result" | "traceback" = "combined",
  query_mode: "auto" | "literal" | "regex" = "auto",
  context_before: int = 0,
  context_after: int = 0,
  ignore_case: bool = false,
  max_chars: int | null = null
)
```

### 7.2 Query Mode

`literal` 按字面量匹配。`regex` 按正则匹配。`auto` 优先按正则解释；正则编译失败时回退为字面量匹配，并在返回中标记 `query_interpretation`。

`query` 必须是字符串。空字符串允许，按所选 matcher 解释。

正则编译失败且 `query_mode == "regex"` 时，返回 `invalid_query`。

### 7.3 Context

`context_before` 与 `context_after` 表示每条命中前后的上下文行数。它们必须大于等于 0。

搜索结果中的上下文行与命中行合并去重，并保持原始行号顺序。

### 7.4 Search Return

```json
{
  "ok": true,
  "output_log": "python-output:exec-000001",
  "execution_id": "exec-000001",
  "stream": "combined",
  "query": "match",
  "query_interpretation": "literal",
  "matched_lines": 2,
  "matches": 2,
  "context_before": 1,
  "context_after": 1,
  "text": "C 2 | stderr-02 warn\nM 3 | stdout-03 beta-match\nC 4 | stdout-04 tail\nM 5 | stderr-05 err-match"
}
```

无命中不是错误。无命中返回 `ok == true`、`matched_lines == 0`、`matches == 0`、`text == ""`。

## 8. Combined Log Authoring

combined log 是面向 agent 阅读的文本流。

stdout 事件按原文追加到 stdout log 与 combined log。

stderr 事件按原文追加到 stderr log 与 combined log。

`execute_result` 与 `display_data` 的 `text/plain` 追加到 result log。追加到 combined log 时使用 IPython 风格前缀：

```text
Out[<execution_count>]: <text/plain>
```

如果 result text 包含多行，第一行带 `Out[n]: ` 前缀，后续行保持原始换行结构。

error traceback 追加到 traceback log 与 combined log。状态对象的 `error` 字段只保留 `ename`、`evalue` 与 traceback log handle。

## 9. 内部实现策略

### 9.1 ExecutionLogs

`Execution` 持有一个 `ExecutionLogs` 对象。该对象管理五个 `LineLog`：

```text
combined
stdout
stderr
result
traceback
```

`Execution.append_stdout(text)` 写入 stdout log 与 combined log。

`Execution.append_stderr(text)` 写入 stderr log 与 combined log。

`Execution.append_result_text(text)` 写入 result log，并把 IPython authored surface 写入 combined log。

`Execution.record_error(error)` 写入结构化错误摘要，并把 traceback 写入 traceback log 与 combined log。

### 9.2 LineLog

`LineLog` 是 append-only 文本日志。第一版可以用完整字符串与 `splitlines(keepends=False)` 构造行视图；如果输出规模变大，再改成 offset index。外部接口不得依赖内部存储方式。

`LineLog` 提供：

```text
append(text)
read(line_range, show_line_numbers, max_chars)
search(query, query_mode, context_before, context_after, ignore_case, max_chars)
```

### 9.3 Snapshot Fields

`Execution.snapshot()` 保留 `stdout`、`stderr`、`result_text` 字段，用于小输出便利返回和结构化消费。running 或大输出分支必须把这些文本字段置空，并通过 `output_log` 暴露日志读取入口。

## 10. 错误表面

新增失败状态：

```text
invalid_output_log
invalid_line_range
invalid_max_chars
invalid_query
invalid_context
execution_not_found
```

日志读取和搜索不因 execution status 为 `error`、`interrupted` 或 `killed` 而失败。只要 execution 存在且日志已收集，就可以读取。

## 11. 黑盒验收

实现满足以下条件时视为通过：

1. `run_python` 返回 canonical `output_log`，默认不返回 `logs` map。
2. `wait_python` 返回 canonical `output_log`，默认不返回 `logs` map。
3. `python_execution_status` 返回 execution 结构化状态，不返回完整日志正文。
4. `read_python_output(line_range=":2")` 返回前两行。
5. `read_python_output(line_range="-2:")` 返回后两行。
6. `read_python_output(line_range="3:3")` 返回单行。
7. `read_python_output(max_chars=N)` 对每一行横向裁切。
8. `search_python_output(query, query_mode="literal")` 执行字面量搜索。
9. `search_python_output(query, query_mode="regex")` 执行正则搜索。
10. `search_python_output(context_before=1, context_after=1)` 返回上下文行。
11. stdout handle 只读取 stdout log。
12. stderr handle 只读取 stderr log。
13. result handle 只读取 result log。
14. traceback handle 在 Python 异常后可读取 traceback。
15. reset 杀掉 running execution 后，旧 execution status 为 `killed`，旧 output log 仍可读取。

## 12. 文档结论

IPython MCP adapter 的 output 阅读能力应以日志文本对象为中心。状态工具产生和更新 execution 状态，并返回 output log handle；日志工具使用 handle 做按行读取和搜索。该模型符合响应式 agent 的工作方式：每次工具调用都是一次离散观察，观察对象必须可定位、可重读、可缩小范围、可搜索。状态与日志分离后，长输出不会淹没状态，状态也不会污染日志阅读面。
