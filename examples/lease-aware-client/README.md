# Lease-aware FastMCP Client 示例

这个例子专门展示：当 `loommux` 服务器的新客户端租约策略是
`heartbeat` 时，FastMCP 客户端需要怎样配合，才能让一个暂时没有业务调用的
IPython 工作台继续存活。

这里的心跳不是 MCP 业务工具，也不会出现在 `tools/list` 中。客户端使用
FastMCP 提供的标准 `Client.ping()`，服务器在成功处理标准 MCP `ping` 后，
将它解释为对应客户端租约的续期信号。

## 这个例子回答什么问题

普通的 FastMCP Client 能够连接 loommux，也能够手动调用：

```python
await client.ping()
```

但是，普通 `Client` 不会自动根据 loommux 的租约策略创建后台心跳任务。
因此长期连接的客户端必须自己负责：

1. 读取服务器当前的 `/api/lease-policy`；
2. 在建立 MCP Session 前固定策略代；
3. 把资源名、操作者和策略代放进每次 MCP 请求使用的请求头；
4. 在 `heartbeat` 模式下按照服务器公布的间隔调用标准 `ping`；
5. 离开客户端生命周期时取消后台心跳，再关闭 MCP Session。

本目录包含两种写法：

- `demo.py`：推荐写法，直接使用项目内的 `LeaseAwareClient`；
- `manual_fastmcp_client.py`：教学写法，只使用 FastMCP `Client`，手动补齐
  loommux 所需的策略发现、请求头和 ping 循环。

`LeaseAwareClient` 保留普通 FastMCP 客户端的工具发现与完整工具调用参数，
因此也可以交给通过 `list_tools()` 动态注册工具的宿主。租约能力只装饰连接
生命周期，不要求宿主读取其内部 Client 或实现 Loommux 专用调用分支。

## 启动服务器

请在项目根目录 `/home/t103o/workbench/projects/loommux` 中执行：

```bash
LOOMMUX_LEASE_MODE=heartbeat \
LOOMMUX_HEARTBEAT_INTERVAL_SECONDS=2 \
LOOMMUX_HEARTBEAT_TIMEOUT_SECONDS=7 \
LOOMMUX_SWEEP_INTERVAL_SECONDS=0.5 \
LOOMMUX_ORPHAN_GRACE_SECONDS=2 \
uv run python -m loommux.mcp.server \
  --server \
  --host 127.0.0.1 \
  --port 8801 \
  --path /mcp
```

心跳超时必须严格大于心跳间隔。这里使用较短的时间，只是为了方便观察；
实际业务可以使用更宽松的时间窗口。

## 运行推荐客户端

另开一个终端，在同一个项目根目录执行：

```bash
uv run python examples/lease-aware-client/demo.py
```

也可以调整参数：

```bash
LOOMMUX_SERVER_URL=http://127.0.0.1:8801/mcp \
LOOMMUX_RESOURCE_NAME=shared-heartbeat-demo \
LOOMMUX_OBSERVE_SECONDS=8 \
uv run python examples/lease-aware-client/demo.py
```

演示流程是：

1. `LeaseAwareClient` 请求当前租约策略；
2. 连接 Streamable HTTP MCP endpoint；
3. 执行一次 `run_cell`，创建并初始化一个共享 IPython 工作台；
4. 在没有业务工具调用的时间段里，后台发送标准 MCP `ping`；
5. 再次执行 `run_cell`，确认之前的 Python 变量仍然存在；
6. 客户端退出并停止心跳；
7. 等待服务器扫描器发现租约过期并回收该资源。

如果第二次调用仍然输出第一次写入的变量值，说明心跳期间资源没有被
回收。如果客户端退出后资源在控制台中消失，说明停止心跳后的回收链路也在
工作。

## 运行教学客户端

```bash
LOOMMUX_OBSERVE_SECONDS=8 \
uv run python examples/lease-aware-client/manual_fastmcp_client.py
```

教学客户端刻意不使用 `loommux.client.LeaseAwareClient`。它展示了普通
FastMCP `Client` 要怎样手动配合：

```python
policy = await fetch_policy()
headers = {
    "X-Loommux-Operator": "...",
    "X-Loommux-Resource": "...",
    "X-Loommux-Lease-Policy-Generation": str(policy.generation),
}
transport = StreamableHttpTransport(server_url, headers=headers)
async with Client(transport) as client:
    await client.call_tool(...)
    await client.ping()
```

这段代码的关键不是请求头的名字本身，而是请求头必须随着同一个
Streamable HTTP transport 发送。尤其是命名共享资源时，第一次业务调用和
后续 `ping` 必须解析到同一个资源地址。

## activity 模式对比

如果服务器采用：

```bash
LOOMMUX_LEASE_MODE=activity
```

业务工具调用进入和完成时会刷新租约。客户端不需要为了保持资源而单独
发送后台心跳；但客户端退出后，资源仍会在活动租约超时并经过扫描周期后被
回收。

本目录的客户端仍然会读取策略。这样客户端不会把服务器策略写死，也能
正确处理服务器运行时切换策略的情况。

## 策略代为什么要固定

客户端读取策略和第一次业务调用之间可能发生服务器策略切换：

```text
客户端读取第 G1 代策略
服务器切换到第 G2 代策略
客户端第一次调用业务工具
```

客户端将 `G1` 放到：

```text
X-Loommux-Lease-Policy-Generation: G1
```

服务器因此可以按照客户端已经发现的那一代策略创建租约，而不是让一次
切换改变客户端尚未感知的行为。这是 loommux 的租约控制面约定，不是
FastMCP 标准客户端自动提供的功能。

## 生命周期注意事项

- `Client.ping()` 只证明 MCP Session 仍能通信；它不会凭空创建 worker
  或客户端租约。
- 必须先有一次业务调用，服务器才会创建资源租约。
- 客户端退出时必须停止心跳，否则后台任务可能继续占用客户端状态。
- `heartbeat_timeout_seconds` 应明显大于 `heartbeat_interval_seconds`，为
  短暂网络抖动留出空间。
- `X-Loommux-Resource` 是资源寻址头，不是认证凭据。
- loommux 能够执行任意 Python；HTTP 服务应保持在受信任边界内。

## 文件

```text
README.md                  本说明
demo.py                    使用 LeaseAwareClient 的推荐示例
manual_fastmcp_client.py   只使用 FastMCP Client 的手动配合示例
```
