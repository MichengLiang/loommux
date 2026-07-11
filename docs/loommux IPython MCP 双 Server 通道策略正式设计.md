# Historical Design Notice

> Superseded by [IPython MCP Execution Control Plane Design](ipython-mcp-execution-control-plane-design.md). Result-channel policies remain supported, but the old `execution_id`, output-address, and workspace-tool contract below is historical only.

# loommux IPython MCP 双 Server 通道策略正式设计

> 历史设计材料。workspace、interpreter、工具集合和 kernel 生命周期以
> `docs/ipython-mcp-adapter-design.md` 为当前契约。

## 1. 文档对象

本文档定义 `loommux` 项目中 IPython MCP adapter 的双 server 通道策略。该策略的对象不是一个可动态切换行为的 server，也不是一组提示词约定，而是两个对外可配置、可审查、可测试的 MCP server entrypoint。两个 server 共享同一个 IPython runtime core，暴露同一组 MCP 工具，遵守同一套 workspace、kernel、execution 和 output log 语义，但在 MCP result 的输出通道上具有不同协议表面。

第一个 server 是标准双通道 server。它保留 `content` 与 `structuredContent`，适用于遵守 MCP 分工的客户端：模型阅读 `content`，程序消费 `structuredContent`。该 server 继续服务正常 FastMCP client、测试、自动化程序和任何需要结构化状态字段的调用方。

第二个 server 是 content-only server。它只返回 `content`，不声明 output schema，不返回 `structuredContent`，不通过 `_meta` 或其它旁路携带业务状态。该 server 适用于已经观察到会误用 `structuredContent` 的客户端。对于这类客户端，服务端不再提供结构化输出对象，从协议表面强制其只能接收 pretty text。

本文档只定义这两个 server entrypoint 的职责边界、模块结构、工具契约、结果通道策略、测试要求、验收条件和配置方式。本文档不重新定义 IPython kernel 生命周期，不重新定义 execution 状态机，不重新定义 output log 阅读模型，不重新定义 pretty text 展示规则，不引入客户端自动识别，不引入运行时模式切换，不引入环境变量、CLI flag 或其它隐藏开关。

## 2. 问题背景

`loommux` 当前已经实现了一个基于 FastMCP 的 IPython MCP adapter。其核心能力是让 agent 通过 MCP 工具访问一个持久 IPython kernel：先设置 workspace，再提交 Python code，后续可以查看 execution 状态、读取 output log、搜索 output log、等待执行完成、中断执行或重置 kernel。

当前标准 server 的核心返回逻辑是：adapter 返回原始状态字典，presentation 层把状态字典格式化成 pretty text，然后 server 返回 `ToolResult(content=pretty_text, structured_content=raw_status)`。这形成双通道结果：`content` 是给模型阅读的文本面，`structuredContent` 是给程序消费的结构化面。

这个设计本身成立，并且对合规客户端是正确的。问题来自一类客户端的实际行为：它们不按照“模型读 content、程序读 structuredContent”的分工使用 MCP result，而是默认或优先读取 `structuredContent`，甚至把结构化对象注入模型上下文。这样一来，`presentation.py` 中对模型阅读面的优化会被绕过。模型看到的不是 Python 可见输出或自然语言提示，而是 raw dict、空字段、状态字段和机器对象。

这类问题不能靠提示词解决。只要 server 返回 `structuredContent`，客户端就有读取它的协议可能性。对于已经确认会误用 `structuredContent` 的客户端，正确策略不是继续劝它读 `content`，也不是把 `structuredContent` 做得更短，而是提供一个完全没有 `structuredContent` 的 server entrypoint。该 entrypoint 的协议表面只包含 content blocks，使客户端没有结构化结果可读。

因此，`loommux` 需要把当前单一标准 server 扩展为双 server family：标准 server 服务合规客户端，content-only server 服务问题客户端。二者不是一个 server 的两种运行模式，而是两个明确的 MCP server 人工制品。

## 3. 设计目标

本设计有七个目标。

第一，保留标准双通道 server。当前 `mcp_ipython_server.py` 的外部身份、工具集合、结构化返回能力和测试契约应保持稳定。合规客户端不应该因为 content-only server 的新增而失去结构化消费能力。

第二，新增 content-only server。该 server 必须从 `list_tools` 到 `call_tool` 都不暴露结构化输出面。工具不声明 output schema，工具调用不返回 `structuredContent`，FastMCP client 不应产生 `.data`。

