# Coding Agent 控制面设计

## 1. 文档地位

本文定义 `loommux` 的 coding agent control plane。该对象由 MCP host
提供的启动上下文、一个持久 IPython kernel、八个 MCP tools、execution
记录、文本输出投影和有序图像展示投影构成。

本文是 workspace 解析、kernel 启动、文本输出、图像展示输出和相关黑盒验收的事实源。实现、
测试、README、tool docstring 与其他设计文档不得定义与本文冲突的行为。

`loommux` 的目标消费者是通过离散请求提交、观察和控制 Python 的 coding
agent。普通人类交互式终端不是本文定义的对象。

## 2. 问题世界

### 2.1 请求响应式 Python 工作

coding agent 不持续观看终端屏幕。它发起一个工具调用，读取返回的可见文本，
并据此决定下一次调用。它不能依赖终端颜色、光标位置、分页器或人工输入来
维持工作流。

一个 server process 拥有一个 kernel。kernel namespace 中的变量、导入和
定义在该 process 的生命周期内持续存在。每一次被接受的 Python cell 都拥有
由 `loommux` 分配的正整数 `execution`。该整数是 agent 观察一次执行的唯一
公共坐标。

### 2.2 八工具控制闭环

下表定义八个 tools 的不可替代职责。它们共同构成 agent 对持久 kernel 的
请求响应闭环。

| Tool | 直接职责 | 不承担的职责 |
| --- | --- | --- |
| `run_python` | 提交原始 Python cell，并返回该 execution 的当前可观察结果。 | 不无限等待，不解释历史日志。 |
| `python_status` | 观察 kernel、workspace 和最近执行的运行状态。 | 不返回日志正文。 |
| `python_execution_status` | 观察一个 execution 的生命周期状态和元数据。 | 不读取日志正文。 |
| `read_python_output` | 读取一个 execution 的指定文本流与行范围。 | 不改变 kernel 状态。 |
| `search_python_output` | 在一个 execution 的指定文本流中定位匹配文本。 | 不改变 kernel 状态。 |
| `wait_python` | 在给定等待时长内等待一个 execution 进入可观察状态。 | 不终止 execution。 |
| `interrupt_python` | 向当前运行的 execution 请求中断。 | 不重启 kernel。 |
| `reset_python` | 替换 kernel，并保留既有 execution 的可读记录。 | 不保留旧 kernel namespace。 |

正常工作流是：`run_python` 创建 execution；完成的小输出直接成为观察面；
运行中的 execution 由 `wait_python`、`python_execution_status`、
`read_python_output` 或 `search_python_output` 继续观察；不再需要的运行由
`interrupt_python` 控制；无法继续使用的 kernel 由 `reset_python` 替换。该
闭环不要求 agent 持续附着到终端，也不要求新增 profile 选择或额外交互工具。

### 2.3 现有隐式状态

kernel 子进程继承 server process 的环境。该环境可能含有颜色强制变量、分页器
变量、用户级 IPython/Jupyter 配置位置和其他与本次 agent session 无关的状态。

IPython 的默认行为会读取用户级 profile，持久保存输入历史，并保留输出缓存。
这些状态不是通过八个 tools 建立或观察的对象。它们可能写入用户 home、保留大型
对象，或在不同 server session 之间引入不可见差异。

workspace 解析曾通过从 launch cwd 向上搜索同名 Python 文件实现。目录树中出现
一个文件即可改变 workspace，并在 kernel 创建前执行 Python。项目目录不是
自动获得执行 server 配置代码权限的来源。

IOPub 文本事件可以携带 ANSI terminal formatting。实际 Python 库可以在非 TTY
的 Jupyter 路径中产生这类格式。仅依赖 `NO_COLOR` 或移除某个环境变量不能保证
所有库都停止发送控制序列。若 `stdout`、`stderr`、`result`、`traceback` 或
`combined` 对同一类控制序列采用不同处理，agent 获得的日志就不是统一的文本对象。

## 3. 期望效果与边界

### 3.1 期望效果

coding agent control plane 必须满足下列状态。

1. 正常八工具闭环只依赖公开 execution、kernel 状态、文本日志和有序图像展示，不依赖用户的
   IPython profile、输入历史、输出缓存或终端显示能力。
