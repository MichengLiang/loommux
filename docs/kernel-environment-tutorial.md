# Writing A Kernel Environment

A tool answers when it is called, and loading it is one line when it is
needed. An environment is different: it is already there when a kernel starts,
it watches without being asked, and it speaks only when something deserves
attention. An alarm that someone must remember to start is a tool pretending to
be an environment.

This tutorial builds a small environment from the three pieces loommux offers:
a configuration directory, startup files that every kernel runs before its
first cell, and `loommux.notify`, which speaks to the agent apart from a cell's
output. The rules behind each piece are in the
[Kernel Environment Design](kernel-environment-design.md).

The finished directory is
[examples/kernel-environment](../examples/kernel-environment/README.md). Its
`tour.py` runs everything below against a real loommux and prints what the
agent sees at each step: `uv run python examples/kernel-environment/tour.py`.

## 1. The configuration directory

Create `.loommux/` at the root of the tree you work in:

```text
project/
    .loommux/
        config.py
        startup/
```

`config.py` holds settings as plain names. One line is enough here:

```python
lease_mode = "connection"
```

Start loommux anywhere in the tree. It finds the nearest `.loommux` upward, as
git finds `.git`, and says so on standard error:

```text
loommux: configuration /home/me/project/.loommux (found from the launch directory)
```

`--config DIR` names a different directory instead.

## 2. A first startup file: cells that ran long

Files in `startup/` run in name order, in the kernel's user namespace, before
the agent's first cell. They take no execution number, so the first cell is
still `In [1]`. `loommux` is already defined there.

`startup/10-long-cells.py` registers two IPython hooks:

```python
import time
from datetime import timedelta

_LONG = timedelta(minutes=10)
_started: float | None = None


def _start(info):
    global _started
    _started = time.monotonic()


def _end(result):
    # The startup request that registers these hooks ends too, without having
    # started a cell through them.
    if _started is None:
        return
    took = timedelta(seconds=round(time.monotonic() - _started))
    if took >= _LONG:
        loommux.notify(f"⏱ this cell ran {took}")


get_ipython().events.register("pre_run_cell", _start)
get_ipython().events.register("post_run_cell", _end)
```

Two details come from where the file runs:

- What it defines lands in the user namespace, beside the agent's own names.
  Names starting with `_` stay out of the way.
- The file runs inside a request of its own, so `post_run_cell` fires once for
  that request, right after the hook is registered. `_started` is still `None`
  then, and the hook stays quiet.

## 3. Asking the daemon: child kernels nobody uses

A main kernel can open child kernels with `%%loommux --kernel build`. They live
as long as their main kernel, so one that finished its job keeps its memory
until someone recycles it. `startup/20-idle-children.py` notices that:

```python
from datetime import UTC, datetime, timedelta

from loommux.daemon import DaemonError

_IDLE = timedelta(minutes=30)
_told: set[tuple[str, str]] = set()


def _idle_children(result):
    try:
        kernels = list(loommux.kernels(timeout=2))
    except DaemonError:
        return  # a stuck or stdio daemon costs one missed check, not a held cell
    me = next((k for k in kernels if k.id == loommux.self.id), None)
    if me is None or me.snapshot["scope"] != "main":
        return  # a child kernel's siblings are its main kernel's to watch
    now = datetime.now(UTC)
    for kernel in kernels:
        since = kernel.snapshot["idle_since"]
        if kernel.snapshot["home"] != me.name or since is None or (kernel.id, since) in _told:
            continue
        if now - datetime.fromisoformat(since) >= _IDLE:
            _told.add((kernel.id, since))
            loommux.notify(f"{kernel.name} has run nothing since {since}; loommux.recycle({kernel.name!r}) if it is done")


get_ipython().events.register("post_run_cell", _idle_children)
```

- The hook runs after every cell, so it asks with `timeout=2`. The default
  control-plane timeout is a minute, and a stuck daemon would hold every cell
  that long.
- `kernels()` lists every kernel, as the console does. A child kernel's row
  carries `home`, the name of its main kernel, so the hook keeps the rows whose
  home is its own. Startup files run in child kernels too, so the hook speaks
  only in a main kernel and each child is reported once.
- `idle_since` is when the kernel last became idle, or `None` while it runs.
  A child is reported once per idle stretch: `(id, idle_since)` changes when it
  runs again, and the next stretch may be reported anew.

## 4. What the agent sees

With both files in place, a cell that crosses both thresholds ends like this:

```text
In [3]:

⏱ this cell ran 0:12:41
build has run nothing since 2026-10-23T20:30:03.526315+00:00; loommux.recycle('build') if it is done
```

The notices are their own block after the cell's output. They are not part of
any output stream, so `read_output` never shows them, and a cell whose output
passes the 5,000-token limit still ends with them, after the omission message.
Each is delivered once.

## 5. When a startup file breaks

A typo in `30-typo.py` does not stop the kernel. The next tool result says what
happened:

```text
Python kernel restarted.

Startup · 30-typo.py failed: SyntaxError: invalid syntax (30-typo.py, line 1)
```

The other files still ran. The directory is read each time a kernel starts, so
the fix applies to the next kernel or the next `restart` without restarting
loommux.

## 6. Shaping a notice

- **One line and a pointer.** A notice says what happened and the line of code
  that deals with it. It passes the context guard, so it is cut at 500
  characters and a cell keeps ten. A report is something the agent asks for.
- **Once per occurrence.** Speak when a line is crossed, not while it stays
  crossed. The `_told` set above is the whole mechanism.
- **Never hold the cell.** A hook that asks something outside the kernel bounds
  the wait and treats a failure as "nothing to say this time".
- **Later is the same as at start.** A hook registered from an ordinary cell,
  in the middle of work, speaks through the same `loommux.notify` and arrives
  the same way. Startup files are only for what must work without anyone
  remembering it.
