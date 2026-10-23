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
