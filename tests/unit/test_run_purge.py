"""Deleting a run removes everything recorded for it (#117).

The seeding is generic on purpose: it writes one row into every table of the migrated schema that
has a run_id column, so a run-scoped table added later fails these tests until the purge covers it.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, func, insert, select, text
from sqlalchemy.exc import OperationalError

from xorcise.core import agents, runs
from xorcise.core.db import get_engine
from xorcise.core.roles.boot.role_all import build_rest_app

_KNOWN_RUN_TABLES = {
    "traces",
    "logs",
    "trace_seals",
    "agent_event_runs",
    "agent_events",
    "run_submissions",
    "results",
    "run_observed_facts",
    "terrain_updates",
}
# Created by 0001_initial for the v1 terrain cache. No code reads or writes it since
# terrain_updates replaced it, so it never holds a run's rows.
_UNWRITTEN_TABLES = {"terrain_actions"}
_FILLER: dict[type, object] = {
    int: 0,
    str: "x",
    float: 0.0,
    bool: False,
    datetime: datetime(2026, 1, 1),
}


def _client(*, raise_server_exceptions: bool = True) -> TestClient:
    return TestClient(build_rest_app(), raise_server_exceptions=raise_server_exceptions)


def _run_tables() -> list[Table]:
    meta = MetaData()
    meta.reflect(bind=get_engine())
    return [
        table
        for table in meta.sorted_tables
        if "run_id" in table.c and table.name not in _UNWRITTEN_TABLES
    ]


def _seed(run_id: str) -> None:
    with get_engine().begin() as conn:
        for table in _run_tables():
            row: dict[str, object] = {}
            for col in table.c:
                kind = col.type.python_type
                if col.name == "run_id":
                    row[col.name] = run_id
                elif col.primary_key and kind is int:
                    continue
                elif col.primary_key:
                    row[col.name] = f"{run_id}-{col.name}"
                elif not col.nullable and col.server_default is None:
                    row[col.name] = _FILLER[kind]
            conn.execute(insert(table).values(row))


def _rows(run_id: str) -> dict[str, int]:
    with get_engine().connect() as conn:
        return {
            table.name: conn.execute(
                select(func.count()).select_from(table).where(table.c.run_id == run_id)
            ).scalar_one()
            for table in _run_tables()
        }


def _none() -> dict[str, int]:
    return dict.fromkeys((table.name for table in _run_tables()), 0)


def _one_each() -> dict[str, int]:
    return dict.fromkeys((table.name for table in _run_tables()), 1)


def _write_run_dir(home: Path, run_id: str) -> Path:
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "agent-events.jsonl").write_text('{"type": "header"}\n')
    return run_dir


def _run_dirs(home: Path) -> list[str]:
    return sorted(entry.name for entry in (home / "runs").iterdir())


def _terminal_run(home: Path, agent_name: str, run_id: str) -> str:
    agent = agents.get(agent_name) or agents.register(agent_name, endpoint="http://agent")
    runs.create_run(agent_id=agent.id, mission="c1", run_id=run_id, run_control_key="K")
    runs.mark_terminal(run_id, "done", datetime.now(UTC))
    _seed(run_id)
    _write_run_dir(home, run_id)
    return run_id


def _listed_runs() -> list[str]:
    return sorted(entry["run_id"] for entry in _client().get("/api/runs").json())


def test_delete_run_removes_every_record_and_file_of_that_run_only(migrated_home):
    assert {table.name for table in _run_tables()} >= _KNOWN_RUN_TABLES
    gone = _terminal_run(migrated_home, "alpha", "run-gone")
    kept = _terminal_run(migrated_home, "alpha", "run-kept")

    assert _client().delete(f"/api/runs/{gone}").status_code == 204

    assert _rows(gone) == _none()
    assert _rows(kept) == _one_each()
    assert _run_dirs(migrated_home) == ["run-kept"]
    assert _listed_runs() == ["run-kept"]


def test_delete_agent_removes_every_record_and_file_of_its_runs_only(migrated_home):
    alpha = [_terminal_run(migrated_home, "alpha", f"alpha-{n}") for n in (1, 2)]
    beta = _terminal_run(migrated_home, "beta", "beta-1")

    assert _client().delete("/api/agents/alpha").status_code == 204

    assert [_rows(run_id) for run_id in alpha] == [_none(), _none()]
    assert _rows(beta) == _one_each()
    assert _run_dirs(migrated_home) == ["beta-1"]
    assert _listed_runs() == ["beta-1"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_delete_that_fails_partway_keeps_the_run_and_a_retry_finishes_it(migrated_home):
    run_id = _terminal_run(migrated_home, "alpha", "run-stuck")
    run_dir = migrated_home / "runs" / run_id
    run_dir.chmod(0o500)
    try:
        failed = _client(raise_server_exceptions=False).delete(f"/api/runs/{run_id}")
    finally:
        run_dir.chmod(0o700)

    assert failed.status_code == 500
    assert _listed_runs() == ["run-stuck"]

    assert _client().delete(f"/api/runs/{run_id}").status_code == 204
    assert _rows(run_id) == _none()
    assert _run_dirs(migrated_home) == []
    assert _listed_runs() == []


def _boot(monkeypatch) -> list[str]:
    """The boot reconcile, with the Docker/Headscale convergence (which needs live deps) and any
    grade it starts recorded instead of run."""
    import xorcise.core.rest.reconcile as reconcile
    import xorcise.core.rest.run_terminate as run_terminate

    calls: list[str] = []

    def converge() -> reconcile.ReconcileReport:
        calls.append("runs")
        return reconcile.ReconcileReport()

    monkeypatch.setattr(reconcile, "reconcile_on_startup", converge)
    monkeypatch.setattr(run_terminate, "grade_and_record", lambda run_id: calls.append(run_id))
    reconcile.reconcile_all_on_startup()
    return calls


def test_boot_clears_orphaned_run_artifacts_and_keeps_live_runs(migrated_home, monkeypatch, caplog):
    live = runs.create_run(agent_id="a1", mission="c1", run_id="run-live").run_id
    _seed(live)
    _write_run_dir(migrated_home, live)
    _seed("run-orphan")
    _write_run_dir(migrated_home, "run-orphan")
    _write_run_dir(migrated_home, "run-dir-only")

    with caplog.at_level(logging.INFO):
        assert _boot(monkeypatch) == ["runs"]

    assert _rows("run-orphan") == _none()
    assert _rows(live) == _one_each()
    assert _run_dirs(migrated_home) == ["run-live"]
    assert "cleared 2 orphaned run(s)" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO):
        _boot(monkeypatch)

    assert "orphaned run" not in caplog.text
    assert _rows(live) == _one_each()
    assert _run_dirs(migrated_home) == ["run-live"]


def test_boot_never_follows_a_run_id_out_of_the_runs_directory(migrated_home, monkeypatch):
    """A trace row's run_id is whatever the OTLP export named, so it is not a safe path."""
    (migrated_home / "runs").mkdir()
    outside = migrated_home / "escape"
    outside.mkdir()
    (outside / "keep.txt").write_text("not a run")
    _seed("../escape")

    _boot(monkeypatch)

    assert _rows("../escape") == _none()
    assert (outside / "keep.txt").read_text() == "not a run"


