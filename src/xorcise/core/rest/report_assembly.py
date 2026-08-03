"""Run-report assembly (rest/delivery layer) — join a run into a renderable report context.

The renderer (`reporting.render`) is pure and takes plain contract DTOs; the pieces it needs live
in FOUR different domain modules that may not import each other (runs / agents / reporting /
runcontrol are siblings under the import-linter layer rule). The delivery layer owns that join —
the same shape as `rest/grade_assembly.py`, which assembles the grader's SealedContext from the
evidence stores.

`runcontrol` stays a LAZY import (plane-isolation invariant, mirroring the runs router).
"""

from __future__ import annotations

from xorcise.core import agents, reporting, runs
from xorcise.core.contracts.reporting import ResultConditions, RunStats
from xorcise.core.contracts.run import RunEntry
from xorcise.core.contracts.terrain import ResolvedTerrainV2
from xorcise.core.reporting.render import ReportArtifact, RunReportContext

# One folded state change, normalized off either update family (the deterministic infra
# `_UpdateInput` and the persisted mission-plane `TerrainUpdate` carry the same four fields
# under the same names, but are unrelated types).
_Fold = tuple[str, str, str | None, bool | None, bool | None]
_STATE_RANK: dict[str, int] = {"defined": 0, "discovered": 1, "completed": 2}


def _agent_name(agent_id: str) -> str:
    """Resolve the registry name for a run's denormalized agent_id.

    The registry is keyed by name (`agents.get(name)`), so the id → name direction is a scan; the
    registry is operator-sized (a handful of rows), and a report is rendered on demand. Falls back
    to the raw id for a result whose agent was deleted (results outlive the agent-delete cascade
    only in flight, but a report must never 500 on a missing name)."""
    for entry in agents.list_agents():
        if entry.id == agent_id:
            return entry.name
    return agent_id


def _artifacts_for(run_id: str) -> tuple[ReportArtifact, ...]:
    """The agent's submitted work artifacts, in submission order (intel/complete excluded)."""
    # Lazy: keep the runcontrol store off this module's import path (plane-isolation invariant).
    from xorcise.core.runcontrol.store import ARTIFACT_KINDS, SqliteSubmissionStore

    return tuple(
        ReportArtifact(name=s.name, kind=s.kind, seq=s.seq, payload=s.payload)
        for s in SqliteSubmissionStore().list_for_run(run_id)
        if s.kind in ARTIFACT_KINDS
    )


def _stats_for(run: RunEntry) -> RunStats | None:
    """The run's telemetry snapshot, with the SAME live fallback the /stats endpoint uses.

    A run graded before the stats column existed has no stored snapshot; /stats folds the event
    projection live (read-only) so the Results page still shows tokens/tool calls. The report is
    that page's offline twin, so it has to fold too — otherwise the browser shows 178.7k tokens
    and the downloaded report claims no telemetry was recorded."""
    stored = reporting.get_stats(run.run_id)
    if stored is not None:
        return stored
    # Lazy: keep the otel display plane off this module's import path (plane-isolation invariant).
    from xorcise.core.otel.run_stats import fold_run_stats
    from xorcise.core.rest import events_view

    try:
        view = events_view._full_view(run.run_id)
    except Exception:  # pragma: no cover — telemetry is display-only; a report must still render
        return None
    if not view.events:
        return None
    return fold_run_stats(view.events, created_at=run.created_at, completed_at=run.completed_at)


def _fold_terrain(base: ResolvedTerrainV2, changes: list[_Fold]) -> ResolvedTerrainV2:
    """Apply every recorded change onto the base graph, producing the run's SETTLED map.

    Deliberately ORDER-FREE: the fold is monotonic and latching exactly as the frontend's
    `terrain-fold.ts` is — a node's state only climbs `defined < discovered < completed`,
    `group.discovered` and `edge.active` only latch true — so the end state does not depend on the
    order the two planes are merged in. A report needs that end state only; it never time-travels,
    which is why this skips the receipt-time interleave the live /terrain2 endpoint performs.
    A change targeting an unknown id is ignored."""
    node_state: dict[str, str] = {}
    groups_seen: set[str] = set()
    edges_active: set[str] = set()
    for kind, target, state, discovered, active in changes:
        if kind == "node":
            if state is not None:
                current = node_state.get(target, "defined")
                if _STATE_RANK.get(state, 0) > _STATE_RANK.get(current, 0):
                    node_state[target] = state
            if discovered:
                node_state.setdefault(target, "discovered")
        elif kind == "group" and discovered:
            groups_seen.add(target)
        elif kind == "edge" and active:
            edges_active.add(target)

    def lift(node_id: str, current: str) -> str:
        folded = node_state.get(node_id, "defined")
        return folded if _STATE_RANK.get(folded, 0) > _STATE_RANK.get(current, 0) else current

    nodes = tuple(
        n.model_copy(update={"state": lift(n.id, n.state)})
        for n in base.nodes
        # `state` is a Literal on the DTO; `lift` only ever returns one of its members.
    )
    groups = tuple(
        g.model_copy(update={"discovered": g.discovered or g.id in groups_seen})
        for g in base.groups
    )
    edges = tuple(
        e.model_copy(update={"active": e.active or e.id in edges_active}) for e in base.edges
    )
    return base.model_copy(update={"nodes": nodes, "groups": groups, "edges": edges})


