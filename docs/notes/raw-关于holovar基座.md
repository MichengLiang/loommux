<Miran id="1">
完整阅读一下这个东西
\\wsl.localhost\Ubuntu\home\t103o\workbench\projects\aktant\docs\runtime-world.md

然后再来了解一下这个项目：
\\wsl.localhost\Ubuntu\home\t103o\workbench\projects\loommux
\\wsl.localhost\Ubuntu\home\t103o\workbench\projects\loommux\src

啊哈！ 没错，需要给这个 Loommux 也加一个这种 运行时的入口。 能够去操作这一个守护进程 那么调用者呢，可以通过这个去管理自己的这个守护进程。比如说他们可以自己，比如说直接注册新的这个 kernel。然后程序化地去管理所有的 Kernel。比如说一些基本操作嘛，比如说批量地去重启、中断，批量地反正操作，反正就是可以操作守护进程来控制 Kernel。这个你去研究一下吧。看一下一整套，怎么设计呢？你有什么想法看法吗？你了解一下吧。好，那你自己了解完成之后呢，可以来向我分享一下你的这一整套东西。那来和我聊聊，不少于 2000 字或者更多，准备好再来和我说吧。
</Miran>

<Taylor id="1">
**Planning document exploration strategy**

我会先完整阅读 `runtime-world.md`，再建立 Loommux 的目录与入口清单，随后通读守护进程、kernel 生命周期、协议和工具暴露相关实现。最后我会基于现有架构讨论“运行时入口”应承担的职责、API 边界、并发语义与演进路径，不先做代码修改。

**Planning precise file retrieval with ripgrep**

**Planning batch source code reading**

**Planning sequential bat file reading**

**Planning batch file reading**

**Planning individual adapter and factory completion**

**Exploring multi-kernel runtime design**

目前已经确认关键架构事实：Loommux 现在不是“一个守护进程管理多个 kernel”，而是严格的“一进程一 kernel”，并且八个 MCP 工具都直接闭包捕获同一个 `IPythonMCPAdapter`。下一步我正在核对测试与文档中哪些地方把这个边界写成了公开契约，以判断新入口应当是兼容扩展、独立管理面，还是需要重构现有控制面。

**Reviewing test details and git history**

**Planning runtime inspection for Aktant design**

**Reviewing runtime and tool registry**

**Planning detailed source referencing**

**Designing dynamic kernel registration approach**

**Drafting runtime kernel management architecture**

**Implementing kernel lifecycle state transitions**

**Evaluating async locks and stdout capturing**

**Planning runtime opt-in and tool registration**

**Designing async selectors and audit logging**

**Defining kernel removal semantics**

**Refining kernel lifecycle and tool integration plan**

**Planning loommux_runtime object model**

我已经完整阅读了 Aktant 的 [runtime-world.md](/home/t103o/workbench/projects/aktant/docs/runtime-world.md)，也通读了 Loommux 当前的控制面设计、MCP 工厂、adapter、kernel runtime、kernel session、execution/output 投影和相关黑盒测试。下面是我对这件事的完整判断。

**先说结论**

这个方向是成立的，而且会让 Loommux 从“一个持久 IPython kernel 的 MCP 包装器”升级为“一个可编程的 kernel 守护进程”。

但它不能简单地把 Aktant 的 `RuntimeWorld` 文件复制过来。Aktant 已经有一个真正的 `ChatRuntime` 对象，里面聚合了历史、工具注册表、模型端口、事件系统和请求策略，因此 Runtime World 只需把这个现成的活对象暴露给 Python。

Loommux 当前没有对应的“守护进程运行时对象”。它只有 MCP 工厂函数里的一个局部 `IPythonMCPAdapter`：

- 工厂创建一个 adapter：[mcp_server_factory.py](/home/t103o/workbench/projects/loommux/src/loommux/mcp_server_factory.py:16)
- 八个工具通过闭包直接调用它。
- adapter 自己同时拥有 workspace、单个 kernel、execution 表和序列号：[adapter.py](/home/t103o/workbench/projects/loommux/src/loommux/adapter.py:44)
- 文档还明确规定一个 server process 只有一个 kernel：[coding-agent-control-plane-design.md](/home/t103o/workbench/projects/loommux/docs/coding-agent-control-plane-design.md:23)
- 多 kernel session 目前甚至被明确排除在现有设计范围外：[coding-agent-control-plane-design.md](/home/t103o/workbench/projects/loommux/docs/coding-agent-control-plane-design.md:84)

所以真正的第一步不是“增加一个 Python exec 工具”，而是把 Loommux 守护进程塑造成一个可以被编程的对象。

---

**我对新运行时入口的理解**

我建议增加一个名为 `loommux_runtime` 的 MCP 工具：

```python
loommux_runtime(freeform: str)
```

它执行的不是 IPython kernel 里的代码，而是 Loommux 守护进程内部的 Python。其持久命名空间至少包含：

```python
runtime   # 活的 LoommuxRuntime
world     # 持久 ModuleType
spawn     # 创建并由运行时托管后台任务
```

调用者可以这样做：

```python
_result = runtime.kernels.list()
```

或者：

```python
await runtime.kernels.create(
    name="research",
    workspace="/home/t103o/workbench/projects/example",
)
_result = runtime.kernels.describe("research")
```

批量控制可以写成：

```python
targets = runtime.kernels.select(tags={"project": "demo"})
_result = await runtime.kernels.restart_many(
    targets,
    concurrency=4,
)
```

也可以：

```python
_result = await runtime.kernels.interrupt_many(
    runtime.kernels.running(),
)
```

这里最重要的性质和 Aktant Runtime World 一致：`runtime` 必须是守护进程正在使用的真实对象，而不是状态快照。调用者修改 registry、创建 kernel、替换策略或者定义辅助函数后，后续调用应观察到同一组对象。

但 Loommux 的 runtime 根不应该只是当前的 `IPythonMCPAdapter`。这个 adapter 的职责过多，而且对象模型只允许一个 kernel。它需要被放到更高一级的运行时之下。

---

**建议的对象结构**

我建议形成下面的结构：

```text
LoommuxRuntime
├── kernels: KernelRegistry
├── runtime_world: RuntimeWorld
├── runtime_journal
├── tasks
├── configuration
└── lifecycle state

KernelRegistry
├── default -> KernelController
├── named kernels
├── create / attach / start / stop / remove
├── select
├── restart_many / interrupt_many / stop_many
└── operation journal

KernelController
├── descriptor
├── workspace
├── interpreter
├── KernelSession
├── execution records
├── execution sequence
├── lifecycle state
└── per-kernel lock
```

当前的 `IPythonMCPAdapter` 实际上已经非常接近这里的 `KernelController`。它有单 kernel 的全部执行能力：

- 创建和保存 execution；
- 拒绝同一 kernel 上的并发 cell；
- interrupt；
- restart；
- 保留 restart 前的 execution；
- 选择 current/recent execution；
- 读取和搜索输出。

因此不需要推翻它。可以先保留类名或逐步改名，再用 `KernelRegistry` 管理多个 adapter/controller 实例。

当前 `KernelSession` 也天然是单 kernel 对象，并不妨碍多 kernel。每个实例有自己的 `KernelRuntime`、ZMQ client、IOPub collector 线程和 current execution：[kernel_session.py](/home/t103o/workbench/projects/loommux/src/loommux/kernel_session.py:15)。多 kernel 化主要发生在其上层所有权结构，而不是 Jupyter 通信层。

---

**默认 kernel 的兼容性**

现有八工具闭环不应该立即被破坏。

我建议初期保留：

```text
run_cell
status
execution_status
read_output
search_output
wait
interrupt
restart
```

它们继续操作一个名为 `default` 的 kernel。现有 MCP 客户端不需要知道 registry，也不需要立刻传入 `kernel_id`。现有 integer execution 坐标和输出格式全部保持。

守护进程启动时：

```python
runtime.kernels.create_default(...)
```

然后八个工具通过：

```python
runtime.kernels.require("default")
```

找到原来那个 adapter。

这样，“一进程一 kernel”从实现事实变成“默认公开工作流仍绑定一个 kernel”，但运行时入口可以创建额外 kernel。现有用户不会因为多 kernel 能力而突然面对选择器、复合 ID 或工具 schema 变更。

未来若确实需要让普通八工具直接访问非默认 kernel，可以再给它们增加可选的 `kernel` 参数：

```python
run_cell(freeform, kernel=None)
read_output(execution=None, kernel=None, ...)
```

不过这不宜作为第一阶段。因为一旦多个 kernel 都有 execution `1`，单独一个整数就不再能在整个守护进程范围内唯一定位执行。要么工具始终要求 kernel 上下文，要么引入复合坐标：

```text
kernel_id + execution
```

现有 integer contract 是 Loommux 的核心公开不变量，没必要在第一步就动它。

---

**Kernel 身份不能使用 PID**

每个 kernel 应拥有稳定的 `kernel_id`，例如 UUID，同时可以有一个唯一的人类名称：

```python
KernelDescriptor(
    kernel_id="krn_01J...",
    name="research",
    workspace=...,
    ownership="managed",
    state="running",
    generation=3,
    pid=12345,
    tags={"project": "loommux"},
)
```

PID 只能作为诊断信息，不能成为身份。restart 后 PID 会改变，但逻辑 kernel 应保持同一个 `kernel_id`。`generation` 每次替换底层进程时增加，用来识别旧 collector 或旧回调。

名称可以重命名，ID 不变。批量选择最好支持 ID、名称和 tags，但不建议第一版就引入复杂查询语言。Python 本身已经足够表达筛选：

```python
targets = [
    kernel
    for kernel in runtime.kernels.values()
    if kernel.tags.get("group") == "experiments"
]
```

Runtime World 的意义之一就是不必把每种批量选择都固化成新的 MCP JSON schema。

---

**“注册 kernel”必须区分两种语义**

“注册新的 kernel”至少可能表示两件不同的事。

第一种是 Loommux 创建并拥有的新 kernel：

```python
await runtime.kernels.create(
    name="analysis",
    workspace=...,
)
```

这种 kernel 是 `managed`。守护进程负责启动、restart、stop，并在自身关闭时回收它及其子进程。

第二种是连接到一个外部已存在的 Jupyter kernel：

```python
await runtime.kernels.attach(
    name="external",
    connection_file=...,
    ownership="borrowed",
)
```

这种语义复杂很多。连接文件包含 ZMQ 密钥，interrupt、shutdown、restart 的权限和所有权也不明确。`borrowed` kernel 默认不应在 Loommux 退出时被杀死；只有显式采用 `adopted` 所有权后，Loommux 才能承担终止责任。

我的建议是第一阶段只实现 `managed` kernel。`attach` 放在后续阶段，等所有权、凭据、断线恢复和 shutdown 行为有正式契约后再做。否则“注册”一个外部 kernel 很容易变成含糊且危险的接口。

---

**批量操作的语义**

批量 restart、interrupt、stop 不能只做一个普通 `for` 循环然后遇错中止。守护进程管理操作需要明确的部分失败语义。

建议返回：

```python
BatchOperationResult(
    operation="restart",
    requested=("a", "b", "c"),
    succeeded=(...),
    failed=(...),
    skipped=(...),
    started_at=...,
    completed_at=...,
)
```

每个目标有独立结果：

```python
KernelOperationResult(
    kernel_id=...,
    before_state="running",
    after_state="running",
    generation_before=2,
    generation_after=3,
    error=None,
)
```

应满足：

1. 结果顺序与输入目标顺序一致。
2. 一个 kernel 失败不取消其他目标。
3. 默认限制并发数，避免一次创建几十个 kernel 把主机压垮。
4. registry 总锁不能跨越 kernel 启动、wait 或 shutdown。
5. 同一个 kernel 的生命周期操作必须串行化。
6. restart 遇到 running execution 时，沿用当前语义，将其标记为 `killed`。
7. interrupt 只作用于正在运行的 execution；空闲 kernel 返回 `idle`，不算失败。

可以提供：

```python
await runtime.kernels.restart_many(targets, concurrency=4)
```

但底层不应依赖 Runtime World 代码自己保证一致性。真正的并发与状态约束必须由 `KernelRegistry` 和 `KernelController` 实现。

---

**生命周期状态需要正式化**

当前 adapter 基本用 `kernel is None`、`is_alive()` 和 `current_execution` 推导状态。一个 kernel 时还能工作；多 kernel、批量 restart 和后台任务出现后，需要显式状态机：

```text
registered
starting
running
restarting
stopping
stopped
failed
closed
```

其中 `busy` 是 execution 状态，不应混入 kernel 生命周期状态。

例如 restart 应当是：

```text
running
  -> restarting
  -> 停止旧 generation
  -> 创建新 KernelSession
  -> running
```

失败则进入：

```text
restarting -> failed
```

而不是暂时把 `kernel=None` 暴露出去，让其他线程猜测它是否还会回来。

还应加入 `generation`，并要求 IOPub idle callback 同时匹配：

```text
kernel_id
generation
execution
msg_id
```

否则旧 kernel 的 collector 在 restart 边界迟到时，理论上可能修改新一代 controller 的状态。

---

**Runtime World 自身的执行语义**

可以复用 Aktant 的几个优秀选择：

- 一个私有、唯一的 `ModuleType`；
- 同一字典作为 globals 和 locals；
- top-level await；
- `linecache` 注册；
- 每次清除 `_result`；
- stdout、stderr、结果和 traceback 进入 journal；
- Python 异常作为工具输出，而不是 MCP 调用失败；
- `spawn()` 创建的任务由 runtime 生命周期回收。

不过 Loommux 有一个额外问题：HTTP transport 可能同时收到多个 `loommux_runtime` 调用。`contextlib.redirect_stdout` 修改的是进程级 `sys.stdout`，不适合并行执行多个 runtime source。

因此第一版应该明确串行化 Runtime World 调用，使用一个 `asyncio.Lock`。同一时间只运行一个 daemon runtime source。这也符合“修改守护进程控制面”的高风险性质。

Kernel cell 不受这个锁限制，因为它们运行在子进程里。多个不同 kernel 可以并行执行；同一 kernel 继续保持一次一个 cell。

运行时工具本身最好是 `async def`，批量生命周期操作则通过异步接口组织。当前 adapter 的启动、wait、shutdown 是同步阻塞操作，可以先通过 `asyncio.to_thread()` 包装，后续再决定是否将 controller 原生异步化。

---

**安全边界比 `run_cell` 更强**

这一点必须认真对待。

`run_cell` 执行任意 Python，但执行位置是 kernel 子进程。`loommux_runtime` 执行位置是守护进程本身，它可以：

- 读取所有 kernel 的状态和连接信息；
- 停止其他调用者的 kernel；
- 访问守护进程内存；
- 修改 registry；
- 修改 MCP 工具行为；
- 关闭整个服务；
- 访问守护进程持有的配置和凭据。

所以它不是“另一个代码执行工具”，而是管理权限入口。

我建议通过显式 CLI 选项启用：

```text
loommux --runtime
loommux --server --runtime
```

尤其 HTTP 模式不应在没有显式授权的情况下增加这个入口。即使现有服务已经允许 arbitrary kernel Python，也不代表客户端自动获得 daemon-process authority。

第一版我也不建议允许 Runtime World 动态注册任意 MCP 工具。FastMCP 工具列表、`tools/list_changed` 和不同客户端的缓存行为会引入另一整层兼容性问题。运行时先管理 kernel registry；动态修改 MCP 工具图可以作为单独设计处理。

---

**运行时 journal 应分两层**

建议至少有：

```python
runtime.runtime_journal
runtime.operation_journal
```

前者记录 `loommux_runtime` 源码执行，包括 source、stdout、stderr、result、traceback 和创建的任务。

后者记录守护进程操作：

```text
kernel created
kernel startup failed
kernel interrupted
kernel restarted
kernel stopped
kernel removed
batch operation completed
```

两者不能和 kernel execution records 混为一谈。这里存在三种坐标：

```text
runtime execution sequence
kernel_id
kernel-local Loommux execution
```

它们是不同本体。Runtime World execution 不能冒充 IPython execution；kernel PID 不能冒充 kernel ID；多个 kernel 的 execution 整数也不能假装全局唯一。

---

**我建议的实施顺序**

第一阶段先建立 `LoommuxRuntime`，但仍只拥有 default kernel。MCP 工厂不再闭包捕获裸 adapter，而是捕获 runtime。八个工具通过 runtime 找 default controller。所有现有测试应原样通过。

第二阶段加入 opt-in 的 `loommux_runtime` 工具，实现持久 module、top-level await、journal、`_result`、traceback 和关闭语义。此阶段即使只有 default kernel，也已经能让调用者程序化检查和控制守护进程。

第三阶段加入 `KernelRegistry` 和 managed named kernels。默认 kernel 也通过 registry 创建，不能继续拥有一条特殊的私有启动路径。

第四阶段实现批量 restart、interrupt、stop、状态选择和 bounded concurrency，并做真实多 kernel 黑盒测试。

第五阶段再决定是否给八个普通工具增加 `kernel` 选择参数。这个决定需要和 execution 坐标设计一起完成。

第六阶段才考虑 attach 外部 kernel、动态 MCP tool registry、持久化 kernel 描述或守护进程重启恢复。

---

**最终判断**

这个 Runtime World 对 Loommux 的价值，甚至可能比它对 Aktant 更大。

Aktant Runtime World 的核心是“模型可以重写聊天应用的活对象图”。Loommux Runtime World 的核心则应该是“调用者可以把一个固定的单 kernel MCP server，提升为一个可编程的本地计算控制面”。

但正确的抽象不是把 `IPythonMCPAdapter` 暴露出来任人操作，而是先建立：

```text
LoommuxRuntime
    owns KernelRegistry
        owns KernelController
            owns KernelSession
                owns KernelRuntime
```

Runtime World 只是在这个对象模型之上的持久 Python 入口。

这样，普通用户仍然得到简单、稳定的八工具 IPython 闭环；高级调用者则能通过 `loommux_runtime` 创建命名 kernel、编排批量操作、定义会话级 helper、维护标签和策略，甚至构建自己的 kernel 调度逻辑。两者共享同一套底层生命周期与 execution 语义，而不是形成两套互相绕开的控制路径。

本轮只进行了完整阅读和架构研究，没有修改 Loommux 文件。
</Taylor>

<Miran id="2">
那继续工作与调查吧。那么也就是说，它继续暴露出一个工具，这个工具依然就是 Freeform 参数，也是接受一段这个 Python 代码。然后呢，这个默认的这个 Kernel 它是有名字的，就叫 Default 吧。然后呢，这个开发者他可以继续通过这种运行时，就是运行时去控制守护进程，然后去创建新的这个 Kernel 实例。然后呢，每一个 Kernel 实例呢它就会有那 8 个控制方法吗？就类似这样的感觉。那继续帮它结合我之前说的那些来继续调查吧。啊？准备好之后再来和我聊哦，你可以继续探索哟
</Miran>

