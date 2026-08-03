"""Pre-flight checks for the lifecycle commands (cli).

Pure, side-effect-free helpers: detect whether a port is bindable and render an
actionable conflict message. Used by `up` (before spawning) and `serve` (before uvicorn).
"""

from __future__ import annotations

import socket
from collections.abc import Set as AbstractSet

# Human label per known plane port, for the conflict message.
_PLANE = {3001: "rest", 4318: "otlp", 8800: "runner", 8080: "headscale"}

# How far past the requested port the auto-increment scan walks before giving up.
_SCAN_ATTEMPTS = 50
_MAX_PORT = 65535


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
    """True if we can bind host:port right now.

    Sets SO_REUSEADDR so the probe matches how uvicorn actually binds: a port left in
    TIME_WAIT after `xorcise down` (the server's just-closed connections) reads as free,
    while a port with a live listener still fails to bind and reads as in use. Without
    this, `up` right after `down` is falsely blocked until TIME_WAIT expires (~30-60s).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


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

    A port only counts as free when BOTH `host` and the IPv4 wildcard ("0.0.0.0") are
    bindable: role:all binds REST/OTLP on the wildcard, and a wildcard probe also refuses
    a listener on any single interface — so a port that passes here cannot pass preflight
    and then crash uvicorn on an interface-specific conflict. Reuses _bindable, keeping
    the SO_REUSEADDR/TIME_WAIT semantics. `taken` holds ports already promised to other
    planes in the same resolution round.
    """
    for candidate in range(start, min(start + attempts, _MAX_PORT + 1)):
        if candidate in taken:
            continue
        if _bindable(host, candidate) and (host == "0.0.0.0" or _bindable("0.0.0.0", candidate)):
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
