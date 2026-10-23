# A Kernel Environment, Running

One command shows how the pieces of a loommux workbench work together, as an
agent sees them:

```bash
uv run python examples/kernel-environment/tour.py
```

The tour copies this directory to a temporary one, starts loommux in its
`src/` subdirectory, and calls the MCP tools the way Claude Code does. Every
step prints the tool result as the agent receives it and checks it, so the
tour stops if loommux ever stops behaving as shown.

## What is here

```text
kernel-environment/
    .loommux/
        config.py                 the daemon's settings: connection leases
        startup/
            10-long-cells.py      tells the agent when a cell ran long
            20-idle-children.py   asks about child kernels that run nothing
    tour.py
```

The two startup files are the ones the
[kernel environment tutorial](../../docs/kernel-environment-tutorial.md)
writes line by line.

## What the tour shows

| Step | The agent does | It sees |
| --- | --- | --- |
| 0 | starts loommux in `src/` | `loommux: configuration …/.loommux (found from the launch directory)`: the directory is found upward, as git finds `.git` |
| 1 | `%%loommux --attach 巡览 --create` | `In [1]`, with the startup hooks already defined, and `Kernel: 巡览 (opened)` |
| 2 | `%%loommux --kernel build` | the child kernel's output, relayed into this cell |
| 3 | a cell that sleeps and prints 150,000 characters | the output omitted, and after it both notices: the cell ran long, and `build` runs nothing |
| 4 | `loommux.kernels()`, `loommux["build"].run_cell(…)`, `loommux.recycle("build")` | every kernel, `build`'s answer, and its retirement: the daemon's buttons and a kernel's |
| 5 | a typo in a startup file, then `restart` | `Startup · 30-typo.py failed: SyntaxError …`, and the next cell is `In [1]` |

The tour lowers the hooks' thresholds in its first cell, from ten and thirty
minutes to seconds, so it finishes in about fifteen.

## Using it as a starting point

Copy `.loommux/` to the root of a tree you work in. loommux started anywhere
below finds it. Startup files are for environment, things that work only if
nobody has to remember to start them; tools stay one line to load when needed.
The rules are in the
[Kernel Environment Design](../../docs/kernel-environment-design.md) and the
client is described in the
[Kernel Control Surface Design](../../docs/kernel-control-surface-design.md).
