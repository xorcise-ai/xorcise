import os
import subprocess

import httpx
import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands/callback on shared app
from xorcise.core.cli._diagnostics import Check
from xorcise.core.cli._shared import app
from xorcise.core.cli.commands import lifecycle
from xorcise.core.cli.commands import mission as mission_cmd
from xorcise.core.cli.rest_client import RestClient
from xorcise.core.config import REST_PORT
from xorcise.core.headscale import provision
from xorcise.core.roles.boot import AppSpec

runner = CliRunner()


@pytest.fixture
def _prereqs_ok(monkeypatch):
    """`up` gates on host prerequisites before it writes anything; these tests
    exercise the port/db/config path, so treat the host as ready."""
    monkeypatch.setattr(lifecycle, "_require_prerequisites", lambda *, stub=False: None)
    return None


def test_rest_client_uses_base_url():
    c = RestClient(base_url="http://example.test")
    assert c.base_url == "http://example.test"


def test_rest_client_surfaces_json_error_detail(monkeypatch, capsys):
    # an error status with a JSON {"detail": ...} body → clean message + exit 1.
    import typer

    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: httpx.Response(409, json={"detail": "image not built — run ingest"}),
    )
    import pytest

    with pytest.raises(typer.Exit) as ei:
        RestClient().post("/runs", json={})
    assert ei.value.exit_code == 1
    # Errors ride stderr so scripted callers can separate data from diagnostics.
    assert "image not built — run ingest" in capsys.readouterr().err


def test_rest_client_handles_nonjson_error_without_decode_crash(monkeypatch):
    # a text/plain 500 must NOT raise JSONDecodeError — it exits cleanly.
    import typer

    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: httpx.Response(500, text="Internal Server Error")
    )
    import pytest

    with pytest.raises(typer.Exit) as ei:
        RestClient().post("/runs", json={})
    assert ei.value.exit_code == 1


def test_rest_client_read_timeout_is_clean_error_not_traceback(monkeypatch, capsys):
    # a slow endpoint (ReadTimeout) must exit cleanly, never dump an httpx traceback.
    import typer

    def _boom(*a, **k):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "post", _boom)
    import pytest

    with pytest.raises(typer.Exit) as ei:
        RestClient().post("/runs/x/terminate", json={})
    assert ei.value.exit_code == 1
    assert "did not respond" in capsys.readouterr().err


def test_default_base_url_honors_configured_rest_port(monkeypatch, tmp_path):
    """An explicit XORCISE_REST_PORT outranks the runtime record — even with a server up.

    XORCISE_HOME is redirected because otherwise this reads the developer's real
    ~/.xorcise: with a local server running on a relocated port, the runtime record
    would answer instead of the port the caller asked for.
    """
    from xorcise.core.cli.rest_client import default_base_url
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setenv("XORCISE_HOST", "0.0.0.0")
    monkeypatch.setenv("XORCISE_REST_PORT", "4001")
    (tmp_path / "xorcise.pid").write_text("4321")
    (tmp_path / "runtime-ports.json").write_text('{"rest": 3002}')
    get_settings.cache_clear()
    assert default_base_url() == "http://0.0.0.0:4001/api"


def test_agent_list_renders(monkeypatch):
    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path: [
            {"id": "beta-uuid-1", "name": "beta", "kind": "claude-code", "version": 2},
            {"id": "alpha-uuid-2", "name": "alpha"},
        ],
    )
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0
    assert "alpha" in result.stdout and "beta" in result.stdout
    assert "Claude Code" in result.stdout  # kind slug → readable label
    assert "Custom" in result.stdout  # missing kind reads as Custom
    assert "v1" in result.stdout and "v2" in result.stdout
    # internal ids stay hidden by default (only --verbose/--json expose them)
    assert "uuid" not in result.stdout
    # rows sort by name
    assert result.stdout.index("alpha") < result.stdout.index("beta")


def test_run_create_renders(monkeypatch):
    # create validates both names up front (GET /agents + GET /missions) before POSTing.
    def fake_get(self, path):
        if path == "/agents":
            return [{"name": "a"}]
        return [{"mission_id": "c", "name": "C", "installed": True}]

    monkeypatch.setattr(RestClient, "get", fake_get)
    monkeypatch.setattr(RestClient, "post", lambda self, path, json: {"run_id": "run-001"})
    result = runner.invoke(app, ["run", "create", "--agent", "a", "--mission", "c"])
    assert result.exit_code == 0
    assert "run-001" in result.stdout


def test_mission_list_renders(monkeypatch):
    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path: [
            {"source": "your_own", "mission_id": "myown", "name": "Mine", "installed": True},
            {
                "source": "library",
                "mission_id": "sqli",
                "name": "SQLi",
                "installed": False,
                "proficiency": "easy",
            },
        ],
    )
    result = runner.invoke(app, ["mission", "list"])
    assert result.exit_code == 0
    # table cells, never exact layout: source labels, ids, names, difficulty, state words
    assert "Your Own" in result.stdout and "Library" in result.stdout
    assert "myown" in result.stdout and "Mine" in result.stdout
    assert "sqli" in result.stdout and "SQLi" in result.stdout
    assert "Easy" in result.stdout  # difficulty is title-cased
    assert "Installed" in result.stdout and "Available" in result.stdout


