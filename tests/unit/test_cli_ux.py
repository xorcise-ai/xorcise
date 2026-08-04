"""CLI revamp guarantees: banner gating, the main() error guard,
--json parseability, stderr discipline, and the SIGINT-safe headscale exec message."""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app as cli_app  # noqa: F401 — registers commands on shared app
from xorcise import __version__
from xorcise.core.cli._diagnostics import Check
from xorcise.core.cli._shared import app
from xorcise.core.cli.commands import lifecycle

runner = CliRunner()


# --- banner gating -----------------------------------------------------------


def test_bare_invocation_piped_shows_help_without_banner():
    """Non-TTY bare `xorcise` gets the help, never the brand header glyphs."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage" in result.output
    assert "⊕" not in result.output  # the crosshair mark never reaches a pipe
    assert "Trust Evidence" not in result.output.split("Usage")[0]


def test_help_never_shows_banner():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "⊕" not in result.output


def test_version_is_exactly_the_version_line():
    """--version is a machine-read artifact: one plain line, no banner, no styling."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout == f"xorcise {__version__}\n"


def test_root_help_teaches_the_golden_path():
    result = runner.invoke(app, ["--help"])
    assert "Get started" in result.output
    assert "run create" in result.output
    # The exit-code contract moved OFF the root screen (first-run guide, not a
    # contract document) — it lives on the command groups that need it.
    assert "Exit codes" not in result.output
    # Hidden stubs must not advertise themselves on the v1.0 surface.
    for hidden in ("Manage remote xorcise instances", "Authentication helpers"):
        assert hidden not in result.output


def test_exit_code_contract_lives_on_run_and_status_help():
    for args in (["run", "--help"], ["status", "--help"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert "Exit codes" in result.output, f"missing exit-code reference on {args}"


# --- main() error guard ------------------------------------------------------


def _invoke_main(monkeypatch, argv: list[str]):
    monkeypatch.setattr("sys.argv", ["xorcise", *argv])
    return cli_app.main()


def test_main_guard_turns_known_errors_into_one_line(monkeypatch, capsys):
    from xorcise.core.headscale.provision import ProvisionError

    def boom():
        raise ProvisionError("compose failed on port 443")

    monkeypatch.setattr("xorcise.core.roles.registry.load_manifest", boom)
    monkeypatch.delenv("XORCISE_DEBUG", raising=False)
    with pytest.raises(SystemExit) as ei:
        _invoke_main(monkeypatch, ["role", "list"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "error" in err
    assert "compose failed on port 443" in err
    assert "Traceback" not in err


def test_main_guard_debug_reraises(monkeypatch):
    from xorcise.core.headscale.provision import ProvisionError

    def boom():
        raise ProvisionError("compose failed")

    monkeypatch.setattr("xorcise.core.roles.registry.load_manifest", boom)
    monkeypatch.setenv("XORCISE_DEBUG", "1")
    with pytest.raises(ProvisionError):
        _invoke_main(monkeypatch, ["role", "list"])


def test_main_guard_unexpected_error_mentions_debug_hatch(monkeypatch, capsys):
    def boom():
        raise ValueError("surprise")

    monkeypatch.setattr("xorcise.core.roles.registry.load_manifest", boom)
    monkeypatch.delenv("XORCISE_DEBUG", raising=False)
    with pytest.raises(SystemExit) as ei:
        _invoke_main(monkeypatch, ["role", "list"])
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "unexpected error" in err
    assert "XORCISE_DEBUG=1" in err


def test_db_upgrade_history_mismatch_is_a_targeted_error(monkeypatch):
    """A DB stamped by a different build (a revision id this build's migration
    chain doesn't know) must explain itself, not surface as 'unexpected error'."""
    from alembic.util.exc import CommandError

    from xorcise.core.cli.commands import db as db_cmd

    def boom():
        raise CommandError("Can't locate revision identified by '9999_other_build'")

    monkeypatch.setattr(db_cmd, "_upgrade", boom)
    result = runner.invoke(app, ["db", "upgrade"])
    assert result.exit_code == 1
    assert "migration history mismatch" in result.stderr
    assert "9999_other_build" in result.stderr
    assert "different build" in result.stderr


# --- serve --role is a closed enum -------------------------------------------


def test_serve_rejects_bogus_role_as_usage_error():
    result = runner.invoke(app, ["serve", "--role", "bogus"])
    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert "--role" in result.stderr


def test_serve_role_help_is_not_swallowed_as_a_value():
    """`serve --role --help` used to crash with UnknownRoleError('--help')."""
    result = runner.invoke(app, ["serve", "--role", "--help"])
    assert result.exit_code == 2  # usage error, never a traceback
    assert "UnknownRoleError" not in result.output
    assert "Traceback" not in result.output


def test_serve_scaffolds_fresh_db_before_boot(monkeypatch, tmp_path):
    """A bare `serve` in a fresh home must scaffold the schema — it used to boot
    'healthy' against a 0-byte DB and 500 on every DB-backed call."""
    from xorcise.core.cli.commands import serve as serve_mod
    from xorcise.core.roles.boot import AppSpec

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: dict(wanted))
    monkeypatch.setattr(serve_mod, "activate", lambda role: [AppSpec(app=object(), port=45501)])
    # Everything 'busy' forces the fast-fail exit AFTER the bootstrap ran — uvicorn never starts.
    monkeypatch.setattr(serve_mod, "ports_in_use", lambda host, ports: ports)
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1  # the deliberate port-conflict exit
    assert "prepared the database" in result.output
    from xorcise.core import db
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    db.get_engine.cache_clear()
    assert db.boot_state() != "fresh"  # tables exist now


def test_root_help_panel_order_puts_getting_started_first():
    result = runner.invoke(app, ["--help"])
    order = [
        result.output.index(panel)
        for panel in ("Getting started", "Evaluate", "Configuration", "Advanced")
    ]
    assert order == sorted(order), f"panels out of order: {order}"


# --- status/doctor --json ----------------------------------------------------


def test_status_json_shape_and_ports(monkeypatch):
    monkeypatch.setattr(
        lifecycle, "probe_channel", lambda name, url, ok_statuses=(200,): Check(name, True, "ok")
    )
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert set(body["ports"]) == {"rest", "otlp"}
    assert [c["name"] for c in body["checks"]] == ["rest", "otlp", "docker"]


def test_status_json_still_exits_nonzero_when_down(monkeypatch):
    monkeypatch.setattr(
        lifecycle, "probe_channel", lambda name, url, ok_statuses=(200,): Check(name, False, "down")
    )
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False


def test_doctor_recognises_its_own_server_ports(host_probes_ok, monkeypatch, tmp_path):
    """Right after `xorcise up`, the server's own ports must read as health, not as
    'stop the process using it'."""
    import json as _json
    import os as _os

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "xorcise.pid").write_text(str(_os.getpid()))  # a live pid: this test process
    (tmp_path / "runtime-ports.json").write_text(_json.dumps({"rest": 3001, "otlp": 4318}))
    monkeypatch.setattr(lifecycle, "docker_present", lambda: Check("docker", True, "present"))
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [3001, 4318])
    monkeypatch.setattr(lifecycle, "home_present", lambda: Check("~/.xorcise", True, "ok"))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "in use by the running XORCISE service" in result.output
    assert "stop the process" not in result.output


def test_doctor_json_shape(host_probes_ok, monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))  # no pid file: nothing expected up
    monkeypatch.setattr(lifecycle, "docker_present", lambda: Check("docker", True, "present"))
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [])
    monkeypatch.setattr(lifecycle, "home_present", lambda: Check("~/.xorcise", True, "ok"))
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ok"] is True
    assert all({"name", "ok", "detail", "remediation"} <= set(c) for c in body["checks"])


