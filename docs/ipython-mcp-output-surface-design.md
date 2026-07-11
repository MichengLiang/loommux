# IPython MCP Output Surface 最终设计

## 1. 文档对象

本文档定义 `loommux` IPython MCP adapter 的工具说明、pretty text 展示面与结构化返回面。本文档只规定工具返回给 agent 阅读的表面，不重新定义 kernel 生命周期、workspace 解析、执行提交、IOPub 收集或进程控制。

本文档是当前输出展示面、双通道返回、output log handle、日志读取、搜索和状态/日志分离规则的事实源。`docs/ipython-mcp-adapter-design.md` 定义 workspace、kernel 和 execution 生命周期；`docs/ipython-output-log-reader-design.md` 只保留 output log reader 的机制说明；双通道设计稿只保留历史背景。

本文档的约束对象包括：

1. MCP tool docstring 的信息承载职责。
2. `run_python` 与 `wait_python` 的 pretty text surface。
3. `read_python_output` 的 pretty text surface。
4. `search_python_output` 的 pretty text surface。
5. `python_status` 与 `python_execution_status` 的 pretty text surface。
6. 结构化返回中必须保留、必须省略和必须由规则派生的字段。
7. 小输出、running 输出、大输出、错误输出的分支规则。

本文档不定义 UI 样式，不定义颜色，不定义 Markdown 渲染器行为，不定义持久化日志存储，不定义多 kernel session，不定义输出保留上限。

## 2. 设计依据

Codex 这类 agent 不是持续观看终端的用户。agent 通过离散工具调用观察当前状态。每一次工具返回都必须成为一次可读、可定位、可继续追问的观察面。

普通 shell 中执行 Python 代码时，用户主要看到 Python 产生的可见文本。`print()` 输出文本；stderr 输出错误文本；Python 异常输出 traceback；普通 Python 脚本中的裸表达式不自动显示。IPython kernel 额外具有表达式结果显示语义，表达式结果应使用 IPython 风格的 `Out[n]: value` surface，而不是暴露内部字段名 `result_text`。

因此，`run_python` 的 pretty text surface 必须优先呈现 Python output，而不是优先呈现 adapter 状态字典。adapter 状态字典属于 structured content；pretty text 不是 structured content 的逐项转写。

## 3. 基本纪律

### 3.1 最小剂量原则

工具返回只展示当前动作必须阅读的信息。工具不得在每次返回中重复展示可由工具说明或命名规则推导出的对象。

以下信息属于规则，不属于每次执行的观测事实：

```text
combined log:  python-output:<execution_id>
stdout log:    python-output:<execution_id>/stdout
stderr log:    python-output:<execution_id>/stderr
result log:    python-output:<execution_id>/result
traceback log: python-output:<execution_id>/traceback
```

该规则必须写入相关工具 docstring。`run_python`、`wait_python`、`python_execution_status` 的常规返回不得每次展开 `logs` map。

### 3.2 工具意图优先

每个工具的 pretty text surface 必须服务该工具的直接意图。

`run_python` 的直接意图是执行 Python 并观察该次执行的可见结果。

`wait_python` 的直接意图是等待 execution 并观察等待后的可见结果或可继续读取的 handle。

`read_python_output` 的直接意图是读取 output log 的文本行。

`search_python_output` 的直接意图是在 output log 中搜索并返回命中行。

`python_status` 的直接意图是查看 workspace 与 kernel 状态。

`python_execution_status` 的直接意图是查看 execution 的结构化状态。

### 3.3 状态与日志分离

状态对象返回事实、标识符、状态枚举、时间、错误摘要和 canonical output log handle。

日志对象返回文本正文、行号、搜索命中和上下文。

状态对象不得承载大段日志正文。日志工具的成功返回不得承载 kernel 状态、workspace 状态或 execution status。日志工具失败返回使用 `status` 表达工具错误，例如 `execution_not_found` 或 `invalid_line_range`。

### 3.4 小输出便利规则

如果 execution 已结束，且 combined output log 的总行数不超过 300 行，`run_python` 与 `wait_python` 的 structured content 携带完整小输出正文。

该规则是产品行为，不是兼容债务。小输出直接返回用于减少额外工具调用，并保持执行 Python 后直接观察结果的交互直觉。

### 3.5 分离触发规则

以下任一条件成立时，`run_python` 与 `wait_python` 不得在 pretty text 或 structured content 中携带输出正文：

1. execution 状态为 `running`。
2. combined output log 总行数大于 300。

此时返回必须包含：

