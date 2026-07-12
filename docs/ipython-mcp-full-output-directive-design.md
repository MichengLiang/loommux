# IPython MCP 完整输出标记设计

## 1. 文档职责

本文定义 loommux IPython MCP 中完整输出标记的公共契约。它规定调用者如何在
一个 freeform Python cell 中声明完整阅读意图、该声明如何改变可观察的工具
结果，以及后续开发者必须验证的行为。

执行编号、记录选择、kernel 生命周期、五个输出流和两个 MCP result channel
由 [Coding Agent Control Plane Design](coding-agent-control-plane-design.md)
定义。本文只为其中的输出交付规则增加一个 cell-level 标记；它不重新定义
execution 身份、日志读取坐标或 Python 执行语义。

## 2. 问题与目标体验

### 2.1 默认体验

`run_python` 与 `wait_python` 的默认交付规则保护一次 MCP 响应免于承载过长
正文：当 execution 已达到终态且 combined 输出超过 300 行时，响应保留该
execution 供后续读取，但不直接返回完整正文。调用者使用
`read_python_output` 或 `search_python_output` 继续读取。

这条默认规则适用于调用者尚不知道输出规模、只需要定位局部内容，或不希望
把大量日志放入当前对话的情形。

### 2.2 完整阅读体验

另一类情形中，调用者在提交 cell 前已经知道：该 execution 的完整 combined
输出本身就是当前任务需要阅读的对象。例如，cell 有意生成一份完整报告、清单、
审阅材料或其他确定需要整体分析的文本。把这类结果按 300 行分割，不增加判断
质量，只要求调用者额外发起多次读取调用并自行重组上下文。

完整输出标记使调用者能够在提交时表达这一意图。带有该标记的 execution 达到
终态后，loommux 直接交付已收集的完整 combined 输出，不以 300 行作为省略
正文的条件。

完整输出标记不是全局配置。它只影响写有标记的那个 execution；没有标记的
execution 继续使用默认交付规则。

## 3. 公开输入契约

### 3.1 Canonical 标记

完整输出标记的唯一 authored surface 是一行完整注释：

```python
# loommux: full_output
```

它没有参数值。标记的存在表达一个布尔事实：该 execution 的终态 combined
输出应当完整交付。

调用者可将它与 timeout directive 并列使用：

```python
# loommux: timeout_seconds=120
# loommux: full_output

build_report()
```

两条注释分别控制本次工具调用等待多久，以及该 execution 的终态输出如何交付。
它们互不覆盖，也不互相改变解释方式。

### 3.2 识别规则

server 对 `freeform` 的每一行独立匹配。完整输出标记只接受下列完整行：

```text
^# loommux: full_output$
```

至少出现一条有效标记时，完整输出请求生效。该标记是幂等的；重复出现不改变
请求含义。包含额外空格、额外文本或其他拼写的注释不是完整输出标记。

标记保留在原始 cell 中提交给 IPython kernel，并作为普通 Python 注释执行。
它不创建变量、不改变 namespace、不改变 Python 代码的控制流，也不改变后续
cell 的默认输出规则。

## 4. 可观察行为

### 4.1 交付决策

下表只描述终态 execution 的正文交付。`combined` 是 stdout、stderr、显示
结果与 traceback 按 IOPub 到达顺序组成的阅读流。

| execution 条件 | 完整输出标记 | `run_python` / `wait_python` 的正文结果 |
| --- | --- | --- |
| 仍在运行 | 有或无 | 不返回不断增长的完整正文；返回 running 状态与 execution。 |
| 已终态，combined 不超过 300 行 | 无 | 按默认规则直接返回 combined 正文。 |
| 已终态，combined 超过 300 行 | 无 | 按默认规则省略正文，调用者使用读取或搜索工具。 |
| 已终态，任意 combined 行数 | 有 | 直接返回完整 combined 正文。 |

“完整”指该 execution 在响应时已经收集到的整个 combined 输出流。对
`completed` execution，它通常是完整的 Python 可见结果；对 `error`、
`interrupted` 或 `killed` execution，它包含终态前已经到达的 stdout、stderr、
显示结果与 traceback。完整输出标记不把未产生的输出视为已经存在。

### 4.2 在默认等待时间内完成

若带标记的 cell 在 `run_python` 本次等待窗口内达到终态，`run_python` 直接
返回完整 combined 正文。即使该正文有 1,000 行或更多，300 行默认阈值也不
触发正文省略。

调用者不需要在这种情形下额外调用 `read_python_output`，也不需要把多个读取
片段重新拼接成完整上下文。

### 4.3 先返回 running、后续完成

完整输出标记属于 execution，而不是只属于最初的 `run_python` 工具调用。
因此，若 `run_python` 因等待到期返回 running，调用者随后对同一 execution
调用 `wait_python`，在该记录达到终态时仍获得完整 combined 正文。

在 execution 尚未达到终态时，标记不要求 server 返回部分的“完整结果”。
调用者需要观察进度时，仍可用 `read_python_output` 读取当前已到达的流文本。

### 4.4 其他工具

`python_execution_status` 继续返回状态与元数据，不因完整输出标记而返回正文。
`read_python_output` 与 `search_python_output` 的输入、行坐标、stream 选择、
裁切和搜索语义完全不变；它们继续用于默认省略的记录、局部阅读与检索。

`interrupt_python` 继续只发送中断信号。带标记的 execution 在 kernel 到达终态
后，可由 `wait_python` 返回截至终态所收集的完整 combined 输出。

