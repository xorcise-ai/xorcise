"""Composition point for the nested-container precondition.

The mission stack always runs INSIDE the run's own container. The former host-daemon "sibling"
topology is gone (see runner/docker/rosetta.py for why), so "can this host nest containers?" is
no longer a choice between two runtimes — it is a yes/no precondition for deploying a lab
mission, and a "no" must fail the run.

This is the ONLY place that puts the three pieces together: the `nested_container_check` setting
(config), the capability probe (runner), and the cached verdict (home). It lives in `rest`
because `runner` is a part-island that must not import `home`, and `config` is shared-kernel that
must not import upward — `rest` sits above all three.

The Tier 2 probe costs ~10-40 s, far too much to pay per run, so its verdict is cached under the
operator's home against a fingerprint of the host state (Docker version, macOS version, the
Rosetta binfmt registration). Tier 1 still runs on every check — it is cheap (~0.2 s), it is what
produces that fingerprint, and it is therefore what notices a mid-session Rosetta toggle that
would otherwise sit behind a stale positive.

The cache is advisory in both directions: an unreadable or corrupt file simply re-probes, and a
write failure is swallowed. Losing the cache costs time, never correctness.

The Docker client arrives as a FACTORY, not a client, so a check that is configured off never
opens a connection.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from xorcise.core.config import Settings
from xorcise.core.contracts.errors import NestedContainersUnavailableError
from xorcise.core.home import xorcise_home
from xorcise.core.runner.docker.rosetta import (
    NestedSupport,
    RosettaProbe,
    binfmt_signal,
    check_nested_support,
    verify_nested_amd64,
)

log = logging.getLogger(__name__)

CACHE_NAME = "nested-support.json"

# Verdicts already logged in this process. The check is deliberately NOT memoised — Tier 1 is the
# per-run guard and must actually run — so this keeps a long-lived server from repeating the same
# line on every run, while still logging the moment the verdict CHANGES.
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
        log.debug("could not write the nested-support cache at %s", path)


def _once(factory: Callable[[], object]) -> Callable[[], object]:
    """Call `factory` at most once per check — both tiers want the same client, and neither
    should open a connection when the setting alone decides the answer."""
    box: list[object] = []

    def get() -> object:
        if not box:
            box.append(factory())
        return box[0]

    return get


def nested_support(
    settings: Settings,
    client_factory: Callable[[], object],
    *,
    home: Path | None = None,
    nested_image: str | None = None,
) -> NestedSupport:
    """Can this host nest containers? Consults/refreshes the Tier 2 cache.

    `nested_image` lets a caller point Tier 2 at the fused mission image instead of the generic
    dind base — the fused image is the exact artifact the answer is about, and it is already
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

    return check_nested_support(
        skip=settings.nested_container_check == "skip",
        probe_tier1=lambda: binfmt_signal(client()),
        probe_tier2=tier2,
    )


def require_nested_support(
    settings: Settings,
    client_factory: Callable[[], object],
    *,
    home: Path | None = None,
    nested_image: str | None = None,
) -> None:
    """Raise NestedContainersUnavailableError unless this host can nest containers.

    Call this BEFORE anything stateful or expensive (subnet reservation, control-plane fence,
    image pull) so a host that cannot run missions costs an error message rather than a
    half-built run that has to be torn down.
    """
    support = nested_support(settings, client_factory, home=home, nested_image=nested_image)
    log_support(support)
    if support.ok:
        return
    # The probe's detail often already ends in a period (it quotes a runtime error verbatim), so
    # only add one where it is missing — a stray ".." reads like a typo in the one message an
    # operator sees when nothing works.
    detail = support.detail.rstrip(". ")
    raise NestedContainersUnavailableError(
        f"this host cannot run a mission's containers inside the run container — {detail}. "
        f"{support.remediation}. "
        "To bypass this check on a host you know is fine, set "
        "XORCISE_NESTED_CONTAINER_CHECK=skip"
    )


def log_support(support: NestedSupport) -> None:
    """Log the verdict once per distinct value (see `_logged`)."""
    line = f"{'ok' if support.ok else 'unavailable'}: {support.detail}"
    if line in _logged:
        return
    _logged.add(line)
    (log.info if support.ok else log.warning)("nested containers — %s", line)
