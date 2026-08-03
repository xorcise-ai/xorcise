# tests/unit/test_grader_isolation.py
"""Guard: the grader assembles SealedContext from RAW evidence only — it must NEVER import or
read the per-run CACHED AgentEvent display projection (replay is display-only, never a
grading input). Static AST scan (catches module-level AND in-body imports).

One narrow exemption (mirrors .importlinter's `grader-never-reads-projection` contract + its
`ignore_imports`): grade_assembly is allowed to resolve the run's ADAPTER
(`otel.adapters.registry` + `harness_adapters.load_adapters`) and read its statically DECLARED
capability profile (`contracts.agent_event.KindSupport`) for the judge's telemetry-gaps
disclosure — that is adapter metadata, not per-run derived event data. This is an EDGE-SCOPED
(name-level) exemption, not a blanket module exemption: `_FORBIDDEN_MODULES` below still lists the
full packages, and `_ALLOWED_NAMES` allowlists ONLY the three specific names this feature imports —
any OTHER name pulled from one of those modules (e.g. `normalize_run`, `AgentEvent`,
`RunEventsView`) still fails this guard, mirroring the .importlinter contract's per-edge
`ignore_imports`. `xorcise.core.otel.store.agent_events` (the per-run CACHED projection, the
invariant's true subject) carries no exemption at all — no name from it is ever allowed. The sibling
`test_grade_assembly_source_has_no_projection_references` independently blocks any literal
reference to the projection machinery itself (`normalize_run`/`AgentEvent`/`agent_events`/
`events_view`) as defense in depth."""

from __future__ import annotations

import ast
from pathlib import Path

import xorcise.core.rest.grade_assembly as grade_assembly

_FORBIDDEN_MODULES = (
    "xorcise.core.otel.adapters",
    "xorcise.core.contracts.agent_event",
    "xorcise.core.otel.store.agent_events",
    "xorcise.core.harness_adapters",
)

# The ONLY (module, imported-name) edges the harness-capability-matrix feature is allowed to pull
# from an otherwise-forbidden module — mirrors .importlinter's per-edge `ignore_imports` exactly.
# xorcise.core.otel.store.agent_events has NO entry here: zero exemption, by design.
_ALLOWED_NAMES: dict[str, set[str]] = {
    "xorcise.core.contracts.agent_event": {"KindSupport"},
    "xorcise.core.harness_adapters": {"load_adapters"},
    "xorcise.core.otel.adapters": {"registry"},
}


def _is_forbidden(module: str) -> str | None:
    for forbidden in _FORBIDDEN_MODULES:
        if module == forbidden or module.startswith(forbidden + "."):
            return forbidden
    return None


def test_grade_assembly_does_not_import_the_projection():
    """Any import reaching into a forbidden module must be one of the exact allowlisted names —
    anything else (including a plain `import <forbidden module>`) fails."""
    src = Path(grade_assembly.__file__)
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                forbidden = _is_forbidden(alias.name)
                assert forbidden is None, (
                    f"grade_assembly imports the AgentEvent projection ({alias.name}); "
                    "the grader must read only SealedContext."
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            forbidden = _is_forbidden(node.module)
            if forbidden is None:
                continue
            allowed = _ALLOWED_NAMES.get(node.module, set())
            for alias in node.names:
                assert alias.name in allowed, (
                    f"grade_assembly imports {node.module}.{alias.name}, not in the sanctioned "
                    f"edge allowlist for {forbidden!r}; the grader must read only SealedContext "
                    "+ the adapter's declared capability profile."
                )


def test_grade_assembly_source_has_no_projection_references():
    src = Path(grade_assembly.__file__).read_text()
    for token in ("normalize_run", "AgentEvent", "agent_events", "events_view"):
        assert token not in src, f"grade_assembly references the projection token {token!r}"
