• 关键点是：content 模式并不是“整个结果是一段非结构化字符串”。它仍然使用 MCP 定义的、有序的类型化内容块数组。所谓“不结构化”，只是没
  有额外的 structured_content JSON 对象。

  例如图文混排在协议层大致是：

  {
    "content": [
      {
        "type": "text",
        "text": "In [2]:\n"
      },
      {
        "type": "text",
        "text": "第一张图：\n"
      },
      {
        "type": "image",
        "data": "<Base64 PNG>",
        "mimeType": "image/png"
      },
      {
        "type": "text",
        "text": "两张图片之间的文字\n"
      },
      {
        "type": "image",
        "data": "<Base64 PNG>",
        "mimeType": "image/png"
      }
    ]
  }

  客户端按数组顺序渲染，所以自然得到：

  In [2]:
  第一张图：
  [图片]
  两张图片之间的文字
  [图片]

  Loommux 正是在 projects/loommux/src/loommux/mcp_result_policy.py:31 中构造 ToolResult：

  if result_mode == "structured":
      return ToolResult(content=content, structured_content=status)

  if result_mode == "content":
      return ToolResult(content=content)

  两种模式拥有完全相同的 content。区别只是：

  - content 模式：只有面向模型和界面展示的有序内容块。
  - structured 模式：除了相同的内容块，还附带可供程序读取的 structured_content，例如执行编号、状态、输出行数等。

  富媒体内容由 projects/loommux/src/loommux/mcp_result_policy.py:48 转换：

  - PresentationText → TextContent
  - PresentationImage → ImageContent
  - 图片传输失败 → 占据原位置的 TextContent 诊断信息

  因此即使某张图片无法交付，前后的顺序也不会坍缩：

  文字 A
  图片交付失败的诊断
  文字 B

  还有一个容易混淆的地方：structured_content 才是普通意义上的 JSON 业务结果；content 本身也有协议结构，只是它的结构是“多模态展示
  流”，而不是 Loommux 的执行状态对象。图片 Base64 只放在 content 中，不会再复制进 structured_content。