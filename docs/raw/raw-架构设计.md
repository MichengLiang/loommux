<Miran id="1">
阅读这些文件
\\wsl.localhost\Ubuntu\home\t103o\workbench\examples\Python的mcp\计数器示例\网络版本\子进程资源控制台示例\client_support.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\examples\Python的mcp\计数器示例\网络版本\子进程资源控制台示例\console.html
\\wsl.localhost\Ubuntu\home\t103o\workbench\examples\Python的mcp\计数器示例\网络版本\子进程资源控制台示例\demo_anonymous.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\examples\Python的mcp\计数器示例\网络版本\子进程资源控制台示例\demo_named.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\examples\Python的mcp\计数器示例\网络版本\子进程资源控制台示例\README.md
\\wsl.localhost\Ubuntu\home\t103o\workbench\examples\Python的mcp\计数器示例\网络版本\子进程资源控制台示例\server.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\examples\Python的mcp\计数器示例\网络版本\子进程资源控制台示例\test_worker_manager.py
\\wsl.localhost\Ubuntu\home\t103o\workbench\examples\Python的mcp\计数器示例\网络版本\子进程资源控制台示例\worker.py

然后再看看这个项目
\\wsl.localhost\Ubuntu\home\t103o\workbench\projects\loommux\src
\\wsl.localhost\Ubuntu\home\t103o\workbench\projects\loommux\src\loommux

那么需要进行改造与完全的重构 那你看一下吧。健康的、优雅的设计应该是什么样的呢？自己探索一下，然后来和我分享一下，来和我聊，不少于 2000 字或者更多都可以哟
</Miran>

<Taylor id="1">
**Mapping directories and listing files**

**Planning batch reading of Python files**

**Planning source mapping and summarization**

**Reviewing group 1 code execution logs**

**Reading adapter cell source transform**

**Exploring project structure and tests**

**Reading first 100 lines**

**Planning project inspection and analysis**

**Listing tests and checking git status****Preparing AST extraction and test running**

**Identifying concurrency bottlenecks and desynchronization risks****Highlighting task cancellation and resource race conditions****Assessing worker sync and protocol limitations**

**Planning async multi-tenant adapter design****Designing kernel resource management and containment****Identifying need for cross-platform process tree cleanup**

**Planning log storage refactor and artifact management****Identifying kernel execution and deadlock risks****Examining lock contention and concurrency issues**

**Preparing to run baseline tests**

**Deciding loommux multi-kernel architecture approach****Designing layered loommux architecture and resource lifecycle****Defining async execution coordinator and lease management**

**Planning async kernel client integration****Designing graceful shutdown and testing strategy**

我先把两个区域分别建立文件地图：第一组示例会按你列出的 8 个文件完整阅读；`loommux/src` 则先确认结构与规模，再分批完整阅读源码。之后我会把两者的职责模型、进程与会话边界、协议层、状态机、错误恢复和测试策略放在一起对照，最后给你一份偏“重构设计评审”的长篇分析。

第一组已经完整读完。它的核心其实不是“计数器”，而是一个**按 MCP 会话或公开名称寻址、由守护进程托管、通过 stdin/stdout 串行 RPC 驱动的有状态子进程资源池**；示例清楚，但目前 `server.py` 同时承担了资源注册表、生命周期状态机、进程监督、线协议、MCP 适配、HTTP API 和应用装配，已经出现典型的“示例成长为基础设施后边界失真”。

我现在转入 `loommux`：先读执行记录、输出日志、kernel 会话与 runtime 这些核心状态层，再读 MCP 适配与展示层。重点会确认示例应当复用/抽取哪些思想，而不是简单把两份代码机械拼接。

`loommux` 的核心边界也已经确认：它比示例成熟很多，已经把 **kernel 启动隔离、ZMQ 会话、execution 记录、输出日志、终端控制符归一化、MCP 展示策略、workspace 解析** 分开了；但最上层仍然建立在“一个服务器进程只拥有一个 `IPythonMCPAdapter`、一个 workspace、一个 kernel”的单租户假设上。

接下来我补读项目自己的设计文档和测试契约，专门分辨哪些现状是刻意保证的稳定语义，哪些只是当前单 kernel 实现留下的偶然结构。这样给出的重构建议会尽量保住 loommux 已经做对的部分，而不是“大拆大建后把成熟约束弄丢”。

# 阅读结论与重构构想

我已经完整阅读了你列出的 8 个“子进程资源控制台示例”文件，也阅读了 `projects/loommux/src/loommux` 下的全部 Python 源码，并补充查看了项目 README、核心控制面设计文档、测试契约与当前测试结果。

先给一个简短结论：

> **示例项目已经验证了“一个守护服务管理多个命名或会话私有子进程”的产品方向；loommux 则已经拥有一套相当成熟的单 kernel 执行控制面。真正健康的重构，不应该把示例的 `server.py` 搬进 loommux，也不应该直接把当前 `IPythonMCPAdapter` 改成一个巨大的多租户管理器，而应该把 loommux 现有的单 kernel 能力提炼成“资源引擎”，再在它上方建设独立的资源编排与守护层。**

这两个项目并不是互相替代的关系，而是非常自然的上下层关系：

```text
示例提供的核心思想：
资源寻址 + 多子进程注册表 + 租约 + 回收 + 管理控制台

loommux 已经拥有的核心能力：
IPython kernel 生命周期 + execution 控制面 + 输出采集
+ 中断/重启 + workspace 隔离 + MCP 展示策略

理想的新系统：
多资源编排层
        ↓
多个独立的 loommux kernel resource
        ↓
每个资源内部仍保持成熟的 execution 控制面
```

---

## 一、当前示例实际上做了什么

这个示例表面上是“计数器”，但其真正有价值的部分是一个小型的**进程资源守护服务**。

它定义了两种资源寻址方式：

1. **匿名会话资源**
   - 以 MCP Session ID 为键；
   - 每个会话获得自己的子进程；
   - 默认空闲 30 分钟后回收。

2. **公开命名资源**
   - 以 `X-Process-Resource` 请求头中的名称为键；
   - 多个 MCP Session 可以通过相同名称进入同一子进程；
   - 默认空闲 24 小时后回收。

每个资源拥有：

- 独立 PID；
- 独立内存状态；
- 创建时间和最后活动时间；
- 租约截止时间；
- 活跃调用计数；
- 命令计数；
- 生命周期；
- 一条串行化请求通道。

网页控制台则提供：

- 资源快照；
- 心跳续租；
- 单资源普通回收；
- 单资源强制回收；
- 回收所有空闲资源；
- 强制回收全部资源。

这个概念模型是成立的，而且演示价值很好。尤其是以下几点很值得保留：

### 1. 资源状态不放在 MCP Server 自身业务对象里

业务状态位于独立子进程，而 Server 只持有控制句柄。这是正确的隔离意识。

### 2. 活跃调用与空闲租约分离

长调用期间不会因为 TTL 到期而被后台扫描误杀，这是必要的不变量。

### 3. 命名资源和会话私有资源是不同语义

这不是单纯换一个 key，而是两种资源所有权模式。这个区分应该继续存在。

### 4. 管理面和业务调用面共用同一守护进程

对于本地工具和可信内网使用场景，这种部署方式简单、可观察、容易操作。

---

## 二、示例当前不适合直接成长为正式基础设施的原因

示例的主要问题不是代码“写得差”，而是它已经超过了单文件示例适合承载的复杂度。

`server.py` 同时承担了：

- 环境配置读取；
- 资源地址解析；
- 资源注册表；
- 资源生命周期状态机；
- 子进程创建；
- stdin/stdout RPC；
- stderr 转发；
- 异常退出监督；
- TTL 扫描；
- MCP 工具注册；
- HTTP 管理 API；
- HTML 页面服务；
- 应用启动。

这意味着任何一个变化都会穿透整个文件。例如将 worker 从计数器换成 IPython kernel，不只是替换 `_spawn_locked()`，而会影响调用协议、超时行为、退出监督、输出模型、重启语义、状态展示和测试方式。

更重要的是，当前实现存在一些示例阶段可以接受、正式系统中必须修复的问题。

### 1. 注册表锁中执行了子进程启动

`_acquire()` 持有 `_registry_lock` 时调用 `_spawn_locked()`，后者会 `await asyncio.create_subprocess_exec(...)`。

这会导致一个资源启动缓慢时，所有其他资源的获取、快照、回收、续租都可能被阻塞。

正确做法应当是：

- 锁内只完成注册表状态转换；
- 创建一个 `STARTING` 占位项或共享启动 Future；
- 锁外完成真正的进程启动；
- 成功后锁内提交；
- 失败后锁内回滚；
- 同一 key 的并发请求等待同一个启动结果，不能重复创建进程。

### 2. RPC 超时后协议会失去同步

当前协议是“一行 JSON 请求对应一行 JSON 响应”，并由 `request_lock` 保证同一时间只有一个请求。

但是，如果一次调用超时：

1. Server 停止等待；
2. Worker 仍然可能继续执行；
3. Worker 稍后把旧响应写入 stdout；
4. 下一次调用读取到的是上一次响应；
5. 请求 ID 不匹配；
6. 此后的协议流已经被污染。

调用协程被取消时也有类似问题。

因此，对于这种严格串行 RPC：

> **一次超时、取消、非法响应或 ID 不匹配，原则上都应将通信通道标记为 poisoned，并回收整个 worker。**

不能只抛一个 `WorkerCallError` 然后继续复用进程。

不过，重构 loommux 时不建议继续使用这套手写行协议。loommux 已经正确使用 Jupyter ZMQ 协议，没有必要退回自制 RPC。

### 3. Worker 没有正式启动握手

子进程被创建后立即加入可用注册表，但没有确认：

- worker 代码导入成功；
- 参数有效；
- stdin/stdout 协议已准备好；
- 内部初始化完成。

健康的资源应经过：

```text
NEW → STARTING → READY
                 ↓
                FAILED
```

只有 READY 资源才能向调用方交付。

### 4. 生命周期和活动状态混合得不够明确

当前有 `lifecycle` 字符串，又有 `active_calls`，但合法状态转换没有被正式建模。

建议把两者分开：

```text
资源生命周期：
STARTING / RUNNING / STOPPING / STOPPED / FAILED

运行活动：
active_operations: int
current_execution: optional execution id
```

