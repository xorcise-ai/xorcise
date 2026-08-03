# tests/unit/test_events_export.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
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


def test_cli_run_events_export(migrated_home):
    import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
    from xorcise.core.cli._shared import app
    from xorcise.core.rest.events_export import default_export_path

    _seed("r5", [(0, "s0", "shell.exec")])
    result = CliRunner().invoke(app, ["run", "events", "export", "r5"])
    assert result.exit_code == 0, result.output
    assert default_export_path("r5").exists()
    assert "wrote" in result.output
