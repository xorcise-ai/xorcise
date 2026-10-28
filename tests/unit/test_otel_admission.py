"""Admitting a telemetry batch against sealing its run (#147, #184).

The receiver used to check the seal, count the run's batches and append, as three steps with
nothing holding them together. Concurrent batches could share a seq, and a batch that passed the
check could land while the run was being hashed, so an untouched run reported MISMATCH. These
tests drive real threads against a real database file, because the bugs were interleavings.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from xorcise.core.otel.decode import RUN_ID_ATTR
from xorcise.core.otel.ingest.embedded import create_otel_app
from xorcise.core.otel.store import (
    InMemorySealStore,
    InMemoryTraceStore,
    SqliteLogStore,
    SqliteSealStore,
    SqliteTraceStore,
)

_SENDERS = 8


def _resource(run_id: str) -> dict[str, object]:
    return {"attributes": [{"key": RUN_ID_ATTR, "value": {"stringValue": run_id}}]}


def _span(run_id: str) -> str:
    return json.dumps(
        {"resourceSpans": [{"resource": _resource(run_id), "scopeSpans": [{"spans": [{}]}]}]}
    )


def _log(run_id: str) -> str:
    return json.dumps(
        {"resourceLogs": [{"resource": _resource(run_id), "scopeLogs": [{"logRecords": [{}]}]}]}
    )


def _hammer(
    run_id: str, stop: threading.Event, errors: list[BaseException]
) -> list[threading.Thread]:
    """Start senders that append batches for `run_id` until `stop` is set."""

    def send(sender: int) -> None:
        store, n = SqliteTraceStore(), 0
        while not stop.is_set():
            try:
                store.append_next(run_id, f'{{"sender":{sender},"n":{n}}}')
            except BaseException as exc:  # noqa: BLE001 — surfaced by the test, not swallowed
                errors.append(exc)
                return
            n += 1

    threads = [threading.Thread(target=send, args=(i,)) for i in range(_SENDERS)]
    for t in threads:
        t.start()
    return threads


def test_concurrent_appends_get_distinct_dense_seqs(migrated_home) -> None:
    errors: list[BaseException] = []

    def send(sender: int) -> None:
        store = SqliteTraceStore()
        try:
            for n in range(40):
                store.append_next("r1", f'{{"sender":{sender},"n":{n}}}')
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=send, args=(i,)) for i in range(_SENDERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert [r.seq for r in SqliteTraceStore().read("r1")] == list(range(_SENDERS * 40))


def test_a_sealed_run_admits_nothing_not_even_a_first_batch(migrated_home) -> None:
    """The trap in the obvious one-statement form: `SELECT MAX(seq)+1 ... WHERE NOT sealed`
    still yields one row, so a sealed run took a batch at seq 0."""
    SqliteSealStore().seal("never-exported")
    SqliteSealStore().seal("exported")
    assert SqliteTraceStore().read("exported") == []

    assert SqliteTraceStore().append_next("never-exported", "{}") is None
    assert SqliteLogStore().append_next("exported", "{}") is None
    assert SqliteTraceStore().read("never-exported") == []
    assert SqliteLogStore().read("exported") == []


def test_traces_and_logs_number_independently(migrated_home) -> None:
    assert SqliteTraceStore().append_next("r1", "t") == 0
    assert SqliteLogStore().append_next("r1", "l") == 0
    assert SqliteTraceStore().append_next("r1", "t") == 1


@pytest.mark.parametrize("attempt", range(5))
def test_a_run_sealed_under_load_still_verifies(migrated_home, attempt) -> None:
    """#184: with batches arriving the whole time, every one is either inside the digest or
    refused. The pre-fix receiver failed this on every attempt."""
    from xorcise.core.rest.evidence_seal import seal_with_digest, verify_evidence

    stop, errors = threading.Event(), list[BaseException]()
    senders = _hammer("r1", stop, errors)
    try:
        while len(SqliteTraceStore().read("r1")) < 20:
            time.sleep(0.005)
        seal_with_digest("r1")
        stored = len(SqliteTraceStore().read("r1"))
        time.sleep(0.1)  # keep sending after the seal: any batch admitted now is the bug
    finally:
        stop.set()
        for t in senders:
            t.join()

    assert errors == []
    assert len(SqliteTraceStore().read("r1")) == stored
    assert verify_evidence("r1") is True


def test_ingest_runs_off_the_event_loop() -> None:
    """REST and OTLP share one event loop in `serve`; a batch decoded and written inline held
    every REST request behind it."""
    seen: list[str] = []

    class Recording(InMemoryTraceStore):
        def append_next(self, run_id: str, payload: str) -> int:
            seen.append(threading.current_thread().name)
            return super().append_next(run_id, payload)

    client = TestClient(create_otel_app(Recording(), InMemorySealStore()))
    assert client.post("/v1/traces", content=_span("r1")).status_code == 200
    assert seen and seen[0].startswith("otlp-ingest")
    assert threading.current_thread().name not in seen


def test_a_busy_database_answers_503_so_the_exporter_retries() -> None:
    class Locked(InMemoryTraceStore):
        def append_next(self, run_id: str, payload: str) -> int:
            raise OperationalError("INSERT", {}, Exception("database is locked"))

    client = TestClient(create_otel_app(Locked(), InMemorySealStore(), log_store=Locked()))
    for path, body in (("/v1/traces", _span("r1")), ("/v1/logs", _log("r1"))):
        resp = client.post(path, content=body)
        assert resp.status_code == 503
        assert resp.headers["retry-after"] == "1"
        assert resp.json()["code"] == 14


def _two_runs_and_a_stray() -> str:
    """One export: a batch for a sealed run, one for an open run, and one with no run id."""
    doc = json.loads(_span("sealed"))
    doc["resourceSpans"] += json.loads(_span("open"))["resourceSpans"]
    doc["resourceSpans"].append({"resource": {"attributes": []}, "scopeSpans": [{"spans": [{}]}]})
    return json.dumps(doc)


def test_a_503_records_no_drops_so_the_retry_does_not_count_them_twice() -> None:
    from xorcise.core.otel.ingest.drops import DropRecorder

    class LockedForOpen(InMemoryTraceStore):
        def append_next(self, run_id: str, payload: str) -> int:
            raise OperationalError("INSERT", {}, Exception("database is locked"))

    seals, recorder = InMemorySealStore(), DropRecorder()
    seals.seal("sealed")
    client = TestClient(create_otel_app(LockedForOpen(), seals, drops=recorder))
    assert client.post("/v1/traces", content=_two_runs_and_a_stray()).status_code == 503
    assert set(recorder.counters.as_dict().values()) == {0}

    client = TestClient(create_otel_app(InMemoryTraceStore(), seals, drops=recorder))
    assert client.post("/v1/traces", content=_two_runs_and_a_stray()).status_code == 200
    counts = recorder.counters.as_dict()
    assert (counts["sealed_batches"], counts["unroutable_batches"]) == (1, 1)


def test_a_write_that_keeps_clashing_answers_503_not_500() -> None:
    """A 500 is not retried by OTLP exporters, so the batch would be lost."""
    from sqlalchemy.exc import IntegrityError

    class Clashing(InMemoryTraceStore):
        def append_next(self, run_id: str, payload: str) -> int:
            raise IntegrityError("INSERT", {}, Exception("UNIQUE constraint failed"))

    client = TestClient(create_otel_app(Clashing(), InMemorySealStore()))
    assert client.post("/v1/traces", content=_span("r1")).status_code == 503


def test_drop_counts_stay_exact_across_ingest_threads() -> None:
    """Up to INGEST_WORKERS threads record drops at once. The counter here yields between its read
    and its write, which is where an unguarded read-add-write loses increments."""
    from xorcise.core.otel.ingest.drops import DropCounters, DropRecorder

    class Yielding(DropCounters):
        def add(self, signal: str, reason: str, n: int) -> None:
            before = self.sealed_spans
            time.sleep(0.0001)
            self.sealed_spans = before + n

    recorder = DropRecorder(counters=Yielding())

    def record() -> None:
        for _ in range(50):
            recorder.record(signal="traces", reason="sealed", n=1, run_id="r", payload="{}")

    threads = [threading.Thread(target=record) for _ in range(_SENDERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert recorder.counters.sealed_spans == _SENDERS * 50
