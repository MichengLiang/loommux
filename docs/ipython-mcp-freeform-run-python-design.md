# IPython MCP `run_python` Freeform 输入适配设计与黑盒验收规格

## 1. 文档对象与适用范围

本文定义 `loommux` IPython MCP 适配层中 `run_python` 的 Codex freeform 输入契约。该契约面向支持 MCP freeform tool 投影的 Codex 宿主，使模型以原始 Python cell 文本调用 `run_python`，而不是把 Python 源码编码进 JSON string 参数。

本文只定义 `run_python` 的模型可见输入形态、单次 timeout directive、默认等待时长、解析规则、执行语义、错误和回退表面、实现边界、测试规格与验收条件。本文不重新定义 workspace 选择、kernel 生命周期、execution 状态模型、output log、读取工具、搜索工具、中断工具或 reset 语义；这些对象仍以 `docs/ipython-mcp-adapter-design.md` 和 output surface 相关文档为事实源。

本文中的 `run_python` freeform 契约适用于 `loommux` 当前 MCP entrypoint。`run_python` 的公开工具表面不保留结构化 `code` 或 `timeout_seconds` 参数。

## 2. 术语

**Freeform `run_python`** 指 MCP input schema 精确暴露唯一字符串字段 `freeform` 的 `run_python` 工具。Codex 在启用 MCP freeform contract 后将该工具声明为 Responses custom/freeform tool。模型调用该工具时直接写 Python cell 文本。

**Python cell** 指一次提交给当前持久 IPython kernel 的原始文本。该文本以 Python 源码为主体，可以包含 Python 注释。`loommux` 不把该文本包装成 JSON code string 后再交给模型书写。

**Timeout directive** 指 Python cell 中一行完整匹配指定文本形态的注释。该 directive 只由 MCP server 在提交前读取，用于覆盖本次 `run_python` 工具调用的等待时长。

**默认等待时长** 指 `run_python` 在没有识别到唯一有效 timeout directive 时等待 execution 完成的秒数。默认等待时长固定为 10 秒。

**本次调用** 指当前一次 MCP `run_python` tool call。timeout directive 对本次调用生效，不写入 adapter 持久状态，不改变后续 `run_python` 的默认等待时长。

**运行时** 指 IPython kernel 的 Python namespace、对象、导入、变量、当前目录、后台线程和执行状态。timeout directive 不属于运行时状态。

**全文匹配** 指 parser 对 Python cell 的每一行分别执行完整行匹配。只有整行从第一个字符到最后一个字符都匹配 canonical directive grammar 时，该行才是有效 timeout directive。

## 3. 设计目标

`run_python` freeform 契约的目标是让模型直接书写 Python cell。模型不需要生成 JSON arguments，不需要转义换行、引号、反斜杠、三引号、字典文本、正则表达式或嵌套 JSON。

timeout directive 的目标是保留原 `timeout_seconds` 参数的单次覆盖能力。该能力从 JSON 字段迁移到 Python-compatible 注释行。该迁移不引入运行时变量，不引入持久 timeout 状态，不改变 execution 的继续运行语义。

默认等待时长改为 10 秒。该默认值使 `run_python` 优先保持响应性。长任务在 10 秒后返回 `running`，并继续通过 `execution_id`、`output_log`、`read_python_output`、`search_python_output`、`wait_python`、`interrupt_python` 和 `reset_python` 进行观察与控制。

## 4. 非目标

本文不定义通用 cell metadata 系统。

本文不定义多个 timeout directive 的优先级。

本文不定义宽松 parser。

本文不定义 timeout 单位后缀。

本文不定义运行时变量、注入模块、IPython magic 或 decorator 语法。

本文不让无效 timeout directive 阻止 Python cell 执行。

本文不把 `wait_python`、`read_python_output`、`search_python_output` 改为 freeform 工具。

## 5. 公共契约

### 5.1 Tool schema

Codex freeform entrypoint 中的 `run_python` 工具必须暴露唯一输入字段：

```text
freeform: string
```

该字段承载原始 Python cell 文本。字段名 `freeform` 是 Codex MCP Text Contract 的输入身份，不是业务术语。server wrapper 可以在内部将 `freeform` 变量重命名为 `code` 或 `source`。

目标 MCP input schema 等价于：

```json
{
  "type": "object",
  "properties": {
    "freeform": {
      "type": "string"
    }
  },
  "required": ["freeform"],
  "additionalProperties": false
}
```

`run_python` 的 freeform tool schema 不包含 `code` 字段，不包含 `timeout_seconds` 字段，不包含输出截断字段。

### 5.2 Canonical timeout directive

