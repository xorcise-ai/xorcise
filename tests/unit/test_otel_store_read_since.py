import pytest

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.store import InMemoryTraceStore

pytestmark = pytest.mark.unit


def _seed(store: InMemoryTraceStore, run_id: str, n: int) -> None:
    for seq in range(n):
        store.append(TraceRecord(run_id=run_id, seq=seq, payload=f'{{"i":{seq}}}'))


def test_read_since_returns_only_newer_records_in_seq_order() -> None:
    store = InMemoryTraceStore()
    _seed(store, "run-1", 5)  # seq 0..4
    out = store.read_since("run-1", after_seq=2)
    assert [r.seq for r in out] == [3, 4]


def test_read_since_zero_returns_all() -> None:
    store = InMemoryTraceStore()
    _seed(store, "run-1", 3)
    assert [r.seq for r in store.read_since("run-1", after_seq=0)] == [1, 2]  # seq 0 is NOT > 0
    # records start at seq 0, so "everything" uses after_seq=-1
    assert [r.seq for r in store.read_since("run-1", after_seq=-1)] == [0, 1, 2]


def test_read_since_unknown_run_is_empty() -> None:
    assert InMemoryTraceStore().read_since("ghost", after_seq=-1) == []