2. 所有公开文本流都是可阅读、可搜索、可引用的普通文本。terminal formatting
   不能改变文本解释或要求调用方模拟终端。
3. workspace 的默认来源是 MCP host 的 launch cwd。动态发现 workspace 是显式
   授权的 host 能力。
4. kernel 以启动 `loommux` 的 Python 解释器运行。workspace 配置不能选择
   第二个解释器。
5. kernel start 与 kernel reset 具有同一套 coding-agent 启动语义。

### 3.2 责任边界

本文不授权 `loommux` 改写被执行项目的依赖、网络、代理、凭据、时区、locale、
警告策略、随机种子、matplotlib backend 或 shell 命令业务参数。它们属于用户
代码和项目环境的语义。

本文不定义人类终端模拟、浏览器富媒体渲染、多 kernel session、持久 execution
数据库、动态 workspace 切换或宿主插件框架。图像展示的 MCP 交付由
`ipython-mcp-multimodal-output-design.md` 定义。它们不是八工具闭环成立的条件。

### 3.3 Transport 与结果通道选择

启动 entrypoint 接受两个相互独立的 host 选择。`--transport stdio` 使用 host
启动的子进程标准输入输出；`--transport streamable-http` 在指定 host、port 与 path
上提供远程 MCP endpoint。transport 只改变 MCP message 的承载方式，不改变一个
server process 只拥有一个 kernel 的边界，也不改变八工具、workspace、execution、
日志或图像投影。

`--result-mode structured` 返回 `content` 与 `structuredContent`；
`--result-mode content` 只返回 `content`。该选择只改变调用方可观察的结果通道，
不改变同一 execution 的模型可读文本或图像 content。server 不自动推断客户端能力；
host 必须显式选择适合其客户端的模式。

`loommux` 的无参数默认值是 structured stdio。`loommux-content` 的无参数默认值
是 content-only Streamable HTTP，并保留该 explicit host composition 的 Codex
workspace resolver。两个 executable 都可以显式覆盖 transport 与结果通道。

## 4. 控制面结构

控制面由三个输入层和一个输出层组成。

| 层 | 输入来源 | 负责对象 | 不可控制对象 |
| --- | --- | --- | --- |
| Host workspace 层 | MCP host 的 cwd 与显式 resolver 路径。 | kernel workspace。 | kernel interpreter、IPython policy。 |
| Kernel bootstrap 层 | `loommux` 内置 coding-agent policy。 | kernel command、私有 runtime root、受控 child environment。 | 项目业务环境语义。 |
| Execution 层 | 八个 MCP tools。 | execution 生命周期、kernel 控制、日志读取。 | workspace 的运行时切换。 |
| Output projection 层 | IOPub text 与展示 events。 | stream、combined log、monitor text 与有序图像内容。 | 终端屏幕与浏览器渲染。 |

host workspace 层只在 server 启动前决定工作目录。Kernel bootstrap 层只在
kernel start 和 reset 时构造子进程。Execution 层不修改 host 配置。Text
projection 层不启动或控制 kernel。任何实现不得让一个层通过隐式全局状态接管
另一个层的职责。

## 5. Host Workspace Resolver

### 5.1 Canonical surface

`LOOMMUX_WORKSPACE_CONFIG` 是 workspace resolver 的唯一配置入口。

环境变量未设置时，workspace 等于解析后的 launch cwd。此路径是 MCP host 启动
server process 时指定的 cwd。

环境变量设置时，其值必须是绝对路径，且必须指向一个普通文件。该文件是 host
明确授权执行的 Python resolver。resolver 使用下列 public surface：

```python
from pathlib import Path


def resolve_workspace(launch_cwd: Path) -> Path:
    """Return the directory used as the persistent kernel workspace."""
```

返回的 `str` 或 `Path` 按 launch cwd 解释为路径；最终结果必须存在且为目录。
resolver 的唯一输出是 workspace 路径。resolver 不选择 Python 解释器，不设置
kernel 环境，不注册 tool，不修改 output policy。

### 5.2 授权规则

workspace 树及其祖先目录中的文件不具有自动配置权。`loommux` 不搜索
`loommux_workspace.py`，不搜索 `.git`、`.codex`、`.claude`、`pyproject.toml`
或其他目录标记。

