# 001 宿主已把 loommux 放进作业对象时，Windows 上起不来 kernel

:发现: 2026-10-23，小汤，经 loommux_ipython 远程连接办公室电脑时
:代码版本: ``cad890d``
:影响: Windows 原生 loommux 由某些宿主启动时完全不可用：每个连接都起不来 kernel
:涉及: ``src/loommux/kernel/runtime.py:61``、``src/loommux/kernel/runtime.py:69``、``src/loommux/kernel/runtime.py:81``

# 现象与复现

办公室电脑上，Windows 原生运行的 loommux（MCP 地址 ``http://172.16.32.33:9901/mcp``）
每次新建连接都失败，连续四次结果相同::

    resource_unavailable: kernel failed to start: (5, 'AssignProcessToJobObject', '拒绝访问。')

网络是通的：看板 ``/`` 与 ``/api/resources`` 都能读到，``/mcp`` 握手成功；资源列表始终为空，
说明失败发生在新建 kernel 的那一步。同一台电脑 WSL 里的 loommux 不经过这段代码，正常起 kernel、
正常执行单元格。小亮在办公室本机用 FastMCP 客户端复测时，起初也卡住超时，之后又成功了，
是否复现与 loommux 当时由什么方式启动有关（见原因）。

# 原因

``_WindowsKernelContainment.attach`` 每起一个 kernel 就新建一个作业对象，设成
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``，再把 kernel 进程放进去。loommux 自己已经被宿主
放进一个不允许嵌套的作业时（例如经 SSH 会话、任务计划程序、某些启动器或 IDE 终端启动），
``AssignProcessToJobObject`` 返回错误 5；这里没有退路，异常直接上抛，kernel 起不来。

同一个类的 ``close()`` 已经写明了这种宿主（"A host may already place loommux in a Job Object
without nested membership"），并为此先用 ``taskkill /T /F`` 收进程树——收尾考虑到了，起的时候没有。

# 修复要守住的

- 外层作业不允许嵌套时，kernel 照常启动；进程树的回收退回 ``close()`` 里已有的 ``taskkill`` 路径。
- 能放进作业时，行为不变。
- 退回时留一条可见的诊断，让人知道这次没有作业对象兜底。

# 验收

在一个禁止嵌套的作业里启动 loommux（或在测试里让 ``AssignProcessToJobObject`` 抛出错误 5），
新建连接能起 kernel、执行单元格；``close()`` 之后 kernel 及其子进程都已结束。
