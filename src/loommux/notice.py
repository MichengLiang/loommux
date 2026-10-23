"""The notice channel: how code in a kernel speaks to the agent apart from a cell's output.

Output is what a cell produced, and the 5,000-token limit may omit it. A notice
is the environment telling the agent something about this moment, so it never
enters an output stream and is never cut with one. Because it passes that guard,
its bound is part of its shape. The rules and their reasons are in
docs/kernel-environment-design.md, section 6.

Both sides of the channel import this module: the kernel speaks through
``loommux.notify`` and the daemon's IOPub collector recognizes the MIME type.
It stays free of imports so that a kernel pays nothing for it.
"""

# Display data of only this type renders as nothing in a Jupyter frontend.
NOTICE_MIME = "application/vnd.loommux.notice"
NOTICE_MAX_CHARACTERS = 500
NOTICES_PER_EXECUTION = 10


def bounded(text: str) -> str:
    """One notice cut to the channel's length, marked where it was cut."""
    return text if len(text) <= NOTICE_MAX_CHARACTERS else f"{text[: NOTICE_MAX_CHARACTERS - 1]}…"


def more_notices(count: int) -> str:
    """What an execution says in place of the notices beyond its limit."""
    return f"and {count} more notice{'' if count == 1 else 's'} that were not kept"
