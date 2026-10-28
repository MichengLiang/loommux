# 004 kernel 忙时拒收的单元格被呈现成正在跑的那一格的记录：提交的一方看不出这一格没有执行

:发现: 2026-10-28，小汤，hora 计划 #24 收尾时复现（小宁 hora 24.7 背对背推导时初见）
:代码版本: ``bf1be94``
:来自: hora
:影响: 主 kernel 正忙（包括正替子 kernel 转发一格）时交的单元格被拒收，工具结果却是正在跑的那一格的标题行加空白：Agent 以为执行过，实际没有执行
:涉及: ``src/loommux/session.py:141``、``src/loommux/mcp/presentation.py:32-58``、``src/loommux/mcp/factory.py:273-274``

## 现象与复现

实测（2026-10-28，hora 计划 #24 收尾时；主 kernel ``hora分身与伙伴工作流调优``）：

1. 主 kernel 里交一格转发到子 kernel、并提前交回控制权的单元格::

      %%loommux --kernel 探C8 --wait 3 --silence 3
      import time
      time.sleep(12)
      print("A 在子 kernel 里跑完了")

   返回 ``In [45] · 3.0 s: Running, silent for 3 s: …``。

2. 它还在跑时，交一格普通的单元格::

      print("B 在主 kernel 里跑了", get_ipython().execution_count)
      探C8标记 = "B 执行过"

   返回的全文只有 ``In [45]:``——第 45 格（A）的编号，空白正文，没有任何一个字说这一格被拒收。

3. A 跑完以后看：``"探C8标记" in globals()`` 为 ``False``，``input_hist_raw`` 里没有 B 那一格。B 从没执行过。

同一现象此前在小宁做 hora 24.7 背对背推导时测到过一次（那一格返回了正在跑的那一格的记录、没有执行）。

## 原因（读代码，没有单独打桩验证）

* ``src/loommux/session.py:141``：kernel 正忙时拒收，回 ``{"ok": False, "status": "busy", "execution": <正在跑的那一格>, "message": "kernel is already executing code"}``。
  拒收本身是设计（``daemon.py`` 的说明：cells are never queued）。
* ``src/loommux/mcp/presentation.py:32``：``_is_tool_failure`` 对 ``run_cell``，凡 ``ok`` 为假又带整数 ``execution`` 的，都当作执行状态、
  不当作失败——本意是让 Python 报错与 killed 的记录照常呈现。busy 拒收也带着整数 ``execution``，于是落进 ``_execution_surface``；
  那里没有 ``busy`` 这一支，走到最后的 ``_with_execution_input(status, output)``，正文为空，只剩正在跑的那一格的标题行。

## 影响到的用法

* 提交的一方（Agent）读到的是一次“执行过、没有输出”的结果，会照着它往下走：以为变量已经赋上、文件已经写了。
* ``--kernel`` 转发的那一格本身是主 kernel 里的一次执行，跑多久占多久。内置 Agent 与分身和主会话共用一条连接、各开子 kernel
  干活时，主会话这期间交的格子都会这样无声地丢掉。
* ``run_cell`` 的工具说明（``src/loommux/mcp/factory.py:273-274``）写着“同时想利用Kernel本身……去跑长任务的同时，还想要使用
  主Kernel去做别的事情使用”，与实际行为相反：转发那一格没跑完，主 kernel 收不进别的格子。

## 期望

拒收说出来：这一格没有执行、kernel 正忙、正在跑的是哪一格、怎样接着等它。怎么改（呈现层认出 ``busy``，还是拒收不带
``execution``，还是转发不再占着主 kernel）由 loommux 定；工具说明随行为写准。
