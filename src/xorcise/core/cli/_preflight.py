"""Pre-flight checks for the lifecycle commands (cli).

Pure, side-effect-free helpers: detect whether a port is bindable and render an
actionable conflict message. Used by `up` (before spawning) and `serve` (before uvicorn).
"""

from __future__ import annotations

import errno
import os
import socket
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
    """Bind (not yet listening) `host:port` exactly the way asyncio's `create_server(host=…)`
    would, and hand the socket back for the server to adopt. Raises OSError on failure.

    Binding here, once, is the preflight: the same socket is then passed to uvicorn, so there is
    no gap in which another process can take the port between the check and the listen. Options
    mirror CPython's base_events.create_server — SO_REUSEADDR on POSIX (so a port left in
    TIME_WAIT by the previous server rebinds immediately) and IPV6_V6ONLY on an IPv6 socket (so
    the IPv6 loopback listener never shadows or collides with the IPv4 one). asyncio calls
    listen() itself when it adopts the socket."""
    sock = socket.socket(address_family(host), socket.SOCK_STREAM)
    try:
        if os.name == "posix":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if sock.family == socket.AF_INET6 and hasattr(socket, "IPPROTO_IPV6"):
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.bind((host, port))
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


def find_free_port(
    host: str,
    start: int,
    attempts: int = _SCAN_ATTEMPTS,
    taken: AbstractSet[int] = frozenset(),
    label: str = "port",
) -> int:
    """First free port at-or-above `start` (walks start, start+1, … for `attempts` probes).

    A port only counts as free when `host`, the IPv4 wildcard ("0.0.0.0") AND the IPv6
    loopback ("::1") are all bindable: a wildcard probe refuses a listener on any single IPv4
    interface, and the IPv6 probe sees the `[::1]` listener an IPv4 probe cannot — role:all
    binds every agent-facing plane on the IPv6 loopback too, so a port that passes here cannot
    pass preflight and then crash uvicorn on an address-specific conflict. Reuses _bindable,
    keeping the SO_REUSEADDR/TIME_WAIT semantics (and "IPv6 disabled" reads as free, not
    taken). `taken` holds ports already promised to other planes in the same resolution round.
    """
    for candidate in range(start, min(start + attempts, _MAX_PORT + 1)):
        if candidate in taken:
            continue
        if (
            _bindable(host, candidate)
            and (host == "0.0.0.0" or _bindable("0.0.0.0", candidate))
            and (host == IPV6_LOOPBACK or _bindable(IPV6_LOOPBACK, candidate))
        ):
            return candidate
    raise PortScanError(label, start, attempts)


def resolve_ports(host: str, wanted: dict[str, int]) -> dict[str, int]:
    """Resolve each plane's requested port to the first free one at-or-above it.

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
