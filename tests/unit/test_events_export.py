# tests/unit/test_events_export.py
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from xorcise.core import runs
from xorcise.core.contracts.telemetry import TraceRecord
from xorcise.core.otel.store import SqliteTraceStore

pytestmark = pytest.mark.unit


def _otlp(span_id: str, name: str) -> dict[str, Any]:
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
                                "attributes": [
                                    {"key": "command", "value": {"stringValue": "ls -la"}}
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }


def _seed(run_id: str, spans: list[tuple[int, str, str]], source_agent: str = "generic") -> None:
    runs.create_run(
        run_id=run_id, agent_id="a1", mission="c", budget_seconds=60, source_agent=source_agent
    )
    st = SqliteTraceStore()
    for seq, sid, name in spans:
        st.append(TraceRecord(run_id=run_id, seq=seq, payload=json.dumps(_otlp(sid, name))))


def _read_jsonl(path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    return lines[0], lines[1:]


def test_export_writes_header_plus_one_event_per_line(migrated_home):
    from xorcise.core.rest.events_export import default_export_path, export_run_events

    _seed("r1", [(0, "s0", "shell.exec"), (1, "s1", "assistant.msg")])
    path = export_run_events("r1")
    assert path == default_export_path("r1")
    assert path.exists()

    header, events = _read_jsonl(path)
    assert header["type"] == "header"
    assert header["run_id"] == "r1"
    assert header["event_count"] == len(events)
    assert len(events) == 2
    # each event line is a full AgentEvent with a clean body + raw provenance (not raw OTLP)
    for ev in events:
        assert "kind" in ev and "body" in ev and ev["raw_ref"]["run_id"] == "r1"


def test_export_default_path_under_home_runs(migrated_home):
    from xorcise.core.rest.events_export import default_export_path

    expected = migrated_home / "runs" / "rX" / "agent-events.jsonl"
    assert default_export_path("rX") == expected


def test_export_out_override(migrated_home, tmp_path):
    from xorcise.core.rest.events_export import export_run_events

    _seed("r2", [(0, "s0", "shell.exec")])
    out = tmp_path / "custom" / "events.jsonl"
    path = export_run_events("r2", out)
    assert path == out and out.exists()
    header, events = _read_jsonl(out)
    assert header["event_count"] == len(events)


def test_export_content_matches_the_events_view_projection(migrated_home):
    from xorcise.core.rest import events_view
    from xorcise.core.rest.events_export import export_run_events

    _seed("r3", [(0, "s0", "shell.exec"), (1, "s1", "assistant.msg")])
    view = events_view._full_view("r3")
    _, events = _read_jsonl(export_run_events("r3"))
    assert [e["id"] for e in events] == [e.id for e in view.events]
    assert [e["kind"] for e in events] == [e.kind.value for e in view.events]


def test_export_written_on_seal_via_grade_and_record(migrated_home):
    # The on-seal hook: finalizing a run materializes the export file (best-effort, in the
    # background grade_and_record phase after teardown).
    from xorcise.core.rest.events_export import default_export_path
    from xorcise.core.rest.run_terminate import grade_and_record, seal_terminal

    _seed("r4", [(0, "s0", "shell.exec"), (1, "s1", "assistant.msg")])
    seal_terminal("r4", "done", datetime(2026, 7, 5, tzinfo=UTC))
    grade_and_record("r4")  # judge degrades cleanly (no model) — records + exports
    path = default_export_path("r4")
    assert path.exists()
    header, events = _read_jsonl(path)
    assert header["run_id"] == "r4" and len(events) == 2


# ── The download endpoint (GET /runs/{id}/events.jsonl) ──────────────────────────────────────


def test_events_jsonl_download_matches_the_projection(migrated_home):
    from fastapi.testclient import TestClient

    from xorcise.core.rest import events_view
    from xorcise.core.roles.boot.role_all import build_rest_app

    _seed("r6", [(0, "s0", "shell.exec"), (1, "s1", "assistant.msg")])
    resp = TestClient(build_rest_app()).get("/api/runs/r6/events.jsonl")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert (
        resp.headers["content-disposition"]
        == 'attachment; filename="xorcise-run-r6-c-events.jsonl"'
    )
    lines = [json.loads(ln) for ln in resp.text.splitlines() if ln.strip()]
    header, events = lines[0], lines[1:]
    assert header["type"] == "header" and header["run_id"] == "r6"
    assert header["event_count"] == len(events) == 2
    view = events_view._full_view("r6")
    assert [e["id"] for e in events] == [e.id for e in view.events]


def test_events_jsonl_unknown_run_404(migrated_home):
    from fastapi.testclient import TestClient

    from xorcise.core.roles.boot.role_all import build_rest_app

    assert TestClient(build_rest_app()).get("/api/runs/ghost/events.jsonl").status_code == 404


# ── The CLI thin client (`xorcise run events export`) ────────────────────────────────────────

RID = "a1b2c3d4e5f6a7b8c9d0a1b2c3d4e5f6"


def _plain(text: str) -> str:
    """Terminal-colour-proof: strip ANSI codes, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"\x1b\[[0-9;]*m", "", text))


def _cli_app() -> typer.Typer:
    import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
    from xorcise.core.cli._shared import app

    return app


def _patch_download(monkeypatch, body: str) -> dict[str, str]:
    """Stub the one REST call the export makes; capture the get_text path."""
    captured: dict[str, str] = {}

    def fake_get_text(self, path: str) -> str:  # noqa: ANN001 — test stub
        captured["path"] = path
        return body

    monkeypatch.setattr("xorcise.core.cli.commands.run.RestClient.get_text", fake_get_text)
    return captured


def test_cli_run_events_export_downloads_via_rest(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = json.dumps({"type": "header", "event_count": 2}) + '\n{"kind":"a"}\n{"kind":"b"}\n'
    captured = _patch_download(monkeypatch, body)
    result = CliRunner().invoke(_cli_app(), ["run", "events", "export", RID])
    assert result.exit_code == 0, result.output
    assert captured["path"] == f"/runs/{RID}/events.jsonl"
    written = tmp_path / f"xorcise-run-{RID[:8]}-events.jsonl"
    assert written.read_text(encoding="utf-8") == body
    assert f"wrote {written.name} (2 events)" in _plain(result.output)


def test_cli_run_events_export_out_override(monkeypatch, tmp_path):
    body = json.dumps({"type": "header", "event_count": 1}) + '\n{"kind":"a"}\n'
    _patch_download(monkeypatch, body)
    out = tmp_path / "events.jsonl"
    result = CliRunner().invoke(_cli_app(), ["run", "events", "export", RID, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") == body
    assert "(1 event)" in _plain(result.output)


def test_cli_run_events_export_resolves_short_ids(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    body = json.dumps({"type": "header", "event_count": 0}) + "\n"
    captured = _patch_download(monkeypatch, body)
    monkeypatch.setattr("xorcise.core.cli.commands.run.resolve_run_id", lambda client, given: RID)
    result = CliRunner().invoke(_cli_app(), ["run", "events", "export", RID[:8]])
    assert result.exit_code == 0, result.output
    assert captured["path"] == f"/runs/{RID}/events.jsonl"
    assert "(0 events)" in _plain(result.output)