def test_a_sweep_that_cannot_read_the_runs_deletes_nothing_and_boot_carries_on(
    migrated_home, monkeypatch, caplog
):
    import xorcise.core.rest.reconcile as reconcile
    import xorcise.core.rest.run_terminate as run_terminate

    run_id = runs.create_run(agent_id="a1", mission="c1", run_id="run-live").run_id
    _seed(run_id)
    _write_run_dir(migrated_home, run_id)
    with get_engine().begin() as conn:
        conn.execute(text("ALTER TABLE runs RENAME TO runs_unreadable"))
    calls: list[str] = []

    def converge() -> reconcile.ReconcileReport:
        calls.append("runs")
        return reconcile.ReconcileReport()

    monkeypatch.setattr(reconcile, "reconcile_on_startup", converge)
    monkeypatch.setattr(
        run_terminate, "regrade_orphaned_terminal_runs", lambda: calls.append("regrade")
    )

    with caplog.at_level(logging.WARNING), pytest.raises(OperationalError, match="runs"):
        reconcile.reconcile_all_on_startup()

    assert calls == ["runs", "regrade"]
    assert _rows(run_id) == _one_each()
    assert _run_dirs(migrated_home) == ["run-live"]
    assert "the orphaned runs step failed" in caplog.text
    status = reconcile.boot_reconcile_status()
    assert status.state == "failed"
    assert status.error.startswith("orphaned runs: ")
