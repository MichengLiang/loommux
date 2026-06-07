# loommux MCP Python 工具实时审计面：完整设计汇总

下面是我把前两轮观察、你的补充、项目现状和技术选型统一整理后的完整设计汇总。这份内容的目标不是再发散讨论，而是把我们已经对齐的方案变成一个清楚、可审查、可落地的设计对象。你可以把它理解为正式 spec 前的总览版：它说明这个东西为什么存在、负责什么、不负责什么、怎么接入 loommux、前后端怎么组织、事件怎么流动、浏览器怎么展示、失败时怎么处理、后续怎么实现和验证。

## 一、设计思想

这个功能的核心思想是：给 loommux 当前的 IPython MCP 工具系统增加一个旁路的、临时的、实时的浏览器审计投影。它不是替代 MCP 工具，不是替代 IPython kernel，不是替代 agent 对话，也不是一个长期日志平台。它只解决一个非常明确的现实摩擦：当 Code Agent 使用这套 Python MCP 工具时，你作为人类操作者缺少一个直接观察面，无法在浏览器里清楚看到 agent 实际调用了哪些工具、传入了什么 Python 代码、Python 输出了什么、工具返回给 agent 的结果摘要是什么。

当前 loommux 已经有非常清楚的内部对象：`IPythonMCPAdapter` 管理 workspace、kernel、execution；`Execution` 保存 code、状态、stdout、stderr、result、error、时间戳；`ExecutionLogs` 保存 combined/stdout/stderr/result/traceback 五类内存日志；`presentation.py` 负责把工具结果变成模型可读的 pretty text。也就是说，执行事实已经存在，只是没有投影给浏览器。新功能不应该重新发明执行系统，而应该把这些已经形成的 execution 和 tool call 事实，通过事件的方式旁路投影出来。

这个设计坚持一个边界：审计面是观察者，不是控制者。第一版不让浏览器发 Python，不让浏览器 interrupt/reset，不让浏览器调用 MCP。浏览器只看发生过和正在发生的事情。这样可以避免把“观察需求”膨胀成“第二个 MCP 客户端”或“Notebook UI”。你现在要的是看清楚 agent 做了什么，不是用浏览器替 agent 做事。

另一个基本思想是：monitor 不应该成为 MCP 工具成功运行的前提。你明确说“我想看就看，不想看就不看”。所以 monitor 后端没有启动时，MCP 工具仍然必须正常工作。事件发送失败不能影响 `run_python`、`wait_python` 等工具的返回，不能改变现有 pretty text、structured content、output_log handle、等待语义和错误语义。monitor 是旁路观察面，不是主路径依赖。

## 二、设计对象与正面定义

这个人工制品可以命名为 `loommux monitor` 或 `loommux MCP monitor`。它是一个本地运行的实时审计工具，由两部分组成：

1. MCP 侧事件发布器：嵌入 loommux Python 进程，观察 MCP tool call 与 Python execution lifecycle，形成结构化事件，并尝试发送给本地 monitor 后端。

2. 本地 monitor web 子项目：放在 `projects/loommux/monitor/`，包含 Hono 后端和 React 19 前端。Hono 后端接收 MCP 侧事件，维护内存 ring buffer，并通过 SSE 推送给浏览器；React 前端连接 SSE，渲染工具调用时间线、execution 列表、代码和输出详情。

它作为人工制品成立的构成性条件是：

第一，它必须能记录 Python MCP 工具调用。只显示 kernel health 或后端状态不够。它必须展示 agent 调用了什么工具、传了什么参数、何时开始、何时结束、返回状态是什么。

第二，它必须能记录 Python execution 的核心事实。`run_python` 的 `freeform` code 必须可见；stdout、stderr、IPython result、traceback 必须可见；completed、running、error、interrupted、killed 等状态必须可见。

第三，它必须是近实时的。后端收到事件后应立即广播给浏览器。浏览器断开后重连，重连后继续接收后续事件。

第四，它必须是临时内存型。可以有内存 ring buffer 和 volatile outbox，但不写数据库，不承诺长期历史，不要求用户以后回来查旧记录。