```text
execution_id
status
output_log
output_omitted = true
output_omitted_reason
output_line_limit
output_total_lines
```

此时返回不得包含 stdout、stderr、result 或 traceback 正文。相关正文必须通过 `read_python_output` 或 `search_python_output` 读取。

## 4. Tool Docstring 设计

MCP tool docstring 是模型使用工具前能看到的提示词文档。稳定规则必须写入 docstring，而不是通过每次返回重复发送。

### 4.1 `run_python` Docstring

`run_python` 的 docstring 必须说明调用者需要知道的执行、等待和后续读取规则：

1. 该工具向当前 IPython kernel 提交 `freeform` Python cell。
2. 如果 execution 在本次等待上限内结束，并且 combined output 不超过 300 行，返回会直接展示该次执行的可见输出。
3. 如果 execution 仍在运行，返回不展示 partial output，只返回 execution id 与 output log handle。
4. 如果 output 超过 300 行，返回不展示正文，只返回 execution id 与 output log handle。
5. `output_log` 是 canonical combined log handle。
6. 分流 log 由 canonical handle 派生。
7. 读取日志使用 `read_python_output`。
8. 搜索日志使用 `search_python_output`。
9. 查 execution 状态使用 `python_execution_status`。

`run_python` 的 freeform 输入、默认 10 秒等待和 timeout directive 语义以 `docs/ipython-mcp-freeform-run-python-design.md` 为事实源。`run_python` docstring 必须使用以下内容：

```python
def run_python(freeform: str) -> dict[str, Any]:
    """向当前 IPython kernel 提交 Python cell，并等待至完成或达到等待上限。

    输入
    ----

    ``freeform`` 是原始 Python cell 源码文本。该文本原样提交给当前
    持久 IPython kernel。模型不需要生成 JSON arguments，也不需要把
    Python 源码转义成 JSON string。

    等待上限
    --------

    本次调用默认等待 10 秒。若本次调用需要等待更长时间，在 cell 中
    放入且只放入一行完整匹配的注释::

        # loommux: timeout_seconds=120

    没有该注释、该注释无效或存在多条有效注释时，本次调用仍等待
    10 秒。等待上限只控制本次工具调用等待多久；达到等待上限后
    execution 继续在 kernel 中运行，不会被 interrupt 或 reset。

    返回表面
    --------

    已结束且 combined output 不超过 300 行时直接展示可见输出。running
    或大输出时返回 ``status="running"``、``execution_id`` 和
    ``output_log``。``output_log`` 是 combined output log handle，
    格式为 ``python-output:<execution_id>``。分流日志由该 handle 加
    固定后缀 ``/stdout``、``/stderr``、``/result``、``/traceback`` 派生。

    后续工具
    --------

    读取日志使用 ``read_python_output``。搜索日志使用
    ``search_python_output``。等待运行中 execution 使用 ``wait_python``。
    查看 execution 结构化状态使用 ``python_execution_status``。中断或
    重启使用 ``interrupt_python`` 或 ``reset_python``。

    :param freeform: 要提交给当前 IPython kernel 的 Python cell 源码文本。
    :returns: execution 结果。小输出直接返回 Python 可见输出；running 或
        大输出返回 ``execution_id``、``status``、``output_log``、
        ``output_omitted_reason`` 和已收集行数。
    """
```

### 4.2 `read_python_output` Docstring

`read_python_output` 的 docstring 必须说明目标选择、行范围、行号和横向裁切规则：

`read_python_output` docstring 必须使用以下内容：

```python
def read_python_output(
    execution_id: str | None = None,
    output_log: str | None = None,
    stream: str = "combined",
    line_range: str | None = None,
    show_line_numbers: bool = False,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """读取 execution output log 的文本行。

    可用 execution_id 或 output_log 选择目标。`python-output:<execution_id>`
    表示 combined log；后缀 `/stdout`、`/stderr`、`/result`、
    `/traceback` 表示分流日志。stream 选择分流；line_range 使用
    `start:stop`，`:10` 读取前 10 行，`-10:` 读取后 10 行；max_chars
    只裁切单行。

    Args:
        execution_id: execution 标识。未提供 `output_log` 时，读取该
            execution 的 combined log。未提供该参数时，优先读取 current
            execution，其次读取 last execution。
        output_log: output log handle。`python-output:<execution_id>` 表示
            combined log；后缀 `/stdout`、`/stderr`、`/result`、`/traceback`
            表示对应分流日志。
        stream: 要读取的 stream，支持 `combined`、`stdout`、`stderr`、
            `result`、`traceback`。如果 `output_log` 已包含 stream 后缀，
            该后缀决定 stream；显式传入冲突 stream 会返回
            `invalid_output_log`。
        line_range: 行范围，使用 `start:stop`。正数端点按 1-indexed line
            number 解释；端点可省略；负数端点按从日志尾部相对定位解释；
            stop 为包含端点。`:10` 读取前 10 行，`-10:` 读取后 10 行，
            `20:40` 读取第 20 到第 40 行。
        show_line_numbers: 是否在返回文本中显示 1-indexed 行号。
        max_chars: 每一行的最大显示宽度。该参数只裁切单行，不裁切整段
            结果。

    Returns:
        日志读取结果，包括 `output_log`、`stream`、`total_lines`、
        `returned_lines`、省略行数和 `text`。
    """
```