`run_python` 只识别一种 timeout directive：

```python
# loommux: timeout_seconds=120
```

该示例中的 `120` 是正数秒值。公开文档和工具 docstring 只展示这一种写法。

### 5.3 Directive grammar

timeout directive 的完整行文法为：

```text
timeout_directive ::= "# loommux: timeout_seconds=" positive_decimal_number
positive_decimal_number ::= decimal_integer | decimal_float
decimal_integer ::= digit_nonzero digit*
decimal_float ::= digit+ "." digit+
digit ::= "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
digit_nonzero ::= "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
```

有效 directive 示例：

```python
# loommux: timeout_seconds=1
# loommux: timeout_seconds=10
# loommux: timeout_seconds=120
# loommux: timeout_seconds=0.5
# loommux: timeout_seconds=10.0
```

无效 directive 示例：

```python
#loommux: timeout_seconds=120
# loommux timeout_seconds=120
# loommux: timeout=120
# loommux: timeout_seconds = 120
# loommux: timeout_seconds=120s
# loommux: timeout_seconds=.5
# loommux: timeout_seconds=1e3
# loommux: timeout_seconds=0
# loommux: timeout_seconds=-1
# loommux: timeout_seconds=abc
```

无效示例不是兼容形式。parser 不得为了接受无效示例而增加宽松分支。

### 5.4 Recognition rule

server 对整个 `freeform` 文本按行扫描。每一行必须完整匹配 timeout directive grammar 才计入有效 directive。

识别结果按以下规则解释：

1. 有效 directive 数量等于 1 时，本次 `run_python` 使用该 directive 中的秒值。
2. 有效 directive 数量等于 0 时，本次 `run_python` 使用默认等待时长 10 秒。
3. 有效 directive 数量大于 1 时，本次 `run_python` 使用默认等待时长 10 秒。
4. 无效 loommux 注释行不计入有效 directive 数量。
5. 无效 loommux 注释行不阻止 Python cell 执行。
6. 多条有效 directive 不产生优先级，不执行 first-wins，不执行 last-wins。

### 5.5 Timeout meaning

timeout directive 覆盖的是当前 MCP `run_python` 调用等待 execution 完成的最长时间。

timeout directive 不限制 Python code 的总运行时长。

timeout directive 不在超时后 interrupt execution。

timeout directive 不在超时后 reset kernel。

timeout directive 不改变后续 `run_python` 调用的默认等待时长。

timeout directive 不改变 `wait_python` 的 timeout 参数。

### 5.6 Runtime meaning

timeout directive 是 Python 注释。server 可以将包含该注释的原始 cell 提交给 IPython kernel。该注释不得被解释为 Python 变量、Python module attribute、IPython magic、decorator 或运行时配置对象。

server 不得为了 timeout directive 向 kernel 注入 `LOOMMUX_*` 变量。

server 不得在每次 `run_python` 前从 kernel 读取 timeout 变量。

server 不得在每次 `run_python` 后更新 kernel timeout 状态。

## 6. 详细设计

### 6.1 Entry point boundary

MCP entrypoint 必须暴露 freeform `run_python`。

推荐落点：

```text
src/loommux/mcp_ipython_content_server.py
```

该 entrypoint 应暴露：

```python
@mcp.tool(output_schema=None)
def run_python(freeform: str) -> ToolResult:
    ...
```

`src/loommux/mcp_ipython_server.py` 与 `src/loommux/mcp_ipython_content_server.py` 必须采用同一 freeform 输入契约。不得在 MCP tool surface 中暴露结构化 `run_python(code, timeout_seconds)`。

### 6.2 Adapter API

adapter 的公开执行方法是：

```python
run_python(freeform: str) -> dict[str, Any]
```

adapter 可以保留私有提交方法：

```python
_submit_python_cell(code: str, timeout_seconds: float) -> dict[str, Any]
```

`run_python` 负责：

1. 验证 `freeform` 是字符串。
2. 从 `freeform` 文本中识别 timeout directive。
3. 在有效 directive 数量等于 1 时取 directive 秒值。
4. 在其他情况下使用默认等待时长 10 秒。
5. 调用私有提交方法提交原始 `freeform` 文本。

私有提交方法负责 workspace、kernel、busy、execution 创建、kernel submit、等待、response snapshot 和 output log 语义。

### 6.3 Default timeout

`run_python` 的默认等待时长必须为 10 秒。该默认值适用于：

1. freeform `run_python` 没有有效 directive。
2. freeform `run_python` 有多条有效 directive。
3. freeform `run_python` 只有无效 loommux 注释行。

