"""Viewer-driven BYOM terrain catch-up for the v2 map — DELIVERY layer.

v2 analog of `terrain_catchup.py` (read that module first — this mirrors it exactly, targeting the
v2 update-only attribution + the v2 update store instead of v1's action store). Reads the run's
agent_events + the resolved v2 graph's MISSION-ONLY closed id vocabulary (`mission_terrain` /
`mission_element_ids` — the infra scaffold is excluded from both the prompt and the update-only
guard; the deterministic infra plane is the sole writer of infra element updates) + prior v2
attributions, attributes one bounded batch via the injected model, and persists via
`TerrainUpdateStore.record_many` + `record_considered`. `maybe_start_catchup_v2` builds the
server-owned model (None when unconfigured -> no-op) and runs the drain on a daemon thread under
its OWN per-run lock (separate from v1's `_inflight`/`_lock`) so concurrent polls never
double-attribute and a v1 catch-up in flight never blocks a v2 one (or vice versa). Store reads
live here, not in the domain module."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence

from xorcise.core import reporting, runs
from xorcise.core.config import get_settings
from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.contracts.terrain import TerrainUpdate
from xorcise.core.orchestration.clients.judge_model import build_terrain_model
from xorcise.core.orchestration.clients.token_counter import build_token_counter
from xorcise.core.otel.store.agent_events import SqliteAgentEventStore
from xorcise.core.runs.terrain_attribution_v2 import PromptContext, Scorer, attribute_batch_v2
from xorcise.core.runs.terrain_update_store import SqliteTerrainUpdateStore, _UpdateInput
from xorcise.core.runs.terrain_v2 import (
    mission_element_ids,
    mission_terrain,
    project_terrain_v2,
)

log = logging.getLogger(__name__)

# Deterministic "solved" bar for objective terminal-grade reconciliation.
# GradeResult (contracts/grading.py) has no dedicated solved/passed boolean, so the reconciler
# reads `breakdown.deterministic` — the deterministic sub-score in [0,1] — as the flag-check pass
# signal: 1.0 means every deterministic check passed, independent of the (possibly unconfigured
# or partially-scoring) LLM judge. A mission with NO deterministic checks at all has an empty
# `check_breakdown` and its `deterministic` sub-score is not a meaningful pass signal, so that
# case falls back to the full blended `overall` instead (a judge-only mission still greens on a
# maximal grade). The epsilon absorbs float-sum noise from equal-split check weights (1/n summed
# n times) without weakening the bar in practice.
_SOLVED_THRESHOLD = 1.0 - 1e-9

_inflight: set[str] = set()
_lock = threading.Lock()

# Hard backstop on the drain loop (below), mirroring v1's cap for the same reason: each pass
# records >=1 new consideration and shrinks the backlog, so the loop terminates naturally when
# `attribute_batch_v2` returns []; this cap only guards against a pathological non-shrinking store.
_MAX_DRAIN_PASSES = 2000


def is_attributing_v2(run_id: str) -> bool:
    """True while a v2 catch-up batch is in flight for this run (drives the 'attributing…' pulse).
    Tracked independently of v1's `is_attributing`."""
    with _lock:
        return run_id in _inflight