### 4.3 `search_python_output` Docstring

`search_python_output` 的 docstring 必须说明目标选择、query 解释、上下文和返回统计：

`search_python_output` docstring 必须使用以下内容：

```python
def search_python_output(
    query: str,
    execution_id: str | None = None,
    output_log: str | None = None,
    stream: str = "combined",
    query_mode: str = "auto",
    context_before: int = 0,
    context_after: int = 0,
    ignore_case: bool = False,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """搜索 execution output log。

    可用 execution_id 或 output_log 选择目标。`python-output:<execution_id>`
    表示 combined log；后缀 `/stdout`、`/stderr`、`/result`、
    `/traceback` 表示分流日志。stream 选择分流；query_mode 支持
    literal、regex、auto；context_before/context_after 返回上下文；
    ignore_case 控制大小写。

    Args:
        query: 要搜索的字面量或正则模式。
        execution_id: execution 标识。未提供 `output_log` 时，搜索该
            execution 的 combined log。未提供该参数时，优先搜索 current
            execution，其次搜索 last execution。
        output_log: output log handle。`python-output:<execution_id>` 表示
            combined log；后缀 `/stdout`、`/stderr`、`/result`、`/traceback`
            表示对应分流日志。
        stream: 要搜索的 stream，支持 `combined`、`stdout`、`stderr`、
            `result`、`traceback`。如果 `output_log` 已包含 stream 后缀，
            该后缀决定 stream；显式传入冲突 stream 会返回
            `invalid_output_log`。
        query_mode: `literal` 按字面量搜索；`regex` 按正则搜索；`auto`
            优先按正则解释，正则编译失败时回退为字面量搜索。
        context_before: 每条命中前返回的上下文行数。
        context_after: 每条命中后返回的上下文行数。
        ignore_case: 是否忽略大小写。
        max_chars: 每一行的最大显示宽度。该参数只裁切单行，不裁切整段
            结果。

    Returns:
        搜索结果，包括 `output_log`、`stream`、`query_interpretation`、
        `matched_lines`、`matches`、上下文设置和 `text`。
    """
```

## 5. Structured Content 规则

### 5.1 `run_python` Small Completed

当 execution 已结束且 output 不超过 300 行，structured content 必须包含：

```json
{
  "ok": true,
  "status": "completed",
  "execution_id": "exec-000001",
  "output_log": "python-output:exec-000001",
  "output_omitted": false,
  "output_omitted_reason": null,
  "output_line_limit": 300,
  "output_total_lines": 3,
  "stdout": "hello\n",
  "stderr": "",
  "result_text": "42",
  "error": null
}
```

structured content 不得默认包含 `logs` map。分流 handle 由 docstring 规则派生。

### 5.2 `run_python` Running

当 execution 仍在运行，structured content 必须包含：

```json
{
  "ok": true,
  "status": "running",
  "execution_id": "exec-000002",
  "output_log": "python-output:exec-000002",
  "output_omitted": true,
  "output_omitted_reason": "running",
  "output_line_limit": 300,
  "output_total_lines": 1,
  "stdout": "",
  "stderr": "",
  "result_text": "",
  "error": null
}
```

### 5.3 `run_python` Large Completed

当 execution 已结束但 output 大于 300 行，structured content 必须包含：

```json
{
  "ok": true,
  "status": "completed",
  "execution_id": "exec-000003",
  "output_log": "python-output:exec-000003",
  "output_omitted": true,
  "output_omitted_reason": "line_limit_exceeded",
  "output_line_limit": 300,
  "output_total_lines": 301,
  "stdout": "",
  "stderr": "",
  "result_text": "",
  "error": null
}
```

### 5.4 Error Summary

