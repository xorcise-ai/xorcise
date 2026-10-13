from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from xorcise.core import config, db
from xorcise.core.db import engine as engine_module
from xorcise.core.db.engine import SQLITE_BUSY_TIMEOUT_S, _sqlite_wal


def test_engine_targets_home_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    engine = db.get_engine()
    assert engine.url.drivername.startswith("sqlite")
    assert str(tmp_path) in str(engine.url)


def test_session_scope_commits_and_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    with db.session_scope() as s:
        assert s.execute(text("select 1")).scalar() == 1


def _fresh_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    return db.get_engine()


def test_sqlite_engine_is_wal_and_waits_on_a_lock(tmp_path, monkeypatch):
    engine = _fresh_engine(tmp_path, monkeypatch)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        busy_ms = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert busy_ms == int(SQLITE_BUSY_TIMEOUT_S * 1000)
    engine.dispose()


def test_a_commit_does_not_wait_on_an_open_reader(tmp_path, monkeypatch):
    """#107. In rollback-journal mode a commit needs every reader gone: it waits out the busy
    timeout behind any connection still holding a read, then fails with "database is locked".
    Under WAL a reader never blocks the writer, so the commit lands while the read is open."""
    monkeypatch.setattr(engine_module, "SQLITE_BUSY_TIMEOUT_S", 0.5)  # fail fast on a regression
    engine = _fresh_engine(tmp_path, monkeypatch)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1), (2), (3)"))

    reading = threading.Event()
    committed = threading.Event()

    def reader() -> None:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT x FROM t"))
            rows.fetchone()  # cursor left open: the read lock is held until the writer is done
            reading.set()
            committed.wait(5)
            rows.fetchall()

    holder = threading.Thread(target=reader)
    holder.start()
    try:
        assert reading.wait(5)
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO t VALUES (20)"))
            conn.commit()
    finally:
        committed.set()
        holder.join(10)
    with engine.connect() as conn:
        assert 20 in set(conn.execute(text("SELECT x FROM t")).scalars())
    engine.dispose()


def test_a_busy_file_leaves_the_connection_usable(tmp_path, caplog):
    """Switching an existing file to WAL needs it to itself. Another connection's lock must cost
    a warning, never the connection — the switch is retried on the next one."""
    path = tmp_path / "busy.db"
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("CREATE TABLE t (x INTEGER)")
    holder.execute("BEGIN EXCLUSIVE")
    conn = sqlite3.connect(path, timeout=0)
    try:
        with caplog.at_level(logging.WARNING, logger="xorcise.core.db.engine"):
            _sqlite_wal(conn, None)
        assert "not in WAL mode yet" in caplog.text
    finally:
        conn.close()
        holder.execute("ROLLBACK")
        holder.close()

    conn = sqlite3.connect(path)
    _sqlite_wal(conn, None)  # the file is free now: the next connection switches it
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_a_non_sqlite_url_gets_no_sqlite_settings(tmp_path, monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_create_engine(url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return object()

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setenv("XORCISE_DATABASE_URL", "postgresql://u@localhost/xorcise")
    monkeypatch.setattr(engine_module, "create_engine", fake_create_engine)
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    try:
        db.get_engine()
    finally:
        config.get_settings.cache_clear()
        db.get_engine.cache_clear()
    assert calls == [("postgresql://u@localhost/xorcise", {"future": True})]
