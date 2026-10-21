# Spikes

Each directory is one experiment that settles uncertainties before a design
commits to them: its probes, and a `REPORT.md` with the questions, method,
numbers, and what they mean for the design. Reports are evidence for the
design documents in `docs/`, which cite them; probes are kept runnable so a
number can be measured again when a dependency changes.

| Spike | Question | Outcome |
|---|---|---|
| [01-anyio-async-kernel](01-anyio-async-kernel/REPORT.md) | Can the daemon drive kernels with jupyter_client's async API under AnyIO, and what does it remove and cost? | Yes on Linux (asyncio and uvloop) and Windows (Proactor); found a thread-pool starvation in the current design and a Windows handle leak in jupyter_client |
