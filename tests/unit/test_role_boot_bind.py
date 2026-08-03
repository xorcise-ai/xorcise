"""Unit tests for per-topology bind address on agent-facing AppSpecs.

Exposing REST + OTLP so the agent container reaches them via
host.docker.internal on 0.0.0.0 also exposed the unauthenticated /api
surface to the LAN, violating the loopback-bind assumption the local
no-auth posture leans on. The specs now bind the narrowest set that keeps
the container path working: loopback everywhere, plus the docker bridge
gateway on native Linux (where host.docker.internal maps to the gateway,
not the host loopback). Docker Desktop (macOS/Windows) proxies
host.docker.internal to the host loopback, so loopback alone suffices there.
"""

import pytest

from xorcise.core.config import get_settings
from xorcise.core.roles.boot import role_all

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv("XORCISE_HOST", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _agent_facing_hosts(monkeypatch) -> dict[int, object]:
    monkeypatch.setenv("XORCISE_DEPLOYMENT_TOPOLOGY", "local")
    get_settings.cache_clear()
    return {s.port: s.host for s in role_all.apps()}


def test_local_desktop_platforms_bind_loopback_only(monkeypatch):
    # macOS/Windows: Docker Desktop proxies host.docker.internal to the host
    # loopback, so the agent container still reaches REST + OTLP without a
    # wildcard bind. ::1 keeps the loopback hostname (IPv6-first) working.
    monkeypatch.setattr(role_all, "_system", lambda: "Darwin", raising=False)
    hosts = _agent_facing_hosts(monkeypatch)
    s = get_settings()
    assert hosts[s.rest_port] == ("127.0.0.1", "::1")
    assert hosts[s.otlp_port] == ("127.0.0.1", "::1")
    assert set(hosts) == {s.rest_port, s.otlp_port}


def test_local_linux_adds_docker_bridge_gateway(monkeypatch):
    # Native Linux: host.docker.internal:host-gateway maps to the bridge
    # gateway (a real host interface), so the specs add it — and nothing wider.
    monkeypatch.setattr(role_all, "_system", lambda: "Linux", raising=False)
    monkeypatch.setattr(role_all, "_docker_bridge_gateway", lambda: "172.17.0.1", raising=False)
    monkeypatch.setattr(role_all, "_locally_bindable", lambda ip: True, raising=False)
    hosts = _agent_facing_hosts(monkeypatch)
    s = get_settings()
    assert hosts[s.rest_port] == ("127.0.0.1", "::1", "172.17.0.1")
    assert hosts[s.otlp_port] == ("127.0.0.1", "::1", "172.17.0.1")


def test_local_linux_falls_back_wide_when_gateway_unknown(monkeypatch):
    # No detectable gateway (docker down / exotic daemon config): a silent
    # loopback-only bind would break every run mysteriously, so keep the
    # historical wildcard rather than trade a working install for lockdown.
    monkeypatch.setattr(role_all, "_system", lambda: "Linux", raising=False)
    monkeypatch.setattr(role_all, "_docker_bridge_gateway", lambda: "", raising=False)
    hosts = _agent_facing_hosts(monkeypatch)
    s = get_settings()
    assert hosts[s.rest_port] == ("0.0.0.0",)
    assert hosts[s.otlp_port] == ("0.0.0.0",)


def test_local_linux_falls_back_wide_when_gateway_not_bindable(monkeypatch):
    # Docker Desktop on Linux: the reported gateway lives inside the Desktop VM
    # and is not a host address — binding it would crash uvicorn at boot.
    monkeypatch.setattr(role_all, "_system", lambda: "Linux", raising=False)
    monkeypatch.setattr(role_all, "_docker_bridge_gateway", lambda: "192.168.65.1", raising=False)
    monkeypatch.setattr(role_all, "_locally_bindable", lambda ip: False, raising=False)
    hosts = _agent_facing_hosts(monkeypatch)
    s = get_settings()
    assert hosts[s.rest_port] == ("0.0.0.0",)
    assert hosts[s.otlp_port] == ("0.0.0.0",)


def test_explicit_wildcard_host_opts_back_into_wide(monkeypatch):
    # Escape hatch: an operator who deliberately serves the UI to the LAN sets
    # XORCISE_HOST=0.0.0.0 and gets the historical wildcard bind back.
    monkeypatch.setattr(role_all, "_system", lambda: "Darwin", raising=False)
    monkeypatch.setenv("XORCISE_HOST", "0.0.0.0")
    hosts = _agent_facing_hosts(monkeypatch)
    s = get_settings()
    assert hosts[s.rest_port] == ("0.0.0.0",)
    assert hosts[s.otlp_port] == ("0.0.0.0",)


def test_explicit_lan_host_is_served_alongside_loopback(monkeypatch):
    # An operator who configured a concrete LAN host was previously covered by the
    # wildcard; the narrowed bind must keep serving that address explicitly.
    monkeypatch.setattr(role_all, "_system", lambda: "Darwin", raising=False)
    monkeypatch.setenv("XORCISE_HOST", "192.168.1.10")
    hosts = _agent_facing_hosts(monkeypatch)
    s = get_settings()
    assert hosts[s.rest_port] == ("127.0.0.1", "::1", "192.168.1.10")
    assert hosts[s.otlp_port] == ("127.0.0.1", "::1", "192.168.1.10")


def test_distributed_keeps_default_bind(monkeypatch):
    monkeypatch.setenv("XORCISE_DEPLOYMENT_TOPOLOGY", "distributed")
    get_settings.cache_clear()
    specs = {s.port: s for s in role_all.apps()}
    assert all(sp.host in (None, get_settings().host) for sp in specs.values())