def test_mission_pull_renders(monkeypatch):
    # pull resolves the entry, starts a pull job, polls it to 'installed', and reports.
    posted: dict[str, object] = {}

    def fake_post(self, path, json=None, timeout=None):
        posted["path"] = path
        return {"job_id": "j1"}

    def fake_get(self, path):
        if path == "/missions":
            return [{"source": "library", "mission_id": "sqli", "name": "SQLi", "installed": False}]
        assert path == "/missions/pull-jobs/j1"
        return {"status": "installed", "phase": "done", "entry": {"name": "SQLi"}}

    monkeypatch.setattr(RestClient, "post", fake_post)
    monkeypatch.setattr(RestClient, "get", fake_get)
    result = runner.invoke(app, ["mission", "pull", "sqli"])
    assert result.exit_code == 0
    assert posted["path"] == "/missions/sqli/pull-jobs"
    # guards the entry['mission_id'] read + the success render (regression site)
    assert "installed 'SQLi' (sqli)" in result.stdout


def test_mission_pull_ctrl_c_cancels_server_side_and_exits_130(monkeypatch):
    # Ctrl-C during a pull must stop the job server-side (POST .../cancel), not just detach the
    # CLI — then exit 130 (interrupted).
    posts: list[str] = []

    def fake_get(self, path):
        assert path == "/missions"
        return [{"source": "library", "mission_id": "sqli", "name": "SQLi", "installed": False}]

    def fake_post(self, path, json=None, timeout=None):
        posts.append(path)
        return {"job_id": "j1"}

    def interrupt(_client, _job_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(RestClient, "get", fake_get)
    monkeypatch.setattr(RestClient, "post", fake_post)
    monkeypatch.setattr(mission_cmd, "_watch_pull", interrupt)
    result = runner.invoke(app, ["mission", "pull", "sqli"])
    assert result.exit_code == 130
    assert "/missions/sqli/pull-jobs" in posts  # the pull was started
    assert "/missions/pull-jobs/j1/cancel" in posts  # …and then cancelled server-side


def test_mission_pull_ctrl_c_exits_130_even_if_cancel_post_fails(monkeypatch):
    # A server that vanished mid-pull must not mask the interrupt: a failed cancel POST is
    # swallowed and the exit stays 130.
    import typer

    def fake_get(self, path):
        return [{"source": "library", "mission_id": "sqli", "name": "SQLi", "installed": False}]

    def fake_post(self, path, json=None, timeout=None):
        if path.endswith("/cancel"):
            raise typer.Exit(1)  # RestClient's clean-exit on an unreachable server
        return {"job_id": "j1"}

    def interrupt(_client, _job_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(RestClient, "get", fake_get)
    monkeypatch.setattr(RestClient, "post", fake_post)
    monkeypatch.setattr(mission_cmd, "_watch_pull", interrupt)
    result = runner.invoke(app, ["mission", "pull", "sqli"])
    assert result.exit_code == 130


def test_mission_pull_double_ctrl_c_during_cancel_still_exits_130(monkeypatch):
    # An impatient second Ctrl-C landing inside the cancel POST must not escape the interrupt
    # handler and downgrade the exit code — it stays 130.
    def fake_get(self, path):
        return [{"source": "library", "mission_id": "sqli", "name": "SQLi", "installed": False}]

    def fake_post(self, path, json=None, timeout=None):
        if path.endswith("/cancel"):
            raise KeyboardInterrupt  # the second Ctrl-C, mid cancel POST
        return {"job_id": "j1"}

    def interrupt(_client, _job_id):
        raise KeyboardInterrupt  # the first Ctrl-C, during the pull watch

    monkeypatch.setattr(RestClient, "get", fake_get)
    monkeypatch.setattr(RestClient, "post", fake_post)
    monkeypatch.setattr(mission_cmd, "_watch_pull", interrupt)
    result = runner.invoke(app, ["mission", "pull", "sqli"])
    assert result.exit_code == 130


def test_mission_pull_external_cancel_reports_and_exits_0(monkeypatch):
    # A cancel by another client (GUI / second CLI) flips the watched job to terminal 'cancelled'.
    # The CLI must report a clean not-installed stop and exit 0 — NOT the "still pulling" exit 3
    # fall-through, and NOT exit 1 (that's for errors).
    def fake_get(self, path):
        if path == "/missions":
            return [{"source": "library", "mission_id": "sqli", "name": "SQLi", "installed": False}]
        assert path == "/missions/pull-jobs/j1"
        return {"status": "cancelled", "phase": "pulling_image"}

    def fake_post(self, path, json=None, timeout=None):
        return {"job_id": "j1"}

    monkeypatch.setattr(RestClient, "get", fake_get)
    monkeypatch.setattr(RestClient, "post", fake_post)
    result = runner.invoke(app, ["mission", "pull", "sqli"])
    assert result.exit_code == 0
    assert "cancelled" in result.output
    assert "still pulling" not in result.output  # the exit-3 fall-through must NOT fire


def test_mission_delete_calls_delete_endpoint(monkeypatch):
    # `xorcise mission delete <id>` uninstalls via DELETE /missions/{id}.
    seen: dict[str, str] = {}

    def fake_delete(self, path):  # noqa: ANN001, ANN202 — test stub
        seen["path"] = path
        return None

    # delete resolves the id against the live list first
    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path: [{"mission_id": "sqli", "name": "SQLi", "installed": True}],
    )
    monkeypatch.setattr(RestClient, "delete", fake_delete)
    result = runner.invoke(app, ["mission", "delete", "sqli"])
    assert result.exit_code == 0
    assert seen["path"] == "/missions/sqli"
    assert "deleted mission 'sqli'" in result.stdout


def test_rest_client_post_forwards_custom_timeout(monkeypatch):
    # A caller can override the default 5s read timeout for long ops (e.g. a real image pull).
    captured: dict[str, object] = {}

    def fake_post(url, json, timeout):
        captured["timeout"] = timeout
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "post", fake_post)
    RestClient("http://x").post("/p", json={}, timeout=300.0)
    assert captured["timeout"] == 300.0


