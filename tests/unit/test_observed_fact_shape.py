from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.telemetry import ObservedFact


@pytest.mark.unit
def test_observed_fact_is_frozen_and_strict():
    f = ObservedFact(run_id="r1", kind="network-lifecycle", name="entry-cidrs", value="10.0.0.0/24")
    assert (f.run_id, f.kind, f.name, f.value) == (
        "r1",
        "network-lifecycle",
        "entry-cidrs",
        "10.0.0.0/24",
    )
    with pytest.raises(ValidationError):  # frozen
        f.value = "x"
    with pytest.raises(ValidationError):  # extra forbidden
        ObservedFact.model_validate(
            {"run_id": "r1", "kind": "k", "name": "n", "value": "v", "extra": "no"}
        )


@pytest.mark.unit
def test_created_at_defaults_none_and_round_trips_from_the_sqlite_store(migrated_home):
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    # constructed facts leave created_at None
    assert ObservedFact(run_id="r1", kind="k", name="n", value="v").created_at is None

    store = SqliteObservedFactsStore()
    store.record(
        ObservedFact(run_id="r1", kind="network-lifecycle", name="join", value="confirmed")
    )
    (fact,) = store.list_for_run("r1")
    # the store stamps + surfaces the server-side record time (SQLite returns it tz-naive; the
    # timeline sort normalizes naive-vs-aware — see runs/terrain_timeline.py)
    assert isinstance(fact.created_at, datetime)