`LOOMMUX_WORKSPACE_CONFIG` 的绝对路径是唯一的执行授权。MCP host 的所有者可将
resolver 放在任意受信任位置，例如用户配置目录、企业配置仓库或受控的宿主配置
目录。该位置不是 `loommux` 的隐式约定，必须由 host 明确提供。

官方 examples 提供 generic resolver 与 Codex resolver。Codex resolver 可以
从 launch cwd 向上寻找最近 `.codex` 目录并返回其父目录；不存在该标记时返回
launch cwd。该行为属于示例 resolver，不属于 `loommux` 默认行为。

手动启动的 `loommux-content` entrypoint 是一个明确选择 Codex resolver 的 host
composition：仅当 `LOOMMUX_WORKSPACE_CONFIG` 未设置时，其 `main()` 才将包内
Codex resolver 的绝对路径写入该变量。调用该 entrypoint 是 host 对该规则的显式
选择；host 已提供的变量优先，通用 `create_mcp()` 与 `loommux` entrypoint 仍遵循
未设置变量时的 launch cwd 默认规则。

### 5.3 解释器规则

kernel interpreter 始终是 server process 的 `sys.executable`。这是已经成功导入
`loommux` 与 `ipykernel` 的解释器。workspace resolver 不拥有解释器选择权。

### 5.4 失败表面

resolver 配置错误必须在 MCP tools 暴露前使 server 初始化失败。不得静默回退到
launch cwd。

| 状态 | 条件 |
| --- | --- |
| `workspace_config_not_absolute` | `LOOMMUX_WORKSPACE_CONFIG` 不是绝对路径。 |
| `workspace_config_not_found` | 配置路径不存在。 |
| `workspace_config_not_file` | 配置路径不是普通文件。 |
| `workspace_config_load_failed` | resolver 文件无法加载或执行。 |
| `workspace_config_invalid_return` | resolver 未返回 `str` 或 `Path`。 |
| `workspace_not_found` | 解析后的 workspace 不存在。 |
| `workspace_not_directory` | 解析后的 workspace 不是目录。 |

`python_status` 必须公开 `workspace_resolution`。其 authored surface 只能是
`launch_cwd` 或 `explicit_config`。该字段说明 workspace 的来源类别，不公开
resolver 文件内容或私有配置路径。

## 6. Coding-Agent Kernel Bootstrap

### 6.1 KernelLaunch

每一个 kernel session 由一个 `KernelLaunch` 对象启动。该对象由以下元素组成：

1. 运行 `ipykernel_launcher` 的 Python command。
2. child process environment。
3. 由该 kernel session 独占的 private runtime root。

`KernelSession` 是 private runtime root 的拥有者。它在 kernel 启动前创建该
目录，在正常 shutdown、启动失败和 reset 替换旧 session 时清理该目录。private
runtime root 不得位于 workspace 或用户 home。

start 与 reset 必须由同一 `KernelLaunch` 规约构造。reset 产生的新 kernel 不得
继承旧 kernel 的 namespace、IPython history、output cache 或 private runtime
root。

### 6.2 IPython policy

coding-agent kernel command 必须显式设置下列 trait。

| Trait | 值 | 公开理由 |
| --- | --- | --- |
| `InteractiveShell.colors` | `nocolor` | IPython 自身不产生终端颜色格式。 |
| `InteractiveShell.cache_size` | `0` | `Out[n]` 不保留大型历史结果。 |
| `HistoryManager.enabled` | `False` | IPython 不将 agent cell 写入持久 history。 |
| `InteractiveShellApp.exec_PYTHONSTARTUP` | `False` | 用户机器的 `PYTHONSTARTUP` 不成为 kernel 输入。 |

IPython 的其他默认交互语义保持默认。本文不因其源自人类 IPython 而自动改写
automagic、xmode、top-level await 或最后表达式展示。没有已观察到的 agent 工作流
摩擦时，这些行为不构成修改授权。

### 6.3 Private configuration root

private runtime root 包含彼此独立的 IPython 与 Jupyter 配置目录。child process
必须同时满足下列条件：

