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
