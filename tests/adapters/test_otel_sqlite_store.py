from __future__ import annotations

import pytest

from tests.adapters._contracts import TraceStoreContract
from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.ports import TraceStore
from xorcise.core.otel.store import SqliteTraceStore


class TestSqliteTraceStore(TraceStoreContract):
    @pytest.fixture
    def store(self, migrated_home) -> TraceStore:
        return SqliteTraceStore()


@pytest.mark.adapters
def test_sqlite_store_preserves_payload_and_order(migrated_home) -> None:
    store = SqliteTraceStore()
    store.append(TraceRecord(run_id="r1", seq=0, payload='{"resourceSpans":[1]}'))
    store.append(TraceRecord(run_id="r1", seq=1, payload='{"resourceSpans":[2]}'))
    store.append(TraceRecord(run_id="r2", seq=0, payload='{"resourceSpans":[9]}'))
    assert [r.payload for r in store.read("r1")] == [
        '{"resourceSpans":[1]}',
        '{"resourceSpans":[2]}',
    ]
    assert [r.seq for r in store.read("r1")] == [0, 1]
    assert [r.payload for r in store.read("r2")] == ['{"resourceSpans":[9]}']
    assert store.read("absent") == []


@pytest.mark.adapters
def test_sqlite_read_since_filters_by_seq(migrated_home) -> None:
    store = SqliteTraceStore()
    for seq in range(4):
        store.append(TraceRecord(run_id="r", seq=seq, payload=f'{{"i":{seq}}}'))
    assert [r.seq for r in store.read_since("r", after_seq=1)] == [2, 3]
