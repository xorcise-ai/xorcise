"""`serve` binds every address up front and runs ONE uvicorn.Server per app.

Two properties carry the weight. Each app gets exactly one Server (so its ASGI lifespan, and
every startup/shutdown hook, runs once) with one pre-bound socket per address it must serve. And
binding IS the preflight: a squatter on any of those addresses — including `[::1]`, which the
old IPv4-only probe could not see — is reported as a clean conflict before any server starts,
never as uvicorn's sys.exit(1) from inside Server.startup().
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from xorcise.core.cli.commands import serve as serve_mod
from xorcise.core.roles.boot import AppSpec

pytestmark = pytest.mark.unit


def _released_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ipv6_loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
            s.bind(("::1", 0))
            return True
    except OSError:
        return False


needs_ipv6 = pytest.mark.skipif(not _ipv6_loopback_available(), reason="no IPv6 loopback here")


async def _noop_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover - not run
    pass


@needs_ipv6
def test_bind_specs_binds_one_socket_per_address_per_app() -> None:
    p1, p2 = _released_port(), _released_port()
    specs = [
        AppSpec(_noop_app, p1, host=("127.0.0.1", "::1")),
        AppSpec(_noop_app, p2, host=("127.0.0.1", "::1")),
    ]
    bound = serve_mod.bind_specs(specs, "127.0.0.1")
    try:
        assert [b.spec.port for b in bound] == [p1, p2]
        for b in bound:
            assert b.hosts == ["127.0.0.1", "::1"]
            assert [s.family for s in b.sockets] == [socket.AF_INET, socket.AF_INET6]
            assert [s.getsockname()[1] for s in b.sockets] == [b.spec.port, b.spec.port]
    finally:
        for b in bound:
            b.close()


@needs_ipv6
def test_a_squatter_on_the_ipv6_loopback_is_a_clean_conflict() -> None:
    """Finding 1 of #43: `_bindable` hardcoded AF_INET, so a listener on `[::1]:PORT` with
    `127.0.0.1:PORT` free passed preflight, and uvicorn then hit the conflict inside
    Server.startup() and called sys.exit(1) — a raw traceback, no remediation."""
    port = _released_port()
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as squatter:
        squatter.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        squatter.bind(("::1", port))
        squatter.listen()
        with pytest.raises(serve_mod.BindConflict) as exc:
            serve_mod.bind_specs([AppSpec(_noop_app, port, host=("127.0.0.1", "::1"))], "127.0.0.1")
        assert exc.value.taken == {"::1": [port]}
    # Everything bound before the conflict was released: the IPv4 side is free again.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))


def test_a_squatter_on_ipv4_is_reported_with_its_host() -> None:
    port = _released_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as squatter:
        squatter.bind(("127.0.0.1", port))
        squatter.listen()
        with pytest.raises(serve_mod.BindConflict) as exc:
            serve_mod.bind_specs([AppSpec(_noop_app, port, host="127.0.0.1")], "127.0.0.1")
        assert exc.value.taken == {"127.0.0.1": [port]}


def test_missing_ipv6_loopback_is_a_warning_not_a_failure(monkeypatch, capsys) -> None:
    """A host with IPv6 disabled has no `::1`; the IPv4 listeners must still serve."""
    import errno

    from xorcise.core.cli._preflight import bind_listener as real

    port = _released_port()

    def _no_v6(host: str, p: int) -> socket.socket:
        if host == "::1":
            raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
        return real(host, p)

    monkeypatch.setattr(serve_mod, "bind_listener", _no_v6)
    bound = serve_mod.bind_specs([AppSpec(_noop_app, port, host=("127.0.0.1", "::1"))], "127.0.0.1")
    try:
        assert bound[0].hosts == ["127.0.0.1"]
        assert len(bound[0].sockets) == 1
    finally:
        bound[0].close()
    assert "IPv4 only" in capsys.readouterr().err


def test_serve_bound_runs_one_server_per_app_with_all_its_sockets(monkeypatch) -> None:
    """The structural fix for #43: N sockets per app go to ONE Server (`serve(sockets=…)`), not
    one Server per socket. uvicorn runs the lifespan once per Server, so the app's startup hooks
    run once."""
    import uvicorn

    made: list[dict[str, Any]] = []

    class _Config:
        def __init__(self, app: Any, **kw: Any) -> None:
            self.app = app
            self.kw = kw

    class _Server:
        def __init__(self, config: _Config) -> None:
            self.config = config
            self.should_exit = False
            self.started = False

        async def serve(self, sockets: list[socket.socket] | None = None) -> None:
            self.started = True  # what uvicorn sets once startup() succeeded
            made.append({"app": self.config.app, "sockets": list(sockets or [])})

    monkeypatch.setattr(uvicorn, "Config", _Config)
    monkeypatch.setattr(uvicorn, "Server", _Server)

    a_sockets = [socket.socket(), socket.socket()]
    b_sockets = [socket.socket(), socket.socket(), socket.socket()]
    bound = [
        serve_mod.BoundSpec(AppSpec("app-a", 1), ["127.0.0.1", "::1"], a_sockets),
        serve_mod.BoundSpec(AppSpec("app-b", 2), ["127.0.0.1", "::1", "172.17.0.1"], b_sockets),
    ]
    try:
        failures = asyncio.run(serve_mod.serve_bound(bound))
    finally:
        for b in bound:
            b.close()
    assert failures == []
    assert [m["app"] for m in made] == ["app-a", "app-b"]  # one Server per APP…
    assert made[0]["sockets"] == a_sockets  # …carrying every one of its listeners
    assert made[1]["sockets"] == b_sockets


def test_a_failed_lifespan_startup_is_reported_as_a_failure() -> None:
    """Review of #81: uvicorn returns normally from serve() when the app's startup fails
    (`started` stays False), so the exception filter alone found nothing and `serve` exited 0
    having served nothing."""

    async def failing_app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await receive()
            await send({"type": "lifespan.startup.failed", "message": "config error"})

    port = _released_port()
    bound = serve_mod.bind_specs([AppSpec(failing_app, port, host="127.0.0.1")], "127.0.0.1")
    try:
        failures = asyncio.run(serve_mod.serve_bound(bound))
    finally:
        for b in bound:
            b.close()
    assert len(failures) == 1
    assert "never started" in str(failures[0]) and str(port) in str(failures[0])


def test_bind_specs_holds_the_address_so_a_concurrent_binder_loses_immediately() -> None:
    """Review of #81: bound-but-not-listening sockets share an address under SO_REUSEADDR, so a
    concurrent `serve` could bind the same port and win the listen() race inside uvicorn. The
    sockets bind_specs hands out are listening, so the rival fails at its own bind()."""
    port = _released_port()
    bound = serve_mod.bind_specs([AppSpec(_noop_app, port, host="127.0.0.1")], "127.0.0.1")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rival:
            rival.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError):
                rival.bind(("127.0.0.1", port))
    finally:
        for b in bound:
            b.close()


def test_listener_lines_name_every_bound_address() -> None:
    bound = [
        serve_mod.BoundSpec(AppSpec("a", 3001), ["127.0.0.1", "::1", "172.17.0.1"], []),
        serve_mod.BoundSpec(AppSpec("b", 4318), ["127.0.0.1"], []),
    ]
    assert serve_mod.listener_lines(bound) == [
        "listening on 127.0.0.1:3001, [::1]:3001, 172.17.0.1:3001",
        "listening on 127.0.0.1:4318",
    ]