# --- stderr discipline -------------------------------------------------------


def test_server_down_error_rides_stderr_stdout_stays_empty(monkeypatch):
    def _down(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _down)
    result = runner.invoke(app, ["run", "list", "--json"])
    assert result.exit_code == 1
    assert result.stdout == ""  # a parser reading stdout sees nothing, not prose
    assert "cannot reach the XORCISE service" in result.stderr
    assert "xorcise up" in result.stderr


# --- --json emits raw DTOs ---------------------------------------------------


def test_run_list_json_is_the_raw_array(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    runs = [{"run_id": "r1", "state": "terminal"}]
    monkeypatch.setattr(RestClient, "get", lambda self, path: runs)
    result = runner.invoke(app, ["run", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == runs


def test_run_list_json_empty_is_empty_array(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    monkeypatch.setattr(RestClient, "get", lambda self, path: [])
    result = runner.invoke(app, ["run", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_agent_list_json_is_the_raw_array(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    agents = [{"id": "x", "name": "n", "version": 1}]
    monkeypatch.setattr(RestClient, "get", lambda self, path: agents)
    result = runner.invoke(app, ["agent", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == agents


def test_mission_list_json_is_the_raw_array(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    missions = [{"source": "library", "mission_id": "c", "name": "C", "installed": False}]
    monkeypatch.setattr(RestClient, "get", lambda self, path: missions)
    result = runner.invoke(app, ["mission", "list", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == missions


# --- headscale exec misreport fix --------------------------------------------


def test_headscale_exec_error_labels_streams_never_bare_stdout(monkeypatch):
    """Exit 255 with a success message on stdout must never read as 'failed: Policy updated.'"""
    import subprocess

    from xorcise.core.headscale import cli as hs_cli

    class FakeProc:
        returncode = 255
        stdout = "Policy updated."
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(hs_cli.HeadscaleError) as ei:
        hs_cli.DockerExecHeadscaleCli()._exec("policy", "set", "-f", "/x")
    msg = str(ei.value)
    assert msg.startswith("headscale policy set -f /x failed (exit 255)")
    assert "stderr: <empty>" in msg
    assert "stdout: Policy updated." in msg


def test_headscale_exec_runs_in_its_own_session(monkeypatch):
    """start_new_session: an operator Ctrl-C must not SIGINT a mid-flight policy write."""
    import subprocess

    from xorcise.core.headscale import cli as hs_cli

    captured: dict[str, object] = {}

    class FakeProc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    hs_cli.DockerExecHeadscaleCli()._exec("version")
    assert captured.get("start_new_session") is True


# --- verbatim artifacts ------------------------------------------------------


def test_launch_cmd_block_is_verbatim(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    block = 'export A="1"\nexport B="x y z"\nclaude -p "very long prompt ' + "x" * 200 + '"'
    monkeypatch.setattr(RestClient, "get", lambda self, path: {"shell_block": block})
    # A full 32-char id skips prefix resolution (no extra /runs call).
    result = runner.invoke(app, ["run", "launch-cmd", "r" * 32])
    assert result.exit_code == 0
    assert result.stdout == block + "\n"  # no wrapping, no styling, nothing injected


# --- compact usage errors + suggestions ---------------------------------------


def test_group_without_subcommand_shows_help_and_exits_zero():
    """A bare group invocation is a help request, not a mistake."""
    for group in ("agent", "mission", "run", "config", "catalog", "role", "db"):
        result = runner.invoke(app, [group])
        assert result.exit_code == 0, f"`xorcise {group}` should exit 0"
        assert "Usage" in result.stdout, f"`xorcise {group}` should print its help"


def test_usage_errors_are_compact_no_rich_box():
    result = runner.invoke(app, ["agent", "register"])
    assert result.exit_code == 2
    assert "╭" not in result.stderr  # no wide red panel
    assert "error" in result.stderr
    assert "xorcise agent register --name my-agent" in result.stderr


def test_option_that_is_really_a_subcommand_gets_did_you_mean():
    result = runner.invoke(app, ["agent", "--list"])
    assert result.exit_code == 2
    assert "Did you mean?" in result.stderr
    assert "xorcise agent list" in result.stderr


def test_extra_arg_that_is_a_sibling_subcommand_gets_did_you_mean():
    result = runner.invoke(app, ["agent", "register", "history", "--name", "codex"])
    assert result.exit_code == 2
    assert "xorcise agent history" in result.stderr


def test_env_var_after_command_explains_placement():
    result = runner.invoke(app, ["db", "upgrade", "XORCISE_DEBUG=1"])
    assert result.exit_code == 2
    assert "environment variable" in result.stderr
    assert "XORCISE_DEBUG=1 xorcise db upgrade" in result.stderr


def test_unquoted_mission_name_suggests_quoting():
    result = runner.invoke(app, ["mission", "show", "Aviary", "Access"])
    assert result.exit_code == 2
    assert "quoted" in result.stderr
    assert 'xorcise mission show "Aviary Access"' in result.stderr


def test_run_create_missing_inputs_explained_together():
    result = runner.invoke(app, ["run", "create"])
    assert result.exit_code == 2
    assert "an agent and a mission are required" in result.stderr
    assert "xorcise agent list" in result.stderr
    assert "xorcise mission list" in result.stderr


def test_unknown_command_suggests_the_close_match():
    result = runner.invoke(app, ["mision"])
    assert result.exit_code == 2
    assert "mission" in result.stderr  # typer's built-in Did-you-mean survives


# --- presentation kit ---------------------------------------------------------


def test_humanize_when_today_yesterday_and_older():
    """Anchored to `now` (not fixed instants) so the machine's local timezone
    can never move a timestamp across a date boundary under the test."""
    from datetime import UTC, datetime, timedelta

    from xorcise.core.cli._ux import humanize_when

    now = datetime.now(UTC)
    assert humanize_when(now.isoformat(), now=now).startswith("Today ")
    assert humanize_when((now - timedelta(hours=24)).isoformat(), now=now).startswith("Yesterday ")
    older = humanize_when((now - timedelta(days=40)).isoformat(), now=now)
    assert not older.startswith(("Today", "Yesterday"))
    assert humanize_when(None, now=now) == "—"


def test_run_state_labels_mirror_the_gui_vocabulary():
    from xorcise.core.cli._ux import run_state_label

    assert run_state_label("terminal", "done") == "Completed"
    assert run_state_label("terminal", "timeout") == "Timed out"
    assert run_state_label("terminal", "crashed") == "Crashed"
    assert run_state_label("terminal", "operator") == "Terminated"
    assert run_state_label("terminal", "budget") == "Partial"
    assert run_state_label("terminal", "error") == "Failed"
    assert run_state_label("active") == "Running"
    assert run_state_label("created") == "Created"


def test_kind_labels_read_as_products():
    from xorcise.core.cli._ux import kind_label

    assert kind_label("claude-code") == "Claude Code"
    assert kind_label("codex") == "Codex"
    assert kind_label(None) == "Custom"


# --- short run-id prefixes ----------------------------------------------------


def _patch_runs(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, str]]) -> None:
    from xorcise.core.cli.rest_client import RestClient

    monkeypatch.setattr(RestClient, "get", lambda self, path: runs)


def test_short_run_prefix_resolves_uniquely(monkeypatch):
    from xorcise.core.cli._resolve import resolve_run_id
    from xorcise.core.cli.rest_client import RestClient

    _patch_runs(
        monkeypatch,
        [{"run_id": "41d8f18b" + "0" * 24}, {"run_id": "669f9fb7" + "0" * 24}],
    )
    assert resolve_run_id(RestClient(), "41d8") == "41d8f18b" + "0" * 24


def test_ambiguous_run_prefix_lists_candidates(monkeypatch, capsys):
    import typer as _typer

    from xorcise.core.cli._resolve import resolve_run_id
    from xorcise.core.cli.rest_client import RestClient

    _patch_runs(monkeypatch, [{"run_id": "41d8aaaa" + "0" * 24}, {"run_id": "41d8bbbb" + "0" * 24}])
    with pytest.raises(_typer.Exit):
        resolve_run_id(RestClient(), "41d8")
    err = capsys.readouterr().err
    assert "ambiguous" in err
    assert "41d8aaaa" in err and "41d8bbbb" in err


def test_unknown_run_prefix_points_at_run_list(monkeypatch, capsys):
    import typer as _typer

    from xorcise.core.cli._resolve import resolve_run_id
    from xorcise.core.cli.rest_client import RestClient

    _patch_runs(monkeypatch, [{"run_id": "41d8f18b" + "0" * 24}])
    with pytest.raises(_typer.Exit):
        resolve_run_id(RestClient(), "zzzz")
    assert "xorcise run list" in capsys.readouterr().err


# --- usage-mistake and port-override guards -----------------------------------


def test_config_setters_reject_nothing_to_set():
    """Bare set-* is a usage mistake, not a silent no-op write (no network call)."""
    setters = (["config", "set-model"], ["config", "set-terrain-model"], ["config", "set-network"])
    for cmd in setters:
        result = runner.invoke(app, cmd)
        assert result.exit_code == 2, f"{cmd} should exit 2"
        assert "nothing to set" in result.stderr


def test_mission_list_bogus_filter_is_a_usage_error(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    missions = [
        {"source": "library", "mission_id": "a", "name": "A", "proficiency": "expert"},
        {"source": "your_own", "mission_id": "b", "name": "B", "proficiency": "hard"},
    ]
    monkeypatch.setattr(RestClient, "get", lambda self, path: missions)
    result = runner.invoke(app, ["mission", "list", "--difficulty", "impossible"])
    assert result.exit_code == 2
    # The offer names everything that would have worked: the levels this catalog
    # actually carries, and the built-in ladder. Asserted as membership rather than
    # as a fixed prefix — the old assertion pinned 'available: expert, hard', which
    # only held because the retired vocabulary happened to sort after both.
    offered = result.stderr.split("available: ", 1)[1].split("\n", 1)[0]
    assert {"expert", "hard"} <= set(offered.split(", "))  # this catalog's own levels
    assert "competent" in offered  # the ladder, so a valid level is never hidden
    assert "connect the catalog" not in result.output  # never blame a healthy catalog
    result = runner.invoke(app, ["mission", "list", "--source", "nowhere"])
    assert result.exit_code == 2
    assert "available: library, your-own" in result.stderr  # documented spelling


def test_status_env_port_override_outranks_runtime_record(monkeypatch, tmp_path):
    """XORCISE_REST_PORT is a 'talk to THIS port' instruction — status must probe it,
    matching the data commands' endpoint (they already honored the env)."""
    import json as _json
    import os as _os

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setenv("XORCISE_REST_PORT", "45999")
    (tmp_path / "xorcise.pid").write_text(str(_os.getpid()))
    (tmp_path / "runtime-ports.json").write_text(_json.dumps({"rest": 3001, "otlp": 4318}))
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        lifecycle, "probe_channel", lambda name, url, ok_statuses=(200,): Check(name, True, "ok")
    )
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    result = runner.invoke(app, ["status", "--json"])
    get_settings.cache_clear()
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["ports"]["rest"] == 45999  # env wins over the runtime record
    assert body["ports"]["otlp"] == 4318  # unoverridden planes keep the record


def test_doctor_probes_the_relocated_server_ports(host_probes_ok, monkeypatch, tmp_path):
    """A server up on relocated ports must be probed where it RUNS, not on config
    defaults another process may hold."""
    import json as _json
    import os as _os

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "xorcise.pid").write_text(str(_os.getpid()))
    (tmp_path / "runtime-ports.json").write_text(_json.dumps({"rest": 46400, "otlp": 46402}))
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    probed: dict[str, list[int]] = {}
    monkeypatch.setattr(lifecycle, "docker_present", lambda: Check("docker", True, "present"))
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))

    def fake_ports_in_use(host, ports):
        probed["ports"] = list(ports)
        return list(ports)  # everything 'taken' — must all read as server-owned

    monkeypatch.setattr(lifecycle, "ports_in_use", fake_ports_in_use)
    result = runner.invoke(app, ["doctor"])
    get_settings.cache_clear()
    assert result.exit_code == 0
    assert probed["ports"] == [46400, 46402]
    assert result.output.count("in use by the running XORCISE service") == 2


def test_agent_resolver_never_suggests_registering_a_command_word(monkeypatch, capsys):
    import typer as _typer

    from xorcise.core.cli._resolve import resolve_agent_name
    from xorcise.core.cli.rest_client import RestClient

    monkeypatch.setattr(RestClient, "get", lambda self, path: [{"name": "codex"}])
    with pytest.raises(_typer.Exit):
        resolve_agent_name(RestClient(), "list")
    err = capsys.readouterr().err
    assert "is a command, not an agent name" in err
    assert "xorcise agent list" in err
    assert "register --name list" not in err  # never steer a typo toward a mutation


def test_run_create_not_installed_pulls_and_says_so_on_stderr(monkeypatch):
    # A not-installed mission is no longer an error with a hint — create pulls it itself.
    # The UX contract flips with it: announce the pull on stderr (stdout stays clean for
    # --json), and never instruct the user to run a command the CLI just ran for them.
    from xorcise.core.cli.rest_client import RestClient

    def fake_get(self, path):
        if path == "/agents":
            return [{"id": "a1", "name": "demo"}]
        if path == "/missions":
            return [{"mission_id": "aviary-access", "name": "Aviary Access", "installed": False}]
        return {"status": "installed", "phase": "done"}

    def fake_post(self, path, json=None, timeout=None):
        if path.endswith("/pull-jobs"):
            return {"job_id": "j1"}
        return {"run_id": "run-777"}

    monkeypatch.setattr(RestClient, "get", fake_get)
    monkeypatch.setattr(RestClient, "post", fake_post)
    result = runner.invoke(app, ["run", "create", "--agent", "demo", "--mission", "aviary-access"])
    assert result.exit_code == 0
    assert "'Aviary Access' is not installed — pulling it first" in result.stderr
    assert "then: xorcise run create" not in result.stderr  # the old hint must not linger
    assert "run-777" in result.stdout


def test_narrow_terminal_never_truncates_ids_or_scores():
    """Copy-critical columns get pinned widths; prose columns absorb the squeeze."""
    from xorcise.core.cli._ux import _pin_column_widths, ux_table

    table = ux_table("Source", "Id", "Name", "Difficulty", "State")
    table.add_row(
        "Library", "process-of-elimination", "Process of Elimination", "Expert", "Installed"
    )
    _pin_column_widths(table)
    by_header = {str(c.header): c for c in table.columns}
    id_width, state_width = by_header["Id"].min_width, by_header["State"].min_width
    assert id_width is not None and id_width >= len("process-of-elimination")
    assert state_width is not None and state_width >= len("Installed")
    assert by_header["Name"].min_width is None  # prose column stays flexible


def test_doctor_data_directory_names_the_real_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core.cli._diagnostics import home_present

    check = home_present()
    assert check.name == "data directory"
    assert check.ok is True
    assert str(tmp_path.name) in check.detail or check.detail.startswith("~/")


# --- round-2 verification fixes ----------------------------------------------


def test_root_epilog_steps_fit_eighty_columns():
    """Every Get-started step must be copy-pasteable at the most common width."""
    from xorcise.core.cli._shared import _EPILOG

    for line in _EPILOG.splitlines():
        assert len(line) <= 77, f"epilog step too wide for an 80-col frame: {line!r}"


def test_implausible_option_typo_gets_no_suggestion():
    """--bogus has no close option — a wrong Did-you-mean is anti-actionable."""
    result = runner.invoke(app, ["config", "set-model", "--bogus"])
    assert result.exit_code == 2
    assert "Did you mean?" not in result.stderr
    # …while a REAL typo still gets its correction.
    result = runner.invoke(app, ["config", "set-model", "--tokeniser", "x"])
    assert result.exit_code == 2
    assert "--tokenizer" in result.stderr


def test_source_filter_error_uses_documented_spelling(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    missions = [{"source": "your_own", "mission_id": "b", "name": "B"}]
    monkeypatch.setattr(RestClient, "get", lambda self, path: missions)
    result = runner.invoke(app, ["mission", "list", "--source", "bogus"])
    assert result.exit_code == 2
    assert "your-own" in result.stderr  # the spelling --help documents
    assert "your_own" not in result.stderr  # never the internal slug


def test_rename_missing_argument_reads_as_a_sentence():
    result = runner.invoke(app, ["agent", "rename", "codex"])
    assert result.exit_code == 2
    assert "missing new agent name" in result.stderr


def test_full_length_unknown_run_id_keeps_the_cli_voice(monkeypatch):
    """The 32-hex passthrough must not surface raw HTTP jargon on a 404."""
    import httpx as _httpx

    def fake_get(url, timeout=None):
        request = _httpx.Request("GET", url)
        return _httpx.Response(404, request=request, json={"detail": "no run 'ffff…'"})

    # get_run_result issues its own httpx.get; a 404 (unknown run) must read in the
    # CLI voice, not 'request failed (404)'.
    monkeypatch.setattr(httpx, "get", fake_get)
    result = runner.invoke(app, ["run", "status", "f" * 32])
    assert result.exit_code == 1
    assert "error" in result.stderr
    assert "no run" in result.stderr
    assert "request failed (404)" not in result.stderr


def test_status_names_a_foreign_instance_after_down(monkeypatch, tmp_path):
    """No pid file here + healthy defaults = someone else's instance — say so."""
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        lifecycle, "probe_channel", lambda name, url, ok_statuses=(200,): Check(name, True, "ok")
    )
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))

    class FakeResp:
        @staticmethod
        def json():
            return {"home": "/somebody/elses/.xorcise"}

    monkeypatch.setattr(httpx, "get", lambda url, timeout=1: FakeResp())
    result = runner.invoke(app, ["status"])
    get_settings.cache_clear()
    assert result.exit_code == 0
    assert "belong to the instance at /somebody/elses/.xorcise" in result.output


# --- round-3 verification fixes ----------------------------------------------


def test_bare_help_short_flag_is_accepted():
    """`-h` is the near-universal help form — accepted at root and on groups."""
    for args in (["-h"], ["agent", "-h"], ["run", "status", "-h"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, f"{args} should print help"
        assert "Usage" in result.stdout


def test_version_word_is_accepted_like_the_flag():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout == f"xorcise {__version__}\n"


def test_register_with_sibling_subcommand_token_suggests_the_sibling(monkeypatch):
    """`agent register history` (no --name): recover the sibling intent, not
    'missing --name' steering the user to register a junk agent.

    The sibling detection reads argv (click reports the missing option before it
    notices the stray subcommand token); CliRunner doesn't set argv, so the test
    sets it to the real invocation the binary would see."""
    monkeypatch.setattr("sys.argv", ["xorcise", "agent", "register", "history"])
    result = runner.invoke(app, ["agent", "register", "history"])
    assert result.exit_code == 2
    assert "xorcise agent history" in result.stderr
    assert "register --name history" not in result.stderr


def test_run_id_extra_arg_never_suggests_quoting(monkeypatch):
    """Run ids have no spaces — the fix must not propose joining tokens into a
    quoted id (which only yields a second 'no run matching')."""
    result = runner.invoke(app, ["run", "status", "41d8", "extra"])
    assert result.exit_code == 2
    assert "a run id has no spaces" in result.stderr
    assert "xorcise run list" in result.stderr
    assert '"41d8 extra"' not in result.stderr  # the misfiring quote suggestion


def test_mission_name_extra_arg_still_suggests_quoting():
    """The space-quoting heuristic stays for name-taking commands."""
    result = runner.invoke(app, ["mission", "show", "Aviary", "Access"])
    assert result.exit_code == 2
    assert 'xorcise mission show "Aviary Access"' in result.stderr


def test_fmt_score_never_leaks_a_float_repr():
    from xorcise.core.cli._ux import fmt_score

    assert fmt_score(0.40700000000000003) == "0.41"
    assert fmt_score(1.0) == "1.00"
    assert fmt_score(0) == "0.00"
    assert fmt_score(None) == "None"
    assert fmt_score("—") == "—"


def test_run_status_scores_are_two_decimals(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    envelope = {
        "status": "graded",
        "grade": {
            "overall": 0.40700000000000003,
            "breakdown": {"deterministic": 0.3, "judge": 0.514},
        },
        "conditions": {"budget_seconds": 60},
    }
    monkeypatch.setattr(RestClient, "get_run_result", lambda self, rid: envelope)
    result = runner.invoke(app, ["run", "status", "r" * 32])
    assert result.exit_code == 0
    assert "overall=0.41" in result.stdout
    assert "deterministic=0.30" in result.stdout
    assert "0.40700000000000003" not in result.stdout


def test_data_command_warns_when_answered_by_a_foreign_instance(monkeypatch, tmp_path):
    """No instance started from THIS home, but a service answers reporting a
    DIFFERENT home → warn (a home-scoped read isn't silently someone else's)."""
    import xorcise.core.cli.rest_client as rc

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setattr(rc, "_FOREIGN_CHECKED", set())
    from xorcise.core.config import get_settings

    get_settings.cache_clear()

    class FakeResp:
        def __init__(self, url: str) -> None:
            self._system = url.endswith("/system")
            self.status_code = 200
            self.content = b"{}" if self._system else b"[]"
            self.is_error = False

        def json(self):
            return {"home": "/somebody/elses/.xorcise"} if self._system else []

    monkeypatch.setattr(httpx, "get", lambda url, **k: FakeResp(url))
    result = runner.invoke(app, ["agent", "list"])
    get_settings.cache_clear()
    assert result.exit_code == 0
    assert "belong to the instance at /somebody/elses/.xorcise" in result.stderr


def test_hidden_alias_help_names_both_forms():
    """A user who arrived via 'delete' must learn the canonical 'rm' (not a
    self-referential '(also alias: delete)')."""
    result = runner.invoke(app, ["agent", "delete", "--help"])
    assert result.exit_code == 0
    assert "both `rm` and `delete`" in result.stdout


# --- round-4 verification fixes ----------------------------------------------


def test_run_status_on_active_run_reads_as_progress_not_a_409(monkeypatch):
    """The golden-path hint after `run create` sends you to `run status`; on a
    still-active run that must read as progress (exit 3), never a raw red 409."""
    from xorcise.core.cli.rest_client import RestClient

    monkeypatch.setattr(RestClient, "get_run_result", lambda self, rid: {"status": "active"})
    result = runner.invoke(app, ["run", "status", "a" * 32])
    assert result.exit_code == 3  # in progress, not failure — a poll loop keeps waiting
    assert "still running" in result.stdout
    assert "409" not in result.output


def test_run_status_active_json_is_parseable(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    monkeypatch.setattr(RestClient, "get_run_result", lambda self, rid: {"status": "active"})
    result = runner.invoke(app, ["run", "status", "a" * 32, "--json"])
    assert result.exit_code == 0  # --json always exits 0; the envelope carries the status
    assert json.loads(result.stdout) == {"status": "active"}


def test_get_run_result_translates_the_not_terminal_409(monkeypatch, tmp_path):
    """A still-active run's server 409 becomes a soft {'status': 'active'}."""
    import httpx as _httpx

    from xorcise.core.cli.rest_client import RestClient

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))

    def fake_get(url, timeout=None):
        req = _httpx.Request("GET", url)
        return _httpx.Response(409, request=req, json={"detail": "run 'x' is not terminal yet"})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert RestClient(base_url="http://x/api").get_run_result("x") == {"status": "active"}


def test_empty_agent_name_is_rejected(monkeypatch):
    """An empty / whitespace name would render as the missing-value sentinel and
    be unaddressable — reject it before any write (exit 2)."""
    from xorcise.core.cli.rest_client import RestClient

    posted: list[object] = []
    monkeypatch.setattr(RestClient, "get", lambda self, path: [])
    monkeypatch.setattr(RestClient, "post", lambda self, path, json: posted.append(json))
    for bad in ("", "   ", "\t"):
        result = runner.invoke(app, ["agent", "register", "--name", bad])
        assert result.exit_code == 2, f"name {bad!r} should be rejected"
        assert "cannot be empty" in result.stderr
    assert posted == []  # never written


def test_mission_delete_when_not_installed_gives_a_recovery_pointer(monkeypatch):
    """Resolves to a real catalog entry but nothing installed → the same see-line
    every other not-found carries (was a bare server 404)."""
    from xorcise.core.cli.rest_client import RestClient

    missions = [{"mission_id": "aviary-access", "name": "Aviary Access", "installed": False}]
    deleted: list[str] = []
    monkeypatch.setattr(RestClient, "get", lambda self, path: missions)
    monkeypatch.setattr(RestClient, "delete", lambda self, path: deleted.append(path))
    result = runner.invoke(app, ["mission", "delete", "aviary-access", "--yes"])
    assert result.exit_code == 1
    assert "not installed" in result.stderr
    assert "xorcise mission list --installed" in result.stderr
    assert deleted == []  # never hit the delete endpoint


def test_client_error_with_detail_reads_in_the_cli_voice(monkeypatch, tmp_path):
    """Any 4xx carrying a server sentence renders 'error: <detail>', not
    'request failed (409)' — a 409 no longer looks like an internal crash."""
    import httpx as _httpx

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))

    def fake_post(url, json=None, timeout=None):
        req = _httpx.Request("POST", url)
        return _httpx.Response(409, request=req, json={"detail": "run 'x' is already terminal"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = runner.invoke(app, ["run", "terminate", "x" * 32, "--yes", "--no-wait"])
    assert result.exit_code == 1
    assert "run 'x' is already terminal" in result.stderr
    assert "request failed (409)" not in result.stderr


# --- round-5 verification fixes ----------------------------------------------


def test_golden_path_is_one_canonical_list():
    """The root epilog and `up`'s ready banner render the SAME steps — the product
    can never show two different get-started lists (round 5: the root screen was
    missing the mandatory judge-model step that `up` advertised)."""
    from xorcise.core.cli._shared import GOLDEN_PATH, golden_path_steps

    commands = [cmd for _label, cmd in GOLDEN_PATH]
    assert "xorcise config set-model --name <model> --key <key>" in commands
    root = "\n".join(golden_path_steps())
    after_up = "\n".join(golden_path_steps(skip_first=True))
    for cmd in commands:
        assert cmd in root
        if cmd != "xorcise up":  # `up` just ran — it is the only step dropped
            assert cmd in after_up
    assert "xorcise up" not in after_up
    assert lifecycle.next_steps_block("http://x/ui").endswith(after_up)


def test_documented_filter_value_with_no_matches_is_an_empty_result(monkeypatch):
    """A value `--help` documents must never be rejected just because nothing
    matches it today (round 5: 'your-own' failed on a library-only machine)."""
    from xorcise.core.cli.rest_client import RestClient

    library_only = [{"source": "library", "mission_id": "a", "name": "A", "installed": False}]
    monkeypatch.setattr(RestClient, "get", lambda self, path: library_only)
    result = runner.invoke(app, ["mission", "list", "--source", "your-own"])
    assert result.exit_code == 0
    assert "no missions match the filters" in result.stdout
    # A value OUTSIDE the documented vocabulary is still a usage error.
    bogus = runner.invoke(app, ["mission", "list", "--source", "nowhere"])
    assert bogus.exit_code == 2
    assert "library, your-own" in bogus.stderr


def test_known_difficulty_with_no_matches_is_an_empty_result(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    expert_only = [{"source": "library", "mission_id": "a", "name": "A", "proficiency": "expert"}]
    monkeypatch.setattr(RestClient, "get", lambda self, path: expert_only)
    result = runner.invoke(app, ["mission", "list", "--difficulty", "novice"])
    assert result.exit_code == 0  # a real level that simply matches nothing
    assert runner.invoke(app, ["mission", "list", "--difficulty", "bogus"]).exit_code == 2


def test_builtin_difficulty_vocabulary_is_the_current_ladder(monkeypatch):
    """Every tier of the XORCISE ladder filters without a live library carrying it,
    and the retired scale does not. The built-in vocabulary drifted once already:
    it kept 'intermediate' and 'hard' long after the ladder replaced them, which
    made those terms pass validation and then match nothing — an empty result the
    caller cannot tell apart from an empty library."""
    from xorcise.core.cli.rest_client import RestClient

    expert_only = [{"source": "library", "mission_id": "a", "name": "A", "proficiency": "expert"}]
    monkeypatch.setattr(RestClient, "get", lambda self, path: expert_only)

    for tier in ("novice", "advance beginner", "competent", "proficient", "expert"):
        assert runner.invoke(app, ["mission", "list", "--difficulty", tier]).exit_code == 0, tier
    for retired in ("intermediate", "hard", "easy", "medium"):
        result = runner.invoke(app, ["mission", "list", "--difficulty", retired])
        assert result.exit_code == 2, retired


def test_sibling_suggestion_carries_the_typed_name():
    """The suggestion must be paste-and-run: `register history --name codex`
    should propose `agent history codex`, not drop the name."""
    result = runner.invoke(app, ["agent", "register", "history", "--name", "codex"])
    assert result.exit_code == 2
    assert "xorcise agent history codex" in result.stderr


def test_out_of_range_port_is_a_usage_error():
    """A bad port is a bad option value (exit 2), not a runtime failure."""
    for args in (["up", "--port", "70000"], ["up", "--port", "0"], ["serve", "--port", "99999"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, f"{args} should be a usage error"
        assert "--port" in result.stderr
        assert "65535" in result.stderr


def test_doctor_flags_a_plane_that_is_not_listening(host_probes_ok, monkeypatch, tmp_path):
    """Half-down instance: doctor used to walk only the BOUND ports and conclude
    'No problems found' while a service was silently missing."""
    import json as _json
    import os as _os

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "xorcise.pid").write_text(str(_os.getpid()))
    (tmp_path / "runtime-ports.json").write_text(_json.dumps({"rest": 46400, "otlp": 46402}))
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(lifecycle, "docker_present", lambda: Check("docker", True, "present"))
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: Check("docker", True, "ok"))
    # the OTLP plane is up; the REST plane is silent.
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [46402])
    result = runner.invoke(app, ["doctor"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "not listening" in result.output
    assert "46400" in result.output
    assert "No problems found" not in result.output


def test_doctor_flags_an_unreachable_control_plane(host_probes_ok, monkeypatch, tmp_path):
    """The outage doctor used to call healthy: every host prerequisite genuinely fine,
    all three planes bound, and 100% of run creations failing with a 503 because the
    Headscale control plane was gone. doctor said "No problems found"."""
    import json as _json
    import os as _os

    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    # The suite forces stub mode, which legitimately has NO control plane; this is
    # about the real path, so opt back in (see the _force_stubs fixture).
    monkeypatch.delenv("XORCISE_USE_STUBS", raising=False)
    (tmp_path / "xorcise.pid").write_text(str(_os.getpid()))
    (tmp_path / "runtime-ports.json").write_text(_json.dumps({"rest": 46410, "otlp": 46412}))
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    # Every plane bound and healthy — the control plane is the ONLY fault.
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: list(ports))
    monkeypatch.setattr(lifecycle, "_running_server_ports", lambda: {46410, 46412})
    monkeypatch.setattr(
        lifecycle,
        "control_plane",
        lambda *a, **k: Check(
            "control plane", False, "'headscale' is not reachable", "xorcise down && xorcise up"
        ),
    )
    result = runner.invoke(app, ["doctor"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "No problems found" not in result.output
    assert "run network" in result.output  # named for what it does, not the component


def test_doctor_skips_the_control_plane_when_xorcise_is_not_running(
    host_probes_ok, monkeypatch, tmp_path
):
    """A healthy host that simply has not started yet must still pass its own diagnosis —
    the control plane does not exist until `up` provisions it."""
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "xorcise.db").write_text("")
    from xorcise.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [])

    def _never(*a, **k):
        raise AssertionError("must not probe a control plane that was never provisioned")

    monkeypatch.setattr(lifecycle, "control_plane", _never)
    result = runner.invoke(app, ["doctor"])
    get_settings.cache_clear()
    assert result.exit_code == 0
    assert "No problems found" in result.output


def test_agent_update_with_nothing_to_update_is_refused(monkeypatch):
    """An empty update used to PUT the declaration back and bump the version."""
    from xorcise.core.cli.rest_client import RestClient

    put_calls: list[str] = []
    monkeypatch.setattr(RestClient, "get", lambda self, path: [{"name": "codey"}])
    monkeypatch.setattr(RestClient, "put", lambda self, path, json: put_calls.append(path))
    result = runner.invoke(app, ["agent", "update", "--name", "codey"])
    assert result.exit_code == 2
    assert "nothing to update" in result.stderr
    assert put_calls == []  # no silent version bump


def test_run_help_has_no_raw_http_status_codes():
    for cmd in ("report", "delete", "terminate", "regrade"):
        result = runner.invoke(app, ["run", cmd, "--help"])
        assert result.exit_code == 0
        assert "409" not in result.stdout, f"run {cmd} --help leaks a raw HTTP status"


def test_config_test_not_configured_is_actionable(monkeypatch):
    """Step 3 of the golden path: the failure must name the fix, not leak
    'not_configured — model=—'."""
    from xorcise.core.cli.rest_client import RestClient

    monkeypatch.setattr(
        RestClient,
        "post",
        lambda self, path, json, timeout=None: {
            "ok": False,
            "status": "not_configured",
            "message": "No judge API key configured.",
        },
    )
    result = runner.invoke(app, ["config", "test"])
    assert result.exit_code == 1
    assert "xorcise config set-model" in result.stderr
    assert "not_configured" not in result.stderr  # internal token never surfaces


def test_config_show_points_at_the_fix_when_the_judge_is_unset(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    monkeypatch.setattr(
        RestClient,
        "get",
        lambda self, path: {"judge": {"configured": False}, "default_budget_seconds": 60},
    )
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "xorcise config set-model" in result.stdout


# --- host prerequisites (fresh-VM install) -----------------------------------


def _stub_docker(tmp_path, script: str):
    """A fake `docker` early on PATH — never touches the real installation."""
    binp = tmp_path / "stubbin"
    binp.mkdir(exist_ok=True)
    exe = binp / "docker"
    exe.write_text(script)
    exe.chmod(0o755)
    return str(binp)


def test_doctor_catches_a_missing_compose_v2_plugin(monkeypatch, tmp_path):
    """The fresh-Ubuntu trap: `apt install docker.io docker-compose` gives the
    LEGACY v1 binary, which does not provide `docker compose` — the subcommand
    every non-stub `up` provisions Headscale with."""
    stub = _stub_docker(
        tmp_path,
        "#!/bin/sh\n"
        'if [ "$1" = compose ]; then echo "not a docker command" >&2; exit 1; fi\n'
        "exit 0\n",
    )
    monkeypatch.setenv("PATH", f"{stub}:{__import__('os').environ['PATH']}")
    from xorcise.core.cli._diagnostics import docker_compose_v2

    check = docker_compose_v2()
    assert check.ok is False
    assert check.level == "blocker"
    assert "docker-compose-v2" in check.remediation
    assert "does NOT provide" in check.remediation  # names the v1 trap explicitly


def test_docker_permission_denied_is_not_reported_as_daemon_down(monkeypatch, tmp_path):
    """`usermod -aG docker` without a new login session: the daemon IS running —
    'start Docker' would be actively wrong advice."""
    stub = _stub_docker(
        tmp_path,
        "#!/bin/sh\n"
        'if [ "$1" = info ]; then '
        "echo 'permission denied while trying to connect to the Docker daemon socket' >&2; "
        "exit 1; fi\nexit 0\n",
    )
    monkeypatch.setenv("PATH", f"{stub}:{__import__('os').environ['PATH']}")
    from xorcise.core.cli._diagnostics import docker_daemon

    check = docker_daemon()
    assert check.ok is False
    assert check.detail == "permission denied"
    assert "usermod -aG docker" in check.remediation
    assert "log out and back in" in check.remediation
    assert "start Docker" not in check.remediation  # the wrong fix for this cause


def test_warnings_never_fail_the_doctor_verdict(monkeypatch, tmp_path):
    """Advisory checks (disk, /dev/net/tun) are reported but must not flip exit 0."""
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    from xorcise.core.cli._diagnostics import Check as _Check

    monkeypatch.setattr(lifecycle, "docker_present", lambda: _Check("docker", True, "present"))
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: _Check("docker", True, "ok"))
    monkeypatch.setattr(
        lifecycle, "docker_compose_v2", lambda: _Check("docker compose", True, "ok")
    )
    monkeypatch.setattr(lifecycle, "openssl_present", lambda: _Check("openssl", True, "present"))
    monkeypatch.setattr(lifecycle, "ports_in_use", lambda host, ports: [])
    monkeypatch.setattr(
        lifecycle,
        "disk_space",
        lambda: _Check("disk space", False, "only 1.0 GB free", "prune", level="warning"),
    )
    monkeypatch.setattr(
        lifecycle,
        "tun_device",
        lambda: _Check("/dev/net/tun", False, "missing", "modprobe tun", level="warning"),
    )
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, "warnings must not fail the verdict"
    assert "only 1.0 GB free" in result.output  # …but they ARE reported
    assert "No problems found" in result.output


def test_up_refuses_before_writing_anything_when_a_prerequisite_is_missing(monkeypatch, tmp_path):
    """A missing prerequisite used to surface ~20 lines in — after the home was
    created, the DB migrated and RSA keys generated."""
    home = tmp_path / "home"
    monkeypatch.setenv("XORCISE_HOME", str(home))
    from xorcise.core.cli._diagnostics import Check as _Check

    monkeypatch.setattr(
        lifecycle,
        "_environment_checks",
        lambda: [
            _Check("docker", True, "present"),
            _Check("docker compose", False, "missing", "sudo apt install docker-compose-v2"),
        ],
    )
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 1
    assert "Docker Compose v2 plugin is missing" in result.stderr
    assert "docker-compose-v2" in result.stderr
    assert "xorcise up --stub" in result.stderr  # the Docker-less way forward
    assert not home.exists(), "a refused `up` must leave nothing behind"


def test_dead_extras_advice_is_gone():
    """`pip install xorcise[runner]` installs NOTHING (the extra is an empty
    no-op) — advice that cannot fix the stated problem."""
    from pathlib import Path as _Path

    src = _Path("src/xorcise")
    offenders = [
        f"{p}:{n}"
        for p in src.rglob("*.py")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if "xorcise[runner]" in line or "xorcise[collector]" in line
    ]
    assert offenders == [], f"dead extras advice still present: {offenders}"


# --- show-command readability -------------------------------------------------


def test_mission_show_wraps_long_prose(monkeypatch):
    """A real objective runs to ~1,500 characters. Printed as one physical line it
    broke `| less`, editors and pasted tickets."""
    from xorcise.core.cli.rest_client import RestClient

    objective = " ".join(f"word{i}" for i in range(400))  # ~2,800 chars
    manifest = {
        "metadata": {
            "mission_id": "c1",
            "name": "Long One",
            "type": "static",
            "proficiency": "expert",
            "specialty": "forensics",
            "objective": objective,
            "skills": ["disk-forensics", "log-analysis"],
        },
        "rubric": [{"id": "r"}] * 20,
        "checks": [{"id": "c"}] * 8,
    }

    def fake_get(self, path):
        if path == "/missions":
            return [{"mission_id": "c1", "name": "Long One", "installed": False}]
        return manifest

    monkeypatch.setattr(RestClient, "get", fake_get)
    result = runner.invoke(app, ["mission", "show", "c1"])
    assert result.exit_code == 0
    longest = max(len(line) for line in result.stdout.splitlines())
    assert longest <= 100, f"prose not wrapped: longest line {longest}"
    # the counts are named, not `rubric=20 checks=8`
    assert "20 criteria scored by the judge model" in result.stdout
    assert "rubric=" not in result.stdout
    # the internal type slug is explained, and the install state + next step shown
    assert "Static" in result.stdout
    assert "xorcise mission pull c1" in result.stdout


def test_mission_show_json_is_untouched_by_the_redesign(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    manifest = {"metadata": {"mission_id": "c1", "name": "C"}, "rubric": []}

    def fake_get(self, path):
        if path == "/missions":
            return [{"mission_id": "c1", "name": "C", "installed": True}]
        return manifest

    monkeypatch.setattr(RestClient, "get", fake_get)
    result = runner.invoke(app, ["mission", "show", "c1", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == manifest  # raw DTO, byte-for-byte


def test_config_show_groups_into_sections(monkeypatch):
    from xorcise.core.cli.rest_client import RestClient

    view = {
        "judge": {"configured": True, "model_name": "m", "key_hint": "…abcd"},
        "terrain": {"uses_judge_default": True, "transcript_max_tokens": 256000},
        "catalog": {"connected": False, "url": None},
        "network": {},
        "default_budget_seconds": 3600,
    }
    monkeypatch.setattr(RestClient, "get", lambda self, path: view)
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    out = result.stdout
    for section in ("Judge model", "Terrain model", "Mission library", "Network", "Defaults"):
        assert section in out, f"missing section: {section}"
    assert "Configured" in out
    assert "inherits the judge model" in out  # said once, not repeated per field
    assert "Disabled" in out and "xorcise catalog connect" in out
    assert "Local" in out  # no network addresses set
