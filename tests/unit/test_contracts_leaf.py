"""Leaf rule: every xorcise.core.contracts.* module imports nothing
internal — stdlib + third-party only. Guards the contract seam from logic creep.
"""

from __future__ import annotations

import ast
import pathlib

_CONTRACTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "src/xorcise/core/contracts"


def _imported_modules(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_contracts_import_nothing_internal() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(_CONTRACTS_DIR.glob("*.py")):
        bad = [m for m in _imported_modules(path) if m.startswith("xorcise")]
        if bad:
            offenders[path.name] = bad
    assert not offenders, f"contracts must not import internal modules: {offenders}"
