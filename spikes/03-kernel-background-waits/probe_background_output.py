"""After a cell ends, do a task and a thread it started still reach iopub, and as whose output?

Three cells. A starts an asyncio task, a thread and a comm, and ends; both
speak a second or so later. B reads the task's result. C starts a task that
is due to speak while the next cell, D, runs a blocking sleep.
"""

from harness import kernel, run, show

CELL_A = """
import asyncio, threading, time
from comm import create_comm
from IPython.display import display
print("loop running in the cell:", asyncio.get_running_loop() is not None)
channel = create_comm(target_name="loommux.watch", data={"opened": True})
async def background():
    await asyncio.sleep(1.0)
    print("task prints after its cell", flush=True)
    display({"application/x-probe": "task display"}, raw=True)
    channel.send({"done": "task"})
    return 42
task = asyncio.get_running_loop().create_task(background())
def worker():
    time.sleep(1.5)
    print("thread prints after its cell", flush=True)
    channel.send({"done": "thread"})
threading.Thread(target=worker, daemon=True).start()
print("cell A ends")
"""

CELL_C = """
import time
async def speaks_later():
    await asyncio.sleep(0.5)
    print(f"C's task prints at {time.monotonic():.2f}", flush=True)
task_c = asyncio.get_running_loop().create_task(speaks_later())
"""

with kernel() as client:
    known: dict[str, str] = {}
    a, seen = run(client, CELL_A, settle=3.0)
    known[a] = "cell A"
    show("cell A, and 3 s after its idle", a, seen, known)

    b, seen = run(client, "print(task.done(), task.result() if task.done() else None)")
    known[b] = "cell B"
    show("cell B", b, seen, known)

    c, seen_c = run(client, CELL_C)
    known[c] = "cell C"
    d, seen_d = run(client, "import time; print(f'cell D starts at {time.monotonic():.2f}'); time.sleep(1.5); print(f'cell D ends at {time.monotonic():.2f}')", settle=1.0)
    known[d] = "cell D"
    show("cell C", c, seen_c, known)
    show("cell D (C's task is due 0.5 s into it)", d, seen_d, known)
