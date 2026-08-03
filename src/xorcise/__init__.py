"""XORCISE — evaluate cyber AI agents.

LAYER: distribution root. The top-level ``xorcise.*`` namespace is reserved for
shippable products; all internal code lives under ``xorcise.core.*``.
ZERO side-effect imports — only ``__version__`` is exposed here.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("xorcise")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