1. `--ipython-dir` 指向 private IPython directory。
2. `IPYTHONDIR` 指向同一 private IPython directory。
3. `JUPYTER_CONFIG_DIR` 指向 private Jupyter directory。
4. 继承的 `IPYTHONDIR` 与 `JUPYTER_CONFIG_DIR` 在构造 child environment 时被
   移除，再写入受控值。

该规则排除用户 home 中 IPython/Jupyter profile、startup 和 history 对 session
的隐式影响。系统级与虚拟环境级 Jupyter 配置属于已选择 Python 运行时的一部分；
本文规定的 kernel command trait 覆盖本文关心的核心行为。

### 6.4 Child environment

child environment 从 server process environment 的副本构造，以保留项目确实需要
的代理、证书、凭据、包工具路径和业务变量。它必须移除下列变量：

```text
CLICOLOR
CLICOLOR_FORCE
FORCE_COLOR
```

它必须设置下列变量：

```text
NO_COLOR=1
PY_COLORS=0
PAGER=cat
GIT_PAGER=cat
SYSTEMD_PAGER=cat
```

这些环境变量是减少上游 terminal formatting 与 pager 行为的手段。它们不替代
第 7 节的文本规范化契约。

## 7. Terminal Text 公共契约

### 7.1 Text transcript

`loommux` 的公开日志是 append-only text transcript，不是 terminal screen。
文本日志不模拟光标、颜色、终端标题、超链接元数据或分页器状态。agent 从日志中
读取 Python 可见文本，不从日志重建人类终端画面。

下列 IOPub 文本来源属于同一个 terminal text 规约：

| IOPub 来源 | 目标 stream |
| --- | --- |
| `stream` 的 stdout 文本 | `stdout` 与 `combined` |
| `stream` 的 stderr 文本 | `stderr` 与 `combined` |
| `execute_result` 的 `text/plain` | `result` 与 `combined` |
| `display_data` 的 `text/plain` | `result` 与 `combined` |
| `error` 的 traceback 行 | `traceback` 与 `combined` |

每一段文本在写入 `Execution`、`ExecutionLogs`、MCP response 或 monitor event
之前都必须通过同一个 terminal text normalizer。任何旁路都违反本契约。

### 7.2 Normalization invariant

公开文本不得包含 ANSI terminal formatting 或使调用方解释终端格式的 escape
sequence。业务 Unicode 文本、普通换行和 IOPub event 的到达顺序必须保留。

normalizer 必须能处理跨 IOPub chunk 分割的 terminal formatting。对同一个
execution 的 `stdout` 与 `stderr`，normalizer 分别维护足以识别跨 chunk sequence
的状态。一个 sequence 在前一段文本开始、后一段文本结束时，不得将残片泄漏到
任何公开 stream。

`combined` 追加每个已规范化 IOPub event 的文本，顺序严格等于事件到达顺序。
`stdout`、`stderr`、`result`、`traceback` 是同一已规范化文本的流投影。monitor
只接收已规范化文本，不得重新发布 raw terminal formatting。

环境变量降低产生格式的概率；normalizer 保证公开文本满足不变量。二者缺一不可：
前者减少上游噪声，后者定义输出正确性。

### 7.3 Result label

`result` stream 的 `text/plain` 保持其文本内容。向 `combined` 追加该内容时，
`loommux` 使用稳定的 `Out[<execution>]:` 前缀。前缀中的整数是 `loommux`
execution，不是 IPython kernel-local execution count。

### 7.4 图像展示投影

`execute_result` 与 `display_data` 可以携带可交付图像。图像不属于 terminal
text transcript，不写入文本行日志，也不以 Base64 文本替代。它们按 IOPub 到达
顺序形成 MCP image content，并保留该 display event 的 metadata。

图像 MIME、展示 metadata、图像资源限制、终态交付与错误表面由
`ipython-mcp-multimodal-output-design.md` 定义。本文的文本投影规则继续适用于
同一 event 的 `text/plain`。

## 8. 数据流与状态所有权

下图描述对象之间的方向关系。

