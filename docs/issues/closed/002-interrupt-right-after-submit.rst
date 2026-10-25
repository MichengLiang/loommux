# 002 提交后立刻 interrupt，中断约两成概率落空

:发现: 2026-10-25，小汤，loommux 计划 #29 收尾时由分身循环复现
:代码版本: ``1a49e21``，写下时项目里有未提交的改动，行号可能与这个提交对不上
:影响: agent 或程序在提交后立刻中断（例如提交错了马上撤），中断可能落空、cell 照跑；相关测试约两成概率失败
:涉及: ``src/loommux/session.py:258``、``src/loommux/kernel/session.py:83``、``tests/mcp/test_kernel_control.py:226``

## 现象与复现

``tests/mcp/test_kernel_control.py::test_a_kernel_opens_runs_and_holds_kernels_that_leave_with_it`` 在
``run_cell("import time; time.sleep(30)", wait_seconds=0)`` 之后立刻 ``interrupt()``：``interrupt`` 返回
``interrupt_sent``，可 10 秒后 ``wait`` 拿到的不是 ``interrupted``。单独循环跑 30 次
（``-p no:randomly -o addopts= -x``），``1a49e21`` 上 6/30 失败，``d717756``（``1a49e21`` 之前）上 7/30 失败，
所以与 #29 的提醒交付无关，是原有的竞争。在全套 ``pytest -n 4`` 里多半能过，以前不显眼。

两种症状：

- 状态 ``running``：信号没落到那次 sleep 上，``sleep(30)`` 照常在跑；
- 状态 ``completed``：30 秒的 sleep 在 10 秒内"完成"，KeyboardInterrupt 落在了别处后被吞掉，
  推测落在 cell 开始执行之前。

测试时机器上还有别的负载（8801 日常服务、其他 Claude Code），kernel 起 cell 越慢，这个窗口可能越大；
没测空闲机器上的数字。

## 原因

没有查到是哪一层：推测是 ``interrupt`` 发出时 kernel 还没开始执行这一格（``execute_request`` 已发、
``execute_input`` 未到），SIGINT 落在 IPython 进入 cell 之前，被事件循环或 ipykernel 自己处理掉。
没有在 kernel 里加日志证实。

## 修复要守住的

- ``run_cell`` 已接受、kernel 尚未开始执行时的 ``interrupt``，结果仍是这一格被中断（或明确报告"还没开始"，
  由调用者重试），不能静默落空。
- 已经在执行的 cell，中断行为不变。

## 验收

上述测试单独循环 100 次不失败；另写一条直接针对"提交后立刻中断"的测试。

## 解决

:提交: 工作包 30.1（git log --grep "Work-Package: 30.1"）

中断交给的是这一格，不是此刻的进程。``KernelSession.interrupt()`` 遇到还没出现 ``execute_input`` 的一格时先挂起，
collector 收到这一格的 ``execute_input`` 时再发 SIGINT：ipykernel 只在 handler 开始时装 ``default_int_handler``
（``kernelapp.py:605`` 平时是 ``SIG_IGN``），``execute_input`` 在 handler 里发出。发过中断、没有 ``execute_reply``、
也没有 error 的一格记为 ``KeyboardInterrupt: interrupted before its code ran``，状态 ``interrupted``——这就是记录里
"30 秒的 sleep 在 10 秒内完成"那种症状：信号落在 handler 里、代码之前，被 ``kernelbase.py:481`` 吞掉且不发 reply。

修复前新进程直接驱动 ``IPythonSession``：6/6 落空；修复后分段 sleep 200 轮 0 次落空。剩下的是 CPython 的推迟：
信号正好落在阻塞调用（单次 ``time.sleep(30)``）进入系统调用之前时，要等调用返回才抛出，仍落在这一格上，只是晚到；
不补发信号，因为转发 cell 接住第一次后还要收尾，见 ``docs/kernel-resource-daemon-design.md`` 第 4 节。

新增测试：``tests/session/test_session.py::test_an_interrupt_right_after_submission_interrupts_that_cell``（旧代码上为红），
``tests/kernel/test_kernel.py`` 三条消息驱动的测试（开始时才发、无 reply 记为中断、没中断的无 reply 仍是完成）；
原测试与 ``test_silence`` 的同类 cell 改为分段 sleep。
