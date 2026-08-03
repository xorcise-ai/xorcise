"""Update-available notice for `up` (cli).

Asks PyPI for the newest published xorcise version — in a background thread,
answer cached in the home for a day — and renders a one-line notice when it is
newer than the installed release. Failure of any kind (offline host, PyPI down,
the package not yet published, an unreadable cache) resolves to "no notice":
the check must never slow `up`, break it, or nag when it cannot know better.
Source checkouts (a local version segment, e.g. ``0.0.2.dev12+gabc123``) are
never nagged — they update with git, not pip. ``XORCISE_NO_UPDATE_CHECK=1``
opts out entirely (nothing is fetched, nothing is written).
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from xorcise import __version__
from xorcise.core.cli._shared import console
from xorcise.core.home import xorcise_home

_PYPI_JSON_URL = "https://pypi.org/pypi/xorcise/json"
_CACHE_FILE = "update-check.json"
# One network hit per day at most; a FAILED fetch is cached too, so an offline
# host pays the (threaded, capped) attempt once per TTL, not once per `up`.
_CACHE_TTL_SECONDS = 24 * 60 * 60
_OPT_OUT_ENV = "XORCISE_NO_UPDATE_CHECK"
_FETCH_TIMEOUT = 2.0
# The ready banner waits at most this long for the thread — by then the health
# poll has already paid for the network round-trip many times over.
_JOIN_TIMEOUT = 0.75


def _fetch_latest() -> str | None:
    """The newest version PyPI knows, else None (404 = not yet published)."""
    try:
        resp = httpx.get(_PYPI_JSON_URL, timeout=_FETCH_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    info = data.get("info") if isinstance(data, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return version if isinstance(version, str) else None


def _cache_path() -> Path:
    return Path(xorcise_home()) / _CACHE_FILE


def _read_cache(now: float) -> tuple[bool, str | None]:
    """(still fresh, cached latest) — a missing/corrupt/expired cache reads as stale."""
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (False, None)
    if not isinstance(data, dict) or not isinstance(data.get("checked_at"), int | float):
        return (False, None)
    if not (0 <= now - float(data["checked_at"]) <= _CACHE_TTL_SECONDS):
        return (False, None)
    latest = data.get("latest")
    return (True, latest if isinstance(latest, str) else None)


def latest_published_version(*, now: float | None = None) -> str | None:
    """The newest PyPI version, hitting the network at most once per TTL."""
    now = time.time() if now is None else now
    fresh, cached = _read_cache(now)
    if fresh:
        return cached
    latest = _fetch_latest()
    # Best-effort: an unwritable home must not turn a cosmetic notice into an error.
    with contextlib.suppress(OSError):
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": now, "latest": latest}), encoding="utf-8")
    return latest


def update_notice(installed: str, latest: str | None) -> str | None:
    """The rendered one-line notice, or None when there is nothing to say."""
    if not latest:
        return None
    from packaging.version import InvalidVersion, Version

    try:
        have, avail = Version(installed), Version(latest)
    except InvalidVersion:
        return None
    if have.local is not None:  # a source checkout (setuptools-scm +g<sha>) updates via git
        return None
    if avail <= have:
        return None
    return (
        f"[dim]update available:[/dim] [accent]v{avail}[/accent] "
        f"[dim](installed v{have}) — upgrade:[/dim] [value]pip install -U xorcise[/value]"
    )


def begin_update_check() -> Callable[[], str | None]:
    """Start the check without blocking `up`; the returned closure yields the notice.

    Call the closure where the notice would print (the ready banner). It waits
    at most ``_JOIN_TIMEOUT`` for the thread and returns None whenever there is
    nothing to show — a still-running check simply misses this `up` (the cache
    makes the next one instant). Non-TTY invocations never check: the notice is
    advice for a human at a terminal, not output for a script.
    """
    if os.environ.get(_OPT_OUT_ENV) or not console.is_terminal:
        return lambda: None
    result: list[str | None] = [None]

    def _work() -> None:
        result[0] = latest_published_version()

    thread = threading.Thread(target=_work, name="xorcise-update-check", daemon=True)
    thread.start()

    def _notice() -> str | None:
        thread.join(timeout=_JOIN_TIMEOUT)
        if thread.is_alive():
            return None
        return update_notice(__version__, result[0])

    return _notice
