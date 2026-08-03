"""Runs router — gated, persisted create/list; later lifecycle still canned."""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from xorcise.core import agents, reporting, runs
from xorcise.core.config import get_settings
from xorcise.core.contracts.agent_event import EventCursor, RunEventsView
from xorcise.core.contracts.errors import ImageNotInstalledError
from xorcise.core.contracts.grading import GradeResult
from xorcise.core.contracts.reporting import ResultConditions, RunStats
from xorcise.core.contracts.run import (
    RunCreate,
    RunCreatedEntry,
    RunEntry,
    RunEnvironmentView,
)
from xorcise.core.contracts.terrain import ResolvedTerrainV2
from xorcise.core.rest.mission_pull import MissionNotInCatalogError, PullError
from xorcise.core.rest.run_create import (
    NoAgentError,
    build_run_create_deps,
)
from xorcise.core.rest.run_create import (
    create_run as create_run_spine,
)

router = APIRouter(prefix="/runs", tags=["runs"])


class RunResultView(BaseModel):
    """Recorded result + disclosed conditions + partial state."""

    grade: GradeResult
    conditions: ResultConditions
    partial: bool = False  # True when graded on incomplete data (timeout trigger)
    partial_trigger: str | None = None  # the terminal trigger when partial


class RunArtifactView(BaseModel):
    """A single artifact an agent submitted during a run, with its full content."""

    name: str
    kind: str  # "flag" | "artifact"
    seq: int
    payload: str


@router.get("")
def list_runs() -> list[RunEntry]:
    """List runs, each stamped with `last_telemetry_at` — the newest OTLP receipt across the
    trace + log signals. A cross-module join the delivery layer owns (like /result's intel count):
    the runs module stays telemetry-blind, and the RAW stores stay canonical. Lazy import keeps
    the otel plane off the module-import path (plane-isolation invariant)."""
    from xorcise.core.otel.store import SqliteLogStore, SqliteTraceStore

    traces = SqliteTraceStore().latest_receipts()
    logs = SqliteLogStore().latest_receipts()
    entries = []
    for entry in runs.list_runs():
        latest = max(
            (t for t in (traces.get(entry.run_id), logs.get(entry.run_id)) if t is not None),
            default=None,
        )
        entries.append(
            entry if latest is None else entry.model_copy(update={"last_telemetry_at": latest})
        )
    return entries


@router.post("", status_code=201)
def create_run(payload: RunCreate) -> RunCreatedEntry:
    """Create a 1:1:1 run: gated on a registered agent + installed mission.

    Every failure maps to a clean JSON error (never a raw text/plain 500 that the CLI can't
    decode): the deps build + spine run share one try so the fail-loud
    RuntimeError (Docker/Headscale unreachable) surfaces as a 503 with its remediation.

    The 201 response is RunCreatedEntry — a RunEntry superset that adds run_control_key so the
    agent can authenticate run-control calls. The key is NEVER returned by
    GET /api/runs (list) or GET /api/runs/{id} (get) — only this once, like an API token."""
    try:
        deps = build_run_create_deps(get_settings())
        run, mission = create_run_spine(
            agent_name=payload.agent,
            mission_slug=payload.mission,
            budget_seconds=payload.budget_seconds,
            deps=deps,
            name=payload.name,
            intel_policy=payload.intel_policy,
        )
    except (NoAgentError, MissionNotInCatalogError) as exc:
        # no registered agent, or the mission is neither installed nor pullable
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ImageNotInstalledError as exc:  # fused image not built locally (or pruned)
        raise HTTPException(
            status_code=409,
            detail=f"{exc} — run 'xorcise mission ingest <bundle>' to (re)build it",
        ) from exc
    except PullError as exc:  # in-catalog but the fetch failed (e.g. registry unreachable)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:  # fail-loud: Docker/Headscale not reachable
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RunCreatedEntry(**run.model_dump(), run_control_key=mission.run_control_key)


