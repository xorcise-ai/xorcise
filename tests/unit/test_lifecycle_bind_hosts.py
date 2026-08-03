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
