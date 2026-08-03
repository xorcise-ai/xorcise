import socket
import time

import pytest

from xorcise.core.cli._preflight import (
    PortScanError,
    conflict_message,
    find_free_port,
    ports_in_use,
    resolve_ports,
)


def _released_port(host: str = "127.0.0.1") -> int:
    """A port number that was just free (bound at 0, then released)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def _plain_bind_fails(host: str, port: int) -> bool:
    """True if a plain bind() (no SO_REUSEADDR) is refused — i.e. the port is occupied."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def test_ports_in_use_treats_time_wait_port_as_free():
    """A port occupied only by a TIME_WAIT socket (no listener) must read as free.

    After `xorcise down`, the server's ports linger in TIME_WAIT; uvicorn rebinds them
    with SO_REUSEADDR, so the preflight must too — otherwise `up` right after `down` is
    falsely blocked until TIME_WAIT expires (~30-60s). Reproduces a TIME_WAIT socket by
    letting the server actively close an established loopback connection.
    """
    host = "127.0.0.1"
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, 0))
    srv.listen()
    port = srv.getsockname()[1]
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        cli.connect((host, port))
        conn, _ = srv.accept()
        srv.close()  # stop listening — no live listener remains on `port`
        conn.close()  # server actively closes first → TIME_WAIT on (host, port)
        cli.close()

        # Precondition: the port is genuinely occupied (a plain bind is refused).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not _plain_bind_fails(host, port):
            time.sleep(0.02)
        assert _plain_bind_fails(host, port), "expected TIME_WAIT to occupy the port"

        # The SO_REUSEADDR preflight must report it FREE (poll while FIN_WAIT settles).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and ports_in_use(host, [port]) != []:
            time.sleep(0.02)
        assert ports_in_use(host, [port]) == []
    finally:
        srv.close()
        cli.close()


def test_ports_in_use_detects_a_bound_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert ports_in_use("127.0.0.1", [port]) == [port]


def test_ports_in_use_empty_when_free():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # released when the with-block exits
    assert ports_in_use("127.0.0.1", [port]) == []


def test_conflict_message_names_port_plane_and_remediation():
    msg = conflict_message("127.0.0.1", [3001])
    assert "3001" in msg
    assert "rest" in msg
    assert "xorcise down" in msg  # actionable remediation, no stack trace


def test_conflict_message_labels_configured_nondefault_port():
    # A configured rest_port=4001 must be labelled "rest", not degrade to "?".
    msg = conflict_message("127.0.0.1", [4001], planes={4001: "rest"})
    assert "4001" in msg
    assert "(rest)" in msg


def test_find_free_port_returns_start_when_free():
    port = _released_port()
    assert find_free_port("127.0.0.1", port) == port


def test_find_free_port_walks_past_a_bound_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        got = find_free_port("127.0.0.1", port)
        assert got > port  # auto-incremented past the busy port
        assert ports_in_use("127.0.0.1", [got]) == []  # and the result is actually bindable


def test_find_free_port_skips_ports_promised_to_other_planes():
    port = _released_port()
    assert find_free_port("127.0.0.1", port, taken={port}) != port


def test_find_free_port_raises_after_attempts(monkeypatch):
    monkeypatch.setattr("xorcise.core.cli._preflight._bindable", lambda host, port: False)
    with pytest.raises(PortScanError) as ei:
        find_free_port("127.0.0.1", 3001, attempts=3, label="rest")
    assert "rest" in str(ei.value)
    assert "3001" in str(ei.value)


def test_find_free_port_rejects_listener_on_another_interface():
    """role:all binds the wildcard, so a listener on ANY interface makes a port unusable —
    a port free on `host` but bound on another loopback address must be walked past."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as other:
        other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            other.bind(("127.0.0.2", 0))
        except OSError:
            pytest.skip("secondary loopback address unavailable on this host")
        other.listen()
        port = other.getsockname()[1]
        assert find_free_port("127.0.0.1", port) != port


def test_resolve_ports_returns_wanted_when_free():
    p1 = _released_port()
    p2 = _released_port()
    if p1 == p2:  # pragma: no cover — ephemeral collision
        pytest.skip("ephemeral ports collided")
    assert resolve_ports("127.0.0.1", {"rest": p1, "otlp": p2}) == {"rest": p1, "otlp": p2}


def test_resolve_ports_never_assigns_the_same_port_twice():
    port = _released_port()
    resolved = resolve_ports("127.0.0.1", {"rest": port, "otlp": port})
    assert resolved["rest"] == port
    assert resolved["otlp"] != port  # the second plane must not collide with the first
    assert len(set(resolved.values())) == 2
