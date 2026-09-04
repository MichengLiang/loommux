"""Public protocol-neutral Python API for persistent IPython sessions."""

from importlib.metadata import version

from loommux.session import IPythonSession, PreparedRunCell, prepare_run_cell

__version__ = version("loommux")

__all__ = ["IPythonSession", "PreparedRunCell", "__version__", "prepare_run_cell"]
