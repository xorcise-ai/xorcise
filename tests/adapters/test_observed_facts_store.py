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


@pytest.mark.adapters
def test_sqlite_refuses_a_fact_for_a_sealed_run(migrated_home):
    """Observed facts are inside the evidence digest, so a fact written after the seal turned an
    untouched run's verification into `mismatch` (#194). The store refuses it, and says so."""
    from xorcise.core.otel.store import SqliteSealStore

    store = SqliteObservedFactsStore()
    assert store.record(ObservedFact(run_id="r", kind="target", name="web", value="10.0.0.5"))
    SqliteSealStore().seal("r")

    late = ObservedFact(run_id="r", kind="network-lifecycle", name="join", value="confirmed")
    assert store.record(late) is False
    assert [f.name for f in store.list_for_run("r")] == ["web"]
    # Another run is not affected by this one's seal.
    assert store.record(late.model_copy(update={"run_id": "other"}))
