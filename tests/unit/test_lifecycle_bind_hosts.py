"""_bind_hosts pairs an IPv4-wildcard listener with an IPv6 loopback companion.

Regression guard for the "GUI loads on 127.0.0.1 but not the loopback hostname"
bug: the hostname resolves to IPv6 ::1 first on many systems, which a 0.0.0.0
listener never answers.
"""

from __future__ import annotations

import pytest

from xorcise.core.cli.commands.lifecycle import _bind_hosts

pytestmark = pytest.mark.unit


def test_ipv4_wildcard_gains_ipv6_loopback_companion() -> None:
    assert _bind_hosts("0.0.0.0") == ["0.0.0.0", "::1"]


def test_explicit_hosts_are_left_alone() -> None:
    # Loopback-only / specific hosts don't need (or want) a companion listener.
    assert _bind_hosts("127.0.0.1") == ["127.0.0.1"]
    assert _bind_hosts("::1") == ["::1"]
    assert _bind_hosts("192.168.1.10") == ["192.168.1.10"]


def test_tuple_hosts_flatten_in_order() -> None:
    # Agent-facing specs now carry an explicit host tuple (loopback + docker
    # gateway); each element becomes one listener, order preserved.
    assert _bind_hosts(("127.0.0.1", "::1", "172.17.0.1")) == ["127.0.0.1", "::1", "172.17.0.1"]


def test_tuple_with_wildcard_still_gains_companion_without_duplicates() -> None:
    # The Linux fallback tuple ("0.0.0.0",) keeps the IPv6-loopback companion,
    # and a tuple already naming ::1 doesn't get it twice.
    assert _bind_hosts(("0.0.0.0",)) == ["0.0.0.0", "::1"]
    assert _bind_hosts(("0.0.0.0", "::1")) == ["0.0.0.0", "::1"]


def test_role_bind_hosts_is_the_set_serve_binds(monkeypatch) -> None:
    """Review of #81: port resolution probed a hand-maintained guess (`host`, `0.0.0.0`, then
    `::1` for everyone) that had to be kept in sync with `_bind_hosts`/`bind_specs` by hand —
    twice widened after a production miss. Resolution now probes the role's real listener set,
    built by the same role_all function `serve` uses — without the docker call (the bridge
    gateway is a specific IPv4 interface the wildcard probe already covers)."""
    import subprocess

    from xorcise.core.cli.commands import lifecycle
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_DEPLOYMENT_TOPOLOGY", "local")
    get_settings.cache_clear()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("resolution shelled out"))
    try:
        # role:all binds the agent-facing loopbacks (IPv4 + IPv6) …
        assert lifecycle._role_bind_hosts("all", "127.0.0.1") == ["127.0.0.1", "::1"]
        # … plus a configured non-loopback host; the wildcard stays an explicit opt-in.
        assert lifecycle._role_bind_hosts("all", "192.168.1.10") == [
            "127.0.0.1",
            "::1",
            "192.168.1.10",
        ]
        assert lifecycle._role_bind_hosts("all", "0.0.0.0") == ["0.0.0.0", "::1"]
        # every other role binds the configured host only: no ::1 probe, so an unrelated
        # `[::1]:8800` listener cannot relocate `serve --role runner`.
        assert lifecycle._role_bind_hosts("runner", "127.0.0.1") == ["127.0.0.1"]
        assert lifecycle._role_bind_hosts("collector", "0.0.0.0") == ["0.0.0.0", "::1"]
    finally:
        get_settings.cache_clear()