第三，两个 server 共享 runtime core。`IPythonMCPAdapter`、`Execution`、`ExecutionLogs`、`LineLog`、`KernelSession` 不应复制，不应分叉，不应知道自己服务的是标准 server 还是 content-only server。

第四，两个 server 共享 presentation。`presentation.py` 是模型阅读面的唯一来源。content-only server 必须使用同一套 `format_tool_result_text()`，不能把 raw dict 改名塞进 content，也不能为 content-only 单独写一套偏离标准展示规则的文案。

第五，结果通道策略显式化。server 层应明确表达“dual channel”和“content only”这两种 MCP result policy。通道策略不应作为散落的小技巧隐藏在工具函数里。

第六，手动配置是唯一选择机制。用户根据客户端行为，在该客户端的 MCP 配置中选择标准 server 文件或 content-only server 文件。设计不提供自动识别客户端、不提供同一入口动态切换、不提供隐藏开关。

第七，测试可检查。两个 server 的工具集合一致性、标准 server 的 structuredContent 保留、content-only server 的 structuredContent 缺失、outputSchema 缺失、pretty text 保留，都必须有测试覆盖。

## 4. 非目标

本设计明确不做以下事项。

不废弃标准 server。`structuredContent` 对正常客户端、测试和程序消费仍然有价值，不应因为某些客户端误用而从标准 server 中删除。

不把 content-only 做成程序友好模式。content-only server 的目标不是让程序继续读 `.data`，而是让模型只读 pretty text。因此它不提供最小 structuredContent，也不提供隐藏业务 `_meta`。

不引入 output 截断参数。已有设计规定输出阅读由 output log、line_range、search、max_chars 等工具承担，server 通道策略不改变输出保留和读取语义。

不修改 adapter 状态机。workspace 校验、kernel 启动、execution 创建、running/busy/error/killed 状态、中断和 reset 语义都不在本文档范围内改变。

不修改 content 展示规则。小输出 output-first、running/large 省略正文、日志读取 text-first、搜索命中 text-first、状态工具 compact surface，这些规则继续由已有 output surface design 负责。

不设计单入口多行为。对用户和客户端配置而言，标准 server 与 content-only server 是两个文件入口，不是同一个 server 的参数化模式。

## 5. 术语

**标准双通道 server** 指现有 `mcp_ipython_server.py`。该 server 返回 `ToolResult(content=pretty_text, structured_content=raw_status)`，并可在工具声明中保留 output schema。它面向合规客户端。

**content-only server** 指新增 `mcp_ipython_content_server.py`。该 server 返回 `ToolResult(content=pretty_text)`，不传 `structured_content`，工具注册时 `output_schema=None`。它面向会误用 structuredContent 的客户端。

**runtime core** 指 `IPythonMCPAdapter`、`Execution`、`ExecutionLogs`、`LineLog`、`KernelSession` 等负责 Python runtime、execution 状态和 output log 的核心模块。

**presentation layer** 指 `presentation.py` 中的 `format_tool_result_text(tool_name, status)` 及其私有 formatter。它负责把 raw status 转成模型阅读面。

**result channel policy** 指 server 层把 pretty text 和 raw status 组装成 MCP result 的规则。本文档定义 `dual channel` 与 `content only` 两种 policy。

**raw status** 指 adapter 返回的原始状态字典，例如 `{'ok': True, 'status': 'completed', 'execution_id': ..., 'output_log': ...}`。

**pretty text** 指 presentation layer 生成的文本 content，例如 Python 输出正文、自然语言提示、日志读取结果、搜索命中或 compact status。

## 6. 总体架构

双 server 架构如下：

```text
MCP client A, compliant
  -> mcp_ipython_server.py
       -> IPythonMCPAdapter
       -> presentation.format_tool_result_text
       -> ToolResult(content=pretty_text, structured_content=raw_status)

MCP client B, misuses structuredContent
  -> mcp_ipython_content_server.py
       -> IPythonMCPAdapter
       -> presentation.format_tool_result_text
       -> ToolResult(content=pretty_text)
```

两个 server 的共享部分是 adapter 和 presentation。两个 server 的差异只发生在 MCP result channel 和 tool output schema declaration。

这种架构有一个核心纪律：内部共享，外部分明。内部代码可以复用，避免 runtime 行为分叉；外部 server entrypoint 必须分开，避免客户端配置层看不出协议表面。

