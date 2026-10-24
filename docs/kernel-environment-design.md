# Kernel Environment Design

> **Status: Written on October 24, 2026.** This document owns the
> configuration directory loommux is started with, the startup files every
> kernel runs before its first cell, the notice channel through which code in a
> kernel speaks to the agent, the execution number across a restart, and the
> `idle_since` time of a kernel. The
> [Coding Agent Control Plane Design](coding-agent-control-plane-design.md)
> keeps workspace semantics, the kernel bootstrap policy and the terminal text
> contract; the [Kernel Resource Daemon Design](kernel-resource-daemon-design.md)
> keeps leases and the control plane; the
> [Main Kernel Binding Design](kernel-binding-design.md) keeps main kernels,
> homes and retention.

## 1. Decision

loommux uses one **configuration directory**: the nearest `.loommux/` at or
above the directory it is started in, or the directory `--config DIR` names.

```text
loommux --server              # in the workbench, or any directory below it

.loommux/           found upward from the launch directory
    config.py        read once, by the daemon, when it starts
    startup/         run by every kernel, every time it starts, in name order
        00-vitals.ipy
```

- The daemon logs, as it starts, which directory it uses and how it was found.
- `config.py` is a Python module. Its plain names are settings, such as
  `lease_mode = "connection"`; a function `resolve_workspace(launch_cwd)`
  chooses the kernel workspace. A public name that is not a setting stops the
  daemon before it serves anything.
- The `.py` and `.ipy` files in `startup/` run in every kernel process, after it
  is ready and before it takes its first cell, without taking an execution
  number. A file that fails becomes a notice.
- Code in a kernel speaks to the agent with `loommux.notify(text)`. A notice is
  not output: it never enters the output streams, it is never cut by the
  5,000-token limit on output, and every tool result ends with the notices not
  delivered yet, each delivered once.
- A loommux execution number is IPython's own: restart clears the records and
  the next cell is `In [1]`, exactly as IPython counts it.
- A kernel's snapshot says since when it has been idle, `idle_since`.

Nothing else configures loommux. The environment variables that used to carry
settings and the workspace resolver path are gone.

## 2. Where The Shape Comes From

Each rule below is one other tools reached before; loommux takes it as it is.

| Rule | Prior art | Here |
| --- | --- | --- |
| Configuration and startup files live together in one directory | an IPython profile directory holds `ipython_config.py` and `startup/` | `.loommux/` holding `config.py` and `startup/` |
| A tool finds its configuration by walking up from where it starts, and the nearest wins; a flag names another | git finds `.git`, just a `justfile`, Ruff and pytest a `pyproject.toml`, Claude Code `CLAUDE.md` and `.claude/`; `ruff --config`, `just --justfile`, `ipython --profile-dir` | the nearest `.loommux/`; `--config DIR` |
| A configuration file is a Python module whose plain names are the settings | Sphinx `conf.py`; Django settings modules | `lease_mode = "connection"` |
| Startup files run in name order, `.py` and `.ipy` alike, and are not recorded as input | IPython runs `startup/00-first.py` … with `store_history=False` | each file is one request with `store_history=False` |
| An unknown option is an error, not a silent default | argparse rejects unknown options; Ruff rejects unknown settings | an unknown name in `config.py` stops the start |
| Restarting a kernel starts its count again | after a Jupyter Notebook restart the next cell is `In [1]` | records are cleared, the next cell is `In [1]` |
| A warning travels apart from the program's output, and a repeated one is shown once | Python's `warnings` reach stderr, not stdout, and the default filter shows each once; an andon lamp hangs above the station, not on the product | notices travel outside the output, each delivered once |
| What the harness says inside a turn is wrapped in a tag that names it, apart from what it wraps | Claude Code wraps its own words in `<system-reminder>`; Anthropic's prompting guidance separates kinds of content with XML tags | the environment's lines are one `<loommux-notice>` envelope |

## 3. Journeys

