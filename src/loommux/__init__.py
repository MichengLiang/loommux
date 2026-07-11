from importlib.metadata import version

from loommux.adapter import IPythonMCPAdapter
from loommux.mcp_ipython_server import create_mcp, mcp

__version__ = version("loommux")

__all__ = ["IPythonMCPAdapter", "__version__", "create_mcp", "mcp"]
