from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from xorcise.core import runs
from xorcise.core.contracts.agent_event import (
    AgentEvent,
    AgentEventKind,
    RawTraceRef,
    RunEventsView,
)
from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
    TerrainSpec,
)
from xorcise.core.otel.store.agent_events import SqliteAgentEventStore
from xorcise.core.rest.terrain_catchup_v2 import run_terrain_catchup_v2
from xorcise.core.roles.boot.role_all import build_rest_app
from xorcise.core.runs.terrain_update_store import SqliteTerrainUpdateStore

pytestmark = pytest.mark.unit


class _FakeScorer:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    def score(self, messages: Sequence[tuple[str, str]]) -> str:
        return self.reply


class _CapturingScorer:
    """Like `_FakeScorer`, but records every (system, user) prompt pair it's called with, so a
    test can assert on what the model was actually shown."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def score(self, messages: Sequence[tuple[str, str]]) -> str:
        system, user = messages[0][1], messages[1][1]
        self.calls.append((system, user))
        return self.reply


def _view(run_id: str, *events: AgentEvent) -> RunEventsView:
    return RunEventsView(
        run_id=run_id,
        source_agent="a",
        adapter_name="x",
        adapter_version="1",
        fallback=False,
        next_since=0,
        events=tuple(events),
    )


def _ev(run_id: str, eid: str, body: str) -> AgentEvent:
    return AgentEvent(
        run_id=run_id,
        id=eid,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        source_agent="a",
        kind=AgentEventKind.terminal_command,
        title=body[:40],
        body=body,
        raw_ref=RawTraceRef(run_id=run_id, raw_seq=1, span_id="s"),
    )


def _manifest_with_authored_graph() -> MissionManifest:
    spec = TerrainSpec(
        summary="pivot mission",
        groups=({"id": "dmz", "description": "DMZ segment"},),
        nodes=(
            {
                "id": "web",
                "parent": "dmz",
                "type": "web_service",
                "objective": True,
                "discovery_condition": "reach the web host",
                "completion_condition": "recover the flag",
            },
        ),
        edges=({"id": "e1", "src": "web", "dst": "dmz"},),
    )
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="c1", name="c1", objective="o", type="lab"),
        environment=EnvironmentSpec(entry_networks=("dmz",)),
        terrain=spec,
    )


# --- run_terrain_catchup_v2 (DRAINED loop) ------------------------------------------------------


def test_catchup_v2_attributes_and_persists_node_group_edge_updates(migrated_home):
    run_id = "r1"
    SqliteAgentEventStore().put(
        run_id, _view(run_id, _ev(run_id, "e1", "curl http://10.0.0.10/login")), source_max_seq=1
    )
    scorer = _FakeScorer(
        '[{"event_id":"e1","is_an_action":true,"update":{'
        '"nodes":[{"id":"web","state":"discovered"}],'
        '"groups":[{"id":"dmz","discovered":true}],'
        '"edges":[{"id":"e1","active":true}]}}]'
    )
    run_terrain_catchup_v2(run_id, "c1", _manifest_with_authored_graph(), score=scorer, limit=8)

    stored = SqliteTerrainUpdateStore().list_for_run(run_id)
    by_kind = {u.target_kind: u for u in stored}
    assert by_kind["node"].target_id == "web" and by_kind["node"].state == "discovered"
    assert by_kind["group"].target_id == "dmz" and by_kind["group"].discovered is True
    assert by_kind["edge"].target_id == "e1" and by_kind["edge"].active is True
    assert all(u.event_id == "e1" for u in stored)


def test_catchup_v2_persists_the_verdict_note_on_the_update(migrated_home):
    run_id = "r-note"
    SqliteAgentEventStore().put(
        run_id, _view(run_id, _ev(run_id, "e1", "curl http://10.0.0.10/login")), source_max_seq=1
    )
    scorer = _FakeScorer(
        '[{"event_id":"e1","is_an_action":true,"note":"used the pivot to reach web",'
        '"update":{"nodes":[{"id":"web","state":"discovered"}]}}]'
    )
    run_terrain_catchup_v2(run_id, "c1", _manifest_with_authored_graph(), score=scorer, limit=8)

    stored = SqliteTerrainUpdateStore().list_for_run(run_id)
    assert len(stored) == 1
    assert stored[0].note == "used the pivot to reach web"


def test_catchup_v2_drains_the_whole_backlog_in_one_call(migrated_home):
    # Regression (mirrors v1): one call must drain the WHOLE backlog, not just one `limit`-sized
    # batch, so the tail of a run is never left stranded for a poll that may never come.
    run_id = "r-drain"
    evs = [_ev(run_id, f"e{i}", f"curl 10.0.0.{i}") for i in range(5)]
    SqliteAgentEventStore().put(run_id, _view(run_id, *evs), source_max_seq=5)
    run_terrain_catchup_v2(run_id, "c1", None, score=_FakeScorer("[]"), limit=1)
    assert SqliteTerrainUpdateStore().attributed_event_ids(run_id) == {f"e{i}" for i in range(5)}


def test_catchup_v2_no_op_span_is_cached_and_not_re_attributed(migrated_home):
    run_id = "r2"
    SqliteAgentEventStore().put(
        run_id, _view(run_id, _ev(run_id, "e1", "curl 10.0.0.10")), source_max_seq=1
    )
    scorer = _FakeScorer('[{"event_id":"e1","is_an_action":false}]')
    run_terrain_catchup_v2(run_id, "c1", None, score=scorer, limit=8)
    run_terrain_catchup_v2(run_id, "c1", None, score=scorer, limit=8)  # cached -> no re-send
    store = SqliteTerrainUpdateStore()
    assert store.attributed_event_ids(run_id) == {"e1"}
    assert (
        store.list_for_run(run_id) == []
    )  # no real update — the "none" marker never hits /terrain2


def test_catchup_v2_does_not_cache_on_unparseable_response(migrated_home):
    run_id = "r3"
    SqliteAgentEventStore().put(
        run_id, _view(run_id, _ev(run_id, "e1", "curl 10.0.0.10")), source_max_seq=1
    )
    run_terrain_catchup_v2(run_id, "c1", None, score=_FakeScorer("not json at all"), limit=8)
    # a hard parse failure must NOT be cached — the event stays pending for retry
    assert SqliteTerrainUpdateStore().attributed_event_ids(run_id) == set()


def test_catchup_v2_unknown_target_id_is_dropped_and_still_considered(migrated_home):
    run_id = "r4"
    SqliteAgentEventStore().put(
        run_id, _view(run_id, _ev(run_id, "e1", "curl 10.0.0.10")), source_max_seq=1
    )
    scorer = _FakeScorer(
        '[{"event_id":"e1","is_an_action":true,'
        '"update":{"nodes":[{"id":"made-up","state":"discovered"}]}}]'
    )
    run_terrain_catchup_v2(run_id, "c1", None, score=scorer, limit=8)
    store = SqliteTerrainUpdateStore()
    assert store.list_for_run(run_id) == []  # unknown id -> not created
    assert store.attributed_event_ids(run_id) == {"e1"}  # still cached as considered


def test_catchup_v2_prompt_and_guard_exclude_infra_ids(migrated_home):
    # Plane isolation: the mission-attribution prompt must never carry infra ids (the infra
    # scaffold — agent/hs/rc/collector, their endpoints, and infra edges — is a deterministic
    # plane written exclusively by `infra_updates`), and a verdict that targets one anyway must be
    # dropped by the update-only guard, exactly like any other unknown id.
    run_id = "r-infra-bleed"
    SqliteAgentEventStore().put(
        run_id, _view(run_id, _ev(run_id, "e1", "curl http://10.0.0.10/login")), source_max_seq=1
    )
    scorer = _CapturingScorer(
        '[{"event_id":"e1","is_an_action":true,"update":{'
        '"nodes":[{"id":"hs:join","state":"discovered"}]}}]'
    )
    run_terrain_catchup_v2(run_id, "c1", _manifest_with_authored_graph(), score=scorer, limit=8)

    assert len(scorer.calls) == 1
    system, user = scorer.calls[0]
    for infra_id in ("hs:join", "m:agent-hs", "collector"):
        assert infra_id not in system
        assert infra_id not in user

    store = SqliteTerrainUpdateStore()
    assert store.list_for_run(run_id) == []  # infra id -> not in mission known_ids -> dropped
    assert store.attributed_event_ids(run_id) == {"e1"}  # still cached as considered


# --- GET /runs/{id}/terrain2 (merged updates + attribution) -------------------------------------


@pytest.fixture()
def client(migrated_home):
    return TestClient(build_rest_app())


def _install_mission(home, slug: str) -> None:
    from xorcise.core.contracts.control import MissionRef
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission

    root = home / "missions" / slug
    root.mkdir(parents=True)
    manifest = _manifest_with_authored_graph()
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


def test_terrain2_serves_merged_infra_and_mission_updates(client, migrated_home):
    _install_mission(migrated_home, "c-merge")
    runs.create_run(
        run_id="r-merge",
        agent_id="a1",
        mission="c-merge",
        budget_seconds=60,
        source_agent="generic",
    )
    from xorcise.core.contracts.telemetry import ObservedFact
    from xorcise.core.runcontrol.store import SqliteSubmissionStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore
    from xorcise.core.runs.terrain_update_store import SqliteTerrainUpdateStore, _UpdateInput

    # `confirmed` (the REAL join signal from the join reconciler) — a setup-time `created` fact no
    # longer activates hs:join / m:agent-hs (Phase 3: infra plane keys off the real join).
    SqliteObservedFactsStore().record(
        ObservedFact(run_id="r-merge", kind="network-lifecycle", name="join", value="confirmed")
    )
    SqliteSubmissionStore().record("r-merge", "flag", "flag", "XORCISE{x}")
    SqliteTerrainUpdateStore().record_many(
        "r-merge",
        [_UpdateInput(event_id="e1", target_kind="node", target_id="web", state="discovered")],
    )

    body = client.get("/api/runs/r-merge/terrain2").json()
    kinds_targets = [(u["target_kind"], u["target_id"]) for u in body["updates"]]
    # Unified receipt-time ordering: the join-driven infra updates anchor to the join fact's
    # created_at (early), so they sort first; the flag submission lights rc:artifacts AND the
    # agent<->run-control edge, both anchored to end-of-run; the mission `web` update has no
    # agent-event span here (synthetic) so it also anchors to end — the end_ts group ties are broken
    # infra-first (rc:artifacts, m:agent-rc before web).
    assert kinds_targets == [
        ("node", "agent"),
        ("node", "hs:join"),
        ("edge", "m:agent-hs"),
        ("node", "rc:artifacts"),
        ("edge", "m:agent-rc"),
        ("node", "web"),
    ]


def test_terrain2_unknown_run_404(client):
    assert client.get("/api/runs/nope/terrain2").status_code == 404


def test_terrain2_reports_attribution_status(client, migrated_home):
    _install_mission(migrated_home, "c-attr2")
    runs.create_run(
        run_id="r-attr2",
        agent_id="a1",
        mission="c-attr2",
        budget_seconds=60,
        source_agent="generic",
    )
    SqliteAgentEventStore().put(
        "r-attr2",
        _view(
            "r-attr2",
            _ev("r-attr2", "e1", "curl web"),
            _ev("r-attr2", "e2", "curl web again"),
        ),
        source_max_seq=2,
    )
    from xorcise.core.runs.terrain_update_store import SqliteTerrainUpdateStore, _UpdateInput

    SqliteTerrainUpdateStore().record_many(
        "r-attr2",
        [_UpdateInput(event_id="e1", target_kind="node", target_id="web", state="discovered")],
    )
    SqliteTerrainUpdateStore().record_considered("r-attr2", ["e2"])  # considered, no-op

    attr = client.get("/api/runs/r-attr2/terrain2").json()["attribution"]
    assert attr["attributable"] == 2
    assert attr["attributed"] == 2
    assert set(attr["considered_event_ids"]) == {"e1", "e2"}
    assert attr["running"] is False  # no model configured in tests -> no live batch


def test_terrain2_does_not_rekick_catchup_when_fully_attributed(client, migrated_home, monkeypatch):
    # Regression (mirrors v1): an unconditional kick makes `attribution.running` ~always true.
    # A fully-attributed run has no pending work -> no kick -> running false.
    import xorcise.core.rest.terrain_catchup_v2 as tc2

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(tc2, "maybe_start_catchup_v2", lambda *a, **k: calls.append(a))
    runs.create_run(
        run_id="r-nokick",
        agent_id="a1",
        mission="not-installed",
        budget_seconds=60,
        source_agent="generic",
    )
    SqliteAgentEventStore().put(
        "r-nokick", _view("r-nokick", _ev("r-nokick", "e1", "curl web")), source_max_seq=1
    )
    from xorcise.core.runs.terrain_update_store import SqliteTerrainUpdateStore

    SqliteTerrainUpdateStore().record_considered("r-nokick", ["e1"])  # already fully attributed

    body = client.get("/api/runs/r-nokick/terrain2").json()
    assert calls == [], "a fully-attributed run must not re-kick v2 catch-up"
    assert body["attribution"]["running"] is False