## 7. 文件结构设计

建议最终文件结构如下：

```text
src/loommux/
  adapter.py
  execution.py
  kernel_session.py
  output_log.py
  presentation.py
  mcp_result_policy.py
  mcp_ipython_server.py
  mcp_ipython_content_server.py
```

`adapter.py` 保持 runtime core 职责，不感知 server 类型。

`presentation.py` 保持 pretty text 职责，不感知 structuredContent 是否发送。

`mcp_result_policy.py` 是新增模块，封装 MCP result channel policy。它不注册工具，不启动 server，不调用 adapter。它只接收 `tool_name`、`raw_status` 和 policy，返回 `ToolResult`。

`mcp_ipython_server.py` 是标准 server entrypoint。它创建 adapter，注册工具，使用 dual channel policy。

`mcp_ipython_content_server.py` 是 content-only server entrypoint。它创建 adapter，注册同一组工具，使用 content-only policy，并在工具注册处关闭 output schema。

可选地，可以新增 `mcp_ipython_common.py` 存放共享常量，例如 `EXPECTED_TOOL_NAMES` 或 docstring 文本。但第一版不需要为了消灭所有重复而牺牲两个 server 的声明清晰度。

## 8. Result Channel Policy 设计

新增模块 `mcp_result_policy.py`：

```python
from typing import Any, Literal, Mapping, cast
from fastmcp.tools import ToolResult
from loommux.presentation import format_tool_result_text

ResultChannelPolicy = Literal['dual_channel', 'content_only']

def make_tool_result(tool_name: str, raw_status: Mapping[str, Any], policy: ResultChannelPolicy) -> ToolResult:
    text = format_tool_result_text(tool_name, raw_status)
    if policy == 'dual_channel':
        return ToolResult(content=text, structured_content=dict(raw_status))
    if policy == 'content_only':
        return ToolResult(content=text)
    raise ValueError(f'unknown result channel policy: {policy}')
```

具体实现可以根据类型检查需要调整，但语义必须保持：dual channel 传 `structured_content`；content-only 不传 `structured_content`。content-only 不得把 `raw_status` 放入 `_meta`，不得把 `raw_status` 序列化后追加到 content，不能通过其它字段暴露业务状态。

这里使用浅拷贝 `dict(raw_status)` 是为了让 ToolResult 中的 structured content 与 adapter 返回对象解耦。adapter 当前每次返回新 dict，直接传也可行，但浅拷贝表达了“这是输出快照”的意图。content-only policy 不复制 raw status，因为它不暴露结构化状态。

## 9. 标准 Server 设计

标准 server 文件继续是：

```text
src/loommux/mcp_ipython_server.py
```

它继续提供：

```python
def create_mcp() -> FastMCP: ...

mcp = create_mcp()

if __name__ == '__main__':
    mcp.run()
```

该 server 注册工具时可以保持当前函数签名与返回注解风格，例如 `-> dict[str, Any]`，因为标准 server 需要保留结构化输出能力。工具函数运行时实际返回 `ToolResult`，FastMCP 接受该行为；当前项目已有测试验证 `result.data` 和 `result.content` 同时存在。

标准 server 的 `_tool_result()` 可改为：

```python
def _tool_result(tool_name: str, raw_status: dict[str, Any]) -> ToolResult:
    return make_tool_result(tool_name, raw_status, 'dual_channel')
```

或者直接在每个工具函数里调用 `make_tool_result()`。建议保留 `_tool_result()`，因为 server 文件可读性更好。

标准 server 的协议契约：

1. `list_tools` 返回同一组工具。
2. 工具 input schema 正常。
3. 工具 output schema 可继续存在。
4. `call_tool` 返回 content blocks。
5. `call_tool` 返回 structuredContent。
6. FastMCP client 的 `result.data` 可用。
7. `result.content[0].text` 是 presentation layer 生成的 pretty text。
8. `result.data` 是 adapter raw status。
9. 现有黑盒测试继续通过。

标准 server 是默认稳定入口。现有用户、现有测试、合规客户端不需要迁移。

## 10. Content-Only Server 设计

新增 server 文件：

```text
src/loommux/mcp_ipython_content_server.py
```

它提供同样形状的入口：

```python
def create_mcp() -> FastMCP: ...

mcp = create_mcp()

if __name__ == '__main__':
    mcp.run()
```

该 server 注册的工具集合必须与标准 server 完全一致：

