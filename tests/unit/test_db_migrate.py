from __future__ import annotations

import pytest
from sqlalchemy import inspect

from xorcise.core import config, db

_NOW = "2026-10-13 00:00:00"


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


def test_upgrade_gives_every_agent_a_kind_and_keeps_the_table_intact(tmp_path, monkeypatch):
    """0006 (#141): an agent registered without a harness was stored with kind NULL or blank. Its
    runs already replayed as `generic`, so the upgrade writes that down and makes the column NOT
    NULL DEFAULT 'generic'. A real kind, including one no harness answers to, is left alone."""
    from alembic import command
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from xorcise.core.db.migrate import _alembic_config

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    command.downgrade(_alembic_config(), "0005_drop_environment_failure_results")
    stored = {
        "null": None,
        "empty": "",
        "spaces": "   ",
        "tab": "\t\n",
        "known": "openhands",
        "typo": "openhand",
    }
    with db.get_engine().begin() as conn:
        for name, kind in stored.items():
            conn.execute(
                text(
                    "INSERT INTO agents (id, name, kind, version, created_at) "
                    "VALUES (:id, :n, :k, 1, :t)"
                ),
                {"id": f"id-{name}", "n": name, "k": kind, "t": _NOW},
            )
        conn.execute(
            text(
                "INSERT INTO runs (id, agent_id, mission, state, created_at) "
                "VALUES ('r1', 'id-null', 'm', 'terminal', :t)"
            ),
            {"t": _NOW},
        )

    db.upgrade()

    with db.get_engine().begin() as conn:
        kinds = {name: kind for name, kind in conn.execute(text("SELECT name, kind FROM agents"))}
        assert kinds == {
            "null": "generic",
            "empty": "generic",
            "spaces": "generic",
            "tab": "generic",
            "known": "openhands",
            "typo": "openhand",
        }
        # A writer that leaves the column out gets the default, and nothing can store NULL again.
        conn.execute(
            text(
                "INSERT INTO agents (id, name, version, created_at) VALUES ('id-new', 'new', 1, :t)"
            ),
            {"t": _NOW},
        )
        new_kind = conn.execute(text("SELECT kind FROM agents WHERE name = 'new'")).scalar()
        assert new_kind == "generic"
    with pytest.raises(IntegrityError), db.get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agents (id, name, kind, version, created_at) "
                "VALUES ('x', 'x', NULL, 1, :t)"
            ),
            {"t": _NOW},
        )
    # The rebuild keeps the table's constraints and its run still points at it.
    with pytest.raises(IntegrityError), db.get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agents (id, name, version, created_at) VALUES ('dup', 'known', 1, :t)"
            ),
            {"t": _NOW},
        )
    assert "ix_agents_name" in {i["name"] for i in inspect(db.get_engine()).get_indexes("agents")}
    [fk] = inspect(db.get_engine()).get_foreign_keys("runs")
    assert fk["referred_table"] == "agents"
    with db.get_engine().begin() as conn:
        assert conn.execute(text("SELECT agent_id FROM runs")).scalar() == "id-null"


def test_upgrade_adds_the_release_columns_without_backfilling(tmp_path, monkeypatch):
    """0007 (#173): a terminal run that predates the columns starts out unreleased, so the release
    sweeper revisits it once; the downgrade drops the columns again."""
    from alembic import command
    from sqlalchemy import text

    from xorcise.core.db.migrate import _alembic_config

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    command.downgrade(_alembic_config(), "0006_agent_kind_defaults_to_generic")
    with db.get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO runs (id, agent_id, mission, state, created_at, terminal_trigger, "
                "completed_at, network_cidr) VALUES ('r1', 'a1', 'm', 'terminal', :now, 'done', "
                ":now, '10.200.1.0/24')"
            ),
            {"now": _NOW},
        )
    db.upgrade()
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("runs")}
    assert {"released_at", "release_attempts", "release_error"} <= cols
    with db.get_engine().begin() as conn:
        row = conn.execute(
            text("SELECT released_at, release_attempts, release_error FROM runs WHERE id = 'r1'")
        ).one()
    assert tuple(row) == (None, 0, None)

    command.downgrade(_alembic_config(), "0006_agent_kind_defaults_to_generic")
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("runs")}
    assert not {"released_at", "release_attempts", "release_error"} & cols


