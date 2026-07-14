# IPython MCP 多模态输出设计

## 1. 文档对象

本文定义 LoomMux 如何将一个 IPython execution 的有序可见输出转换为 MCP tool
result。对象包括文本流、富展示事件、展示 metadata、execution 终态观察面与 MCP
content blocks。

本文不定义 Python 作者如何书写图像细节字段。该公共书写表面由
[`IPython 图像展示细节契约`](../../../drafts/%E5%85%B3%E4%BA%8Eagent%20runtime/openai-python-sdk%E8%B0%83%E6%9F%A5/docs/ipython-display-image-detail-contract.md)
定义。本文负责读取和保留该契约产生的 display metadata。

本文补充 `coding-agent-control-plane-design.md` 的文本投影规约。stdout、stderr、
`text/plain` result 与 traceback 的既有文本语义保持成立；富图像内容形成与文本日志
并列的有序展示投影。

## 2. 问题边界

IPython 可为一次 execution 发出 `stream`、`execute_result`、`display_data`、
`error` 与 `status` IOPub event。一个 `display_data` event 可以同时携带
`text/plain` 和 `image/png`。仅保留 `text/plain` 会保留文本可读性，却丢失图像像素；
仅保留图像会丢失 IPython 的文本结果、stdout 与 traceback。

LoomMux 必须同时满足下列条件。

1. 既有五条文本流继续是可读取、可搜索、可引用的文本对象。
2. 可交付图像以 MCP `ImageContent` 返回，而不是 Base64 文本或 JSON 字符串。
3. 文本与图像保持 IOPub 到达顺序。
4. 每张图像保留所属 display event 的 `detail` metadata。
5. 图像采集、转换或交付失败具有可定位的工具结果表面。

本文不定义人类终端截图、浏览器渲染、音频、视频、富前端组件、图像文件存储服务或
第二套图像读取工具。

## 3. 输出对象

### 3.1 文本投影

下列来源继续进入既有文本流。

| IOPub 来源 | 文本投影 |
| --- | --- |
| `stream` stdout | `stdout` 与 `combined` |
| `stream` stderr | `stderr` 与 `combined` |
| `execute_result` 的 `text/plain` | `result` 与 `combined` |
| `display_data` 的 `text/plain` | `result` 与 `combined` |
| `error` traceback | `traceback` 与 `combined` |

文本投影继续经过 terminal text normalizer。富内容不得绕过文本规范化规则修改同一
execution 的文字日志。

### 3.2 展示事件

一个 execution 维护追加式展示序列（presentation sequence）。每个可见 IOPub event
按到达顺序向该序列追加文本元素或图像元素。`execute_result` 与
`display_data` 同时维护展示事件记录；展示事件按接受该 execution 的 IOPub 消息
顺序编号。每个展示事件包含：

| 字段 | 含义 |
| --- | --- |
| `ordinal` | execution 内从 1 开始的展示事件序号。 |
| `source` | `execute_result` 或 `display_data`。 |
| `text_plain` | 该 event 的 `text/plain`，缺失时为空。 |
| `images` | 该 event 中按 MIME 选择后的图像对象。 |
| `metadata` | 该 event 的 Jupyter display metadata。 |

`text_plain` 继续写入文本投影，并在展示序列中形成文本元素。`images` 不写入文本行
日志，并在同一展示事件的位置形成图像元素。一个展示事件同时含 `text/plain` 与图像
时，文本元素先于该展示事件的图像元素。不同 IOPub event 之间的元素顺序严格等于
event 到达顺序。

展示事件属于 execution record，不属于 kernel namespace、IPython output cache 或
用户 home profile。reset 保留既有 execution record 时，也保留其已捕获展示事件。

### 3.3 可交付图像

可交付图像的 MIME 类型为：

```text
image/png
image/jpeg
image/webp
image/gif
```

`image/gif` 仅接受单帧 GIF。动画 GIF 是不支持图像类型。每个可交付图像保留 Jupyter
payload 中的 Base64 数据与选定 MIME 类型，不将其解码、重编码或写入 workspace。

一个 display event 同时含有多个可交付 MIME 表示时，LoomMux 选择一个表示。选择
顺序为 PNG、JPEG、WEBP、GIF。选择规则保证一个展示对象只生成一个 MCP image
block，避免同一视觉对象以多种编码重复占用模型输入。

## 4. 展示 metadata

IPython `display()` 的 `metadata` 参数属于 display event。LoomMux 从 event metadata
的顶层 `detail` 字段读取视觉细节声明。

| metadata 状态 | 解释 |
| --- | --- |
| 缺少 `detail` | 未声明；下游交付使用公共契约规定的 `high`。 |
| `detail="low"` | 此图像以低视觉细节交付。 |
| `detail="high"` | 此图像以高视觉细节交付。 |
| `detail="original"` | 此图像以原始视觉细节交付。 |

`detail` 属于展示事件，不写回 Python 图像对象。一个对象被多次 `display()` 时，每个
event 独立读取自己的 metadata。

缺少 `detail` 不是错误。存在但不是 `low`、`high` 或 `original` 的值是错误。错误
记录必须包含 execution、展示事件序号、收到的值与允许值。它不得被静默改写为其他
detail 值。

## 5. MCP 结果构造

### 5.1 内容顺序

