"""Public protocol-neutral Python API for persistent IPython sessions."""

from importlib.metadata import version

from loommux.session import IPythonSession

__version__ = version("loommux")

__all__ = ["IPythonSession", "__version__"]