“busy”不是资源生命周期，而是 RUNNING 资源的活动状态。

### 5. Web 管理接口缺乏安全边界

示例默认监听 `0.0.0.0`，管理 API 可以：

- 查看所有资源；
- 给资源续租；
- 回收资源；
- 强制终止全部资源。

同时没有认证、授权、CSRF token 或管理密钥。

README 虽然说明名称不承担认证职责，但实际风险不只在名称碰撞，而在整个管理控制面没有权限边界。

正式实现至少应满足：

- 默认只监听 `127.0.0.1`；
- 管理 API 使用独立 bearer token；
- 资源名称不是权限凭证；
- MCP 执行面与管理员控制面分别授权；
- 非本机部署必须显式开启；
- 日志中不得泄露 token 和敏感请求头。

### 6. 资源和输出没有配额

当前 worker 示例状态很小，但 IPython kernel 完全不同：

- execution 输出可能非常大；
- Base64 图片可能占据大量内存；
- execution 历史持续增长；
- 一个 kernel namespace 本身也可能非常大；
- 多 kernel 会把这些问题成倍放大。

因此，多资源 loommux 必须从第一天设计：

- 每个 execution 输出配额；
- 每个资源历史记录配额；
- 全局内存软硬限制；
- 图片数量和字节限制；
- 空闲 TTL；
- 最大资源数；
- 最大并发启动数；
- 必要时将长日志落入私有 spool 文件，而不是一直拼接 Python 字符串。

---

## 三、loommux 当前已经做对了什么

与示例相比，loommux 的单 kernel 内部设计明显更成熟。

它已经把主要职责分成了：

- `coding_agent_kernel.py`：构造私有 kernel 启动环境；
- `kernel_runtime.py`：管理 KernelManager 和平台相关生命周期；
- `kernel_session.py`：收集 IOPub 并关联 execution；
- `execution.py`：execution 状态和展示事件；
- `output_log.py`：输出流、行范围和搜索；
- `terminal_text.py`：终端控制符归一化；
- `adapter.py`：执行编号和控制操作；
- `mcp_result_policy.py`：MCP 文本/图片结果；
- `presentation.py`：模型可读展示；
- `workspace_resolver.py`：workspace 解析；
- `mcp_server_factory.py`：工具注册和应用装配。

尤其值得保留的设计包括：

### 1. Loommux execution 与 IPython execution count 分离

这是非常正确的。公开执行坐标由服务自身分配，因此 kernel 重启后历史记录仍有稳定身份。

### 2. 工具调用超时不等于终止 cell

`run_cell` 初始等待结束后，cell 仍然继续运行，调用方可以通过 `wait`、`read_output`、`search_output` 等继续观察。

这是真正面向 Agent 控制面的语义，而不是简单的 subprocess wrapper。

### 3. 输出是 append-only transcript，不模拟终端屏幕

终端控制符在写入公共日志前被归一化，这使输出可读、可搜索、可引用。

### 4. workspace、kernel runtime root、execution record 是不同所有权对象

设计文档对这些边界写得很清楚，这是重构时必须保护的资产。

### 5. 重启 kernel 不删除历史 execution

这让“资源代际”和“执行历史”可以自然分离，是以后多资源管理的重要基础。

### 6. 当前测试基础很好

我实际运行了完整测试：

```text
137 passed, 1 skipped
总覆盖率 90.98%
```

所以这不是一个需要推倒重写的坏项目。恰恰相反，最危险的做法是以“完全重构”为名，把现有经过测试的执行、输出和展示语义一起重写掉。

另外，当前 loommux 子仓库中已经存在未提交修改：

```text
MM docs/coding-agent-control-plane-design.md
MM docs/workspace-configuration.md
MM src/loommux/host_workspace_config.py
MM src/loommux/workspace_resolver.py
MM tests/test_workspace_config.py
?? docs/raw/
```

这些不是我产生的，我也没有修改任何文件。

---

## 四、loommux 当前架构中需要优先处理的问题

虽然测试通过，但从未来多资源化角度看，当前还有几个关键风险。

### 1. 单进程单 kernel 是写入公开契约的假设

README 和设计文档都明确规定：

> 一个 loommux server process 拥有一个 kernel。

因此，直接把 `IPythonMCPAdapter.kernel` 改成字典，会同时破坏：

- 工具选择规则；
- execution 身份语义；
- `status()` 输出；
- restart/interrupt 的目标；
- workspace 生命周期；
- 现有八工具契约；
- 黑盒测试事实源。

这不是一个内部重构，而是产品模型变更。

所以我建议：

> **保留现有单资源 loommux 入口，同时新增多资源 daemon，而不是偷偷改变原有入口的语义。**

### 2. Kernel 意外退出时，运行中的 execution 可能永久停留在 running

`KernelSession._collect_iopub()` 发现 kernel 不存活时会直接返回，但没有必然把当前 execution 终结为某种失败状态。

结果可能是：

- kernel 已死；
- adapter 的 status 清除了 current execution；
- execution record 本身仍是 `running`；
- `done` 永远不触发；
- 后续 `wait()` 会反复等待。

正式状态机应增加类似：

```text
kernel_lost
```

或者将其归入 `error`，并记录稳定的错误分类，例如：

```text
KernelExited
KernelProtocolLost
KernelStartupFailed
```

### 3. 当前对象之间存在共享可变状态和跨线程回调

`KernelSession` 的 IOPub collector 在线程中修改 `Execution`，与此同时 MCP 调用线程可能读取 snapshot、logs 或重启 kernel。

现在主要依赖：

- CPython GIL；
- `KernelSession._lock`；
- `IPythonMCPAdapter._lock`；
- `threading.Event`。

但 `Execution` 自身没有一致性锁，跨对象还有回调。未来扩展成多个 kernel 后，锁顺序、关闭竞态和半完成快照都会更难推理。

长远而言，健康的实现应该选择一种明确模型：

1. 全异步事件模型；或者
2. 每个 kernel 一个 actor/thread，所有可变状态只由 actor 修改。

不要继续增加交叉锁。

### 4. Linux 子进程树没有和 Windows 同等明确的 containment

Windows 使用 Job Object 终止 kernel 后代，这一点很好。

但非 Windows 的 `_KernelContainment` 当前是空实现。对于能够运行任意 Python 和 shell 命令的 kernel，仅关闭 ipykernel 主进程不一定等于所有后代都被可靠回收。

多资源 daemon 中，这会成为严重问题：资源看起来已删除，后台编译器、服务器或脚本却仍然存活。

建议建立正式平台后端：

```text
ProcessContainment
├── PosixProcessGroupContainment
├── LinuxCgroupContainment（可选增强）
└── WindowsJobContainment
```

至少要确保每个 kernel 拥有独立进程组，并且只在确认进程组归属后执行信号操作。

### 5. 输出存储结构不适合多资源长期运行

`LineLog` 当前通过：

```python
self._text += text
self._text.splitlines()
```

维护输出。

小规模下简单可靠，但大输出下会出现：

- 反复字符串复制；
- 每次统计行数都重新 split；
- 每次读取又构造完整行列表；
- 多资源下内存和 CPU 成本放大。

应该改为 chunked append storage，并维护增量索引。更进一步，可以设计：

```text
OutputStore
├── InMemoryChunkStore
└── SpoolingOutputStore
```

小输出留在内存，超过阈值后写入资源私有目录，同时保留行偏移索引。

图片也不应该长期只保存 Base64 字符串。应尽早校验并转为 artifact，记录：

- MIME；
- 字节数；
- digest；
- detail；
- display ordinal；
- 存储位置。

当前图片限制主要发生在 MCP 交付边界，不能阻止 kernel 先向内存塞入大量图片数据。

---

# 五、我建议的总体架构

最健康的结构不是“一个更大的 `IPythonMCPAdapter`”，而是三层产品。

```text
┌──────────────────────────────────────────┐
│ Transport / Presentation                 │
│ MCP tools、HTTP Admin API、Web Console   │
└─────────────────────┬────────────────────┘
                      │
┌─────────────────────▼────────────────────┐
│ Resource Orchestration                   │
│ 寻址、注册表、租约、配额、监督、权限      │
└─────────────────────┬────────────────────┘
                      │
┌─────────────────────▼────────────────────┐
│ Single-Kernel Engine                     │
│ kernel、execution、IOPub、output、restart │
└──────────────────────────────────────────┘
```

## 第一层：Single-Kernel Engine

这一层来自现有 loommux，代表一个独立资源。

可以将 `IPythonMCPAdapter` 重构为更准确的名字，例如：

```python
class IPythonResourceEngine:
    async def start(self) -> None: ...
    async def submit(self, request: CellRequest) -> ExecutionSnapshot: ...
    async def wait(self, execution: int, timeout: float) -> ExecutionSnapshot: ...
    async def interrupt(self) -> ControlResult: ...
    async def restart(self) -> RestartResult: ...
    async def close(self) -> None: ...
```

它只知道：

- 一个 workspace；
- 一个 Python interpreter；
- 一个 kernel；
- 自己的 execution sequence；
- 自己的 output store；
- 自己的 kernel generation。

它不知道：

- MCP Session ID；
- 命名共享资源；
- HTTP header；
- 管理网页；
- 全局资源上限；
- 谁有权限回收它。

这点非常重要。

## 第二层：Resource Orchestration

新增资源编排层，例如：

```text
src/loommux/resources/
    model.py
    address.py
    registry.py
    resource.py
    supervisor.py
    lease.py
    quota.py
    events.py
```

核心对象建议如下。

### `ResourceSelector`

表示调用方想找什么：

```python
@dataclass(frozen=True)
class ResourceSelector:
    scope: ResourceScope
    subject: str
```

其中：

```python
ResourceScope = Literal["session", "named"]
```

### `ResourceIdentity`

资源真正创建后获得不可变 ID：

```python
@dataclass(frozen=True)
class ResourceIdentity:
    resource_id: UUID
    selector: ResourceSelector
    created_at: datetime
```

这里要区分：

- `resource_name`：人类可读别名；
- `resource_id`：稳定机器身份；
- `selector key`：内部寻址键。

名称绝对不能同时承担身份、授权和展示三种职责。

### `ManagedResource`