@router.post("/{run_id}/terminate")
def terminate_run(run_id: str, background: BackgroundTasks) -> RunEntry:
    """Operator-initiated termination: abort an active run from GUI/CLI.

    404 if the run is unknown; 409 if it is already terminal. Closes run-control synchronously and
    returns the terminal entry immediately; the background finalizer drains telemetry, seals, and
    grades without blocking the caller. Result surfaces via `run status` (202 grading → grade).
    """
    from datetime import UTC, datetime

    from xorcise.core.rest.run_terminate import grade_and_record, seal_terminal

    if runs.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    if runs.terminal_state(run_id)[0]:
        raise HTTPException(status_code=409, detail=f"run '{run_id}' is already terminal")
    seal_terminal(run_id, "operator", datetime.now(UTC))
    background.add_task(grade_and_record, run_id)
    updated = runs.get(run_id)
    if updated is None:  # pragma: no cover — present above, stays present after seal
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    return updated


@router.delete("/{run_id}", status_code=204)
def delete_run(run_id: str) -> Response:
    """Operator-initiated delete of a single run's result + record.

    The narrow, per-run counterpart to the agent-scoped cascade (DELETE /agents/{name}): removes
    the recorded result and the run row so the run leaves the runs list, the agent history, and the
    results view. 404 if the run is unknown; 409 if it has not reached a terminal state — it must be
    terminated first (deleting it would strand its container/tailnet env). Idempotent on the result
    (delete of an ungraded terminal run still removes the record).
    """
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    if not runs.terminal_state(run_id)[0]:
        # Name the state rather than asserting the run "is still active": the gate is
        # `state != "terminal"`, which also catches `created` — a run that never started, where
        # "still active" describes nothing that is happening and makes "terminate it" read as
        # advice for a different situation.
        raise HTTPException(
            status_code=409,
            detail=(
                f"run '{run_id}' has not finished (state: {run.state}) — "
                f"terminate it before deleting"
            ),
        )
    reporting.delete_result(run_id)
    runs.delete_run(run_id)
    return Response(status_code=204)


@router.post("/{run_id}/regrade")
def regrade_run(run_id: str, background: BackgroundTasks) -> JSONResponse:
    """Re-evaluate a terminal run: re-grade its already-SEALED evidence (the deterministic checks +
    the LLM judge) against the CURRENT settings, without re-running the agent.

    The motivating case: a run the agent actually solved (deterministic checks pass) whose JUDGE
    half failed on a config limit — the transcript was larger than the judge's token budget, or the
    key was rejected. The operator raises the transcript budget / fixes the key in Settings, then
    re-evaluates, and the judge half runs over the same preserved evidence.

    Drops the recorded result and schedules a fresh grade off the request thread (the judge can be
    slow); the new result surfaces via the SAME `202 grading → grade` poll the initial grade uses,
    so the client needs no new state machine. De-duplicated, so a double click grades once. 404 if
    the run is unknown; 409 if it has not reached a terminal state (nothing is sealed to grade yet).
    """
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    if not runs.terminal_state(run_id)[0]:
        raise HTTPException(
            status_code=409,
            detail=(
                f"run '{run_id}' has not finished (state: {run.state}) — nothing to re-evaluate yet"
            ),
        )
    from xorcise.core.rest.run_terminate import ensure_graded_async

    # Drop the old result so a fresh grade records over it, then re-drive grading (idempotent +
    # guarded — the same path a poll uses to heal an ungraded run).
    reporting.delete_result(run_id)
    ensure_graded_async(run_id, background.add_task)
    return JSONResponse(status_code=202, content={"run_id": run_id, "status": "grading"})