def test_mission_pull_already_installed_is_noop(monkeypatch):
    # Replaces the old single-POST generous-timeout contract: a long pull is now a background
    # job that the CLI polls (covered above); an already-installed mission is a safe no-op —
    # success without ever starting a job.
    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path: [{"mission_id": "sqli", "name": "SQLi", "installed": True}],
    )

    def no_post(self, path, json=None, timeout=None):
        raise AssertionError("pull must not start a job for an installed mission")

    monkeypatch.setattr(RestClient, "post", no_post)
    result = runner.invoke(app, ["mission", "pull", "sqli"])
    assert result.exit_code == 0
    assert "'SQLi' is already installed" in result.stdout


def test_catalog_status_renders(monkeypatch):
    """status reads the SETTING from /config and the LIVE state from the probe."""
    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path, timeout=None: {"catalog": {"connected": False, "url": None}},
    )
    monkeypatch.setattr(RestClient, "get_or_none", lambda self, path, timeout=None: None)
    result = runner.invoke(app, ["catalog", "status"])
    assert result.exit_code == 0
    assert "Configuration: Disabled" in result.stdout
    assert "xorcise catalog connect" in result.stdout


def test_apply_runtime_env_sets_role_and_stub(monkeypatch):
    from xorcise.core.cli.commands.lifecycle import _apply_runtime_env
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_ROLE", "all")  # register for restoration (no leak)
    monkeypatch.setenv("XORCISE_USE_STUBS", "0")
    _apply_runtime_env("runner", stub=True)
    assert get_settings().role == "runner"
    assert get_settings().use_stubs is True


def test_serve_help_exposes_role():
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--role" in result.output


def test_role_list_lists_manifest_roles():
    result = runner.invoke(app, ["role", "list"])
    assert result.exit_code == 0
    for name in ("all", "control", "runner", "headscale", "collector"):
        assert name in result.output


def test_server_down_is_clean_error_not_traceback(monkeypatch):
    """A REST command run before `xorcise up` exits cleanly with a hint, not a traceback."""

    def refused(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", refused)
    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 1
    assert "cannot reach the XORCISE service" in result.output
    assert "xorcise up" in result.output
    # the connect error is translated, not leaked as an unhandled exception
    assert not isinstance(result.exception, httpx.ConnectError)


def test_serve_banner_omits_ui_for_restless_role():
    banner = lifecycle._serve_banner("collector", [AppSpec(app=object(), port=4318)])
    assert "collector" in banner
    assert "UI at" not in banner


def test_serve_banner_shows_ui_when_rest_present():
    banner = lifecycle._serve_banner("all", [AppSpec(app=object(), port=REST_PORT)])
    assert "UI at" in banner


def test_next_steps_block_lists_first_actions():
    block = lifecycle.next_steps_block("http://127.0.0.1:3001/ui")
    assert "http://127.0.0.1:3001/ui" in block
    assert "Next steps" in block
    assert "xorcise agent register" in block
    assert "xorcise mission list" in block
    assert "xorcise run create" in block


def test_up_fails_fast_when_no_free_port(_prereqs_ok, monkeypatch, tmp_path):
    # Busy ports auto-increment now; only an exhausted scan window is a hard failure.
    from xorcise.core.cli._preflight import PortScanError

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))

    def no_free(host, wanted):
        raise PortScanError("rest", REST_PORT, 50)

    monkeypatch.setattr(lifecycle, "resolve_ports", no_free)

    # If up tried to spawn, this would explode — proves we fail BEFORE spawning.
    def boom(*a, **k):
        raise AssertionError("up must not spawn serve when no port is free")

    monkeypatch.setattr(subprocess, "Popen", boom)
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 1
    assert "no free port" in result.output
    assert str(REST_PORT) in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_serve_fails_fast_on_port_conflict(monkeypatch, tmp_path):
    from xorcise.core.cli.commands import serve as serve_mod

    # Identity resolution keeps this hermetic (no real bind probes / env stamping).
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))  # serve bootstraps home+db now
    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: dict(wanted))
    monkeypatch.setattr(serve_mod, "activate", lambda role: [AppSpec(app=object(), port=REST_PORT)])
    monkeypatch.setattr(serve_mod, "ports_in_use", lambda host, ports: [REST_PORT])
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    assert str(REST_PORT) in result.output
    assert "in use" in result.output


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "xorcise" in result.output


def test_help_lists_lifecycle_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("up", "serve", "status", "doctor", "down"):
        assert cmd in result.output


def test_init_command_removed():
    result = runner.invoke(app, ["init"])
    assert result.exit_code != 0  # no such command — bootstrap lives on `up`


