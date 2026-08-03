from typing import Any

import pytest
from fastapi.testclient import TestClient

from xorcise.core import runs
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


@pytest.fixture()
def client(migrated_home):
    return TestClient(build_rest_app())


def test_terrain2_unknown_run_404(client):
    assert client.get("/api/runs/nope/terrain2").status_code == 404


def test_terrain2_returns_v2_shape_with_infra_scaffold(client):
    runs.create_run(
        run_id="r1",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    body = client.get("/api/runs/r1/terrain2").json()
    assert body["run_id"] == "r1"
    assert {"groups", "nodes", "edges", "updates"} <= body.keys()
    gids = {g["id"] for g in body["groups"]}
    assert {"agent", "xorcise"} <= gids
    assert body["updates"] == []  # no attribution yet (Phase 2)


def _updates_by_target(body: dict[str, Any], target_id: str) -> list[dict[str, Any]]:
    return [u for u in body["updates"] if u["target_id"] == target_id]


def test_terrain2_activates_collector_once_a_trace_is_stored(client):
    import json

    from xorcise.core.contracts.telemetry import TraceRecord
    from xorcise.core.otel.store import SqliteTraceStore

    runs.create_run(
        run_id="r-tel",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    SqliteTraceStore().append(
        TraceRecord(run_id="r-tel", seq=0, payload=json.dumps({"resourceSpans": []}))
    )

    body = client.get("/api/runs/r-tel/terrain2").json()
    collector = _updates_by_target(body, "collector")
    assert collector and collector[0]["target_kind"] == "node"
    assert collector[0]["state"] == "discovered"
    collector_edge = _updates_by_target(body, "m:agent-collector")
    assert collector_edge and collector_edge[0]["target_kind"] == "edge"
    assert collector_edge[0]["active"] is True


def test_terrain2_activates_hs_join_only_when_confirmed(client):
    from xorcise.core.contracts.telemetry import ObservedFact
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    runs.create_run(
        run_id="r-join-created",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    SqliteObservedFactsStore().record(
        ObservedFact(
            run_id="r-join-created", kind="network-lifecycle", name="join", value="created"
        )
    )
    created_only = client.get("/api/runs/r-join-created/terrain2").json()
    assert _updates_by_target(created_only, "hs:join") == []
    assert _updates_by_target(created_only, "m:agent-hs") == []

    runs.create_run(
        run_id="r-join-confirmed",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    SqliteObservedFactsStore().record(
        ObservedFact(
            run_id="r-join-confirmed", kind="network-lifecycle", name="join", value="confirmed"
        )
    )
    confirmed = client.get("/api/runs/r-join-confirmed/terrain2").json()
    hs_join = _updates_by_target(confirmed, "hs:join")
    assert hs_join and hs_join[0]["target_kind"] == "node" and hs_join[0]["state"] == "discovered"
    agent_hs = _updates_by_target(confirmed, "m:agent-hs")
    assert agent_hs and agent_hs[0]["target_kind"] == "edge" and agent_hs[0]["active"] is True
    # the wire carries each update's server-clock receipt anchor (the join fact's created_at), so
    # the frontend can interleave this infra activity into the Trace + time-travel to it
    assert hs_join[0]["ts"] is not None


def test_terrain2_survives_naive_join_fact_with_aware_telemetry_receipt(client):
    """Regression (run 213836cf… 500): the join fact's created_at comes back tz-naive from SQLite
    while the first-span trace-ingest receipt is UTC-aware — with both signals present the agent
    anchor min() must normalize the mix, not TypeError into an HTTP 500."""
    import json

    from xorcise.core.contracts.telemetry import ObservedFact, TraceRecord
    from xorcise.core.otel.store import SqliteTraceStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    runs.create_run(
        run_id="r-mixed-tz",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    SqliteObservedFactsStore().record(
        ObservedFact(run_id="r-mixed-tz", kind="network-lifecycle", name="join", value="confirmed")
    )
    SqliteTraceStore().append(
        TraceRecord(run_id="r-mixed-tz", seq=0, payload=json.dumps({"resourceSpans": []}))
    )

    resp = client.get("/api/runs/r-mixed-tz/terrain2")
    assert resp.status_code == 200
    body = resp.json()
    agent = _updates_by_target(body, "agent")
    assert agent and agent[0]["ts"] is not None  # anchored to the earliest connection signal
    assert _updates_by_target(body, "hs:join") and _updates_by_target(body, "collector")


def test_terrain2_kicks_infra_reconciler_for_a_live_run_only(client, monkeypatch):
    from datetime import UTC, datetime

    import xorcise.core.rest.join_reconcile as jr

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(jr, "maybe_reconcile_join", lambda *a, **k: calls.append(a))

    # live run → kicked (the reconciler self-gates internally on join+telemetry confirmed state)
    runs.create_run(
        run_id="r-recon-live",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    client.get("/api/runs/r-recon-live/terrain2")
    assert calls == [("r-recon-live",)]

    # terminal run → NOT kicked (nothing to reconcile once the run is over)
    calls.clear()
    runs.create_run(
        run_id="r-recon-terminal",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    runs.mark_terminal("r-recon-terminal", "done", datetime.now(UTC))
    client.get("/api/runs/r-recon-terminal/terrain2")
    assert calls == []

    # a live run that already join-confirmed is STILL kicked — the reconciler also latches the
    # telemetry connection and self-gates (no-ops) once BOTH facts exist; the endpoint just kicks
    # for any live run rather than pre-judging which connection still needs reconciling.
    calls.clear()
    from xorcise.core.contracts.telemetry import ObservedFact
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    runs.create_run(
        run_id="r-recon-confirmed",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    SqliteObservedFactsStore().record(
        ObservedFact(
            run_id="r-recon-confirmed", kind="network-lifecycle", name="join", value="confirmed"
        )
    )
    client.get("/api/runs/r-recon-confirmed/terrain2")
    assert calls == [("r-recon-confirmed",)]
