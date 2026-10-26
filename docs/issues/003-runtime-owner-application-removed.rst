# 003 Hypermorph 删掉了 RuntimeServer(runtime_owner="application")：重建时宿主用默认所有权，Daemon 作宿主持有的应用

:发现: 2026-10-26，小汤，Hypermorph HO 一组删掉 application 所有权时
:代码版本: ``3134b3e``
:性质: 转告
:来自: Hypermorph
:影响: loommux 重建的 HTTP 宿主：照 spike 02 §4.5 写 runtime_owner="application" 会在构造时报 ValueError；rebuild-brief 指向的形状已不存在

## 发生了什么

Hypermorph#021 删掉了 ``RuntimeServer`` 的 ``runtime_owner="application"``\ ，只剩 ``"server"``\ （默认：宿主进入并收束 Runtime）与
``"external"``\ （Runtime 借来、由外部宿主进入与结束）。提交：``git log --grep "Work-Package: 24.1" -- playground/Hypermorph``\ 。

loommux 的重建要照着走的 spike 02 定稿的正是被删的形状：``spikes/02-hypermorph-live-objects/REPORT.md`` §4.5 第 218 行
“The host is ``RuntimeServer``. ``runtime_owner="application"``”，``docs/rebuild-brief.md`` 第 60、94 行指向它。照抄会在构造时报
``ValueError: runtime_owner must be 'server' or 'external'``。spike 是定稿的实验记录，不必改；这份转告给重建时用。

原因：lifespan 的退出排在网络排空之后，那时 Runtime 已经结束；宿主持有的应用（Hypermorph 文档 ``hosted-application``\ ）要的
顺序是“Runtime 先进、应用后进；应用先退、Runtime 后退”，应用退出时 Runtime 与观察还开着，最后的提交观察者看得到。

## 重建时的形状（Hypermorph 那边冷审时推断，没有在 loommux 里跑过）

* 宿主仍是 ``RuntimeServer``\ ，用默认所有权；Daemon（打开资源与执行对象、接上来源的那部分）做成宿主持有的应用，交给
  ``RuntimeServer(app, runtime, application=..., wind_down=...)``\ 。参照 Hypermorph 的 ``src/hypermorph/examples/queue_desk/server.py``
  与 ``src/hypermorph/applications.py`` 的 hosted-application 一节。
* ``wind_down`` 只负责让 MCP 的 GET 长流结束（sse-starlette 的 ``AppStatus.should_exit``\ ，spike 02 §4.5 第 171 行实测 0.13 s 退出）；
  “把运行中的执行记为中断”放进应用的退出，那时 Runtime 与观察都还开着。
* 同一节里的 ``create_inspected_app`` 已在 Hypermorph 的 EM 一组删掉（``git log --grep "Work-Package: 20.2" -- playground/Hypermorph``\ ），“协议加检查器”现在是
  ``create_app(runtime, inspector=True)``\ 。

## 一个相关的开着的问题

Hypermorph#025：``runtime_owner="external"`` 的服务局部停止时，还连着的观察会让停止挂住。loommux 若打算让 Runtime 比网络
活得久（局部停服务），先看那份记录。