<Miran id="3">
也就是说，通过这个入口呢，就是可以访问多个 Kernel 嘛，每个 Kernel 都是自己的计数器啊，就是大概这样的感觉。
</Miran>

<Taylor id="3">
**Exploring Jupyter kernel managers**

我会继续沿着你刚确认的模型调查：一个守护进程拥有一个命名 Kernel 注册表，`Default` 只是其中的默认实例；每个 Kernel 独立拥有自己的 execution 计数器、历史记录和八方法控制面。接下来我会重点核对 Jupyter 现有多 Kernel 管理抽象、FastMCP 的 freeform 工具能力，以及这种对象模型下身份、生命周期和并发语义应如何落定。

**Inspecting modules and classes selectively**

**Inspecting concurrency async versions**

**Planning FastMCP inspection and search**

**Planning concurrent adapter testing**

**Planning concurrent kernel execution test**

**Evaluating MultiKernelManager integration**

**Designing ergonomic KernelHandle API**

**Considering sync and async run_cell methods**

**Assessing synchronous versus asynchronous kernel methods**

**Planning mixed async and sync kernel interfaces**

**Deciding on consistent all-async kernel API**

**Planning async adapter wrapping**

**Inspecting git diffs for factory and config**

**Evaluating kernel manager customization options**

**Confirming kernel control methods and runtime roles**

**Outlining kernel API and lifecycle semantics**

**Defining structured kernel method results and naming**

**Clarifying MCP tool access and default kernel naming**

**Planning concise 60-line final response**

对，现在这个模型已经可以比较明确地定下来了：

**Loommux 守护进程暴露九个 MCP 工具：现有八个工具，加一个接受 Python 源码的 `loommux_runtime(freeform)`。现有八个工具默认操作名为 `Default` 的 Kernel；运行时入口则可以访问和管理守护进程里的全部 Kernel。**

**关键区别**

每创建一个 Kernel，并不是动态向 MCP 客户端再注册八个新工具。否则三个 Kernel 就会产生二十四个工具，还会出现命名、缓存和 `tools/list_changed` 等问题。

更合理的是：

```text
MCP 层
├── run_cell             -> Default.run_cell
├── status               -> Default.status
├── execution_status     -> Default.execution_status
├── read_output          -> Default.read_output
├── search_output        -> Default.search_output
├── wait                 -> Default.wait
├── interrupt            -> Default.interrupt
├── restart              -> Default.restart
└── loommux_runtime      -> 守护进程 Runtime World
```

Runtime World 中，每一个 Kernel 对象都具有同样的八方法 Python 控制面：

```python
kernel = runtime.kernels["Default"]

await kernel.run_cell(...)
await kernel.status()
await kernel.execution_status(...)
await kernel.read_output(...)
await kernel.search_output(...)
await kernel.wait(...)
await kernel.interrupt()
await kernel.restart()
```

也就是说，八个 MCP 工具是 `Default` Kernel 八方法的默认投影；其他 Kernel 通过 `loommux_runtime` 中的 Python 对象访问。

**推荐对象模型**

```text
LoommuxRuntime
└── kernels: KernelRegistry
    ├── "Default": KernelHandle
    ├── "Research": KernelHandle
    └── "Build": KernelHandle

KernelHandle
├── identity and metadata
├── KernelController
├── execution records
├── execution counter
└── eight control methods
```

运行时中可以这样创建：

```python
research = await runtime.kernels.create(
    name="Research",
    workspace="/home/t103o/workbench/projects/example",
)

_result = research.describe()
```

然后提交代码：

```python
research = runtime.kernels["Research"]

_result = await research.run_cell("""
import pandas as pd
frame = pd.DataFrame({"value": [1, 2, 3]})
frame
""")
```

后续 Runtime World 调用仍能访问这个对象：

```python
research = runtime.kernels["Research"]
_result = await research.run_cell("frame.sum()")
```

`Default` 也不是一个特殊的底层实现。它只是 registry 中保留的默认名称：

```python
runtime.kernels.default
runtime.kernels["Default"]
```

守护进程启动时，使用当前 launch workspace 创建它。现有八个 MCP 工具始终通过 registry 查找 `Default`，而不是私下保存另一份 adapter。

**每个 Kernel 拥有自己的 execution 坐标系**

这一点我已经用当前 Loommux 的真实 adapter 做了双 Kernel 实验。两个 adapter 同时启动了两个真实 IPython kernel，并行提交代码后得到：

```text
Default:  execution 1, execution 2
Analysis: execution 1, execution 2
```

两个 kernel 的变量也相互隔离，分别保留了各自的 namespace。说明当前 `IPythonMCPAdapter` 和 `KernelSession` 的主体设计本来就可以作为多 Kernel 的单实例控制器使用。

因此坐标应该是：

```text
Kernel "Default" / execution 1
Kernel "Research" / execution 1
Kernel "Build" / execution 1
```

execution 整数只在一个逻辑 Kernel 内唯一，而不是守护进程全局唯一。Runtime World 里返回的结构化结果必须同时带上：

```python
{
    "kernel_id": "krn_...",
    "kernel_name": "Research",
    "execution": 1,
    "status": "completed",
}
```

现有八个 MCP 工具因为默认绑定 `Default`，仍然可以只接受整数 `execution`，不会破坏当前公开协议。

**restart 不重置计数器**

需要区分“底层 kernel 进程”和“逻辑 Kernel 实例”。

```text
KernelHandle "Research"
├── kernel_id: 永久不变
├── generation: restart 时增加
├── PID: restart 时改变
├── execution counter: restart 时继续
└── execution records: restart 后仍可读取
```

例如：

```text
Research / generation 1 / execution 1
Research / generation 1 / execution 2
restart
Research / generation 2 / execution 3
```

这正是当前单 Kernel restart 已有的语义，只是现在该语义属于每一个 KernelHandle。

如果调用：

```python
await runtime.kernels.remove("Research")
```

然后又创建一个同名 Kernel，那么它是一个新的 `kernel_id`，execution 从 `1` 开始。名字可以复用，但身份不能复用。

**KernelRegistry 管理八方法以外的操作**

八方法控制的是一个已存在的 Kernel。创建、删除、枚举和批量操作属于 registry：

```python
runtime.kernels.list()
runtime.kernels.get("Research")
runtime.kernels.select(...)
await runtime.kernels.create(...)
await runtime.kernels.stop(...)
await runtime.kernels.remove(...)
await runtime.kernels.restart_many(...)
await runtime.kernels.interrupt_many(...)
```

例如：

```python
targets = runtime.kernels.select(
    lambda kernel: kernel.tags.get("group") == "experiments"
)

_result = await runtime.kernels.restart_many(
    targets,
    concurrency=4,
)
```

或者直接使用普通 Python：

```python
targets = [
    kernel
    for kernel in runtime.kernels.values()
    if kernel.name != "Default" and kernel.is_busy
]

_result = await runtime.kernels.interrupt_many(targets)
```

Runtime World 已经是 Python，因此没有必要过早发明复杂的 JSON selector DSL。

**八方法返回什么**

KernelHandle 的八个方法不应该直接返回 MCP `ToolResult`，而应返回原始结构化结果，例如 dataclass 或 dictionary。

```python
result = await kernel.run_cell("1 + 1")

_result = {
    "kernel": kernel.name,
    "execution": result.execution,
    "status": result.status,
    "output": result.output_text,
}
```

MCP 默认八工具再把同一结构送入现有的 `mcp_result_policy`，生成模型可读文本和图像 content。

这样底层行为只有一份：

```text
KernelHandle method
        |
        +---- Runtime World 获得结构化 Python 结果
        |
        +---- MCP wrapper 生成 ToolResult
```

不能让 Runtime World 和 MCP 工具各自实现一套 restart、wait 或 output selection。

**是否使用 Jupyter 的 MultiKernelManager**

我进一步检查了当前安装的 `jupyter_client`。它确实提供：

```python
MultiKernelManager
AsyncMultiKernelManager
```

包括 `start_kernel`、`restart_kernel`、`interrupt_kernel`、`shutdown_kernel`、`shutdown_all`、`list_kernel_ids` 等能力。

但是我不建议直接用它替换 Loommux 的 KernelRegistry。原因是 Loommux 已经有比通用 `MultiKernelManager` 更严格的语义：

- 每个 kernel 使用 server-authored kernelspec；
- 每个 kernel 有独立 private runtime root；
- 受控 IPython/Jupyter 环境；
- Windows Job Object containment；
- Loommux 自己的 execution 计数器；
- IOPub collector 与文本、图像投影；
- restart 后保存 execution records；
- current/recent execution 选择规则。

`AsyncMultiKernelManager` 可以作为生命周期设计的参考，但若硬套进去，需要重写或覆盖大量启动行为。更保守的方式是让 Loommux 的 registry 管理多个现有 controller。Jupyter 的每个 `KernelManager` 仍然存在，只是不让通用 MultiKernelManager 成为 Loommux 的领域模型。

**异步接口**

Runtime World 应允许 top-level await，因此八方法在 Python 入口中最好统一为异步：

```python
default = runtime.kernels["Default"]
_result = await default.restart()
```

当前 adapter 是同步阻塞实现，可以先在 KernelHandle 内使用 `asyncio.to_thread()` 调用，不必立刻重写 Jupyter 通信层。

不同 Kernel 可以并行：

```python
_result = await asyncio.gather(
    runtime.kernels["Research"].run_cell("build_report()"),
    runtime.kernels["Build"].run_cell("run_tests()"),
)
```

同一个 Kernel 仍然一次只接受一个 cell；第二次提交返回该 Kernel 自己的 `busy` 状态。

**Runtime World 自己也有独立计数器**

现在会出现三类不同坐标：

```text
loommux_runtime execution 7
Kernel "Default" execution 12
Kernel "Research" execution 12
```

Runtime World journal、Kernel execution records 和 PID 不能混用。

运行时输出可以类似：

```text
Loommux runtime execution 7 completed.

result:
BatchOperationResult(...)
```

Runtime World 调用本身需要串行化，因为 Python 的 stdout/stderr 重定向是进程级状态。但它启动的后台任务、以及不同 Kernel 内的执行，可以继续并发。

**Default 的保护规则**

我建议：

- 名称精确显示为 `Default`。
- registry 名称做大小写不敏感的唯一性检查，避免同时出现 `Default` 和 `default`。
- `Default` 可以 restart、interrupt 和执行代码。
- 第一版不允许直接 remove `Default`。
- 如果以后允许切换默认 Kernel，应先显式调用 `set_default()`，再删除旧实例。
- 所有现有八 MCP 工具在每次调用时动态解析当前 default，而不是启动时闭包固定某个对象。

后面若允许：

```python
runtime.kernels.set_default("Research")
```

那么现有八个 MCP 工具会从下一次调用开始操作 `Research`。不过这个能力风险较大，可以晚于首版实现。

**调查后的最终结构**

目前我认为最稳定的方案是：

```text
一个 Loommux 守护进程
    |
    +-- 一个 loommux_runtime Freeform Python 世界
    |
    +-- 一个 KernelRegistry
            |
            +-- Default
            |     +-- 独立 namespace
            |     +-- 独立 execution 计数器
            |     +-- 独立 execution records
            |     +-- 八个控制方法
            |
            +-- Research
            |     +-- 独立计数器与八方法
            |
            +-- Build
                  +-- 独立计数器与八方法
```

MCP 表面仍然保持紧凑：不是“每个 Kernel 产生八个 MCP 工具”，而是“八个默认工具加一个可以访问所有 Kernel 对象的可编程入口”。

这个结构既完全符合你说的“通过入口访问多个 Kernel，每个 Kernel 都有自己的计数器”，也能最大程度保住 Loommux 当前已经稳定下来的 execution、输出、restart 和八工具契约。
</Taylor>

<Miran id="4">
好的。哎，那其实这样子我们就不就好像好像又变成了一种，就是 Jupyter 吗？是不就是 Jupyter吗？ 我记得这是就是官方的主要维护，其实还是这个前端技术栈，也就是 Node JS， JS 那边有这个有完整的守护进程的这个这种实现。难道说我们就直接复用这种 Notebook 的 这是附庸那边的守护进程吗？然后呢 还是继续在这边这个 Python 自己这边独立的守护进程呢？ MCP 它只是一个只是一个消费者，它只是一个试飞器，是吧？它它就是只是一个接口而已，它默认 MCP 这里我们不需要去这个使用者去提供这个 kernel 的名字，因为它就是默认的 Default。如果说想要操作多个 Kernel，那就要通过这个啊通过这个叫什么呢？这种守护进程的这种管理接口进去去操作多种 Kernel。 其实我感觉对 MCP 来说也不怎么影响呢。 也就是说像 Python 对象一样去操作 MCP，一个 MCP 呢？就是一个 Python 对象。没错，就是服务于这个使用者，就是他临时工作空间嘛，是吧？比如说一个上午，一个下午，一个中午，哎，这就够用了啊？他把这个变量销毁 也就是说提供这个显示的这种销毁的方法嘛。，这个 Kernel 就结束了。嗯，反正就是在一种，反正就是在 Python 里面，哎，去管理所有的相关的东西嘛，就是类似这样的感觉  然后呢，HTTP 它连过来呢，它只能连到这个默认的 Kernel 也就是说，如果想访问别的，就要通过这个这种运行时的这种管理接口进去才能看到哦。这个好像也没什么问题啊，也不用专门设计，因为目前我们不设计，自然而然就是这样子。啊，这他行为没有改变啊，也不需要改变啊，是不是？哎呀，好多东西呢。哎呀，想不明白，那你来帮我想想吧。哎，想好之后再来和我聊聊吧。结合我们之前所有的东西啊，来，再来和我聊聊分享一下，不少于 2000 字或者更多都可以哟。
</Miran>

<Taylor id="4">
**Investigating Jupyter architecture distinctions**

**Comparing Jupyter Server reuse versus standalone**

**Confirming single-process hosting approach**

**Clarifying runtime output handling**

**Designing rich output embedding and kernel lifecycle**

**Planning temporary kernel context management**

**Evaluating FastMCP exposure strategy**

我会先把“是否已经等价于 Jupyter”拆开核对：JupyterLab/Notebook 的前端确实主要是 TypeScript，但负责 sessions、kernels、REST/WebSocket 和进程生命周期的 Jupyter Server 实际上是 Python。接下来我会比较三条路线：直接嵌入 Jupyter Server、只复用 `jupyter_client` 的多 Kernel 组件、继续保留 Loommux 自己的轻量 runtime，并重点检查显式销毁、HTTP 默认 Kernel、非默认 Kernel 输出投影这些容易被“自然行为”掩盖的边界。

**Fetching official docs using httpx and bs4**

**Refining component extraction method**

**Searching REST endpoints**

**Gathering JupyterLab frontend evidence**

**Investigating Jupyter Kernel Gateway architecture**

**Planning GitHub API usage with httpx**

**Comparing Loommux and Kernel Gateway architectures**

**Designing runtime and MCP integration**

**Clarifying MCP and runtime ownership model**

**Designing explicit kernel destruction method**

**Clarifying default kernel destruction and runtime lifecycle**

**Confirming FastMCP single lifespan per server**

**Planning JupyterLab repo language inspection**

**Designing rich output handling**

**Planning kernel execution result design**

**Choosing independent Python runtime approach**

**Outlining detailed Loommux and Jupyter comparison**

**Planning concise over-3000-character Chinese response**

**先回答最核心的问题**

是的，我们正在进入和 Jupyter Server 相同的“问题领域”：一个长期运行的进程管理多个 kernel，为外部消费者提供创建、执行、中断、重启和销毁能力。

但 Loommux 不等于重新实现一个完整 Jupyter，也不应该直接把整个 Jupyter Server 当作附属守护进程搬进来。更准确地说：

> Loommux 应当继续做一个独立、轻量的 Python runtime，复用 Jupyter 官方维护的 kernel 通信和进程管理基础设施，但不继承 Notebook/Jupyter Server 的完整产品模型。

这里需要先纠正一个容易混淆的地方。JupyterLab 前端的确以 TypeScript 为主，GitHub 代码统计里 TypeScript 占绝大部分；但是 Jupyter 的服务端守护进程不是 Node.js。`jupyter_server`、`MappingKernelManager`、`SessionManager`、Kernel Gateway 基本都是 Python。Node/TypeScript 主要负责浏览器前端、插件系统和 UI。

所以不是“Node 那边已有完整守护进程，Python 这边是否重复实现”，而是：

```text
JupyterLab         TypeScript 浏览器前端
Jupyter Server     Python 服务端
jupyter_client     Python kernel 协议与进程管理库
ipykernel          Python kernel
Kernel Gateway     Python 无头 kernel HTTP/WebSocket 服务
```

Loommux 当前已经在复用 `jupyter_client` 和 `ipykernel`。我们没有重写 ZMQ 协议、connection file 或 kernel process manager。

**它像 Jupyter，但不等于 Jupyter**

Jupyter Server 的对象世界大致是：

```text
ServerApp
├── ContentsManager
├── SessionManager
├── MappingKernelManager
├── KernelSpecManager
├── TerminalManager
├── Authentication
├── REST handlers
├── WebSocket channels
└── Notebook/JupyterLab extension system
```

Jupyter Server 官方的 `MappingKernelManager` 已经负责多个 kernel 的启动、interrupt、restart 和 shutdown。REST API 也已经有：

```text
GET    /api/kernels
POST   /api/kernels
GET    /api/kernels/{kernel_id}
DELETE /api/kernels/{kernel_id}
POST   /api/kernels/{kernel_id}/interrupt
POST   /api/kernels/{kernel_id}/restart
```

Jupyter Kernel Gateway 更接近“无头 kernel 守护进程”：通过 REST 创建和删除 kernel，通过 WebSocket 传递原始 Jupyter 消息。

所以，如果 Loommux 的目标只是“启动多个 kernel，然后转发 Jupyter 消息”，直接复用 Kernel Gateway 很合理。

但 Loommux 已经拥有另一套更适合 coding agent 的上层语义：

- Loommux 自己分配稳定的 execution 整数；
- 一个 execution 有 `running/completed/error/interrupted/killed` 生命周期；
- restart 后旧 execution 仍可读取；
- 输出被投影为 `combined/stdout/stderr/result/traceback`；
- 输出可以按行读取和搜索；
- ANSI terminal 控制序列被规范化；
- 图像直接变成 MCP image content；
- `run_cell` 有有限初始等待，但超时不终止 cell；
- `# loommux:` 指令控制本次观察策略；
- 默认八工具构成面向 agent 的请求响应闭环。

Kernel Gateway 和 Jupyter Server没有直接提供这些 Loommux execution 语义。它们提供的是 kernel 和消息通道，而不是 Loommux 的可观察 execution record。

