from __future__ import annotations

from datetime import datetime

import pytest

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.store import SqliteLogStore, SqliteTraceStore

pytestmark = pytest.mark.unit


def test_receipt_times_maps_seq_to_server_ingest_time(migrated_home):
    store = SqliteTraceStore()
    store.append(TraceRecord(run_id="r1", seq=0, payload='{"resourceSpans":[]}'))
    store.append(TraceRecord(run_id="r1", seq=1, payload='{"resourceSpans":[]}'))
    store.append(TraceRecord(run_id="other", seq=0, payload="{}"))

    times = store.receipt_times("r1")
    assert set(times) == {0, 1}  # scoped to the run
    assert all(isinstance(t, datetime) for t in times.values())
    assert all(t.tzinfo is not None for t in times.values())
    # ingest order is non-decreasing in the record seq
    assert times[0] <= times[1]


def test_receipt_times_empty_for_unknown_run(migrated_home):
    assert SqliteTraceStore().receipt_times("nope") == {}


def test_trace_records_carry_their_receipt_time_on_read(migrated_home):
    store = SqliteTraceStore()
    store.append(TraceRecord(run_id="r1", seq=0, payload="{}"))

    [record] = store.read("r1")

    assert isinstance(record.received_at, datetime)
    assert record.received_at == store.receipt_times("r1")[0]


def test_log_store_exposes_the_same_receipt_clock(migrated_home):
    store = SqliteLogStore()
    store.append(TraceRecord(run_id="r1", seq=0, payload='{"resourceLogs":[]}'))

    [record] = store.read("r1")

    assert isinstance(record.received_at, datetime)
    assert record.received_at == store.receipt_times("r1")[0]
    assert store.receipt_times("other") == {}
