"""The frontend build's live progress — `build_phase` parsing + the TTY spinner path.

Guarantees: the phase parser reads real `next build` wording (last marker wins,
page counts carried through) and degrades to None on anything else; a live
terminal runs the build through Popen + spinner (never `subprocess.run`); and
piped/captured output keeps the historical line-based behaviour, which the
tests in test_up_builds_frontend.py / test_frontend_ensure.py already lock in.
"""

from __future__ import annotations

import io
import re
from types import SimpleNamespace

import pytest
from rich.console import Console

from xorcise.core.cli import _frontend
from xorcise.core.cli._shared import XORCISE_THEME

pytestmark = pytest.mark.unit

_FE = "xorcise.core.cli._frontend"

_NEXT_TRANSCRIPT = """\
> frontend@0.1.0 build:static
> next build && rm -rf _static && cp -r out _static

   ▲ Next.js 15.3.1

   Creating an optimized production build ...
 ✓ Compiled successfully in 12.3s
   Linting and checking validity of types ...
   Collecting page data ...
   Generating static pages (3/14) ...
   Generating static pages (14/14)
   Finalizing page optimization ...
   Collecting build traces ...
"""


def test_build_phase_last_marker_wins() -> None:
    assert _frontend.build_phase(_NEXT_TRANSCRIPT) == "collecting build traces"


def test_build_phase_carries_page_counts() -> None:
    upto_pages = _NEXT_TRANSCRIPT.split("Generating static pages (14/14)")[0]
    assert _frontend.build_phase(upto_pages) == "generating pages (3/14)"


def test_build_phase_newest_page_count_supersedes() -> None:
    upto_final = _NEXT_TRANSCRIPT.split("Finalizing")[0]
    assert _frontend.build_phase(upto_final) == "generating pages (14/14)"


def test_build_phase_unrecognized_log_is_none() -> None:
    assert _frontend.build_phase("npm WARN deprecated something\nadded 120 packages\n") is None
    assert _frontend.build_phase("") is None


class _Proc:
    """Popen stand-in: alive for two polls, then exited cleanly."""

    def __init__(self) -> None:
        self._polls = 2

    def poll(self) -> int | None:
        self._polls -= 1
        return None if self._polls > 0 else 0

    def wait(self) -> int:
        return 0


def test_tty_build_runs_behind_spinner_via_popen(tmp_path, monkeypatch) -> None:
    """An interactive stderr must never block silently: the build goes through
    the spinner's Popen path (subprocess.run would be the silent one), the
    spinner rides stderr, and the stdout success line gains the elapsed time."""
    monkeypatch.delenv("XORCISE_SKIP_FRONTEND_BUILD", raising=False)
    monkeypatch.delenv("XORCISE_DEBUG", raising=False)
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))  # the build log lands here
    fe = tmp_path / "frontend"
    (fe / "node_modules" / ".bin").mkdir(parents=True)
    (fe / "package.json").write_text("{}")
    (fe / "node_modules" / ".bin" / "next").write_text("#!/bin/sh\n")
    monkeypatch.setattr(_frontend, "find_frontend_source", lambda: fe)
    monkeypatch.setattr(_frontend, "frontend_build_status", lambda f, s: "stale")
    monkeypatch.setattr(f"{_FE}.shutil.which", lambda _: "/usr/bin/npm")

    def _no_run(*a, **k):  # pragma: no cover - only hit on a regression
        raise AssertionError("the interactive path must use Popen, not subprocess.run")

    monkeypatch.setattr(f"{_FE}.subprocess.run", _no_run)

    calls: dict[str, object] = {}

    def _popen(cmd, cwd=None, stdout=None, stderr=None):
        calls["cmd"] = list(cmd)
        calls["cwd"] = cwd
        return _Proc()

    monkeypatch.setattr(f"{_FE}.subprocess.Popen", _popen)

    err = io.StringIO()  # the spinner's stream — its interactivity gates the path
    monkeypatch.setattr(
        _frontend,
        "err_console",
        Console(file=err, force_terminal=True, theme=XORCISE_THEME, soft_wrap=True),
    )
    out = io.StringIO()
    console = Console(file=out, force_terminal=True, theme=XORCISE_THEME, soft_wrap=True)
    _frontend.ensure_frontend_ready(console, static_dir=tmp_path / "_static")

    assert calls["cmd"] == ["npm", "run", "build:static"]
    assert calls["cwd"] == str(fe)
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out.getvalue())
    assert "frontend UI built ✓ (" in plain, "interactive success line carries elapsed time"


def test_non_tty_success_line_is_exactly_historical(tmp_path, monkeypatch) -> None:
    """Piped stdout must keep the exact 'frontend UI built ✓' line — no elapsed
    suffix — because scripts anchor on it."""
    monkeypatch.delenv("XORCISE_SKIP_FRONTEND_BUILD", raising=False)
    monkeypatch.delenv("XORCISE_DEBUG", raising=False)
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    fe = tmp_path / "frontend"
    (fe / "node_modules" / ".bin").mkdir(parents=True)
    (fe / "package.json").write_text("{}")
    (fe / "node_modules" / ".bin" / "next").write_text("#!/bin/sh\n")
    monkeypatch.setattr(_frontend, "find_frontend_source", lambda: fe)
    monkeypatch.setattr(_frontend, "frontend_build_status", lambda f, s: "stale")
    monkeypatch.setattr(f"{_FE}.shutil.which", lambda _: "/usr/bin/npm")
    monkeypatch.setattr(f"{_FE}.subprocess.run", lambda *a, **k: SimpleNamespace(returncode=0))
    out = io.StringIO()
    console = Console(file=out, theme=XORCISE_THEME, soft_wrap=True)  # not a terminal
    _frontend.ensure_frontend_ready(console, static_dir=tmp_path / "_static")
    assert "frontend UI built ✓\n" in out.getvalue()
    assert "✓ (" not in out.getvalue()