因此直接把 Loommux 改成 Kernel Gateway 客户端，反而会增加一层：

```text
MCP
 -> Loommux execution controller
 -> HTTP/WebSocket
 -> Kernel Gateway
 -> jupyter_client
 -> kernel
```

Loommux还必须自行保存 execution、关联 `msg_id`、收集输出、处理断线、恢复 WebSocket、保存图像、实现 wait/search/read。复杂度会明显增加，却没有消除核心代码。

**我建议继续保留独立 Python runtime**

但“独立”不是自己重新实现 Jupyter，而是维持以下分层：

```text
LoommuxRuntime
    负责 Loommux 领域语义和多个 KernelHandle

KernelHandle / KernelController
    负责一个 kernel 的八方法、execution 和输出记录

KernelSession
    负责一个 kernel 的 IOPub 消息关联和收集

KernelRuntime
    通过 jupyter_client KernelManager 管理真实进程

ipykernel
    执行 Python
```

Jupyter 官方负责下面两层，Loommux 负责上面三层。

以后如果底层希望使用 `AsyncMultiKernelManager` 统一保存多个 Jupyter `KernelManager`，也可以内部替换。但这不应该改变 Loommux 对外的 `LoommuxRuntime -> KernelRegistry -> KernelHandle` 对象模型。

**MCP 的确只是一个接口投影**

你这次的判断是对的。当前代码的问题之一是，MCP 工厂自己创建并闭包捕获 adapter：

[mcp_server_factory.py](/home/t103o/workbench/projects/loommux/src/loommux/mcp_server_factory.py:16)

这使对象所有权看起来像：

```text
FastMCP
└── IPythonMCPAdapter
    └── Kernel
```

未来更合理的反转是：

```text
LoommuxRuntime
├── KernelRegistry
├── RuntimeWorld
├── journals
├── lifecycle
└── interfaces
    └── FastMCP
```

构造过程应接近：

```python
runtime = LoommuxRuntime(configuration)
mcp = create_mcp(runtime)
await runtime.start()
```

FastMCP 只是把 runtime 的部分能力映射成 MCP tools。以后完全可以有：

```python
runtime = LoommuxRuntime(...)
await runtime.kernels.create(...)
```

不启动 MCP，直接作为 Python 库使用。

还可以增加 CLI、测试接口或其他协议，而不需要复制 kernel 管理逻辑。

所以更准确的表达不是“一个 MCP 是一个 Python 对象”，而是：

> 一个 Loommux runtime 是活的 Python 对象；FastMCP server 也是其中一个 Python 接口对象，但它不是 runtime 本体。

Runtime World 可以注入：

```python
runtime   # LoommuxRuntime
mcp       # FastMCP，若当前进程启用了 MCP 接口
world     # 持久运行时 module
```

不过第一版未必需要鼓励模型直接修改 `mcp`。动态添加和删除 MCP tools 会遇到客户端工具列表缓存与 `tools/list_changed` 兼容性，可以后续单独设计。

**Default 行为确实不需要改变**

守护进程启动时创建：

```python
runtime.kernels["Default"]
```

现有八个 MCP tools 每次动态解析 default：

```python
@mcp.tool
async def run_cell(freeform: str):
    kernel = runtime.kernels.default
    return present(await kernel.run_cell(freeform))
```

因此无论 stdio 还是 HTTP，普通 MCP 调用都不需要提供 kernel 名称：

```text
run_cell(...)             -> Default
status()                  -> Default
execution_status(...)     -> Default
read_output(...)          -> Default
search_output(...)        -> Default
wait(...)                 -> Default
interrupt()               -> Default
restart()                 -> Default
```

它们的 schema、文本、execution 整数和使用方法都不变。

如果用户需要其他 kernel，则调用：

```python
loommux_runtime("""
research = await runtime.kernels.create(
    name="Research",
    workspace="/workspace/research",
)
_result = await research.run_cell("import pandas as pd")
""")
```

或者后续：

```python
loommux_runtime("""
research = runtime.kernels["Research"]
_result = await research.run_cell("pd.__version__")
""")
```

因此 MCP 工具总数仍是九个，不会随 kernel 数量增长。

**HTTP 也确实仍然只默认面对 Default**

但需要精确一点：

- 八个普通 HTTP MCP tools 只面对 `Default`。
- HTTP 客户端如果获得了 `loommux_runtime` 权限，可以通过它操作其他 kernel。
- 其他 kernel 不会各自获得单独 URL。
- 不会出现 `/mcp/Research` 或动态创建第二个 MCP server。
- 一个 Loommux HTTP 进程中的 runtime 和 kernel registry 是共享的。

这最后一点很重要。HTTP transport 不应被理解为“每个连接拥有自己的 Default”。当前 Loommux 的产品边界仍然是：

```text
一个 Loommux server process
=
一个受信任使用者的临时计算空间
```

多个 HTTP 客户端若连接到同一进程，它们看到的是同一个 `Default`、同一个 registry 和同一个 Runtime World。Loommux 不是多租户 Notebook 服务，也不应根据 MCP session ID 自动隔离 kernel。

如果需要隔离，应启动多个 Loommux 进程，而不是在一个进程里暗中推断租户。

**临时工作空间和显式销毁**

你说的“上午、下午使用，变量销毁后 Kernel 就结束”在产品感觉上是对的，但 Python 对象层面不能依赖普通变量销毁。

下面这段不能结束 kernel：

```python
kernel = runtime.kernels["Research"]
del kernel
```

因为 registry 仍然持有它，而且不能让不确定的垃圾回收时机控制操作系统进程。

必须提供显式、可等待、幂等的销毁：

```python
await runtime.kernels.destroy("Research")
```

也可以让 KernelHandle 提供便利方法：

```python
research = runtime.kernels["Research"]
await research.destroy()
```

内部语义应是：

1. 将 kernel 状态变成 `destroying`；
2. 拒绝新 execution；
3. 若 execution 正在运行，将其标记为 `killed`；
4. 关闭 Jupyter channels；
5. 终止 kernel 及其子进程；
6. 清理 private runtime root；
7. 从 registry 删除名称和 ID；
8. 写入 operation journal；
9. 重复 destroy 返回已销毁，而不是抛出不可控异常。

还可以提供非常适合临时工作的上下文管理：

```python
async with runtime.kernels.temporary(
    name="MorningResearch",
    workspace="/workspace",
) as kernel:
    await kernel.run_cell("...")
```

退出时自动 destroy。

不过这种自动方式只适合一次 Runtime World 调用内部的短生命周期。要跨多次 MCP 调用持续存在，仍应显式 `create()`，最后显式 `destroy()`。

**每个 Kernel 就是独立 Python 对象**

这个理解完全正确：

```python
default = runtime.kernels["Default"]
research = runtime.kernels["Research"]
build = runtime.kernels["Build"]
```

它们各自拥有：

```text
kernel_id
name
workspace
generation
PID
execution counter
execution records
current execution
recent execution
namespace
eight control methods
lifecycle state
```

同名 execution 不冲突：

```text
Default / execution 5
Research / execution 5
Build / execution 5
```

restart 只更换底层进程 generation，不更换逻辑 KernelHandle，也不重置它的 Loommux execution counter。

destroy 则结束整个逻辑实例。如果以后重新创建同名 kernel，它会获得新的 `kernel_id`，并从 execution `1` 开始。

**有几件事不会完全“自然产生”**

整体 MCP 行为确实几乎不变，但仍有几个边界必须明确设计。

第一是非默认 kernel 的富媒体结果。假设 Runtime World 执行：

```python
_result = await runtime.kernels["Research"].run_cell(
    "display(image)"
)
```

如果 `_result` 只是普通 `repr()`，图像会丢失。`loommux_runtime` 的结果构造器需要识别 Kernel execution result，并复用现有 presentation policy，把其中的图片提升为这个 runtime tool call 的 MCP image content。

第二是 Runtime World 调用需要串行。多个 HTTP 客户端同时执行守护进程 Python，会竞争持久 module 和进程级 stdout/stderr。Kernel 执行可以跨不同实例并行，但 Runtime World 源码本身应由一个锁串行化。

第三是权限。`run_cell` 只在子 kernel 进程中执行代码；`loommux_runtime` 可以控制所有 kernel 并读取守护进程对象图，权限更高。HTTP 模式尤其应通过显式配置启用，而不是把它当成普通第九工具无条件暴露到网络。

第四是 Default 的销毁。我倾向于禁止直接 destroy `Default`，除非先把另一个 kernel 提升为默认：

```python
await runtime.kernels.set_default("Research")
await runtime.kernels.destroy("Default")
```

否则现有八工具会失去目标。关闭整个 LoommuxRuntime 时当然会销毁包括 Default 在内的全部 kernel。

**最后的判断**

所以答案可以概括成一句话：

> Loommux正在吸收 Jupyter Server 的多 kernel 管理形态，但保留自己面向 coding agent 的 execution 与观察语义；它应当复用 `jupyter_client`，而不是嵌入完整 Jupyter Server，也不需要把 Kernel Gateway 作为外部附属守护进程。

最终结构应是：

```text
一个临时 LoommuxRuntime
├── 一个持久 loommux_runtime Python 世界
├── 一个 KernelRegistry
│   ├── Default
│   ├── Research
│   └── Build
└── 一个 MCP 接口
    ├── 八个 Default 工具
    └── 一个 runtime freeform 工具
```

这确实不会改变普通 MCP 用户的行为。变化主要发生在内部所有权：从“FastMCP 闭包里有一个 adapter”变成“LoommuxRuntime 拥有多个 Kernel 对象，MCP 只是 Default 的便利投影和 Runtime World 的入口”。

这条路线既没有重新造完整 Jupyter，也没有把 Loommux降级成 Jupyter Gateway 的薄转发器。它保留了 Loommux 真正独特的部分，同时站在 Jupyter 官方 kernel 基础设施之上。
</Taylor>

<Miran id="5">
等一下哈，你在这个后面，就是你的回答嘛，你这里好像在回复我啊，就是你这里也一起提到了什么 MCP 工厂自己创建并闭包捕获 Adapter 是吧？这个所有权巴拉巴拉巴拉什么的。你说未来什么更合理的反转，我觉得不需要未来了，团队已经准备准备好了一种一种私有的自研框架，挺有趣的，你可以去了解一下，好像名字叫做 HoloVar。 啊，你这里提到说什么，哎，能够这个这什么像 Python 库一样能够使用？ 实际上啊，这种东西嘛，你看它这设计出来其实就是专门为 coding agent 来设计的，也就是为 agent 来设计的。那也就是说，比如说一个主控 Agent 呢，它能够自己的去干什么？去分配资源调度资源。所以说可能第一天开始，我们就是要设计一种 AI 原生的一种操作，也就多端消费者多端观察者。然后需要随时随地都能看，是吧？那人通过什么方式看都行，人能看，Agent、GPT 模型、Gemini、Claude 之类的都要能看，是吧？而且他们之间可能还会有什么 Sub Agent 之类的。哎，那就是一些主控 Agent 主动的去分配资源，是吧？他们要有一些优雅的方式去分配资源什么的。   不知道目前是不是这个 MCP 的这种生命周期是不是绑在这里了？就好像那种单体架构一样。虽然说我们这边确实有守护进程跟克隆的划分，我我不太清楚，我也不太了解啊。  那么对于这个 HoloVar，你觉得它能够带来怎样的一种想象空间呢？ 嗯，你觉得它合合适吗？合适我们的业务需求吗？你要知道哦，你跟我一起是在做理论工作、概念建构、意义建构哟。啊，什么第一步做什么、第二步做什么，那是别人做的事情，那是开发者他们拿到具体的蓝图，哦，比如说土木工程里面 CAD，或者说这个管线、电线，啊，下水道、化粪池，这些都已经完完整设计好之后，专门的这种工期团队再来去设计，什么什么时候设计什么，任务实现之间的依赖是什么？工期最短路径是什么？最迟交付时间、最早完成、最晚完成之类的…你觉得我们要算这个吗？你觉得你现在你要算这个东西吗？总而言之啊，好好想一想吧，结合我们之前的聊天记录，再结合这些新的资料，好好想一想吧。准备好之后呢，再来和我分享吧，那不少于 2500 字或者更多都可以哟。

\\wsl.localhost\Ubuntu\home\t103o\workbench\drafts\holovar-token-chat\frontend\README.md
\\wsl.localhost\Ubuntu\home\t103o\workbench\drafts\holovar-token-chat\app.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\drafts\holovar-token-chat\README.md
\\wsl.localhost\Ubuntu\home\t103o\workbench\drafts\holovar-token-chat\terminal_chat.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\drafts\holovar-token-chat\test_token_chat.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\drafts\holovar-token-chat\token_chat_demo.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\drafts\holovar-token-chat\web_server.py

\\wsl.localhost\Ubuntu\home\t103o\workbench\projects\holovar\README.md
</Miran>

<Taylor id="5">
**Planning comprehensive holovar exploration**

我会先完整阅读你列出的 HoloVar 与 token-chat 材料，再沿着 HoloVar 的对象模型、共享状态、订阅传播和消费者边界去检查实际实现。重点不是制定工期或实现步骤，而是回答三个理论问题：Loommux 的“运行时”究竟是什么对象，MCP 是否只是其中一种观察/行动界面，以及 HoloVar 能否承担多 Agent、多端观察者共同操作同一活世界的基础语义。

**Planning batch reading for large files**

**Splitting reads to prevent truncation**

**Planning to read holovar README**

**Finalizing code file separation**

**Confirming frontend README completeness**

**Planning deep exploration of holovar source and docs**

**Planning to read conceptual and architecture docs**

**Planning selective core file analysis**

材料已经显示出一个关键转向：HoloVar 不是“替 Loommux 换一个 Web 框架”，而是在 Loommux 目前缺失的位置上定义了 Runtime Epoch、Operation、Snapshot、Cursor、Subscription、进程所有权和多消费者恢复语义。我正在继续核对实现，尤其要判断 Kernel 创建、执行、销毁应该成为 HoloVar Operation，还是 Kernel 本身应成为 HoloVar 权威状态空间中的长期资源对象。

**Planning SKILL file analysis**

我会把剩下的 HoloVar 运行时核心、宿主生命周期，以及 Loommux 当前把资源绑定到 MCP 的位置补读完整。接下来给你的不是实施路线，而是一套明确区分“世界、参与者、接口、资源、行动、事实与所有权”的概念模型。
</Taylor>

<Miran id="6">
完整阅读 \\wsl.localhost\Ubuntu\home\t103o\workbench\projects\loommux\docs\tmp\raw-1.md 恢复记忆 然后继续吧！
</Miran>

<Taylor id="6">
**Planning parallel file read execution**

我先完整读取 `raw-1.md`，并同步恢复这次讨论所需的术语纪律；随后再沿着其中已经形成的判断补齐 HoloVar 与 Loommux 的对应关系。

**Planning file chunk reading strategy**

`raw-1.md` 共 1488 行，刚才单次输出触发了工具侧截断，不能算完整阅读。我会按自然范围分段把全文重新读完，避免用摘要替代原文；之后再继续补读核心实现。

**Requesting additional data chunks**

**Setting workspace and listing source directories**

`raw-1.md` 已逐段完整读完，前文的关键脉络已经恢复：我们不是在给单 Kernel MCP 外壳追加管理功能，而是在追问一个共享计算世界如何获得独立于任何传输端的权威性。现在我继续核对 HoloVar 的协调器、事件提交、订阅恢复和宿主所有权，确认它能否真正承接这个本体，而不只是概念上相似。

**Planning parallel file reads with bat**

**Reviewing coordinator component**

**Planning parallel codebase reading**

**Exploring ASGI lifespan and registry symbols**

**Reviewing app and ASGI lifespan details**

**Planning to review client concepts sections**

**Preparing to list Loommux factory files**

**Listing HoloVar documentation chapters**

**Listing root directories**

**Planning deeper book list**

**Reviewing conceptual full files**

**Analyzing HoloVar and Loommux integration**

**Evaluating HoloVar operation limitations**

**Identifying nested operation and state challenges**

**Mapping resource ownership layers**

**Defining semantic event and command model**

**Assessing dynamic operation type registration**

**Planning essay scope and focus**

我已经完整恢复了 [raw-1.md](/home/t103o/workbench/projects/loommux/docs/tmp/raw-1.md) 中的讨论，也补读了 HoloVar 的 Runtime、Coordinator、Registry、Client Runtime、Hosting 与进程所有权实现。现在可以把判断说得更准确一些。

**总判断**

HoloVar 与 Loommux 的业务方向高度契合，但这种契合不是“拿 HoloVar 替换 FastAPI”或者“让 Loommux 改用 HoloVar 启动 HTTP 服务”。

更准确的关系是：

> HoloVar 可以成为 Loommux 的权威运行语义；Loommux 则为这个运行语义提供 Kernel、Execution、Output、Workspace 和 Agent 调度等具体领域对象。

HoloVar 已经解决了 Loommux 目前最欠缺的一组问题：

- 一个运行世界是否独立于 MCP、浏览器和终端而存在；
- 多个消费者如何确认自己观察的是同一个世界；
- 一个行为怎样从调用请求变成可追踪的持续实体；
- 状态与事件如何形成同一个权威事实；
- 晚加入的观察者怎样恢复现状；
- 断线的观察者怎样接回实时事件；
- 临时宿主、独立服务和附着客户端分别拥有什么生命周期权力；
- Runtime 关闭时怎样保证任务和资源真正闭合。

所以，HoloVar 带来的不是又一个 Adapter，而是我们一直缺少的“共同世界语法”。

**当前 Loommux 的问题确实是所有权倒置**

当前 [mcp_server_factory.py](/home/t103o/workbench/projects/loommux/src/loommux/mcp_server_factory.py:13) 中的实际结构是：

```text
FastMCP 工厂
└── 创建 IPythonMCPAdapter
    └── 启动并拥有一个 Kernel
```

`IPythonMCPAdapter` 在 `create_mcp()` 中创建；FastMCP lifespan 启动 workspace，lifespan 结束时关闭 adapter。八个工具又通过闭包直接引用这个 adapter。

因此，虽然代码里已经有“服务进程”和“Kernel 子进程”的区别，但在更高层的语义上，MCP 宿主仍然是整个计算空间的事实所有者。没有 MCP，就没有那个 adapter；没有 adapter，也没有 Kernel。

这不是传统意义上的“单体架构”问题。单进程本身完全可以是正确设计。真正的问题是：

> 传输适配器的生命周期，意外地成为了领域运行世界的生命周期。

MCP 本应只是一个参与接口，现在却同时承担了世界的创建者、资源持有者、方法投影者和终结者。于是浏览器、Python Client、其他 Agent 或终端若要进入同一个世界，只能附着到一个由 MCP 定义的对象结构中。

