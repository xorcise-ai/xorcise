import json

import pytest
from fastapi.testclient import TestClient

from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.store import SqliteTraceStore
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def test_traces_endpoint_returns_records_since_seq(migrated_home) -> None:
    store = SqliteTraceStore()
    for seq in range(3):
        store.append(TraceRecord(run_id="r1", seq=seq, payload=json.dumps({"i": seq})))
    client = TestClient(build_rest_app())

    body = client.get("/api/runs/r1/traces", params={"since": 0}).json()
    assert [rec["seq"] for rec in body["records"]] == [1, 2]
    assert body["records"][0]["payload"] == {"i": 1}


def test_traces_endpoint_unknown_run_is_empty_200(migrated_home) -> None:
    client = TestClient(build_rest_app())
    resp = client.get("/api/runs/ghost/traces")
    assert resp.status_code == 200
    assert resp.json() == {"run_id": "ghost", "records": []}


# ── Raw OTLP download (GET /runs/{id}/otlp.jsonl) ────────────────────────────────────────────


def _otlp_span_batch(name: str) -> dict[str, object]:
    return {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": name}]}]}]}


def test_otlp_jsonl_download_is_pure_otlp_lines(migrated_home) -> None:
    from xorcise.core import runs
    from xorcise.core.otel.store import SqliteLogStore

    runs.create_run(run_id="r2", agent_id="a1", mission="c", budget_seconds=60)
    traces = SqliteTraceStore()
    for seq in range(2):
        traces.append(
            TraceRecord(run_id="r2", seq=seq, payload=json.dumps(_otlp_span_batch(f"s{seq}")))
        )
    SqliteLogStore().append(
        TraceRecord(run_id="r2", seq=0, payload=json.dumps({"resourceLogs": []}))
    )
    resp = TestClient(build_rest_app()).get("/api/runs/r2/otlp.jsonl")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert (
        resp.headers["content-disposition"] == 'attachment; filename="xorcise-run-r2-c-otlp.jsonl"'
    )
    # No header line, no XORCISE framing: every line is a plain OTLP/JSON envelope, so the
    # file feeds the Collector's otlpjson file receiver (and from there any trace backend).
    lines = resp.text.splitlines()
    assert len(lines) == 3
    envelopes = [json.loads(ln) for ln in lines]
    assert envelopes[:2] == [_otlp_span_batch("s0"), _otlp_span_batch("s1")]
    assert envelopes[2] == {"resourceLogs": []}


def test_otlp_jsonl_reserializes_multiline_payloads_onto_one_line(migrated_home) -> None:
    from xorcise.core import runs

    runs.create_run(run_id="r3", agent_id="a1", mission="c", budget_seconds=60)
    # Ingest may store pretty-printed JSON; the export must still be one envelope per line.
    pretty = json.dumps(_otlp_span_batch("s0"), indent=2)
    SqliteTraceStore().append(TraceRecord(run_id="r3", seq=0, payload=pretty))
    resp = TestClient(build_rest_app()).get("/api/runs/r3/otlp.jsonl")
    lines = resp.text.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == _otlp_span_batch("s0")


def test_otlp_jsonl_unknown_run_404(migrated_home) -> None:
    resp = TestClient(build_rest_app()).get("/api/runs/ghost/otlp.jsonl")
    assert resp.status_code == 404