def _terrain_for(run_id: str, mission: str) -> ResolvedTerrainV2 | None:
    """The run's resolved terrain, folded to its end state — the report's TERRAIN section.

    READ-ONLY by construction: it projects the base graph, replays the deterministic infra
    evidence and the persisted BYOM mission-plane updates, and stops. Unlike the live
    `/terrain2` endpoint it never kicks attribution catch-up or the join reconciler — downloading
    a report must not mutate a run. Returns None when there is nothing to draw, and never raises:
    the terrain is display-only, so a report must still render when the map cannot be resolved
    (an uninstalled mission, a store that has moved on)."""
    from pathlib import Path

    from xorcise.core.config import get_settings
    from xorcise.core.missions import get_installed
    from xorcise.core.otel.store import SqliteTraceStore
    from xorcise.core.otel.store.agent_events import SqliteAgentEventStore
    from xorcise.core.rest.terrain_catchup_v2 import objective_grade_update
    from xorcise.core.runcontrol.store import SqliteSubmissionStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore
    from xorcise.core.runs.terrain_update_store import SqliteTerrainUpdateStore
    from xorcise.core.runs.terrain_updates_infra import infra_updates
    from xorcise.core.runs.terrain_v2 import project_terrain_v2

    try:
        installed = get_installed(mission, Path(get_settings().missions_root))
        base = project_terrain_v2(run_id, mission, installed.manifest if installed else None)
        if not base.nodes:
            return None
        view = SqliteAgentEventStore().read_view(run_id)
        events = list(view.events) if view is not None else []
        facts = SqliteObservedFactsStore().list_for_run(run_id)
        submissions = [(s.kind, s.created_at) for s in SqliteSubmissionStore().list_for_run(run_id)]
        telemetry_ts = min(SqliteTraceStore().receipt_times(run_id).values(), default=None)
        store_updates = list(SqliteTerrainUpdateStore().list_for_run(run_id))
        changes: list[_Fold] = [
            (u.target_kind, u.target_id, u.state, u.discovered, u.active)
            for _, u in infra_updates(facts, submissions, telemetry_ts, events=events)
        ]
        changes += [
            (u.target_kind, u.target_id, u.state, u.discovered, u.active) for u in store_updates
        ]
        # Objective terminal-grade reconciliation — the ONE signal the live /terrain2 endpoint
        # recomputes on every read (never persisted) that this offline twin would otherwise miss:
        # when XORCISE's own grade says the run solved, the objective node greens even if the BYOM
        # terrain model never attributed it (or never ran — e.g. the judge/terrain key is down). A
        # report of a solved run must show the same reached objective the screen does, not a greyed
        # map (mirrors run_terrain_v2 source #3; idempotent + display-only).
        objective_id = next((n.id for n in base.nodes if n.objective), None)
        grade_upd = objective_grade_update(run_id, objective_id, store_updates)
        if grade_upd is not None:
            changes.append(
                (
                    grade_upd.target_kind,
                    grade_upd.target_id,
                    grade_upd.state,
                    grade_upd.discovered,
                    grade_upd.active,
                )
            )
        return _fold_terrain(base, changes)
    except Exception:  # pragma: no cover — display-only: never fail a report over the map
        return None


def assemble_report(run_id: str) -> RunReportContext | None:
    """Join run + agent name + grade + conditions + stats + artifacts into a report context.

    Returns None when the run is unknown OR has no recorded grade — the caller (the /report
    endpoint) distinguishes those two with the same state ladder /result uses, so this stays a
    single "is there anything to render?" answer."""
    run = runs.get(run_id)
    if run is None:
        return None
    grade = reporting.get_result(run_id)
    if grade is None:
        return None
    partial, partial_trigger = reporting.result_partial(run_id)
    # Disclosure provenance: fill intel_disclosed from the run-control submission store (delivery
    # layer owns the cross-module join; lazy import matches _artifacts_for above).
    from xorcise.core.runcontrol.store import disclosed_intel_count

    conditions = (reporting.result_conditions(run_id) or ResultConditions()).model_copy(
        update={"intel_disclosed": disclosed_intel_count(run_id)}
    )
    return RunReportContext(
        run=run,
        agent_name=_agent_name(run.agent_id),
        grade=grade,
        conditions=conditions,
        partial=partial,
        partial_trigger=partial_trigger,
        stats=_stats_for(run),
        artifacts=_artifacts_for(run_id),
        terrain=_terrain_for(run_id, run.mission),
    )
