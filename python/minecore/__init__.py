"""MineCore mining engineering platform.

The Python package is the application and scripting layer. Engineering
computation lives in the Rust core engine, exposed here as ``minecore._core``.
"""

from minecore._core import core_build_profile, core_version

__version__: str = core_version()

__all__ = ["__version__", "core_build_profile", "core_version"]