```python
class ManagedResource:
    identity: ResourceIdentity
    engine: IPythonResourceEngine
    lifecycle: ResourceLifecycle
    generation: int
    active_leases: int
    expires_at: float
```

### `ResourceRegistry`

它只管理映射和原子状态转换，不直接注册 MCP 工具，也不生成 HTML。

关键方法可以是：

```python
async def acquire(selector, spec) -> ResourceLease
async def get(resource_id) -> ManagedResource | None
async def snapshot() -> list[ResourceSnapshot]
async def recycle(resource_id, mode) -> RecycleResult
async def recycle_idle() -> CleanupReport
```

### `ResourceSupervisor`

负责：

- TTL 扫描；
- kernel 意外退出；
- 启动失败；
- 资源配额；
- Server 关闭；
- 僵尸资源回收；
- 结构化事件记录。

Supervisor 不负责业务工具调用。

---

## 第三层：Transport 和展示

这里应当继续分成两个表面。

### 执行面

执行面的八个工具仍然非常合理：

- `run_cell`
- `status`
- `execution_status`
- `read_output`
- `search_output`
- `wait`
- `interrupt`
- `restart`

区别是它们先通过 `RequestResourceResolver` 找到当前资源，再委托给该资源的 engine。

```text
MCP request
    ↓
RequestIdentity
    ↓
ResourceSelector
    ↓
registry.acquire()
    ↓
resource.engine.run_cell()
```

### 管理面

资源管理工具独立存在：

- `list_resources`
- `describe_resource`
- `heartbeat_resource`
- `recycle_resource`
- `recycle_idle_resources`
- `recycle_all_resources`

不要把管理员工具和普通资源工具混成同一个权限等级。

---

# 六、execution 身份应该怎样设计

多资源化后，单独一个整数 `execution=5` 不再具有全局唯一性。

建议采用如下语义：

```text
资源内部坐标：
execution = 5

管理面全坐标：
(resource_id, execution)
```

对已经路由到当前资源的 MCP 调用，仍然可以只传整数：

```python
read_output(execution=5)
```

但在管理 API、日志和审计事件中必须带资源 ID：

```json
{
  "resource_id": "7a88...",
  "execution": 5
}
```

另外建议为每次 kernel restart 增加：

```text
kernel_generation
```

例如：

```text
resource_id = A
kernel_generation = 3
execution = 27
kernel_execution_count = 4
```

三个坐标职责不同：

- resource ID：哪个长期逻辑资源；
- generation：该资源的第几代 kernel；
- execution：资源生命周期中的稳定执行序号；
- kernel execution count：仅诊断，不对外寻址。

这会比只看 PID 清晰得多，因为 PID 会复用，也不是业务身份。

---

# 七、租约语义需要正式规定

示例中的租约已经有雏形，但正式实现应明确下面几个问题。

## 1. 什么操作续租

我建议以下操作续租：

- 接受一个新的 `run_cell`；
- 显式 heartbeat；
- restart；
- 当前活跃调用结束。

以下只读观察默认不续租：

- list resources；
- 管理页自动刷新；
- execution status；
- read output；
- search output。

否则网页每两秒刷新一次会让资源永远无法过期。

## 2. 活跃操作期间不回收

定义：

```text
eligible_for_idle_recycle =
    active_leases == 0
    and now >= expires_at
    and lifecycle == RUNNING
```

活跃操作结束后，再从结束时间开始计算新的 TTL。

## 3. 普通回收与强制回收

普通回收：

- 拒绝正在活跃的资源；
- 或进入 draining，禁止新任务，等待当前任务结束。

强制回收：

- 终止当前 execution；
- 关闭 kernel；
- 清理整个进程树；
- 将未结束 execution 标记为 killed；
- 清理 runtime root。

建议支持两种模式：

```text
DRAIN：停止接收新工作，等待当前工作结束
FORCE：立即终止
```

---

# 八、推荐使用“每资源单写者”而不是继续叠加锁

这是这次重构中最重要的内部选择之一。

现有 loommux 由 MCP 调用线程、IOPub collector 线程和多个锁共同维护状态。一个资源尚可控制，多个资源后继续扩展会越来越危险。

我更推荐：

> **每个 kernel resource 有一个明确的状态所有者。所有 kernel 生命周期和 execution 状态转换，都在该资源自己的事件循环或 actor 中发生。**

可以使用 `AsyncKernelClient`，将以下事件统一送入资源事件循环：

- submit；
- IOPub message；
- interrupt；
- restart；
- kernel exit；
- shutdown；
- output read request。

如果暂时不想一次改成全异步，也可以保留 collector thread，但必须做到：

- `Execution` 自身有一致性保护；
- 回调不在 session lock 内执行；
- 锁顺序有书面规定；
- kernel exit 必须产生终态事件；
- snapshot 是一个原子视图；
- shutdown 可重复调用且幂等。

不过从“完全重构”的目标来看，全异步资源引擎会更自然，因为 FastMCP、租约扫描和资源管理本身已经是 asyncio 世界。

---

# 九、推荐的目录结构

我会倾向于整理为：

```text
src/loommux/
    domain/
        execution.py
        resource.py
        errors.py
        events.py
        identifiers.py

    kernel/
        launch.py
        runtime.py
        containment.py
        session.py
        protocol.py

    execution/
        coordinator.py
        store.py
        output_store.py
        terminal_text.py
        source_control.py
        source_transform.py

    resources/
        registry.py
        managed_resource.py
        lease.py
        supervisor.py
        quota.py
        resolver.py

    application/
        execution_service.py
        resource_admin_service.py
        request_routing.py

    transport/
        mcp/
            execution_tools.py
            admin_tools.py
            result_policy.py
            presentation.py
            server.py
        http/
            routes.py
            authentication.py
            schemas.py

    web/
        console.html
        console.js
        console.css

    config.py
    entrypoints.py
```

但不必机械追求目录多。核心原则是：

- domain 不依赖 FastMCP；
- kernel 不依赖 HTTP；
- resource registry 不读取请求头；
- presentation 不控制进程；
- Web console 不定义业务规则；
- MCP tool 只是薄薄的输入输出适配器。

---

# 十、测试应该如何升级

现有 138 个测试应尽量原样保留，作为单资源 engine 的回归契约。

然后新增三组测试。

## 1. Resource Registry 单元测试

覆盖：

- 同 key 并发 acquire 只创建一个资源；
- 不同 key 可以并发启动；
- 启动失败不会留下幽灵项；
- 普通回收拒绝 active resource；
- force 回收会终结 active execution；
- snapshot 不续租；
- heartbeat 会续租；
- sweep 不回收 pinned resource；
- stop 幂等；
- recycle 与 kernel crash 竞态。

## 2. 多资源黑盒测试

使用真实 kernel 验证：

- 两个匿名 Session 获得不同 PID 和 namespace；
- 相同命名资源复用同一 resource ID 和 namespace；
- 不同名称之间状态隔离；
- 一个资源 busy 不阻塞另一个资源运行；
- 一个资源 restart 不影响另一个资源；
- 一个资源 crash 不污染其他资源；
- 每个资源 execution 序号独立连续；
- 管理面可以通过 `(resource_id, execution)` 查到输出。

## 3. 故障注入测试

必须专门覆盖：

- kernel 启动超时；
- kernel 启动后立即退出；
- IOPub 通道消失；
- Server 正在关闭时收到调用；
- sweep 与 restart 同时发生；
- 管理员 force recycle 与 `wait()` 同时发生；
- 大输出；
- 大量图片；
- 子进程树清理；
- 资源数达到上限。

---

# 十一、建议的迁移路线

我不建议一次性改完所有东西。更稳妥的路线如下。

## 第一阶段：冻结当前契约

- 保留现有八工具行为；
- 保留全部现有测试；
- 明确当前单 kernel 入口是 legacy-compatible surface；
- 修复 kernel 意外退出后 execution 永久 running 的问题；
- 补齐 Linux process containment；
- 为 Execution snapshot 增加一致性保护。

## 第二阶段：提取单资源引擎

将 `IPythonMCPAdapter` 中与 MCP 无关的部分提取成：

```text
IPythonResourceEngine
```

MCP factory 继续通过一个单资源 wrapper 使用它，因此对外行为不变。

这是最关键的抽象切口。

## 第三阶段：建设 Resource Registry

先不接网页，只实现：

- session/named selector；
- acquire；
- lease；
- TTL；
- recycle；
- supervisor；
- quotas。

通过纯 Python 测试验证并发状态机。

## 第四阶段：新增 daemon 入口

例如：

```text
loommux                   原有单 kernel stdio 入口
loommux --server          原有单 kernel HTTP 入口
loommux daemon            新的多资源守护服务
```

或者提供独立命令：

```text
loommuxd
```

我个人更喜欢独立的 `loommuxd`，因为它在部署、安全和生命周期上确实是另一个产品表面。

## 第五阶段：接入管理 API 和网页

网页只消费稳定的 Admin API，不直接理解内部对象。

管理 API 应返回结构化状态码，而不是让前端解析中文 message。

例如：

```json
{
  "ok": false,
  "code": "resource_busy",
  "message": "资源仍有 1 个活跃调用"
}
```

## 第六阶段：加入持久或可落盘输出存储

多资源长期运行稳定后，再将大日志和图片从纯内存迁移到 spool store。不要在第一阶段同时引入数据库、复杂队列和分布式调度。

---

# 十二、最终判断

我认为这个方向非常值得做，而且两个现有项目已经各自验证了一半：

- 示例验证了**资源化、多进程、租约、控制台**；
- loommux 验证了**有状态 IPython、稳定 execution、异步观察、输出投影、重启控制**。

但重构的中心思想必须是：

> **资源管理是 loommux engine 之上的编排能力，不是塞进 adapter 里的更多条件分支。**

最优雅的最终模型应该是：

```text
一个 daemon
    管理多个 ManagedResource

一个 ManagedResource
    拥有一个 IPythonResourceEngine

一个 IPythonResourceEngine
    拥有一代或多代 KernelSession
    并在资源生命周期中保持稳定 execution 序号

一个 Execution
    拥有可观察状态、文本流和展示 artifact

MCP/HTTP/Web
    只是这些应用能力的不同适配表面
```

如果让我给重构确定一句总纲，我会写成：