1. Xiaoliang starts loommux with `just loommux`, which runs `loommux --server`
   in the workbench. The daemon logs that it uses the workbench's `.loommux/`.
   Every value it uses, and why, is in `.loommux/config.py`; the justfile
   recipe is one command. A Claude Code that starts loommux over stdio in a
   project below the workbench finds the same directory, as it finds the
   workbench's `CLAUDE.md`.
2. An agent's first cell opens a main kernel. The kernel already holds the
   environment the startup files set up, such as the vitals alarm, and the
   agent's first cell is `In [1]`.
3. A cell prints 40,000 tokens while memory crosses the vitals line. The result
   says the output was omitted and how to read it, and then gives the vitals
   line. Before, that line was the last line of the omitted output.
4. Someone breaks `00-vitals.ipy`. The kernel still starts, and the first
   result ends with `Startup · 00-vitals.ipy failed: NameError: …`. Before, the
   agent would have met the missing name later as if it were its own mistake.
5. The agent restarts its kernel. The next cell is `In [1]`; the startup files
   ran again; `read_output(5)` for a cell before the restart answers
   `execution_not_found`. What the agent saw of those cells is in its Claude
   Code transcript.
6. A cell forwarded to the child kernel `build` ends while `build`'s vitals has
   something to say. The forwarding cell's result ends with `build · ⚠ 体征｜…`.
7. The vitals alarm in a main kernel reads `idle_since` of its child kernels and
   asks whether a child idle for an hour is still needed.

## 4. The Configuration Directory

### 4.1 Finding it

Without `--config`, loommux looks at the launch directory and then at each
ancestor in turn, and uses the first `.loommux` that is a directory. When none
is found, every setting has its default and the workspace is the launch
directory.

`--config DIR` names the directory instead, and nothing is searched. A relative
path is read from the launch directory, as every command-line path is. The
directory must exist.

`config.py` and `startup/` are each optional, so a directory with neither
configures nothing and is accepted.

As it starts, the daemon logs one line: the directory and whether it was found
or named, or that there is none. Which configuration applies is then a fact in
the log, as `git rev-parse --show-toplevel` answers which repository a command
is in.

An earlier version of this design never searched. Its reason was that a file in
the tree could change the workspace and run before any kernel existed, so only
a command that named the directory could allow it to run. That reason guards the
tree from the person who starts loommux, and they are the same person: loommux
is started by its owner, in a directory of theirs, by a command or an MCP
configuration they wrote, and the kernels it opens run whatever that person
types. Starting a program in a directory is already the act of trusting it, as
it is for just with a justfile, for pytest with a `conftest.py`, and for Claude
Code with the hooks in `.claude/`.

### 4.2 `config.py`

The daemon runs `config.py` once, in its own process, before it serves
anything. These module-level names are read:

| Name | Default | Meaning |
| --- | --- | --- |
| `lease_mode` | `"activity"` | `"activity"`, `"heartbeat"` or `"connection"` ([daemon design](kernel-resource-daemon-design.md), section 5) |
| `private_activity_timeout_seconds` | `4800` | activity window of an unnamed main kernel |
| `named_activity_timeout_seconds` | `86400` | activity window of a named main kernel, and in connection mode how long a named main kernel is kept after its sessions leave |
| `heartbeat_interval_seconds` | `15` | heartbeat mode: how often a client renews |
| `heartbeat_timeout_seconds` | `60` | heartbeat mode: how long a lease survives without renewal; greater than the interval |
| `connection_grace_seconds` | `60` | connection mode: how long a closed stream may reopen before its lease is released |
| `sweep_interval_seconds` | `10` | how often leases and retention are checked |
| `orphan_grace_seconds` | `30` | how long a kernel that nothing anchors may keep running a cell |
| `resolve_workspace(launch_cwd)` | absent | returns the kernel workspace; its presence makes `workspace_resolution` `explicit_config` |

Every number must be positive and finite. A configuration file is also a small
program: it may import modules and define helpers. So names starting with `_`,
and names bound to a module, a class or a function other than
`resolve_workspace`, are its own and are not read. Every other module-level name
must be a setting from the table; a misspelled `retention_seconds` stops the
start instead of running for a day on the default.

