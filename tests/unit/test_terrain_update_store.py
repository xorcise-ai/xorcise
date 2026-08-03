from __future__ import annotations

import pytest

from xorcise.core.runs.terrain_update_store import (
    InMemoryTerrainUpdateStore,
    SqliteTerrainUpdateStore,
    TerrainUpdateStore,
    _UpdateInput,
)


@pytest.fixture(params=["memory", "sqlite"])
def store(request, migrated_home) -> TerrainUpdateStore:  # migrated_home: migrated-DB fixture
    return InMemoryTerrainUpdateStore() if request.param == "memory" else SqliteTerrainUpdateStore()


def test_record_many_then_list_returns_only_real_updates_seq_ordered(store: TerrainUpdateStore):
    store.record_many(
        "r1",
        [
            _UpdateInput(
                event_id="e1", target_kind="node", target_id="host:web", state="discovered"
            ),
            _UpdateInput(
                event_id="e2", target_kind="group", target_id="grp:segment", discovered=True
            ),
            _UpdateInput(event_id="e3", target_kind="edge", target_id="edge:1", active=True),
        ],
    )
    store.record_considered("r1", ["e4"])  # a "none" marker — must NOT reach list_for_run
    got = store.list_for_run("r1")
    assert [u.target_id for u in got] == ["host:web", "grp:segment", "edge:1"]
    assert [u.seq for u in got] == sorted(u.seq for u in got)  # seq-ordered
    assert all(u.target_kind in ("node", "group", "edge") for u in got)


def test_list_for_run_is_run_scoped(store: TerrainUpdateStore):
    store.record_many(
        "r1",
        [_UpdateInput(event_id="e1", target_kind="node", target_id="host:web", state="discovered")],
    )
    store.record_many(
        "r2",
        [_UpdateInput(event_id="e9", target_kind="node", target_id="host:db", state="discovered")],
    )
    assert {u.target_id for u in store.list_for_run("r1")} == {"host:web"}
    assert {u.target_id for u in store.list_for_run("r2")} == {"host:db"}


def test_attributed_event_ids_includes_considered_markers(store: TerrainUpdateStore):
    store.record_many(
        "r1",
        [_UpdateInput(event_id="e1", target_kind="node", target_id="host:web", state="discovered")],
    )
    store.record_considered("r1", ["e2", "e3"])
    assert store.attributed_event_ids("r1") == {"e1", "e2", "e3"}


def test_record_considered_skips_events_that_already_have_a_row(store: TerrainUpdateStore):
    store.record_many(
        "r1",
        [_UpdateInput(event_id="e1", target_kind="node", target_id="host:web", state="discovered")],
    )
    store.record_considered("r1", ["e1", "e2"])  # e1 already attributed — no marker for it
    assert store.attributed_event_ids("r1") == {"e1", "e2"}
    got = store.list_for_run("r1")
    assert len(got) == 1  # e1's real row is not duplicated by a marker


def test_record_many_first_write_wins_on_duplicate_key(store: TerrainUpdateStore):
    store.record_many(
        "r1",
        [_UpdateInput(event_id="e1", target_kind="node", target_id="host:web", state="discovered")],
    )
    store.record_many(
        "r1",
        [_UpdateInput(event_id="e1", target_kind="node", target_id="host:web", state="completed")],
    )
    got = store.list_for_run("r1")
    assert len(got) == 1 and got[0].state == "discovered"  # first write wins, no dupe


def test_record_many_allows_multiple_targets_for_same_event(store: TerrainUpdateStore):
    # UniqueConstraint is (run_id, event_id, target_kind, target_id) — one event can fan out to
    # more than one terrain update (e.g. a node AND its edge) without colliding.
    store.record_many(
        "r1",
        [
            _UpdateInput(
                event_id="e1", target_kind="node", target_id="host:web", state="discovered"
            ),
            _UpdateInput(event_id="e1", target_kind="edge", target_id="edge:1", active=True),
        ],
    )
    got = store.list_for_run("r1")
    assert {u.target_kind for u in got} == {"node", "edge"}


def test_record_many_null_event_id_for_deterministic_infra_updates(store: TerrainUpdateStore):
    store.record_many(
        "r1",
        [_UpdateInput(event_id=None, target_kind="node", target_id="host:web", state="discovered")],
    )
    got = store.list_for_run("r1")
    assert len(got) == 1 and got[0].event_id is None
    assert store.attributed_event_ids("r1") == set()  # no event_id -> not an attributed event


def test_record_many_round_trips_note(store: TerrainUpdateStore):
    store.record_many(
        "r1",
        [
            _UpdateInput(
                event_id="e1",
                target_kind="node",
                target_id="host:web",
                state="discovered",
                note="did the pivot",
            ),
            _UpdateInput(event_id="e2", target_kind="edge", target_id="edge:1", active=True),
        ],
    )
    got = {u.target_id: u for u in store.list_for_run("r1")}
    assert got["host:web"].note == "did the pivot"
    assert got["edge:1"].note is None
