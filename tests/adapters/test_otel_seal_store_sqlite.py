import pytest

from xorcise.core.otel.store import SqliteSealStore

pytestmark = pytest.mark.adapters


def test_seal_persists_across_fresh_store(migrated_home) -> None:
    SqliteSealStore().seal("run-x")
    # a brand-new instance (simulating a process restart) still sees the seal
    assert SqliteSealStore().is_sealed("run-x") is True
    assert SqliteSealStore().sealed_at("run-x") is not None


def test_unsealed_run_not_sealed(migrated_home) -> None:
    assert SqliteSealStore().is_sealed("nope") is False


def test_seal_takes_the_admission_lock(migrated_home) -> None:
    """Sealing must update the run's admission row, as `append_next` does (#184).

    On SQLite the file's single write lock already orders the two, so no race test catches a seal
    that skips this lock. On an engine with row locks, this row is the only thing that does. The
    lock is taken by updating `batches`, so a seal that takes it leaves the row bumped."""
    from xorcise.core.db import session_scope
    from xorcise.core.otel.store import SqliteTraceStore
    from xorcise.core.otel.store.models import AdmissionRow

    def batches(run_id: str) -> int | None:
        with session_scope() as s:
            row = s.get(AdmissionRow, run_id)
            return row.batches if row is not None else None

    SqliteSealStore().seal("never-exported")
    assert batches("never-exported") == 1

    SqliteTraceStore().append_next("exported", "{}")
    SqliteSealStore().seal("exported")
    assert batches("exported") == 2