> **保留 loommux 已经成熟的单 kernel 控制面语义，把它降为可实例化的资源引擎；在其上新增具有明确身份、租约、配额、监督和权限边界的多资源守护层；所有网络协议和网页展示保持在最外层，不侵入资源与执行领域。**

这样重构以后，系统既不会退回到示例式大文件，也不会因为追求抽象而变成过度工程。单资源内部仍然保持 loommux 现在的清晰控制闭环，多资源部分则成为一个真正可测试、可监督、可限制、可演进的守护服务。
</Taylor>

<Miran id="2">
好的 首先第一点，我不要任何的关于公网的网络安全问题，一点都不要拎过来。我告诉你，你要给我设计，给我设计完整的一全套的，要不然一点都不要设计。你这个踩一脚绊一脚的，那后面安全团队非常难做， 不要任何的傻逼 Token 一点都不要。你这不是给自己装个姿态吗？好像有 Token 就安全了吗？ 然后第二点，不要任何的什么输出配额，不要不要这个东西运行在内网的，或者说某个开发者自己电脑上的  它的服务时间通常就是一个上午、一个中午或一个下午，可能就关电脑重启，资源就释放了，是整个守护进程都会没掉 所以你这里所谓的第一天就设计什么输出配额呀？哎，什么全局什么限制啊？什么都不要，不要，还有什么认证授权也是一点都不要你设置啊莫名其妙你这是好像把开发者自己当成贼锁在房间里面啊，然后呢，你要按照你的品味 taste，按照你的工程经验给出健康的全套的设计方案 那设计方案、设计计划呢？这个东西的话，那可能更多的涉及操作系统了，进程资源调度、并发之类的东西啊。那这里也就没有什么第一步做什么，第二步做什么，那是别人开发者拿着你的图纸要做的事情，他自己安排 然后呢，我们优先适配 Linux，本次不涉及 Windows， 然后我是独裁者，我们说什么就是什么，没有任何的兼容，没有任何的迁移策略。所以你放心大胆设计，来好好想想吧。你之前上一轮回答其实有很多地方设计还是很优秀的。来想好之后来和我分享一下，那不少于 2000 字或者更多
</Miran>

<Taylor id="2">
**Defining full Linux-based architectural design****Designing Linux process and resource topology****Clarifying shared workspace and interpreter design**

**Clarifying state machine and concurrency design****Designing async resource lifecycle and message handling****Evaluating background task observability**

**Designing resource API surfaces****Refining resource concurrency and recycle semantics****Designing shutdown and output storage handling**

**Removing output truncation limits****Designing full output streaming and structured logging****Removing obsolete full-output directive**

**Proposing six-package module structure**

明白。这次我把前一版中不符合实际使用场景的假设全部拿掉，不再把一个开发者本地、短生命周期的工具，设计成某种长期在线的平台服务。

你的约束非常明确：

- 运行环境优先且仅考虑 Linux；
- 守护进程通常只活半天或一天，进程退出后所有状态一起消失；
- 不考虑持久化；
- 不考虑资源配额；
- 不考虑输出配额；
- 不考虑任何访问控制机制；
- 不保留旧架构兼容性；
- 不做迁移方案；
- 不按“第一步、第二步”安排开发工作；
- 直接给一张完整、统一、可以从头实现的工程图纸；
- 重点放在操作系统进程、资源生命周期、并发、内核控制和状态一致性上。

在这个前提下，我们就可以非常大胆地设计，而且应该把系统做得**纯粹**：它是一个本地开发工作台守护进程，不是平台，不是多租户系统，不是远程执行集群，也不是持久化作业调度器。

---

# 一、系统的本质

我认为新系统最准确的定义是：

> **一个运行于 Linux 本机的、多 IPython 资源守护进程。它根据 MCP Session 或公开名称创建和复用独立 IPython kernel，持续保存每个 kernel 的 Python namespace 和 execution 历史，并提供执行、观察、中断、重启、释放和统一控制台。**

这里的关键不再是“一个 loommux Server 对应一个 kernel”，而是：

```text
一个 loommux daemon
    ├── Resource A
    │     └── IPython kernel process group
    ├── Resource B
    │     └── IPython kernel process group
    ├── Resource C
    │     └── IPython kernel process group
    └── ...
```

每个 Resource 都是完整、独立的 IPython 工作台：

- 独立 namespace；
- 独立 PID；
- 独立执行序号；
- 独立输出历史；
- 独立 kernel generation；
- 独立生命周期；
- 独立当前执行；
- 独立进程组；
- 独立临时 runtime root。

而 daemon 负责：

- 资源寻址；
- 资源创建和复用；
- 资源生命周期；
- 空闲回收；
- 进程监督；
- 管理控制；
- Web 控制台；
- MCP 请求路由。

---

# 二、最核心的设计选择：保留“单资源引擎”，重写上层编排

现有 loommux 最大的价值，不是 `IPythonMCPAdapter` 这个类本身，而是它已经形成了一套比较完整的单 kernel 控制语义：

- execution 编号；
- IOPub 采集；
- stdout/stderr/result/traceback；
- wait；
- interrupt；
- restart；
- 历史 execution 保留；
- terminal text normalization；
- 图片展示；
- workspace 与 private runtime root 分离。

这些语义要保留，但类和模块边界可以全部重写。

新架构中不再有“大而全的 `IPythonMCPAdapter`”，而是两个核心对象：

```text
KernelResourceManager
        |
        +-- KernelResource
                |
                +-- IPythonEngine
```

其中：

## `KernelResourceManager`

负责多个资源，不碰具体 IPython 消息细节。

## `KernelResource`

代表一个逻辑工作台，拥有生命周期、租约、身份和一个 `IPythonEngine`。

## `IPythonEngine`

只管理一个 kernel，以及这个 kernel 的 execution、IOPub、输出和重启。

这样就形成一个清楚的边界：

```text
资源管理器决定“用哪个 kernel”
IPython 引擎决定“这个 kernel 怎么运行”
```

任何 MCP、HTTP、HTML、Header、Session ID 都不应该进入 `IPythonEngine`。

---

# 三、进程拓扑

Linux 上的真实进程关系建议设计为：

```text
loommux daemon
│
├── kernel resource A：session leader / process group leader
│   ├── ipykernel
│   ├── Python cell 启动的 shell
│   ├── 编译器
│   └── 其他后代进程
│
├── kernel resource B：session leader / process group leader
│   ├── ipykernel
│   └── 后代进程
│
└── kernel resource C：session leader / process group leader
    └── ...
```

每个 kernel 必须启动在自己的 Unix session 和 process group 中。

也就是说，创建 kernel 时应确保等价于：

```python
start_new_session=True
```

由此获得：

```text
kernel PID = session leader PID = process group ID
```

这样 daemon 才真正拥有一个完整资源，而不只是拥有 ipykernel 主进程。

当资源被回收时，目标不是杀掉一个 PID，而是结束整个进程组：

```python
os.killpg(kernel_pgid, signal.SIGTERM)
```

必要时：

```python
os.killpg(kernel_pgid, signal.SIGKILL)
```

这是 Linux 版本的核心不变量：

> **一个 KernelResource 对应一个独立进程组；Resource 的终结意味着整个进程组被终结。**

否则用户在 cell 中启动的后台服务、编译器、shell、子 Python 进程可能在 kernel 已回收后继续存在，资源管理就只是表面工作。

---

# 四、资源身份模型

资源需要三个不同概念，不能混成一个字符串。

## 1. `ResourceKey`

用于查找和复用资源。

```python
@dataclass(frozen=True)
class ResourceKey:
    scope: Literal["session", "named"]
    value: str
```

例如：

```text
session:fc74c9...
named:三楼网络排障沙盒
```

匿名资源的 `value` 是 MCP Session ID。

命名资源的 `value` 是调用方给出的公开名称。

## 2. `resource_id`

资源每次真正创建时获得一个不可变 ID，例如 UUID。

```text
resource_id = 922e581fa55c4d72a1fe5093be6eeacd
```

相同 `ResourceKey` 在资源被彻底回收后重新创建，会得到新的 `resource_id`。

因此：

- `ResourceKey` 表示“我要哪个逻辑房间”；
- `resource_id` 表示“这次实际创建出来的是哪个资源实例”。

## 3. `display_name`

只用于展示。

匿名资源可以显示：

```text
临时工作台-fc74c9a2
```

命名资源直接显示其公开名称。

这三个概念必须在代码中是不同字段，不能让一个字符串同时承担寻址、身份和展示。

---

# 五、Resource 的数据模型

建议核心对象如下：

```python
@dataclass
class KernelResource:
    resource_id: str
    key: ResourceKey
    display_name: str
    scope: ResourceScope

    state: ResourceState
    generation: int

    created_at: datetime
    last_activity_at: datetime
    idle_deadline: float

    active_uses: int

    engine: IPythonEngine | None
    start_future: asyncio.Future[None] | None
    stop_future: asyncio.Future[None] | None

    activity_version: int
```

资源状态定义为：

```python
class ResourceState(Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
```

这里不需要 `BUSY` 状态。

busy 是运行中的资源是否存在 active execution：

```python
resource.engine.current_execution is not None
```

生命周期和活动状态必须分开，否则以后会出现：

```text
这个资源是 busy，还是 stopping？
这个资源正在 stopping，但是否还有 active call？
```

这样的语义混乱。

---

# 六、资源状态机

完整状态转换应该是：

```text
           start success
STARTING -----------------> RUNNING
    |                          |
    | start failure            | recycle / idle timeout / daemon shutdown
    v                          v
  FAILED                    STOPPING
                               |
                               | process group fully terminated
                               v
                            STOPPED
```

`FAILED` 和 `STOPPED` 都不是可复用状态。

注册表只把 `STARTING` 和 `RUNNING` 资源视为当前有效资源。

资源启动失败后：

- 所有等待同一资源的调用得到相同启动错误；
- 该资源从 key 映射中移除；
- private runtime root 被清理；
- 下次访问同一个 key 可以重新创建新资源。

资源进入 `STOPPING` 后：

- 不接受新的执行；
- 不允许继续 heartbeat；
- 已经拿到的观察请求可以读取历史快照；
- 最终进程组结束后进入 `STOPPED`。

---

# 七、注册表并发：不能在全局锁里启动 kernel