`wait_python` 的默认等待时长可以保持既有契约，除非另有文档明确修改。本文只要求 freeform `run_python` 默认等待时长为 10 秒。

### 6.4 Parser algorithm

parser 输入为 `freeform: str`。

parser 输出为：

```text
timeout_seconds: float
timeout_source: "directive" | "default"
```

parser 执行步骤：

1. 将 `freeform` 按行分割。
2. 对每一行执行完整行匹配。
3. 匹配模式只接受 canonical directive grammar。
4. 将每个匹配行中的数字解析为 `float`。
5. 丢弃非有限值或小于等于 0 的值。
6. 若剩余有效值数量等于 1，返回该值和 `timeout_source = "directive"`。
7. 否则返回 `10.0` 和 `timeout_source = "default"`。

推荐正则：

```text
^# loommux: timeout_seconds=([1-9][0-9]*|[0-9]+\.[0-9]+)$
```

该正则之后仍必须检查数值大于 0 且为有限数。`0`、`0.0` 和 `01` 不得作为有效 directive。

### 6.5 Submission rule

server 必须将原始 `freeform` 文本作为 Python code 提交给 kernel。server 不得为了移除 timeout directive 而修改 Python cell 文本。

该规则保证 execution 中保存的 code 与模型提交的 freeform 文本一致。timeout directive 是 Python 注释，提交给 kernel 不改变运行时行为。

### 6.6 Invalid and ambiguous directive behavior

无效 timeout directive 不产生 tool failure。

多条有效 timeout directive 不产生 tool failure。

无效或歧义情况统一回退到默认等待时长 10 秒。

server 不得向模型可见输出添加详细 parser 诊断。

server 可以在内部结构化状态中记录实际使用的 timeout 来源，但不得要求模型读取该字段才能继续工作。

### 6.7 Execution response

`run_python` 的 completed、error、running、busy、workspace_not_set、kernel_not_started 等状态语义沿用现有 adapter 设计。

如果 execution 在等待窗口内完成，`run_python` 返回 completed 或 error 的 execution surface。

如果 execution 在等待窗口后仍未完成，`run_python` 返回 running，并保留 execution 继续运行。

返回 running 时必须提供 `execution_id` 和 canonical `output_log`。

### 6.8 Output surface

模型可见输出仍由 `presentation.py` 和 result policy 决定。timeout directive 不改变 stdout、stderr、result_text、traceback 或 output log 文本。

当无效或歧义 directive 回退默认等待时，模型可见输出不需要包含详细 parser 诊断。

### 6.9 Interaction with other tools

`wait_python` 继续通过结构化参数表达等待目标和等待时长。

`read_python_output` 继续通过结构化参数表达 output log、stream、line range、line number 和 max chars。

`search_python_output` 继续通过结构化参数表达 query、stream、query mode、上下文和 max chars。

`interrupt_python` 和 `reset_python` 不读取 timeout directive。

## 7. Tool docstring 规格

Codex freeform `run_python` 的 docstring 必须直接说明 raw input 是 Python cell，并给出唯一 timeout directive 写法。

推荐 docstring：

```text
向当前 IPython kernel 提交 Python cell，并等待至完成或达到等待上限。

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
```

docstring 必须使用 reST/Sphinx 结构。tool description 正文必须用小标题分出输入、等待上限、返回表面和后续工具。正文必须保留 freeform 输入语义、canonical timeout directive、默认 10 秒、execution、running、output_log、分流日志和后续观察工具的信息。`:param freeform:` 和 `:returns:` 是结构化补充，不得承载模型唯一需要看到的 directive 规则。

docstring 不得展示其他 timeout directive 写法。

docstring 不得要求模型生成 JSON arguments。

docstring 不得描述运行时 `LOOMMUX_*` 变量。

## 8. 黑盒测试规格

### 8.1 测试环境

测试在 Linux 下执行。测试 workspace 必须有可执行的 `<workspace>/.venv/bin/python`，且该 Python 能导入 `ipykernel`。

测试应优先通过 MCP client 调用 Codex freeform entrypoint。对 parser 的精确边界可以补充 adapter 单元测试。

### 8.2 API-FF-001：freeform schema

目的：确认 Codex freeform entrypoint 的 `run_python` 满足 MCP freeform contract。

步骤：

1. 启动 Codex freeform MCP entrypoint。
2. 调用 `list_tools`。
3. 读取 `run_python` input schema。

期望：

1. `run_python` input schema 是 object。
2. `properties` 只包含 `freeform`。
3. `freeform` 类型是 string。
4. `required` 只包含 `freeform`。
5. schema 不包含 `code`。
6. schema 不包含 `timeout_seconds`。
7. schema 不包含输出截断参数。

