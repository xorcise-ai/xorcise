"""Pre-flight checks for the lifecycle commands (cli).

Pure, side-effect-free helpers: detect whether a port is bindable and render an
actionable conflict message. Used by `up` (before spawning) and `serve` (before uvicorn).
"""

from __future__ import annotations

import errno
import os
import socket
from collections.abc import Sequence
from collections.abc import Set as AbstractSet

# Human label per known plane port, for the conflict message.
_PLANE = {3001: "rest", 4318: "otlp", 8800: "runner", 8080: "headscale"}

# How far past the requested port the auto-increment scan walks before giving up.
_SCAN_ATTEMPTS = 50
_MAX_PORT = 65535

# The IPv6 loopback every agent-facing spec also listens on (role_all + _bind_hosts). A probe on
# the IPv4 side alone cannot see a squatter here, which is how a `[::1]:3001` listener used to
# pass preflight and then crash uvicorn from inside Server.startup() with a bare sys.exit(1).
IPV6_LOOPBACK = "::1"

# bind() errors that mean "this address does not exist here" rather than "someone holds it":
# IPv6 disabled on the host, or a configured host that is not a local interface.
_ADDRESS_UNAVAILABLE = frozenset(
    e for e in (errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT, getattr(errno, "EPFNOSUPPORT", None)) if e
)


def address_family(host: str) -> int:
    """AF_INET6 for a literal IPv6 address, AF_INET otherwise."""
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def address_unavailable(exc: OSError) -> bool:
    """Whether a failed bind means the ADDRESS is not usable on this host at all (as opposed to
    the port being held by someone). Such an address cannot be "in use"."""
    return exc.errno in _ADDRESS_UNAVAILABLE


def bind_listener(host: str, port: int) -> socket.socket:
    """Bind AND listen on `host:port` the way asyncio's `create_server(host=…)` would, and hand
    the socket back for the server to adopt. Raises OSError on failure.

    The listen() is what makes this an atomic preflight. `SO_REUSEADDR` deliberately lets two
    sockets bind the same address while NEITHER is listening (that is how a port in TIME_WAIT is
    reused), so a bound-but-not-listening socket holds nothing: a concurrent `serve` would bind
    it too, and the loser would only find out at listen() — inside uvicorn's socket-adoption
    path, after the app's startup hooks had already run, with no shutdown to follow. Listening
    here means the conflict surfaces in THIS call, where the caller reports it cleanly, and the
    address is held from this moment on. asyncio's listen() on adoption merely re-applies the
    backlog. Options mirror CPython's base_events.create_server — SO_REUSEADDR on POSIX (so a
    port left in TIME_WAIT by the previous server rebinds immediately) and IPV6_V6ONLY on an IPv6
    socket (so the IPv6 loopback listener never shadows or collides with the IPv4 one)."""
    sock = socket.socket(address_family(host), socket.SOCK_STREAM)
    try:
        if os.name == "posix":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if sock.family == socket.AF_INET6 and hasattr(socket, "IPPROTO_IPV6"):
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((host, port))
        sock.listen()
    except OSError:
        sock.close()
        raise
    return sock


class PortScanError(RuntimeError):
    """No free port found within the scan window (start … start+attempts-1)."""

    def __init__(self, label: str, start: int, attempts: int) -> None:
        self.label = label
        self.start = start
        self.attempts = attempts
        super().__init__(
            f"no free port for {label} in {start}-{min(start + attempts - 1, _MAX_PORT)} — "
            "free one up or configure a different base port"
        )


def _bindable(host: str, port: int) -> bool:
    """True if nobody else holds host:port right now.

    Probes with the exact socket the server will bind (bind_listener), for either address
    family: SO_REUSEADDR so a port left in TIME_WAIT after `xorcise down` (the server's
    just-closed connections) reads as free while a live listener still reads as in use —
    without it, `up` right after `down` was falsely blocked until TIME_WAIT expired (~30-60 s).
    An address this host cannot bind AT ALL (IPv6 disabled, a non-local host) is not "in use"
    either: nobody can be holding it, and reporting it as taken would make every port look busy.
    """
    try:
        sock = bind_listener(host, port)
    except OSError as exc:
        return address_unavailable(exc)
    sock.close()
    return True


def ports_in_use(host: str, ports: list[int]) -> list[int]:
    """Return the subset of `ports` already bound on `host`, in input order."""
    return [p for p in ports if not _bindable(host, p)]


def _probe_addresses(hosts: str | Sequence[str]) -> list[str]:
    """The addresses a port must be free on for the listener set `hosts`: every address the
    server will bind, plus the IPv4 wildcard when any of them is a specific IPv4 interface — a
    wildcard probe refuses a listener on ANY single interface, so it catches a squatter on an
    interface the set does not name. Nothing else: the IPv6 loopback is probed exactly when it
    is in the set (role:all on a local topology), not for a role whose spec never binds it — a
    `[::1]:8800` listener must not relocate `serve --role runner`."""
    listen_on = [hosts] if isinstance(hosts, str) else list(hosts)
    probe = list(dict.fromkeys(listen_on))
    if any(address_family(h) == socket.AF_INET and h != "0.0.0.0" for h in listen_on):
        probe.append("0.0.0.0")
    return probe


def find_free_port(
    host: str | Sequence[str],
    start: int,
    attempts: int = _SCAN_ATTEMPTS,
    taken: AbstractSet[int] = frozenset(),
    label: str = "port",
) -> int:
    """First free port at-or-above `start` (walks start, start+1, … for `attempts` probes).

    `host` is the address the server will bind, or the WHOLE list of addresses it will bind
    (`_bind_hosts` of the role's spec) — the scan probes exactly that set, plus the IPv4
    wildcard for any specific IPv4 interface in it (see _probe_addresses). Handing the real
    listener set in, rather than a hand-maintained guess of it, is what keeps this from
    drifting behind `_bind_hosts`/`bind_specs` again. Reuses _bindable, keeping the
    SO_REUSEADDR/TIME_WAIT semantics (and "IPv6 disabled" reads as free, not taken). `taken`
    holds ports already promised to other planes in the same resolution round.
    """
    addresses = _probe_addresses(host)
    for candidate in range(start, min(start + attempts, _MAX_PORT + 1)):
        if candidate in taken:
            continue
        if all(_bindable(address, candidate) for address in addresses):
            return candidate
    raise PortScanError(label, start, attempts)


def resolve_ports(host: str | Sequence[str], wanted: dict[str, int]) -> dict[str, int]:
    """Resolve each plane's requested port to the first free one at-or-above it, on the
    address (or the full listener set) the planes will bind.

    Planes resolve in dict order; earlier choices feed into `taken` so two planes
    contending for the same base port never resolve to the same one. Raises
    PortScanError (naming the plane) when a plane's scan window is exhausted.
    """
    resolved: dict[str, int] = {}
    for plane, start in wanted.items():
        resolved[plane] = find_free_port(
            host, start, taken=frozenset(resolved.values()), label=plane
        )
    return resolved


def conflict_message(host: str, ports: list[int], planes: dict[int, str] | None = None) -> str:
    """Actionable remediation for taken ports (no stack trace).

    `planes` lets callers label configured non-default ports (e.g. rest_port=4001);
    it overlays the well-known default map.
    """
    names = {**_PLANE, **(planes or {})}
    return "\n".join(
        f"port {p} ({names.get(p, '?')}) on {host} is in use — "
        f"stop the process using it or run 'xorcise down', then retry"
        for p in ports
    )
