"""Composition point for the macOS container-runtime decision (DinD vs host-daemon siblings).

This is the ONLY place that puts the three pieces together: the `macos_container_runtime`
setting (config), the capability probe (runner), and the cached Tier 2 verdict (home). It lives
in `rest` because `runner` is a part-island that must not import `home`, and `config` is
shared-kernel that must not import upward — `rest` sits above all three.

The Tier 2 probe costs 20-40 s, far too much to pay per deploy, so its verdict is cached under
the operator's home against a fingerprint of the host state (Docker version, macOS version, the
Rosetta binfmt registration). Tier 1 still runs on every resolution — it is cheap, and it is what
catches a mid-session Rosetta toggle or VMM switch, which would otherwise sit behind a stale
cache entry until the next upgrade.

The cache is advisory in both directions: an unreadable or corrupt file simply re-probes, and a
write failure is swallowed. Losing the cache costs time, never correctness.

The Docker client arrives as a FACTORY, not a client. Under the shipped default (`host-daemon`)
the decision is reached without probing anything, so no daemon connection is opened at all —
resolving the runtime must not become a new way for `xorcise` to fail on a host with no Docker.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from xorcise.core.config import Settings
from xorcise.core.home import xorcise_home
from xorcise.core.runner.docker.rosetta import (
    RosettaProbe,
    RuntimeDecision,
    binfmt_signal,
    decide,
    host_is_macos,
    verify_nested_amd64,
)

log = logging.getLogger(__name__)

CACHE_NAME = "rosetta-probe.json"

# Reasons already logged in this process. The decision is deliberately NOT memoised — Tier 1 is
# the per-deploy guard and must actually run — so this keeps a long-lived server from repeating
# the same line on every run, while still logging the moment the verdict CHANGES.
_logged: set[str] = set()


def cache_path(home: Path | None = None) -> Path:
    return (home or xorcise_home()) / CACHE_NAME


def read_cached(fingerprint: str, *, home: Path | None = None) -> RosettaProbe | None:
    """The cached Tier 2 verdict, iff it was recorded against this exact host fingerprint."""
    path = cache_path(home)
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("fingerprint") != fingerprint:
        return None  # host moved underneath the verdict — re-probe
    return RosettaProbe(
        ok=bool(record.get("ok")),
        detail=str(record.get("detail", "")),
        fingerprint=fingerprint,
    )


def write_cached(probe: RosettaProbe, *, home: Path | None = None) -> None:
    path = cache_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fingerprint": probe.fingerprint, "ok": probe.ok, "detail": probe.detail})
        )
    except OSError:  # a read-only home must not break a run — we just re-probe next time
        log.debug("could not write the rosetta probe cache at %s", path)


def _once(factory: Callable[[], object]) -> Callable[[], object]:
    """Call `factory` at most once per resolution — both tiers want the same client, and neither
    should open a connection when the setting alone decides the answer."""
    box: list[object] = []

    def get() -> object:
        if not box:
            box.append(factory())
        return box[0]

    return get


def resolve_runtime(
    settings: Settings,
    client_factory: Callable[[], object],
    *,
    home: Path | None = None,
    nested_image: str | None = None,
) -> RuntimeDecision:
    """Resolve the container runtime for this host, consulting/refreshing the Tier 2 cache.

    `nested_image` lets a caller point Tier 2 at the fused mission image instead of the generic
    `docker:dind` — the fused image is the exact artifact the decision is about, and it is already
    local, so it is both higher-fidelity and cheaper when one is available.
    """
    client = _once(client_factory)

    def tier2(tier1: RosettaProbe) -> RosettaProbe:
        cached = read_cached(tier1.fingerprint, home=home)
        if cached is not None:
            return cached
        kwargs = {"image": nested_image} if nested_image else {}
        probe = verify_nested_amd64(client(), **kwargs)  # type: ignore[arg-type]
        # Stamp the Tier 1 fingerprint on the Tier 2 verdict so the cache key is the host state,
        # not the probe's own (Tier 2 does not compute one).
        probe = RosettaProbe(probe.ok, probe.detail, tier1.fingerprint)
        write_cached(probe, home=home)
        return probe

    return decide(
        settings.macos_container_runtime,
        is_macos=host_is_macos(),
        probe_tier1=lambda: binfmt_signal(client()),
        probe_tier2=tier2,
    )


def inspect_runtime(
    settings: Settings,
    client_factory: Callable[[], object],
    *,
    home: Path | None = None,
) -> tuple[RuntimeDecision, RosettaProbe | None]:
    """`doctor`'s view: the mode this host WOULD use, plus the nested-Rosetta verdict regardless
    of whether the setting made it relevant.

    Reporting the probe even when the setting pins the mode is the entire point of the first
    rollout step — the default is `host-daemon`, so without this the probe would never run on a
    real host and there would be nothing to collect. Tier 2 is read from cache but never
    REFRESHED here: `doctor` is interactive and must not silently block for 20-40 s.
    """
    decision = resolve_runtime(settings, client_factory, home=home)
    if decision.probe is not None:  # `auto` already probed — don't pay for it twice
        return decision, decision.probe
    if not host_is_macos():
        return decision, None
    tier1 = binfmt_signal(client_factory())
    if not tier1.ok:
        return decision, tier1
    return decision, read_cached(tier1.fingerprint, home=home) or tier1


def log_decision(decision: RuntimeDecision) -> None:
    """Log the chosen mode once per distinct verdict (see `_logged`)."""
    line = f"{decision.mode}: {decision.reason}"
    if line in _logged:
        return
    _logged.add(line)
    log.info("macOS container runtime — %s", line)
