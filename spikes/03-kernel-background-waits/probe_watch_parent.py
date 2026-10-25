"""Can each watch's output carry its own parent header, without catching anyone else's?

A watch is a task the cell registered under a name. In the task, before its
body runs, the parent header is set to one naming the watch. Two ways:

- ``shell``: ``get_ipython().set_parent(...)``, the public call. It also sets
  each publisher's process-wide fallback, ``_parent_header_global``, which a
  thread with no parent of its own uses (``iostream.py``, ``zmqshell.py``,
  ``displayhook.py``: ``_parent_header.get()`` falls back to it).
- ``context``: set only the publishers' ``_parent_header`` ContextVars. An
  asyncio task runs in its own copy of the context, so this reaches the task
  and nothing else.

Cell A starts two watches and, beside them, a plain thread that prints after
both have set their headers; the second watch also sends on a comm the cell
opened. Cell B then starts another thread.
A third argument ``buffered`` drops ``flush=True`` from the watches' prints, so
their text leaves through ipykernel's buffered flush rather than at once.
Usage: probe_watch_parent.py shell|context [buffered]
"""

import sys

from harness import kernel, run, show

SET_PARENT = {
    "shell": "get_ipython().set_parent(header)",
    # Each publisher's ContextVar holds the header itself, as its set_parent
    # stores extract_header(parent); the whole message there breaks publishing.
    "context": (
        "for publisher in (sys.stdout, sys.stderr, ip.display_pub, ip.displayhook, ip):\n"
        "        publisher._parent_header.set(header['header'])"
    ),
}

CELL_A = """
import asyncio, sys, threading, time
import anyio
from comm import create_comm
from IPython.display import display
ip = get_ipython()
channel = create_comm(target_name="loommux.probe")

def watch_header(watch_id):
    return {"header": {"msg_id": watch_id, "msg_type": "execute_request", "session": "", "username": "", "date": "", "version": "5.3"}}

async def watched(watch_id, body):
    header = watch_header(watch_id)
    %s
    return await body()

async def first():
    await anyio.sleep(0.5)
    print("first says", flush=True)
    return "r1"

async def second():
    await anyio.sleep(0.8)
    print("second says", flush=True)
    display({"text/plain": "second displays"}, raw=True)
    channel.send({"from": "second"})
    return "r2"

def bystander():
    time.sleep(1.2)
    print("cell A's thread says", flush=True)

loop = asyncio.get_running_loop()
t1 = loop.create_task(watched("watch-1", first))
t2 = loop.create_task(watched("watch-2", second))
threading.Thread(target=bystander).start()
print("cell A has started two watches", flush=True)
"""

CELL_B = """
def later():
    time.sleep(0.3)
    print("cell B's thread says", flush=True)
threading.Thread(target=later).start()
print(t1.result(), t2.result())
"""

way = sys.argv[1]
buffered = sys.argv[2:] == ["buffered"]
with kernel() as client:
    known = {"watch-1": "watch-1", "watch-2": "watch-2"}
    cell_a = CELL_A % SET_PARENT[way]
    if buffered:
        cell_a = cell_a.replace('says", flush=True)', 'says")')
    a, seen = run(client, cell_a, settle=1.5)
    known[a] = "cell A"
    show(f"[{way}{' buffered' if buffered else ''}] cell A, two watches and a thread", a, seen, known)
    b, seen = run(client, CELL_B, settle=1.0)
    known[b] = "cell B"
    show(f"[{way}] cell B", b, seen, known)