这是整个资源管理器最重要的并发规则。

错误方式是：

```python
async with registry_lock:
    resource = await start_kernel()
```

因为 kernel 启动可能需要几秒钟。这期间所有其他资源的查询、创建、回收都会被挡住。

健康设计使用“占位资源 + 共享 Future”：

```text
请求 A 访问 named:x
    ↓
注册表中不存在
    ↓
锁内创建 STARTING Resource 和 start_future
    ↓
释放锁
    ↓
A 在锁外启动 kernel

请求 B 同时访问 named:x
    ↓
看到同一个 STARTING Resource
    ↓
获得相同 start_future
    ↓
等待 A 的启动结果

请求 C 访问 named:y
    ↓
独立创建并启动另一个资源
```

因此，同一 ResourceKey 的创建是 single-flight，不同 ResourceKey 的创建可以完全并行。

注册表锁只保护：

- key → resource 映射；
- id → resource 映射；
- 状态切换的线性化点；
- active use 计数；
- activity version。

注册表锁绝不包围：

- kernel 启动；
- kernel 关闭；
- ZMQ 等待；
- 输出读取；
- 进程等待；
- 文件系统清理。

---

# 八、资源使用与空闲回收

可以保留示例中的“租约”思想，但内部更准确的概念是 `ResourceUse`。

每次执行面请求路由到资源后，先获得一个 use guard：

```python
async with manager.use(selector) as resource:
    return await resource.engine.run_cell(...)
```

进入时：

```text
active_uses += 1
activity_version += 1
更新最后活动时间
```

退出时：

```text
active_uses -= 1
如果回到 0，则从当前时刻重新计算 idle_deadline
```

这样长执行期间即使原来的 deadline 已过，也不会被回收。

空闲回收条件是：

```python
resource.state is RUNNING
and resource.active_uses == 0
and monotonic() >= resource.idle_deadline
```

管理页面的资源列表刷新不应修改活动时间，否则页面开着时所有资源永远不会过期。

明确会刷新资源活动时间的操作可以是：

- `run_cell`；
- `interrupt`；
- `restart`；
- 显式 heartbeat；
- 获得当前资源并执行控制操作；
- 活跃执行结束。

只读观察操作，例如：

- list resources；
- resource snapshot；
- execution status；
- read output；
- search output；

不应改变 idle deadline。

这里使用：

- `datetime.now(UTC)`：用于展示；
- `time.monotonic()`：用于 TTL 判断。

绝不能用系统时钟计算 timeout，因为开发者调整时钟、系统时间同步或休眠恢复都可能影响 wall clock。

---

# 九、IPythonEngine 的内部结构

`IPythonEngine` 应当是完整的单 kernel 控制引擎：

```python
class IPythonEngine:
    workspace: Path
    python_path: Path

    generation: int
    runtime: LinuxKernelRuntime | None
    client: AsyncKernelClient | None

    executions: ExecutionStore
    current_execution: int | None
    next_execution: int

    iopub_task: asyncio.Task[None] | None
    kernel_watch_task: asyncio.Task[None] | None

    lifecycle_lock: asyncio.Lock
```

这里建议彻底转为 asyncio 模型，使用 Jupyter 的异步 client。

原因是新系统本身已经包含：

- 多 kernel；
- 多个 IOPub 消费循环；
- 多个 kernel exit watcher；
- TTL sweeper；
- 多个 HTTP/MCP 请求；
- 多个资源并行启动和关闭。

继续使用“每个 kernel 一个 collector thread + 多层 threading lock”会使状态所有权越来越模糊。

更健康的模型是：

> **所有引擎状态都只在 daemon 的 asyncio event loop 中修改。**

这样：

- 不需要 `threading.RLock`；
- 不需要跨线程 `_on_idle` 回调；
- `Execution` 快照不会在另一个线程中被同时修改；
- kernel exit 和 IOPub idle 可以在同一个事件循环里竞争并确定唯一终态；
- 资源回收和 execution 终结拥有清晰顺序。

阻塞的文件处理或特殊系统调用可以交给 `asyncio.to_thread()`，但领域状态不应在线程中修改。

---

# 十、Execution 模型

execution ID 在每个 Resource 内独立递增：

```text
Resource A: 1, 2, 3, 4...
Resource B: 1, 2, 3...
```

管理面上的完整执行坐标是：

```text
(resource_id, execution)
```

Resource 内部只需要整数 execution。

建议 Execution 状态为：

```python
class ExecutionState(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    KILLED = "killed"
    KERNEL_LOST = "kernel_lost"
```

其中：

- `COMPLETED`：收到对应执行的 IOPub idle，且没有 Python error；
- `ERROR`：收到 error 消息并最终 idle；
- `INTERRUPTED`：调用过 interrupt，最终错误是 KeyboardInterrupt；
- `KILLED`：restart、resource recycle 或 daemon shutdown 主动终止；
- `KERNEL_LOST`：kernel 非预期退出或通信通道消失。

`KERNEL_LOST` 非常重要。现有 loommux 在 kernel 意外退出时可能让 execution 永久保持 running。新实现必须把 kernel 退出看作正式事件。

当 kernel watcher 发现进程退出时：

```text
如果 current execution 仍为 RUNNING：
    标记为 KERNEL_LOST
    写入 kernel exit code
    设置 completed_at
    唤醒所有 wait 调用

将 engine 标记为不可运行
通知 KernelResource
```

资源可以随后进入 FAILED，也可以在下次请求时显式 restart。不要无声地自动重启，因为自动重启会模糊“上一代 namespace 已经消失”这一事实。

---

# 十一、Kernel generation

同一个 Resource 允许 restart。

restart 后：

- `resource_id` 不变；
- ResourceKey 不变；
- execution 序号继续递增；
- 已有 execution 历史继续可读；
- kernel namespace 清空；
- PID 改变；
- private runtime root 改变；
- `generation += 1`。

例如：

```text
resource_id: abc
generation: 1
execution: 1..12

restart

resource_id: abc
generation: 2
execution: 13..20
```

每个 execution 在提交时记录：

```python
kernel_generation: int
kernel_pid: int
kernel_execution_count: int | None
```

其中公开、稳定的坐标仍然是：

```text
resource_id + execution
```

kernel-local execution count 只用于诊断。

---

# 十二、执行提交与并发规则

同一个 IPython kernel 同一时间只接受一个顶层 cell。

新系统不排队。

如果一个资源已经有运行中的 execution，再调用 `run_cell`：

```json
{
  "ok": false,
  "status": "resource_busy",
  "resource_id": "...",
  "execution": 7
}
```

不排队是正确的，因为这是交互式、有状态工作台，不是任务队列。

如果偷偷排队，会出现：

- 调用方认为命令正在执行，实际上只是在等待；
- 前一个 cell 改变 namespace 后，后一个排队 cell 的语义可能改变；
- 调用方无法准确决定是否取消排队任务；
- 多客户端共享命名资源时，执行顺序变得隐蔽。

所以规则应当非常简单：

```text
同一资源：单 execution，不排队
不同资源：完全并行
```

观察操作则可以在 execution 运行期间并发进行：

- `execution_status`；
- `read_output`；
- `search_output`；
- `wait`；
- `resource_status`。

interrupt 和 restart 属于控制操作：

- interrupt 只发送一次中断信号；
- restart 终止当前 generation；
- 多个同时到达的 restart 通过 `lifecycle_lock` 合并成一次实际重启。

---

# 十三、IOPub 消息处理

每个 engine 只有一个 IOPub 消费任务：

```python
async def _iopub_loop(self) -> None:
    while runtime alive:
        message = await client.get_iopub_msg()
        self._handle_iopub(message)
```

不要让多个协程同时读取同一 IOPub channel，否则消息归属会失控。

提交 cell 时保存：

```text
msg_id → execution
```

收到 IOPub 消息后根据 `parent_header.msg_id` 定位 execution。

处理规则：

- `execute_input`：记录 kernel-local execution count；
- `stream/stdout`：追加 stdout 和 combined；
- `stream/stderr`：追加 stderr 和 combined；
- `execute_result`：追加 text result、combined 和 rich display；
- `display_data`：追加 text projection 和 rich display；
- `error`：记录错误和 traceback；
- `status/idle`：终结 execution。

Execution 的终结必须幂等。

也就是说，无论以下事件怎样竞争：

- idle；
- kernel exit；
- restart；
- force recycle；
- daemon shutdown；

都只能有一个事件成功把 execution 从 RUNNING 转为终态。

可以通过：

```python
execution.finish_if_running(final_state)
```

实现。

---

# 十四、后台输出的语义

IPython cell 可能启动后台线程或异步任务，在 cell 已经 idle 后继续输出。

这种输出不能随意追加到另一个正在运行的 execution，也不能丢进错误 execution。

建议为每个 Resource 增加一个独立的 `background` transcript：

```text
execution streams:
    combined
    stdout
    stderr
    result
    traceback

resource stream:
    background
```

如果一条 IOPub 消息：

- 有已知 parent msg ID；
- 但对应 execution 已经终态；

则将它写入 resource background stream，并记录来源 execution。

如果 parent msg ID 不可识别，也写入 background stream，来源标记为空。

这样不会破坏 execution 的“终态输出快照”语义，同时开发者仍然可以查看长期运行线程和后台任务的输出。

控制台资源详情页可以同时展示：

- 当前 execution；
- execution 历史；
- background output。

---

# 十五、输出存储：无限制，但结构必须高效

不设置任何自动输出截断、总量上限或图片数量上限。

但是“不限制”不等于应该继续使用低效数据结构。

当前 `LineLog` 使用：

```python
self._text += text
self._text.splitlines()
```

输出越来越大时会产生大量重复复制和重复扫描。

建议改成分段 journal：

```python
class TextJournal:
    chunks: list[str]
    line_starts: list[TextPosition]

    character_count: int
    utf8_byte_count: int
    line_count: int
```

append 时：

- 直接添加 chunk；
- 增量记录换行位置；
- 增量更新字符数和字节数；
- 不重新扫描全部历史。

读取完整输出时：

```python
"".join(chunks)
```

按行读取时根据 line index 定位 chunk，而不是先对整个文本 `splitlines()`。

搜索可以逐 chunk 或逐逻辑行执行。

