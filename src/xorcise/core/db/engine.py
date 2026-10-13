"""Engine + session helpers (SHARED-KERNEL). Reads config; no domain-module imports."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from xorcise.core.config import get_settings

log = logging.getLogger(__name__)

# How long a contended write waits for the lock. This was already the sqlite3 driver's default;
# it is spelled out so the value is a decision, not an accident of the driver.
SQLITE_BUSY_TIMEOUT_S = 5.0


@lru_cache
def get_engine() -> Engine:
    """Build (and cache) the engine from settings.database_url (sqlite default)."""
    url = get_settings().database_url
    if not url.startswith("sqlite"):
        return create_engine(url, future=True)
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_S},
        future=True,
    )
    event.listen(engine, "connect", _sqlite_wal)
    return engine


def _sqlite_wal(dbapi_conn: sqlite3.Connection, _record: Any) -> None:
    """Put the file in WAL mode, so a reader never blocks the writer (#107).

    In the default rollback-journal mode a commit needs every reader gone: it waits out the busy
    timeout behind any connection still holding a read — a report, a list, the gate's poll — and
    then fails with "database is locked". Under WAL the commit does not wait on readers at all.

    The mode is stored in the database file, so after the first switch this is a no-op. The
    switch itself needs the file to itself; on a busy file it is logged and left for the next
    connection, never raised — a connection without WAL still works, one that fails to open does
    not. An in-memory database answers "memory" and is left as it is."""
    try:
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        log.warning("SQLite is not in WAL mode yet (%s); retrying on the next connection", exc)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session: commit on success, rollback on error, always close."""
    session = sessionmaker(bind=get_engine(), future=True)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