def test_up_scaffolds_config_on_first_run(_prereqs_ok, monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))

    class _Proc:
        pid = 4321

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc())

    class _Resp:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: dict(wanted))
    # keep this scaffolding test Docker-free — neutralise the local-Headscale step.
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: type("C", (), {"ok": True})())
    monkeypatch.setattr(
        provision, "ensure_up", lambda wd, cp, **k: provision.ProvisionResult("u", "c", "a", "1")
    )
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert (tmp_path / "config.toml").exists()
    assert (tmp_path / ".env").exists()


def test_up_scaffolds_fresh_db(_prereqs_ok, monkeypatch, tmp_path):
    from sqlalchemy import inspect

    from xorcise.core import config, db

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()

    class _Proc:
        pid = 4321

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc())

    class _Resp:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: dict(wanted))
    # keep this scaffolding test Docker-free — neutralise the local-Headscale step.
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: type("C", (), {"ok": True})())
    monkeypatch.setattr(
        provision, "ensure_up", lambda wd, cp, **k: provision.ProvisionResult("u", "c", "a", "1")
    )

    result = runner.invoke(app, ["up"])
    assert result.exit_code == 0
    assert "prepared the database" in result.output.lower()
    assert inspect(db.get_engine()).has_table("agents")


def test_up_refuses_when_db_behind_head(_prereqs_ok, monkeypatch, tmp_path):
    from sqlalchemy import text

    from xorcise.core import config, db

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.upgrade()
    # simulate a DB stamped by an older build: its revision differs from this head
    with db.get_engine().begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = '0000_older_build'"))

    def boom(*a, **k):
        raise AssertionError("up must not spawn serve against a behind/data DB")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: dict(wanted))

    result = runner.invoke(app, ["up"])
    assert result.exit_code == 1
    assert "xorcise db upgrade" in result.output


def test_status_table_all_ok_exits_zero(monkeypatch):
    monkeypatch.setattr(
        lifecycle, "probe_channel", lambda name, url, ok_statuses=(200,): Check(name, True, "ok")
    )
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    # service rows carry user-facing labels + state words, not probe names
    assert "REST API" in result.output
    assert "Docker" in result.output
    assert "Healthy" in result.output
    assert "All services are healthy." in result.output


def test_status_exits_nonzero_when_a_channel_down(monkeypatch):
    monkeypatch.setattr(
        lifecycle, "probe_channel", lambda name, url, ok_statuses=(200,): Check(name, False, "down")
    )
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "Stopped" in result.output  # the 'down' probe detail reads as Stopped
    assert "not responding" in result.output  # summary names the down services


def test_doctor_reports_missing_docker_and_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        lifecycle, "docker_present", lambda: Check("docker", False, "missing", "install Docker")
    )
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [])
    monkeypatch.setattr(lifecycle, "home_present", lambda: Check("~/.xorcise", True, "ok"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "Docker is not installed" in result.output  # friendly Environment line
    assert "install Docker" in result.output  # remediation rides the ✗ line


def test_doctor_names_taken_port(monkeypatch, tmp_path):
    # tmp home: no pid file → no running server → a taken port is a real conflict.
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setattr(lifecycle, "docker_present", lambda: Check("docker", True, "present"))
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [REST_PORT])
    monkeypatch.setattr(lifecycle, "home_present", lambda: Check("~/.xorcise", True, "ok"))
    # The holder is a plain process, not another XORCISE instance (that case is
    # attributed by name instead — see test_doctor_attributes_a_foreign_instance).
    monkeypatch.setattr(lifecycle, "_foreign_instance_home", lambda host, port: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert str(REST_PORT) in result.output
    assert "in use" in result.output


def test_doctor_reports_missing_home(monkeypatch):
    monkeypatch.setattr(lifecycle, "docker_present", lambda: Check("docker", True, "present"))
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [])
    monkeypatch.setattr(
        lifecycle,
        "home_present",
        lambda: Check("~/.xorcise", False, "missing", "run 'xorcise up' to bootstrap it"),
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "xorcise up" in result.output


def test_doctor_all_ok_exits_zero(host_probes_ok, monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))  # no pid file: nothing expected up
    monkeypatch.setattr(lifecycle, "docker_present", lambda: Check("docker", True, "present"))
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [])
    monkeypatch.setattr(lifecycle, "home_present", lambda: Check("~/.xorcise", True, "ok"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0


def test_down_default_clears_transient_keeps_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "logs").mkdir()
    (tmp_path / "config.toml").write_text("k=1")
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert not (tmp_path / "logs").exists()
    assert (tmp_path / "config.toml").exists()


def test_down_keep_data_preserves_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "logs").mkdir()
    result = runner.invoke(app, ["down", "--keep-data"])
    assert result.exit_code == 0
    assert (tmp_path / "logs").exists()


def test_down_purge_yes_removes_home(monkeypatch, tmp_path):
    home = tmp_path / ".xorcise"
    home.mkdir()
    (home / "config.toml").write_text("k=1")
    monkeypatch.setenv("XORCISE_HOME", str(home))
    result = runner.invoke(app, ["down", "--purge", "--yes"])
    assert result.exit_code == 0
    assert not home.exists()


