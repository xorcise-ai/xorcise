from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from xorcise.core import config, db
from xorcise.core.db import engine as engine_module
from xorcise.core.db.engine import (
    SQLITE_BUSY_TIMEOUT_S,
    SQLITE_JOURNAL_SIZE_LIMIT_BYTES,
    _sqlite_wal,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A throwaway XORCISE_HOME with no schema. Same teardown as `migrated_home`: an engine left
    in the cache would outlive the test, pointing at a deleted tmp dir — with whatever timeout
    the test patched in — for the next test on this worker."""
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    yield tmp_path
    if db.get_engine.cache_info().currsize:
        db.get_engine().dispose()
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()


def _seeded_engine() -> Engine:
    engine = db.get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1), (2), (3)"))
    return engine


def test_engine_targets_home_sqlite(home: Path) -> None:
    engine = db.get_engine()
    assert engine.url.drivername.startswith("sqlite")
    assert str(home) in str(engine.url)


def test_session_scope_commits_and_reads(home: Path) -> None:
    with db.session_scope() as s:
        assert s.execute(text("select 1")).scalar() == 1


def test_sqlite_engine_is_wal_with_a_bounded_journal(home: Path) -> None:
    with db.get_engine().connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        limit = conn.execute(text("PRAGMA journal_size_limit")).scalar()
    assert limit == SQLITE_JOURNAL_SIZE_LIMIT_BYTES


def test_a_write_behind_a_held_write_lock_waits_then_lands(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The busy timeout is the engine's, not the driver's: patch it, and the patched value is
    what a contended write waits on. The holder releases well inside it, so the write lands."""
    monkeypatch.setattr(engine_module, "SQLITE_BUSY_TIMEOUT_S", 3.0)
    engine = _seeded_engine()
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 3000

    locked = threading.Event()

    def holder() -> None:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO t VALUES (10)"))  # takes the write lock
            locked.set()
            time.sleep(0.3)
            conn.commit()

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert locked.wait(5)
        started = time.monotonic()
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO t VALUES (20)"))
            conn.commit()
        waited = time.monotonic() - started
    finally:
        thread.join(10)
    assert waited >= 0.1  # it queued behind the holder rather than failing at once
    with engine.connect() as conn:
        assert {10, 20} <= set(conn.execute(text("SELECT x FROM t")).scalars())


def test_the_default_busy_timeout_is_five_seconds(home: Path) -> None:
    with db.get_engine().connect() as conn:
        busy_ms = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert busy_ms == int(SQLITE_BUSY_TIMEOUT_S * 1000) == 5000


def test_a_commit_does_not_wait_on_an_open_reader(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#107. In rollback-journal mode a commit needs every reader gone: it waits out the busy
    timeout behind any connection still holding a read, then fails with "database is locked".
    Under WAL a reader never blocks the writer, so the commit lands while the read is open."""
    monkeypatch.setattr(engine_module, "SQLITE_BUSY_TIMEOUT_S", 0.5)  # fail fast on a regression
    engine = _seeded_engine()

    reading = threading.Event()
    committed = threading.Event()

    def reader() -> None:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT x FROM t"))
            rows.fetchone()  # cursor left open: the read lock is held until the writer is done
            reading.set()
            committed.wait(5)
            rows.fetchall()

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        assert reading.wait(5)
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO t VALUES (20)"))
            conn.commit()
    finally:
        committed.set()
        thread.join(10)
    with engine.connect() as conn:
        assert 20 in set(conn.execute(text("SELECT x FROM t")).scalars())


def test_a_busy_file_leaves_the_connection_usable(tmp_path: Path, caplog) -> None:
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


def test_close_engine_folds_the_wal_into_the_database_file(home: Path) -> None:
    """After close_engine, xorcise.db alone holds every committed write: a copy of just that
    file is a complete backup, and no -wal is left to replay over a restored older copy.

    An idle connection stays open throughout, as a daemon thread's would. SQLite folds the WAL
    in by itself when the LAST connection closes, so without one this test would pass on
    dispose() alone and never exercise the checkpoint."""
    engine = _seeded_engine()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO t VALUES (99)"))
    wal = home / "xorcise.db-wal"
    assert wal.stat().st_size > 0  # the write is in the WAL, not yet in the database file
    idle = sqlite3.connect(home / "xorcise.db")
    idle.execute("SELECT count(*) FROM t").fetchall()  # opens the WAL; no read left open
    try:
        db.close_engine()
        assert wal.stat().st_size == 0  # TRUNCATE: folded in and emptied, though idle is open
    finally:
        idle.close()
    copy = home / "copy.db"
    copy.write_bytes((home / "xorcise.db").read_bytes())
    alone = sqlite3.connect(copy)
    try:
        assert 99 in {x for (x,) in alone.execute("SELECT x FROM t")}
    finally:
        alone.close()


def test_close_engine_reports_a_checkpoint_a_reader_holds_open(home: Path, caplog) -> None:
    engine = _seeded_engine()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO t VALUES (99)"))
    reader = sqlite3.connect(home / "xorcise.db")
    rows = reader.execute("SELECT x FROM t")
    rows.fetchone()  # an open read pins the WAL: the TRUNCATE checkpoint cannot finish
    try:
        with caplog.at_level(logging.WARNING, logger="xorcise.core.db.engine"):
            db.close_engine()  # must not raise
        assert "did not finish" in caplog.text
    finally:
        rows.fetchall()
        reader.close()


def test_close_engine_builds_no_engine_when_none_was_opened(home: Path) -> None:
    db.close_engine()
    assert db.get_engine.cache_info().currsize == 0
    assert not (home / "xorcise.db").exists()


def test_a_non_sqlite_url_gets_no_sqlite_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