Loading fails before any tool is exposed, with one of these statuses:

| Status | Condition |
| --- | --- |
| `config_not_found` | `DIR` does not exist |
| `config_not_directory` | `DIR` is not a directory |
| `config_load_failed` | `config.py` raised while it ran |
| `config_unknown_setting` | a public plain name is not a setting; the message names it |
| `config_invalid_setting` | a setting has the wrong type or value; the message names it |

The workspace statuses of the control-plane design (section 5.4) are unchanged
and apply to what `resolve_workspace` returns.

### 4.3 One entrance

A value that can be set in two places is a value whose reader of one place
cannot know it. So the environment no longer configures loommux:
`LOOMMUX_LEASE_MODE`, the seven `LOOMMUX_*_SECONDS` variables and
`LOOMMUX_WORKSPACE_CONFIG` are gone. A test or a load run that wants other
values writes its own directory and passes it.

Two kinds of `LOOMMUX_` variables remain because they are not configuration:
the identity variables loommux sets in a kernel's environment to tell it who it
is, and `LOOMMUX_CONTROL_URL`, which tells a `Daemon()` client outside a kernel
where the daemon is.

## 5. Startup Files

### 5.1 What runs, and when

The files in `startup/` whose names end in `.py` or `.ipy` run in name order,
each as its own execute request with `store_history=False`, in the kernel's
user namespace: a `.py` file as IPython's `safe_execfile` runs one, an `.ipy`
file as `safe_execfile_ipy` does, so it may use magics such as `%run`. Other
files are ignored.

They run in every kernel process after it is ready and before it takes its
first cell: when a main or child kernel opens, after `restart`, and after the
process replacement that stands for an interrupt on Windows. The directory is
read at each start, so a changed file applies to the next kernel without
restarting loommux.

`store_history=False` is why they take no number: IPython counts only requests
that store history, so the agent's first cell is still `In [1]`.

### 5.2 Why loommux sends them

IPython can run startup files itself (`exec_files`, a profile's `startup/`),
but it does so while the kernel initializes, before any request exists. Their
output and tracebacks then have no parent request, and loommux collects only
what answers its own requests. A failing file would be invisible, and the agent
would meet what it should have defined as its own `NameError`. A file sent as
loommux's own request has an owner for everything it prints and raises.

### 5.3 Failure

A file that raises, or that has not finished after 60 seconds and is
interrupted, becomes the notice `Startup · NAME failed: Ename: evalue`. The
next files still run, as IPython continues past a failing startup file. A file
that succeeds is silent: what it prints is discarded. Notices it speaks are
startup notices.

### 5.4 What belongs there

Environment, not tools. A tool answers when it is called, and loading it is one
line when it is needed. An environment, such as an alarm that watches memory,
works only if nobody has to remember to start it; a step someone must remember
is exactly what it exists to remove. loommux runs whatever the directory holds;
this is the rule for whoever writes it.

The user's own IPython profile is still never read (control-plane design,
section 6.3). What configures a kernel is the `.loommux/` of the tree loommux
runs in, not IPython's per-user state.

## 6. Notices

### 6.1 Two kinds of information

Output is what a cell produced. A notice is the environment telling the agent
something about this moment: memory is low, a reply arrived, a child kernel
has idled for an hour. The 5,000-token limit protects the agent's context from
output that floods it; it should not also swallow the alarm that happened to
fire in that cell. The vitals alarm and the buddy chime grew the same
workaround independently: print one last line with a fixed prefix, and tell
readers to `search_output` for it when the output was omitted. Two tools growing
one shape is the sign that the shape needs a place of its own.

### 6.2 Speaking

`loommux.notify(text)` speaks inside a cell, including from `pre_run_cell` and
`post_run_cell` hooks. It publishes display data whose only MIME type is
`application/vnd.loommux.notice`: a Jupyter frontend has nothing it renders,
and loommux records the text on the execution instead of in any output stream,
so `read_output` and `search_output` never see it. Text spoken while no cell
runs has no request to belong to and is lost. Outside a kernel, `notify`
raises.