`run_python` 或 `wait_python` 返回已终态 execution 时，`ToolResult.content` 逐项
迭代展示序列：

1. 已规范化 stdout、stderr、`text/plain` 与 traceback 文本元素形成
   `TextContent`。
2. 每个可交付图像元素形成 `ImageContent`。
3. 后续元素继续追加，不得跨越前面的文本或图像元素。

`combined` log 是同一文本 element 的独立文本投影。它用于日志读取和搜索，不决定
`ToolResult.content` 中 text 与 image 的排列。

文本投影中的 `Out[<execution>]:` 前缀继续只用于 `text/plain` 的 combined 表面。
图像不伪装为 `Out[...]` 文本，也不向文本日志写入 Base64。

### 5.2 图像内容块

每张可交付图像形成一个 MCP `ImageContent`：

```json
{
  "type": "image",
  "data": "<base64 image data>",
  "mimeType": "image/png",
  "_meta": {
    "detail": "low",
    "execution": 12,
    "display_ordinal": 2
  }
}
```

`detail` 是图像交付解释字段。`execution` 与 `display_ordinal` 是诊断坐标，用于
错误定位与测试；它们不替代图像在 `content` 序列中的位置。

`structured_content` 保留 execution 状态、行数、输出省略原因与错误摘要。图像数据
必须位于 `content`，因为 MCP consumer 需要以图片而非 JSON 字段观察它。

### 5.3 终态与文本省略

running execution 不返回部分图像内容。`run_python` 在等待上限内未达到终态时，保持
既有 running 表面；`wait_python` 在 execution 终态后返回可交付展示内容。

文本行数超过文本输出阈值时，文本正文继续遵守既有省略规则。该结果是展示序列的有损
文本投影，必须携带文本省略原因。已捕获图像不因文本行数超过阈值而
自动丢失，并按其在未省略 sequence 中的相对次序交付。图像是否交付只由图像 MIME、
图像大小、图像数量和请求大小限制决定。

`read_python_output` 与 `search_python_output` 保持文本工具身份，只读取和搜索文本
流。它们不重新编码或重传图像。需要再次向模型展示一个已有 Python 对象时，代码作者
使用新的 `display()` 调用。

## 6. 资源与失败表面

每个图像在转换前检查 MIME、Base64 形状、解码后的字节数、execution 内图像数量与
本次 tool result 的总图像字节数。具体上限由运行配置提供；上限本身不改变本文的
展示顺序语义。

| 条件 | 结果表面 |
| --- | --- |
| 不支持 MIME | 指出 execution、display ordinal、MIME。 |
| 缺少或无效 Base64 | 指出 execution、display ordinal 与数据错误。 |
| 无效 `detail` | 指出 execution、display ordinal、收到值与允许值。 |
| 单图、数量或总大小超限 | 指出被拒绝图像和适用限制。 |
| 图像转换异常 | 指出来源展示事件与异常类别。 |

一张图像失败不改变其他已成功文本或图像的相对顺序。工具结果必须保留已经可交付的
内容，并为失败的展示位置追加可读错误文本；它不得以无标记省略代替错误。

## 7. Tool Docstring

`run_python` 的 MCP docstring 必须说明图像展示的直接使用规则：IPython
`display()` 产生的可交付图像直接进入模型内容；普通 `display(image)` 使用 `high`
视觉细节；单张图像可通过 `metadata={"detail": "low"}` 或
`metadata={"detail": "original"}` 声明不同细节。

docstring 必须说明 `detail` 只作用于对应的 `display()` 调用。它不得要求调用者
了解 MCP image block、Base64、data URL、运行时服务名或下游模型 API。

## 8. 实现责任

| 模块 | 责任 |
| --- | --- |
| `kernel_session.py` | 从 IOPub `execute_result` 与 `display_data` 采集 data bundle 和 metadata。 |
| `execution.py` | 保存有序展示事件、图像诊断坐标和终态可见投影。 |
| `adapter.py` | 将终态 execution 投影为 text/image MCP 内容。 |
| `mcp_result_policy.py` | 构造同时含 `TextContent` 与 `ImageContent` 的 `ToolResult`。 |
| `mcp_server_factory.py` | 保持八个 tool 的既有职责并公开富结果。 |
| `tests/` | 验证真实 kernel、MCP client 与结果顺序。 |

## 9. 验收条件

实现完成时，以下外部观察同时成立。

1. `display(PIL.Image)` 返回一个 MCP image block，而不仅是 Python repr 文本。
2. matplotlib 图、PIL 图和支持的图像 MIME 保留正确 MIME 与 Base64 数据。
3. stdout、图像、文本结果、图像的到达顺序在 MCP content 中保持一致。
4. `display(image)` 的 image metadata 显示 `detail="high"` 的下游解释。
5. 两次独立 display 可以分别产生 `low` 与 `original` 图像。
6. 一个 display 调用内的多个对象共享该调用的 detail metadata。
7. 文本超过文本行数阈值时，终态结果仍可交付符合图像资源限制的图像。
8. running execution 不交付部分图像；终态 `wait_python` 可以交付图像。
9. 无效 detail、无效图像数据和超限图像产生可定位错误，且不丢失其他内容。
10. 既有 stdout、stderr、result、traceback、combined、read 与 search 的文本行为保持成立。
