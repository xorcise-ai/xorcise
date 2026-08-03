import pytest

from xorcise.core.runcontrol.store import SqliteSubmissionStore

pytestmark = pytest.mark.adapters


def test_sqlite_record_persists_across_fresh_store(migrated_home) -> None:
    SqliteSubmissionStore().record("r1", "flag", "", "FLAG{x}")
    rows = SqliteSubmissionStore().list_for_run("r1")
    assert len(rows) == 1 and rows[0].payload == "FLAG{x}"


def test_sqlite_count_is_run_scoped(migrated_home) -> None:
    store = SqliteSubmissionStore()
    store.record("r1", "intel", "i1", "x")
    store.record("r2", "intel", "i1", "y")
    assert store.count("r1", "intel") == 1


def test_sqlite_list_exposes_created_at(migrated_home) -> None:
    # created_at is the XORCISE-core-clock anchor the unified terrain timeline reads to place each
    # run-control interaction (artifact/intel/complete) at its own moment (per-path anchoring).
    SqliteSubmissionStore().record("r1", "artifact", "a", "x")
    row = SqliteSubmissionStore().list_for_run("r1")[0]
    # SQLite returns tz-naive datetimes (terrain_timeline._utc normalizes to UTC); just assert the
    # anchor is present.
    assert row.created_at is not None