### 6.3 Delivery

Every MCP tool result ends with the notices of its main kernel that no result
has delivered: those of finished executions, and those of the kernel's startup.
Each is delivered once, in the order spoken. A notice from the execution the
result shows has no label; one from another execution is labeled `In [N] · `,
one from the startup files `Startup · `. The notices come after the execution
content, the omission message included, inside the envelope of section 6.4,
before `Kernel:` and `Lease alert`. Structured results carry them as `notices`
too.

A cell still running when its call returns has spoken nothing final; its
notices come with whichever result follows its end, a `wait` or any other call.

### 6.4 The envelope

A tool result is a list of text blocks, and the reader does not see where one
ends. Claude Code joins a result's blocks end to end, nothing between them; a
notice in a block of its own, after an omission message that did not end with a
newline, reached the agent as the tail of that message's last sentence. A block
of its own separates nothing.

So everything the environment says in one result is one block, opened on a
line of its own and closed by a tag:

```text
<loommux-notice>
⚠ 体征｜内存 2.1 GiB，越过 2 GiB
build · build has run nothing since 14:02; loommux.recycle('build') if it is done
Kernel: 提醒包裹设计 (opened)
</loommux-notice>
```

The block begins with a newline, so the envelope starts a line whatever came
before it. Inside are, in order, the notices, the `Kernel:` line and the
`Lease alert`: what a kernel's environment said and what the daemon said are
one kind of information, the environment speaking about this moment (section
6.1), and the reader needs one boundary, not three. Each line keeps the prefix
that names its speaker, `⚠ 体征｜`, `build · `, `In [N] · `, `Startup · `,
`Kernel:`, so the tag carries no `from`; it would say the same thing twice. A
result with nothing to say has no envelope. Structured results keep `notices`,
`binding` and `lease_alert` as fields.

The envelope is a tag because a tag says whose words these are and where they
end; Claude Code's `<system-reminder>` and the `<file path=…>` containers of the
workbench's reading tools are the same shape, and the agent reads it without
being taught. It is not a fenced code block: a fence marks quoted literal
material to be read as it is, which is what output is, not what the environment
telling the agent something is, and output may hold fences of its own. It is
not `<system-reminder>` or a `developer` role either: those are the host's
voice and a message role, and a tool speaking in them claims a speaker it is
not; a reader that learned to distrust host tags inside tool output would
discount it. The name is `notice`, the word of `loommux.notify` and of the MIME
type, one word for one thing.

### 6.5 Bounds

This channel passes the context guard, so it carries its own bound in its
shape. A notice is cut at 500 characters and ends with `…`; an execution keeps
its first 10 notices and reports how many more it had. A notice is an alarm and
a pointer to act on it; a report is something the agent asks for.

### 6.6 Child kernels

No MCP session works in a child kernel, so its notices travel through the
cell that forwarded to it: when the forwarded cell ends, the forwarding cell
speaks each of its notices again, prefixed with the child's name, `build · `.
The control plane's `combined` follow hands an execution's notices over once,
with its final state. Notices of a child execution nobody followed stay with
its record.

### 6.7 Restart

Restart clears the records, and the notices they had not delivered go with
them. The startup files of the new process speak anew.

## 7. Execution Numbers

### 7.1 One number

The loommux execution `N` is the number IPython gives the same cell: `In [N]`
in a result, `In[N]` and `_iN` in the kernel, `%history N`. The two agree by
construction. IPython counts a cell when it starts to run it, a syntax error
included, and does not count requests that store no history, which is how the
bootstrap lines and the startup files run. loommux accepts one cell at a time
and sends every accepted cell to the kernel. A test watches the agreement.

### 7.2 Restart starts again

loommux used to keep its records across a restart and continue its count.
From the first restart on, its `N` and IPython's `N` named different cells,
and `status` had to warn that the kernel's own count must never be used.