第五，它必须不影响现有 MCP 行为。monitor 后端未启动、网络失败、浏览器未打开，都不能让 MCP 工具调用失败或显著变慢。

第六，它必须在当前 `loommux` 项目下独立放置前端/monitor 文件夹，而不是把整个项目改造成 TS monorepo，也不是把前端塞进 Python 包根目录造成混乱。

## 三、范围

第一版范围按我们已经确认的临界值收束，不继续膨胀。

需要覆盖的 MCP 工具调用包括当前 loommux 暴露的 Python 工具：`set_workspace`、`run_python`、`python_status`、`python_execution_status`、`read_python_output`、`search_python_output`、`wait_python`、`interrupt_python`、`reset_python`。这些工具的调用都会进入审计时间线，记录工具名、输入参数摘要、返回摘要、开始时间、结束时间、耗时、ok/status。

其中 `run_python` 和 execution lifecycle 是主视图重点。因为你的核心需求是“我想看到执行了什么 Python 代码，返回了什么东西”。所以 UI 应围绕 execution 聚合：一次 `run_python` 产生一个 execution，页面展示 code、stdout、stderr、result、traceback、状态、输出行数、output_log handle 和返回摘要。

`wait_python`、`read_python_output`、`search_python_output` 等工具也会展示，但它们更多出现在 tool timeline 中，用于审计 agent 的行为路径。例如你能看到 agent 先 `run_python`，得到 running 后又 `wait_python`，或者输出过大时调用 `read_python_output` 读取尾部，或者调用 `search_python_output` 搜索错误。这能帮助你判断 agent 是否正确使用了 loommux 的日志工具。

第一版不做浏览器主动控制。不提供“运行代码”输入框，不提供 interrupt/reset 按钮，不提供 workspace 设置表单。虽然这些能力以后可以做，但它们会把观察面变成控制面，增加权限、安全和语义复杂度，不属于当前已经成立的需求。

第一版不做磁盘持久化。不接 SQLite，不写 JSONL，不写日志文件。所有事件只保存在 MCP 进程内的 volatile outbox 和 monitor 后端的内存 ring buffer 中。进程退出后记录消失。

第一版不做远程认证和多用户系统。默认只监听 `127.0.0.1`。因为事件中可能包含 Python 源码、文件路径、环境信息、输出数据、异常信息，默认开放到局域网或公网都不合适。

## 四、为什么采用这个方案

我们考虑过几种架构。把 web 后端嵌进 MCP server 进程最直接，但会把浏览器服务、SSE 长连接、静态资源服务和 MCP 执行主路径绑在一起，不符合你“单独启动一个网络服务器后端”的使用方式，也会扩大 MCP server 的职责。让 web 后端反向作为 MCP client 去轮询状态也不合适，因为当前 loommux 的 MCP server 主要是 stdio/工具调用语义，反向抓取会绕、慢、易丢事件，也很难完整看到每次工具输入。

最终选择的是：MCP 侧主动事件上报 + 独立 monitor 后端 + 浏览器 SSE。这正好符合你的表达：MCP 的客户端/工具侧持续尝试把事件发给后端；后端如果没启动，事件发送失败但不影响 MCP；你想看时启动后端，后端接收后续事件并推给浏览器；浏览器只负责展示。

这个方案的结构优势是：

第一，职责清楚。MCP 侧负责产生执行事实和上报事件；monitor 后端负责接收、缓存、广播；浏览器负责展示和交互。三个职责没有互相冒充。

第二，失败隔离。monitor 后端没启动时，只影响观察面，不影响 Python 执行。浏览器没打开时，只影响你看不到，不影响后端接收。后端重启时，只丢内存事件，不影响 MCP。

第三，符合临时审计工具的形态。它不需要数据库，不需要复杂部署，不需要用户账户，不需要远程协议治理。它就是一个本地开发工具，需要时开，不需要时关。

第四，能保留未来行动权。如果以后你要持久化，可以在后端 ring buffer 后面加 JSONL 或 SQLite；如果以后要浏览器控制，可以新增控制 API；如果以后要多个 MCP server，可以给事件加 source/session id。当前设计不提前承诺这些能力，但事件边界足够清楚，未来可以扩。

