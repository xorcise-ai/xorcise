"""`up` / `down` / `db upgrade` agree on ONE question — is a server of this home running? —
and answer it from more than the pid file.

Three defects shared a root (#72, #73, #74). `down` unlinked the pid file even when its kill
failed, so a server it could not stop became unfindable forever. `up` guarded on the pid file
alone, so with that file stale or gone a live sibling on the REST port read as a generic busy
port: `up` auto-incremented past it and booted a SECOND instance over the same SQLite file —
after preparing that shared DB. And `db upgrade` had no liveness check at all, while the only
boot-time guard pointed straight at it ("run 'xorcise db upgrade' first") in exactly the state
where an older server may still be serving. The pinned behaviours below are the fixes.
"""

from __future__ import annotations

import errno
import os

import pytest
import typer
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
from xorcise.core.cli._shared import app
from xorcise.core.cli.commands import lifecycle

pytestmark = pytest.mark.unit

runner = CliRunner()


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_REST_PORT", raising=False)
    monkeypatch.delenv("XORCISE_OTLP_PORT", raising=False)
    (tmp_path / "logs").mkdir(parents=True)
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    # No real network in a unit test: nothing answers on any port, unless a test says so.
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda host, port: None)
    monkeypatch.setattr(lifecycle, "_foreign_instance_home", lambda host, port: None)
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [])
    monkeypatch.setattr("xorcise.core.rest.reap.reap_managed_containers", lambda settings, **kw: [])
    yield tmp_path
    get_settings.cache_clear()


def _instance(pid: int | None, rest: int = 3001, otlp: int = 4318) -> dict[str, object]:
    """What `/api/system` of an instance of THIS home answers."""
    return {
        "home": os.environ["XORCISE_HOME"],
        "pid": pid,
        "planes": [
            {"name": "rest", "location": f"127.0.0.1:{rest}"},
            {"name": "otlp", "location": f"127.0.0.1:{otlp}"},
        ],
    }


# ------------------------------------------------------------------------------- down (#72)


def test_down_keeps_the_pid_file_and_fails_when_the_kill_is_refused(home, monkeypatch) -> None:
    """PermissionError used to be swallowed into the success path: pid file unlinked, "stopped"
    printed, exit 0 — and the still-running server could never be named again."""
    (home / "xorcise.pid").write_text("1")

    def refuse(pid, sig):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "kill", refuse)
    # The server IS alive on its port (so this is not pid reuse) — but reports no pid.
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda h, p: _instance(None))
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 1
    assert "could not stop XORCISE (pid 1)" in result.output
    assert "left in place" in result.output
    assert (home / "xorcise.pid").exists(), "a failed kill must not erase the only record"


def test_down_treats_an_unsignalable_pid_with_no_server_behind_it_as_stale(home, monkeypatch):
    """pid reuse: the pid file names a root-owned process, but nothing of this home answers on
    any port — the server is long gone, the record is stale, and `down` is a clean no-op."""
    (home / "xorcise.pid").write_text("1")

    def refuse(pid, sig):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "kill", refuse)
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "pid reuse" in result.output
    assert not (home / "xorcise.pid").exists()


def test_down_fails_when_the_process_survives_sigkill(home, monkeypatch) -> None:
    (home / "xorcise.pid").write_text("4242")
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)  # every signal "delivered", never dies
    monkeypatch.setattr(lifecycle, "_await_exit", lambda pid, **kw: False)
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 1
    assert "did not exit after SIGKILL" in result.output
    assert (home / "xorcise.pid").exists()


def test_down_stops_an_orphaned_instance_the_pid_file_does_not_name(home, monkeypatch) -> None:
    """The #73 aftermath: the pid file names the SECOND instance; the first (orphaned by the
    overwrite) still serves the configured port. `down` used to kill only the recorded pid and
    report "stopped" while the orphan kept serving."""
    (home / "xorcise.pid").write_text("2000")
    (home / "runtime-ports.json").write_text('{"rest": 3002, "otlp": 4319}')
    signalled: list[int] = []
    alive = {2000, 1000}

    def kill(pid, sig):
        if pid not in alive:
            raise ProcessLookupError
        if sig != 0:
            signalled.append(pid)
            alive.discard(pid)  # exits on the first signal

    monkeypatch.setattr(os, "kill", kill)
    # The orphan answers on the CONFIGURED port (3001) and reports its pid; 3002 is now free.
    monkeypatch.setattr(
        lifecycle,
        "_same_home_instance_on",
        lambda h, p: _instance(1000) if p == 3001 and 1000 in alive else None,
    )
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert signalled == [2000, 1000]  # the recorded pid, then the orphan it found by port
    assert "orphaned instance of this home (pid 1000)" in result.output
    assert alive == set()


def test_down_refuses_to_claim_stopped_when_an_old_instance_cannot_name_its_pid(home, monkeypatch):
    (home / "xorcise.pid").write_text("2000")
    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda h, p: _instance(None))
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 1
    assert "still answers on port 3001" in result.output
    assert "ss -tlnp" in result.output


def test_down_names_a_foreign_holder_of_the_port_without_failing(home, monkeypatch) -> None:
    """Another home's instance on the default ports is a fact, not a failure of THIS down."""
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [3001])
    monkeypatch.setattr(lifecycle, "_foreign_instance_home", lambda h, p: "/other/home")
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "held by the XORCISE instance at /other/home" in result.output


