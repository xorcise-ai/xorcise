"""`down` must wait for the stopped server to actually exit before returning, so a
back-to-back `xorcise down` / `xorcise up` doesn't race the still-terminating process."""

from __future__ import annotations

import os

from xorcise.core.cli.commands import lifecycle


def test_await_exit_true_once_process_is_gone(monkeypatch):
    calls = {"n": 0}

    def fake_kill(pid: int, sig: int) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:  # alive on the first probe, gone on the next
            raise ProcessLookupError

    # lifecycle calls os.kill(pid, 0); patch the real os.kill it resolves to.
    monkeypatch.setattr(os, "kill", fake_kill)
    assert lifecycle._await_exit(4321, timeout=1.0, poll=0.01) is True


def test_await_exit_false_when_process_persists(monkeypatch):
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)  # never dies
    assert lifecycle._await_exit(4321, timeout=0.1, poll=0.01) is False
