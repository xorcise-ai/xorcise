"""`xorcise up` must (re)build the /ui export BEFORE spawning the serve process.

Guards the wiring added for the auto-build feature: the frontend readiness step
runs in the foreground `up` process (so its output reaches the operator) and
strictly before the detached `serve` subprocess is spawned. All side-effecting
pre-flight (home/db/headscale/ports/health) is neutralised so this stays a unit
test of the ordering, not an integration boot.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from xorcise.core.cli import _frontend
from xorcise.core.cli.commands import lifecycle

pytestmark = pytest.mark.unit


def test_up_builds_frontend_before_spawning_serve(tmp_path, monkeypatch) -> None:
    calls: list[str] = []

    # The step under test — record that (and when) it ran.
    monkeypatch.setattr(
        lifecycle, "ensure_frontend_ready", lambda console: calls.append("frontend")
    )

    # Neutralise every side-effecting pre-flight so nothing touches the real host.
    monkeypatch.setattr(lifecycle, "init_home", lambda: None)
    monkeypatch.setattr(lifecycle, "scaffold_config", lambda home: None)
    monkeypatch.setattr(lifecycle, "_ensure_db_ready", lambda: None)
    monkeypatch.setattr(lifecycle, "xorcise_home", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "resolve_ports", lambda host, wanted: dict(wanted))
    monkeypatch.setattr(lifecycle, "write_runtime_ports", lambda ports: None)
    monkeypatch.delenv("XORCISE_USE_STUBS", raising=False)  # up(stub=True) stamps it; restore
    monkeypatch.setattr(lifecycle, "docker_daemon", lambda: SimpleNamespace(ok=True))
    monkeypatch.setattr(lifecycle, "_maybe_provision_headscale", lambda *a, **k: "skip-stub")
    monkeypatch.setattr(lifecycle, "pid_file", lambda: tmp_path / "pid")

    def _popen(cmd, **kw):
        calls.append("popen")
        return SimpleNamespace(pid=4321)

    # String targets: subprocess/httpx are imported by lifecycle, so `lifecycle.<mod>` trips
    # mypy's no-implicit-reexport; patch via the dotted path instead.
    monkeypatch.setattr("xorcise.core.cli.commands.lifecycle.subprocess.Popen", _popen)
    monkeypatch.setattr(
        "xorcise.core.cli.commands.lifecycle.httpx.get",
        lambda url, timeout=1: SimpleNamespace(status_code=200),
    )

    try:
        lifecycle.up(stub=True)
    finally:
        from xorcise.core.config import get_settings

        get_settings.cache_clear()  # up stamps role/stub env; don't leak the cached view

    assert "frontend" in calls, "up did not build the frontend"
    assert calls.index("frontend") < calls.index("popen"), "frontend build must precede serve spawn"


# --- ensure_frontend_ready installs npm deps before building on a fresh checkout ---


def _mk_frontend(tmp_path: Path, *, node_modules: bool, lockfile: bool = True) -> Path:
    fe = tmp_path / "frontend"
    fe.mkdir()
    (fe / "package.json").write_text("{}")
    if lockfile:
        (fe / "package-lock.json").write_text("{}")
    if node_modules:
        # A *populated* install: the `next` binary is what build:static needs on PATH.
        bindir = fe / "node_modules" / ".bin"
        bindir.mkdir(parents=True)
        (bindir / "next").write_text("#!/bin/sh\n")
    return fe


class _RunSpy:
    """Records each subprocess argv; returns rc=1 for any command containing ``fail_on``."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.cmds: list[list[str]] = []
        self._fail_on = fail_on

    def __call__(self, cmd, **kw):  # noqa: ANN001, ANN003
        self.cmds.append(list(cmd))
        rc = 1 if (self._fail_on and self._fail_on in cmd) else 0
        return SimpleNamespace(returncode=rc)


def _wire(monkeypatch, fe: Path, spy: _RunSpy, status: str = "missing") -> None:
    monkeypatch.setattr(_frontend, "find_frontend_source", lambda: fe)
    monkeypatch.setattr(_frontend, "frontend_build_status", lambda f, s: status)
    # dotted-string target: patching _frontend.shutil directly trips mypy no-implicit-reexport.
    monkeypatch.setattr("xorcise.core.cli._frontend.shutil.which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr("xorcise.core.cli._frontend.subprocess.run", spy)
    monkeypatch.delenv(_frontend._SKIP_ENV, raising=False)


def _console() -> Console:
    return Console(file=io.StringIO())


def test_frontend_install_runs_before_build_when_node_modules_absent(tmp_path, monkeypatch) -> None:
    fe = _mk_frontend(tmp_path, node_modules=False, lockfile=True)
    spy = _RunSpy()
    _wire(monkeypatch, fe, spy)
    _frontend.ensure_frontend_ready(_console(), static_dir=tmp_path / "_static")
    # lockfile present → npm ci, and it must run strictly before the build.
    assert spy.cmds == [["npm", "ci"], ["npm", "run", "build:static"]]


def test_frontend_install_uses_npm_install_without_lockfile(tmp_path, monkeypatch) -> None:
    fe = _mk_frontend(tmp_path, node_modules=False, lockfile=False)
    spy = _RunSpy()
    _wire(monkeypatch, fe, spy)
    _frontend.ensure_frontend_ready(_console(), static_dir=tmp_path / "_static")
    assert spy.cmds[0] == ["npm", "install"]
    assert ["npm", "run", "build:static"] in spy.cmds


def test_frontend_no_install_when_next_binary_present(tmp_path, monkeypatch) -> None:
    fe = _mk_frontend(tmp_path, node_modules=True)
    spy = _RunSpy()
    _wire(monkeypatch, fe, spy)
    _frontend.ensure_frontend_ready(_console(), static_dir=tmp_path / "_static")
    assert spy.cmds == [["npm", "run", "build:static"]]


def test_frontend_install_when_node_modules_present_but_empty(tmp_path, monkeypatch) -> None:
    # Regression: a cleared/partial node_modules (dir exists, no `next` binary) must still
    # trigger an install — `node_modules` existing is not proof the deps are usable.
    fe = _mk_frontend(tmp_path, node_modules=False, lockfile=True)
    (fe / "node_modules").mkdir()  # empty dir, no .bin/next
    spy = _RunSpy()
    _wire(monkeypatch, fe, spy)
    _frontend.ensure_frontend_ready(_console(), static_dir=tmp_path / "_static")
    assert spy.cmds == [["npm", "ci"], ["npm", "run", "build:static"]]


def test_frontend_install_failure_warns_and_skips_build(tmp_path, monkeypatch) -> None:
    fe = _mk_frontend(tmp_path, node_modules=False, lockfile=True)
    spy = _RunSpy(fail_on="ci")  # npm ci fails
    _wire(monkeypatch, fe, spy)
    # Must not raise (a UI build problem never stops the server booting) and must NOT build.
    _frontend.ensure_frontend_ready(_console(), static_dir=tmp_path / "_static")
    assert spy.cmds == [["npm", "ci"]]
