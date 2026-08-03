import json

import pytest
from fastapi.testclient import TestClient

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.store import SqliteSealStore, SqliteTraceStore
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.integration


def test_two_readers_see_same_stream_and_a_stalled_reader_does_not_affect_the_other(
    migrated_home,
):
    store = SqliteTraceStore()
    for seq in range(3):
        store.append(TraceRecord(run_id="r", seq=seq, payload=json.dumps({"i": seq})))
    client = TestClient(build_rest_app())

    # reader A reads everything; reader B "stalls" (reads nothing). Ingest continues.
    a1 = client.get("/api/runs/r/traces", params={"since": -1}).json()["records"]
    assert [rec["seq"] for rec in a1] == [0, 1, 2]
    store.append(TraceRecord(run_id="r", seq=3, payload=json.dumps({"i": 3})))  # ingest continues

    # reader A's incremental cursor still works; the RAW record is intact and unaffected
    a2 = client.get("/api/runs/r/traces", params={"since": 2}).json()["records"]
    assert [rec["seq"] for rec in a2] == [3]
    assert len(store.read("r")) == 4  # RAW unmutated by reads


def test_done_seals_then_late_spans_are_rejected_and_record_frozen(migrated_home):
    from xorcise.core import agents, runs

    agent = agents.register("alice", endpoint="http://a")
    runs.create_run(agent_id=agent.id, mission="c", run_id="run-e2e", run_control_key="K")
    store = SqliteTraceStore()
    store.append(TraceRecord(run_id="run-e2e", seq=0, payload=json.dumps({"i": 0})))

    rest = TestClient(build_rest_app())
    assert (
        rest.post("/api/runs/run-e2e/complete", headers={"Authorization": "Bearer K"}).status_code
        == 200
    )
    assert SqliteSealStore().is_sealed("run-e2e") is True

    # a late span after seal is rejected by the receiver and not admitted to the record
    from xorcise.core.otel.ingest.embedded import create_otel_app

    otel = TestClient(create_otel_app(store, SqliteSealStore()))
    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "xorcise.run_id", "value": {"stringValue": "run-e2e"}}]
                },
                "scopeSpans": [{"spans": [{"name": "late"}]}],
            }
        ]
    }
    resp = otel.post("/v1/traces", content=json.dumps(body))
    assert resp.json()["partialSuccess"]["rejectedSpans"] == 1
    assert len(store.read("run-e2e")) == 1  # frozen: still just the pre-seal record


def test_terminal_drain_admits_final_export_then_seals(migrated_home, monkeypatch):
    from datetime import UTC, datetime

    from xorcise.core import agents, config, runs
    from xorcise.core.otel.ingest.embedded import create_otel_app
    from xorcise.core.rest import run_terminate

    monkeypatch.setenv("XORCISE_TELEMETRY_DRAIN_SECONDS", "2")
    config.get_settings.cache_clear()
    waits: list[float] = []
    monkeypatch.setattr("xorcise.core.rest.run_terminate.time.sleep", waits.append)
    agent = agents.register("drainer", endpoint="http://a")
    runs.create_run(agent_id=agent.id, mission="c", run_id="run-drain")
    store = SqliteTraceStore()
    seals = SqliteSealStore()
    otel = TestClient(create_otel_app(store, seals))
    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "xorcise.run_id",
                            "value": {"stringValue": "run-drain"},
                        }
                    ]
                },
                "scopeSpans": [{"spans": [{"name": "final-tool-result"}]}],
            }
        ]
    }

    run_terminate.seal_terminal("run-drain", "done", datetime.now(UTC))
    assert seals.is_sealed("run-drain") is False
    assert otel.post("/v1/traces", content=json.dumps(body)).json() == {}
    assert len(store.read("run-drain")) == 1

    run_terminate.grade_and_record("run-drain")
    assert waits == [2.0]
    assert seals.is_sealed("run-drain") is True
    rejected = otel.post("/v1/traces", content=json.dumps(body)).json()
    assert rejected["partialSuccess"]["rejectedSpans"] == 1
    assert len(store.read("run-drain")) == 1
