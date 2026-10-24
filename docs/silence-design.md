# Waiting Until It Goes Quiet

This document records why a wait in loommux has a second condition besides its
length: return when the execution has printed nothing for a while.

## 1. Decision

```text
%%loommux --wait 600 --silence 30
%%bash
pnpm exec playwright test
```

`--wait SECONDS` stays what it was: the most the call waits. `--silence SECONDS`
returns earlier, when the execution has produced no output for that long. A cell
that finishes returns at once, as before. Whichever of the three comes first ends
the call; the cell keeps running unless it finished. The `wait` tool takes
`silence_seconds` with the same meaning, and so do `Kernel.run_cell` and
`Kernel.wait` in a kernel.

Both are on by default, at ten seconds each, on every call that waits: a cell
without a header, the `wait` tool, `Kernel.wait`. Writing one overrides it for
that call (section 4.4).

A result that returns for silence says so and shows where the output stopped:

```text
In [7]:
Running, silent for 30 s: use wait() for completion, read_output() for collected output, or search_output() to locate text.
Last lines:
  ✓ 41 passed (12.3s)
  waiting for http://localhost:5173 …
```

Any result of a running execution carries those last lines, at most five, each
cut to 200 characters, so an expired `--wait` shows how far the cell got too.

## 2. Why

An agent submitting a test or a build holds two estimates: about how long it
takes, and how often it prints. A wait with only a length serves the first.
With `--wait 600`, a run stuck at second 40 on a port that never opens, on
stdin, or on a deadlock holds the agent for nine more minutes with nothing to
see; the agent learns to write short waits instead and fetches a healthy run
in several trips. The second estimate is the one that tells stuck from slow.
With it, a generous `--wait` costs nothing when the run is healthy and little
when it is not.

## 3. Where The Shape Comes From

| Prior art | Total | Silence |
| --- | --- | --- |
| HTTP clients (httpx, requests) | the overall timeout | the read timeout: the longest gap between bytes |
| CircleCI | the job timeout | `no_output_timeout`, ten minutes by default |
| Travis CI | the job timeout | "No output has been received in the last 10m0s" |
| Jenkins build-timeout | Absolute | No Activity |

Each made them two numbers, and none let one switch change what the other
means: a loop printing every five seconds forever never goes quiet, so silence
alone would hold the caller indefinitely. The length is the bound; silence is
the early return.

CI kills the job at either limit, because nobody is watching it. Here an agent
is, so loommux only hands control back; the agent chooses to wait again, read
the output or interrupt.

## 4. Rules

### 4.1 The name

`--silence` names what is measured. `--quiet` means "print less" in almost
every command-line tool (`pip -q`, `git -q`); a reader would take it as
suppressing output. `--idle` is taken in loommux: an idle kernel runs no cell,
as in `idle_since` and the `idle` column, and a cell that prints nothing is not
idle. `--no-output` reads as an instruction.

### 4.2 What counts as output

Any output message the kernel sends for the execution: stream text on stdout
or stderr, an execute result, display data, an error. It counts when it
arrives, before terminal normalization, so a progress bar redrawing one line
with `\r` is activity even when the normalized log does not grow. A notice is
not output (Kernel Environment Design section 6) and does not reset the clock.
A forwarded cell's output is the child's output relayed into the forwarding
cell, so it counts for the forwarding cell.

### 4.3 When the clock starts

For `run_cell`, at submission: a cell that prints nothing for its first 30
seconds, importing or compiling, returns at second 30, which is the point.
For `wait`, at the later of the call's start and the last output: "nothing new
for N seconds while I wait". Measured from the last output alone, waiting again
on a run in a quiet stretch would return at once, and the second call would be
wasted. This is the read timeout's rule: the gap is measured while reading.

The clock is checked every quarter second; silence is meant in seconds.

### 4.4 On by default, ten seconds each

A default is not a convenience but the path every call that does not write the
option takes, so it is chosen by which mistake it makes. A silence that is too
short returns one call early: the agent sees `Running, silent for 10 s`, the last
lines, and waits again. A silence that is missing holds the agent through a
stall for the rest of the length, in real minutes. The first is cheap and
visible; the second is the problem section 2 starts from. So silence is on
unless the call says otherwise.

Ten seconds is `--wait`'s own default, and the two meet in a useful way. Silence
counts from submission and cannot elapse before an equal length does, so a cell
without a header behaves exactly as before. It starts to matter the moment a
cell writes a longer `--wait`, which is exactly when the agent expects a long
run: from then on a stall returns at ten seconds unless the cell also states how
often the run prints. Writing one estimate invites the other, which is the
habit section 2 asks for.

The `wait` tool's length is the same ten seconds. It is the same length on the
other panel (section 4.5); its earlier thirty seconds had no reason of its own.

