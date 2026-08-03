# tests/unit/test_otel_traces_migration.py
from __future__ import annotations

from sqlalchemy import inspect

from xorcise.core import config, db


def test_upgrade_creates_traces_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("traces")}
    assert {"id", "run_id", "seq", "payload", "created_at"} <= cols


def test_head_revision_is_the_latest_migration(tmp_path, monkeypatch) -> None:
    # Derived, not pinned: alembic's head must be the highest-numbered version file,
    # so adding a migration can't silently leave the chain's tip behind.
    from pathlib import Path

    import xorcise.core.db.migrations as migrations

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    versions = Path(migrations.__file__).parent / "versions"
    latest = max(p.stem for p in versions.glob("0*.py"))
    assert db.head_revision() == latest


def test_upgrade_creates_logs_table(tmp_path, monkeypatch) -> None:
    # the RAW OTLP logs signal table, peer of `traces`.
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("logs")}
    assert {"id", "run_id", "seq", "payload", "created_at"} <= cols


def test_upgrade_adds_agent_event_receipt_time(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("agent_events")}
    assert "received_at" in cols


def test_sqlite_log_store_round_trips(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    from xorcise.core.contracts.telemetry import TraceRecord
    from xorcise.core.otel.store import SqliteLogStore

    store = SqliteLogStore()
    store.append(TraceRecord(run_id="r1", seq=0, payload='{"resourceLogs":[]}'))
    store.append(TraceRecord(run_id="r1", seq=1, payload='{"a":1}'))
    assert [r.seq for r in store.read("r1")] == [0, 1]
    assert [r.seq for r in store.read_since("r1", 0)] == [1]
    assert store.read("other") == []
