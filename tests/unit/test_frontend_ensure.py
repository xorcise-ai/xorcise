"""`ensure_frontend_ready` — the impure orchestration around the build.

Guarantees: the skip env and packaged/fresh statuses are silent no-ops; a needed
rebuild shells out to `npm run build:static`; and — critically — a missing npm
or a failing build WARNS but never raises, so a UI problem can't stop the server
from booting.

Monkeypatches of stdlib modules imported by `_frontend` (subprocess/shutil) use the
string target form so they don't trip mypy's no-implicit-reexport on `_frontend.<mod>`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from rich.console import Console

from xorcise.core.cli import _frontend

pytestmark = pytest.mark.unit

_FE = "xorcise.core.cli._frontend"


class _Console:
    """Minimal Rich-console stand-in that records printed lines."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, msg: str = "") -> None:
        self.lines.append(msg)


def _ready(con: _Console) -> None:
    """Call ensure_frontend_ready with the recording console (cast to satisfy the type)."""
    _frontend.ensure_frontend_ready(cast(Console, con))


@pytest.fixture
def _no_real_build(monkeypatch):
    """Fail loudly if a test lets subprocess.run actually execute a build."""

    def _boom(*a, **k):  # pragma: no cover - only hit on a test bug
        raise AssertionError("subprocess.run should not run in this test")

    monkeypatch.setattr(f"{_FE}.subprocess.run", _boom)


def test_skip_env_short_circuits(monkeypatch, _no_real_build) -> None:
    monkeypatch.setenv("XORCISE_SKIP_FRONTEND_BUILD", "1")
    con = _Console()
    _ready(con)
    assert con.lines == []


def test_packaged_is_silent_noop(monkeypatch, _no_real_build) -> None:
    monkeypatch.delenv("XORCISE_SKIP_FRONTEND_BUILD", raising=False)
    monkeypatch.setattr(_frontend, "find_frontend_source", lambda: None)
    con = _Console()
    _ready(con)
    assert con.lines == []


def test_fresh_is_silent_noop(monkeypatch, _no_real_build) -> None:
    monkeypatch.delenv("XORCISE_SKIP_FRONTEND_BUILD", raising=False)
    monkeypatch.setattr(_frontend, "find_frontend_source", lambda: Path("/repo/frontend"))
    monkeypatch.setattr(_frontend, "frontend_build_status", lambda fe, sd: "fresh")
    con = _Console()
    _ready(con)
    assert con.lines == []


def test_npm_absent_warns_and_does_not_raise(monkeypatch, _no_real_build) -> None:
    monkeypatch.delenv("XORCISE_SKIP_FRONTEND_BUILD", raising=False)
    monkeypatch.setattr(_frontend, "find_frontend_source", lambda: Path("/repo/frontend"))
    monkeypatch.setattr(_frontend, "frontend_build_status", lambda fe, sd: "missing")
    monkeypatch.setattr(f"{_FE}.shutil.which", lambda _: None)
    con = _Console()
    _ready(con)  # must not raise
    assert any("npm" in ln and "build:static" in ln for ln in con.lines)


def test_build_runs_and_reports_success(monkeypatch) -> None:
    monkeypatch.delenv("XORCISE_SKIP_FRONTEND_BUILD", raising=False)
    fe = Path("/repo/frontend")
    monkeypatch.setattr(_frontend, "find_frontend_source", lambda: fe)
    monkeypatch.setattr(_frontend, "frontend_build_status", lambda f, sd: "broken")
    monkeypatch.setattr(f"{_FE}.shutil.which", lambda _: "/usr/bin/npm")

    seen = {}

    def _run(cmd, cwd=None, check=False, **kwargs):  # build output now logs to a file
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(f"{_FE}.subprocess.run", _run)
    con = _Console()
    _ready(con)
    assert seen["cmd"] == ["npm", "run", "build:static"]
    assert seen["cwd"] == str(fe)
    assert any("built" in ln for ln in con.lines)


def test_build_failure_warns_and_does_not_raise(monkeypatch) -> None:
    monkeypatch.delenv("XORCISE_SKIP_FRONTEND_BUILD", raising=False)
    monkeypatch.setattr(_frontend, "find_frontend_source", lambda: Path("/repo/frontend"))
    monkeypatch.setattr(_frontend, "frontend_build_status", lambda fe, sd: "missing")
    monkeypatch.setattr(f"{_FE}.shutil.which", lambda _: "/usr/bin/npm")
    monkeypatch.setattr(f"{_FE}.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=1))
    con = _Console()
    _ready(con)  # must not raise
    assert any("failed" in ln for ln in con.lines)
