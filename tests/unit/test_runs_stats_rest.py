"""GET /api/runs/{id}/stats — the per-run telemetry snapshot endpoint — and its offline twin,
the report's statistics block, which shares the same reader (report_assembly.current_run_stats).

Covers the four states: 404 unknown, 409 not-terminal, 202 terminal-but-ungraded, 200 stored
snapshot, the live-fold fallback for a result recorded without a stored snapshot — and the
re-fold of a snapshot that predates the run's current event projection (#129/#130).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from xorcise.core import reporting, runs
from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
from xorcise.core.contracts.reporting import CountStats, RunStats
from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.run_stats import projection_key
from xorcise.core.otel.store import SqliteTraceStore
from xorcise.core.rest.events_view import telemetry_summary
from xorcise.core.rest.report_assembly import assemble_report
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _now() -> datetime:
    return datetime(2026, 6, 25, 12, 0, tzinfo=UTC)


def test_stats_unknown_run_is_404(migrated_home) -> None:
    assert _client().get("/api/runs/ghost/stats").status_code == 404


def test_stats_active_run_is_409(migrated_home) -> None:
    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    assert _client().get(f"/api/runs/{r.run_id}/stats").status_code == 409


def test_stats_terminal_ungraded_is_202(migrated_home) -> None:
    from xorcise.core.rest.run_terminate import seal_terminal

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    seal_terminal(r.run_id, "done", _now())  # sealed, NOT graded
    resp = _client().get(f"/api/runs/{r.run_id}/stats")
    assert resp.status_code == 202
    assert resp.json()["status"] == "grading"


def test_stats_graded_returns_stored_snapshot(migrated_home) -> None:
    from xorcise.core.rest.run_terminate import terminate_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    terminate_run(r.run_id, "done", _now())  # grades + records + persists the snapshot
    resp = _client().get(f"/api/runs/{r.run_id}/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "tokens" in body and "total" in body["tokens"]
    assert "counts" in body and "timing" in body


def test_stats_live_fold_fallback_for_snapshotless_result(migrated_home) -> None:
    """A result recorded before the snapshot existed (empty stats_json) still serves stats via a
    read-only live fold, not a 500."""
    from xorcise.core.rest.run_terminate import seal_terminal

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600)
    seal_terminal(r.run_id, "done", _now())
    # Record a result directly WITHOUT a stats snapshot (simulates a pre-migration graded run).
    reporting.record_result(
        r.run_id,
        "a1",
        GradeResult(
            run_id=r.run_id, overall=0.5, breakdown=ScoreBreakdown(deterministic=0.5, judge=0.5)
        ),
    )
    assert reporting.get_stats(r.run_id) is None  # no stored snapshot
    resp = _client().get(f"/api/runs/{r.run_id}/stats")
    assert resp.status_code == 200  # live-fold fallback
    assert "tokens" in resp.json()


# ── A snapshot frozen under an older renderer ───────────────────────────────────────────────────


def _marker_batch(names: list[str]) -> dict[str, object]:
    """Marker-only spans — names no generic rule matches, no content-bearing attribute — the shape
    from #119/#120. GenericOtelAdapter v1 called every one of these a `tool_call`; v2 says
    `unclassified`. A run graded under v1 therefore has a snapshot that disagrees with what the
    (version-invalidated, rebuilt) projection says today."""
    return {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "scope": {"name": "t"},
                        "spans": [
                            {
                                "spanId": f"{i:016x}",
                                "name": n,
                                "startTimeUnixNano": str(1_700_000_000_000_000_000 + i),
                                "attributes": [{"key": "event.class", "value": {"stringValue": n}}],
                            }
                            for i, n in enumerate(names)
                        ],
                    }
                ]
            }
        ]
    }


def _graded_run_with_markers(names: list[str]) -> str:
    """A terminated + graded run whose RAW is `names` as marker-only spans, generic renderer."""
    from xorcise.core.rest.run_terminate import terminate_run

    r = runs.create_run(agent_id="a1", mission="c", budget_seconds=600, source_agent="generic")
    SqliteTraceStore().append(
        TraceRecord(run_id=r.run_id, seq=0, payload=json.dumps(_marker_batch(names)))
    )
    terminate_run(r.run_id, "done", _now())
    return r.run_id


def _freeze_as_v1_fold(run_id: str, n: int) -> None:
    """Overwrite the stored snapshot with what the v1 classifier persisted for these spans: every
    unmatched span a tool_call, no `unclassified` key, and the older projection stamp."""
    assert reporting.put_stats(
        run_id,
        RunStats(
            counts=CountStats(tool_calls=n, events_total=n, by_kind={"tool_call": n}),
            projection="generic@1+normalizer.2",
        ),
    )


_MARKERS = ["agent.ActionEvent", "agent.ObservationEvent", "agent.SystemPromptEvent"]


def test_stats_stale_snapshot_is_refolded_under_the_current_renderer_and_persisted(
    migrated_home,
) -> None:
    """The #129/#130 contradiction, pinned. A snapshot frozen under the v1 classifier reads
    "Tool calls 3 / Unclassified 0" while the live projection (v2) says all 3 are unclassified.
    /stats must serve the re-fold — and write it back, so the fold runs once per renderer change,
    not once per read."""
    run_id = _graded_run_with_markers(_MARKERS)
    _freeze_as_v1_fold(run_id, len(_MARKERS))
    stale = reporting.get_stats(run_id)
    assert stale is not None and stale.counts.tool_calls == 3  # the stale reading is in place

    body = _client().get(f"/api/runs/{run_id}/stats").json()
    assert body["counts"]["tool_calls"] == 0
    assert body["counts"]["by_kind"]["unclassified"] == 3
    summary = telemetry_summary(run_id)
    assert body["projection"] == projection_key(summary.adapter_name, summary.adapter_version)
    # Persisted: the next reader gets the refreshed snapshot without another fold.
    refreshed = reporting.get_stats(run_id)
    assert refreshed is not None
    assert refreshed.counts.by_kind.get("unclassified") == 3 and refreshed.counts.tool_calls == 0
    assert refreshed.projection == body["projection"]


def test_stats_fresh_snapshot_is_served_as_recorded(migrated_home) -> None:
    """A snapshot stamped with the run's CURRENT projection is the record: it is not re-folded on
    every read. (Doctored counts survive the round trip — proof no fold happened.)"""
    run_id = _graded_run_with_markers(_MARKERS[:1])
    current = reporting.get_stats(run_id)
    assert current is not None and current.projection is not None
    doctored = current.model_copy(
        update={"counts": current.counts.model_copy(update={"findings": 42})}
    )
    assert reporting.put_stats(run_id, doctored)
    assert _client().get(f"/api/runs/{run_id}/stats").json()["counts"]["findings"] == 42


def test_report_stats_and_telemetry_warnings_agree_after_a_renderer_change(
    migrated_home,
) -> None:
    """The report is /stats' offline twin, and its statistics rows sit in the same table as the
    telemetry warnings. Both now come from the same projection: the Unclassified row equals the
    warning's count, and Tool calls no longer counts spans the current classifier could not
    place — no more "Tool calls 2 / Unclassified 0" above "2 span(s) matched no rule"."""
    run_id = _graded_run_with_markers(_MARKERS[:2])
    _freeze_as_v1_fold(run_id, 2)

    ctx = assemble_report(run_id)
    assert ctx is not None and ctx.stats is not None and ctx.telemetry is not None
    warning = next(w for w in ctx.telemetry.warnings if w.code == "unclassified_spans")
    assert ctx.stats.counts.by_kind.get("unclassified") == warning.count == 2
    assert ctx.stats.counts.tool_calls == 0
    assert ctx.stats.projection == projection_key(
        ctx.telemetry.adapter_name, ctx.telemetry.adapter_version
    )
