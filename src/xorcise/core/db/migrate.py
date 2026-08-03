"""Programmatic Alembic runner (SHARED-KERNEL). `db upgrade` calls upgrade()."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from xorcise.core.config import get_settings
from xorcise.core.db.engine import get_engine

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def upgrade() -> str:
    """Apply pending migrations to a populated DB. Explicit step (`xorcise db
    upgrade`); `up` only scaffolds a fresh DB via this same call."""
    command.upgrade(_alembic_config(), "head")
    return "migrations: upgraded to head"


def head_revision() -> str:
    """The latest migration revision per the migration scripts."""
    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    assert head is not None  # there is always at least one migration
    return head


def current_revision() -> str | None:
    """The revision the live DB is stamped at; None if it has never been migrated."""
    with get_engine().connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def boot_state() -> Literal["fresh", "ready", "stale"]:
    """Classify the DB for boot-time handling.

    ``fresh`` — no schema at all; safe to scaffold to head on ``up``.
    ``ready`` — already at head; nothing to do.
    ``stale`` — has data/schema but is behind head (or unmanaged); migrating it
                is a *deliberate* act, so ``up`` must refuse and defer to
                ``xorcise db upgrade``.
    """
    current = current_revision()
    if current is None:
        app_tables = [t for t in inspect(get_engine()).get_table_names() if t != "alembic_version"]
        return "fresh" if not app_tables else "stale"
    return "ready" if current == head_revision() else "stale"
