"""The distributed topology refuses to start when it would listen on loopback only (#169).

In the distributed topology the agent-facing apps bind the configured host, which defaults to
127.0.0.1, while agents, the collector ACL and the tailnet route all point at the advertised
host. Nothing answered there, and every run failed with no diagnosis. Until multi-machine
deployment binds the advertised address, `up` and `serve` refuse to start instead, and say why.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
from xorcise.core.cli._shared import app
from xorcise.core.cli.commands import lifecycle
from xorcise.core.config import get_settings
from xorcise.core.roles.boot.role_all import distributed_bind_refusal

pytestmark = pytest.mark.unit

runner = CliRunner()
ADVERTISED = "192.168.1.10"


@pytest.fixture
def topology(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))

    def _set(kind: str, host: str, advertise: str = ADVERTISED) -> None:
        monkeypatch.setenv("XORCISE_DEPLOYMENT_TOPOLOGY", kind)
        monkeypatch.setenv("XORCISE_HOST", host)
        monkeypatch.setenv("XORCISE_HEADSCALE_ADVERTISE_HOST", advertise)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "127.0.0.2", "localhost", "LOCALHOST", "localhost.", "::1"]
)
def test_distributed_on_loopback_is_refused(host, topology) -> None:
    topology("distributed", host)
    refusal = distributed_bind_refusal(get_settings())
    assert refusal is not None
    assert f"listen only on {host}" in refusal
    assert "0.0.0.0" not in refusal, "never suggest exposing /api on every network"


def test_the_fix_goes_in_config_toml_so_every_later_command_agrees(topology, tmp_path) -> None:
    """A shell-only XORCISE_HOST would move the server and leave every later CLI command, which
    finds the server at the same `host`, aiming at 127.0.0.1."""
    topology("distributed", "127.0.0.1")
    refusal = distributed_bind_refusal(get_settings())
    assert refusal is not None
    assert f'set host = "{ADVERTISED}" in {tmp_path / "config.toml"}' in refusal
    assert "XORCISE_HOST" not in refusal


def test_the_refusal_names_no_plane_a_role_may_not_serve(topology) -> None:
    """It applies to every role, and a collector has no run-control."""
    topology("distributed", "127.0.0.1")
    refusal = distributed_bind_refusal(get_settings())
    assert refusal is not None
    assert "run-control" not in refusal
    assert "collector" not in refusal


def test_the_refusal_still_reads_with_no_advertised_host(topology) -> None:
    topology("distributed", "127.0.0.1", advertise="")
    refusal = distributed_bind_refusal(get_settings())
    assert refusal is not None
    assert 'set host = "<the address agents use>"' in refusal


@pytest.mark.parametrize("host", [ADVERTISED, "10.0.0.5", "0.0.0.0", "xorcise.lan"])
def test_distributed_off_loopback_may_start(host, topology) -> None:
    topology("distributed", host)
    assert distributed_bind_refusal(get_settings()) is None


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_the_local_topology_is_never_refused(host, topology) -> None:
    topology("local", host)
    assert distributed_bind_refusal(get_settings()) is None


def test_serve_exits_before_binding_anything(topology, monkeypatch) -> None:
    from xorcise.core.cli.commands import serve as serve_mod

    topology("distributed", "127.0.0.1")
    bound: list[object] = []
    monkeypatch.setattr(serve_mod, "bind_specs", lambda *a, **k: bound.append(a))
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 1
    assert "deployment_topology is 'distributed'" in result.output
    assert bound == []


def test_up_exits_before_writing_anything(topology, monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(lifecycle, "init_home", lambda: calls.append("home"))
    monkeypatch.setattr(lifecycle, "scaffold_config", lambda home: calls.append("config"))
    monkeypatch.setattr(lifecycle, "_ensure_db_ready", lambda: calls.append("db"))
    monkeypatch.setattr(lifecycle, "xorcise_home", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "pid_file", lambda: tmp_path / "pid")
    monkeypatch.setattr(lifecycle, "_live_instance", lambda *a, **k: None)
    monkeypatch.setattr(lifecycle, "_require_prerequisites", lambda **k: None)
    monkeypatch.setattr(lifecycle, "begin_update_check", lambda: lambda: None)
    monkeypatch.setattr(
        lifecycle, "_maybe_provision_headscale", lambda *a, **k: calls.append("headscale")
    )
    monkeypatch.setattr(lifecycle, "ensure_frontend_ready", lambda c: calls.append("frontend"))

    def _popen(*_a: object, **_k: object) -> SimpleNamespace:
        calls.append("popen")
        return SimpleNamespace(pid=1)

    monkeypatch.setattr("xorcise.core.cli.commands.lifecycle.subprocess.Popen", _popen)
    topology("distributed", "127.0.0.1")
    result = runner.invoke(app, ["up"])
    assert result.exit_code == 1
    assert "deployment_topology is 'distributed'" in result.output
    assert calls == []