`error` 字段必须是摘要对象，不得包含 traceback 正文：

```json
{
  "ename": "ZeroDivisionError",
  "evalue": "division by zero",
  "traceback_log": "python-output:exec-000004/traceback"
}
```

traceback 正文属于 output log。小错误的 pretty text 必须展示 traceback 文本，因为 pretty text 展示的是小 output log；structured `error` 不得携带 traceback 列表。

## 6. Pretty Text 总规则

### 6.1 Pretty Text 不是字段表

pretty text 不得默认把 structured content 转成完整 key-value 表。pretty text 是模型阅读 surface，必须按工具意图组织。

### 6.2 Output First

`run_python` 与 `wait_python` 在小输出完成时必须先展示 Python 可见输出。状态 footer 只能占一行。

### 6.3 One-Line Footer

当 pretty text 已展示正文时，footer 使用一行表达最小定位信息：

```text
[exec-000001 completed | log: python-output:exec-000001]
```

footer 不得展开分流 handle。

### 6.4 Omitted Surface

当 output 被省略时，pretty text 不展示空的 `stdout:`、`stderr:` 或 `result_text:` 块。它只展示一行省略说明：

```text
[exec-000002 running | output omitted: running | 1 line available | log: python-output:exec-000002]
```

```text
[exec-000003 completed | output omitted: 301 lines > 300 | log: python-output:exec-000003]
```

### 6.5 Log Read Surface

`read_python_output` 成功时 pretty text 先展示读取到的文本。footer 使用一行表达 log、行范围和总行数。

### 6.6 Search Surface

`search_python_output` 成功时 pretty text 先展示命中和上下文。footer 使用一行表达 log、query 和命中统计。

## 7. `run_python` Pretty Text 规格

### 7.1 Small Stdout

输入产生 stdout：

```text
hello
```

pretty text：

```text
hello

[exec-000001 completed | log: python-output:exec-000001]
```

### 7.2 Small Result

输入产生 IPython result：

```text
42
```

pretty text：

```text
Out[1]: 42

[exec-000001 completed | log: python-output:exec-000001]
```

### 7.3 Small Stdout And Result

输入同时产生 stdout 和 result：

```text
hello
Out[1]: 42

[exec-000001 completed | log: python-output:exec-000001]
```

### 7.4 Small Error

输入产生 Python 异常：

```text
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
ZeroDivisionError: division by zero

[exec-000002 error | traceback: python-output:exec-000002/traceback | log: python-output:exec-000002]
```

### 7.5 Running

execution 未结束：

```text
[exec-000003 running | output omitted: running | 1 line available | log: python-output:exec-000003]
```

### 7.6 Large Completed

execution 已结束但 output 超过 300 行：

```text
[exec-000004 completed | output omitted: 301 lines > 300 | log: python-output:exec-000004]
```

### 7.7 Completed No Output

execution 已结束且无输出：

```text
[exec-000005 completed | no output | log: python-output:exec-000005]
```

## 8. `read_python_output` Pretty Text 规格

### 8.1 Range With Line Numbers

```text
299 | line-298
300 | line-299
301 | line-300

[python-output:exec-000004/stdout | lines 299-301 of 301]
```

### 8.2 Range Without Line Numbers

```text
line-298
line-299
line-300

[python-output:exec-000004/stdout | 3 lines of 301 | omitted_before=298]
```

### 8.3 Empty Result

```text
[python-output:exec-000004/stdout | no lines]
```

### 8.4 Invalid Range

```text
invalid_line_range: line_range must use start:stop
```

## 9. `search_python_output` Pretty Text 规格

### 9.1 Matches With Context

```text
C 2 | stderr-02 warn
M 3 | stdout-03 beta-match
C 4 | stdout-04 tail
M 5 | stderr-05 err-match

[python-output:exec-000004 | query: match (literal) | 2 matched lines, 2 matches]
```

### 9.2 No Matches

```text
[python-output:exec-000004 | query: missing (literal) | no matches]
```

### 9.3 Invalid Regex

```text
invalid_query: query is not a valid regular expression
```

## 10. Status Tool Pretty Text

### 10.1 `python_status`

`python_status` 是状态工具，使用 compact key-value surface：

```text
kernel: idle
workspace: /home/t103o/workbench/projects/loommux
python: /home/t103o/workbench/projects/loommux/.venv/bin/python
last_execution_id: exec-000004
```

如果 kernel busy：

```text
kernel: busy
current_execution_id: exec-000004
workspace: /home/t103o/workbench/projects/loommux
```