### 8.3 API-FF-002：docstring contains only canonical directive

目的：确认工具描述只教模型一种 timeout directive 写法。

步骤：

1. 调用 `list_tools`。
2. 读取 `run_python` description。

期望：

1. description 包含 `# loommux: timeout_seconds=120`。
2. description 说明无 directive 时等待 10 秒。
3. description 不包含 `LOOMMUX_RUN_TIMEOUT_SECONDS`。
4. description 不包含 `timeout_seconds = 120`。
5. description 不要求 JSON arguments。

### 8.4 EXEC-FF-001：无 directive 使用默认 10 秒

目的：确认无 timeout directive 时 `run_python` 使用默认等待窗口。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
import time
time.sleep(11)
42
```

期望：

1. 返回 `ok == true`。
2. 返回 `status == "running"`。
3. 返回 `execution_id`。
4. 返回 `output_log`。
5. execution 继续运行。
6. 后续 `wait_python(execution_id, timeout_seconds=5)` 返回 completed，且 result 包含 `42`。

### 8.5 EXEC-FF-002：单条有效 directive 覆盖本次等待

目的：确认唯一有效 timeout directive 覆盖本次等待窗口。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
# loommux: timeout_seconds=0.5

import time
time.sleep(2)
42
```

期望：

1. 返回 `ok == true`。
2. 返回 `status == "running"`。
3. 返回 `execution_id`。
4. 后续 `wait_python(execution_id, timeout_seconds=5)` 返回 completed，且 result 包含 `42`。

### 8.6 EXEC-FF-003：有效 directive 不持久

目的：确认 timeout directive 只影响本次调用。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
# loommux: timeout_seconds=0.5

import time
time.sleep(1)
"first"
```

3. 等待该 execution 完成。
4. 再次调用 freeform `run_python`，输入：

```python
import time
time.sleep(2)
"second"
```

期望：

1. 第一次调用返回 `running`。
2. 第二次调用不因第一次 directive 沿用 0.5 秒。
3. 第二次调用在默认 10 秒窗口内返回 completed。
4. 第二次 result 包含 `"second"`。

### 8.7 EXEC-FF-004：多条有效 directive 回退默认

目的：确认多条有效 directive 不产生优先级。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
# loommux: timeout_seconds=0.5
# loommux: timeout_seconds=0.5

import time
time.sleep(2)
42
```

期望：

1. 返回 completed。
2. 返回结果包含 `42`。
3. 行为表明本次调用使用默认 10 秒，而不是 0.5 秒。

### 8.8 EXEC-FF-005：无效 directive 回退默认并继续执行

目的：确认无效 loommux 注释不阻止 Python cell 执行。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
# loommux: timeout_seconds = 0.5

import time
time.sleep(2)
42
```

期望：

1. 返回 completed。
2. 返回结果包含 `42`。
3. 行为表明本次调用使用默认 10 秒，而不是 0.5 秒。
4. 工具不返回 parser failure。

### 8.9 EXEC-FF-006：directive 注释不改变 Python runtime

目的：确认 timeout directive 不创建运行时控制变量。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
# loommux: timeout_seconds=1

"LOOMMUX_RUN_TIMEOUT_SECONDS" in globals()
```

期望：

1. 返回 completed。
2. result 表达式结果为 `False`。
3. 该 execution 不要求任何 `LOOMMUX_*` 变量存在。

### 8.10 EXEC-FF-007：directive 保留为普通 Python 注释

目的：确认 server 不需要修改提交给 kernel 的 Python cell。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
# loommux: timeout_seconds=1

print("hello")
```

期望：

1. 返回 completed。
2. stdout 包含 `hello`。
3. stdout 不包含 timeout directive 文本。
4. result 不包含 timeout directive 文本。

### 8.11 EXEC-FF-008：运行中输出仍可读取

目的：确认 freeform timeout 不改变长任务分段观察语义。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
# loommux: timeout_seconds=0.5

import time
for i in range(3):
    print(i, flush=True)
    time.sleep(1)
```

3. 使用返回的 `execution_id` 调用 `read_python_output(execution_id=execution_id, stream="stdout")`。
4. 调用 `wait_python(execution_id, timeout_seconds=5)`。
5. 再次读取 stdout。

期望：

1. 第一次 `run_python` 返回 running。
2. 运行中读取返回该 execution 的 stdout log。
3. 完成后 stdout 包含 `0`、`1`、`2`。

### 8.12 CTRL-FF-001：timeout 不 interrupt

目的：确认 timeout directive 只控制等待，不控制执行终止。

步骤：

1. 调用 `set_workspace(valid_workspace)`。
2. 调用 freeform `run_python`，输入：