```text
set_workspace
run_python
python_status
python_execution_status
read_python_output
search_python_output
wait_python
interrupt_python
reset_python
```

content-only server 的每个工具都必须显式关闭 output schema：

```python
@mcp.tool(output_schema=None)
def python_status() -> ToolResult:
    return _tool_result('python_status', adapter.python_status())
```

返回注解应为 `ToolResult`。这不是为了让 FastMCP 自动生成 schema，而是为了阻止读代码的人误以为该函数承诺 dict output。输入参数仍然保留正常类型注解，因此 input schema 不受影响。

content-only server 的 `_tool_result()`：

```python
def _tool_result(tool_name: str, raw_status: dict[str, Any]) -> ToolResult:
    return make_tool_result(tool_name, raw_status, 'content_only')
```

content-only server 的协议契约：

1. `list_tools` 返回同一组工具。
2. 工具 input schema 正常。
3. 工具 outputSchema 全部为 None。
4. `call_tool` 返回 content blocks。
5. `call_tool` 不返回 structuredContent。
6. FastMCP client 的 `result.structured_content is None`。
7. FastMCP client 的 `result.data is None`。
8. content 文本必须来自 `presentation.py`。
9. content 文本不得追加 raw status JSON。
10. result `_meta` 不得携带业务状态。

content-only server 的成功标准不是“还能程序化读字段”，而是“没有结构化字段可读”。任何让 `.data` 恢复可用的改动都应视为破坏该 server 的目标。

## 11. 两个 Server 的关系

两个 server 是同一个项目下的 sibling entrypoints。它们不是继承关系，不是 fallback 关系，也不是同一 server 的 mode。它们共享核心模块，但对外身份独立。

标准 server 可以被称为：

```text
loommux IPython MCP adapter
```

content-only server 可以被称为：

```text
loommux IPython MCP adapter (content only)
```

server name 可以在 `FastMCP(...)` 初始化时体现差异。这样客户端日志或 inspector 里也能看到当前连接的是哪个 server。

两个 server 的工具名称应相同。不要给 content-only 工具加前缀，例如 `content_run_python`。工具语义没有变，变的是 server result channel。工具名变化会增加模型使用成本，也会让两个 server 的说明难以保持一致。

两个 server 的 docstring 应尽量一致。content-only server 的 docstring 可以保持现有工具说明，因为这些说明描述的是工具语义和 output log handle 规则，不依赖 structuredContent。必要时可以在 content-only server 的模块级文档字符串中说明该 server 不返回 structuredContent，而不必在每个工具 docstring 中重复。

## 12. 配置方式

用户根据客户端行为手动选择 server。

合规客户端配置标准 server：

```text
/home/t103o/workbench/projects/loommux/src/loommux/mcp_ipython_server.py
```

会误用 structuredContent 的客户端配置 content-only server：

```text
/home/t103o/workbench/projects/loommux/src/loommux/mcp_ipython_content_server.py
```

如果某个 MCP host 使用 module path 而不是 file path，则对应为：

```text
loommux.mcp_ipython_server
loommux.mcp_ipython_content_server
```

客户端配置层是唯一选择机制。不要为同一 server 文件设计隐藏切换。不要让同一路径在不同启动上下文中表现不同。这样每个客户端到底使用哪个协议表面，可以从配置文件直接审查。

## 13. 为什么 Content-Only 不保留最小 StructuredContent

content-only server 不应返回如下结构：

```json
{"execution_id": "exec-000001", "status": "completed"}
```

哪怕这看起来很方便，也会破坏目标。因为问题客户端一旦看到 structuredContent，就可能优先使用它。模型随后只看到一个干瘪状态对象，而不是完整 pretty text。这样会重复当前问题，只是把 raw status 换成更小的 raw status。

content-only server 不重复 `execution_id`、`status` 或 `output_log`。最新 execution 的继续观察使用 `wait_python()`、`read_python_output()` 或 `search_python_output()` 的 current-or-last 默认选择规则；工具说明定义 stream 与 handle 规则。需要程序 API 的客户端应使用标准 server。

同理，content-only server 不应在 `_meta` 中放业务状态。MCP `_meta` 是运行时元数据通道，不应被用作 raw status 的替代 structuredContent。对于会误用 structuredContent 的客户端，也不能假设它不会误用 `_meta`。因此 content-only server 的业务状态只能存在于 content text 中。

## 14. 为什么 Content-Only 不返回 Raw JSON Text