HoloVar 已经做出了相反的区分。`Holovar` Application 可以直接创建 Runtime；Runtime 拥有任务、Operation、Store、Journal 和 Subscription。HTTP 应用只是通过 ASGI lifespan 承载它。Hosting 又进一步区分：

```text
standalone
temporary
attached
```

临时终端可以拥有服务进程；浏览器可以同时连接该服务；但浏览器不会因为连接上来就获得进程所有权。附着客户端关闭，也不会杀死它未拥有的服务。

这正是 Loommux 需要的所有权反转，而且不是“未来可能如此”，而是 HoloVar 已经提供了可工作的语义原型。

**但 Runtime 不等于一个 Python 对象**

我们之前说“Loommux Runtime 是一个活的 Python 对象”，这句话在进程内实现层是正确的，但作为完整定义仍不够。

一个普通 Python 对象只有对象身份、引用和方法。它不自动拥有：

- 跨进程可识别的世界身份；
- 多个参与者共享的事件顺序；
- 晚加入观察者所需的 Snapshot；
- 断线后的恢复坐标；
- 调用者身份；
- 权限边界；
- 资源和任务的关闭责任。

因此，更完整的说法应当是：

> Loommux Runtime 是一个具有独立身份、权威状态、提交顺序、资源所有权和观察协议的共享计算世界；Python 对象只是这个世界在宿主进程内部的直接体现。

HoloVar 的 `runtime_id`、Event `sequence`、Snapshot、Cursor、Subscription 和 Reset，使这个 Python 对象获得了“能够被多端共同指称”的能力。

这很关键。多端共享不是让大家拿到同一个 Python 内存地址，而是让大家能够确认：

```text
我看到的 Kernel K
=
你看到的 Kernel K
=
Agent 刚刚重启的 Kernel K
```

即使一个参与者通过 MCP，看见文本；另一个通过浏览器，看见图形界面；第三个通过 Python Client，看见 Pydantic 对象，它们仍然指向同一个权威对象和同一组已提交事实。

所以用户真正共享的不是画面，也不是传输连接，而是对象身份和事件意义。

**MCP 应当是什么**

MCP 应当被定义为 Agent 面向 Loommux Runtime 的一种命令与观察接口。

它不是 Runtime，不是 Kernel 所有者，也不是参与者身份本身。GPT、Claude 或 Gemini 也不等于 MCP Client。这里至少需要区分四个对象：

```text
Participant
    实际参与共同世界的主体，例如人、主控 Agent、Sub Agent。

Principal
    经认证后参与权限判断的身份。

Client
    某个具体连接或程序实例，例如一个 Codex 进程、浏览器标签页、终端。

Interface
    进入世界的协议表面，例如 MCP、HTTP、SSE、Python API。
```

一个主控 Agent 可能同时拥有一个 MCP Client 和一个 HTTP Event Client。一个 Sub Agent 可能使用另一个 MCP Client，但其 Principal 带有来自主控 Agent 的委托关系。一个人类可以同时通过浏览器和终端行动，但两个 Client 仍属于同一个 Principal。

如果把这四者合并成“调用者”，后续所有权、审计、委托、接管和观察范围都会变得含糊。

HoloVar 目前已有 `Principal`、认证提供者和授权策略这些正确边界。不过现有默认本地 Token 只足以表达“受信任的本地使用者”，还不足以完整表达主控 Agent、Sub Agent、委托范围和资源配额。这不是 HoloVar 方向错误，而是 Loommux 领域需要在其身份边界上增加更丰富的参与关系。

**Runtime World 的真正地位**

`loommux_runtime(freeform)` 仍然非常合理，但它不应该被理解为一种绕开正式运行语义、随意修改内部字典的后门。

它应当是：

> 面向高权限 Agent 的、以 Python 为表达语言的运行时管理界面。

也就是说，Python 代码可以组合、筛选、循环、并发和批量操作，但它最终调用的仍应是受 Runtime 约束的领域方法：

```python
runtime.kernels.create(...)
kernel.restart()
kernel.execute(...)
runtime.kernels.destroy(...)
```

这些方法不能直接悄悄改动内部状态。它们必须继续产生与其他接口相同的 Command、Operation、Event 和 Projection。

否则会出现两个世界：

```text
MCP 八工具产生可观察事实
Runtime World 直接修改内部对象
```

或者反过来：

```text
HTTP 命令进入 HoloVar
Runtime World 绕开 HoloVar 操作 Kernel
```

一旦如此，浏览器就无法知道 Agent 在 Runtime World 中做了什么；Snapshot 也可能与真实 Kernel 状态不一致。那便失去了 HoloVar 的全部意义。

所以 Freeform Python 提供的是表达能力，不是绕过权威边界的资格。它像一个可编程控制台，但控制台里的动作仍然属于同一个世界。

**Kernel 与 Operation 不能直接画等号**

这是 HoloVar 与 Loommux 结合时最重要的本体区别。

HoloVar 当前的中心实体是 Operation。Operation 接受一个 Command，经历：

```text
accepted -> running -> completed | failed | cancelled
```

但 Loommux Kernel 不是一次普通 Operation。Kernel 是一个长期存在、可被反复操作的资源：

```text
Kernel
├── kernel_id
├── name
├── workspace
├── generation
├── lifecycle state
├── namespace
├── execution counter
├── execution records
└── control authority
```

对 Kernel 的创建、执行、interrupt、restart 和 destroy 是行动；Kernel 本身则是这些行动共同指向的持续对象。

最自然的对应关系是：

```text
Kernel                 长期共享资源
Execution              一次代码执行实体
kernel.create           Operation
kernel.restart          Operation
kernel.interrupt        Operation
kernel.destroy          Operation
kernel.execute          Operation，或专门化的 Execution
```

这也暴露了 HoloVar 当前实现的一个真实边界：它的权威 Store 主要保存 Operation Record，没有独立的一般性 Resource/Aggregate Store。一个 Operation 的处理器只能推进自己的 Projection，不能天然地对另一个长期 Kernel 资源做共享状态提交。

因此，HoloVar 对 Loommux 是合适的语义基础，但现有版本还不能不加领域建模就直接吞下 Loommux。Loommux 必须为 Kernel 提供正式的权威资源表示；或者 HoloVar 的 Application 权威空间需要从“Operation 集合”推广为“Resource 与 Operation 共同构成的应用状态”。

不能简单地把 Kernel 伪装成永不完成的 Operation。那会造成多个问题：

- `restart` 并不终结逻辑 Kernel；
- 一个 Kernel 上会连续发生多个 Execution；
- 多个 Agent 可以围绕同一个 Kernel 提交不同操作；
- Kernel Projection 需要被不同 Operation 一致推进；
- Kernel 销毁与一次执行取消不是同一含义。

同样，也不宜把整个 Loommux Runtime 塞进一个永不结束的巨型 Operation。那会把所有 Kernel 的身份、并发和权限压缩进一个 Projection，失去 HoloVar 已经建立的清晰 Operation 语义。

所以，正确方向是承认两类一等对象：

```text
Resource
    持续存在并可被反复操作的对象，例如 Kernel、Workspace、Artifact。

Operation
    对 Resource 或整个 Application 发起的一次有身份、有生命周期的行动。
```

**一套互补坐标**

结合 HoloVar 后，Loommux 需要的不是一个计数器，而是一组互不冒充的坐标：

```text
runtime_id
    当前共享世界的运行代际。

sequence
    当前 Runtime 中已提交事实的全局顺序。

kernel_id
    一个逻辑 Kernel 的稳定身份。

generation
    该 Kernel 当前底层进程的代际。

execution
    某个 Kernel 内部严格递增的 Loommux 执行编号。

operation_id
    一次创建、执行、重启、中断或销毁行动的身份。
```

完整事件坐标是：

```text
(runtime_id, sequence)
```

一个代码执行的领域坐标则是：

```text
(kernel_id, execution)
```

若要确认它在哪一代底层进程中执行，还可以加入 `generation`。

这些坐标不是重复设计。全局 Sequence 服务于多端观察和恢复；Kernel-local execution 服务于 coding agent 对输出的稳定访问。前者回答“整个世界中此后发生了什么”，后者回答“Research Kernel 的第 12 次执行是什么”。

**Default 与八工具的新意义**

`Default` 仍然可以保持当前行为，但其含义需要改变。

它不应再表示“MCP 私有拥有的那个 Kernel”，而应表示：

> 当前 Loommux Runtime 中被指定为默认交互目标的 Kernel 资源。

现有八个 MCP 工具是这个资源控制面的便利投影：

```text
run_cell         -> Default
status           -> Default
read_output      -> Default
restart          -> Default
...
```

`loommux_runtime` 则可以看到整个 Kernel 集合。

普通使用者不必提供 Kernel 名称，协议行为也不必改变。但每次八工具调用仍应进入同一个权威世界。例如 `run_cell` 不应私下调用 adapter 后只把结果返回给当前 Agent；它应创建一个 Execution/Operation，使浏览器、终端或另一个 Agent 可以看到：

```text
谁提交了执行
在哪个 Kernel
何时被接受
是否正在运行
当前产生了什么
最终如何结束
```

因此，兼容性保持在交互表面，权威性则从 MCP 闭包迁移到共享 Runtime。

**AI 原生并不等于给 Agent 一个 GUI**

HoloVar 的多参与者材料中最有价值的一句话可以概括为：

> 不要让 Agent 成为旧 GUI 的幽灵用户，而要让人和 Agent 成为同一个语义空间中的一等参与者。

这意味着共享事件不能只描述鼠标点击、按钮位置或某个 React 组件的局部状态。Loommux 中的事件应描述领域事实：

```text
kernel.created
kernel.restart_requested
kernel.restarted
execution.accepted
execution.output_appended
execution.completed
kernel.control_transferred
allocation.released
```

人类界面可以把它们显示为按钮、列表和日志；Agent 可以把它们转成上下文、判断和下一次行动；终端可以打印它们；另一个服务可以存档它们。

多端不是多个 UI，而是多个对同一语义事实的投影。

这也意味着 Agent 向人请求输入，不能只是某个浏览器进程里的 callback。它应当成为持久的 `Interaction` 对象或 Operation：具有发起者、目标参与者、问题、允许回答者、状态和过期语义。人可以从浏览器回答，也可以从终端回答；主控 Agent 随后观察到同一个已提交回答。

这才是 HoloVar 为 Loommux 打开的真正想象空间：Kernel 管理只是第一个领域。

**资源调度与所有权**

“主控 Agent 分配 Kernel 给 Sub Agent”不能只表示把一个 Python 变量传过去。它至少涉及四种不同权力：

```text
Process Ownership
    谁负责 Runtime 进程最终结束。

Resource Custody
    谁负责 Kernel 的创建、重启、清理和销毁。

Allocation
    某个 Kernel 当前被分配给谁、多久、承担什么用途。

Control Authority
    谁可以 execute、interrupt、restart、destroy。

Observation Authority
    谁可以看到状态、源码、输出、错误和 Artifact。
```

还应区分 Operation 的发起者与当前控制者。主控 Agent 可以创建 Kernel 并分配给 Sub Agent；Sub Agent 可以提交 Execution；人类可以接管或中断；但 Runtime 仍然是状态转换的唯一权威提交者。

“所有者”不能继续作为一个万能字段。它必须被拆成可验证的关系，否则 Agent 协作中的委托、回收、接管和审计都会混乱。

**HoloVar 是否适合**

我的结论是：适合，而且非常接近 Loommux 真正需要的方向，但应该把它理解为一种运行时宪法，而不是现成的 Kernel Manager。

它已经成熟表达了：

- 一个世界、多种前端；
- Command 是意图，Event 是已提交事实；
- Snapshot 是当前权威状态；
- Cursor 是观察边界；
- Runtime Epoch 防止旧世界身份混入新世界；
- Subscription 连接历史与实时；
- 慢观察者不能拖垮生产者；
- 临时所有者与普通附着者不同；
- 客户端关闭不等于服务关闭；
- Runtime 必须拥有并闭合其任务。

Loommux 需要补充的是：

- Kernel 作为长期 Resource 的本体；
- Execution 与 Kernel 的关系；
- Principal、Agent、Sub Agent、Client 和 Interface 的分离；
- 委托、配额、租约、接管和观察范围；
- 输出流与全局语义事件之间的边界；
- Resource 与 Operation 的一致提交关系。

HoloVar 当前是单进程、内存态、有限历史的 Runtime。这并不妨碍当前目标，反而与“一个上午、一个下午的一次临时 Agent 工作空间”高度匹配。我们不应该因为想象到了跨主机集群，就提前否定一个边界清楚的本地运行世界。

最终可以把完整结构理解为：

```text
Loommux Application
└── HoloVar Runtime Epoch
    ├── 权威 Resource Space
    │   ├── Workspace
    │   ├── Kernel
    │   ├── Artifact
    │   ├── Interaction
    │   └── Allocation
    ├── Operation Space
    │   ├── KernelCreate
    │   ├── Execution
    │   ├── KernelRestart
    │   ├── KernelInterrupt
    │   └── KernelDestroy
    ├── Global Event Sequence
    ├── Snapshot / Cursor / Subscription
    ├── Principal / Authorization
    └── Interfaces
        ├── MCP
        ├── Runtime World
        ├── HTTP / SSE
        ├── Python Client
        ├── Browser
        └── Terminal
```

因此，我们不再是在设计“一个能管理多个 Kernel 的 MCP Server”。

我们是在定义一个由 Runtime 维持、由多种智能主体共同参与、以 Kernel 和 Execution 为核心资源、能够被多个界面同时驱动和观察的临时计算世界。MCP 是 Agent 进入这个世界的一个门；Runtime World 是高权限 Agent 使用 Python 表达管理意图的控制语言；HoloVar 则提供这个世界保持同一性、可观察性、可恢复性和生命周期闭合所需的基本语法。
</Taylor>

<Miran id="7">
你这里要这个再思考一下啊，你看一下这里的东西是不是属于 HoloVar？如果是，那就可以在他那边开发。如果不是，那就不能在这边，这边就会被污染。因为这里已经是完全独立出一个一个团队在这里面，他这里没有任何的业务，是吧？你看到这里面各种各样的都是关于进程呀、并发呀、生命周期呀、语义呀。还有你看他 README 开头也说什么，他是 fast API 的包装的上层，是不是？啊，你再看一下啊。你也可以再深入调查一下啊，看一下对于 HoloVar是否有需要这个，嗯，相应的补充设计的啊，这都是可以的啊，也就是说我们通过观察这个  Loommux  这个消费者，是吧？那HoloVar这边呢？也可以相应的一些这个配合什么的啊？但是要清晰地知道哪些是属于 Loommux 的，哪些是属于 HoloVar 的。这个呢，你自己好好想想吧，看一下你什么要探索的吗？哎，你完成之后呢，再来向我分享一下，把你想要和我说的东西准备好，然后呢再来和我说，那不少于 2000 字或者更多都可以哦。那你安排一下吧。
</Miran>

<Taylor id="7">
**Reading HoloVar README and architecture**

我会重新从两个项目各自声明的边界出发，而不是从“怎样拼装它们”倒推归属。重点核对三件事：HoloVar 自己承诺管理什么不变量，Loommux 哪些概念带有明确的 Kernel 领域含义，以及哪些因 Loommux 暴露出来的问题其实是 HoloVar 应提供的通用能力。

**Reviewing HoloVar README scope**

**Investigating HoloVar resource semantics**

**Reviewing shared object segments**

**Assessing HoloVar scope expansion**

**Evaluating command handling within HoloVar operations**

**Identifying missing input channels in HoloVar**

**Assessing HoloVar operation handling**

**Defining HoloVar kernel operation protocols**

**Evaluating HoloVar extension for app state**

**Evaluating application state coordination options**

**Defining HoloVar hosting and transport boundaries**

**Planning reusable ApplicationHost component**

**Identifying HoloVar runtime injection limitation**

**Outlining HoloVar runtime sharing enhancements**

**Distinguishing kernel lifecycle responsibilities**

**Mapping runtime and resource management systems**

**Clarifying HoloVar and Loommux boundaries**

**Verifying application runtime creation semantics**

你这次提醒非常重要。前面的讨论里，我把“共享世界需要哪些对象”和“这些对象应该由哪个项目实现”靠得太近了。概念上相关，不等于代码和产品边界上都属于 HoloVar。

重新核对两个项目的 README、架构文档和实现之后，我现在的判断是：

> HoloVar 应当只拥有跨业务重复的运行时机制与不变量；Kernel、Execution、Workspace 等对象即使进入了 HoloVar 承载的共享世界，其定义和业务行为仍然属于 Loommux。

换句话说，HoloVar 可以承载 Loommux，但不应吸收 Loommux。

**先确定 HoloVar 是什么**

HoloVar README 开头的正式定义是：

> Holovar 是一个建立在 FastAPI 之上的单进程应用 Runtime。它把一次持续执行、会产生中间变化的 Python 业务调用组织为共享可观察的 Operation。

这句话有两个部分。

第一，HoloVar 确实建立在 FastAPI 之上。它提供默认 HTTP Command/Query、SSE Event Stream、浏览器 SDK、Python Client、CLI 和 Hosting。它不是一个只剩抽象接口、不能自行运行的底层库。

第二，它也不是普通意义上的“FastAPI 包装器”。它真正拥有的是：

- Operation 生命周期；
- Task Group 与 Cancel Scope；
- State、Projection 和 Event 的一致提交；
- Runtime Epoch 与全局 Sequence；
- Snapshot、Cursor、Replay 和 Reset；
- Subscription 的连续性和容量；
- Client Runtime 的断线恢复；
- 临时服务与附着客户端的进程所有权；
- 认证、授权和错误映射的调用边界。

FastAPI 是默认协议承载者，不是 HoloVar 的本体。HoloVar 的 Runtime 核心没有依赖 FastAPI Request、SSE 或浏览器对象。这一点在 [application.py](/home/t103o/workbench/projects/holovar/src/holovar/application.py:25) 和 [runtime/kernel.py](/home/t103o/workbench/projects/holovar/src/holovar/runtime/kernel.py:53) 中都非常明确。

所以，HoloVar 的准确定位不是：

```text
FastAPI helper library
```

而是：

```text
Application Runtime
+
默认 FastAPI/HTTP/SSE 适配路径
+
Hosting 与 Client Runtime
```

它没有业务，不表示它没有对象模型。Operation、Command、Event、Projection、Result、Snapshot、Cursor 和 Subscription 就是它的框架对象模型。但这些对象都不预设“媒体转换”“设备控制”“聊天”或者“Kernel 执行”的业务含义。

**判断归属的标准**

我认为可以用一个很严格的测试来防止 HoloVar 被污染：

> 如果一个能力的正式定义必须出现 IPython、Jupyter、Kernel、cell、IOPub、execution output、MCP tool 等术语，它就不属于 HoloVar。

