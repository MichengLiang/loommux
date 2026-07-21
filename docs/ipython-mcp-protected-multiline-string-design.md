# IPython MCP 受保护多行原始字符串设计

## 1. 文档职责

本文定义 loommux `run_python` 的受保护多行原始字符串。它规定作者在
freeform cell 中如何书写长文本、loommux 如何在提交 IPython kernel 前转换
该文本、timeout 与完整输出指令如何划分识别区域，以及实现与验收必须满足的
可观察行为。

execution 编号、持久 kernel 生命周期、输出流、等待、中断、重置和 MCP result
channel 由 [Coding Agent Control Plane Design](coding-agent-control-plane-design.md)
定义。timeout 指令的数值语法由
[run_python Freeform Input Contract](ipython-mcp-freeform-run-python-design.md)
定义。完整输出的交付行为由
[IPython MCP Complete Output Directive Design](ipython-mcp-full-output-directive-design.md)
定义。

## 2. 目标体验

`run_python` 的作者需要在 Python cell 中传递补丁、Markdown、Python 源码、
JSON、提示词或其他长文本。这些文本可以自身包含三双引号、反斜杠、花括号和
注释行。受保护多行原始字符串使作者能够把这类材料写在外层三双引号中，并让
loommux 生成等值的普通 Python `str` 表达式后再提交 kernel。

作者书写：

````python
payload = r"""
*** Begin Patch
*** Update File: example.py
@@
a = r"""
你好呀
"""
*** End Patch
"""
````

Python 执行后，`payload` 的值保留外层字符串内部的全部原始文本：

````text

*** Begin Patch
*** Update File: example.py
@@
a = r"""
你好呀
"""
*** End Patch
````

最前面的空行来自 opening triple quote 与 Begin 行之间的原始换行。Begin 行、
End 行、后缀、内部三双引号和所有正文字符都属于字符串值。

## 3. 作者表面

一个受保护字符串由外层三双引号和位于物理行首的 Begin/End 行组成：

````text
<Python expression> <optional r/f prefix> """
*** Begin<any suffix>
<zero or more source lines>
*** End<any suffix>
"""
````

Begin 行与 End 行的坐标规则使用 `freeform` 原始文本的物理行和 0-based 列。
它们的第一个字符必须是 `*`：

```text
BeginLine := "*** Begin" { any character except line ending }
EndLine   := "*** End"   { any character except line ending }
```

`*** Begin Patch`、`*** Begin Splice`、`*** End Patch` 和 `*** End Splice`
都是有效标记行。`Begin` 或 `End` 后的全部字符保留为字符串内容；后缀不改变
Begin/End 的识别身份。Begin 后遇到的第一个 EndLine 结束当前保护字符串。

下列书写具有不同的物理列，因此只有第一行满足 BeginLine：

```text
*** Begin Patch
    *** Begin Patch
text *** Begin Patch
```

外层开头可以没有前缀，也可以带有合法的 `r`、`R`、`f`、`F` 或其 `r`/`f`
组合前缀。前缀只属于作者的外层书写表面；受保护字符串的结果一律是普通
Python `str`。

完整候选字符串使用以下物理行顺序：outer triple quote 后换行，下一行是
BeginLine；第一个 EndLine 后换行，下一行由 matching closing triple quote 组成。
只有满足这一完整顺序的区域参与转换。正文中的三双引号由保护区域承载，继续作为
原始文本保留。

## 4. 值语义

完整受保护字符串的值等于原始 source 中 opening triple quote 与 matching
closing triple quote 之间的字符序列。该序列包含 opening line 之后的原始行
分隔、Begin 行、正文、End 行及 End 行之后到 closing triple quote 之前的原始
行分隔。

因此，转换保持下列事实：

1. Begin/End 标记行是值的一部分。
2. 每一行的后缀、前导字符、反斜杠、引号、花括号和 Unicode 字符保持原样。
3. 文本内部的 `r"""`、`f"""`、Markdown 围栏和 Python 注释只作为文本。
4. 外层 `r` 与 `f` 前缀不改变结果值；保护区不执行 raw-string 或 f-string
   解释。

例如：

````python
name = "Ada"
payload = f"""
*** Begin
Hello, {name}
C:\new\temp
*** End
"""
````

`payload` 中保留字面文本 `{name}` 与 `C:\new\temp`。它们不触发插值或转义。

## 5. Cell 转换决议

loommux 在每次 `run_python` 调用中完整扫描 `freeform`，再决定本次提交使用的
kernel source。

| 扫描结果 | 提交给 kernel 的 source | execution 行为 |
| --- | --- | --- |
| 没有完整受保护字符串 | 原始 `freeform` | 按既有 `run_python` 流程提交 |
| 一个或多个完整受保护字符串 | 等值转换后的 Python source | 按既有 `run_python` 流程提交 |
| 存在未闭合的候选保护字符串 | 原始 `freeform` | 按既有 `run_python` 流程提交 |

完整保护字符串出现时，转换器将整个外层字面量替换为普通 Python `str` 表达式。
生成表达式的值必须与第 4 节定义的原始字符序列相等。转换器可以使用 Python
标准字面量编码，但不得对字符串内容进行语义解释。

同一 cell 中的完整保护字符串按 source 出现顺序转换。一次 execution 对应一份
确定的 kernel source；该 source 是 adapter 在创建 execution 前准备好的提交
材料。

转换是确定性的 source 操作：相同的 `author_source` 必须产生相同的
`submitted_source`、保护字符串数量和位置映射。转换过程不读取 IPython
namespace、execution 编号、当前时间、工作目录或输出状态；它只依据本次
`freeform` 的字符和本文定义的行级规则形成结果。

## 6. Loommux 指令区域