def test_upgrade_makes_seq_unique_and_renumbers_existing_duplicates(tmp_path, monkeypatch, caplog):
    """0008 (#147): a run already holding a duplicate seq is renumbered in the digest's
    (seq, payload) order and named in a WARNING, never a failed upgrade. A sealed one has its
    digest scheme renamed, so it reads as unverifiable rather than as tampered. The unique indexes
    replace the run_id ones, the admission table exists, and the downgrade reverses both."""
    import logging

    from alembic import command
    from sqlalchemy import text

    from xorcise.core.db.migrate import _alembic_config

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    command.downgrade(_alembic_config(), "0007_record_environment_release")
    rows = [
        ("dup-sealed", 0, "b"),
        ("dup-sealed", 0, "a"),
        ("dup-sealed", 1, "c"),
        ("clean", 0, "x"),
        ("clean", 1, "y"),
    ]
    with db.get_engine().begin() as conn:
        for table in ("traces", "logs"):
            for run_id, seq, payload in rows:
                conn.execute(
                    text(
                        f"INSERT INTO {table} (run_id, seq, payload, created_at) "
                        "VALUES (:r, :s, :p, :now)"
                    ),
                    {"r": run_id, "s": seq, "p": payload, "now": _NOW},
                )
        for run_id in ("dup-sealed", "clean"):
            conn.execute(
                text(
                    "INSERT INTO trace_seals (run_id, sealed_at, evidence_digest) "
                    "VALUES (:r, :now, 'xorcise-evidence-v1:abc123')"
                ),
                {"r": run_id, "now": _NOW},
            )

    with caplog.at_level(logging.WARNING):
        db.upgrade()

    with db.get_engine().begin() as conn:
        for table in ("traces", "logs"):
            got = conn.execute(
                text(f"SELECT run_id, seq, payload FROM {table} ORDER BY run_id, seq")
            ).all()
            assert [tuple(r) for r in got] == [
                ("clean", 0, "x"),
                ("clean", 1, "y"),
                ("dup-sealed", 0, "a"),
                ("dup-sealed", 1, "b"),
                ("dup-sealed", 2, "c"),
            ]
        seal_rows = conn.execute(text("SELECT run_id, evidence_digest FROM trace_seals")).all()
    digests = {row.run_id: row.evidence_digest for row in seal_rows}
    assert digests == {
        "dup-sealed": "xorcise-evidence-v1-renumbered-0008:abc123",
        "clean": "xorcise-evidence-v1:abc123",  # never renumbered, so still verifiable
    }
    [warning] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert "dup-sealed" in warning and "clean" not in warning
    engine_inspect = inspect(db.get_engine())
    for table in ("traces", "logs"):
        indexes = engine_inspect.get_indexes(table)
        assert [(i["column_names"], i["unique"]) for i in indexes] == [(["run_id", "seq"], True)]
    assert engine_inspect.has_table("otel_admissions")

    command.downgrade(_alembic_config(), "0007_record_environment_release")
    engine_inspect = inspect(db.get_engine())
    assert not engine_inspect.has_table("otel_admissions")
    for table in ("traces", "logs"):
        indexes = engine_inspect.get_indexes(table)
        assert [(i["column_names"], i["unique"]) for i in indexes] == [(["run_id"], False)]


def test_upgrade_records_when_existing_runs_started_from_their_evidence(tmp_path, monkeypatch):
    """0009 (#209): each run's earliest evidence sets started_at and start_trigger, and an
    unfinished run that holds any moves to xorcising. A finished run keeps `terminal`; a run
    holding no evidence is left alone. The downgrade moves xorcising runs back to created."""
    from alembic import command
    from sqlalchemy import text

    from xorcise.core.db.migrate import _alembic_config

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    command.downgrade(_alembic_config(), "0008_unique_telemetry_seq")
    runs = {
        "otel-first": "created",  # a log at 00:01 beats a flag at 00:02
        "brief-first": "created",  # fetched its brief at 00:01, exported at 00:03
        "joined": "terminal",  # only a confirmed join: the key fetch it stands in for
        "untouched": "created",  # no evidence: never started
    }
    with db.get_engine().begin() as conn:
        for run_id, state in runs.items():
            conn.execute(
                text(
                    "INSERT INTO runs (id, agent_id, mission, state, created_at) "
                    "VALUES (:r, 'a1', 'm', :state, :now)"
                ),
                {"r": run_id, "state": state, "now": _NOW},
            )
        conn.execute(
            text(
                "INSERT INTO logs (run_id, seq, payload, created_at) VALUES "
                "('otel-first', 0, 'x', '2026-10-13 00:01:00'), "
                "('brief-first', 0, 'x', '2026-10-13 00:03:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO run_submissions (run_id, kind, name, payload, created_at) VALUES "
                "('otel-first', 'flag', 'f', 'p', '2026-10-13 00:02:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO run_observed_facts (run_id, kind, name, value, created_at) VALUES "
                "('brief-first', 'runcontrol-lifecycle', 'prompt', 'fetched', "
                "'2026-10-13 00:01:00'), "
                "('joined', 'network-lifecycle', 'join', 'created', '2026-10-13 00:00:30'), "
                "('joined', 'network-lifecycle', 'join', 'confirmed', '2026-10-13 00:04:00')"
            )
        )

    db.upgrade()

    with db.get_engine().begin() as conn:
        got = {
            row.id: (row.state, str(row.started_at)[:19] if row.started_at else None, row.trig)
            for row in conn.execute(
                text("SELECT id, state, started_at, start_trigger AS trig FROM runs")
            )
        }
    assert got == {
        "otel-first": ("xorcising", "2026-10-13 00:01:00", "telemetry"),
        "brief-first": ("xorcising", "2026-10-13 00:01:00", "run_control"),
        "joined": ("terminal", "2026-10-13 00:04:00", "tailnet_key"),
        "untouched": ("created", None, None),
    }

    with db.get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO runs (id, agent_id, mission, state, created_at, terminal_trigger) "
                "VALUES ('cancelled', 'a1', 'm', 'terminal', :now, 'cancelled')"
            ),
            {"now": _NOW},
        )
    command.downgrade(_alembic_config(), "0008_unique_telemetry_seq")
    cols = {c["name"] for c in inspect(db.get_engine()).get_columns("runs")}
    assert not {"started_at", "start_trigger"} & cols
    with db.get_engine().begin() as conn:
        trigger, detail = conn.execute(
            text("SELECT terminal_trigger, terminal_detail FROM runs WHERE id = 'cancelled'")
        ).one()
    # An earlier build does not know `cancelled` and would grade it; it never grades `crashed`.
    assert trigger == "crashed" and "cancelled" in detail
    with db.get_engine().begin() as conn:
        states = {row.id: row.state for row in conn.execute(text("SELECT id, state FROM runs"))}
    assert states == {
        "otel-first": "created",
        "brief-first": "created",
        "joined": "terminal",
        "untouched": "created",
        "cancelled": "terminal",
    }


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
    assert db.head_revision() == "0009_record_when_a_run_started"


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
