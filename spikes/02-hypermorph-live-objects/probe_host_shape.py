"""Probe 3: Hypermorph inside a loommux-shaped host, and the resource directory as state.

``python probe_host_shape.py '{"host": H, "mcp": bool, "drain": bool}'``

The application is Starlette with loommux's own routes first and Hypermorph's
inspected app mounted at ``/`` behind them; the Runtime belongs to the
application's lifespan. With ``mcp`` a real FastMCP app is mounted too, and a
real MCP client keeps its session (and the GET stream Claude Code holds) open.
A browser-like client watches the Daemon over ``/objects/watch`` while a
resource is opened and recycled.

Shutdown is triggered with both streams still open. Hosts:

* ``uvicorn-naive``: the Runtime closes in the lifespan's exit, after uvicorn
  has started waiting for open connections;
* ``uvicorn-ordered``: a ``Server.shutdown`` override closes the Runtime first;
* ``runtime-server``: Hypermorph's RuntimeServer (Hypercorn), whose shutdown
  trigger closes the Runtime before the network drains. ``drain`` gives it a
  ``wind_down`` that tells sse-starlette's streams (the MCP GET stream) to end,
  which outside uvicorn nothing else does.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import sys
import time
from contextlib import asynccontextmanager

import anyio
import httpx
import uvicorn
from domain import CountedExecution, Daemon
from hypermorph import Runtime
from hypermorph.http.server import RuntimeServer
from hypermorph.workbench.service import create_inspected_app
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route


class OrderedServer(uvicorn.Server):
    """Ends the Runtime's observations before uvicorn waits for connections to finish."""

    def __init__(self, config: uvicorn.Config, runtime: Runtime) -> None:
        super().__init__(config)
        self._runtime = runtime

    async def shutdown(self, sockets=None) -> None:
        await self._runtime.aclose()
        await super().shutdown(sockets)


def stage(name: str) -> None:
    # A hang leaves no report; the last stage printed says where it stopped.
    print(f"stage {time.perf_counter():.3f} {name}", flush=True)


async def sse(response: httpx.Response, sink) -> None:
    event, data = None, []
    try:
        async for line in response.aiter_lines():
            if line.startswith(":"):
                continue
            if line == "":
                if event or data:
                    await sink.send((time.perf_counter(), event, json.loads("\n".join(data)) if data else None))
                event, data = None, []
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
    except httpx.HTTPError as error:
        # A connection cut by a forced exit is an outcome to report, not a probe failure.
        await sink.send((time.perf_counter(), f"<{type(error).__name__}>", None))
        return
    await sink.send((time.perf_counter(), "<eof>", None))


async def next_event(receive, want: str, timeline: list, *, limit: float = 5):
    with anyio.fail_after(limit):
        while True:
            at, event, data = await receive.receive()
            timeline.append((event, json.dumps(data, ensure_ascii=False)[:160] if data is not None else None))
            if event == want or event.startswith("<"):
                return data


async def hold_mcp_session(url: str, outcome: dict, ready: anyio.Event) -> None:
    from fastmcp import Client

    try:
        async with Client(url) as client:
            outcome["tool"] = (await client.call_tool("hello")).data
            ready.set()
            await anyio.sleep_forever()
    except Exception as error:  # the server going away ends the session; record how
        outcome["ended_with"] = type(error).__name__
    finally:
        ready.set()


async def end_mcp_streams() -> None:
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit = True


