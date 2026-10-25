# Spikes

Each directory is one experiment that settles uncertainties before a design
commits to them: its probes, and a `REPORT.md` with the questions, method,
numbers, and what they mean for the design. Reports are evidence for the
design documents in `docs/`, which cite them; probes are kept runnable so a
number can be measured again when a dependency changes.
The three reports' conclusions are gathered in
[`docs/rebuild-brief.md`](../docs/rebuild-brief.md).

| Spike | Question | Outcome |
|---|---|---|
| [01-anyio-async-kernel](01-anyio-async-kernel/REPORT.md) | Can the daemon drive kernels with jupyter_client's async API under AnyIO, and what does it remove and cost? | Yes on Linux (asyncio and uvloop) and Windows (Proactor); found a thread-pool starvation in the current design and a Windows handle leak in jupyter_client (jupyter/jupyter_client#1147) |
| [02-hypermorph-live-objects](02-hypermorph-live-objects/REPORT.md) | Can loommux's resources and executions be Hypermorph live objects served from its own host? | Yes, with the output body outside state, one object per execution, and Hypermorph's `RuntimeServer` as the host; found a shutdown hang with uvicorn and an O(state) copy per state operation in Hypermorph |
| [03-kernel-background-waits](03-kernel-background-waits/REPORT.md) | Can a cell start work, register a watch on it and leave, while the agent waits at the tool, and what does an abandoned wait or an interrupt cost? | Yes: background output reaches iopub under its creating cell and can carry a watch's own parent through the ContextVars alone (`set_parent` leaks into threads); a blocking cell holds every watch; an abandoned `wait` keeps its server thread at HEAD; an interrupt reaches agents a cell started unless they run under `setsid` |