## 五、技术选型

前端和 monitor 后端参考 `micheng-ts` 的技术偏好，但不把 loommux 改造成 `micheng-ts` monorepo。

当前 npm registry 实查版本是：React `19.2.7`，React DOM `19.2.7`，Vite `8.0.16`，TypeScript `6.0.3`。`micheng-ts` 当前 catalog 是同一代技术栈，React `^19.2.5`、Vite `^8.0.10`、TypeScript `^6.0.3`。因为你明确说要最新 React 19 和最新前端技术栈，所以实现时我建议采用 registry 当前 patch，同时保留 `micheng-ts` 的工程风格。

建议使用：pnpm、React 19、React DOM 19、Vite 8、TypeScript 6 strict、Tailwind CSS v4、Hono、`@hono/node-server`、Biome、Vitest、Playwright、lucide-react。Radix/shadcn 风格可用于 tabs、switch、tooltip、button 等基础交互，但由于 loommux 不是 `micheng-ts` workspace，不直接依赖 `@micheng-ts/ui`。如果需要 UI 组件，优先在 monitor 内做少量本地组件，保持轻量。

TanStack Query 和 TanStack Router不是硬需求。SSE 是持续事件流，不是典型 request/cache server state；一个页面也不需要复杂路由。为了避免技术堆叠，第一版可以不引 TanStack Query/Router。这样仍然符合 `micheng-ts` 的工程思想：技术服务对象，而不是为了凑栈引入不必要层。

Python 侧继续使用当前 `uv` 管理环境。事件发布如果需要 HTTP client，优先使用 `httpx`，因为 workbench 根依赖偏好已经明确网络请求默认用 `httpx`，且可以通过 `uv add httpx` 添加到 loommux 项目依赖中。如果为了最小依赖可以用标准库 `urllib`，但这不符合你的环境偏好；我倾向于加 `httpx`，并把发布放进后台线程，短超时失败。

## 六、目录安排

建议在 `projects/loommux` 下新增：

```text
monitor/
  package.json
  pnpm-lock.yaml
  index.html
  vite.config.ts
  tsconfig.json
  tsdown.server.config.ts
  playwright.config.ts
  biome.json 或继承/本地配置
  src/
    client/
      App.tsx
      main.tsx
      styles.css
      events.ts
      state.ts
      components/
    server/
      app.ts
      index.ts
      events.ts
    shared/
      schema.ts
      types.ts
    test/
      setup.ts
```

也可以把 `src/client` 简化成 `src/App.tsx` 这种模板形态；正式实现时会按复杂度决定。因为这个 monitor 既有 Hono server 又有 React client，结构上参考 `micheng-ts/templates/fullstack-hono-react` 更合适。

Python 侧建议新增：

```text
src/loommux/monitoring.py
```

或者拆成：

```text
src/loommux/monitor_events.py
src/loommux/monitor_publisher.py
```

如果事件 schema 和发布器逻辑不大，单文件 `monitoring.py` 足够。它应定义事件类型、publisher 协议、no-op publisher、HTTP publisher、后台队列/outbox、配置读取。核心 adapter 和 kernel session 只调用抽象接口，不直接知道 Hono 或浏览器。

测试可新增：

```text
tests/test_monitoring.py
tests/test_ipython_mcp_monitor_events.py
monitor/src/server/app.test.ts
monitor/src/client/App.test.tsx
monitor/e2e/monitor.spec.ts
```

## 七、连接和重试语义

浏览器到后端：使用 SSE。前端通过 `EventSource('/api/events/stream')` 建立连接。浏览器断线后 EventSource 自动重连。后端每隔一段时间发送 heartbeat，避免连接被中间层悄悄断掉。前端状态栏展示 `connecting`、`open`、`closed/retrying`。

MCP 到后端：使用 HTTP POST event ingest。MCP 侧 publisher 读取配置中的 monitor endpoint，例如默认或配置为：

```text
http://127.0.0.1:9765/api/events
```

