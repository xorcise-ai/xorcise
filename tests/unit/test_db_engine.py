from __future__ import annotations

from sqlalchemy import text

from xorcise.core import config, db


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