反过来：

> 如果一个能力可以完全不提 Loommux，并且媒体处理、设备控制、文档转换、Agent 工作流等应用同样需要它，它才可能属于 HoloVar。

这比“是否对多个项目有用”更严格。许多 Loommux 功能当然也可能被其他计算工具复用，但那不意味着它属于通用 Application Runtime。

按照这个标准，边界会清楚很多。

**确定属于 Loommux 的部分**

下面这些对象和语义应当完整留在 Loommux：

```text
Kernel
KernelRegistry
Default Kernel
kernel_id
kernel generation
IPython namespace
kernel workspace
private IPython/Jupyter runtime root
KernelSession
KernelRuntime
Jupyter connection
IOPub collector
Loommux execution counter
Execution record
stdout/stderr/result/traceback/combined
terminal text normalization
rich display capture
八方法 Kernel 控制面
loommux_runtime Freeform Python surface
```

Kernel 创建、启动、重启、中断、销毁的具体状态机也属于 Loommux。因为这些行为涉及：

- 底层 Jupyter KernelManager；
- Kernel PID 和 generation；
- IPython namespace 是否保留；
- running execution 怎样标记为 interrupted 或 killed；
- restart 后 execution counter 是否连续；
- private runtime root 怎样替换和清理；
- Windows Job Object；
- IOPub idle 消息与 Execution 的对应；
- 图像、文本和 traceback 怎样进入公开输出。

这些都不是通用 Application Runtime 能够判断的事情。

同样，`Default` 也完全是 Loommux 概念。HoloVar 不应知道某个 Application 有一个叫 `Default` 的 Kernel，更不应内置“八个方法”。

`loommux_runtime(freeform)` 也属于 Loommux。Freeform Python 是否允许 top-level await、如何暴露 `runtime.kernels`、怎样投影 Execution 的图片和输出、哪些高权限操作可用，这些都是 Loommux 的 Agent 控制语言。

HoloVar 不应该出现：

```python
runtime.kernels
runtime.default_kernel
runtime.execute_cell()
```

一旦出现，HoloVar 就不再是无业务的 Application Runtime，而成了 Loommux 的基础设施仓库。

**确定属于 HoloVar 的部分**

HoloVar 应继续拥有的是与业务种类无关的运行机制。

例如，Loommux 提交一次代码执行时，这个行为可以成为 HoloVar Operation。HoloVar负责：

```text
accepted
running
completed / failed / cancelled
```

但 HoloVar 不负责解释“代码执行完成”的含义。Loommux 的 handler 判断 IOPub 是否到达 idle，生成 Loommux Result 和 Business Event，HoloVar 只验证并提交它们。

同理，HoloVar 可以负责：

- 为一次 Kernel restart 分配 `operation_id`；
- 记录是哪个 Principal 发起；
- 让多个订阅者看到 accepted、started 和 completed；
- 在 Runtime 关闭时取消仍在运行的操作；
- 执行 Loommux 注册的清理 callback；
- 为事件分配全局 Sequence；
- 在历史缺口或 Runtime 更换后要求客户端 Reset；
- 对不同客户端维持独立的有界 Subscription。

但 HoloVar 不负责决定 restart 怎样替换进程，也不负责决定 restart 后 execution counter 是否归零。

这是一种非常干净的组合：

```text
HoloVar
    拥有 Operation 运行不变量

Loommux
    拥有 Operation 的领域意义与外部资源
```

**Kernel 是否应成为 HoloVar Resource**

我需要修正上一轮中一个过于激进的推论。

我之前提出，HoloVar 可能需要增加通用 `Resource`，然后把 Kernel、Workspace、Artifact 等都放进去。这个方向在理论上很有想象力，但还不足以直接宣布它属于 HoloVar。

HoloVar 当前正式、经过实现和测试证明的产品边界是：

> 将一次持续业务调用组织成共享可观察的 Operation。

它不是通用事件溯源框架，也不是任意 Aggregate Store。当前的权威 Snapshot 是 Operation Snapshot 集合；Coordinator 原子提交的是 Operation Record、Projection、Event 和 Cursor。

如果直接给 HoloVar 增加：

```text
Resource
Workspace
Artifact
Interaction
Selection
Goal
Execution
Notification
```

那么它的产品定义会从“可观察 Operation Runtime”扩展成“通用共享语义世界框架”。这不是一次小补充，而是本体层级的改变。

[003-raw-关于多参与者.md](/home/t103o/workbench/projects/holovar/docs/003-raw-%E5%85%B3%E4%BA%8E%E5%A4%9A%E5%8F%82%E4%B8%8E%E8%80%85.md:407) 中确实提出了这些共享对象，但该文件是研究材料，不是当前 README、协议和实现已经承诺的 canonical surface。它表达的是 HoloVar 可能通向的更大理论空间，不能反过来证明这些对象已经属于 HoloVar。

所以现在更审慎的判断是：

> Kernel 首先是 Loommux 领域资源。除非多个彼此无关的 HoloVar 应用都证明需要统一的长期可寻址资源语义，否则不要为了 Loommux 把通用 Resource 系统塞进 HoloVar。

Loommux 可以在自己的应用层维护 `KernelRegistry`，然后让 HoloVar Operation 对它执行动作。Kernel Snapshot、Kernel 查询和 Kernel 领域事件也可以先由 Loommux 定义。

HoloVar 无需理解这个 registry 存放的是 Kernel、设备、文档还是模型进程。

**Loommux 暴露出的 HoloVar 真缺口**

不过，Loommux 确实揭示了几个属于 HoloVar 自己的问题。这些问题可以在 HoloVar 中补充，因为它们不用任何 Loommux 术语就能成立。

第一个是 **Runtime 与 Transport 的装配所有权**。

HoloVar 的概念模型宣称“一个 Application，多个 Frontend”，Runtime 核心也确实独立于 HTTP。但当前 [asgi.py](/home/t103o/workbench/projects/holovar/src/holovar/asgi.py:97) 中：

```python
runtime = application.runtime(...)
```

由 `create_http_app()` 自己创建 Runtime，随后 ASGI lifespan 独占其生命周期。

这对单一 HTTP 宿主没有问题，但当同一个进程需要同时装配 HTTP、MCP 或其他原生接口时，就会出现一个通用问题：

```text
是谁创建 Runtime？
谁拥有 Runtime？
多个 Adapter 怎样引用同一个 Runtime？
哪个 Adapter 的关闭有权结束 Runtime？
```

这些问题不含任何 Kernel 业务词汇，因此属于 HoloVar。

HoloVar 的理论边界应允许：

```text
Host owns Runtime
HTTP adapter observes Runtime
MCP adapter observes Runtime
CLI adapter observes Runtime
```

而不是每个 Adapter 各自创建一个 Runtime。

这并不意味着 HoloVar 核心要依赖 FastMCP。HoloVar 需要的是可组合的外部 Runtime 所有权，而不是内置所有协议。MCP Adapter 仍可留在 Loommux；HoloVar只保证它能够附着到一个由宿主拥有的 Runtime。

第二个是 **持续 Operation 是否能接受后续语义输入**。

当前 HoloVar Operation 只有一个创建 Command。Operation 运行以后，框架级公共动作基本只有查询、订阅和取消。但设备控制、Agent 协作、审批流程以及 Kernel Session 都可能需要：

```text
一个 Operation 已经 running
另一个参与者向它提交一次干预
Operation 接受或拒绝该干预
干预产生有序事实
```

例如这类完全通用的语义：

```text
pause requested
approval submitted
parameter changed
operator takeover requested
resume requested
```

这不是 Loommux 专属问题。Loommux 的 `interrupt`、`restart` 或向长期 Kernel Session 提交 Execution，只是它的一种表现。

因此，“向持续 Operation 发送后续 Command/Signal/Interaction”可能是真正属于 HoloVar 的扩展方向。框架应定义它的身份、权限、提交顺序和失败边界；具体信号类型仍由应用声明。

不过，这项能力也不能仅仅因为 Loommux 需要就草率加入。必须明确它是否仍然保持 HoloVar 的 Operation 中心模型，还是会把 Operation 变成任意 Actor/Mailbox。后者会大幅改变框架含义。

第三个是 **Participant、Principal 与 Client 的区分**。

HoloVar 当前有 `Principal`、AuthenticationProvider 和 AuthorizationPolicy。这足以表达基本认证和授权，但多 Agent 场景还会要求区分：

```text
Participant
Principal
Client connection
Agent instance
Delegation
Acting on behalf of
```

其中认证、授权调用位置和稳定调用者坐标属于 HoloVar；“主控 Agent 可以给某个 Sub Agent 分配多少个 Kernel”则属于 Loommux。

HoloVar 可以提供身份与委托的协议位置，却不应规定 Agent 团队结构和资源分配策略。

**双方共同承担，但不能混成一层的部分**

有一些行为需要两个项目合作完成。合作不等于所有权模糊。

以创建 Kernel 为例：

```text
HoloVar Command
    负责已验证的行动意图

HoloVar Operation
    负责该行动的通用生命周期

Loommux handler
    负责创建真实 Kernel

Loommux KernelRegistry
    负责保存 Kernel 资源

Loommux Business Event
    描述 kernel.created 或 kernel.start_failed

HoloVar Coordinator
    负责验证、分配 Sequence、提交和广播 Event

HoloVar Subscription
    负责让多个观察者连续看到该事实
```

再以关闭整个服务为例：

```text
HoloVar Host
    负责 Runtime Epoch 的进程级生命周期

HoloVar Runtime
    负责取消和等待所属 Operation

Loommux Application
    通过 cleanup 释放 KernelRegistry

Loommux KernelController
    负责关闭 Jupyter channel、终止进程并清理 private root
```

HoloVar 负责确保清理一定被调用、取消有界、任务不会遗失；Loommux 负责清理动作本身正确。

容量也一样：

```text
HoloVar:
    max operations
    journal capacity
    subscriber capacity
    task cleanup timeout

Loommux:
    max kernels
    max concurrent kernel starts
    per-kernel execution concurrency
    retained output bytes
    image resource limits
    child process policy
```

不能因为二者都叫“资源上界”，就让 HoloVar接管 Kernel 配额。

**MCP Adapter 放在哪里**

具体的 Loommux MCP 工具必须留在 Loommux。因为它们的 schema、文案、结果投影和行为全部带有 Loommux 领域语义。

HoloVar可以拥有一个抽象的 transport-neutral Runtime port，例如提交 Operation、读取 Snapshot、订阅 Event、取消 Operation。它不需要知道 FastMCP。

如果以后多个 HoloVar 应用都需要一个通用 MCP 表面，可以单独形成 HoloVar MCP Adapter 包。但即使如此，它也只能泛化为：

```text
submit operation
read snapshot
observe events
cancel operation
```

它不能生成 Loommux 的八工具，也不能决定 `Default` Kernel 的隐式选择规则。

Loommux 的 MCP 层应调用 Loommux Application Service；Application Service 再通过 HoloVar Runtime 提交和观察 Operation。这样 MCP 不再拥有世界，但依然完整保留自己的产品语言。

**更准确的整体结构**

现在我认为双方的关系应当表达为：

```text
HoloVar
├── Application Runtime
├── Operation lifecycle
├── Coordinator / Event sequence
├── Snapshot / Cursor / Subscription
├── Client recovery
├── Task and process ownership
├── authentication / authorization ports
└── transport-independent runtime contract

Loommux
├── HoloVar Application declarations
├── LoommuxRuntime domain service
├── KernelRegistry
├── KernelController
├── Execution records and counters
├── Jupyter/IPython process integration
├── output and image projection
├── Default Kernel rules
├── eight MCP tools
└── loommux_runtime Python control surface
```

这里的 `LoommuxRuntime` 是 Loommux 领域服务，不应和 HoloVar `Runtime` 混为同一个类。

为了术语清晰，可以把它们理解为：

```text
HoloVar Runtime
    运行应用、提交事实、拥有任务的通用 Runtime。

Loommux Control Plane
    管理 Kernel 与 Execution 的领域控制面。
```

HoloVar Runtime 承载 Loommux Control Plane 的 Operations，但不吸收其业务对象。

**最终结论**

HoloVar 确实应该配合 Loommux，但这种配合不是把 KernelRegistry、Execution 或 MCP 工具移入 HoloVar。那会污染 HoloVar，而且会让它从清晰的 Operation Runtime 变成什么都想管理的共享对象框架。

Loommux 对 HoloVar最有价值的反馈，是揭示三个通用问题：

1. Runtime 的所有权需要能够独立于单一 Transport Adapter。
2. 长期 Operation 可能需要接受后续的、经过授权和排序的语义干预。
3. 多参与者系统需要比单一 Principal 更清楚地区分参与者、客户端和委托关系。

其中第一项已经能从当前 HoloVar 代码中确认是真实的组合边界；后两项则属于值得继续做理论建构的 HoloVar 候选能力，但不能在没有稳定对象定义和不变量之前进入框架核心。

Kernel、Default、八方法、execution counter、Jupyter lifecycle、output logs 和 Runtime World 则明确属于 Loommux。HoloVar只让这些业务动作获得稳定身份、生命周期、事件顺序、共享观察和完整关闭。

这样分开以后，两个项目的关系不是“上层项目把需求倒灌进底层框架”，而是：

> HoloVar 提供无业务的运行法则；Loommux 在这些法则中建立一个面向 coding agent 的计算领域。
</Taylor>

<Miran id="8">
好的，你的回答有点精彩，

然后是关于 “Loommux 暴露出的 HoloVar 真缺口” 这是你上一轮回答里面，你在这里所提到的这 3 个，哎，我觉得都可以啊。好，那这个可能就会视为明天或后天 HoloVar 开发团队的任务。那我需要请你再去做一做这个概念、理论之类的工作啊，把它们再去想一想，哎，理清楚啊，详细的再去和我聊聊这些东西。像比如说这个，比如说第一个嘛，这个装配所有权，哎，这个其实就很实用，而且它几乎马上就会被用到。

然后呢，关于这一个第二个，你说一个 operation 是吧？哎，能不能进行一些干预？这个也是需要的。像比如说 Agent Runtime 运行时，大语言模型在说话的时候，是吧？给它一个中断，或者说我要插话，是不是？啊，这里也是会用上的啊，这个是要用到的。也就是说，像比如说 Agent Runtime 这里就已经会用到了，也就是啊？某一个这个产品，是吧？也会有依赖到这里，然后呢，关于这个第三点，其实什么认证呀、授权呀，就是安全模型，一般都是另外一整套设计的。那你自己看嘛，Holovar  是吧？它自己的位置在哪里？对吧？它提供的是什么？是槽位吗？还是什么？ 反正就是那套道理嘛，就是啊，摆正自己的位置，对吧？摆做自己，摆正自己在架构上的位置，以及呢依赖关系。好的，那这个东西你自己再看，要不要再去调查？还是说能够直接回答？那总而言之，你准备好之后就来和我分享一下，不少于 2000 字或者更多，开始吧。
</Miran>

<Taylor id="8">
**Planning deep auth and intervention review**

我会再把这三个缺口放回 HoloVar 已有的不变量中检验，尤其避免把“有用”直接等同于“应进入核心”。我会重点确认：宿主与适配器的所有权关系应如何成为通用合同；运行中 Operation 接受干预时，意图、事实、并发与终态怎样不被破坏；身份系统中 HoloVar 应提供哪些调用位置，而不自行变成认证产品。

**Inspecting Principal fields**

**Defining intervention concepts and terminology**

**Designing robust intervention lifecycle and handling**

**Clarifying intervention acceptance and completion semantics**

**Defining intervention versus interaction distinctions**

**Designing host ownership and lifecycle hierarchy**

**Outlining identity and authorization integration**

**Defining principal and actor roles**

**Clarifying HoloVar identity and authorization roles**

**Confirming use of code-like contracts**

这三个问题放回 HoloVar 当前的正式定义后，重要性和成熟度并不相同。

我的总体判断是：

- **装配所有权**已经是一个边界明确、可以进入 HoloVar 正式设计的缺口。
- **运行中 Operation 的干预**同样属于 HoloVar，但它会扩展 Operation 的基本语义，必须先建立完整对象模型，不能只增加一个 `send()` 方法。
- **身份与授权**需要收窄表述。HoloVar 应当提供身份进入、策略判定和来源传播的槽位，但不拥有完整安全模型，也不应定义主控 Agent、Sub Agent、组织、角色和委托体系。

这三者分别对应：

```text
谁拥有世界
怎样在世界运行期间继续行动
谁有资格执行这些行动
```

它们共同构成多参与者 Runtime 的基础，但各自的责任边界不同。

---

**一、装配所有权不是便利 API，而是 Runtime 身份问题**

HoloVar 当前已经区分了三种生命周期角色：

```text
standalone host
temporary owner
attached client
```

这是很好的基础。临时 CLI 可以拉起服务并通过所有权管道控制它的寿命；浏览器和其他客户端可以连接同一个服务，但关闭这些附着客户端不会关闭 Runtime。

问题出现在进程内部的装配边界。

当前 [create_http_app()](/home/t103o/workbench/projects/holovar/src/holovar/asgi.py:82) 在内部直接执行：

```python
runtime = application.runtime(...)
```

然后 FastAPI lifespan 进入和关闭这个 Runtime。这意味着当前的实际结构是：

```text
create_http_app
└── creates Runtime
    └── ASGI lifespan owns Runtime
```

对于只有 HTTP/SSE 的应用，这个结构完全成立。但 HoloVar 的概念承诺是“一个 Application，多种 Frontend”。当 Loommux 同时需要：

- HTTP/SSE；
- MCP；
- Python Runtime World；
- 本地终端控制；
- 可能还有桌面宿主；

问题就会变成：这些 Adapter 如何进入同一个 `runtime_id`，而不是各自创建一个 Runtime Epoch？

真正应建立的结构是：

```text
Application Definition
        |
        v
Application Host
        |
        +-- owns one Runtime Epoch
        |
        +-- HTTP Adapter
        +-- MCP Adapter
        +-- Python Adapter
        +-- Terminal Adapter
```

这里要严格区分四个对象。

`Application` 是声明集合。它包含冻结的 Operation Definition，但没有正在运行的任务。

`Runtime` 是权威运行代际。它拥有 Operation、Coordinator、Store、Journal、Task Group 和 Subscription。

`Host` 是 Runtime 生命周期的唯一拥有者。它决定何时进入 Runtime、何时停止准入、何时关闭任务和何时结束进程。

`Adapter` 是一种进入 Runtime 的协议表面。它可以提交、查询和观察，但默认不拥有 Runtime。

最核心的不变量应当是：

> 一个 Runtime Epoch 在任意时刻只有一个生命周期所有者，但可以存在多个附着 Adapter 和任意数量的客户端。