这里需要一个实现决策：默认是否启用发送。你希望“我随时打开随时能连上”，所以我倾向于默认启用本地 endpoint 探测，但必须把失败成本压得很低。也就是说，MCP 侧默认尝试向 `127.0.0.1:9765` 上报，后端没开则快速失败并退避，不在每次工具调用里同步等待。也可以提供环境变量关闭：`LOOMMUX_MONITOR_DISABLED=1`。端口和 URL 可以用 `LOOMMUX_MONITOR_URL` 覆盖。

后台 publisher 的行为是：工具线程只入队，不做慢网络；后台线程批量或逐条发送；发送失败时记录失败状态并进入退避；退避期间仍可接收新事件到有界 outbox；后端恢复后继续发送后续事件，并尽力 flush 最近仍保留的事件。

outbox 必须有上限。例如最多 1000 个事件或最多若干 MB 文本。超限时丢弃最旧事件，并发一个本地/后端可见的 `events_dropped` 事件或统计字段。这样大输出不会撑爆内存。

这个语义满足“我不想看就不开；我想看就开；打开后它能接上”。它不承诺无限补历史，但会尽力保留短期内存事件，改善临时打开时的体验。

## 八、事件模型

事件分两层：tool call 层和 execution 层。

Tool call 层记录 MCP 工具调用事实。每个工具调用有 `call_id`，事件包括：

`tool_call_started`：工具开始，记录 tool name、arguments、timestamp。

`tool_call_finished`：工具结束，记录 duration、ok、status、result summary、pretty text 摘要。

`tool_call_failed`：如果工具 wrapper 层发生异常，记录异常摘要。正常 Python 异常不算 wrapper failed，而是 `run_python` 返回 `status=error`。

Execution 层记录 Python execution 生命周期。事件包括：

`execution_submitted`：`run_python` 创建 execution 后，记录 execution_id、call_id、workspace、kernel_pid、code、timeout_seconds、submitted_at。

`execution_output`：stdout/stderr/result/traceback 增量。记录 execution_id、stream、text、execution_count、timestamp。

`execution_finished`：execution 完成、错误、中断或被 kill。记录 execution_id、status、completed_at、output_log、output_total_lines、error summary。

`workspace_status` 或 `workspace_set`：workspace 设置结果，作为辅助事件。

事件字段需要做体量控制。`freeform` code 应完整保留，除非极端大，此时可设置硬上限并标记 truncated。stdout/stderr/result/traceback 增量按 chunk 发送，单个事件过大时裁切或分块。tool result 的 `pretty_text` 可以保留摘要，避免把超大输出重复一遍。真实完整输出仍以 `execution_output` 事件流和 existing output_log 概念表达。

## 九、前端状态聚合

前端不直接把事件一条条扔到屏幕上就结束，而是要把事件聚合成两个视角。

第一个视角是 execution 聚合。前端维护 `executionsById`。收到 `execution_submitted` 时创建 execution card，保存 code 和基础信息；收到 `execution_output` 时按 stream 追加文本；收到 `execution_finished` 时更新状态、耗时、错误和 output_log。这个视角用于回答“这次 Python 执行到底干了什么”。

第二个视角是 tool timeline。前端维护 `toolCallsById` 和 ordered timeline。收到 `tool_call_started` 创建调用项；收到 `tool_call_finished` 补齐结果、耗时和状态。这个视角用于回答“agent 使用工具的路径是什么”。

页面可以把 execution 作为主列表，把 tool timeline 放在下方或侧边。点击 execution 时，详情区显示：代码、combined output、stdout、stderr、result、traceback tabs、状态、输出行数、错误摘要、output_log、相关 tool call。点击 tool call 时，详情区显示 arguments/result summary/pretty text。

## 十、前端交互与视觉

第一屏就是工具本体，不做 landing page。顶部状态栏紧凑显示：SSE 连接状态、monitor 后端 health、最近事件时间、事件总数、dropped 计数、当前 workspace、清空按钮、自动滚动开关、暂停渲染开关。

主区域建议桌面两栏：左侧 execution 列表，右侧详情。列表项显示 execution id、状态、开始时间、耗时、代码首行、输出行数、错误名。状态用清楚的 badge：running、completed、error、interrupted、killed。右侧详情顶部显示 code，下面显示 output tabs，再下面显示 tool result summary 或 timeline 关联。

