"""Engine + session helpers (SHARED-KERNEL). Reads config; no domain-module imports."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from xorcise.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    """Build (and cache) the engine from settings.database_url (sqlite default)."""
    url = get_settings().database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


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