```text
MCP host cwd + optional explicit resolver
        |
        v
workspace resolver -----> workspace + workspace_resolution
        |                              |
        |                              v
        +----------------------> adapter startup
                                       |
                                       v
coding-agent kernel bootstrap -> KernelLaunch -> KernelSession -> IPython kernel
                                                              |
run_python ---------------------------------------------------+
                                                              |
                                                              v
                                             IOPub text and display events
                                                              |
                                                              v
                                  terminal text normalizer + display capture
                                                              |
                                                              v
                          Execution + ExecutionLogs + ordered display events
                                                              |
                                                              v
                            eight MCP tools and their text/image/status surfaces
```

| 状态 | 拥有者 | 生命周期 | 可观察方式 |
| --- | --- | --- | --- |
| workspace 与 workspace resolution | server process | server process 生命周期 | `python_status` |
| kernel namespace | kernel session | start 至 shutdown 或 reset | `run_python` 的后续 cell |
| private runtime root | `KernelSession` | 一个 kernel session | 不作为常规 MCP 输出公开 |
| execution records 与 output logs | adapter | server process 生命周期 | execution status、read、search、wait |
| ordered display events | execution record | server process 生命周期 | `run_python` 与 `wait_python` 的 MCP content |
| terminal normalizer state | 一个 execution 的 stream 投影 | 该 execution 的 IOPub 处理期间 | 只通过规范化后的日志观察 |

任何状态不得跨越其拥有者的生命周期伪装为另一种状态。特别是 IPython history
不是 execution record，private runtime root 不是 workspace，kernel-local count
不是 public execution coordinate。

## 9. 对外不变量

下列陈述同时成立时，coding agent control plane 才成立。

1. 八个 tools 保持第 2.2 节定义的职责，且不增加 profile selector。
2. `loommux` 正整数 `execution` 是唯一公开执行坐标。
3. 未授权 resolver 时，workspace 等于 launch cwd，workspace 树中的 Python 不会
   自动执行。
4. 授权 resolver 的失败阻止 tool 暴露，不得产生错误 workspace。
5. kernel 的 IPython history、output cache、用户 home profile 与
   `PYTHONSTARTUP` 不成为 session 的隐式输入或持久输出。
6. start 与 reset 的 kernel 都满足第 6 节的同一 policy。
7. 所有公开文本投影满足第 7 节 terminal text invariant。
8. `python_status` 能观察 workspace 及其来源类别，但不泄露 private runtime
   root 或 resolver 内容。
9. 可交付图像按展示顺序进入 MCP content；同一 event 的 `text/plain` 继续满足
   第 7 节的文本不变量。

## 10. 黑盒验收规约

本节定义完成声明所需的外部证据。每个场景使用真实 MCP server、真实 kernel 或
public tool surface。私有函数的单元测试可以补充边界覆盖，但不能取代这些场景。

### 10.1 八工具闭环

**初始条件：** 使用一个空 workspace 启动 server。

**动作：** 依次提交小输出 cell、超过默认等待时长的 cell、包含 stdout/stderr/
最后表达式的 cell、抛出异常的 cell；对运行中的 execution 使用 `wait_python`、
`python_execution_status`、`read_python_output`、`search_python_output`、
`interrupt_python` 与 `reset_python`。

**验收：** 每一个 accepted cell 都有连续正整数 execution；运行状态和终态可由
对应 tool 观察；日志可按 stream 读取和搜索；interrupt 只控制当前运行 execution；
reset 后旧记录仍可读取且新 execution 连续。配置改造不得改变这些观察结果的
职责边界。

### 10.2 Default workspace 与显式 resolver

**初始条件：** 创建一个 launch cwd、其嵌套目录和祖先目录中的同名
`loommux_workspace.py`。该同名文件在执行时会创建一个可观察标记文件。

**动作：** 不设置 `LOOMMUX_WORKSPACE_CONFIG` 启动 server，并调用
`python_status` 与 `run_python("import os; print(os.getcwd())")`。

**验收：** `workspace`、kernel cwd 都等于 launch cwd；`workspace_resolution`
等于 `launch_cwd`；标记文件不存在。

**动作：** 设置 `LOOMMUX_WORKSPACE_CONFIG` 为一个绝对路径 resolver。该 resolver
返回指定子目录；随后启动 server 并重复状态与 cwd 观察。

**验收：** `workspace`、kernel cwd 都等于 resolver 返回目录；
`workspace_resolution` 等于 `explicit_config`；未设置该变量的启动不会执行此
resolver。