content-only server 也不应把 raw status 序列化成 JSON 字符串放入 content。这样虽然协议上没有 structuredContent，但模型看到的仍然是字段字典，presentation 优化仍然被绕过。

content-only server 的目标不是“没有 structuredContent 这个字段”，而是“模型看到的是设计过的 content 表面”。因此 content 必须只来自 `format_tool_result_text()`。如果某些状态需要更好地展示，应改 `presentation.py`，而不是在 content-only server 里额外追加 raw JSON。

## 15. Presentation 的权威性

`presentation.py` 是两个 server 共同的模型阅读面。它的输出规则具有权威性：

`run_python` 和 `wait_python` 对小完成输出直接展示 combined output body，不追加 execution 或日志元数据。这样模型只看到 Python 可见输出，而不是状态字段。

running execution 不展示 partial output body，而是给出等待、检查状态或读取已可用输出的自然语言提示。

大输出不塞满工具返回，而是给出调用 `read_python_output()` 的自然语言提示。

`read_python_output` 直接展示读取到的日志文本；空结果给出自然语言提示。它不是状态快照。

`search_python_output` 直接展示命中和上下文；无命中给出自然语言提示。

`python_status` 和 `python_execution_status` 是状态工具，保持 compact status-oriented text。

content-only server 的存在，正是为了保证这些 presentation 规则成为问题客户端唯一可见的结果面。

## 16. 实现步骤

第一步，新增 `mcp_result_policy.py`。实现 `ResultChannelPolicy` 与 `make_tool_result()`。该模块应有单元测试，直接给 fixed raw status，验证 dual channel 返回带 structured content，content-only 返回不带 structured content。

第二步，修改标准 `mcp_ipython_server.py`。把当前 `_tool_result()` 改为调用 `make_tool_result(..., 'dual_channel')`。保持工具集合、docstring、adapter 调用和模块入口不变。该改动不改变外部行为，只是让策略显式化。

第三步，新增 `mcp_ipython_content_server.py`。复制标准 server 的工具集合和 adapter lifespan 结构，但每个工具使用 `@mcp.tool(output_schema=None)`，返回注解用 `ToolResult`，结果包装使用 `make_tool_result(..., 'content_only')`。

第四步，新增 content-only 测试。测试不需要重复所有 IPython 行为，但必须覆盖协议表面：list_tools outputSchema、call_tool structured_content、data、content text。

第五步，新增两个 server 工具集合一致性测试。任何新增工具都必须同时出现在两个 server。

第六步，补充文档。新增本文档到 `docs/`，说明两个 server 的用途、配置路径、契约差异和验收标准。

第七步，运行验证。至少运行 `uv run pytest`。如果项目已有 ruff/basedpyright 验证流程，应运行相应命令。

## 17. 测试规格

新增测试文件建议命名为：

```text
tests/test_ipython_mcp_content_server.py
```

测试一：content-only server 工具集合完整。

步骤：使用 FastMCP in-memory Client 连接 `loommux.mcp_ipython_content_server.create_mcp()`，调用 `list_tools()`。

期望：工具名集合等于标准 `EXPECTED_TOOLS`。每个工具存在 input schema。每个工具 outputSchema 为 None。

测试二：content-only `python_status` 无 structured content。

步骤：调用 `python_status()`。

期望：`result.structured_content is None`，`result.data is None`，`result.content` 只有一个 text block，文本等于或符合当前 `presentation.py` 对初始状态的输出，例如 `kernel: not_started` 开头。

测试三：content-only `set_workspace` 无 structured content。

步骤：准备 valid workspace，调用 `set_workspace(path)`。

期望：调用成功的事实只能通过 content 文本观察；`structured_content is None`，`data is None`。文本应包含 workspace 设置成功的 pretty surface。具体文本以 presentation 当前规则为准。

测试四：content-only `run_python` 小输出保留 pretty text。

步骤：设置 workspace，调用 `run_python(freeform="print('hello')\n42")`。

期望：content 文本等于 Python visible output，包含 `Out[n]:` result 或 stdout，不包含 execution id 或 output log handle。`structured_content is None`，`data is None`。

测试五：content-only error execution 不返回 structured content。

步骤：调用 `run_python(freeform="1 / 0")`。

期望：content 文本包含 traceback 或错误可见文本，不包含 traceback log handle；`structured_content is None`，`data is None`。

测试六：content-only running output 不返回 partial body。

步骤：运行 sleep 代码，timeout 很短。