@router.get("/{run_id}/prompt")
def run_prompt(run_id: str, launch_mode: str = "container") -> dict[str, str]:
    """The agent-facing mission prompt for a run. *launch_mode* rebases the run-control
    Base URL — and the container-only --add-host note — to where the agent actually runs: ``host``
    (the server's loopback bind, a terminal CLI) vs ``container`` (host.docker.internal, resolvable
    only inside a container). Defaults to ``container`` so a non-GUI caller keeps the baked host;
    the GUI passes the launch-mode toggle so the whole hand-off — not just the telemetry env —
    follows the operator's choice."""
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.rest.run_create import rebase_prompt_to_launch_mode
    from xorcise.core.runs.launch.registry import select as select_launch

    prompt = runs.get_prompt(run_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    entry = runs.get(run_id)
    load_launch_providers()
    launch, _ = select_launch(entry.source_agent if entry else "generic")
    registered_agent = agents.get_by_id(entry.agent_id) if entry is not None else None
    launch_modes = (
        (registered_agent.launch_mode,)
        if registered_agent is not None and registered_agent.launch_mode is not None
        else launch.launch_modes
    )
    rebased, _mode = rebase_prompt_to_launch_mode(
        get_settings(),
        launch=launch,
        prompt=prompt,
        launch_mode=launch_mode,
        launch_modes=launch_modes,
    )
    return {"run_id": run_id, "prompt": rebased or prompt}


@router.get("/{run_id}/launch-profile")
def run_launch_profile(run_id: str, launch_mode: str = "container") -> dict[str, object]:
    """The harness-facing LaunchProfile: the pre-start OTel env the runner injects into the agent
    container. Served here, out of the agent-facing prompt — the two-artifact split. The
    OTLP endpoint is authoritative (the server's configured collector host + port), so a harness
    need not guess it. Harness-aware: the run's source_agent selects both the telemetry
    provider and the harness launch provider. A known harness gets its solid OTel env including
    OTEL_RESOURCE_ATTRIBUTES=xorcise.run_id=<id> (correlation="resource-attr"), plus the launch
    provider's copy-paste ``command`` and per-harness ``tips``; unknown/blank harnesses get the
    run-agnostic generic env (correlation="prompt-sentinel", fallback=true). env is empty when no
    collector is configured."""
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.rest.run_create import (
        _LAUNCH_MODES,
        build_startup_tips,
        launch_profile_for,
        otlp_signal_endpoints,
        rebase_prompt_to_launch_mode,
    )
    from xorcise.core.runs.launch.base import LaunchContext
    from xorcise.core.runs.launch.registry import select as select_launch

    entry = runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    # Launch command, GUI tips, and supported launch modes come from the LAUNCH provider
    # (decoupled from telemetry).
    load_launch_providers()
    launch, _ = select_launch(entry.source_agent)
    registered_agent = agents.get_by_id(entry.agent_id)
    launch_modes = (
        (registered_agent.launch_mode,)
        if registered_agent is not None and registered_agent.launch_mode is not None
        else launch.launch_modes
    )
    # Clamp the requested mode: sanitize to a known mode, then narrow to what THIS harness
    # supports — a host-only harness (Claude Code) has no container endpoint the host CLI could
    # reach, so a container request is served the host profile instead of a dead one.
    mode = launch_mode if launch_mode in _LAUNCH_MODES else "container"
    if mode not in launch_modes:
        mode = launch_modes[0]
    profile, fallback = launch_profile_for(
        get_settings(), source_agent=entry.source_agent, run_id=run_id, launch_mode=mode
    )
    lctx = LaunchContext(run_id=run_id, source_agent=entry.source_agent, launch_mode=mode)
    # Rebase the embedded mission's run-control host to the launch mode too — a dual
    # harness with a launch command would otherwise inherit the container-baked host in host mode,
    # the same drift the standalone /prompt endpoint fixes.
    mission_prompt, _ = rebase_prompt_to_launch_mode(
        get_settings(),
        launch=launch,
        prompt=runs.get_prompt(run_id),
        launch_mode=mode,
        launch_modes=launch_modes,
    )
    # codex carries its exporter config in the command's `-c` flags (it ignores OTEL_EXPORTER_*), so
    # fill the mode-aware per-signal collector URLs into the template. Harnesses whose template
    # omits these placeholders (Claude Code) are unaffected.
    traces_ep, logs_ep = otlp_signal_endpoints(get_settings(), mode)
    command_template = (
        registered_agent.launch_command_template
        if registered_agent is not None and registered_agent.launch_command_template is not None
        else launch.command_template_for(
            registered_agent.model if registered_agent is not None else None
        )
    )
    launch_tips = (
        registered_agent.launch_tips
        if registered_agent is not None and registered_agent.launch_tips is not None
        else launch.tips(lctx)
    )
    tips = build_startup_tips(
        profile,
        command_template,
        mission_prompt,
        launch_tips,
        otlp_traces_endpoint=traces_ep,
        otlp_logs_endpoint=logs_ep,
    )
    return {
        "run_id": run_id,
        "env": dict(profile.env),
        "correlation": profile.correlation,
        "notes": list(profile.notes),
        "fallback": fallback,
        "launch_mode": mode,
        "launch_modes": list(launch_modes),
        "command": tips.command,
        "shell_block": tips.shell_block,
        "tips": list(tips.tips),
    }


@router.get("/{run_id}/environment", response_model=RunEnvironmentView)
def run_environment(run_id: str) -> RunEnvironmentView:
    """The live state of a run's mission environment (run page's Environment chip).

    Served from the readiness gate's most recent observation rather than re-probing Docker +
    Headscale per request — the page polls this while a run is live, and a nested `compose ps` exec
    on every poll would be far heavier than the fact is worth. The two states that need no probe at
    all are answered from the run row itself: a STATIC mission has no environment by design, and a
    terminal run's has been torn down.
    """
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
    if not runs.has_environment(run_id):
        return RunEnvironmentView(
            run_id=run_id, state="none", detail="static mission — no environment to start"
        )
    if run.state == "terminal":
        return RunEnvironmentView(
            run_id=run_id, state="released", detail="the environment has been torn down"
        )
    from xorcise.core.rest.run_readiness import observed_environment

    observed = observed_environment(run_id)
    if observed is None:
        # Not scanned yet (the gate ticks every few seconds, or is disabled) — report the honest
        # "still coming up" rather than inventing a ready/failed verdict.
        return RunEnvironmentView(
            run_id=run_id, state="starting", detail="waiting for the mission environment to start"
        )
    state, detail = observed
    return RunEnvironmentView(
        run_id=run_id,
        state=state,  # type: ignore[arg-type]  # classify_environment yields exactly these literals
        ready=state == "ready",
        detail=detail,
    )


@router.get("/{run_id}/traces")
def run_traces(run_id: str, since: int = -1) -> dict[str, object]:
    """Incremental RAW trace records for a run (live view fan-out).

    Pull-based and read-only: a stalled/absent poller cannot block ingest or mutate
    the RAW record. `since` is an exclusive seq cursor (default -1 → all records).
    """
    # Lazy: keep the otel plane off the module-import path so control-only roles
    # (role:control) don't drag it in at boot (plane-isolation invariant).
    from xorcise.core.otel.store import SqliteTraceStore

    records = SqliteTraceStore().read_since(run_id, since)
    return {
        "run_id": run_id,
        "records": [{"seq": r.seq, "payload": json.loads(r.payload)} for r in records],
    }


@router.get("/{run_id}/terrain2", response_model=ResolvedTerrainV2)
def run_terrain_v2(run_id: str) -> ResolvedTerrainV2:
    """v2 terrain served alongside v1 /terrain during migration (Strategy A).

    Composes the base v2 graph (infra scaffold + authored/static-ips mission graph) with the
    v2 updates stream and BYOM attribution progress. The `updates` tuple is a
    deterministic MERGE of three sources, in this fixed order (documented, not renumbered):
    1. deterministic infra updates (`infra_updates`, first-party facts/submissions, event_id=None)
       — computed fresh every request, never persisted, given synthetic seq 0..n-1;
    2. persisted BYOM mission updates (`store.list_for_run`, already seq-ordered) — kept as-is;
    3. objective terminal-grade reconciliation (`objective_grade_update`) — appended last,
       only when the run is terminal AND graded solved AND no `completed` update for the
       objective is already present (idempotent; makes the served map agree with XORCISE's own
       recorded grade, independent of the BYOM terrain model).
    The runs module takes plain data — this delivery layer does the store reads. Kicks
    the v2 catch-up ONLY when attribution is pending (v1's truthful-running fix, mirrored) so
    `attribution.running` stays truthful rather than pinned on for a fully-attributed run.
    Unknown run → 404."""
    from datetime import datetime
    from pathlib import Path

    from xorcise.core.contracts.terrain import AttributionStatus, TerrainUpdate
    from xorcise.core.missions import get_installed
    from xorcise.core.otel.store import SqliteTraceStore
    from xorcise.core.otel.store.agent_events import SqliteAgentEventStore
    from xorcise.core.rest.join_reconcile import maybe_reconcile_join, reconcile_telemetry
    from xorcise.core.rest.terrain_catchup_v2 import (
        is_attributing_v2,
        maybe_start_catchup_v2,
        objective_grade_update,
    )
    from xorcise.core.runcontrol.store import SqliteSubmissionStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore
    from xorcise.core.runs.terrain_attribution import attributable_event_ids
    from xorcise.core.runs.terrain_timeline import order_updates
    from xorcise.core.runs.terrain_update_store import SqliteTerrainUpdateStore
    from xorcise.core.runs.terrain_updates_infra import infra_updates
    from xorcise.core.runs.terrain_v2 import project_terrain_v2

    entry = runs.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    installed = get_installed(entry.mission, Path(get_settings().missions_root))
    manifest = installed.manifest if installed is not None else None
    # The OTel collector node activates once the run has exported any RAW trace records.
    telemetry_active = bool(SqliteTraceStore().read(run_id))
    resolved = project_terrain_v2(run_id, entry.mission, manifest)

    # Persist the OTel connection as a runtime fact at connection time (view-time backstop; the
    # infra-connection reconciler also latches it during the run). Terrain rendering still uses the
    # always-current `telemetry_active` below — the fact is the durable DB record.
    facts_store = SqliteObservedFactsStore()
    reconcile_telemetry(run_id, has_telemetry=telemetry_active, store=facts_store)
    facts = facts_store.list_for_run(run_id)
    submissions = [(s.kind, s.created_at) for s in SqliteSubmissionStore().list_for_run(run_id)]
    # Kick the background infra-connection reconciler for a live run — it persists join=confirmed
    # (agent node online in Headscale) and telemetry=connected (first span) at connection time,
    # independent of viewing. Self-gates: no-op once both facts exist, or Headscale isn't owned.
    if not runs.terminal_state(run_id)[0]:
        maybe_reconcile_join(run_id)
    # The agent event view feeds both the infra plane (span-linked join/brief annotations) and the
    # mission-plane catch-up below — read it once here.
    view = SqliteAgentEventStore().read_view(run_id)
    events = list(view.events) if view is not None else []
    # Unified receipt-time timeline: anchor every update to the SERVER clock and interleave, so
    # the position-based frontend fold rewinds infra + mission together, chronologically. Mission
    # updates anchor to their span's trace-ingest receipt time (fallback the agent span ts); each
    # infra update carries its OWN per-record anchor from infra_updates (join/brief/attachment fact
    # or artifact/intel/complete submission created_at, or the first-span receipt for telemetry) so
    # every run-control interaction lands at its own moment; an anchorless update (objective /
    # missing created_at) sorts at end-of-run. See runs/terrain_timeline.py.
    receipt = SqliteTraceStore().receipt_times(run_id)
    ev_by_id = {e.id: e for e in events}
    telemetry_ts = min(receipt.values(), default=None)

    infra = infra_updates(facts, submissions, telemetry_ts, events=events)
    infra_wire = [
        (
            anchor,
            TerrainUpdate(
                seq=i,
                target_kind=u.target_kind,
                target_id=u.target_id,
                event_id=u.event_id,
                state=u.state,
                discovered=u.discovered,
                active=u.active,
                note=u.note,
            ),
        )
        for i, (anchor, u) in enumerate(infra)
    ]
    update_store = SqliteTerrainUpdateStore()
    mission_wire = tuple(update_store.list_for_run(run_id))

    def _mission_key(u: TerrainUpdate) -> tuple[datetime | None, datetime | None]:
        ev = ev_by_id.get(u.event_id) if u.event_id else None
        span_ts = receipt.get(ev.raw_ref.raw_seq) if ev is not None else None
        agent_ts = ev.ts if ev is not None else None
        primary = span_ts or agent_ts
        return (primary, agent_ts or primary)

    # infra first so an exact-tie prefers infra (platform state established before the action); each
    # update carries its own per-record anchor (None → anchorless, sorts last + wire ts stays null).
    # order_updates stamps every update's `ts` with this anchor so the frontend interleaves infra
    # rows into the Trace and time-travels to them on the same server clock.
    merged_updates = order_updates(
        [(a, a, u) for a, u in infra_wire] + [(*_mission_key(u), u) for u in mission_wire]
    )
    objective_update = objective_grade_update(run_id, resolved.objective_id, merged_updates)
    if objective_update is not None:
        merged_updates = merged_updates + (objective_update,)

    # viewer-driven catch-up, for LIVE and TERMINAL runs alike (no-op without a model) — same
    # backfill rationale as v1's /terrain (a terminal run is attributed on first view; the
    # per-run in-flight lock keeps repeated browses from re-attributing).
    attributable = attributable_event_ids(events)
    considered = update_store.attributed_event_ids(run_id)
    # Only kick when work actually remains — an unconditional kick makes is_attributing (read
    # below in the same request) ~always true, pinning the "attributing…" pulse on forever for a
    # done run. Pending-gated, `running` becomes truthful (mirrors v1's fix).
    if attributable - considered:
        maybe_start_catchup_v2(run_id, entry.mission, manifest)
    attribution = AttributionStatus(
        running=is_attributing_v2(run_id),
        attributed=len(considered),
        attributable=len(attributable),
        considered_event_ids=tuple(sorted(considered)),
    )
    return resolved.model_copy(update={"updates": merged_updates, "attribution": attribution})


@router.get("/{run_id}/events", response_model=RunEventsView)
def run_events(
    run_id: str,
    since: int = -1,
    trace_since: int | None = None,
    log_since: int | None = None,
) -> RunEventsView:
    """Normalized, agent-agnostic replay events for a run (generate-on-read).

    `trace_since` and `log_since` are independent exclusive cursors. `since` is retained for
    backward compatibility and supplies either coordinate when its signal-specific parameter is
    absent. New clients should poll with `next_cursor` from the response.
    """
    # Lazy: keep the otel display plane off the module-import path (plane-isolation invariant).
    from xorcise.core.rest.events_view import events_since

    return events_since(
        run_id,
        since,
        cursor=EventCursor(
            trace_seq=since if trace_since is None else trace_since,
            log_seq=since if log_since is None else log_since,
        ),
    )


@router.get("/{run_id}/events/{event_id:path}/raw")
def run_event_raw(run_id: str, event_id: str) -> dict[str, object]:
    """The source RAW OTLP span(s) an event was derived from (drill-down).

    `{event_id:path}` — AgentEvent ids derive from base64 span ids, which may contain '/'.
    """
    from fastapi import HTTPException

    from xorcise.core.rest.events_view import raw_for_event

    result = raw_for_event(run_id, event_id)
    if result is None:
        detail = f"event {event_id!r} not found for run {run_id!r}"
        raise HTTPException(status_code=404, detail=detail)
    return result


@router.get("/{run_id}/artifacts", response_model=list[RunArtifactView])
def run_artifacts(run_id: str) -> list[RunArtifactView]:
    """The artifacts an agent submitted during a run, with full content, for operator review.

    The submission store keeps every payload, but only artifact *names* reached the grade result
    — so the flag and any submitted work were never reviewable. This
    operator-facing GET (same auth model as /result) returns the flag/artifact submissions in
    submission order; intel/complete markers are excluded. Unknown run → 404; a known run with no
    submissions → an empty list.
    """
    if runs.get(run_id) is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    # Lazy: keep the runcontrol store off the module-import path (plane-isolation invariant),
    # matching the otel-store imports elsewhere in this router.
    from xorcise.core.runcontrol.store import ARTIFACT_KINDS, SqliteSubmissionStore

    subs = SqliteSubmissionStore().list_for_run(run_id)
    return [
        RunArtifactView(name=s.name, kind=s.kind, seq=s.seq, payload=s.payload)
        for s in subs
        if s.kind in ARTIFACT_KINDS
    ]


@router.get("/{run_id}/result", response_model=RunResultView)
def run_result(run_id: str, background: BackgroundTasks) -> RunResultView | JSONResponse:
    """Recorded 50/50 explainable result + disclosed conditions for a run.

    When no result is recorded yet, distinguish three cases instead of a blanket 404:
    grading runs asynchronously after /complete, so a terminal-but-ungraded run is a normal
    transient state, NOT a failure. Unknown run → 404; terminal-but-ungraded → 202
    {"status": "grading"}; still-active run → 409 (no result to read yet).

    A terminal-ungraded run also RE-DRIVES grading here (ensure_graded_async): if the grade was
    lost to a server restart or a hung judge call, polling the result heals it rather than spinning
    at "grading" forever (de-duplicated, so the poll loop triggers one grade, not one per poll).
    """
    grade = reporting.get_result(run_id)
    if grade is None:
        if runs.get(run_id) is None:
            raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
        if runs.terminal_state(run_id)[0]:
            from xorcise.core.rest.run_terminate import ensure_graded_async

            ensure_graded_async(run_id, background.add_task)
            return JSONResponse(status_code=202, content={"run_id": run_id, "status": "grading"})
        raise HTTPException(
            status_code=409, detail=f"run '{run_id}' is not terminal yet — no result"
        )
    # Disclosure provenance is a cross-module join the delivery layer owns: reporting supplies the
    # disclosed conditions, run-control supplies the disclosed-intel count (lazy import keeps that
    # module off the module-import path, matching run_artifacts).
    from xorcise.core.runcontrol.store import disclosed_intel_count

    base = reporting.result_conditions(run_id) or ResultConditions()
    conditions = base.model_copy(update={"intel_disclosed": disclosed_intel_count(run_id)})
    partial, partial_trigger = reporting.result_partial(run_id)
    return RunResultView(
        grade=grade, conditions=conditions, partial=partial, partial_trigger=partial_trigger
    )


@router.get("/{run_id}/report")
def run_report(run_id: str, background: BackgroundTasks, format: str = "md") -> Response:
    """The run's full result as a downloadable Markdown or HTML document (run report).

    The Results page is the interactive view; this is the shareable one — the same metadata,
    scores, check table, rubric, evidence, artifacts, telemetry and conditions, rendered offline
    into a single self-contained file (the HTML carries its own dark styling; no external asset).

    Mirrors /result's state ladder exactly, so a caller polling for a report sees the same
    transitions it already handles: unknown run → 404; terminal-but-ungraded → 202
    {"status": "grading"}; still-active run → 409. An unsupported `format` → 422.
    Content-Disposition is `attachment`, so a browser downloads rather than renders it.
    """
    if format not in ("md", "html"):
        raise HTTPException(
            status_code=422, detail=f"unsupported report format '{format}' — use 'md' or 'html'"
        )
    from xorcise.core.rest.report_assembly import assemble_report

    ctx = assemble_report(run_id)
    if ctx is None:
        if runs.get(run_id) is None:
            raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
        if runs.terminal_state(run_id)[0]:
            from xorcise.core.rest.run_terminate import ensure_graded_async

            ensure_graded_async(run_id, background.add_task)
            return JSONResponse(status_code=202, content={"run_id": run_id, "status": "grading"})
        raise HTTPException(
            status_code=409, detail=f"run '{run_id}' is not terminal yet — no report"
        )
    if format == "html":
        body, media_type = reporting.render_html(ctx), "text/html; charset=utf-8"
    else:
        body, media_type = reporting.render_markdown(ctx), "text/markdown; charset=utf-8"
    filename = reporting.report_filename(ctx, format)
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{run_id}/stats", response_model=RunStats)
def run_stats(run_id: str, background: BackgroundTasks) -> RunStats | JSONResponse:
    """Per-run telemetry snapshot (tokens / counts / timing) for the run report.

    Prefers the snapshot recorded at grade time; for a run graded before the snapshot column
    existed (empty stats_json) it folds the event projection LIVE as a read-only fallback (no
    back-write). Mirrors /result's states otherwise: unknown run → 404; terminal-but-ungraded →
    202 {"status": "grading"}; still-active run → 409.
    """
    stored = reporting.get_stats(run_id)
    if stored is not None:
        return stored
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"no run '{run_id}'")
    if reporting.get_result(run_id) is None:
        if runs.terminal_state(run_id)[0]:
            from xorcise.core.rest.run_terminate import ensure_graded_async

            ensure_graded_async(run_id, background.add_task)
            return JSONResponse(status_code=202, content={"run_id": run_id, "status": "grading"})
        raise HTTPException(
            status_code=409, detail=f"run '{run_id}' is not terminal yet — no stats"
        )
    # A graded run with no stored snapshot (pre-migration): fold live, read-only. Lazy imports keep
    # the otel display plane off this module's import path (plane-isolation invariant).
    from xorcise.core.otel.run_stats import fold_run_stats
    from xorcise.core.rest import events_view

    view = events_view._full_view(run_id)
    return fold_run_stats(view.events, created_at=run.created_at, completed_at=run.completed_at)