### 10.2 `python_execution_status`

`python_execution_status` 是 execution 状态工具：

```text
execution exec-000004: completed
log: python-output:exec-000004
submitted_at: 1777911000.0
completed_at: 1777911001.0
output_total_lines: 301
output_omitted_reason: line_limit_exceeded
```

错误 execution：

```text
execution exec-000005: error
error: ZeroDivisionError: division by zero
traceback: python-output:exec-000005/traceback
log: python-output:exec-000005
```

## 11. Implementation Rules

### 11.1 No `logs` Map In Default Returns

Default structured return must not include `logs` map. The only default log handle field is:

```text
output_log
```

分流 handle 的构造规则写在 docstring 中。工具实现内部使用 `ExecutionLogs.handles`；默认返回不得展开该 map。

### 11.2 Stream Selection

`read_python_output` 与 `search_python_output` 必须接收 `stream` parameter:

```text
stream: combined | stdout | stderr | result | traceback = combined
```

If `output_log` is canonical combined handle and `stream` is provided, the tool reads the corresponding stream for that execution.

If `output_log` already includes a stream suffix, the suffix determines the stream. Passing a conflicting `stream` returns `invalid_output_log`.

This rule allows `run_python` to return only `output_log` while still making stream selection explicit at the read/search call site.

### 11.3 Combined Output Body

`run_python` and `wait_python` pretty text must render the combined output body for small completed outputs. It must not independently render `stdout`, `stderr`, and `result_text` blocks.

### 11.4 Error Body

For small error outputs, pretty text must render the traceback body because traceback is output text. Structured `error` remains a summary object.

### 11.5 Failure Surface

Tool failure pretty text uses:

```text
<status>: <message>
```

Examples:

```text
execution_not_found: execution was not found
invalid_output_log: output_log stream is not supported
invalid_line_range: line_range must use start:stop
```

## 12. Test Requirements

Implementation must include tests for:

1. Small completed stdout pretty text begins with stdout body.
2. Small completed result pretty text uses `Out[n]:`.
3. Small completed mixed output does not show `logs` map.
4. Small error pretty text shows traceback body, but structured `error` has no traceback list.
5. Running output pretty text is one omitted line with `output_log`.
6. Large completed output pretty text is one omitted line with line count and `output_log`.
7. `read_python_output` success pretty text starts with log text, not `状态：已返回。`.
8. `search_python_output` success pretty text starts with match text, not `状态：操作成功。`.
9. `run_python` structured return contains `output_log` and does not contain default `logs` map.
10. `read_python_output(stream="stderr")` reads stderr stream.
11. Conflicting `output_log` suffix and `stream` returns `invalid_output_log`.
12. `python_status` remains compact status text.
13. `python_execution_status` remains compact execution status text.

## 13. Migration Rule

Existing fields `stdout`, `stderr`, and `result_text` remain in structured return for small completed outputs. They are not the pretty text surface. The pretty text surface uses combined output.

Existing clients that read `stdout` from structured content remain supported during the current artifact phase. New presentation logic must not use those fields as separate pretty text sections.

`logs` map must be removed from default structured return in the same implementation pass that adds `stream` support to read/search tools. Default pretty text surfaces must not render `logs` map.

## 14. Acceptance Criteria

The design is implemented when all of the following are true:

1. `run_python` small completed pretty text shows Python output first.
2. `run_python` running pretty text contains no output body and no `logs` map.
3. `run_python` large completed pretty text contains no output body and no `logs` map.
4. `run_python` error structured content returns summary error only.
5. `run_python` small error pretty text can show traceback body as output text.
6. `read_python_output` success pretty text is log text plus one footer.
7. `search_python_output` success pretty text is grep text plus one footer.
8. `python_status` and `python_execution_status` keep status-oriented output.
9. Tool docstrings contain the handle derivation rules.
10. Default structured returns do not repeat the full stream handle map.
11. Tests assert absence of `logs` map in pretty text.
12. Full test suite passes with coverage at or above 90%.

## 15. 文档结论

IPython MCP adapter 的输出展示面应遵守最小剂量原则。执行 Python 时，agent 首先需要看到 Python 可见输出；只有输出不可直接返回时，agent 才需要 execution id、output log handle 和省略原因。stream handle 派生规则属于工具说明，不属于每次返回的正文。日志读取和搜索工具应直接展示日志文本和搜索命中，状态工具才展示状态。该结构既保留 IPython 的执行直觉，也适合响应式 agent 的离散观察方式。