这里没有任何后台自动截断。`line_range` 和 `max_chars` 仍然可以存在，但它们只是调用方主动选择的读取投影，不是系统限制。

`run_cell` 或 `wait` 在 execution 终态时直接返回完整输出，不再需要：

```text
300 行阈值
output_omitted
--full-output
```

因此 `# loommux: --full-output` 可以从新设计中完全删除。

控制指令只保留真正有意义的：

```python
# loommux: --wait 120
```

它只控制本次 MCP 调用等待多久，不控制 execution 生命周期。

---

# 十六、图片与展示事件

图片不应该写进文本日志，但可以保存在 execution 的 presentation journal 中：

```python
@dataclass(frozen=True)
class DisplayArtifact:
    ordinal: int
    mime_type: str
    data: bytes
    detail: str | None
```

在采集阶段：

- Base64 只解码一次；
- 内部保存 bytes；
- MCP 返回时再编码为协议需要的形式；
- 保留 IOPub 到达顺序。

Presentation journal 可以包含：

```python
PresentationText
DisplayArtifact
PresentationFailure
```

这样 text/image/text 顺序可以准确还原。

不设置图片数量或字节限制。只做格式正确性检查，因为无法解析的数据本身不是有效展示事件。

---

# 十七、Linux KernelRuntime 的完整关闭协议

每个 kernel 的关闭不是一句 `shutdown_kernel()`，而是固定的状态机。

## 正常资源回收

```text
1. Resource 进入 STOPPING
2. 禁止新的 submit
3. 若有 RUNNING execution，标记正在终止
4. 尝试通过 Jupyter control channel 请求 shutdown
5. 等待短暂宽限时间
6. 如果进程组仍存在，SIGTERM 整个 process group
7. 再等待
8. 如果仍存在，SIGKILL 整个 process group
9. waitpid/reap kernel 主进程
10. 停止 ZMQ channels
11. 取消 iopub task 和 watcher task
12. 清理 private runtime root
13. Resource 进入 STOPPED
14. 唤醒所有等待者
```

这里的等待时间不是资源配额，而是进程关闭协议本身。例如：

```text
Jupyter shutdown grace: 1.5 秒
SIGTERM grace: 1.5 秒
然后 SIGKILL
```

## interrupt

interrupt 只面向当前 execution：

```text
killpg(kernel_pgid, SIGINT)
```

但要谨慎：SIGINT 发送给整个 kernel process group 时，cell 创建的前台子进程也能收到中断，这通常正是开发工作台需要的行为。

## restart

restart 使用同样的完整关闭协议结束旧 generation，然后创建新的 runtime root 和 kernel process group。

不允许只换 Jupyter client 而留下旧进程。

---

# 十八、资源回收的线性化语义

手动回收资源时，应当先从路由表中摘除，再执行较慢的进程关闭。

线性化点是：

```text
key 不再指向该 RUNNING resource
```

之后：

- 已经获得 resource use 的请求可以完成；
- 新请求不会进入旧资源；
- 新请求可以为相同 ResourceKey 创建新 resource；
- 旧资源在后台进入 STOPPING 并清理。

这意味着短时间内可能存在：

```text
旧 named:x：STOPPING
新 named:x：STARTING
```

这是合理的，因为它们有不同 resource ID 和不同进程组，不存在身份混淆。

为了防止旧资源完成关闭时误删新映射，删除映射必须使用对象身份比较：

```python
if registry.by_key.get(key) is old_resource:
    del registry.by_key[key]
```

不能仅按 key 无条件删除。

---

# 十九、Daemon 关闭语义

整个 daemon 收到 SIGTERM、SIGINT 或应用退出时：

```text
1. 停止接受新的资源操作
2. 停止 sweeper
3. 把所有 STARTING/RUNNING 资源标记为 STOPPING
4. 并行关闭所有资源
5. 等待每个 kernel process group 被回收
6. 清理所有 private runtime root
7. 关闭 HTTP/MCP transport
8. daemon 退出
```

所有资源可以并行关闭，不需要串行逐个等待。

如果某个资源关闭失败，不影响其他资源继续关闭。

最终 daemon 退出前，应当确保自己创建的 kernel 进程组已经不存在。

由于整个系统是内存态，daemon 退出后：

- Resource registry 消失；
- execution history 消失；
- namespace 消失；
- resource ID 消失；
- 页面状态消失。

这正好符合本地开发工作台的生命周期，不需要恢复逻辑。

---

# 二十、统一的应用服务层

Transport 不直接操作 registry 和 engine，而是通过两个应用服务。

## `ExecutionService`

负责当前请求所选资源上的操作：

```python
class ExecutionService:
    async def run_cell(...)
    async def status(...)
    async def execution_status(...)
    async def read_output(...)
    async def search_output(...)
    async def wait(...)
    async def interrupt(...)
    async def restart(...)
    async def release(...)
```

## `ResourceAdminService`

负责整个 daemon：

```python
class ResourceAdminService:
    async def list_resources(...)
    async def get_resource(...)
    async def heartbeat(...)
    async def recycle(...)
    async def recycle_idle(...)
    async def recycle_all(...)
    async def read_background_output(...)
```

MCP、HTTP API 和 Web 控制台都调用这两个服务。

这样业务语义只有一份，不会出现：

- MCP recycle 和网页 recycle 行为不一样；
- 网页 heartbeat 会续租，但 MCP heartbeat 不会；
- 某个接口忘记处理 active execution。

---

# 二十一、请求路由

MCP 请求到达后，最外层只做一次上下文解析：

```python
@dataclass(frozen=True)
class RequestResourceContext:
    selector: ResourceKey
    operator: str | None
```

解析规则：

```text
存在 X-Process-Resource：
    scope = named
    value = 请求头解码后的名称

不存在：
    scope = session
    value = MCP Session ID
```

随后所有 execution tools 都使用同一个依赖函数：

```python
async def current_resource_context(ctx: Context) -> RequestResourceContext:
    ...
```

Header 和 FastMCP 的 `Context` 到此为止。

它们不能继续进入：

- registry；
- resource；
- engine；
- execution；
- output store。

内部对象只接收规范化后的 `ResourceKey`。

---

# 二十二、Web 控制台

控制台不再只是一个资源表格，而应该成为完整的本地工作台观察面。

页面结构可以是：

```text
左侧：资源列表
    名称
    Resource ID
    Scope
    State
    PID / PGID
    Generation
    当前 execution
    空闲时间

右侧：资源详情
    基本信息
    当前 execution
    execution 历史
    stdout/stderr/result/traceback
    background output
    restart / interrupt / recycle
```

通信建议使用：

- 普通 JSON API 执行控制操作；
- Server-Sent Events 推送资源状态变化和输出事件。

这样不需要每两秒完整轮询所有资源。

SSE 事件可以是：

```text
resource.created
resource.started
resource.activity
resource.stopping
resource.stopped
resource.failed
kernel.restarted
kernel.exited
execution.accepted
execution.output
execution.finished
```

页面首次连接获得完整 snapshot，随后只消费增量事件。

前端仍然可以保持原生 HTML/CSS/JavaScript，不需要为了这个本地控制台引入大型前端工程。

但应拆成：

```text
web/
    index.html
    console.css
    console.js
```

避免继续让一个 500 行 HTML 同时承载样式、DOM、API client 和业务行为。

---

# 二十三、错误模型

所有错误应当有稳定 code 和可读 message。

例如：

```text
resource_start_failed
resource_not_found
resource_stopping
resource_busy
kernel_not_running
kernel_lost
execution_not_found
execution_not_running
invalid_resource_name
invalid_wait_timeout
invalid_line_range
```

领域层返回结构化错误：

```python
@dataclass(frozen=True)
class OperationError:
    code: str
    message: str
    resource_id: str | None = None
    execution: int | None = None
```

MCP 层决定是否转换成 ToolError。

HTTP 层决定状态码。

控制台决定如何展示。

领域层不应该知道 ToolError、JSONResponse 或 HTML。

---

# 二十四、配置模型

所有配置只在 daemon 启动时读取一次，形成不可变 Settings：

```python
@dataclass(frozen=True)
class DaemonSettings:
    workspace: Path
    python_path: Path

    anonymous_idle_seconds: float
    named_idle_seconds: float
    sweep_interval_seconds: float

    kernel_start_timeout: float
    kernel_shutdown_grace: float
    process_term_grace: float

    host: str
    port: int
    mcp_path: str
```

workspace 对整个 daemon 固定。

所有资源：

- 使用同一个 workspace；
- 使用同一个 Python interpreter；
- 使用同一份基础环境；
- 但拥有独立 kernel namespace 和 private runtime root。

资源名称不允许动态选择 workspace 或 Python 解释器。否则资源寻址会混入环境配置，系统模型会快速变脏。

如果需要另一个项目或解释器，直接启动另一个 daemon。这对于开发者本地使用是最清晰的边界。

---

# 二十五、建议的源码结构

我建议最终结构是：

```text
src/loommux/
    config.py
    errors.py
    identifiers.py

    kernel/
        launch.py
        runtime.py
        session.py
        containment.py
        messages.py

    execution/
        model.py
        store.py
        output.py
        presentation.py
        terminal_text.py
        cell_control.py
        source_transform.py

    resources/
        model.py
        resource.py
        registry.py
        supervisor.py
        routing.py

    services/
        execution_service.py
        resource_admin_service.py

    transports/
        mcp_server.py
        mcp_tools.py
        result_policy.py
        http_routes.py
        event_stream.py

    web/
        index.html
        console.css
        console.js

    application.py
    entrypoint.py
```

职责关系为：

```text
transports
    ↓
services
    ↓
resources
    ↓
kernel + execution
```

禁止反向依赖。

尤其是：

- `execution` 不依赖 FastMCP；
- `kernel` 不依赖 Starlette；
- `resources` 不解析 Header；
- `services` 不生成 HTML；
- `transports` 不直接发送 Unix signal；
- `web` 不决定资源生命周期规则。

---

# 二十六、必须成立的工程不变量

整套系统是否健康，可以用下面这些不变量检查。