期望：content 文本是调用 `wait_python()`、`python_status()` 或 `read_python_output()` 的自然语言提示；`structured_content is None`，`data is None`。

测试七：标准 server 不退化。

步骤：现有标准 server 测试继续运行。

期望：标准 server 的 `result.data` 仍然可用，`structured_content` 仍然存在，`content` 仍然是 pretty text。

测试八：两个 server 工具集合一致。

步骤：分别 list standard server 和 content-only server。

期望：工具名集合完全相同。content-only 的 outputSchema 全 None；标准 server 不要求全 None。

## 18. 验收条件

本设计实现后，必须满足以下条件。

1. `src/loommux/mcp_ipython_server.py` 继续存在，并保持标准双通道行为。
2. `src/loommux/mcp_ipython_content_server.py` 新增，并可作为独立 MCP server 运行。
3. 两个 server 均暴露同一组九个工具。
4. 两个 server 使用同一个 `IPythonMCPAdapter` 语义，不复制 runtime core。
5. 两个 server 使用同一个 `presentation.py` 生成 content。
6. 标准 server 调用结果包含 structuredContent。
7. 标准 server 的 FastMCP client `result.data` 可用。
8. content-only server 的 `list_tools` 中每个工具 outputSchema 为 None。
9. content-only server 的调用结果不包含 structuredContent。
10. content-only server 的 FastMCP client `result.data is None`。
11. content-only server 不在 `_meta` 中携带 raw status。
12. content-only server 不把 raw status JSON 追加到 content。
13. content-only server 的 content 文本与标准 server 的 content 文本同源。
14. 现有标准 server 测试继续通过。
15. 新增 content-only 协议表面测试通过。
16. 新增两个 server 工具集合一致性测试通过。
17. 文档说明两个 server 的用途和配置路径。

## 19. 配置示例

标准客户端使用标准 server：

```json
{
  "mcpServers": {
    "loommux_ipython": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "/home/t103o/workbench/projects/loommux/src/loommux/mcp_ipython_server.py"
      ]
    }
  }
}
```

问题客户端使用 content-only server：

```json
{
  "mcpServers": {
    "loommux_ipython_content": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "/home/t103o/workbench/projects/loommux/src/loommux/mcp_ipython_content_server.py"
      ]
    }
  }
}
```

实际配置格式取决于客户端。原则不变：不同客户端手动指向不同 server 文件。server 文件路径就是协议表面的选择。

## 20. 维护规则

以后新增 MCP 工具时，必须同时更新两个 server。新增工具的 runtime 行为应在 adapter 或相关 core 模块中实现，presentation 中添加 pretty text surface，标准 server 和 content-only server 分别注册该工具。

新增工具时必须补工具集合一致性测试。不能只在标准 server 增加工具，也不能只在 content-only server 增加工具。

如果 presentation 规则调整，两个 server 应同时受益。如果某个展示规则只适合 content-only server，不应直接写在 content-only server 文件中，而应先判断是否属于通用模型阅读面。如果属于，应进入 `presentation.py`；如果不属于，应重新评估它是否应该存在。

如果将来标准 server 的 structuredContent 字段契约变化，应更新标准 server 文档和测试，但 content-only server 仍然不应暴露 structuredContent。

如果将来 FastMCP 修改 ToolResult 行为，应用测试捕获：content-only server 的 `structured_content is None` 和 `data is None` 是硬验收，不可默默退化。

## 21. 设计结论

`loommux` 的正确结构不是一个试图适配所有客户端行为的单 MCP server，而是一组协议表面明确的 twin server entrypoints。标准 server 保留完整双通道，服务合规客户端和程序化消费；content-only server 关闭结构化输出，服务会误用 structuredContent 的客户端。选择发生在客户端 MCP 配置层，由用户手动指定 server 文件。内部共享 runtime core 和 presentation，外部保持两个固定、可审查、可测试的人工制品。

这个设计的关键不是“少改代码”，而是“把对象做完整”。标准 server 是完整的双通道杯子，content-only server 是完整的纯文本杯子。它们都接同一套 IPython 水路，但给不同客户端不同出水口。对守规矩客户端，给结构化能力；对不守规矩客户端，不给结构化抓手。这样 `presentation.py` 的模型阅读面优化才不会被绕过，`adapter.py` 的结构化状态能力也不会被误删。两个 server 各自完整，边界清楚，配置明确，测试可证。