移动端用上下布局：状态栏、execution 列表、详情。所有固定格式元素要有稳定尺寸，长 code 和长 output 在自己的滚动容器里滚动，不能把页面撑坏。日志使用 monospace，行高和间距要适合扫描。

功能按钮应使用图标加 tooltip 或简短文本：复制 code、复制 output、清空视图、暂停自动滚动、恢复 live、筛选状态、筛选工具。不要写大段“如何使用”的说明，不做宣传型 hero，不做装饰性背景。

颜色上采用中性工作台风格。不同 stream 可以用克制颜色区分：stdout 中性，stderr/traceback 用危险色，result 用强调色，但不要让整页变成单一紫蓝或深色主题。暗色模式可以有，但不是第一优先；如果实现简单，可以按系统主题。

## 十一、后端 API

Hono 后端建议提供：

`GET /api/health`：返回 `{ ok, uptime_ms, started_at, clients, events_buffered, events_received, events_dropped }`。

`POST /api/events`：接收 MCP 侧事件。校验 type、timestamp、payload 基本结构。成功返回 `{ ok: true, sequence }`。非法事件返回 400。该 endpoint 默认只接受本机访问。

`GET /api/events/stream`：SSE endpoint。连接建立时发送当前 snapshot，包括 health 和最近 ring buffer 事件。之后每个新事件都按 SSE event 推送。定期 heartbeat。

`GET /` 和静态资源：生产预览时服务前端 build。

后端内存 ring buffer 有上限。例如最多 1000 或 5000 个事件，后续可配置。超限丢旧事件并增加 dropped 计数。后端不写盘，不做数据库。

## 十二、Python 接入点

`mcp_ipython_server.py` 目前每个 tool 都直接调用 adapter 后包装 `_tool_result`。为了记录 tool call，入口层可以加一个统一 wrapper：生成 call_id，发布 `tool_call_started`，调用 adapter 方法，生成 raw_status，发布 `tool_call_finished`，再调用 `_tool_result`。这样能覆盖每个 MCP tool 的 arguments/result。

execution output 的增量发生在 `KernelSession._handle_message`。这里需要把 monitor sink 注入到 `KernelSession`，在处理 stdout/stderr/result/error/status idle 时发 execution 事件。不要让 `Execution` dataclass 直接依赖 HTTP publisher。`Execution` 应继续作为状态对象；`KernelSession` 或 adapter 在状态改变后通知 publisher。

adapter 创建 execution 时发 `execution_submitted`。`finish`、`kill`、`record_error` 后发 finished/status 事件。reset 杀掉 running execution 时也要产生 killed 事件。

publisher 应该是线程安全的，因为 IOPub collector 是后台线程，MCP tool 调用也可能在不同上下文里进入。使用 `queue.Queue`、后台 worker、短超时 HTTP client 是合理方案。

## 十三、测试和验证

Python 侧测试：

1. 后端不存在时，`run_python` 仍返回 completed/error/running 等原有结果，不抛 monitor 异常。

2. fake publisher 能收到 `tool_call_started` 和 `tool_call_finished`，arguments/result summary 正确。

3. fake publisher 能收到 `execution_submitted`、`execution_output`、`execution_finished`。

4. stdout、stderr、result、traceback 都能映射到正确 stream。

5. interrupt/reset/killed 事件正确。

6. 现有测试仍通过，尤其是 output surface、freeform schema、logs map absence、read/search 语义。

Node 后端测试：

1. health endpoint 正常。

2. POST 合法 event 后返回 sequence，并进入 ring buffer。

3. POST 非法 event 返回 400。

4. SSE client 能收到后续 event。

5. ring buffer 超限丢旧事件并统计 dropped。

React 测试：

1. 给定 execution_submitted/output/finished 事件，页面显示 code、stdout、result、状态。

2. 给定 error/traceback，页面显示错误摘要和 traceback tab。

3. 给定 tool_call_started/finished，timeline 显示工具名、参数摘要、返回状态和耗时。