1. 一个 Resource 在任意时刻最多拥有一个 kernel generation。
2. 一个 Resource 的 kernel generation 对应一个独立 Linux session/process group。
3. 一个 Resource 同一时间最多有一个 RUNNING execution。
4. 不同 Resource 可以并行执行。
5. 同一个 ResourceKey 的并发首次访问只创建一个 Resource。
6. 全局注册表锁中绝不等待子进程或 ZMQ。
7. execution 一旦进入终态，不再改变其终态分类。
8. kernel 非预期退出一定会终结当前 execution。
9. restart 保留 Resource ID 和 execution 历史，但更换 PID、generation 和 runtime root。
10. recycle 后重新访问相同 ResourceKey 会创建新的 Resource ID。
11. 空闲扫描绝不回收 `active_uses > 0` 的资源。
12. 管理页面观察资源不会延长资源寿命。
13. daemon 退出时终结所有由它创建的进程组。
14. 所有输出按 IOPub 到达顺序保存。
15. 不自动截断输出。
16. 不自动删除 execution 历史。
17. workspace 和 Python interpreter 在 daemon 生命周期中固定。
18. MCP、HTTP 和 Web 使用同一套应用服务语义。
19. 所有时间截止判断使用 monotonic clock。
20. 资源管理状态只由 asyncio event loop 修改，不跨线程共享修改。

---

# 最后的整体评价

如果按照这个设计重构，loommux 就不再是“一个 MCP Server 包着一个 IPython kernel”，也不是“把示例 WorkerManager 复制进去”。

它会成为一个非常清晰的 Linux 本地开发守护进程：

```text
Resource Manager
    管理资源身份、复用、租约和回收

Kernel Resource
    管理一个逻辑工作台的生命周期

IPython Engine
    管理一代 kernel 及其 execution

Linux Runtime
    管理真正的进程组和退出协议

Execution Store
    保存内存中的执行历史和无限制输出

Application Services
    提供统一的执行与管理语义

MCP / HTTP / Web
    提供不同的操作表面
```

这里最有 taste 的地方，不是把类拆得很多，而是每一层都只拥有一种状态：

- Registry 拥有资源映射；
- Resource 拥有生命周期；
- Engine 拥有 kernel 和 execution sequence；
- Runtime 拥有 PID、PGID 和 runtime root；
- Execution 拥有一次执行及其输出；
- Transport 只拥有请求和响应。

最终系统也不会出现那种“一个 `server.py` 从请求头一路管到 SIGKILL”的失控结构，更不会把 FastMCP、Jupyter、Starlette、进程组和页面 DOM 全部缠在一起。

一句话总结这张图纸：

> **以 Resource 为长期逻辑边界，以 kernel generation 为进程边界，以 execution 为观察边界，以 Linux process group 为回收边界，以 asyncio 单写者模型为并发边界。**

我认为这就是这个场景下最健康、最统一，也最有工程品味的一整套设计。
</Taylor>

<Miran id="3">
我说的什么什么限制输出限制，我指的是你的配儿，是你的守护进程，是你的管理平台不会限制。像什么 300 行那个什么的，那个是语义，那是业务呀，我的天呐，那怎么可能不要呢？ 我说的是你不要自作聪明的让守护进程对 Linux 机器本身去做一些什么配额限制，然后呢不让申请资源，什什么鬼啊？你搞得跟那个云服务器一样，干什么呢？  然后呢，你前面是吧，还提到什么持久化存储，没有持久化存储，东西就是在内存里面，是吧？ 然后呢我们 Python 的支持将改为 3.12+，以下呢不再承诺 ， 同时呢，不再支持 Windows 啊，啊，从这次开始，移除所有的关于这个 Windows 平台适配的代码，将只支持 Linux 好的那你再想想吧，看一下有什么要跟我补充说明的吗？
</Miran>

<Taylor id="3">
**Clarifying Python 3.12+ Linux-only scope****Detailing Linux process group management****Evaluating subreaper and cgroup trade-offs**

**Clarifying resource scope and output limits****Defining execution policies and Linux-only support****Confirming resource manager concurrency design**

**Clarifying resource application and lazy creation**

明白了，这次我准确理解你的边界了。上一轮我把两种完全不同的“限制”混在了一起：

1. **loommux 自己的业务交付语义**  
   例如 300 行自动交付阈值、`--full-output`、`read_output`、图片交付规则。这些是 Agent 如何观察一次 execution 的产品语义，当然应该保留。

2. **守护进程对开发者机器施加的资源政策**  
   例如最大 kernel 数、最大输出总量、每个资源内存上限、全局 CPU 限额、超出额度拒绝创建资源之类。这些全部不要。

这两者确实不能混为一谈。300 行规则不是“限制开发者运行”，而是：

> execution 的完整输出仍然全部保存在内存中，只是默认工具响应不主动塞入超过 300 行的正文；调用方仍然可以通过 `read_output`、`search_output` 或 `--full-output` 完整消费。

所以我需要对上一份设计作以下明确修订和补充。

---

# 一、300 行和 `--full-output` 保持不变

新的多资源守护进程不改变现有 execution 输出交付语义。

一个 execution 的输出仍然包括：

- `combined`
- `stdout`
- `stderr`
- `result`
- `traceback`
- 有序的图片展示事件

所有输出都完整保存在该 execution 的内存记录中，不自动删除，不自动截断。

默认行为保持：

- execution 还在运行时，`run_cell` 和 `wait` 不返回不完整的全部正文；
- execution 终态且 combined 不超过 300 行时，直接交付完整正文；
- execution 终态但超过 300 行时，返回输出统计和省略原因；
- `read_output` 可以读取完整输出或指定行范围；
- `search_output` 可以搜索完整输出；
- `# loommux: --full-output` 让该 execution 在终态时直接交付完整 combined 输出；
- `# loommux: --wait N` 只控制当前工具调用的初始观察时间。

因此我上一轮所说的“取消 300 行阈值、删除 `--full-output`”是错误修订，这部分收回。

健康的分层应该是：

```text
Resource Manager
    不限制输出，不关心输出大小

IPython Engine
    完整保存 execution 输出

Presentation Policy
    决定本次 MCP 调用怎样把输出交付给模型
```

300 行规则属于最外层的 `Presentation Policy`，不属于 Resource Manager，更不属于 Linux 资源管理。

同样，现有图片内容的交付、格式验证、展示顺序和 MCP content 组织，也应该作为 execution presentation 契约继续存在。多资源改造不应顺手改变这些已经成熟的业务语义。

---

# 二、守护进程不设置任何资源准入和机器配额

新的 daemon 不存在下面这些概念：

- 最大资源数量；
- 最大同时运行 kernel 数；
- 每个 kernel 的 CPU 限制；
- 每个 kernel 的内存限制；
- daemon 总内存限制；
- 每个资源最大 execution 数；
- 每个资源最大输出字节数；
- daemon 最大输出字节数；
- 最大图片数量；
- 最大资源创建频率；
- 资源申请审批；
- admission controller；
- 达到某个阈值后拒绝创建资源。

资源访问规则就是：

```text
请求到达
    ↓
已经存在对应资源：复用
    ↓
不存在：创建
```

如果开发者创建了十个命名资源，就启动十个 kernel。

如果创建了一百个，就尝试启动一百个。

守护进程不站在开发者和 Linux 机器之间替开发者判断“你不应该再创建了”。

创建失败只可能来自真实运行状态，例如：

- `fork`/进程启动本身失败；
- Python 解释器不可执行；
- ipykernel 启动失败；
- workspace 不存在；
- ZMQ readiness 超时；
- daemon 正在关闭；
- 目标资源已经处于 stopping；
- 同一个 kernel 当前正在执行另一个 cell。

这些是事实性失败，不是守护进程制定的配额政策。

空闲 TTL 仍然保留，因为它是资源生命周期语义：

> 用户一段时间没有使用某个临时工作台后，daemon 自动结束它。

TTL 不是配额，也不是拒绝资源创建。资源被空闲回收后，下次访问会自然创建新的资源。

---

# 三、所有业务状态只放在内存中

新的设计没有持久化执行数据库，也没有落盘的 execution store。

以下内容全部只存在于 daemon 内存中：

- Resource 注册表；
- ResourceKey 到 Resource 的映射；
- resource ID；
- resource lifecycle；
- kernel generation；
- execution 记录；
- execution 状态；
- stdout/stderr/result/traceback；
- combined transcript；
- presentation 事件；
- 图片内容；
- background output；
- 最后活动时间；
- 空闲 deadline；
- 管理控制台事件流。

daemon 退出后，这些内容一起消失。

重启 daemon 后：

- resource ID 重新生成；
- execution 序号重新从 1 开始；
- 所有 kernel namespace 消失；
- 所有输出历史消失；
- 所有命名资源重新创建；
- 控制台显示为空。

这就是正确行为，不需要恢复机制。

## private runtime root 不算持久化存储

每个 kernel 仍然需要一个临时 runtime root，用来放：

- Jupyter connection file；
- 私有 IPython 配置目录；
- 私有 Jupyter 配置目录；
- kernel 启动期间必须存在的运行文件。

例如：

```text
/tmp/loommux-kernel-xxxxxxxx/
    kernel.json
    ipython/
    jupyter/
```

这些文件不是业务状态，也不保存 execution 输出。

它们属于 Linux 子进程正常运行所需的临时控制文件：

- kernel 启动前创建；
- kernel generation 结束后删除；
- restart 时旧目录删除、新目录重新创建；
- daemon 退出时全部清理。

因此系统仍然是纯内存业务模型。

## 输出存储也不落盘

我上一轮提出过输出超过一定程度后 spool 到文件，这一点同样不符合现在的边界，应当删除。

输出继续全部留在内存中。

但内部实现仍然应该从低效的单字符串拼接改成高效的内存结构，例如：

```python
class TextJournal:
    chunks: list[str]
    line_index: list[LinePosition]
    character_count: int
    utf8_byte_count: int
```

这是为了让大输出读取和搜索更健康，不是为了限制输出，也不是为了把输出转移到磁盘。

---

# 四、Python 版本正式改为 3.12+

这次重构后，项目的运行时契约可以明确写成：

```toml
requires-python = ">=3.12"
```

不再承诺 Python 3.10 和 Python 3.11。

对应地应删除：

- Python 3.10 classifier；
- Python 3.11 classifier；
- 以 3.10 为 target 的 Ruff 配置；
- BasedPyright 中的 `pythonVersion = "3.10"`；
- 专门照顾 3.10/3.11 的语法和 typing 写法；
- CI 中的 3.10、3.11 测试矩阵；
- 文档里关于 3.10+ 的说明；
- 临时 Python 3.10 兼容验证环境。

