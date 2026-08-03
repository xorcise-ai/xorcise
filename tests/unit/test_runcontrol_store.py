import pytest

from xorcise.core.runcontrol.store import (
    InMemorySubmissionStore,
    SqliteSubmissionStore,
    disclosed_intel_count,
)

pytestmark = pytest.mark.unit


def test_record_and_list_in_order() -> None:
    store = InMemorySubmissionStore()
    store.record("r1", "artifact", "a.py", "code")
    store.record("r1", "flag", "", "FLAG{x}")
    subs = store.list_for_run("r1")
    assert [(s.kind, s.name) for s in subs] == [("artifact", "a.py"), ("flag", "")]


def test_count_by_kind_is_run_scoped() -> None:
    store = InMemorySubmissionStore()
    store.record("r1", "intel", "i1", "look here")
    store.record("r1", "intel", "i2", "and here")
    store.record("r2", "intel", "i1", "other run")
    assert store.count("r1", "intel") == 2
    assert store.count("r2", "intel") == 1
    assert store.count("r1", "flag") == 0


def test_disclosed_intel_count_counts_only_intel_submissions(migrated_home) -> None:
    # Disclosure provenance: counts kind="intel" rows for the run (what get_intel records), ignoring
    # artifact/flag/complete markers and other runs. Zero for a run with no disclosed intel.
    store = SqliteSubmissionStore()
    store.record("r1", "intel", "i1", "look here")
    store.record("r1", "intel", "i2", "and here")
    store.record("r1", "artifact", "a.py", "code")
    store.record("r2", "intel", "i1", "other run")
    assert disclosed_intel_count("r1") == 2
    assert disclosed_intel_count("r2") == 1
    assert disclosed_intel_count("r3") == 0