async def main(host: str, mcp: bool, drain: bool) -> None:
    runtime = Runtime()
    opened_in_transaction: list[str] = []

    def open_resource(name: str) -> None:
        runtime.open(name, CountedExecution())
        opened_in_transaction.append(name)

    daemon = Daemon(open_resource)
    runtime.open("daemon", daemon)

    routes = [Route("/api/ping", lambda _: PlainTextResponse("pong"))]
    mcp_app = None
    if mcp:
        from fastmcp import FastMCP

        server_mcp = FastMCP("probe")

        @server_mcp.tool
        def hello() -> str:
            return "hi"

        mcp_app = server_mcp.http_app(path="/mcp")
        routes.append(Mount("/m", app=mcp_app))
    routes.append(Mount("/", app=create_inspected_app(runtime)))

    @asynccontextmanager
    async def lifespan(app):
        async with runtime:
            if mcp_app is None:
                yield
            else:
                async with mcp_app.lifespan(app):
                    yield

    app = Starlette(routes=routes, lifespan=lifespan)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    if host.startswith("runtime-server"):
        server = RuntimeServer(app, runtime, port=port, runtime_owner="application", wind_down=end_mcp_streams if drain else None)
        is_started = lambda: server.application_started  # noqa: E731
        # runtime-server-signal stops the way Ctrl+C does: SIGINT to the process.
        by_signal = host == "runtime-server-signal"
        stop = (lambda: os.kill(os.getpid(), signal.SIGINT)) if by_signal else server.request_shutdown
    else:
        config = uvicorn.Config(app, log_level="warning", lifespan="on")
        server = OrderedServer(config, runtime) if host == "uvicorn-ordered" else uvicorn.Server(config)
        is_started = lambda: server.started  # noqa: E731

        def stop() -> None:
            server.should_exit = True

    report: dict = {"host": host, "mcp": mcp, "drain": drain}
    timeline: list = []
    mcp_outcome: dict = {}

    async with anyio.create_task_group() as tasks:
        serving = anyio.Event()

        async def serve() -> None:
            try:
                if host == "runtime-server-signal":
                    await server.serve([listener], signals=True)
                else:
                    await server.serve([listener])
            except BaseException as error:
                report["serve_raised"] = repr(error)[:300]
                raise
            finally:
                serving.set()

        tasks.start_soon(serve)
        stage("serving")
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=5) as http:
            with anyio.fail_after(10):
                while not is_started():
                    await anyio.sleep(0.01)
                # The listener accepts before the application answers; ask for a real response.
                while True:
                    try:
                        if (await http.get("/api/ping")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await anyio.sleep(0.02)
            stage("ready")
            page = await http.get("/")
            assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.text)
            report["routes"] = {
                "/": page.status_code,
                "assets": [(await http.get(path)).status_code for path in assets],
                "/api/ping": (await http.get("/api/ping")).text,
                "/objects": [item["object_id"] if "object_id" in item else sorted(item)[:3] for item in (await http.get("/objects")).json()],
            }
            if mcp:
                mcp_ready = anyio.Event()
                tasks.start_soon(hold_mcp_session, f"{base}/m/mcp", mcp_outcome, mcp_ready)
                with anyio.fail_after(10):
                    await mcp_ready.wait()
                await anyio.sleep(0.3)  # let the client open its GET stream
                stage(f"mcp session {mcp_outcome}")

            send, receive = anyio.create_memory_object_stream(100)
            stream_cm = http.stream("GET", "/objects/watch", timeout=None)
            stream = await stream_cm.__aenter__()
            tasks.start_soon(sse, stream, send)
            watch = (await next_event(receive, "opened", timeline))["watch"]

            async def declare(*objects: str) -> int:
                return (await http.put(f"/objects/watch/{watch}", json={"objects": list(objects), "paused": []})).status_code

            report["declare_daemon"] = await declare("daemon")
            await next_event(receive, "snapshot", timeline)

            opened = await http.post("/commands", json={"object_id": "daemon", "name": "open", "args": ["k1"], "kwargs": {}})
            report["open_in_transaction"] = {"status": opened.status_code, "body": opened.text[:300], "opened": opened_in_transaction}
            if opened.status_code == 200:
                await next_event(receive, "update", timeline)
                report["declare_daemon_k1"] = await declare("daemon", "k1")
                await next_event(receive, "snapshot", timeline)
                # Recycling cannot happen inside a transaction: close the object, then forget it.
                await runtime.close_object("k1")
                await runtime.submit(daemon._forget, "k1")
                await next_event(receive, "update", timeline, limit=2)
            report["timeline"] = timeline[:]
            timeline.clear()

            stage("stopping")
            started = time.perf_counter()
            stop()
            with anyio.move_on_after(8) as waited:
                await serving.wait()
            stage(f"stopped hung={waited.cancelled_caught}")
            report["shutdown_seconds"] = None if waited.cancelled_caught else round(time.perf_counter() - started, 3)
            report["hung"] = waited.cancelled_caught
            if waited.cancelled_caught and not host.startswith("runtime-server"):
                server.force_exit = True
                with anyio.move_on_after(5):
                    await serving.wait()
                report["after_force_exit"] = serving.is_set()
            with anyio.move_on_after(1):
                while True:
                    at, event, data = await receive.receive()
                    timeline.append((event, json.dumps(data, ensure_ascii=False)[:80] if data is not None else None))
                    if event.startswith("<"):
                        break
            report["stream_end"] = timeline
            report["mcp_session"] = mcp_outcome
            await stream_cm.__aexit__(None, None, None)
        tasks.cancel_scope.cancel()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    case = json.loads(sys.argv[1])
    anyio.run(main, case["host"], case.get("mcp", False), case.get("drain", False))
