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


def test_upgrade_adds_run_terminal_detail_column(tmp_path, monkeypatch):
    # runs.terminal_detail carries the readiness gate's deploy_failed evidence (0003).
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("runs")}
    assert "terminal_detail" in cols


def test_upgrade_adds_trace_seal_evidence_digest_column(tmp_path, monkeypatch):
    # trace_seals.evidence_digest makes the seal tamper-evident rather than a timestamp (0004).
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("trace_seals")}
    assert "evidence_digest" in cols


def test_upgrade_drops_results_recorded_for_environment_failures(tmp_path, monkeypatch):
    """0005 (#109): a deploy_failed or crashed run was graded as a genuine 0.00 by earlier builds.
    The upgrade removes those results — and only those: the run rows stay, and every other run's
    result (including a partial one) is untouched."""
    from datetime import UTC, datetime

    from alembic import command

    from xorcise.core import reporting, runs
    from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown
    from xorcise.core.db.migrate import _alembic_config

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    now = datetime(2026, 10, 8, tzinfo=UTC)
    ids: dict[str, str] = {}
    for trigger in ("deploy_failed", "crashed", "done", "timeout"):
        run = runs.create_run(agent_id="a1", mission="m", budget_seconds=60)
        runs.mark_terminal(run.run_id, trigger, now)
        # What an earlier build recorded for every one of them.
        grade = GradeResult(run_id=run.run_id, overall=0.0, breakdown=ScoreBreakdown())
        reporting.record_result(run.run_id, "a1", grade, partial=trigger == "timeout")
        ids[trigger] = run.run_id

    command.downgrade(_alembic_config(), "0004_trace_seal_evidence_digest")
    db.upgrade()

    assert reporting.get_result(ids["deploy_failed"]) is None
    assert reporting.get_result(ids["crashed"]) is None
    assert reporting.get_result(ids["done"]) is not None
    assert reporting.get_result(ids["timeout"]) is not None
    assert {r.run_id for r in runs.list_runs()} == set(ids.values())


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
    assert db.head_revision() == "0005_drop_environment_failure_results"


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