`reset_python` 继续将运行中的 execution 标记为 `killed`，并保留该记录。
若该记录带有完整输出标记，后续对其调用 `wait_python` 时返回其已收集的完整
combined 输出。

## 5. MCP Result Surface

### 5.1 两个入口的一致内容

完整输出标记不创建新的工具，也不改变八个既有工具的输入 schema。它只改变
`run_python` 和 `wait_python` 对已选 execution 的正文交付规则。每个结果仍以
`In [execution]:` 开始；该 input-history header 不属于 combined 流，但使完整
输出结果与其他 execution result 使用同一公开坐标。

`dual_channel` 与 `content_only` 两个入口必须提供相同的模型内容：带标记的
终态 execution 在两个入口中都在 `In [execution]:` 后直接显示完整 combined
正文。两个入口继续只在 `structuredContent` 是否存在这一点上不同。

### 5.2 执行编号与输出作者身份

完整 combined 输出沿用 execution control plane 的作者规则。结果事件仍以该
execution 的 session-local 整数编号写成 `Out[n]`。完整输出标记不采用
kernel-local execution count，也不改变 reset 后的编号连续性。

## 6. 工具说明要求

`run_python` 的 MCP docstring 必须说明以下事实：

1. `# loommux: full_output` 是无值、单次生效的完整输出标记。
2. 它只绕过 300 行默认交付阈值。
3. execution 仍在运行时，响应仍返回 running，而不是不完整的正文。
4. 该标记属于 execution；后续 `wait_python` 在终态时继续完整交付。
5. 没有标记时，默认规则与读取、搜索工具的使用方式不变。

`wait_python` 的 docstring 必须说明：当所选 execution 带有完整输出标记且
达到终态时，本工具直接返回完整 combined 正文。

其他工具的 docstring 不需要重复介绍该标记，除非该信息改变该工具的输入解释
或返回行为。

## 7. 实现边界

本节面向实现者，不定义额外的用户操作。

1. `Execution` 需要保存一个布尔输出交付偏好，例如
   `full_output_requested`。它在接受 `run_python` 提交时确定，并随该记录
   存续到终态、等待、中断和 reset 后的读取。
2. `adapter.py` 在解析 timeout directive 时独立解析完整输出标记；二者不应
   共用一个有值参数解析器。
3. `Execution.snapshot()` 或等价的结果构造逻辑，在
   `full_output_requested` 为真且 execution 已终态时，不应用
   `DEFAULT_OUTPUT_LINE_LIMIT` 的正文省略分支。
4. 仍在运行的 execution 保持现有 running 省略分支。完整输出标记不能把
   running 记录伪装成终态正文。
5. `presentation.py` 继续以 combined 输出作为完整正文来源；不得重新按
   stdout、stderr、result 和 traceback 拼接另一份顺序。
6. `mcp_server_factory.py` 的共享 `run_python` 与 `wait_python` 描述更新后，
   两个入口的 tool description 与 input schema 必须保持一致。

## 8. 验收与测试

### 8.1 指令识别

1. 完整行 `# loommux: full_output` 启用完整输出请求。
2. 相同完整行重复出现仍启用请求，不产生歧义错误。
3. 带前导空格、尾随文本、额外空格或不同拼写的行不启用请求。
4. 标记可与一条有效 timeout directive 共存，两个规则独立生效。
5. 原始 freeform source 仍完整提交给 kernel，且 kernel 中不存在由标记引入的
   runtime 变量。

### 8.2 黑盒体验

1. 默认 execution 输出 301 行时，`run_python` 省略正文并提供该 execution。
2. 带完整输出标记的 execution 输出 301 行时，`run_python` 直接返回完整
   combined 正文。
3. 带标记的 stdout、stderr、display result 与 traceback 按 combined 的
   IOPub 到达顺序完整出现；显示结果使用 `Out[execution]`。
4. 带标记的长运行 cell 首先返回 running；随后 `wait_python` 返回完整
   combined 正文。
5. 带标记的 error execution 直接返回已收集的完整 combined 输出，其中包含
   traceback。
6. 带标记的运行中 execution 被 reset 后，`wait_python(execution=...)`
   返回 killed 状态及其已收集的完整 combined 输出。
7. 不带标记的 execution 保持 300 行默认规则。

### 8.3 MCP 边界

1. 两个 entrypoint 的 tool 名称、input schema 和描述保持一致。
2. 两个 entrypoint 对带标记 execution 返回相同的模型内容。
3. `content_only` 不产生 `structuredContent`；`dual_channel` 保持其既有
   structured result channel。
4. `run_python` description 包含 canonical 标记、300 行规则、running 行为和
   `wait_python` 延续行为；`wait_python` description 包含带标记终态的完整
   交付行为。

## 9. 文档边界

本文是完整输出标记的唯一当前规范。它与以下文档分别承担不同主题：

| 主题 | 当前规范 |
| --- | --- |
| execution 身份、生命周期、流、选择和 result channel | `coding-agent-control-plane-design.md` |
| timeout directive 的语法 | `ipython-mcp-freeform-run-python-design.md` |
| 完整输出标记的语法与交付行为 | 本文 |
| 已退休的 output handle 设计 | 标有 Historical Design Notice 的文档 |

任何工具描述、测试、示例和 monitor 观察面都不得把完整输出标记误写为全局
设置、Python runtime 参数、额外 MCP tool 参数，或第二种 execution 身份。
