"""Does giving up a ``wait`` tool call let go of what it held on the server?

In-process FastMCP client against ``create_mcp("content")`` at the working
tree's HEAD. A cell runs for 30 s; three ``wait(20)`` calls are each abandoned
after 1 s, as an agent or its user pressing Esc would. Then count the server
threads whose stack is inside ``IPythonSession``'s wait loop (``_await``),
check a bystander call, and end the cell.
"""

import os
import sys
import tempfile
import threading
import time
import traceback

import anyio
from fastmcp import Client

from loommux.mcp.factory import create_mcp

ABANDONED = 3


def threads_in_await() -> int:
    frames = sys._current_frames()
    return sum(
        1
        for thread in threading.enumerate()
        if thread.ident in frames and any(frame.name == "_await" for frame in traceback.extract_stack(frames[thread.ident]))
    )


async def main() -> None:
    async with Client(create_mcp("content")) as client:
        await client.call_tool("run_cell", {"freeform": "%%loommux --wait 1 --silence 1\nimport time\nfor _ in range(300):\n    time.sleep(0.1)"})
        print("before:", threads_in_await(), "threads in _await")
        for _ in range(ABANDONED):
            with anyio.move_on_after(1.0):
                await client.call_tool("wait", {"timeout_seconds": 20, "silence_seconds": 20})
        await anyio.sleep(0.5)
        print(f"after abandoning {ABANDONED} waits:", threads_in_await(), "threads in _await")
        started = time.monotonic()
        await client.call_tool("status", {})
        print(f"a status call beside them: {time.monotonic() - started:.3f} s")
        await client.call_tool("interrupt", {})
        await anyio.sleep(1.0)
        print("after the cell ends:", threads_in_await(), "threads in _await")


with tempfile.TemporaryDirectory() as workspace:
    os.chdir(workspace)
    anyio.run(main)
