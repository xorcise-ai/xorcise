# tests/unit/test_core_no_openhands_import.py
"""Guard: the core adapter framework must NEVER import a CONCRETE agent adapter — each one
self-registers via the composition point (rest/events_view.py) at import.
Static AST scan (catches module-level AND in-body imports), mirroring test_grader_isolation.py.

Generalized over ALL concrete adapters (add a row to _CONCRETE_ADAPTERS per new adapter) so the
guard actually protects each one — an adapter added without a row here would be silently
unguarded."""

from __future__ import annotations

import ast
from pathlib import Path

_CORE_FILES = (
    "src/xorcise/core/otel/flatten.py",
    "src/xorcise/core/otel/adapters/base.py",
    "src/xorcise/core/otel/adapters/generic.py",
    "src/xorcise/core/otel/adapters/registry.py",
    "src/xorcise/core/otel/adapters/genai.py",
    "src/xorcise/core/otel/adapters/__init__.py",
    "src/xorcise/core/contracts/agent_event.py",
)

# (dotted module path, lowercase module-name token, registered slug) — one row per concrete adapter.
# The concrete adapters live in the harness_adapters tier; the core otel
# framework still must not import them (they self-register when load_adapters() imports them).
_CONCRETE_ADAPTERS = (
    ("xorcise.core.harness_adapters.openhands.otel", "openhands", "openhands"),
    ("xorcise.core.harness_adapters.claude_code.otel", "claude_code", "claude-code"),
    ("xorcise.core.harness_adapters.codex.otel", "codex", "codex"),
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


def test_core_framework_modules_do_not_import_concrete_adapters():
    for rel in _CORE_FILES:
        imported = _all_imported_modules(_REPO_ROOT / rel)
        for module, _token, _slug in _CONCRETE_ADAPTERS:
            assert not any(m == module or m.startswith(module + ".") for m in imported), (
                f"{rel} imports concrete adapter {module}; core must stay agent-free."
            )


def test_core_framework_modules_have_no_concrete_adapter_token():
    """Case-sensitive: the lowercase module-name token (e.g. ``openhands`` / ``claude_code`` as
    it appears in an import), NOT prose product names — genai.py's docstring legitimately
    forward-references "OpenHandsAdapter" and "Claude Code" (documentation, not coupling), and
    neither contains the lowercase module token."""
    for rel in _CORE_FILES:
        src = (_REPO_ROOT / rel).read_text()
        for _module, token, _slug in _CONCRETE_ADAPTERS:
            assert token not in src, (
                f"{rel} references adapter module token '{token}'; core must stay agent-free."
            )


def test_events_view_composition_point_registers_all_adapters():
    import xorcise.core.rest.events_view  # noqa: F401 — the composition point
    from xorcise.core.otel.adapters.registry import registered_names

    names = registered_names()
    for _module, _token, slug in _CONCRETE_ADAPTERS:
        assert slug in names, (
            f"{slug!r} not registered after importing the events_view composition point"
        )
