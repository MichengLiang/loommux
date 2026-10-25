"""A bare ipykernel driven by jupyter_client, and a printer for what its iopub says.

No loommux in between: these probes ask what ipykernel itself publishes, so the
daemon's collector, which keeps only the current cell's messages, must not
filter the answer.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from jupyter_client import KernelManager
from jupyter_client.blocking.client import BlockingKernelClient

Message = dict[str, Any]


@contextmanager
def kernel() -> Iterator[BlockingKernelClient]:
    manager = KernelManager(kernel_name="python3", transport="ipc")
    manager.start_kernel()
    client = manager.client()
    client.start_channels()
    try:
        client.wait_for_ready(timeout=30)
        yield client
    finally:
        client.stop_channels()
        manager.shutdown_kernel(now=True)


def run(client: BlockingKernelClient, code: str, settle: float = 0.0) -> tuple[str, list[Message]]:
    """Execute ``code``; collect every iopub message until ``settle`` seconds after its idle.

    Everything published in that time is kept, whatever its parent, because the
    question is which request ipykernel names as the parent.
    """
    msg_id = client.execute(code)
    seen: list[Message] = []
    idle_at: float | None = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if idle_at is not None and time.monotonic() > idle_at + settle:
            break
        try:
            message = client.get_iopub_msg(timeout=0.1)
        except queue.Empty:
            continue
        seen.append(message)
        if (
            idle_at is None
            and message["msg_type"] == "status"
            and message["content"]["execution_state"] == "idle"
            and message["parent_header"].get("msg_id") == msg_id
        ):
            idle_at = time.monotonic()
    return msg_id, seen


def short(msg_id: str) -> str:
    # jupyter_client numbers messages "<session>_<pid>_<n>": every message of one
    # client shares the first part, so a prefix would print the same id each time.
    return msg_id.rsplit("_", 1)[-1] if msg_id else "<none>"


def show(label: str, msg_id: str, seen: list[Message], known: dict[str, str]) -> None:
    """Print each non-status message with the request ipykernel named as its parent."""
    print(f"--- {label}  (this request: #{short(msg_id)})")
    for message in seen:
        if message["msg_type"] == "status":
            continue
        parent = message["parent_header"].get("msg_id", "")
        who = known.get(parent, f"#{short(parent)}")
        content = message["content"]
        brief = (
            (f"{content['ename']}: {content['evalue']}" if message["msg_type"] == "error" else None)
            or content.get("text")
            or content.get("data")
            or {key: content[key] for key in ("target_name", "data") if key in content}
            or content.get("code")
        )
        print(f"  {message['msg_type']:<14} parent={who:<12} {str(brief)[:80]!r}")