新的源码可以直接使用 Python 3.12+ 的能力，包括：

## PEP 695 类型别名和泛型语法

例如：

```python
type ResourceId = str
type ExecutionId = int
type ResourceScope = Literal["session", "named"]
```

泛型数据结构也可以使用新语法，不再围绕旧版 `TypeVar` 写额外样板。

## `asyncio.TaskGroup`

daemon 启动多个资源关闭任务、多个 kernel 后台任务时，可以正式使用结构化并发：

```python
async with asyncio.TaskGroup() as group:
    for resource in resources:
        group.create_task(resource.stop())
```

这比散落的 `asyncio.gather(..., return_exceptions=True)` 更适合作为新架构的默认方式。

## `ExceptionGroup` 与 `except*`

daemon 关闭多个资源时，可以保留多个失败，而不是只看到第一个异常。

## 更现代的 typing 和 dataclass 写法

领域对象可以普遍使用：

```python
@dataclass(slots=True, frozen=True)
```

运行时可变聚合对象则使用：

```python
@dataclass(slots=True)
```

`slots=True` 不是为了限制资源，而是让大量 execution event、line position 和 presentation element 更紧凑、字段更明确。

## `typing.override`

重写 Runtime、Containment 等抽象接口时，可以显式标记：

```python
from typing import override
```

新代码不需要再照顾旧版本 typing 行为。

---

# 五、正式删除所有 Windows 代码

新的项目契约是：

```text
Operating System :: POSIX :: Linux
```

不再写“跨平台”，不再写“Windows 支持”，也不再保留任何运行时平台分支。

应当直接删除：

- `_WindowsKernelContainment`；
- Windows Job Object；
- `win32api`；
- `win32con`；
- `win32job`；
- `taskkill`；
- `subprocess.CREATE_NO_WINDOW`；
- `sys.platform == "win32"` 分支；
- Windows 上 interrupt 通过重启 kernel 模拟的特殊逻辑；
- Windows 路径说明；
- Windows 安装文档；
- Windows CI；
- Windows 测试 skip；
- `pywin32` 条件依赖；
- “平台缺少 Unix process group”之类的抽象空壳。

也就是说，现有：

```python
class _KernelContainment:
    def attach(...): ...
    def close(...): ...
```

这种为了跨平台而存在的空抽象不再需要。

新的实现可以直接叫：

```python
class LinuxKernelProcess:
    ...
```

或者：

```python
class LinuxKernelRuntime:
    ...
```

并且明确依赖：

- Unix signal；
- process group；
- session leader；
- `os.killpg`；
- `/proc` 可观察信息；
- Linux 进程退出语义。

这种情况下，代码反而会更加诚实、简洁。

---

# 六、Linux process group 必须由 loommux 明确创建

这里还有一个值得补充的重要点。

不能仅仅在关闭时假设：

```python
kernel.pid == kernel process group id
```

启动时必须由 loommux 明确保证。

因此 `LinuxKernelRuntime` 不应该只把全部行为模糊地交给默认 `KernelManager`，然后在外面猜测进程组关系。

启动契约应该明确包含：

```text
kernel process:
    starts a new session
    becomes session leader
    becomes process group leader
```

也就是底层进程创建必须确定使用：

```python
start_new_session=True
```

启动完成后应记录：

```python
kernel_pid = process.pid
kernel_pgid = os.getpgid(process.pid)
kernel_sid = os.getsid(process.pid)
```

并验证：

```text
kernel_pid == kernel_pgid
kernel_pid == kernel_sid
```

如果这个不变量不成立，kernel runtime 启动应视为失败，因为后续无法可靠地按资源边界发送信号。

最终资源快照可以直接展示：

```json
{
  "pid": 12345,
  "pgid": 12345,
  "sid": 12345
}
```

这里的 Linux process group 是资源所有权边界，不是资源额度限制。

---

# 七、进程组边界的准确含义

还需要把一件事说清楚。

普通 kernel 子进程会继承 kernel 的 process group，因此：

```text
ipykernel
├── bash
├── uv
├── python
└── compiler
```

通常都可以通过 `killpg()` 一起处理。

但是如果用户代码自己调用 `setsid()`、显式 double-fork，或者启动一个主动脱离当前 session 的 daemon，那么它是在主动脱离 loommux 的资源进程组。

新系统不需要为了追捕这种显式脱离行为而引入额外的机器级管理设施。

loommux 的清理契约可以非常准确地定义为：

> loommux 负责终止属于该 kernel Unix process group 的全部进程。主动创建新 session 并脱离该进程组的进程，不再属于该 KernelResource 的进程边界。

这是一条清晰的操作系统语义，比扫描整台机器上的进程树、猜测父子关系或引入复杂控制设施健康得多。

---

# 八、Linux 上 interrupt 与 recycle 的区别

只支持 Linux 后，这两个动作可以定义得非常漂亮。

## interrupt

对当前 kernel process group 发送：

```python
os.killpg(pgid, signal.SIGINT)
```

它用于中断当前 cell，并尽量保留 kernel namespace。

最终 execution 根据 IOPub 结果进入：

- `interrupted`；
- `error`；
- 或极少数情况下 `kernel_lost`。

## restart

restart 不是 interrupt。

restart 结束整个旧 process group，创建新的 kernel generation：

```text
旧 generation：
    Jupyter shutdown request
    SIGTERM process group
    SIGKILL process group（必要时）
    清理 runtime root

新 generation：
    创建 runtime root
    启动新 session/process group
    等待 kernel ready
```

历史 execution 保留在内存中。

## recycle

recycle 结束整个 Resource：

- execution 历史随 Resource 对象一起释放；
- ResourceKey 映射移除；
- resource ID 失效；
- process group 结束；
- runtime root 删除。

因此：

```text
interrupt：控制一次执行
restart：替换一代 kernel，保留 Resource
recycle：删除整个 Resource
```

三者不能共用一个模糊的 `stop()` 语义。

---

# 九、资源管理器没有“申请资源”概念

我认为这里可以把措辞也修正一下。

这个 daemon 不是资源申请平台，所以内部不应该出现：

```text
申请资源
批准资源
拒绝资源
资源额度
容量规划
```

更准确的是：

```text
resolve resource
get or create resource
use resource
release resource
recycle resource
```

Manager 的职责只是保证：

- 同一个 key 映射到同一个运行资源；
- 并发首次访问不重复创建；
- 不同资源可以并行启动；
- stopping 资源不会再收到新操作；
- 空闲资源被自动回收；
- daemon 退出时清理全部资源。

它不负责评判开发者创建了多少资源。

---

# 十、业务输出策略与资源层必须彻底解耦

最终建议把这一点写进架构不变量。

`KernelResourceManager` 的资源快照可以知道：

- execution 数量；
- 当前 execution；
- 输出统计数字。

但它不应该知道：

- 300 行阈值；
- `--full-output`；
- MCP TextContent；
- 图片交付规则；
- output omission notice；
- `In [N]:` 如何格式化。

这些属于：

```text
Execution
    保存完整事实

PresentationPolicy
    决定一次工具调用怎么呈现这些事实
```

因此，一个 execution 的内存记录可能有 50,000 行输出。

Resource Manager 只知道它存在。

当 `run_cell` 返回时，PresentationPolicy 根据现有业务规则返回省略提示。

当 `read_output` 不带范围时，返回完整 50,000 行。

当 execution 带 `--full-output` 时，`wait` 在终态返回完整 50,000 行。

守护进程既不删除，也不拒绝，也不回收这个 execution。

这就是我现在对你所说“不要输出配额”的准确理解。

---

# 十一、修订后的系统边界

现在整套设计可以收敛成下面这张图：

```text
Linux loommux daemon
│
├── 内存 Resource Registry
│     ├── session resources
│     └── named resources
│
├── KernelResource
│     ├── 内存 execution history
│     ├── 内存 output journals
│     ├── 内存 presentation events
│     └── IPythonEngine
│           ├── Linux process group
│           ├── private temporary runtime root
│           ├── AsyncKernelClient
│           ├── IOPub collector
│           └── kernel exit watcher
│
├── Execution Presentation Policy
│     ├── 300 行默认交付语义
│     ├── --full-output
│     ├── read_output
│     ├── search_output
│     └── text/image MCP content
│
├── MCP execution surface
├── Resource management surface
└── Web console
```

整个系统：

- Python 3.12+；
- Linux only；
- 业务状态纯内存；
- 不设置机器资源额度；
- 不限制资源创建数量；
- 不限制 execution 历史数量；
- 不限制输出存储量；
- 保留现有输出交付语义；
- 每个 kernel 使用独立 Unix session/process group；
- daemon 退出时清理全部资源；
- resource recycle 时释放其全部内存状态；
- restart 时保留资源和 execution 历史，仅替换 kernel generation。

---

# 十二、我现在唯一还要强调的设计区别

接下来重构时，最容易再次混淆的其实就是下面四个概念：

| 概念 | 是否保留 | 含义 |
|---|---:|---|
| 300 行默认交付阈值 | 保留 | MCP 响应展示语义 |
| `--full-output` | 保留 | 单次 execution 的显式完整交付策略 |
| 空闲 TTL | 保留 | Resource 生命周期与自动清理 |
| 机器资源配额 | 不存在 | daemon 不替开发者限制资源使用 |

以及：

| 存储类型 | 是否存在 |
|---|---:|
| 内存 execution store | 存在 |
| 内存 output journal | 存在 |
| 内存 presentation journal | 存在 |
| 临时 Jupyter runtime 文件 | 存在，仅运行所需 |
| 落盘 execution 数据库 | 不存在 |
| 输出 spool 文件 | 不存在 |
| daemon 重启恢复 | 不存在 |

现在边界就完全一致了。

最后一句总结：

> **多资源守护进程只负责创建、复用、监督和回收 Linux kernel process group；它不限制开发者使用机器。Execution 的 300 行、完整输出、读取和搜索规则仍然是 loommux 自己的重要业务语义，并完整保留。所有资源、execution 与输出只存在于 daemon 内存中，项目从此次重构起只支持 Python 3.12+ 和 Linux。**
</Taylor>