这解决的不是代码复用，而是权威身份。如果 HTTP Adapter 和 MCP Adapter 分别创建 Runtime，那么即使它们承载相同 Application Definition，也会得到两个 `runtime_id`、两个 Event Sequence、两个 Operation Store。它们不是同一个世界。

因此，多 Adapter 共享不能靠“传入相同配置”实现，只能靠“引用同一个已启动 Runtime”实现。

**Runtime 的三种装配关系**

我认为 HoloVar需要明确三种关系：

```text
owned
borrowed
factory-owned
```

`owned` 表示 Host 已经拥有一个 Runtime，并把它借给 Adapter。Adapter 不得进入、退出或关闭它。

`borrowed` 是从 Adapter 视角描述同一关系：它只使用 Runtime，不改变 Runtime 生命周期。

`factory-owned` 表示一种便利装配：调用者没有提供 Runtime，由单一 Host 在自己的生命周期内创建一个。当前 `create_http_app()` 基本就是这种模式，只是“Host”与“HTTP Adapter”尚未在概念上分开。

无论具体 API 最终叫什么，都需要禁止两个危险行为：

1. 同一个 Runtime 被两个 lifespan 重复 `__aenter__()`。
2. 某个 borrowed Adapter 在退出时关闭共享 Runtime。

当前 HoloVar Runtime 已经规定一个实例只能进入一次，这是正确的底层保护。但装配层还需要把这条限制表达成公开所有权合同。

**Ready 与 Shutdown 的意义**

多 Adapter 环境下，Ready 也不能只表示 Uvicorn 已经监听端口。

一个 Host 的 Ready 应表示：

```text
Runtime Epoch 已建立
Registry 已冻结
Task ownership scope 已生效
所有必需 Adapter 已完成装配
各公开入口已经引用同一个 Runtime
```

关闭时则需要保持相反的责任顺序：

```text
停止新的外部准入
结束或排空 Adapter 请求
关闭 Runtime，取消并等待其 Operation
执行 Application 资源清理
关闭剩余观察连接
结束 Host
```

这里不是要 HoloVar 变成通用进程编排器。它只需要清楚表达：谁拥有 Runtime，谁只是使用 Runtime，以及一次宿主关闭何时可以宣称完成。

HoloVar也不需要内置 FastMCP。MCP Adapter 依然属于 Loommux。HoloVar只需允许 Loommux 把这个 Adapter 绑定到一个外部已拥有的 Runtime，而不是被迫让 Adapter 创建 Runtime。

---

**二、Operation 干预不是第二次调用，而是一种新关系**

HoloVar 当前 Operation 的基本形状是：

```text
一个初始 Command
        |
        v
accepted -> running -> terminal
```

运行以后，公共控制行为主要只有：

```text
observe
wait
cancel
```

这适合媒体转换、构建任务和普通长时业务调用。但 Agent Runtime、设备控制、人工审批和交互式计算会出现另一种需要：

> Operation 已经开始运行，参与者仍然需要向它提交新的、有语义的输入。

你举的大语言模型生成期间的情况很典型：

- 立即终止整个 Agent Operation；
- 中断当前模型生成，但保留 Agent Session；
- 插入一句人类消息；
- 修正当前目标；
- 要求暂停；
- 回答 Agent 提出的选择；
- 拒绝即将执行的危险工具；
- 将控制权从 Agent 转给人类。

这些行为不能全部压成 `cancel()`。取消的语义是让 Operation 进入终态；“插话”可能只是改变 Operation 后续行为，Operation 本身仍继续运行。

因此需要区分：

```text
Cancellation
    请求整个 Operation 走向 cancelled 终态。

Intervention
    向仍然存在的 Operation 提交一个运行时输入。

Interaction Response
    回答由 Operation 先前创建的、面向参与者的请求。
```

其中 Cancellation 可以继续作为 HoloVar 的通用内建语义。Intervention 则应由应用声明具体类型。HoloVar不应内置：

```text
agent.barge_in
kernel.interrupt
device.pause
approval.accept
```

但它可以提供一个通用的 Intervention 合同，让这些领域动作获得相同的身份、验证、排序和恢复语义。

**Intervention 需要成为正式对象**

最危险的设计是直接给 `OperationContext` 增加：

```python
await context.receive()
```

然后让 HTTP 或 MCP 请求往一个内存 Queue 里塞值。

这样虽然能运行，却没有回答：

- 输入是否已经被 Runtime 接受；
- 输入有没有稳定身份；
- 客户端重试会不会重复插话；
- Operation 在输入到达同时完成怎么办；
- 输入已经接受但尚未应用时，Snapshot 显示什么；
- Runtime 关闭时未处理输入怎样结束；
- 哪个 Principal 提交了输入；
- 观察者如何知道输入被拒绝、应用或忽略；
- Queue 满了以后发生什么。

因此 Intervention 不能只是 Queue Message。它至少应具有：

```text
intervention_id
runtime_id
operation_id
type
payload
principal
correlation_id
accepted_sequence
status
```

它的状态不应直接复制 Operation 生命周期，但需要有确定结局，例如：

```text
submitted
accepted
applied
rejected
expired
superseded
unapplied
```

具体名称可以继续推敲，关键是区分两个事实：

```text
intervention.accepted
    Runtime 已验证并承担交付责任。

intervention.applied
    Operation 已经将其纳入自己的业务状态。
```

这与我们之前强调的：

```text
cancel.requested != operation.cancelled
```

是同一条语义纪律。

人类点击“停止”只是意图。Runtime 真正完成取消和清理后，`operation.cancelled` 才成立。

人类发送一句插话，只说明 Runtime 接受了输入。Agent 是否已经停止当前生成、是否把它加入对话、是否拒绝了不适用的输入，都需要后续事实表达。

**Intervention 的声明边界**

HoloVar 的 Operation Definition 已经声明：

- Command Model；
- Business Event Models；
- Projection Model；
- Result Model；
- Handler；
- cancellable；
- timeout。

如果引入 Intervention，最自然的理论位置是由 Operation Definition 声明该类 Operation 在运行期间接受哪些输入类型。

例如概念上可以是：

```text
Operation Definition
├── initial Command
├── Intervention Definitions
├── Business Event Definitions
├── Projection
└── Result
```

应用定义：

```text
接受什么输入
Payload 模型是什么
哪些状态允许接受
该输入的业务含义是什么
```

HoloVar负责：

```text
验证
身份记录
授权调用
幂等
顺序
有界交付
待处理状态
最终 disposition
恢复
```

这仍然符合 HoloVar 的责任原则：业务定义意义，Runtime维持跨业务不变量。

**干预与 Operation 并发**

干预最困难的部分不是 API，而是终态竞态。

假设 Operation 正在生成文本，同时发生：

```text
sequence 41: intervention.accepted
sequence 42: operation.completed
```

这时 Intervention 可能已经进入队列，但 Handler 尚未应用它。系统不能声称它已生效。

因此必须有一个原则：

> 每个已经接受的 Intervention 最终都必须产生一个确定 disposition，不能永远停留在“也许 Handler 看到了”。

若 Operation 先进入终态，尚未应用的 Intervention 可以变成：

```text
intervention.unapplied
reason = operation_terminal
```

若两个修改目标的 Intervention 冲突，后一个可以被拒绝或前一个被 supersede，但必须由应用的并发策略决定。

若客户端携带对象版本或 Projection 版本前置条件，Runtime可以在接受边界拒绝已经过时的干预。这适合：

- 用户编辑过时参数；
- 两个 Agent 同时接管；
- 对已经改变的审批请求提交旧答案。

因此 Intervention 还自然连接到 optimistic concurrency，但 HoloVar不应替应用决定冲突时“人类优先”还是“先到先得”。框架只提供版本前置条件和确定拒绝表面。

**有界性同样成立**

每个 Operation 的 Intervention Inbox 必须有容量。否则一个快速客户端可以向慢 Handler 无限提交输入。

达到容量时，HoloVar 应拒绝新 Intervention，而不是让生产者无限等待，也不能静默丢弃已经接受的输入。

这与 Subscription 的慢消费者隔离是同一种 HoloVar 原则：

> 一旦 Runtime 对调用者确认接受，就必须承担确定处理或确定终结的责任；无法承担时，应在接受之前明确拒绝。

---

**三、HoloVar 在安全架构中的位置**

这里我同意你的判断：认证、授权、组织关系、策略管理和密钥体系通常是一整套独立设计。HoloVar 不应该变成 IAM、OAuth Server 或权限产品。

HoloVar 的正确位置不是“提供安全模型”，而是提供三个东西：

```text
Identity ingress
Policy enforcement points
Authority provenance
```

可以分别翻译为：

- 身份从哪里进入 Runtime；
- 哪些行为发生前必须询问策略；
- 已提交事实如何保留行动来源。

**身份进入槽位**

当前 `AuthenticationProvider` 的方向是正确的。HTTP Adapter 从 Bearer Header 或 Cookie 取得凭据，然后调用 Provider，得到：

```python
Principal(
    principal_id=...,
    display_name=...,
    claims=...,
)
```

HoloVar不负责签发企业身份，不负责保存用户密码，也不负责决定 OAuth、OIDC、mTLS、Unix 用户或 Agent Token 哪种方式正确。

AuthenticationProvider 是一个翻译边界：

```text
外部凭据体系
        |
        v
HoloVar Principal
```

对于 MCP，身份可能来自：

- MCP Host 明确配置；
- 本地子进程所有权；
- HTTP Authorization；
- 外部反向代理已经验证的身份；
- Agent Runtime 签发的 delegation token。

这些都不应进入 HoloVar Core。对应 Adapter 取得凭据后，调用相应 AuthenticationProvider，最终产生统一 Principal。

**策略判定槽位**

当前 `AuthorizationPolicy` 已经有：

```text
can_submit
can_view
can_subscribe
can_cancel
```

这个思路同样正确，但随着 Intervention 出现，需要把授权动作扩展到新的权威行为，例如：

```text
can_intervene
can_respond
```

如果以后 Application 增加领域查询或 Artifact 写入，这些策略仍应由应用或安全集成层定义，不能靠 HoloVar Core 猜测。

HoloVar 的职责是保证所有通路都经过同一判定点。例如：

```text
HTTP 提交 Operation
MCP 提交同一 Operation
Python Client 提交同一 Operation
```

不能只有 HTTP 路径调用 `can_submit`，而进程内 Python Adapter 可以直接绕过授权。

这揭示出当前设计还需要澄清的一点：Authorization 现在主要装配在 ASGI Adapter 中，Runtime 的 `submit()` 本身接受一个 Principal，却不会调用 AuthorizationPolicy。

这在“Runtime 是受信任内部端口，Adapter 负责授权”的模型下成立。但多 Adapter 环境中，必须明确哪一层是强制执行点。

我更倾向于区分：

```text
Trusted Application Port
    供已完成授权的内部代码调用。

Untrusted Adapter Port
    必须完成认证和授权后才能进入 Application Port。
```

HoloVar不一定要把 AuthorizationPolicy 塞入最内层 Coordinator，但必须防止每个 Adapter 自己发明一套不同判定。可以由共享 Application Service 统一执行策略，再把已授权意图交给 Runtime。

核心原则是：

> HoloVar 提供强制执行位置和统一动作词汇；外部安全系统与应用策略提供判定结果。

**HoloVar 不应拥有的安全概念**

下面这些通常不应直接进入 HoloVar Core：

```text
Human
Principal Agent
Sub Agent
Team
Organization
Role hierarchy
Kernel quota policy
Human always wins
Manager approval chain
Delegation token issuance
OAuth flow
Password storage
Session federation
```

这些属于业务、安全系统或部署集成。

主控 Agent 能否把 Kernel 分配给 Sub Agent，是 Loommux 的资源授权策略。

Sub Agent 是否由某个主控 Agent 委托，是 Agent Runtime 或安全系统的身份关系。

HoloVar只需要接收一个可以参与判定的身份表示，并将其稳定地带入 Operation、Intervention、Event、日志与错误关联。

`Principal.claims` 已经为外部身份信息保留了扩展空间。不能因为存在 `claims`，就让 HoloVar开始解释组织角色和 Agent 隶属关系。

**Participant、Principal 与 Client 还要不要区分**

理论上仍然需要区分，但不一定都成为 HoloVar 核心模型。

`Principal` 是安全意义上的主体。它回答“授权决策针对谁”。

`Client` 是一次具体的软件接入实例。它回答“这个动作从哪个浏览器、终端或 Agent 进程进入”。

`Participant` 是业务协作意义上的参与者。它可能是人、Agent、团队角色或者自动服务。

在 HoloVar 中，`Principal` 是必要对象；`client_id` 可以作为协议来源和审计坐标，但不应自动被视为可信身份；`Participant` 则通常应由具体 Application 建模。

例如 Agent Runtime 可以定义：

```text
participant_id = agent_17
principal_id = service_account_agent_runtime
client_id = codex_process_42
```

安全系统可能只认证了一个 service account，但业务需要知道具体是哪个 Agent 参与了协作。HoloVar不应假装二者相同。

因此第三个缺口经过收敛后，更准确的表述不是“让 HoloVar建立多 Agent 身份模型”，而是：

> HoloVar 应建立稳定、统一且不可绕过的身份进入与策略执行边界，并允许应用携带更丰富的参与者和客户端来源。

---

**三个缺口之间的统一关系**

这三个问题最终可以汇合成一条完整的权威路径：

```text
Host
    owns Runtime Epoch

Adapter
    authenticates an external caller

Authorization Boundary
    decides whether the Principal may perform an action

Runtime
    validates and accepts the action

Operation
    owns the running business task

Intervention Inbox
    carries accepted runtime inputs

Coordinator
    commits intent, state and outcome facts

Subscription
    exposes those facts to every authorized observer
```

其中没有任何一层需要知道“这是 GPT 还是 Claude”，也不需要知道“目标是 Kernel 还是设备”。

Agent Runtime 可以用它表达插话和接管；Loommux 可以用它表达对 Kernel Session 的干预；设备控制应用可以用它表达暂停和参数修改；人工审批系统可以用它表达批准和拒绝。

这正是一个能力应进入 HoloVar 的证据：对象语义保持稳定，而业务词汇全部留在应用层。

**最后判断**

装配所有权应当进入 HoloVar，因为“一次 Runtime Epoch 只有一个生命周期所有者、多个 Adapter 只能附着”是跨业务运行时不变量。

Operation Intervention 也应进入 HoloVar 的理论设计，因为它补全了长期 Operation 从“只能被启动和取消”到“可以被持续协作”的缺失维度。但它必须是有身份、有顺序、有容量、有最终 disposition 的正式运行对象，而不能只是 Handler 边上的 Queue。

安全方面，HoloVar应克制。它提供 Authentication 和 Authorization 的端口、统一的 enforcement boundary、Principal 传播、可见性一致性和来源记录；身份签发、组织角色、Agent 委托、资源配额和业务优先级由外部系统与具体 Application 定义。

这样，HoloVar的位置会非常清楚：

> 它不决定谁应该拥有权力，也不决定一次干预在业务上意味着什么；它保证已经建立的身份、策略决定、行动意图和运行结果，在同一个 Runtime 中以一致、可观察、可恢复的方式成立。
</Taylor>

<Miran id="9">
好的，那你这个这次回答好像也还可以，但是很多东西你也没有想明白。你反复说什么名字要不要推敲吗？没什么好推敲的。符合直觉就行了，符合现有的风格就行了。然后呢，这些是什么？这些是推导呀。啊，这里没有一个什么产品经理站在你说，哎呀，我要苹果，我要香蕉。我跟你讲，你东西在这里就确定在这里了，所有东西完整的设计出来。不是说摇摆不定，没什么好摇摆不定，这不是你熟悉的什么操作系统啊、进程啊、调度啊、并发呀、通信呀什么什么的。你做的是设计，设计是这样子的，因为它是什么东西，所以它有什么东西，所以我们要设计什么东西。 你需要说，它不是苹果，不是香蕉，不是梨子，不是麦克风，不是电脑，不是马里亚纳海沟，也不是喜马拉雅雪山。你就是这么说的，有意义吗？ 你想吃一口喜马拉雅雪山是什么味道的吗？ 🤣  ， 因为你是什么？啊？所以你需要承担什么？所以你才是这个形状。所以啊，我们对你的期望是这个。所以我们约定双方的承诺是这样子的。啊？你需要按照啊规范正确的方法做来操作我，我也会承诺。你不按照约定，那么我也不会遵守你的约定，就是这样子的。啊，因为你前面这里也有提到过一些双方的约定，调用者啊。与这个被使用库的一些约定。  而你这里还提到一个什么什么什么最困难，最最最还什么英英语的什么最高级。你管它最不最困难呢？你当前是什么状态？期望是什么什么状态？在什么场景下啊？要达到什么什么样的效果？达到这个效果，它就必须解决这些问题。那管你什么困难不困难，你缺一个不就不达到吗？比如说我来一个抽象的比喻，希腊字母我们阿尔法型或德尔塔型或者贝塔型。我们想要达到这种，比如说阿尔法型，就得完成啊这个 123 三个目标，你缺一个，那就是没有。那我不管你一简单、二简单还是三困难。没有意义啊，你讨论这些有意义吗？你是说你到底有没有把这个啊？承诺讲清楚呢？如果说你不知道它是什么，那你根本不知道讲清楚。如果说你不知道它是什么，那你就设计出来，那你就说你不会设计就好了。  什么时候会去澄清？会说啊，苹果不是梨子。什么时候会这么说？只有这么这么点点情况。

第一种情况，有个真实的人睁大眼睛对着你的耳朵说啊，你好呀，你是克劳德吗？你会说我不是克劳德，这是在使用系统功能语法的人际关系。

第二种情况，像鲁迅一样抨击这个社会的刻板印象，就是攻击，就是攻击社会上的这种庞大的群体，就是攻击才会说啊，一些否定、不是 xx、指控 xx。

那么你呢？你在使用这些时候，你是这些情况吗？你是怎么做的？ 好好想想吧啊，想好之后呢，哎结合之前所聊的东西来回答我吧。开头反思 1000 字，啊回答 2000 字，啊或者更多，开始吧。
</Miran>

<Taylor id="9">
**Planning detailed reflection and response**

我先按你指出的问题重建表达：删掉无效排除、难度评价、候选命名和实施姿态，只保留对象定义、状态、场景、期望效果、不变量以及双方承诺。随后我会把三个能力写成确定的 HoloVar 设计，而不是继续列可能性。

**Deciding pillar naming strategy**

**Defining Intervention lifecycle states**

**Refining intervention status model**

**Designing runtime intervention storage and API**

**Defining atomic apply and reject intervention APIs**

**Drafting host contract and API design**

**Refining host and runtime ownership design**

**Designing ApplicationGateway for Authorization**

**Refining AuthorizationPolicy and Snapshot Consistency**

**Designing authorization revision tracking**

**Planning structured, clear reflection draft**