What the kept records offered is kept elsewhere already: Claude Code stores
every tool call and every result in its transcript, which the transcripts SDK
reads back. The one exception is an output body omitted by the 5,000-token
limit, which only loommux holds, and restart loses it with the rest.
Restarting is starting again, as it is in a notebook. The process replacement
that stands for an interrupt on Windows is a new process too, and starts again
the same way.

### 7.3 Removed

`kernel_execution_count` leaves `status`, and `execution_count_at_submit`
leaves `execution_status`. Each existed only to tell the two counts apart.

## 8. `idle_since`

A kernel's snapshot, in the console, the control plane and
`loommux.kernels()`, carries `idle_since`: the time, in ISO 8601 UTC, at which
the kernel last became idle, which is the end of its last execution or the
start of its process, and `null` while it runs a cell. The time is born in the
kernel's session, so it is reported from there and reminders about forgotten
kernels read it rather than guess. loommux retires nothing by it; leases and
retention decide a kernel's life.

## 9. Rejected Alternatives

| Alternative | Why not |
| --- | --- |
| Name the directory only with `--config`, never discover it | it guards the tree from its own owner, who is the person starting loommux (section 4.1) |
| Look only in the launch directory, not its ancestors | a Claude Code that starts loommux in a project below the workbench would miss the workbench's environment; `.git`, `pyproject.toml` and `CLAUDE.md` are all found upward |
| Keep the environment variables as an override layer over the file | one value with two entrances; a test that needs other values writes its own directory |
| Let IPython run the startup files through `exec_files` | their failures would have no owner and reach nobody (section 5.2) |
| Keep notices as the last printed line | the 5,000-token limit omits them with the output they end |
| Give each kind of environment line its own text block | the client joins blocks end to end, so nothing separates them from output that does not end with a newline (section 6.4) |
| Fence the environment's lines in a Markdown code block | a fence marks quoted material, and output may hold fences (section 6.4) |
| Wrap them in `<system-reminder>`, or label them `developer` | the voice of the host and of a message role, claimed by a tool (section 6.4) |
| Exempt the last lines of omitted output from the limit | the last line of output is not necessarily a notice, and any print could ride on it |
| Keep records across restart and number from 1 again | two cells with one number in one record set |
| Run startup files only in main kernels | an agent writes cells in a child kernel too, through `--kernel`; a startup file that cares can ask `loommux.self` which kernel it is in |

## 10. Verification

| ID | Behavior | Proved by |
| --- | --- | --- |
| E1 | the directory's settings take effect, and `resolve_workspace` sets the workspace and `explicit_config` | `tests/test_config.py`, `tests/test_workspace_config.py` |
| E2 | a missing or non-directory path, a raising file, an unknown name and an invalid value each stop the start with their status | `tests/test_config.py` |
| E3 | without `--config`, the nearest `.loommux/` at or above the launch directory is used and none means defaults; `--config` overrides and the discovered one is not run; the start logs which | `tests/test_config.py`, `tests/mcp/test_entrypoints.py` |
| E4 | IPython's count agrees with loommux's; after restart the next cell is `In [1]` and earlier records are gone | `tests/session/test_session.py` |
| E5 | a notice arrives after omitted output, stays out of the output streams, is delivered once, labeled when it is another execution's, and is bounded | `tests/session/test_notices.py`, `tests/mcp/test_presentation.py` |
| E6 | a forwarded cell relays its child's notices | `tests/mcp/test_kernel_control.py` |
| E7 | startup files run at open and restart, take no number, and a failing one becomes a notice while the next still runs | `tests/session/test_startup.py` |
| E8 | `idle_since` is `null` while a cell runs and set after it | `tests/session/test_session.py` |
| E9 | the notices, `Kernel:` and `Lease alert` of a result are one `<loommux-notice>` block that starts a line, after output that does not end with a newline too; a result with nothing to say has none | `tests/mcp/test_presentation.py` |
