from __future__ import annotations

from sqlalchemy import inspect

from xorcise.core import config, db


def test_upgrade_creates_agents_table(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("agents")}
    assert {"id", "name", "endpoint", "otel", "created_at"} <= cols


def test_upgrade_adds_run_network_cidr_column(tmp_path, monkeypatch):
    # runs.network_cidr is the run's allocated /24.
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("runs")}
    assert "network_cidr" in cols


def test_upgrade_adds_run_entry_cidrs_column(tmp_path, monkeypatch):
    # runs.entry_cidrs holds the carved subnets for the ACL.
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("runs")}
    assert "entry_cidrs" in cols


def test_upgrade_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    db.upgrade()  # second run is a no-op, must not raise
    assert inspect(db.get_engine()).has_table("agents")


def test_current_revision_is_none_on_fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    assert db.current_revision() is None


def test_current_revision_matches_head_after_upgrade(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    assert db.current_revision() == db.head_revision()


def test_head_revision_is_the_latest_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    assert db.head_revision() == "0002_run_provenance"


def test_boot_state_fresh_on_empty_db(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    assert db.boot_state() == "fresh"


def test_boot_state_ready_after_full_upgrade(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    assert db.boot_state() == "ready"


def test_boot_state_stale_when_behind_head(tmp_path, monkeypatch):
    from sqlalchemy import text

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    # simulate a DB stamped by an older build: its revision differs from this head
    with db.get_engine().begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = '0000_older_build'"))
    assert db.current_revision() != db.head_revision()
    assert db.boot_state() == "stale"


def test_boot_state_stale_when_tables_exist_but_unmanaged(tmp_path, monkeypatch):
    from sqlalchemy import text

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    # a DB with a table but no alembic_version stamp -> unmanaged, not safe to scaffold
    with db.get_engine().begin() as conn:
        conn.execute(text("CREATE TABLE legacy (id INTEGER)"))
    assert db.current_revision() is None
    assert db.boot_state() == "stale"
