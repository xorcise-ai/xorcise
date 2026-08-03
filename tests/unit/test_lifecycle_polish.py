"""TTY polish on the lifecycle commands — `down`'s branded sign-off, `up`'s
update notice, and the step_progress pipe-safety guarantee.

Non-TTY output must stay byte-for-byte historical (scripts parse it); the
CliRunner-based tests in test_cli_commands.py lock that side down. Here the
consoles are force_terminal to exercise the TTY-only branches.
"""

from __future__ import annotations

import io
import re
from types import SimpleNamespace

import pytest
from rich.console import Console

from xorcise.core.cli import _ux
from xorcise.core.cli._shared import XORCISE_THEME
from xorcise.core.cli.commands import lifecycle

pytestmark = pytest.mark.unit

_LC = "xorcise.core.cli.commands.lifecycle"


def _terminal_console(out: io.StringIO) -> Console:
    return Console(file=out, force_terminal=True, theme=XORCISE_THEME, soft_wrap=True)


def _plain(out: io.StringIO) -> str:
    """The captured output with ANSI styling stripped — Rich's highlighter may
    split any literal (e.g. a version number) with colour codes mid-word."""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out.getvalue())


def test_step_progress_is_inert_when_piped() -> None:
    # The shared console is not a terminal under pytest: the body must simply run.
    ran: list[bool] = []
    with _ux.step_progress("doing a slow thing"):
        ran.append(True)
    assert ran == [True]


def test_step_progress_runs_body_on_tty(monkeypatch) -> None:
    err = io.StringIO()
    monkeypatch.setattr(_ux, "err_console", _terminal_console(err))
    ran: list[bool] = []
    with _ux.step_progress("doing a slow thing"):
        ran.append(True)
    assert ran == [True]


def test_down_tty_signs_off_branded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "xorcise.pid").write_text("999999")
    monkeypatch.setattr(f"{_LC}.os.kill", lambda pid, sig: None)
    monkeypatch.setattr(lifecycle, "_await_exit", lambda pid, **kw: True)
    monkeypatch.setattr("xorcise.core.rest.reap.reap_managed_containers", lambda settings, **kw: [])
    out = io.StringIO()
    monkeypatch.setattr(lifecycle, "console", _terminal_console(out))
    # An interactive stderr too, so down()'s step_progress spinners take their LIVE
    # branch here — a spinner regression must fail this test, not slip past it.
    monkeypatch.setattr(_ux, "err_console", _terminal_console(io.StringIO()))
    lifecycle.down(keep_data=False, purge=False, yes=False)
    text = _plain(out)
    assert "⊕" in text, "the branded mark leads the sign-off"
    assert "xorcise up" in text, "the sign-off points back to the restart command"
    assert "stopped — xorcise down" not in text  # the plain line is the non-TTY form


def test_up_tty_prints_update_notice_after_banner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    monkeypatch.setattr(lifecycle, "ensure_frontend_ready", lambda console: None)
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
    monkeypatch.setattr(f"{_LC}.subprocess.Popen", lambda cmd, **kw: SimpleNamespace(pid=4321))
    monkeypatch.setattr(f"{_LC}.httpx.get", lambda url, timeout=1: SimpleNamespace(status_code=200))
    monkeypatch.setattr(
        lifecycle,
        "begin_update_check",
        lambda: lambda: "[dim]update available:[/dim] [accent]v9.9.9[/accent]",
    )
    out = io.StringIO()
    monkeypatch.setattr(lifecycle, "console", _terminal_console(out))
    try:
        lifecycle.up(stub=True)
    finally:
        from xorcise.core.config import get_settings

        get_settings.cache_clear()  # up stamps role/stub env; don't leak the cached view
    text = _plain(out)
    assert "X O R C I S E" in text, "the banner still leads"
    assert "update available" in text
    assert "v9.9.9" in text
