from __future__ import annotations

import pytest

from xorcise.core.contracts.telemetry import ObservedFact
from xorcise.core.runs.observed import (
    InMemoryObservedFactsStore,
    SqliteObservedFactsStore,
)


@pytest.fixture(params=["mem", "sqlite"])
def store(request):
    if request.param == "mem":
        return InMemoryObservedFactsStore()
    request.getfixturevalue("migrated_home")  # apply migrations to a temp home DB
    return SqliteObservedFactsStore()


@pytest.mark.adapters
def test_record_and_list_is_run_scoped(store):
    store.record(
        ObservedFact(run_id="r1", kind="acl-config", name="entry-cidrs", value="10.0.0.0/24")
    )
    store.record(ObservedFact(run_id="r1", kind="network-lifecycle", name="join", value="created"))
    store.record(
        ObservedFact(run_id="r2", kind="acl-config", name="entry-cidrs", value="10.9.0.0/24")
    )

    r1 = store.list_for_run("r1")
    assert {f.name for f in r1} == {"entry-cidrs", "join"}
    assert all(f.run_id == "r1" for f in r1)
    assert len(store.list_for_run("r2")) == 1
    assert store.list_for_run("absent") == ()


@pytest.mark.adapters
def test_list_preserves_insertion_order(store):
    for i in range(3):
        store.record(ObservedFact(run_id="r", kind="network-lifecycle", name=f"n{i}", value=str(i)))
    assert [f.name for f in store.list_for_run("r")] == ["n0", "n1", "n2"]
