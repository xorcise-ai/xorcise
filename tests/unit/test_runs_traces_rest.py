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
