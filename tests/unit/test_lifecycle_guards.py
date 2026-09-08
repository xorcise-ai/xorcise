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
    monkeypatch.setattr(httpx, "get", lambda url, **k: SimpleNamespace(status_code=200))
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


# ------------------------------------------------------------ review follow-ups (#82)


def test_down_probes_the_recorded_port_even_though_it_unlinks_the_record(home, monkeypatch):
    """The review's blocker. `_candidate_rest_ports` read runtime-ports.json, but `down` unlinked
    that file before the sweep — so the instance that auto-incremented to 3002 (exactly the #73
    shape) was never probed and "stopped" was printed over it. The candidates are read first now."""
    (home / "xorcise.pid").write_text("999999")  # stale: a dead pid
    (home / "runtime-ports.json").write_text('{"rest": 3002, "otlp": 4319}')
    killed: list[int] = []
    alive = {4242}

    def kill(pid, sig):
        if pid not in alive:
            raise ProcessLookupError
        if sig != 0:
            killed.append(pid)
            alive.discard(pid)

    monkeypatch.setattr(os, "kill", kill)
    # Only 3002 answers, reporting a live pid 4242.
    monkeypatch.setattr(
        lifecycle,
        "_same_home_instance_on",
        lambda h, p: _instance(4242, rest=3002) if p == 3002 and 4242 in alive else None,
    )
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0, result.output
    assert killed == [4242]
    assert "orphaned instance of this home (pid 4242)" in result.output
    assert not (home / "runtime-ports.json").exists()


def test_down_verifies_release_on_the_recorded_ports_too(home, monkeypatch):
    (home / "runtime-ports.json").write_text('{"rest": 3002, "otlp": 4319}')
    probed: list[list[int]] = []

    def record(h: str, ports: list[int]) -> list[int]:
        probed.append(ports)
        return []

    monkeypatch.setattr(lifecycle, "ports_in_use", record)
    assert runner.invoke(app, ["down"]).exit_code == 0
    assert probed == [[3001, 3002, 4318, 4319]]


def test_up_adopting_an_instance_without_a_pid_says_so_and_names_the_right_port(home, monkeypatch):
    """Review: with no pid reported nothing was repaired, yet `up` printed "pid record repaired"
    and advertised the CONFIGURED port's URL for an instance found on another port."""
    _up_never_spawns(monkeypatch)
    (home / "runtime-ports.json").write_text('{"rest": 3005}')  # a relocated instance, pid lost
    monkeypatch.setattr(
        lifecycle,
        "_same_home_instance_on",
        lambda h, p: _instance(None, rest=3005) if p == 3005 else None,
    )
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert "repaired" not in result.output
    assert "pid is not known" in result.output
    assert ":3005/ui" in result.output and ":3001/ui" not in result.output
    assert not (home / "xorcise.pid").exists()  # nothing to write
    from xorcise.core.home import read_runtime_ports

    assert read_runtime_ports() == {"rest": 3005, "otlp": 4318}  # what lets `down` find it


def test_an_undetermined_port_fails_up_closed(home, monkeypatch):
    """A port of ours accepted but did not answer (hung server, proxy stall): booting past it is
    the duplicate-instance outcome the guard exists for, so `up` refuses and says why."""
    _up_never_spawns(monkeypatch)
    monkeypatch.setattr(lifecycle, "_ensure_db_ready", lambda: pytest.fail("DB touched"))
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda h, p: lifecycle.UNDETERMINED)
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 1
    assert "did not answer /api/system" in result.output and "port 3001" in result.output


def test_an_undetermined_port_fails_db_upgrade_closed_and_force_overrides(home, monkeypatch):
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda h, p: lifecycle.UNDETERMINED)
    monkeypatch.setattr("xorcise.core.cli.commands.db._upgrade", lambda: "migrations: ran")
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 1 and "Not migrating" in result.output
    assert runner.invoke(app, ["db", "upgrade", "--force"]).exit_code == 0


def test_an_undetermined_port_fails_down_closed(home, monkeypatch):
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda h, p: lifecycle.UNDETERMINED)
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 1
    assert "did not answer /api/system" in result.output