# --------------------------------------------------------------------------------- up (#73)


def _up_never_spawns(monkeypatch) -> None:
    import subprocess

    def boom(*a, **k):
        raise AssertionError("up must not spawn a second server")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(lifecycle, "_require_prerequisites", lambda *, stub=False: None)


def test_up_adopts_a_live_instance_when_the_pid_file_is_gone(home, monkeypatch) -> None:
    """The record was lost (a failed `down`, #72) but this home's server still answers on the
    REST port: converge, repair the record from what it reports — never boot a second one."""
    _up_never_spawns(monkeypatch)
    monkeypatch.setattr(lifecycle, "_ensure_db_ready", lambda: pytest.fail("DB touched"))
    monkeypatch.setattr(
        lifecycle, "_same_home_instance_on", lambda h, p: _instance(777, rest=3001, otlp=4318)
    )
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert "already running (pid record repaired)" in result.output
    assert (home / "xorcise.pid").read_text() == "777"
    from xorcise.core.home import read_runtime_ports

    assert read_runtime_ports() == {"rest": 3001, "otlp": 4318}


def test_up_adopts_a_live_instance_when_the_pid_file_is_stale(home, monkeypatch) -> None:
    _up_never_spawns(monkeypatch)
    (home / "xorcise.pid").write_text("999999")  # nobody home at this pid

    def kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda h, p: _instance(778))
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert (home / "xorcise.pid").read_text() == "778"


def test_up_looks_where_the_lost_instance_could_be(home, monkeypatch) -> None:
    """The configured port, the runtime record's port and `--port` are all asked."""
    _up_never_spawns(monkeypatch)
    (home / "runtime-ports.json").write_text('{"rest": 3005}')
    asked: list[int] = []

    def answer(h, p):
        asked.append(p)
        return _instance(779, rest=p) if p == 3009 else None

    monkeypatch.setattr(lifecycle, "_same_home_instance_on", answer)
    result = runner.invoke(app, ["up", "--port", "3009"])
    assert result.exit_code == 0
    assert asked == [3001, 3005, 3009]
    assert (home / "xorcise.pid").read_text() == "779"


def test_up_still_boots_when_only_a_foreign_instance_holds_the_port(home, monkeypatch) -> None:
    """A different home on the default port is what auto-increment is for — proceed."""
    import subprocess
    from types import SimpleNamespace

    import httpx

    monkeypatch.setattr(lifecycle, "_require_prerequisites", lambda *, stub=False: None)
    monkeypatch.setattr(lifecycle, "ensure_frontend_ready", lambda console: None)
    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: {**wanted, "rest": 3002})
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=4321))
    monkeypatch.setattr(httpx, "get", lambda url, timeout=1: SimpleNamespace(status_code=200))
    # `_same_home_instance_on` is already "nobody of THIS home" from the fixture.
    result = runner.invoke(app, ["up", "--stub"])
    assert result.exit_code == 0, result.output
    assert "busy" in result.output and "3002" in result.output
    assert (home / "xorcise.pid").read_text() == "4321"


# --------------------------------------------------------------------------- db upgrade (#74)


def test_db_upgrade_refuses_while_a_server_of_this_home_is_running(home, monkeypatch) -> None:
    (home / "xorcise.pid").write_text(str(os.getpid()))  # a live pid: this test process
    ran: list[int] = []

    def _upgrade() -> str:
        ran.append(1)
        return "ran"

    monkeypatch.setattr("xorcise.core.cli.commands.db._upgrade", _upgrade)
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 1
    assert f"XORCISE is running (pid {os.getpid()})" in result.output
    assert "xorcise down" in result.output
    assert ran == []


def test_db_upgrade_refuses_when_only_the_port_reveals_the_server(home, monkeypatch) -> None:
    """No pid file (the #72 aftermath) — the REST port still says an instance is up."""
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda h, p: _instance(None))
    monkeypatch.setattr("xorcise.core.cli.commands.db._upgrade", lambda: "ran")
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 1
    assert "port 3001" in result.output


def test_db_upgrade_force_overrides_the_guard(home, monkeypatch) -> None:
    (home / "xorcise.pid").write_text(str(os.getpid()))
    monkeypatch.setattr("xorcise.core.cli.commands.db._upgrade", lambda: "migrations: ran")
    result = runner.invoke(app, ["db", "upgrade", "--force"])
    assert result.exit_code == 0
    assert "migrations: ran" in result.output


def test_db_upgrade_runs_when_nothing_is_live(home, monkeypatch) -> None:
    monkeypatch.setattr("xorcise.core.cli.commands.db._upgrade", lambda: "migrations: ran")
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 0


def test_stale_db_refusal_says_to_stop_the_server_first(home, monkeypatch, capsys) -> None:
    """The one boot-time guard used to route operators straight into the unguarded path."""
    from xorcise.core import db

    monkeypatch.setattr(db, "boot_state", lambda: "stale")
    with pytest.raises(typer.Exit):
        lifecycle._ensure_db_ready()
    assert "xorcise down" in capsys.readouterr().err