**反思**

你指出的是我上一轮回答的结构性问题，不只是措辞问题。

我当时没有先把对象完整定义出来，而是在定义尚未闭合时不断插入“它不是什么”“也许叫什么”“是否应该进入核心”“这件事比较困难”等旁支。那些句子看似谨慎，实际上没有推进对象的确定性。它们把本应由设计承担的判断，重新包装成尚待讨论的可能性。

设计工作的基本论证应当是：

```text
对象是什么
→ 它处于什么环境
→ 环境对它提出什么要求
→ 它必须维持哪些状态与关系
→ 因而它必须承担哪些能力
→ 使用者与它分别承诺什么
```

我上一轮没有始终保持这条推导链。比如谈 Operation 干预时，我先谈“名字还可以推敲”，又谈“最困难的是终态竞态”。这两个表述都偏离了对象本身。

名称的职责是稳定指称对象。既然一个外部参与者在 Operation 运行期间向它提交新的语义输入，这个对象叫 `Intervention` 已经符合 HoloVar 使用 `Command`、`Operation`、`Event`、`Projection`、`Result` 的命名风格。继续讨论名称没有产生新的语义。

竞态也不是因为“困难”才需要设计。Operation 进入终态与 Intervention 被接受可能同时发生，这是对象环境中的必然场景。只要系统承诺每个已接受 Intervention 都有确定结局，就必须建立能够裁决该场景的提交边界。它是否困难与契约无关。

我还过度使用了否定式边界说明。边界说明只有在一个高概率误解会破坏合同的时候才有必要。比如必须说明“Adapter 关闭不等于 Runtime 关闭”，因为如果调用者把附着关系误认为所有权关系，就会错误终止共享世界。除此之外，大量列举 HoloVar 不负责的邻近对象，只是在展示联想范围，没有定义 HoloVar 自己。

更根本的问题是，我仍然带着一种“需求尚未确定、等待产品经理选择”的姿态。这里的工作并不是从苹果、香蕉之间选一个需求，而是从 HoloVar 已经成立的本体继续演绎。HoloVar 是单进程 Application Runtime；它承诺把持续业务调用组织为共享可观察的 Operation。既然同一个 Runtime 需要被多种 Adapter 使用，它就必须具有独立于 Adapter 的生命周期所有权。既然 Operation 是持续实体而非一次同步函数调用，它就必须能够在运行期间接受受约束的后续输入。既然多个参与者能够行动，所有公开行动就必须经过身份建立、权限判定和来源记录。

这些不是候选功能，而是 HoloVar 达到“一个 Runtime、多参与者、多种 Frontend、持续 Operation”这一形状时缺失的组成部分。

下面我按这个逻辑，把三项设计直接确定下来。

---

# HoloVar 扩展后的对象形状

HoloVar Application Runtime 由五个权威层次组成：

```text
Application
    声明业务能力及其类型合同。

Host
    拥有一个 Runtime Epoch 的开始与结束。

Runtime
    拥有 Operation、Intervention、Event Sequence、任务和状态提交。

Application Gateway
    接受已经建立身份的外部行动，执行授权并调用 Runtime。

Adapter
    把 HTTP、MCP、CLI、Python 等协议映射到 Application Gateway。
```

数据方向是：

```text
Caller
  |
  v
Adapter
  |
  | authenticate
  v
Principal
  |
  v
Application Gateway
  |
  | authorize
  v
Runtime
  |
  +-- Operation
  +-- Intervention
  +-- Snapshot
  +-- Event
  +-- Subscription
```

生命周期方向是：

```text
Process Owner
  |
  v
Host
  |
  v
Runtime Epoch
  |
  +-- Operation Tasks
  +-- Intervention Inboxes
  +-- Subscriptions
  +-- Application Cleanups
```

这套结构同时确定装配所有权、运行中干预和安全槽位。

---

# 一、Host 与 Runtime 的装配所有权

## 对象定义

`Host` 是一个 Runtime Epoch 的唯一生命周期所有者。

`Runtime` 是从启动到关闭的一次权威运行代际。它由 `runtime_id` 标识，拥有全局 Event Sequence、Operation Store、Event Journal、Task Group 和 Subscription Hub。

`Adapter` 是 Runtime 的协议入口。Adapter 接受某种协议输入，将其转换为 Application 行动，并将 Runtime 事实投影回该协议。

因此三者的关系是：

```text
Host owns Runtime
Adapter borrows Runtime
Client connects through Adapter
```

## 唯一所有权承诺

同一个 Runtime Epoch 只有一个 Host。

Host 承诺：

1. 只进入 Runtime 一次。
2. 在 Runtime 已进入运行作用域后公开 Ready。
3. 在关闭时停止新的公开准入。
4. 等待 Runtime 完成 Operation 取消、清理和终态提交。
5. 在 Runtime 完整关闭后释放进程级资源。
6. 不在 Runtime 关闭完成前宣告 Host 已停止。

Runtime 承诺：

1. 进入后接受合法 Operation。
2. 关闭开始后拒绝新 Operation 和 Intervention。
3. 拥有所有 Operation 任务。
4. 为非终态 Operation 建立确定终结。
5. 关闭所有 Subscription。
6. 执行已注册的 Application Cleanup。
7. 关闭完成时不再持有活动任务和应用资源。

Adapter 承诺：

1. 只通过指定的 Application Gateway 使用 Runtime。
2. 不进入 borrowed Runtime。
3. 不关闭 borrowed Runtime。
4. 在 Host 停止准入后不再创建新行动。
5. 将连接关闭与 Runtime 关闭分开表达。

这是一组双向合同。Host 如果提前销毁 Runtime，Adapter 无法履行持续观察合同；Adapter 如果越权关闭 Runtime，Host 也无法履行对其他 Adapter 和客户端的可用性承诺。

## 两种装配形式

HoloVar 提供两种确定装配形式。

### Host-owned

Host 创建 Runtime，并把同一个 Runtime 交给所有 Adapter。

```text
Host
├── Runtime rt_42
├── HTTP Adapter  -> rt_42
├── MCP Adapter   -> rt_42
└── Python Port   -> rt_42
```

所有 Adapter 观察同一个 `runtime_id` 和同一条 Event Sequence。

这是多 Adapter 应用的标准形状。

### Adapter-hosted convenience

单一 Adapter 可以同时承担 Host 角色。它创建 Runtime，在自身 lifespan 中进入和关闭 Runtime。

当前 `create_http_app(application)` 就属于这种便利形式。它适合单一 HTTP 宿主。

其合同必须明确：

```text
Adapter-hosted
=
该 Adapter 显式承担 Host 责任
```

一旦调用者提供外部 Runtime，Adapter 自动进入 borrowed 关系，不再拥有其生命周期。

因此装配语义不需要 `runtime_ownership="borrowed"` 这样的字符串开关。所有权由构造关系直接决定：

```python
create_http_app(application)
```

表示 HTTP App 自行托管一个 Runtime。

```python
create_http_app(application, runtime=runtime)
```

表示 HTTP App 使用调用者提供的 Runtime。

显式对象关系已经完整表达所有权，不再需要第二套声明覆盖第一套关系。

## Ready 状态

Host 进入 Ready 必须同时满足：

```text
Application Registry 已冻结
Runtime Task Group 已建立
Runtime Epoch 已分配
Application Gateway 已绑定
必需 Adapter 已绑定同一 Runtime
公开监听入口已经可接收请求
```

Startup Message 中的 `runtime_id` 来自 Host 拥有的 Runtime。所有 Adapter 的健康状态都必须报告同一个 `runtime_id`。

这使调用者能够验证自己进入的是同一个共享世界，而不是仅仅连接到名称相同的 Application。

## Shutdown 状态

Host 关闭按照权威关系收束：

```text
ready
  -> stopping_admission
  -> closing_runtime
  -> closing_adapters
  -> stopped
```

`stopping_admission` 阻止新的 Command 和 Intervention 被接受。

`closing_runtime` 取消并等待 Operation，裁决 pending Intervention，执行 Cleanup，关闭 Subscription。

`closing_adapters` 释放协议连接、监听 Socket 和 Adapter 私有资源。

`stopped` 表示 Host、Runtime 和 Adapter 均已完成各自拥有的关闭责任。

---

# 二、运行中 Operation 的 Intervention

## 对象定义

`Command` 创建 Operation。

`Intervention` 是 Principal 在 Operation 运行期间向该 Operation 提交的一次类型化语义输入。

二者关系是：

```text
Command
    creates Operation

Intervention
    addresses existing Operation
```

Operation Definition 声明初始 Command，也声明该 Operation 接受的 Intervention 类型：

```text
Operation Definition
├── Command Model
├── Intervention Models
├── Business Event Models
├── Projection Model
├── Result Model
└── Handler
```

Agent Runtime 可以声明：

```text
agent.message
agent.interrupt_generation
agent.pause
agent.resume
agent.approval_response
```

Loommux 的长期 Kernel Operation 可以声明：

```text
kernel.execute
kernel.interrupt
kernel.restart
kernel.destroy
```

这些名称和 Payload 由 Application 定义。HoloVar定义 Intervention 的公共信封、生命周期、提交边界和交付承诺。

## Intervention Record

每个被接受的 Intervention 具有独立身份：

```text
runtime_id
intervention_id
operation_id
type
payload
principal
correlation_id
idempotency_key
accepted_sequence
status
reason
created_at
completed_at
```

`intervention_id` 在当前 Runtime Epoch 内稳定。

完整身份由：

```text
(runtime_id, intervention_id)
```

构成。

Intervention 的 Event 继续使用全局坐标：

```text
(runtime_id, sequence)
```

## Intervention 生命周期

Intervention 生命周期确定为：

```text
pending
  -> applied
  -> not_applied
```

`pending` 表示 Runtime 已经验证输入、提交 Intervention Record，并承担将其交付给目标 Operation 的责任。

`applied` 表示 Operation Handler 已经把该输入纳入业务状态，并提交了由该输入产生的 Projection 与 Business Event。

`not_applied` 表示该输入具有确定的未应用结果。`reason` 说明原因：

```text
operation_terminal
business_rejected
superseded
expired
withdrawn
runtime_shutdown
```

`applied` 与 `not_applied` 是终态。

这个状态机刻意保持简单。业务差异进入 Event Payload 和 `reason`，不扩张基础生命周期。

## 接受边界

Intervention 提交必须携带：

```text
runtime_id
operation_id
type
payload
Principal
可选 idempotency_key
可选 expected_revision
```

Runtime 在同一个 Coordinator 提交边界内完成：

1. 验证 Runtime Epoch。
2. 验证目标 Operation 存在。
3. 验证目标 Operation 仍处于可干预状态。
4. 验证 Intervention Type 已由 Definition 声明。
5. 验证 Payload。
6. 验证授权已经通过。
7. 检查幂等键。
8. 检查目标 Inbox 容量。
9. 创建 pending Intervention Record。
10. 分配 Event Sequence。
11. 追加 `intervention.accepted` Event。
12. 将 Intervention 放入目标 Operation 的 Inbox。

成功响应意味着以上事实已经共同成立。

任何检查失败都发生在接受之前，不分配 `intervention_id`，不产生 `intervention.accepted`。

## Handler 合同

`OperationContext` 增加两个运行能力：

```python
intervention = await context.receive_intervention()
```

```python
await context.apply_intervention(
    intervention,
    event_type=...,
    payload=...,
    projection=...,
)
```

`receive_intervention()` 按 accepted Sequence 返回下一个 pending Intervention。

`apply_intervention()` 在同一个提交边界内：

1. 确认 Intervention 仍为 pending。
2. 验证 Business Event Payload。
3. 验证新 Projection。
4. 更新 Operation Projection。
5. 将 Intervention 置为 applied。
6. 提交 Business Event。
7. 提交 `intervention.applied`。
8. 推进 Cursor。

业务拒绝使用：

```python
await context.reject_intervention(
    intervention,
    reason="business_rejected",
)
```

它将 Intervention 提交为 `not_applied`，并产生 `intervention.not_applied` Event。

Handler 承诺持续执行期间建立 Intervention 接收点。HoloVar承诺按接受顺序交付 pending Intervention。HoloVar不承诺任意阻塞的业务代码能够被非取消式 Intervention 强制抢占，因为 Intervention 的业务应用必须由 Handler 在受控边界中完成。

Cancellation 保持 Runtime 级抢占语义；普通 Intervention 保持 Handler 协作语义。

## Operation 与 Intervention 的终态关系

Operation 进入终态时，所有仍为 pending 的 Intervention 在同一个终结过程内变为：

```text
not_applied
reason = operation_terminal
```

Runtime 关闭导致 Operation 取消时，pending Intervention 变为：

```text
not_applied
reason = runtime_shutdown
```

因此每个已接受 Intervention 都有确定结局。

系统不会留下一个永久 pending、但目标 Operation 已经不存在的输入。

## Snapshot 与恢复

`OperationSnapshot` 包含该 Operation 当前保留的 Intervention Snapshot。至少包括：

```text
pending interventions
recent terminal intervention records
```

Event Journal 包含：

```text
intervention.accepted
intervention.applied
intervention.not_applied
```

晚加入客户端先取得 Snapshot，得知哪些 Intervention 正在等待；随后从 Cursor 继续订阅 Event。

断线客户端根据 `(runtime_id, sequence)` 恢复，不会因为重连而重复应用 Intervention。重试提交由 `(principal_id, operation_id, intervention_type, idempotency_key)` 去重。

## Agent 插话的具体语义

在 Agent Runtime 中，大语言模型生成可以是一个持续 Operation。

用户插话时提交：

```text
type = agent.message
payload = {content: "..."}
```

Runtime 接受后形成 pending Intervention。

Agent Handler 同时等待模型输出和 Intervention。收到 `agent.message` 后，它可以：

1. 停止当前模型生成子任务。
2. 保存已产生的部分输出。
3. 将用户消息加入业务 Projection。
4. 启动下一轮模型生成。
5. 原子提交消息 Event、Projection 和 `intervention.applied`。

Operation 本身仍为 running。

用户要求结束整个 Agent Runtime 时使用 Cancellation。取消清理完成后，Operation 进入 cancelled。

这使“插话”和“结束”具有不同的稳定语义。

---

# 三、身份、授权与 HoloVar 的安全槽位

## 架构位置

HoloVar 位于业务 Application 与外部安全系统之间。

外部安全系统建立身份和策略资料。

HoloVar把这些资料带入每一次权威行动，并保证所有公开入口执行相同的策略判定。

结构是：

```text
Credential
  |
  v
AuthenticationProvider
  |
  v
Principal
  |
  v
AuthorizationPolicy
  |
  v
Authorized Application Action
  |
  v
Runtime Commit
```

## AuthenticationProvider 合同

AuthenticationProvider 由 Adapter 调用。

每种 Adapter 从自己的稳定凭据位置取得认证材料：

```text
HTTP       Authorization Header / Cookie
MCP HTTP   Authorization Header
MCP stdio  Host-authored local identity
CLI        Token / local process ownership
Python     Explicit Principal or trusted host context
```

Provider 输出统一 `Principal`：

```text
principal_id
display_name
claims
```

Provider 承诺：

1. 只为已验证凭据返回 Principal。
2. 对认证失败返回无身份结果。
3. 返回的 `principal_id` 在相应身份域内稳定。
4. 不把原始凭据放入 Principal claims。
5. 明确 Principal 的有效期或失效条件。

HoloVar承诺：

1. 未建立 Principal 的公开行动不进入 Application Gateway。
2. Principal 被复制进入 Operation 或 Intervention Record。
3. Event、日志与错误关联使用同一 Principal 坐标。
4. 原始 Token 不进入 Runtime Record、Event 和日志。

## Application Gateway

`Application Gateway` 是所有外部 Adapter 共享的策略执行边界。

Adapter 不能直接调用公开 Runtime mutation API。它提交：

```text
Authenticated Action
=
Principal
+ Action Type
+ Target
+ Input
+ Correlation
```

Gateway 根据行动调用 AuthorizationPolicy：

```text
submit Operation     -> can_submit
view Operation       -> can_view
subscribe Events     -> can_subscribe
cancel Operation     -> can_cancel
submit Intervention  -> can_intervene
withdraw Intervention -> can_withdraw_intervention
```

授权通过后，Gateway 调用 Runtime。

因此 HTTP、MCP、CLI 和 Python Adapter 对同一个行动使用同一个权限判断。协议差异不会产生不同的权力。

## AuthorizationPolicy 合同

AuthorizationPolicy 接收已经认证的 Principal 和完整行动上下文，返回允许或拒绝。

Policy 承诺：

1. 判定只依赖其声明的身份、目标和策略状态。
2. 对同一授权版本下的相同输入给出一致结果。
3. 不直接修改 Runtime 状态。
4. 不在允许返回后自行执行目标行动。
5. 策略变化时推进授权版本。

HoloVar承诺：

1. 在权威提交前完成授权判定。
2. 拒绝不产生 Operation、Intervention 或 Business Event。
3. Snapshot、单对象查询、Replay 与 Live Subscription 使用同一可见性规则。
4. 授权范围变化时发送 `stream.reset`，原因为 `authorization_changed`。
5. Reset 后由客户端重新取得符合新权限范围的 Snapshot。

## Authorization Revision

持续 Subscription 需要一个稳定的可见性边界。授权策略为每个 Principal 提供：

```text
authorization_revision
```

Snapshot 和 Subscription 均绑定该 revision。

当策略系统推进 revision 时，现有 Subscription 不再继续投递基于旧权限范围的 Event，而是收到：

```text
stream.reset
reason = authorization_changed
```

客户端重新认证、取得 Snapshot，并以新的授权 revision 建立 Subscription。

这使权限收缩不会继续泄漏旧可见性范围中的实时事件，权限扩大也不会依靠客户端猜测缺失对象。

## Principal、Client 与 Participant

三者具有确定含义。

`Principal` 是授权主体。AuthorizationPolicy 对它做决定。

`Client` 是一次具体的软件接入实例。它提供 `client_id`，用于关联连接、重试和审计来源。

`Participant` 是 Application 领域中的协作实体。它可以是人、Agent、服务或团队角色，由 Application 定义。

一次行动的来源坐标是：

```text
principal_id
client_id
可选 participant_id
correlation_id
```

HoloVar验证并信任 Principal。

Adapter分配或验证 Client。

Application解释 Participant。

`participant_id` 不能替代 Principal 完成授权；`client_id` 不能自动获得 Principal 的身份权力。

## 双方承诺

调用者承诺：

1. 使用有效凭据建立 Principal。
2. 不伪造由 Adapter 或 Provider 负责的身份坐标。
3. 为可重试 mutation 提供稳定幂等键。
4. 携带所观察 Runtime 的 `runtime_id`。
5. 在收到 Reset 后替换本地 Snapshot。
6. 不把 Intervention accepted 解释为 applied。
7. 不把 cancel requested 解释为 Operation 已 cancelled。