4. 清空视图、筛选、复制按钮基本可用。

Playwright 验证：

启动 monitor 页面，确认页面非空、核心区域存在、状态栏不重叠、mock/真实事件可渲染、桌面和移动视口布局正常。因为这是前端体验，完成前必须做浏览器截图或 DOM 检查，不能只靠单元测试。

## 十四、注意事项和风险

最大的风险是 monitor 发送阻塞 MCP 工具。解决方式是主路径只入队、后台发送、短超时、失败退避、队列有上限。

第二个风险是输出过大导致内存膨胀。解决方式是事件 chunk 限制、outbox 限制、后端 ring buffer 限制、前端渲染限制。UI 可以显示较早事件已丢弃。

第三个风险是敏感信息暴露。默认只监听 localhost，不做局域网开放。文档中明确事件可能包含源码、路径、输出和错误。

第四个风险是事件和现有 output_log 语义冲突。解决方式是浏览器事件只是旁路投影，不修改 `ExecutionLogs`、`presentation.py` 的既有契约。现有工具返回仍以 docs 中的 output surface 为准。

第五个风险是过度设计。第一版只做观察，不做控制，不做持久化，不做多用户，不做远程，不做完整 MCP 协议调试器。

## 十五、实施安排

如果你确认这份汇总，我下一步会先写正式设计文档到 loommux 仓库。考虑这个项目已有 docs 目录，位置建议是：

```text
projects/loommux/docs/mcp-monitor-live-audit-design.md
```

文档会把这份汇总压成正式 spec，包含对象定义、范围、事件协议、架构、UI、测试、非目标。写完后按你的仓库习惯提交设计文档。因为 `loommux` 是独立 git 仓库，提交时会在 `projects/loommux` 内操作，并且只提交相关文件。

设计文档确认后，再进入 implementation plan。计划会拆成几步：Python event publisher、tool wrapper、execution event hook、Hono backend、React UI、测试、验证、文档命令。实现时按测试驱动和验证前置推进，不直接一口气堆完。

## 十六、验收标准

最终可以认为功能完成，需要满足：

1. 在 loommux 项目下存在独立 `monitor/` 子项目。

2. monitor 后端可手动启动，默认 localhost，提供 health、event ingest、SSE stream。

3. 浏览器页面可手动打开，显示连接状态、tool timeline、execution 列表、code 和输出详情。

4. MCP monitor 后端未启动时，现有 Python MCP 工具正常运行，不改变返回结果。

5. monitor 后端启动后，后续 MCP tool call 和 execution events 能进入浏览器。

6. `run_python` 的 code、stdout、stderr、result、traceback、status、output_log 可见。

7. `wait_python`、`read_python_output`、`search_python_output` 等工具调用能在 timeline 中看到 arguments/result summary。

8. 不写持久化数据库或日志文件。

9. 现有 Python 测试通过，新 Python 测试覆盖 event publishing。

10. monitor 的 TypeScript typecheck、Biome、Vitest、Playwright 基本验证通过。

11. 前端在桌面和移动视口不出现明显重叠、溢出和不可读。

## 十七、最终设计结论

我建议锁定的方案是：在 `projects/loommux/monitor/` 创建一个 React 19 + Vite 8 + Hono 的本地实时审计子项目；在 loommux Python MCP 侧新增非阻塞事件发布器；入口层记录每个 Python MCP tool 的调用输入和返回摘要；execution 层记录 Python code、输出增量和结束状态；monitor 后端用内存 ring buffer 接收事件并通过 SSE 推给浏览器；浏览器以 execution 主视图和 tool timeline 两个视角展示 agent 使用 Python 工具的全过程。

这套设计解决的是你真实提出的摩擦：Code Agent 用你的 Python 工具时，你能随时打开浏览器看清楚它到底做了什么。它不把临时审计工具做成历史系统，不把观察面做成控制面，不让 monitor 成为 MCP 主路径依赖，也不破坏 loommux 已经稳定下来的 output surface。它在当前需求边界内足够完整，同时保留以后扩展持久化、控制能力、多 source、多会话的行动权。
