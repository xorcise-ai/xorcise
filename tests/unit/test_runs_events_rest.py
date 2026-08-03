# tests/unit/test_runs_events_rest.py
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from xorcise.core import runs
from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.store import SqliteLogStore, SqliteTraceStore
from xorcise.core.roles.boot.role_all import build_rest_app


@pytest.fixture(autouse=True)
def _clear_events_cache(migrated_home):
    # clear_cache() now clears the DB-backed agent_events table, so this autouse
    # fixture must depend on migrated_home — otherwise pytest's "autouse before explicit"
    # ordering would run clear_cache() before XORCISE_HOME is pointed at the tmp DB, hitting
    # the real default XORCISE_HOME instead.
    from xorcise.core.rest import events_view

    events_view.clear_cache()
    yield
    events_view.clear_cache()


def _otlp(span_id: str, name: str) -> dict[str, object]:
    return {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "scope": {"name": "t"},
                        "spans": [
                            {
                                "spanId": span_id,
                                "name": name,
                                "startTimeUnixNano": "1700000000000000000",
                                "attributes": [{"key": "command", "value": {"stringValue": "ls"}}],
                            }
                        ],
                    }
                ]
            }
        ]
    }


def _seed(run_id: str, spans: list[tuple[int, str, str]]) -> None:
    runs.create_run(
        run_id=run_id, agent_id="a1", mission="c", budget_seconds=60, source_agent="generic"
    )
    store = SqliteTraceStore()
    for seq, sid, name in spans:
        store.append(TraceRecord(run_id=run_id, seq=seq, payload=json.dumps(_otlp(sid, name))))


def _assistant_log(text: str) -> dict[str, object]:
    return {
        "resourceLogs": [
            {
                "scopeLogs": [
                    {
                        "scope": {"name": "com.anthropic.claude_code.events"},
                        "logRecords": [
                            {
                                "timeUnixNano": "1700000001000000000",
                                "body": {"stringValue": "claude_code.assistant_response"},
                                "attributes": [{"key": "response", "value": {"stringValue": text}}],
                            }
                        ],
                    }
                ]
            }
        ]
    }