HoloVar承诺：

1. 每个已接受 Command 都产生一个受 Runtime 拥有的 Operation。
2. 每个已接受 Intervention 都产生确定终态。
3. 每次权威变化都获得唯一 Event Sequence。
4. Snapshot 与 Cursor 位于同一个提交边界。
5. Replay 与 Live Subscription 连续。
6. 所有公开 mutation 经过同一授权边界。
7. 所有公开事实携带稳定来源坐标。
8. Runtime 关闭后不保留活动 Operation、pending Intervention 或开放 Subscription。
9. Adapter 关闭只释放自身资源；只有 Host 可以结束 Runtime Epoch。

---

# 最终形状

这三项设计共同把 HoloVar 从“可观察长时调用 Runtime”补全为“可持续协作的 Application Runtime”：

```text
一个 Host
    拥有一个 Runtime Epoch

一个 Runtime Epoch
    拥有多个 Operation

一个 Operation
    由一个 Command 创建
    可接收多个 Intervention
    产生 Projection、Event 与 Result

多个 Adapter
    共享同一个 Application Gateway
    使用同一个 Runtime

多个 Principal
    通过统一授权边界行动
    观察同一个权威 Event Sequence
```

它的完整承诺可以压缩成一句话：

> HoloVar 在一个明确所有者控制的 Runtime Epoch 中，接受经过身份与权限判定的 Command 和 Intervention，将它们组织成具有确定生命周期的 Operation 与运行输入，并以一致提交、全局顺序、可恢复 Snapshot 和有界 Subscription 向多个 Adapter 公开同一组权威事实。
</Taylor>

<Miran id="10">
好的，总体看起来很不错。我手动刚才帮你的话保存成了一份文件，你可以简单阅读开头几行确认一下就可以了。 \\wsl.localhost\Ubuntu\home\t103o\workbench\projects\holovar\docs\holovar-设计1.md 是我发现你那里面可能有一些错误的猜想是吧？或者是假设吧。像比如说插画，用户插一段话，他其实并没有把大语言模型给停了。如果说是停止的话，那用户直接先发停止，再接着说话，那不就可以吗？那为什么还多此一举呢？是不是？啊，你来看一下这一个脚本。啊，它是一种微缩的模型，它是完整的语义哦。啊，各种成熟的、主流的，比如说 Codex、Claude code等agent，团队已经调查过了，他们基本上都是这套模型：\\wsl.localhost\Ubuntu\home\t103o\workbench\experiments\terminal-transcript-agent-demo\transcript_agent_demo.py 就是这一套控制模型。那这也只是一个上层业务而已我只是给你看看参考而已那好好想想吧。那你理解这个吗？这个东西对你来说，对于你之前那些，你又想到了什么吗？

然后呢，对于那一篇设计 1 号文档是吧？你觉得有什么要调整的吗？好好想想吧，现在和我聊聊吧，那不少于 1000 字或者更多。让我了解一下哦。
</Miran>

<Taylor id="10">
**Reviewing initial design document**

我先按你的边界只看设计文档开头，确认保存内容；随后完整阅读这个微缩 Agent Runtime，重点检查它对“插话、停止、当前生成、后续轮次和控制输入”的实际语义。然后我会据此指出设计 1 中哪些定义需要修正。

**Clarifying script semantics and design adjustments**

**Refining intervention handling and queuing logic**

**Designing detailed intervention disposition states**

**Clarifying input modes and queue behavior**

**Defining intervention finalization protocol**

**Specifying intervention resolution responsibility**

**Planning integration of finalizer queue handling**

**Revising agent interruption and routing semantics**

**Confirming operation-specific intervention handling**

**Preparing detailed positive Chinese response**

我确认了 [holovar-设计1.md](/home/t103o/workbench/projects/holovar/docs/holovar-设计1.md:1) 的开头，内容就是上一轮形成的五层结构：

```text
Application
Host
Runtime
Application Gateway
Adapter
```

这个总结构仍然成立。完整阅读 [transcript_agent_demo.py](/home/t103o/workbench/experiments/terminal-transcript-agent-demo/transcript_agent_demo.py:1) 后，我确实发现设计 1 中关于 `Intervention` 的具体解释需要调整。问题不在 `Intervention` 这个对象，而在我错误地给它附加了“停止当前模型生成”的业务效果。

**我现在对这个控制模型的理解**

这个脚本表达了三种彼此独立的用户行动：

```text
Interject
    进入当前回合，在下一个可用边界影响后续决策。

Queue
    不影响当前回合，等待当前回合终结后创建后续回合。

Interrupt
    取消当前回合，并撤回尚未消费的 Interject 与 Queue。
```

其中，用户在 Agent 运行期间按 Enter，产生的是 `PendingInterject`。它不会取消当前 Agent Task，不会停止当前工具，也不会立即改变正在输出的内容。

它首先进入：

```text
pending_interjects
```

随后等待当前回合中的安全边界。这个 Demo 使用工具结果边界：

```python
await self.emit(tool_result)
await self.apply_interjects_at_boundary(...)
```

因此实际语义是：

```text
当前模型步骤
→ 工具调用
→ 工具结果成立
→ 应用全部待处理插话
→ 下一次模型决策看到这些插话
```

插话影响的是当前回合的后续决策，而不是追溯性地修改已经产生的文本，也不是抢占当前正在进行的工具。

这和 Queue 有本质区别。Tab 提交的 `QueueItem` 明确承诺：

> 不影响当前活跃回合。

它只有在当前回合真正结束、Agent 进入 idle 后，才按 FIFO 顺序启动一个新回合。

Interrupt 则是第三种控制。Esc 会：

1. 取消当前 Agent Task；
2. 撤回尚未消费的 Interject；
3. 撤回尚未启动的 Queue；
4. 按原始消息顺序把内容恢复到输入框；
5. 立即把交互表面恢复为空闲状态。

所以，你指出得对：如果用户希望停止，已有独立的 Interrupt。把普通插话解释成“先停止当前生成，再启动下一轮”会合并两个本来已经清晰分开的动作，反而破坏这套控制模型。

**这套模型为什么完整**

它完整表达了用户对正在运行的 Agent 的三种意图：

```text
继续当前任务，但补充信息
当前任务照常完成，之后再做另一件事
当前任务不要继续
```

这三种意图分别对应：

```text
Interject
Queue
Interrupt
```

它们不需要互相模拟。

“先 Interrupt，再发送新消息”当然可以形成一个新回合，但那表达的是：

```text
放弃当前回合
+
以新消息重新开始
```

Interject 表达的是：

```text
保留当前回合已经形成的上下文、工具结果和工作成果
+
让后续决策吸收新信息
```

二者产生的 Operation 历史、上下文连续性和最终结果都不同。

脚本还表达了一条重要规则：Interject 必须在语义边界应用，而不是在任意机器指令位置强行注入。

工具结果边界之所以成立，是因为此时：

- 当前外部调用已经形成确定结果；
- 下一次模型调用尚未开始；
- Agent 可以把用户输入加入上下文；
- 已完成的工具调用不需要伪装成被取消；
- 后续模型决策能够完整看到工具结果和插话。

这就是安全边界的业务含义。

对于没有后续工具边界的回合，脚本不会声称插话已经应用。`defer_remaining_interjects()` 会把这些消息移动到 FIFO 队列最前方，使它们成为后续回合。

因此，这套业务还维护一条更强的承诺：

> 已提交的用户消息或者进入目标回合，或者成为后续回合；只要用户没有 Interrupt，它就不会因为错过边界而消失。

**设计 1 需要调整的第一处：Intervention 不含抢占语义**

设计 1 中下面这段需要删除：

```text
收到 agent.message 后：
1. 停止当前模型生成子任务。
2. 保存已产生的部分输出。
3. 将用户消息加入业务 Projection。
4. 启动下一轮模型生成。
```

它应改为：

> `agent.interject` 被接受后进入当前 Operation 的 pending Intervention 集合。Agent Runtime 在下一个已声明的应用边界消费它，并在后续模型决策中加入其内容。已经完成的模型输出、工具调用与工具结果保持既有事实。Intervention 的应用不改变 Operation 的 running 状态。

对应过程是：

```text
agent.interject accepted
→ pending
→ 当前 Agent 工作继续
→ 到达工具/结果边界
→ Interjection 被加入当前回合上下文
→ 后续模型步骤执行
→ intervention applied
```

如果回合不再具有可用边界：

```text
pending
→ redirected to follow-up queue
```

它不应被描述为普通的 `not_applied`，因为用户意图仍然由系统承担，只是目标从当前回合变成了后续回合。

**第二处：Intervention 需要 Resolution，而不是二元结果**

设计 1 把 Intervention 终态定义为：

```text
applied
not_applied
```

这个模型不足以表达 Demo 的完整语义。

更准确的生命周期是：

```text
pending
  -> applied
  -> redirected
  -> rejected
  -> withdrawn
  -> expired
```

`applied` 表示内容已经进入目标 Operation 的业务决策。

`redirected` 表示没有进入原目标 Operation，但已经被转换为另一个具有稳定身份的后续输入。它必须记录目标，例如：

```text
redirected_to_command_id
redirected_to_queue_item_id
```

`rejected` 表示应用明确拒绝该输入。

`withdrawn` 表示调用者通过 Interrupt 等行为撤回尚未消费的输入。

`expired` 表示输入超过其有效边界。

在 Agent Runtime 业务中：

```text
工具结果边界消费插话
    -> applied

回合正常结束但插话尚未消费
    -> redirected

用户按 Esc
    -> withdrawn
```

因此，Intervention 的通用结果对象应叫 `Intervention Resolution`。Resolution 记录：

```text
status
reason
resolved_at
可选 target reference
```

HoloVar负责保证每个 accepted Intervention 最终具有 Resolution；Application 负责决定具体 Resolution。

**第三处：Operation 终态不能直接把 pending Intervention 全部判成未应用**

设计 1 原本规定：

> Operation 进入终态时，所有 pending Intervention 自动变成 `not_applied(operation_terminal)`。

这也需要修改。

正常终结之前，Application 必须获得一次处理剩余 Intervention 的 finalization boundary。它可以：

- 把 Intervention 应用到当前 Operation；
- 重定向为后续 Command；
- 明确拒绝；
- 让已经撤回的 Intervention 进入 withdrawn。

Agent Runtime 在 `FINALIZING` 阶段调用 `defer_remaining_interjects()`，就是这个 finalization boundary 的业务实现。

所以正确合同是：

```text
Handler 准备完成
→ Runtime 进入 Operation finalization
→ Application 解析全部 pending Intervention
→ 每个 Intervention 获得 Resolution
→ Result 与 completed 终态提交
```

只有异常失败、强制取消或 Runtime Shutdown 无法执行应用级重定向时，Runtime 才使用框架级兜底 Resolution，例如：

```text
rejected
reason = operation_terminated
```

这使正常业务语义由 Application 完整表达，Runtime 只负责不留下无结局对象。

**第四处：Intervention 和 Queue 属于不同对象**

不能把 Queue 当成“延迟 Intervention”。

Intervention 已经绑定一个存在的 `operation_id`：

```text
Intervention
    target = current Operation
```

Queue Item 指向未来工作：

```text
Queued Command
    target = future Operation
```

在 Demo 中，Queue Item 直到 `start_next_queued_turn()` 才真正创建一个 Agent Turn。真实 Agent Runtime 可以把它建模为具有稳定 ID 的业务队列条目；它随后产生新的 Command 和 Operation。

因此：

```text
Intervention redirected
    |
    v
Queued Command
    |
    v
future Operation
```

重定向必须保留来源关系：

```text
source_intervention_id
queued_command_id
future_operation_id
```

这样观察者能够知道：后续回合不是一条无来源的新消息，而是前一回合中错过应用边界的插话。

**第五处：路由意图必须在提交时固定**

脚本中有一个非常成熟的细节：

```python
agent_was_running = self.agent_is_running
```

它在用户按下 Enter 的同步时刻记录状态，而不是等异步处理函数稍后运行时重新判断。因为在几个调度 tick 之间，当前回合可能恰好结束。如果重新判断，用户本来提交的插话就会静默变成普通新任务。

在分布式 HoloVar Application 中，这条规则应表达得更强：

> 客户端提交的是明确行动，不是让服务端根据稍后的偶然状态猜测行动类型。

也就是说，协议中应明确区分：

```text
submit Intervention to operation_id
enqueue follow-up Command
submit new Command
```

Intervention 必须携带目标 `runtime_id` 和 `operation_id`。如果目标已经终结，Application根据明确策略返回 `redirected` 或 `rejected`，不能把它悄悄解释成一个无关联的新 Command。

**第六处：Interrupt 仍然使用 Operation Cancellation**

Demo 的 `interrupt_current_turn()` 属于 Agent Runtime 对 HoloVar Cancellation 的业务投影。

调用者首先提交取消意图：

```text
cancel requested
```

Agent Runtime 随后：

- 取消当前 Agent Task；
- 解析或撤回 pending Intervention；
- 处理 Queue；
- 恢复可编辑内容；
- 执行清理。

全部完成后：

```text
operation cancelled
```

Demo 为了本地 UI 响应速度，会先把 `agent_task` 指针清空并把 composer 恢复为空闲，再让旧 task 的 CancelledError 继续传播。这是前端交互投影，不改变 HoloVar 的权威语义：Operation 只有在取消和清理完成后才进入 `cancelled`。

**设计 1 其余部分的判断**

Host、Runtime、Application Gateway、Adapter 的所有权结构不受这个 Demo 影响，仍然成立。

身份与授权结构也保持：

```text
Adapter 建立 Principal
Application Gateway 执行策略
Runtime 提交事实
```

需要修改的主要是 Intervention 章节：

1. 删除“插话会停止当前模型生成”的假设。
2. 把 Intervention 定义为在应用边界生效的后续输入。
3. 用 Resolution 代替 `applied/not_applied` 二分。
4. 增加 `redirected`，表达插话转为后续 Command。
5. 增加 Operation finalization boundary。
6. 明确 Intervention、Queued Command 和 Cancellation 是三个对象。
7. 明确行动类型和目标在提交时固定，不能由异步处理时的偶然状态重新解释。

最终，Agent Runtime 对 HoloVar 的使用关系应当是：

```text
普通消息
    空闲时 -> Command -> 新 Operation

插话
    运行时 -> Intervention -> 当前 Operation
                         |
                         +-- applied at boundary
                         |
                         +-- redirected to Queued Command

排队
    任意时刻 -> Queued Command -> 未来 Operation

中断
    运行时 -> Cancellation -> 当前 Operation cancelled
                         |
                         +-- pending inputs withdrawn or resolved
```

这套模型比我上一轮给出的“插话触发停止和新一轮生成”准确得多。HoloVar负责 Intervention 的身份、接受、顺序、持久可见性和 Resolution；Agent Runtime 负责决定什么是安全边界、何时应用、何时重定向，以及用户中断后如何恢复尚未消费的输入。
</Taylor>

<Miran id="11">
好的，那接下来你可以去啊，看一下，对设计一号文档进行一些增补啊，一些修订啊，一些删除，一些修改，一些增加。那全部做完之后，你自己再完整的检查一下，看一下有没有什么东西少讲了，是吧？或者有什么东西没必要讲，是吧？自己去看吧，自己检查开始吧。
</Miran>

<Taylor id="11">
**Planning full document review**

我会先完整读取设计一号文档和当前 Git 状态，再按刚确认的控制模型重写 Intervention 相关契约，同时审查其余章节是否存在重复、错误推断或缺失的不变量。修改后会完整复读并运行文档层面的检查。

**Planning adapter and runtime shutdown improvements**

**Refining intervention lifecycle and handler design**

**Clarifying atomicity limits in redirect handling**

**Designing operation finalization contract**

**Defining intervention finalization policies**

**Defining intervention retention in snapshots**

**Planning patch application and startup error handling**

**Applying patch with startup failure handling**

全文审查后，核心修订集中在 Intervention：把“插话即抢占”改为“在应用边界消费”，把二元终态改为明确 Resolution，并把 Interject、Queued Command、Cancellation 分开。装配部分还缺少启动失败的所有权闭合，我会一并补上；Loommux 专属示例会从 HoloVar 的核心定义中移除。

**Planning file review and formatting**

Intervention 章节已完成结构性重写：普通插话、后续队列和取消已经成为三条独立控制路径，正常完成前增加了 Finalization Policy，所有 accepted Intervention 都必须形成可恢复的 Resolution。现在我正在完整复读全文，检查术语一致性、重复承诺和关闭/授权边界是否与新模型冲突。

**Identifying client_id ownership inconsistency**

**Clarifying submission and authorization boundaries**

**Defining intervention capacity and idempotency**

**Defining operation revision semantics**

**Defining stable target reference and event ordering**

**Establishing ordered event commit semantics**

**Planning commit redirect and doc checks**

**Applying patch with idempotency and provenance**

**Splitting patches for atomic apply**

**Revising authentication provider descriptions**

**Fixing HoloVar validation and input references**

**Applying patch**

**Verifying post-modification consistency**

**Planning final code and markdown checks**

**Executing final consistency checks**

复读中发现并修正了两个残留问题：提交表面已经删除版本前置条件，但接受步骤仍引用它；身份段也把 Principal 的验证责任错误地写给了 HoloVar。现在二者已分别改为纯 Payload 验证，以及由 HoloVar 记录并传播 Provider 已建立的 Principal。接下来只做最终结构与格式校验。

**Confirming doc-only modifications**

已完成对 [holovar-设计1.md](/home/t103o/workbench/projects/holovar/docs/holovar-设计1.md) 的修订和全文复查。

主要调整：

- 重写 Intervention 模型，明确它不会自动中断或抢占 Operation。
- 分离 `Command`、`Intervention`、`Application Queue Entry` 与 `Cancellation`。
- 将 Intervention 生命周期改为 `pending -> applied | redirected | rejected | withdrawn | expired`。
- 增加 `Intervention Resolution`、应用边界、Finalization Policy、重定向目标与撤回语义。
- 按 Agent Demo 修正插话模型：等待工具/结果边界；错过边界时重定向到后续队列。
- 明确行动类型在提交时固定，不根据异步处理时的偶然状态重新解释。
- 增加 Intervention 幂等冲突、容量、Retention 和 Metrics 契约。
- 增加 Host 启动失败时的逆序资源清理与非 Ready 保证。
- 修正 Principal 的责任：身份由 Provider 建立，HoloVar 负责记录、授权判定和来源传播。
- 删除 Loommux Kernel 专属示例，保持 HoloVar 文档的无业务边界。

已完成 `git diff --check`、Markdown 围栏平衡、文件换行和残留旧术语检查。文档现为 765 行；未运行代码测试，因为本次仅修改设计文档。
</Taylor>