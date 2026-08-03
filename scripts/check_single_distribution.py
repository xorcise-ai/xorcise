#!/usr/bin/env python
"""Encode 'we are ONE distribution' rules:

1. No `[tool.uv.workspace]` in pyproject.toml.
2. The only top-level package under src/xorcise/ is `core` (namespace reserved
   for products).

Note: this script only flags unexpected top-level *directories* and *.py files
(not arbitrary non-.py files), which is sufficient for the current allowed set.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    errors: list[str] = []
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "[tool.uv.workspace]" in pyproject:
        errors.append("pyproject.toml must not declare [tool.uv.workspace]")

    pkg_root = ROOT / "src" / "xorcise"
    allowed_files = {"__init__.py", "__main__.py", "py.typed"}
    for child in pkg_root.iterdir():
        if child.is_dir() and child.name != "core" and child.name != "__pycache__":
            errors.append(
                f"unexpected top-level package src/xorcise/{child.name} (only 'core' allowed)"
            )
        if child.is_file() and child.name not in allowed_files and child.suffix == ".py":
            errors.append(f"unexpected top-level module src/xorcise/{child.name}")
    if errors:
        for e in errors:
            print(f"GUARD: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