def run_terrain_catchup_v2(
    run_id: str,
    mission_id: str,
    manifest: MissionManifest | None,
    *,
    score: Scorer,
    limit: int = 8,
) -> None:
    """Synchronous: attribute the run's new events against the resolved v2 graph and persist,
    DRAINING to empty (not just one batch), so a single call fully attributes the backlog rather
    than leaving a tail behind for a later poll that may never come. Bounded by _MAX_DRAIN_PASSES.
    Directly unit-testable."""
    view = SqliteAgentEventStore().read_view(run_id)
    if view is None:
        return
    store = SqliteTerrainUpdateStore()
    events = list(view.events)
    resolved = project_terrain_v2(run_id, mission_id, manifest)
    # Mission-plane attribution must operate on AUTHORED elements only — the infra scaffold
    # (agent/hs/rc/collector, their endpoints, and infra edges) is a deterministic plane that
    # never sees the BYOM model and is the sole writer of infra element updates (`infra_updates`).
    # Both the prompt vocabulary AND the update-only guard's `known_ids` are built from the
    # mission-only subset so the model can never see or target an infra id.
    groups, nodes, edges = mission_terrain(resolved)
    known_ids = mission_element_ids(resolved)
    prompt_ctx = PromptContext(
        summary=resolved.summary,
        rubric=manifest.rubric if manifest is not None else (),
        groups=groups,
        nodes=nodes,
        edges=edges,
    )
    # Per-call prompt token safety cap (a batch is shrunk to fit). Counted with the judge tokenizer
    # — terrain has no separate tokenizer knob; the count is a documented approximation for a
    # non-OpenAI BYOM model, same as the judge's transcript budget.
    settings = get_settings()
    count_tokens = build_token_counter(settings.judge_tokenizer)
    max_prompt_tokens = settings.terrain_transcript_max_tokens
    for _ in range(_MAX_DRAIN_PASSES):
        verdicts = attribute_batch_v2(
            events=events,
            attributed_ids=store.attributed_event_ids(run_id),
            known_ids=known_ids,
            prompt_ctx=prompt_ctx,
            score=score,
            limit=limit,
            max_prompt_tokens=max_prompt_tokens,
            count_tokens=count_tokens,
        )
        # Empty => backlog drained, or a hard parse failure this pass (retryable next trigger).
        # Either way there is no progress to make now, so stop.
        if not verdicts:
            return
        updates = [
            _UpdateInput(
                event_id=v.event_id,
                target_kind=u.target_kind,
                target_id=u.target_id,
                state=u.state,
                discovered=u.discovered,
                active=u.active,
                note=v.note,
            )
            for v in verdicts
            for u in v.updates
        ]
        # Persist real updates first, then mark EVERY processed span (action or not) considered —
        # `record_considered` skips ids that already have a real row, so a no-op span gets exactly
        # one "none" marker and an actioned span gets none (it already has real rows), avoiding
        # perpetual re-attribution either way.
        store.record_many(run_id, updates)
        store.record_considered(run_id, (v.event_id for v in verdicts))
    log.warning("terrain v2 drain hit the %d-pass cap for run %s", _MAX_DRAIN_PASSES, run_id)


def maybe_start_catchup_v2(run_id: str, mission_id: str, manifest: MissionManifest | None) -> None:
    """Kick a background v2 catch-up for an active run when a model is configured. No-op if no
    model, or if a v2 catch-up for this run is already in flight."""
    model = build_terrain_model(get_settings())
    if model is None:
        return
    with _lock:
        if run_id in _inflight:
            return
        _inflight.add(run_id)

    def _work() -> None:
        try:
            run_terrain_catchup_v2(run_id, mission_id, manifest, score=model)
        finally:
            with _lock:
                _inflight.discard(run_id)

    threading.Thread(target=_work, name=f"terrain-catchup-v2-{run_id}", daemon=True).start()


def objective_grade_update(
    run_id: str, objective_id: str | None, existing: Sequence[TerrainUpdate]
) -> TerrainUpdate | None:
    """Objective terminal-grade reconciliation.

    Objective completion is model-driven live (the BYOM catch-up above), then reconciled at
    terminal against XORCISE's OWN recorded grade — `reporting.get_result` — rather than the
    terrain-attribution model's read of the trace, since XORCISE cannot independently verify a
    submitted flag; the run's GRADE is the authoritative "solved" signal. Deterministic +
    display-only: never persisted, recomputed fresh every request from the store reads this
    delivery layer already owns.

    "Solved" is keyed on the DETERMINISTIC flag-check half, not the full blended grade: when the
    mission has deterministic checks (`grade.check_breakdown` non-empty), the objective greens
    once `grade.breakdown.deterministic >= _SOLVED_THRESHOLD` — i.e. every deterministic check
    passed — independent of the LLM judge, so a genuinely-solved run with a partial or absent
    judge score still greens. A mission with NO deterministic checks (`check_breakdown` empty)
    has no deterministic pass signal to read, so it falls back to the full blended
    `grade.overall >= _SOLVED_THRESHOLD` (a judge-only mission still greens on a maximal grade).

    No-op when there's no objective, the run isn't terminal yet, it wasn't graded solved (see
    above), or `existing` already carries a `completed` update for the objective node (idempotent
    append — a later BYOM attribution of the same objective must not double it).
    """
    if objective_id is None:
        return None
    if not runs.terminal_state(run_id)[0]:
        return None
    grade = reporting.get_result(run_id)
    if grade is None:
        return None
    if grade.check_breakdown:
        solved = grade.breakdown.deterministic >= _SOLVED_THRESHOLD
    else:
        solved = grade.overall >= _SOLVED_THRESHOLD
    if not solved:
        return None
    already_completed = any(
        u.target_kind == "node" and u.target_id == objective_id and u.state == "completed"
        for u in existing
    )
    if already_completed:
        return None
    seq = max((u.seq for u in existing), default=-1) + 1
    return TerrainUpdate(
        seq=seq, target_kind="node", target_id=objective_id, event_id=None, state="completed"
    )