There is no "off". A run known to be quiet for a while, a heavy import, an epoch
that logs once every few minutes, writes a silence as long as that stretch; a
silence as long as the wait never comes first.

The numbers are defined once, beside the header's grammar, and every panel
takes them from there. A program that leaves an argument out leaves it to the
cell's header, whose own defaults are these: `Kernel.run_cell(source)` of a cell
headed `%%loommux --wait 60` waits sixty seconds, as the same cell submitted by
`run_cell` does.

### 4.5 One meaning on both panels

| Where | Length | Silence |
| --- | --- | --- |
| cell header | `--wait SECONDS` | `--silence SECONDS` |
| `wait` tool | `timeout_seconds` | `silence_seconds` |
| `Kernel.run_cell` | `wait_seconds` | `silence_seconds` |
| `Kernel.wait` | `timeout_seconds` | `silence_seconds` |
| control plane | `wait_seconds`, `timeout_seconds` in the body | `silence_seconds` in the body |

A program's argument overrides the header; an argument it leaves out leaves the
header in charge (section 4.4).
Structured results carry `silent_seconds` when the call returned for silence
and `recent_lines` while the execution runs.

## 5. Output That Does Not Arrive

Silence is only as true as the pipe. Measured on this machine, one line a
second from each:

| Source | Lines arrived at |
| --- | --- |
| `print` in the kernel | 0.3, 1.3, 2.3, 3.3, 4.3 s |
| `!python script.py` | 0.3, 1.3, 2.3, 3.1, 4.1 s |
| `echo` in `%%bash` | 0.3, 1.0, 2.0, 3.0, 4.0 s |
| node, in `%%bash` or after `!` | 1.3, 2.0, 3.0, 4.0, 5.1 s |
| **python in `%%bash`** | **all five at 5.1 s** |
| `PYTHONUNBUFFERED=1` python in `%%bash` | 0.3, 1.0, 2.0, 3.0, 4.1 s |

ipykernel's stdout flushes itself on a short interval. `!` runs the command
through pexpect, on a pseudo-terminal, so a child sees a terminal and flushes
lines. `%%bash` runs it on plain pipes, and a Python child that sees a pipe
buffers by blocks until it exits: `uv run pytest` or `python build.py` under
`%%bash`, the most common way agents run them, would look silent for its whole
run. So the kernel's environment sets `PYTHONUNBUFFERED=1`, inherited by every
Python process the cell starts, as container images that stream Python logs do.
Nobody has to remember it.

C programs using stdio buffer on pipes too, and no environment variable turns
that off for all of them; `stdbuf -oL command` does for glibc programs. Node,
Go, cargo and the shell itself write as they go.

## 6. Rejected Alternatives

| Alternative | Why not |
| --- | --- |
| A switch that turns `--wait` into a silence limit | a run printing forever never goes quiet, and the call loses its bound (section 3) |
| Interrupt the cell at the silence limit, as CI does | an agent is present to choose; stopping is `interrupt`'s job |
| Count silence from the last output in `wait` | a second wait on a quiet run returns at once (section 4.3) |
| Measure output by the normalized log's growth | an in-place progress bar does not grow it (section 4.2) |
| Leave buffering to each agent (`python -u`, `flush=True`) | the trap is invisible until it misfires; the environment can close it once (section 5) |
| Silence off unless written, as first built | a long `--wait` without `--silence` held the agent through a stall; returning one call early costs less (section 4.4) |
| Thirty seconds for the `wait` tool, ten for `--wait` | the same length on two panels; one number in one place (section 4.4) |
| A program's arguments default to ten seconds | sent on every call, they overrode the header of a cell submitted from code (section 4.4) |

## 7. Verification

| ID | Behavior | Proved by |
| --- | --- | --- |
| S1 | a cell that stops printing returns after `--silence` seconds, still running, with `silent_seconds` and its last lines | `tests/session/test_silence.py` |
| S2 | a cell that keeps printing is not returned early, and one that finishes returns at once | `tests/session/test_silence.py` |
| S3 | `wait` with `silence_seconds` measures from the later of its start and the last output | `tests/session/test_silence.py` |
| S4 | output redrawn with `\r` resets the clock; a notice does not | `tests/session/test_silence.py` |
| S5 | the header, the `wait` tool, `Kernel.run_cell` and `Kernel.wait` accept the same silence | `tests/mcp/test_kernel_control.py` |
| S6 | a Python child under `%%bash` streams its lines as they are printed | `tests/session/test_silence.py`, which fails without `PYTHONUNBUFFERED` |
| S7 | what a call writes reaches the wait, and what it leaves out is the default; the clock itself is S1 to S3's | `tests/session/test_silence.py` |
| S8 | `Kernel.run_cell` without arguments waits as the cell's header says | `tests/mcp/test_kernel_control.py` |
