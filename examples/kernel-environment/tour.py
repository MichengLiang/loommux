"""Walk through a loommux workbench as an agent sees it.

    uv run python examples/kernel-environment/tour.py

The tour copies this directory to a temporary one, starts loommux in its
``src/`` subdirectory without ``--config``, and talks to it over MCP the way
Claude Code does. Each step prints what the agent's tool result says, then
checks it; a step that does not see what it expects stops the tour.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import anyio
import httpx
from fastmcp import Client

HERE = Path(__file__).parent


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loommux-tour-") as scratch:
        project = Path(scratch) / "project"
        shutil.copytree(HERE / ".loommux", project / ".loommux")
        (project / "src").mkdir()
        with serving(project / "src") as (origin, log):
            step("0. loommux, started in src/, found the configuration above it")
            [found] = [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("loommux: configuration")]
            show(found)
            expect(found, f"configuration {project / '.loommux'} (found from the launch directory)")
            anyio.run(tour, origin, project)


async def tour(origin: str, project: Path) -> None:
    async with Client(f"{origin}/mcp") as agent:

        async def call(tool: str, **arguments: object) -> str:
            result = await agent.call_tool(tool, arguments)
            # Joined end to end, as Claude Code hands a result to the model.
            text = "".join(block.text for block in result.content if hasattr(block, "text"))
            show(text)
            return text

        step("1. The first cell names the main kernel; the startup files ran before it and took no number")
        # The startup files' thresholds are ten and thirty minutes; a tour cannot wait that long.
        seen = await call("run_cell", freeform="%%loommux --attach 巡览 --create\n_LONG = timedelta(seconds=2)\n_IDLE = timedelta(seconds=2)\nprint('hooks from startup/:', _end.__name__, _idle_children.__name__)")
        expect(seen, "In [1] ·", "hooks from startup/: _end _idle_children", "Kernel: 巡览 (opened)")

        step("2. --kernel build runs one cell in a child kernel, opened on first use")
        seen = await call("run_cell", freeform="%%loommux --kernel build\nanswer = 6 * 7\nanswer")
        expect(seen, "Out[1]: 42")

        step("3. A slow, loud cell: the output is omitted, the two notices are not; they come in the envelope")
        seen = await call("run_cell", freeform="import time\ntime.sleep(2.5)\nprint('word ' * 30000)")
        expect(seen, "Output omitted", "\n<loommux-notice>\n⏱ this cell ran", "build has run nothing since", "\n</loommux-notice>")

        step("4. The daemon's buttons and a kernel's, from Python inside the main kernel")
        seen = await call(
            "run_cell",
            freeform=(
                "print(loommux.kernels())\n"
                "build = loommux['build']\n"
                "n = build.run_cell('answer + 1', wait_seconds=5)['execution']\n"
                "print('build answered', build.read_output(n)['text'])\n"
                "print(loommux.recycle('build'))"
            ),
        )
        expect(seen, "scope", "build answered Out[2]: 43", "recycled build")

        step("5. A broken startup file is a notice, not a broken kernel; restart starts at In [1]")
        (project / ".loommux" / "startup" / "30-typo.py").write_text("improt json\n", encoding="utf-8")
        seen = await call("restart")
        expect(seen, "Python kernel restarted.", "Startup · 30-typo.py failed: SyntaxError")
        seen = await call("run_cell", freeform="'still here'")
        expect(seen, "In [1] ·", "'still here'")


@contextmanager
def serving(launch: Path) -> Iterator[tuple[str, Path]]:
    """``loommux --server`` on a free port in ``launch``: its origin and its log."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    log = launch.parent / "loommux.log"
    with log.open("w") as sink:
        process = subprocess.Popen([sys.executable, "-m", "loommux.mcp.server", "--server", "--port", str(port)], cwd=launch, stdout=sink, stderr=subprocess.STDOUT)
    try:
        origin = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise SystemExit(f"loommux exited:\n{log.read_text(encoding='utf-8')}")
            if time.monotonic() > deadline:
                raise SystemExit("loommux did not start within 30 seconds")
            try:
                httpx.get(f"{origin}/api/lease-policy", timeout=1).raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        yield origin, log
    finally:
        process.terminate()
        process.wait(timeout=30)


def step(title: str) -> None:
    print(f"\n── {title}")


def show(text: str) -> None:
    # The omitted cell's body never arrives, but its notice lines are short; keep the rest readable.
    for line in text.splitlines():
        print(f"   │ {line if len(line) <= 160 else line[:157] + '...'}")


def expect(text: str, *pieces: str) -> None:
    missing = [piece for piece in pieces if piece not in text]
    if missing:
        raise SystemExit(f"the tour expected to see {missing}")


if __name__ == "__main__":
    main()