def test_events_route_returns_run_events_view(migrated_home):
    _seed("r1", [(0, "s0", "shell.exec"), (1, "s1", "assistant.reply")])
    client = TestClient(build_rest_app())
    resp = client.get("/api/runs/r1/events", params={"since": -1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "r1"
    assert body["source_agent"] == "generic"
    assert body["next_since"] == 1
    assert {
        "adapter_name",
        "adapter_version",
        "fallback",
        "counts",
        "warnings",
        "events",
    } <= set(body)
    assert len(body["events"]) == 2
    assert all("id" in e and "kind" in e and "raw_ref" in e for e in body["events"])
    assert all(e["received_at"] is not None for e in body["events"])
    assert all(e["received_at"].endswith("Z") for e in body["events"])


def test_events_route_since_cursor_slices(migrated_home):
    _seed("r2", [(0, "s0", "a"), (1, "s1", "b")])
    client = TestClient(build_rest_app())
    body = client.get("/api/runs/r2/events", params={"since": 0}).json()
    assert all(e["raw_ref"]["raw_seq"] > 0 for e in body["events"])
    assert len(body["events"]) == 1
    assert body["next_since"] == 1


def test_events_route_compound_cursor_does_not_drop_same_numbered_log(migrated_home):
    runs.create_run(
        run_id="mixed",
        agent_id="a1",
        mission="c",
        budget_seconds=60,
        source_agent="claude-code",
    )
    SqliteTraceStore().append(
        TraceRecord(
            run_id="mixed",
            seq=0,
            payload=json.dumps(_otlp("s0", "claude_code.tool")),
        )
    )
    client = TestClient(build_rest_app())
    first = client.get("/api/runs/mixed/events", params={"trace_since": -1, "log_since": -1}).json()
    assert first["next_cursor"] == {"trace_seq": 0, "log_seq": -1}

    SqliteLogStore().append(
        TraceRecord(run_id="mixed", seq=0, payload=json.dumps(_assistant_log("done")))
    )
    second = client.get(
        "/api/runs/mixed/events",
        params={
            "trace_since": first["next_cursor"]["trace_seq"],
            "log_since": first["next_cursor"]["log_seq"],
        },
    ).json()

    assert [event["body"] for event in second["events"]] == ["done"]
    assert second["events"][0]["raw_ref"]["signal"] == "log"
    assert second["next_cursor"] == {"trace_seq": 0, "log_seq": 0}


def test_raw_route_returns_source_log_batch(migrated_home):
    runs.create_run(
        run_id="log-raw",
        agent_id="a1",
        mission="c",
        budget_seconds=60,
        source_agent="claude-code",
    )
    SqliteLogStore().append(
        TraceRecord(run_id="log-raw", seq=0, payload=json.dumps(_assistant_log("raw assistant")))
    )
    client = TestClient(build_rest_app())
    event = client.get("/api/runs/log-raw/events").json()["events"][0]

    raw = client.get(f"/api/runs/log-raw/events/{event['id']}/raw").json()

    assert raw["raw_ref"]["signal"] == "log"
    assert raw["spans"] == []
    assert raw["logs"][0]["body"]["stringValue"] == "claude_code.assistant_response"


def test_events_route_unknown_run_is_empty_200(migrated_home):
    client = TestClient(build_rest_app())
    resp = client.get("/api/runs/ghost/events")
    assert resp.status_code == 200
    assert resp.json()["events"] == []


def test_raw_route_returns_source_span(migrated_home):
    _seed("r3", [(0, "sABC", "shell.exec")])
    client = TestClient(build_rest_app())
    ev = client.get("/api/runs/r3/events").json()["events"][0]
    resp = client.get(f"/api/runs/r3/events/{ev['id']}/raw")
    assert resp.status_code == 200
    raw = resp.json()
    assert raw["event_id"] == ev["id"]
    assert raw["raw_ref"]["span_id"] == "sABC"
    assert raw["spans"][0]["spanId"] == "sABC"


def test_raw_route_event_id_with_slash_routes(migrated_home):
    # base64 span ids can contain '/', so the {event_id:path} converter must handle it.
    _seed("r6", [])
    store = SqliteTraceStore()
    store.append(
        TraceRecord(run_id="r6", seq=0, payload=json.dumps(_otlp("a/b+c==", "shell.exec")))
    )
    client = TestClient(build_rest_app())
    ev = client.get("/api/runs/r6/events").json()["events"][0]
    assert "/" in ev["id"]  # id derives from the base64 span id
    resp = client.get(f"/api/runs/r6/events/{ev['id']}/raw")
    assert resp.status_code == 200
    assert resp.json()["raw_ref"]["span_id"] == "a/b+c=="


def test_raw_route_unknown_event_404(migrated_home):
    _seed("r4", [(0, "s0", "x")])
    client = TestClient(build_rest_app())
    assert client.get("/api/runs/r4/events/nope/raw").status_code == 404


def test_openapi_exposes_agent_event_dtos(migrated_home):
    client = TestClient(build_rest_app())
    schema = client.get("/openapi.json").json()
    names = schema["components"]["schemas"]
    assert "RunEventsView" in names
    assert "AgentEvent" in names
    assert "RawTraceRef" in names
    assert "AdapterWarning" in names
    assert "/api/runs/{run_id}/events" in schema["paths"]


def test_traces_route_byte_for_byte_unchanged(migrated_home):
    # golden: /traces still returns exactly {run_id, records:[{seq, payload}]} and nothing else.
    _seed("r5", [(0, "s0", "shell.exec")])
    client = TestClient(build_rest_app())
    body = client.get("/api/runs/r5/traces", params={"since": -1}).json()
    assert set(body) == {"run_id", "records"}
    assert body["run_id"] == "r5"
    assert set(body["records"][0]) == {"seq", "payload"}
    assert body["records"][0]["seq"] == 0
    spans = body["records"][0]["payload"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[0]["spanId"] == "s0"


def test_runs_list_reports_last_telemetry_at(migrated_home):
    # Stall detection (agent-inactivity warning): the list surfaces the server-side receipt time
    # of the run's newest OTLP export, across BOTH signals — a log-only exporter still counts
    # as alive.
    _seed("r-tel", [(0, "s0", "shell.exec")])
    SqliteLogStore().append(
        TraceRecord(run_id="r-tel", seq=0, payload=json.dumps(_assistant_log("hi")))
    )
    client = TestClient(build_rest_app())
    entry = next(e for e in client.get("/api/runs").json() if e["run_id"] == "r-tel")
    assert entry["last_telemetry_at"] is not None
    assert entry["last_telemetry_at"].endswith("Z")  # UTC by contract, like received_at


def test_runs_list_last_telemetry_null_before_first_export(migrated_home):
    runs.create_run(
        run_id="r-quiet", agent_id="a1", mission="c", budget_seconds=60, source_agent="generic"
    )
    client = TestClient(build_rest_app())
    entry = next(e for e in client.get("/api/runs").json() if e["run_id"] == "r-quiet")
    assert entry["last_telemetry_at"] is None
