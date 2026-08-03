from __future__ import annotations

import pytest

from xorcise.core.roles.activate import _breaches, activate
from xorcise.core.roles.boot import AppSpec
from xorcise.core.roles.registry import UnknownRoleError


def test_breaches_is_pure_intersection() -> None:
    assert _breaches({"a", "b"}, frozenset({"b", "c"})) == {"b"}
    assert _breaches({"a"}, frozenset({"x"})) == set()


def test_activate_all_returns_rest_and_otlp() -> None:
    specs = activate("all")
    assert all(isinstance(s, AppSpec) for s in specs)
    assert sorted(s.port for s in specs) == [3001, 4318]


def test_activate_control_serves_rest_only() -> None:
    # No OTLP receiver (that is the collector role) and no MCP plane (removed).
    assert sorted(s.port for s in activate("control")) == [3001]


def test_activate_unknown_role_raises() -> None:
    with pytest.raises(UnknownRoleError):
        activate("nope")


def test_activate_all_honors_configured_ports(monkeypatch) -> None:
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_REST_PORT", "4001")
    monkeypatch.setenv("XORCISE_OTLP_PORT", "5318")
    get_settings.cache_clear()
    try:
        assert sorted(s.port for s in activate("all")) == [4001, 5318]
    finally:
        get_settings.cache_clear()


def test_activate_runner_honors_configured_port(monkeypatch) -> None:
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_RUNNER_PORT", "9900")
    get_settings.cache_clear()
    try:
        assert [s.port for s in activate("runner")] == [9900]
    finally:
        get_settings.cache_clear()


def test_activate_headscale_honors_configured_port(monkeypatch) -> None:
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_HEADSCALE_PORT", "9091")
    get_settings.cache_clear()
    try:
        assert [s.port for s in activate("headscale")] == [9091]
    finally:
        get_settings.cache_clear()
