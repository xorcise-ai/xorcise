# tests/unit/test_core_no_telemetry_provider_import.py
"""Guard: the core control path must NEVER import a CONCRETE telemetry provider — the Claude Code
provider lives in the harness_adapters tier and self-registers when load_providers() imports it,
which launch_profile_for lazy-imports. Static AST scan, mirroring
test_core_no_openhands_import.

Add a row to _CONCRETE_PROVIDERS per new provider so the guard actually protects each one."""

from __future__ import annotations

import ast
from pathlib import Path

_CORE_FILES = (
    "src/xorcise/core/contracts/connect.py",
    "src/xorcise/core/runs/prompt.py",
    "src/xorcise/core/runs/telemetry/__init__.py",
    "src/xorcise/core/runs/telemetry/base.py",
    "src/xorcise/core/runs/telemetry/registry.py",
    "src/xorcise/core/runs/telemetry/generic.py",
    "src/xorcise/core/rest/run_create.py",
    "src/xorcise/core/rest/routers/runs.py",
)

# (dotted module, source-token, registered slug) — one row per concrete provider.
# The concrete provider lives in the harness_adapters tier; the core control
# path still must not import it (it self-registers when load_providers() imports it).
# The token is a source substring the scan forbids in a core file; it must appear only in a real
# import, never in prose. Bare "codex" collides with legitimate prose ("codex ignores…") in
# run_create.py/routers/runs.py, so codex uses the dotted import fragment instead.
_CONCRETE_PROVIDERS = (
    ("xorcise.core.harness_adapters.claude_code.telemetry", "claude_code", "claude-code"),
    ("xorcise.core.harness_adapters.codex.telemetry", "harness_adapters.codex.telemetry", "codex"),
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _all_imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            mods.update(f"{node.module}.{alias.name}" for alias in node.names)
    return mods


def test_core_control_path_does_not_import_concrete_provider():
    for rel in _CORE_FILES:
        imported = _all_imported_modules(_REPO_ROOT / rel)
        for module, _token, _slug in _CONCRETE_PROVIDERS:
            assert not any(m == module or m.startswith(module + ".") for m in imported), (
                f"{rel} imports concrete provider {module}; control path must stay harness-free."
            )


def test_core_control_path_has_no_concrete_provider_token():
    for rel in _CORE_FILES:
        src = (_REPO_ROOT / rel).read_text()
        for _module, token, _slug in _CONCRETE_PROVIDERS:
            assert token not in src, (
                f"{rel} references provider module token '{token}'; must stay harness-free."
            )


def test_load_providers_registers_all_providers():
    from xorcise.core.harness_adapters import load_providers
    from xorcise.core.runs.telemetry.registry import registered_names

    load_providers()
    names = registered_names()
    for _module, _token, slug in _CONCRETE_PROVIDERS:
        assert slug in names, f"{slug!r} not registered after harness_adapters.load_providers()"