def test_down_purge_abort_on_no(monkeypatch, tmp_path):
    from xorcise.core.cli.commands import lifecycle

    home = tmp_path / ".xorcise"
    home.mkdir()
    (home / "config.toml").write_text("k=1")
    monkeypatch.setenv("XORCISE_HOME", str(home))
    monkeypatch.setattr(lifecycle, "_stdin_is_interactive", lambda: True)
    result = runner.invoke(app, ["down", "--purge"], input="n\n")
    assert result.exit_code == 1
    assert home.exists()
    assert "aborted" in result.output


def test_down_purge_non_interactive_needs_yes(monkeypatch, tmp_path):
    """A non-TTY caller without --yes must fail fast (exit 2), never hang on a prompt."""
    home = tmp_path / ".xorcise"
    home.mkdir()
    (home / "config.toml").write_text("k=1")
    monkeypatch.setenv("XORCISE_HOME", str(home))
    result = runner.invoke(app, ["down", "--purge"])  # CliRunner stdin is not a TTY
    assert result.exit_code == 2
    assert home.exists()
    assert "--yes" in result.stderr


def test_down_keep_data_and_purge_is_error(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    result = runner.invoke(app, ["down", "--keep-data", "--purge"])
    assert result.exit_code == 2


def test_down_reaps_orphaned_run_containers(monkeypatch, tmp_path):
    """Down reaps xorcise-managed per-run containers and reports the count."""
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setattr(
        "xorcise.core.rest.reap.reap_managed_containers",
        lambda settings, **kw: ["run-a", "run-b"],
    )
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "reaped 2" in result.output


def test_down_not_running_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "not running" in result.output


def test_down_running_stops_server_and_reports(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "logs").mkdir(parents=True)
    pf = tmp_path / "xorcise.pid"
    pf.write_text("999999")
    killed: dict[str, int] = {}
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.update(pid=pid, sig=sig))
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert "xorcise down" in result.output
    assert not pf.exists()
    assert killed["pid"] == 999999


def test_db_upgrade_runs_seam(tmp_path, monkeypatch):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core import config, db

    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 0
    assert "migrations" in result.output.lower()


