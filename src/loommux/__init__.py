"""Public protocol-neutral Python API for persistent IPython sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from loommux.session import IPythonSession

    __version__: str

__all__ = ["IPythonSession", "__version__"]


# Every kernel imports loommux.kernel_extension at start, which imports this
# package first. Eager imports here would load the session, Jupyter client,
# and tokenizer into each kernel (about 85 ms) for nothing it uses.
def __getattr__(name: str) -> Any:  # noqa: ANN401
    if name == "IPythonSession":
        from loommux.session import IPythonSession

        return IPythonSession
    if name == "__version__":
        from importlib.metadata import version

        return version("loommux")
    raise AttributeError(f"module 'loommux' has no attribute {name!r}")