### 10.3 Resolver 失败

**动作：** 分别提供相对 resolver 路径、缺失路径、目录路径、加载时抛出异常的
文件、返回整数的文件、返回缺失路径的文件和返回普通文件的文件。

**验收：** 每种输入在 tools 暴露前失败，且得到第 5.4 节相应的状态分类；任何
场景都不回退到 launch cwd 或启动一个部分配置的 kernel。

### 10.4 Codex resolver example

**初始条件：** 创建嵌套 launch cwd，其中某个祖先目录含 `.codex`，并将官方
Codex example 作为显式 resolver。

**动作：** 启动 server 并观察 workspace。

**验收：** workspace 是最近 `.codex` 目录的父目录。删除所有 `.codex` 后，
workspace 是 launch cwd。该行为只在显式选择 example 时发生。

### 10.5 Private IPython/Jupyter state

**初始条件：** 在测试用户 profile、Jupyter config 和 `PYTHONSTARTUP` 文件中放入
会创建标记文件的启动行为；预先记录用户 history database 的内容与修改时间。

**动作：** 在该环境中启动 server，提交多个普通 Python cell，随后 reset 并再次
提交 cell。

**验收：** 所有标记文件均不存在；用户 history database 不被创建或修改；kernel
内部可观察的 IPython directory 与 `JUPYTER_CONFIG_DIR` 指向 session private
runtime root；`HistoryManager.enabled` 为 false；displayhook cache size 为零；
reset 前后的 private runtime root 不相同，旧 root 已清理。

### 10.6 Terminal text

**初始条件：** 在真实 kernel 中构造含 ANSI formatting 的 stdout、stderr、
`text/plain` result 与 traceback。构造同一 formatting sequence 跨两个 write
输出的情况。

**动作：** 使用 `run_python`、`read_python_output` 和 `search_python_output` 读取
五条 stream，并读取 monitor 收到的 execution output event。

**验收：** `stdout`、`stderr`、`result`、`traceback`、`combined`、小输出响应和
monitor event 都不含 ANSI escape sequence；业务文本仍存在并可搜索；combined 中
事件的相对顺序与 IOPub 到达顺序一致；跨 write sequence 没有控制残片出现在任何
公开文本中。

### 10.7 图像展示输出

图像展示的真实 kernel、MCP client、metadata、资源失败与顺序验收由
`ipython-mcp-multimodal-output-design.md` 第 9 节定义。该验收不得以仅检查
`text/plain` repr 的单元测试替代。

## 11. 实现边界与文档关系

下列源文件承担对应职责：

| 文件 | 职责 |
| --- | --- |
| `src/loommux/workspace_resolver.py` | launch cwd、显式 resolver 和 workspace resolution。 |
| `src/loommux/host_workspace_config.py` | 读取、验证和加载 `LOOMMUX_WORKSPACE_CONFIG`。 |
| `src/loommux/coding_agent_kernel.py` | `KernelLaunch`、private runtime root、kernel command 与 child environment。 |
| `src/loommux/terminal_text.py` | terminal text normalizer 及其跨 chunk 状态。 |
| `src/loommux/kernel_session.py` | 启动 `KernelLaunch`、接收 IOPub、规范化文本并采集展示 data bundle。 |
| `src/loommux/adapter.py` | execution 生命周期、文本与图像 MCP 投影、公开状态与 bootstrap 的集成。 |
| `src/loommux/execution.py` | 已规范化文本、logs 与有序展示事件。 |
| `src/loommux/mcp_result_policy.py` | text/image MCP content 的 ToolResult 构造。 |
| `tests/` | 本文第 10 节的真实 kernel 与 MCP 验收。 |

旧的自动上溯 workspace 配置入口不属于该结构。generic 与 Codex resolver 作为
examples 保留，不作为 package runtime 输入。

README、workspace 配置文档、tool docstring 与输出设计文档必须引用本文定义的
workspace surface、kernel policy 和 terminal text invariant。它们可以面向不同
读者压缩说明，但不得改变 canonical surface、责任边界或验收语义。

`ipython-mcp-multimodal-output-design.md` 是图像展示输出的事实源。它不得改变
本文定义的八工具职责、execution 坐标、文本日志、terminal text invariant 或
kernel 生命周期。
