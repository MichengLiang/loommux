import os

_before: str | None = None


def _remember(info):
    global _before
    _before = os.getcwd()


def _moved(result):
    # The startup request that registers these hooks ends too, without having
    # started a cell through them.
    if _before is None:
        return
    now = os.getcwd()
    if now != _before:
        loommux.notify(f"📁 working directory is now {now}, was {_before}; %cd {_before} goes back")


get_ipython().events.register("pre_run_cell", _remember)
get_ipython().events.register("post_run_cell", _moved)