def test_serve_preflight_uses_configured_host_and_ports(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))  # serve bootstraps home+db now
    monkeypatch.setenv("XORCISE_HOST", "0.0.0.0")
    monkeypatch.setenv("XORCISE_REST_PORT", "4001")
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    seen = {}

    def fake_ports_in_use(host, ports):
        seen["host"] = host
        seen["ports"] = ports
        return [4001]  # force the fast-fail path, no uvicorn

    from xorcise.core.cli.commands import serve as serve_mod

    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: dict(wanted))
    monkeypatch.setattr(serve_mod, "ports_in_use", fake_ports_in_use)
    try:
        result = runner.invoke(app, ["serve"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert seen["host"] == "0.0.0.0"
    assert 4001 in seen["ports"]


def test_scaffolded_config_documents_endpoint_knobs(monkeypatch, tmp_path):
    from xorcise.core.home import scaffold_config

    scaffold_config(tmp_path)
    text = (tmp_path / "config.toml").read_text()
    for knob in ("rest_port", "otlp_port", "runner_port", "headscale_port", "host"):
        assert knob in text
    assert "3001" in text  # the rest_port default, rendered from the constant


def test_agent_register_posts_declaration(monkeypatch):
    captured: dict[str, object] = {}

    def fake_post(self, path, json):
        captured["path"] = path
        captured["json"] = json
        return {"id": "x", "name": json["name"]}

    # register pre-checks duplicates via GET /agents before POSTing
    monkeypatch.setattr(RestClient, "get", lambda self, path: [])
    monkeypatch.setattr(RestClient, "post", fake_post)
    result = runner.invoke(app, ["agent", "register", "--name", "alpha", "--endpoint", "http://a"])
    assert result.exit_code == 0
    # model is optional; omitted ⇒ None (registers at version 1 with model undisclosed).
    assert captured["json"] == {
        "name": "alpha",
        "endpoint": "http://a",
        "otel": None,
        "model": None,
        "kind": None,
        "launch_mode": None,
    }


def test_agent_register_with_model_posts_model(monkeypatch):
    # --model discloses the agent's model at registration (no update/version bump needed).
    captured: dict[str, object] = {}

    def fake_post(self, path, json):
        captured["path"] = path
        captured["json"] = json
        return {"id": "x", "name": json["name"]}

    monkeypatch.setattr(RestClient, "get", lambda self, path: [])  # duplicate pre-check
    monkeypatch.setattr(RestClient, "post", fake_post)
    result = runner.invoke(
        app,
        ["agent", "register", "--name", "a1", "--endpoint", "http://a", "--model", "m1"],
    )
    assert result.exit_code == 0
    assert captured["path"] == "/agents"
    assert captured["json"] == {
        "name": "a1",
        "endpoint": "http://a",
        "otel": None,
        "model": "m1",
        "kind": None,
        "launch_mode": None,
    }


def test_agent_register_posts_launch_mode(monkeypatch):
    captured: dict[str, dict[str, object]] = {}

    monkeypatch.setattr(RestClient, "get", lambda self, path: [])

    def fake_post(self, path, json):
        captured["json"] = json
        return {"id": "x", "name": json["name"]}

    monkeypatch.setattr(RestClient, "post", fake_post)
    result = runner.invoke(
        app,
        ["agent", "register", "--name", "boxed", "--launch-mode", "container"],
    )
    assert result.exit_code == 0
    assert captured["json"]["launch_mode"] == "container"


def test_agent_register_with_kind_posts_kind(monkeypatch):
    captured: dict[str, dict[str, object]] = {}

    def fake_post(self, path, json):
        captured["json"] = json
        return {"id": "x", "name": json["name"]}

    monkeypatch.setattr(RestClient, "get", lambda self, path: [])  # duplicate pre-check
    monkeypatch.setattr(RestClient, "post", fake_post)
    result = runner.invoke(app, ["agent", "register", "--name", "scout", "--kind", "openhands"])
    assert result.exit_code == 0
    assert captured["json"]["kind"] == "openhands"


def test_agent_update_puts_declaration(monkeypatch):
    """update merges the passed options onto the current declaration — an update that
    sets only --model must not silently clear endpoint/otel/kind."""
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path: [
            {
                "id": "x",
                "name": "a1",
                "endpoint": "http://old",
                "otel": "grpc://o",
                "model": "m1",
                "kind": "openhands",
            }
        ],
    )

    def fake_put(self, path, json):
        captured["path"] = path
        captured["json"] = json
        return {"id": "x", "name": json["name"], "version": 2}

    monkeypatch.setattr(RestClient, "put", fake_put)
    result = runner.invoke(
        app, ["agent", "update", "--name", "a1", "--endpoint", "http://y", "--model", "m2"]
    )
    assert result.exit_code == 0
    assert captured["path"] == "/agents/a1"
    assert captured["json"] == {
        "name": "a1",
        "endpoint": "http://y",
        "otel": "grpc://o",  # preserved, not cleared
        "model": "m2",
        "kind": "openhands",  # preserved, not cleared
        "launch_command_template": None,
        "launch_tips": None,
        "mission_preamble": None,
        "launch_mode": None,
    }


def test_agent_update_unknown_agent_is_clean_error(monkeypatch):
    monkeypatch.setattr(RestClient, "get", lambda self, path: [])
    result = runner.invoke(app, ["agent", "update", "--name", "ghost", "--model", "m"])
    assert result.exit_code == 1
    assert "no agent named 'ghost'" in result.output


def test_agent_rm_calls_delete(monkeypatch):
    captured: dict[str, object] = {}
    # rm resolves the name against the live list first; CliRunner stdin is
    # non-interactive, so the TTY confirm never prompts.
    monkeypatch.setattr(RestClient, "get", lambda self, path: [{"name": "alpha"}])
    monkeypatch.setattr(RestClient, "delete", lambda self, path: captured.setdefault("path", path))
    result = runner.invoke(app, ["agent", "rm", "alpha"])
    assert result.exit_code == 0
    assert captured["path"] == "/agents/alpha"
    assert "removed agent 'alpha'" in result.stdout


def _history_get(name: str, entries: list[dict[str, object]]):
    """A path-keyed GET stub: history resolves the name via /agents, then reads
    /runs + /missions to label each row with its mission display name."""

    def fake_get(self, path):
        if path == "/agents":
            return [{"name": name}]
        if path == "/runs":
            return [{"run_id": e["run_id"], "mission": f"chal-{e['run_id']}"} for e in entries]
        if path == "/missions":
            return []
        assert path == f"/agents/{name}/history"
        return entries

    return fake_get


def test_agent_history_lists_runs_with_version_labels(monkeypatch):
    entries = [
        {
            "run_id": "r1",
            "agent_id": "a",
            "overall": 0.4,
            "deterministic": 0.6,
            "judge": 0.2,
            "trace_ref": "r1",
            "created_at": "2026-06-01T00:00:00Z",
            "conditions": {
                "model": None,  # undisclosed model reads as 'not disclosed'
                "judge_model": None,
                "budget_seconds": 60,
                "sandbox_ref": "img:1",
                "agent_version": 1,
                "mission_version": 1,
            },
        },
        {
            "run_id": "r2",
            "agent_id": "a",
            "overall": 0.7,
            "deterministic": 0.8,
            "judge": 0.6,
            "trace_ref": "r2",
            "created_at": "2026-06-02T00:00:00Z",
            "conditions": {
                "model": "m2",
                "judge_model": "jm",
                "budget_seconds": 60,
                "sandbox_ref": "img:2",
                "agent_version": 2,
                "mission_version": 2,
            },
        },
    ]
    monkeypatch.setattr(RestClient, "get", _history_get("myagent", entries))
    result = runner.invoke(app, ["agent", "history", "myagent"])
    assert result.exit_code == 0
    assert "r1" in result.stdout and "r2" in result.stdout
    # version labels ride their own Agent/Mission table columns now
    assert "v1" in result.stdout and "v2" in result.stdout
    assert "0.4" in result.stdout and "0.7" in result.stdout
    assert "not disclosed" in result.stdout and "m2" in result.stdout


def test_agent_history_single_run_renders_cleanly(monkeypatch):
    one = [
        {
            "run_id": "r1",
            "agent_id": "a",
            "overall": 0.4,
            "deterministic": 0.6,
            "judge": 0.2,
            "trace_ref": "r1",
            "created_at": "2026-06-01T00:00:00Z",
            "conditions": {
                "model": "m1",
                "judge_model": None,
                "budget_seconds": 60,
                "sandbox_ref": "img:1",
                "agent_version": 1,
                "mission_version": 1,
            },
        }
    ]
    monkeypatch.setattr(RestClient, "get", _history_get("solo", one))
    result = runner.invoke(app, ["agent", "history", "solo"])
    assert result.exit_code == 0
    assert "r1" in result.stdout
    # no broken "comparison" framing on a single run — no delta/vs/previous language
    out = result.stdout.lower()
    assert "vs" not in out
    assert "previous" not in out
    assert "delta" not in out


def test_agent_history_empty_prints_no_results_message(monkeypatch):
    monkeypatch.setattr(RestClient, "get", _history_get("nobody", []))
    result = runner.invoke(app, ["agent", "history", "nobody"])
    assert result.exit_code == 0
    assert "no recorded results" in result.stdout


def test_agent_history_marks_partial_run_with_badge(monkeypatch):
    """Agent history badges a partial (timed-out) run's overall score."""
    entries = [
        {
            "run_id": "r-partial",
            "agent_id": "a",
            "overall": 0.3,
            "deterministic": 0.5,
            "judge": 0.1,
            "trace_ref": "r-partial",
            "created_at": "2026-06-01T00:00:00Z",
            "partial": True,
            "partial_trigger": "timeout",
            "conditions": {
                "model": "m1",
                "judge_model": None,
                "budget_seconds": 60,
                "sandbox_ref": "img:1",
                "agent_version": 1,
                "mission_version": 1,
            },
        }
    ]
    monkeypatch.setattr(RestClient, "get", _history_get("myagent", entries))
    result = runner.invoke(app, ["agent", "history", "myagent"])
    assert result.exit_code == 0
    assert "⚠ partial" in result.stdout


def test_agent_history_no_partial_badge_on_clean_run(monkeypatch):
    """Agent history must NOT badge a clean (done) run.

    No-false-positive guard: the partial marker must be absent when partial=False.
    """
    entries = [
        {
            "run_id": "r-clean",
            "agent_id": "a",
            "overall": 1.0,
            "deterministic": 1.0,
            "judge": 1.0,
            "trace_ref": "r-clean",
            "created_at": "2026-06-02T00:00:00Z",
            "partial": False,
            "partial_trigger": None,
            "conditions": {
                "model": "m2",
                "judge_model": "jm",
                "budget_seconds": 120,
                "sandbox_ref": "img:2",
                "agent_version": 1,
                "mission_version": 1,
            },
        }
    ]
    monkeypatch.setattr(RestClient, "get", _history_get("myagent", entries))
    result = runner.invoke(app, ["agent", "history", "myagent"])
    assert result.exit_code == 0
    # No-false-positive: clean run must NOT show the partial badge
    assert "⚠ partial" not in result.stdout


# --- port management (auto-increment + runtime-ports discovery) ---


def test_up_and_serve_expose_port_flags():
    for cmd in ("up", "serve"):
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0
        for flag in ("--port", "--otlp-port"):
            assert flag in result.output


def test_apply_runtime_env_stamps_only_relocated_ports(monkeypatch, tmp_path):
    from xorcise.core.cli.commands.lifecycle import _apply_runtime_env
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setenv("XORCISE_ROLE", "all")  # register for restoration (no leak)
    monkeypatch.delenv("XORCISE_REST_PORT", raising=False)
    monkeypatch.delenv("XORCISE_OTLP_PORT", raising=False)
    get_settings.cache_clear()
    try:
        otlp = get_settings().otlp_port
        _apply_runtime_env("all", stub=False, ports={"rest": 3002, "otlp": otlp})
        assert os.environ["XORCISE_REST_PORT"] == "3002"
        # an unchanged plane is not stamped — config stays its source of truth
        assert "XORCISE_OTLP_PORT" not in os.environ
        assert get_settings().rest_port == 3002
    finally:
        get_settings.cache_clear()


def test_up_auto_increments_and_records_runtime_ports(_prereqs_ok, monkeypatch, tmp_path):
    """A busy rest port relocates with a notice; env, health poll, UI URL and the
    runtime-ports.json record all follow the resolved port."""
    import json

    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_REST_PORT", raising=False)
    monkeypatch.delenv("XORCISE_ROLE", raising=False)
    get_settings.cache_clear()

    class _Proc:
        pid = 4321

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc())

    polled: dict[str, str] = {}

    class _Resp:
        status_code = 200

    def fake_get(url, timeout=1):
        polled["url"] = url
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(lifecycle, "ensure_frontend_ready", lambda console: None)
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: type("C", (), {"ok": True})())
    monkeypatch.setattr(
        provision, "ensure_up", lambda wd, cp, **k: provision.ProvisionResult("u", "c", "a", "1")
    )
    monkeypatch.setattr(
        lifecycle, "resolve_ports", lambda host, wanted: {**wanted, "rest": wanted["rest"] + 1}
    )
    try:
        result = runner.invoke(app, ["up"])
        assert result.exit_code == 0
        assert "3001" in result.output and "3002" in result.output and "busy" in result.output
        assert os.environ["XORCISE_REST_PORT"] == "3002"
        assert ":3002" in polled["url"]  # health poll targets the relocated port
        assert "3002/ui" in result.output  # printed UI URL follows the resolved port
        record = json.loads((tmp_path / "runtime-ports.json").read_text())
        assert record["rest"] == 3002
    finally:
        get_settings.cache_clear()