`# loommux: timeout_seconds=...` 和 `# loommux: full_output` 是 cell 控制区的
指令。完整受保护字符串的 source 范围属于字符串值区。

处理顺序为：

```text
author source
-> 识别完整受保护字符串范围
-> 在字符串值区之外识别 timeout 与 full_output 指令
-> 生成 submitted source
-> 创建 execution 并提交 kernel
```

因此，下面两行进入 `payload` 的字符串值，不参与本次 execution 的控制决议：

````python
payload = """
*** Begin
# loommux: timeout_seconds=120
# loommux: full_output
*** End
"""
````

位于完整保护字符串之外的有效指令继续遵循既有契约：唯一有效 timeout directive
确定本次 `run_python` 的等待时长；full-output directive 确定该 execution 的
终态 combined 输出交付偏好。

## 7. 实现责任

转换属于 adapter 接收 `freeform` 与创建 `Execution` 之间的输入准备阶段。
`KernelSession` 继续只接收已准备的 Python source，并通过 Jupyter
`client.execute()` 提交给持久 IPython kernel。

实现必须保留以下三类 execution 输入事实：

| 记录 | 含义 |
| --- | --- |
| `author_source` | MCP 调用收到的原始 `freeform` cell。 |
| `submitted_source` | 本次实际提交 IPython kernel 的 Python source。 |
| `protection_transform` | 转换是否应用、完整保护字符串数量及作者 source 到提交 source 的位置映射。 |

`execution_submitted` 监控事件以 `author_source` 作为作者书写记录，并携带足以
诊断实际 kernel 提交内容的转换信息。source 位置映射必须让保护字符串之后的
Python 诊断位置能够对应回作者 cell 的物理行。

转换报告是 execution 输入准备的确定记录。它至少包含下列字段：

| 字段 | 语义 |
| --- | --- |
| `applied` | 本次 cell 是否使用保护字符串转换。 |
| `literal_count` | 已转换完整保护字符串的数量。 |
| `author_ranges` | 每个完整保护字符串在 author source 中的起止行、列范围。 |
| `submitted_ranges` | 对应普通 Python `str` 表达式在 submitted source 中的起止范围。 |
| `line_map` | author source 与 submitted source 的行坐标对应。 |

`author_ranges` 的起点覆盖 outer triple quote，终点覆盖 matching closing triple
quote；`submitted_ranges` 覆盖替换该区域的完整 Python 表达式。执行输出和
traceback 仍由现有 IOPub 收集路径处理，adapter 在呈现与诊断层使用 `line_map`
恢复作者 cell 坐标。转换报告不进入 Python namespace，不产生运行时变量，也不
改变同一 server process 中后续 cell 的 namespace。

涉及的代码职责如下：

| 位置 | 职责 |
| --- | --- |
| `src/loommux/adapter.py` | 预处理 cell、划分指令识别区域、创建 execution。 |
| source-transform 模块 | 扫描保护字符串、生成 submitted source、返回转换报告和位置映射。 |
| `src/loommux/execution.py` | 保存作者 source、提交 source 与转换元数据。 |
| monitor 发布路径 | 发布作者 source 与转换事实。 |
| `src/loommux/mcp_server_factory.py` | 说明作者可见的受保护字符串写法。 |

## 8. 验收与测试

### 8.1 转换单元测试

1. Begin/End 行只在原始物理行第 0 列识别。
2. Begin/End 后缀逐字符保留在结果字符串中。
3. 结果字符串保留 opening/closing triple quote 之间的原始换行。
4. 内部三双引号、单引号、反斜杠、Markdown 围栏、Unicode、花括号和
   `# loommux:` 文本逐字符保留。
5. 无前缀、`r` 前缀和 `f` 前缀的完整保护字符串均产生普通 `str` 原始文本值。
6. 多个完整保护字符串按 source 顺序转换。
7. 未闭合候选保护字符串返回原始 `freeform` 作为 submitted source。
8. 转换报告包含 author source、submitted source、完整保护字符串数量和位置
   映射。

### 8.2 Adapter 与真实 kernel 测试

1. 赋值后的 Python 变量包含 Begin、End、后缀和内部三双引号。
2. 受保护字符串作为函数实参时，函数收到完整原始文本。
3. 保护字符串内的 `{name}` 保持字面文本，不产生 f-string 插值。
4. 保护字符串内的 timeout/full-output 文本不改变 execution 的等待和交付行为。
5. 保护字符串外的有效 timeout/full-output 指令维持当前行为。
6. 先前 cell 写入的变量可被后续受保护字符串 cell 使用，确认 persistent
   IPython namespace 保持既有语义。
7. timeout 后的 execution 继续可由 `wait_python`、状态工具和输出工具观察。
8. 保护字符串后的 Python 异常位置可以映射回 author source 的正确物理行。
9. 普通 freeform cell 保持现有提交、输出和 execution 生命周期行为。

### 8.3 MCP 边界测试

1. 两个 server entrypoint 对同一 protected multiline source 产生相同的
   execution 结果。
2. `run_python` tool description 包含 canonical 作者表面、原始文本值语义和
   指令区域规则。
3. `content_only` 与 `dual_channel` 保持既有 result-channel 契约。
4. execution 监控记录可区分 author source、submitted source 和转换事实。

## 9. 文档关系

| 主题 | 当前规范 |
| --- | --- |
| 受保护多行原始字符串 | 本文 |
| freeform 输入与 timeout 数值语法 | [run_python Freeform Input Contract](ipython-mcp-freeform-run-python-design.md) |
| 完整输出指令与交付行为 | [IPython MCP Complete Output Directive Design](ipython-mcp-full-output-directive-design.md) |
| execution 生命周期、控制工具和输出流 | [Coding Agent Control Plane Design](coding-agent-control-plane-design.md) |
| 作者概览 | [README](../README.md) |