```python
# loommux: timeout_seconds=0.5

import time
time.sleep(2)
99
```

3. 在返回 running 后调用 `python_status()`。
4. 调用 `wait_python(execution_id, timeout_seconds=5)`。

期望：

1. `run_python` 返回 running。
2. `python_status()` 在 execution 完成前报告 busy。
3. `wait_python` 返回 completed。
4. result 包含 `99`。

### 8.13 COMPAT-FF-001：其他工具保持结构化

目的：确认 freeform 改造只作用于 `run_python`。

步骤：

1. 调用 `list_tools`。
2. 检查 `wait_python`、`read_python_output`、`search_python_output`、`python_execution_status` 的 schema。

期望：

1. `wait_python` 仍接受结构化参数。
2. `read_python_output` 仍接受结构化参数。
3. `search_python_output` 仍接受结构化参数。
4. `python_execution_status` 仍接受结构化参数。
5. 这些工具不声明唯一 `freeform` 输入字段。

## 9. 单元测试规格

### 9.1 Parser unit tests

parser 单元测试必须覆盖：

1. 空文本返回 `(10.0, "default")`。
2. 无 directive 的 Python code 返回 `(10.0, "default")`。
3. `# loommux: timeout_seconds=120` 返回 `(120.0, "directive")`。
4. `# loommux: timeout_seconds=0.5` 返回 `(0.5, "directive")`。
5. `# loommux: timeout_seconds=0` 返回 default。
6. `# loommux: timeout_seconds=-1` 返回 default。
7. `# loommux: timeout_seconds=.5` 返回 default。
8. `# loommux: timeout_seconds=1e3` 返回 default。
9. `# loommux: timeout_seconds = 120` 返回 default。
10. 两条有效 directive 返回 default。
11. 一条有效 directive 与普通 Python 注释共存时返回 directive。
12. Windows 换行输入按行识别。

### 9.2 Adapter unit tests

adapter 单元测试必须覆盖：

1. freeform wrapper 调用结构化 core 时传入原始 source。
2. freeform wrapper 在唯一有效 directive 时传入 directive timeout。
3. freeform wrapper 在无 directive 时传入 10 秒。
4. freeform wrapper 在多条有效 directive 时传入 10 秒。
5. freeform wrapper 不写 adapter 持久 timeout 状态。

### 9.3 MCP schema tests

MCP schema tests 必须覆盖 Codex freeform entrypoint 的 `run_python` schema。测试不得只调用 Python function signature 后自行推断 schema。

## 10. 覆盖率要求

实现完成时，`loommux` 项目的测试覆盖率不得低于 90%。覆盖率统计应覆盖 `src/loommux` 下的实现代码。

若现有测试基础设施尚未统计覆盖率，实施者必须为 `loommux` 测试命令增加 coverage 统计，并在项目可用命令中保留该统计方式。

覆盖率达标不能替代黑盒验收。schema、freeform 输入、timeout directive、默认回退、运行中读取和不持久语义都必须有对应测试。

## 11. 验收条件

实现满足以下全部条件时视为通过验收：

1. Codex freeform entrypoint 的 `run_python` input schema 精确为唯一 `freeform: string`。
2. `run_python` 模型输入是原始 Python cell 文本。
3. `run_python` 不要求模型生成 JSON arguments。
4. `run_python` 默认等待时长为 10 秒。
5. 全文中恰好一行完整匹配 `# loommux: timeout_seconds=<positive decimal number>` 时，本次调用使用该秒值。
6. 无匹配、多匹配或无效 loommux 注释时，本次调用使用 10 秒。
7. 无效或歧义 directive 不阻止 Python cell 执行。
8. timeout directive 不创建、读取或更新 Python runtime 变量。
9. timeout directive 不持久影响后续 `run_python` 调用。
10. timeout 到达后 execution 继续运行。
11. running execution 仍可通过 output log 读取、搜索、等待、中断和 reset。
12. `wait_python`、`read_python_output`、`search_python_output` 等工具保持结构化输入。
13. 工具 docstring 只展示 canonical directive 写法。
14. 文档中的黑盒测试和单元测试通过。
15. `loommux` 测试覆盖率不低于 90%。

## 12. 结论

`run_python` freeform 契约把 Python code 从 JSON 参数壳中移出，使模型直接书写 Python cell。timeout directive 使用唯一 Python 注释形态表达本次等待覆盖值。该 directive 是 adapter 消费的 cell-level 控制注释，不是 Python runtime 状态。默认 10 秒、唯一有效 directive 覆盖、其他情况回退默认并继续执行，是该契约的稳定语义。