def test_db_upgrade_guard_holds_for_a_direct_call(home, monkeypatch):
    """Review: `bool(typer.Option(False))` is True, so `if force:` skipped the guard whenever
    db_upgrade() was called directly (a test, a script, a future internal caller)."""
    from xorcise.core.cli.commands import db as db_cmd

    (home / "xorcise.pid").write_text(str(os.getpid()))
    ran: list[int] = []

    def _upgrade() -> str:
        ran.append(1)
        return "ran"

    monkeypatch.setattr(db_cmd, "_upgrade", _upgrade)
    with pytest.raises(typer.Exit):
        db_cmd.db_upgrade()  # default = the OptionInfo, not False
    assert ran == []


@pytest.mark.parametrize("bad", [True, 0, -1, 2**40, "4242", None])
def test_pids_from_a_json_body_are_validated_before_any_signal(home, monkeypatch, bad):
    """Review: `isinstance(True, int)` is True and os.kill(0, SIGTERM) signals the caller's whole
    process group. Nothing from /api/system reaches os.kill without `_valid_pid`."""
    signalled: list[int] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append(pid))
    monkeypatch.setattr(lifecycle, "_same_home_instance_on", lambda h, p: _instance(bad))
    result = runner.invoke(app, ["down"])
    assert signalled == []
    assert result.exit_code == 1 and "usable pid" in result.output


def test_a_pid_file_past_c_long_is_stale_not_a_traceback(home, monkeypatch):
    """Review: a huge value raised OverflowError from os.kill, which `except ValueError` did not
    catch — `up`/`down`/`db upgrade` died with a raw traceback."""
    (home / "xorcise.pid").write_text(str(10**29))
    _up_never_spawns(monkeypatch)
    import subprocess
    from types import SimpleNamespace

    import httpx

    monkeypatch.setattr(lifecycle, "ensure_frontend_ready", lambda console: None)
    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: dict(wanted))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=4321))
    monkeypatch.setattr(httpx, "get", lambda url, timeout=1, **k: SimpleNamespace(status_code=200))
    result = runner.invoke(app, ["up", "--stub"])
    assert result.exit_code == 0, result.output  # the corrupt record was dropped, not fatal
    assert (home / "xorcise.pid").read_text() == "4321"
    result = runner.invoke(app, ["down"])  # and down reads it as nothing to signal
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_probes_never_use_the_proxy_environment(monkeypatch):
    """Review: httpx defaults to trust_env=True with no loopback bypass, so HTTP_PROXY diverted the
    probe and a dead proxy read as "nothing running" — the failure the probe exists to prevent."""
    import httpx

    seen: list[dict[str, object]] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"home": os.environ.get("XORCISE_HOME", ""), "pid": 4242}

    def fake_get(url, **kwargs):
        seen.append(kwargs)
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    lifecycle._probe_system("127.0.0.1", 3001)
    lifecycle._foreign_instance_home("127.0.0.1", 3001)
    assert seen and all(k.get("trust_env") is False for k in seen)


def test_probe_distinguishes_refused_from_undetermined(monkeypatch):
    import httpx

    def refused(url, **k):
        raise httpx.ConnectError("refused")

    def timed_out(url, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(httpx, "get", refused)
    assert lifecycle._probe_system("127.0.0.1", 3001) is None  # nothing there
    monkeypatch.setattr(httpx, "get", timed_out)
    assert lifecycle._probe_system("127.0.0.1", 3001) is lifecycle.UNDETERMINED  # cannot tell

    class _Resp:
        def __init__(self, status: int, body: object) -> None:
            self.status_code, self._body = status, body

        def json(self) -> object:
            if isinstance(self._body, ValueError):
                raise self._body
            return self._body

    # A plain web service on the port (HTML 404) is FOREIGN, not undetermined — `up` must still
    # auto-increment past it rather than refuse to boot behind it forever.
    monkeypatch.setattr(httpx, "get", lambda url, **k: _Resp(404, ValueError("not json")))
    assert lifecycle._probe_system("127.0.0.1", 3001) is None
    # A 5xx is a server that exists and is broken — possibly a sick instance of ours: cannot tell.
    monkeypatch.setattr(httpx, "get", lambda url, **k: _Resp(500, {"detail": "boom"}))
    assert lifecycle._probe_system("127.0.0.1", 3001) is lifecycle.UNDETERMINED