def test_serve_port_flag_resolves_before_activate(monkeypatch, tmp_path):
    """--port feeds resolution, and the resolved port is stamped BEFORE activate()
    builds the specs (role boot captures get_settings() ports at build time)."""
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))  # serve bootstraps home+db now
    monkeypatch.setenv("XORCISE_ROLE", "all")
    monkeypatch.delenv("XORCISE_REST_PORT", raising=False)
    wanted_seen: dict[str, int] = {}
    seen: dict[str, int] = {}

    def fake_resolve(host, wanted):
        wanted_seen.update(wanted)
        return {**wanted, "rest": 4445}

    monkeypatch.setattr(lifecycle, "resolve_ports", fake_resolve)

    def fake_activate(role):
        seen["rest_port_at_activate"] = get_settings().rest_port
        return [AppSpec(app=object(), port=4445)]

    from xorcise.core.cli.commands import serve as serve_mod

    monkeypatch.setattr(serve_mod, "activate", fake_activate)
    # report everything busy so serve fast-fails before reaching uvicorn
    monkeypatch.setattr(serve_mod, "ports_in_use", lambda host, ports: ports)
    try:
        result = runner.invoke(app, ["serve", "--port", "4001"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 1
    assert wanted_seen["rest"] == 4001  # the flag beats the configured port
    assert seen["rest_port_at_activate"] == 4445  # stamped before specs were built
    assert "using 4445" in result.output  # the relocation notice names the new port


def test_down_clears_runtime_ports_record(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "xorcise.pid").write_text("999999")
    (tmp_path / "runtime-ports.json").write_text('{"rest": 3002}')

    def fake_kill(pid, sig):
        if sig == 0:  # liveness probe — report the server gone so down returns fast
            raise ProcessLookupError

    monkeypatch.setattr(os, "kill", fake_kill)
    result = runner.invoke(app, ["down"])
    assert result.exit_code == 0
    assert not (tmp_path / "runtime-ports.json").exists()


def test_default_base_url_overlays_runtime_rest_port(monkeypatch, tmp_path):
    from xorcise.core.cli.rest_client import default_base_url
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_HOST", raising=False)
    monkeypatch.delenv("XORCISE_REST_PORT", raising=False)
    get_settings.cache_clear()
    (tmp_path / "xorcise.pid").write_text("4321")
    (tmp_path / "runtime-ports.json").write_text('{"rest": 3002}')
    try:
        assert default_base_url() == "http://127.0.0.1:3002/api"
    finally:
        get_settings.cache_clear()


def test_default_base_url_ignores_runtime_record_when_server_down(monkeypatch, tmp_path):
    # No pid file → a stale record must not redirect the CLI off the configured port.
    from xorcise.core.cli.rest_client import default_base_url
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_HOST", raising=False)
    monkeypatch.delenv("XORCISE_REST_PORT", raising=False)
    get_settings.cache_clear()
    (tmp_path / "runtime-ports.json").write_text('{"rest": 3002}')
    try:
        assert default_base_url() == f"http://127.0.0.1:{REST_PORT}/api"
    finally:
        get_settings.cache_clear()


def test_ui_prints_runtime_resolved_url(monkeypatch, tmp_path):
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.delenv("XORCISE_HOST", raising=False)
    monkeypatch.delenv("XORCISE_REST_PORT", raising=False)
    get_settings.cache_clear()
    (tmp_path / "xorcise.pid").write_text("4321")
    (tmp_path / "runtime-ports.json").write_text('{"rest": 3002}')
    try:
        result = runner.invoke(app, ["ui"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0
    assert "http://127.0.0.1:3002/ui" in result.output


def test_status_probes_runtime_resolved_ports(monkeypatch, tmp_path):
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    get_settings.cache_clear()
    (tmp_path / "xorcise.pid").write_text("4321")
    (tmp_path / "runtime-ports.json").write_text('{"rest": 3002, "otlp": 4319}')
    urls: dict[str, str] = {}

    def fake_probe(name, url, ok_statuses=(200,)):
        urls[name] = url
        return Check(name, True, "ok")

    monkeypatch.setattr(lifecycle, "probe_channel", fake_probe)
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    try:
        result = runner.invoke(app, ["status"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0
    assert ":3002" in urls["rest"]
    assert ":4319" in urls["otlp"]


def test_mission_ingest_is_disabled_and_never_calls_the_server(monkeypatch, tmp_path):
    """Ingest is not part of this release: it explains itself and touches nothing.

    Locked deliberately. The command still exists so `xorcise mission ingest` answers
    rather than erroring as an unknown command, but it must not reach the server — a
    half-disabled surface that still POSTs would build an image nobody can run.
    """

    def boom(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("disabled ingest must not call the server")

    monkeypatch.setattr("xorcise.core.cli.commands.mission.RestClient.post", boom)
    monkeypatch.setattr("xorcise.core.cli.commands.mission.RestClient.get", boom)

    for argv in (["mission", "ingest"], ["mission", "ingest", str(tmp_path)]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 0, argv
        assert "coming soon" in result.output.lower()
        assert "xorcise mission pull" in result.output

    # A path that does NOT exist must get the same answer — the operator's problem is the
    # feature being unavailable, not their argument.
    missing = runner.invoke(app, ["mission", "ingest", str(tmp_path / "nope")])
    assert missing.exit_code == 0
    assert "coming soon" in missing.output.lower()
