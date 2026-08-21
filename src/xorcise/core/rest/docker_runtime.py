"""Composition point for the nested-container precondition and base-generation gate.

The mission stack always runs INSIDE the run's own container. The former host-daemon "sibling"
topology is gone (see runner/docker/rosetta.py for why), so "can this host nest containers?" is
no longer a choice between two runtimes — it is a yes/no precondition for deploying a lab
mission, and a "no" must fail the run.

Two separate questions live here:

  * HOST capability — can this machine run a container inside a container at all? A behavioural
    probe (runner/docker/rosetta.py). Expensive (~20-40 s for Tier 2), so its verdict is memoised
    IN MEMORY for the server's lifetime (host capability does not change without a Docker restart,
    which restarts the server). `doctor` bypasses the memo (fresh=True) to report current health.
    There is deliberately NO on-disk cache: a persisted verdict turned a transient probe failure
    (a registry blip) into a permanent refusal keyed on a fingerprint that never cleared.

  * ARTIFACT compatibility — was THIS mission's fused image built on a base generation this
    XORCISE can run? A cheap label/tag check (require_base_compatible), no container.

It lives in `rest` because `runner` is a part-island that must not import `home`, and `config` is
shared-kernel that must not import upward — `rest` sits above both.

The Docker client arrives as a FACTORY, not a client, so a check that is configured off never
opens a connection, and the memo builds exactly one client per (re)probe.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping

from xorcise.core.config import Settings
from xorcise.core.contracts.errors import (
    BaseImageIncompatibleError,
    NestedContainersUnavailableError,
)
from xorcise.core.runner.docker.build import (
    REQUIRED_BASE_MAJOR,
    base_compat,
    base_major_from_labels,
    base_major_from_ref,
)
from xorcise.core.runner.docker.rosetta import (
    NestedSupport,
    binfmt_signal,
    check_nested_support,
    verify_nested_amd64,
)

log = logging.getLogger(__name__)

# In-memory verdict, memoised for the process lifetime. The lock serialises the (slow) probe so
# concurrent first run-creates queue behind ONE probe instead of each launching a privileged DinD
# and racing a cache file — the failure the removed on-disk cache used to have.
_MEMO_LOCK = threading.Lock()
_memo: NestedSupport | None = None
#: The last verdict line logged, so a long-lived server logs only when the verdict CHANGES
#: (a single value, not an unbounded set of every distinct failure string ever seen).
_last_logged: str | None = None


def _log_support(support: NestedSupport) -> None:
    global _last_logged
    line = f"{'ok' if support.ok else 'unavailable'}: {support.detail}"
    if line == _last_logged:
        return
    _last_logged = line
    (log.info if support.ok else log.warning)("nested containers — %s", line)


def _probe(client_factory: Callable[[], object]) -> NestedSupport:
    """Run both tiers against one client, closing it after. Never memoises — caller decides."""
    client = client_factory()  # may raise (daemon down); the caller wraps it into a typed error
    try:
        return check_nested_support(
            skip=False,
            probe_tier1=lambda: binfmt_signal(client),
            probe_tier2=lambda _t1: verify_nested_amd64(client),
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()  # release the connection pool — one client per probe, not per run


def nested_support(
    settings: Settings,
    client_factory: Callable[[], object],
    *,
    fresh: bool = False,
) -> NestedSupport:
    """Can this host nest containers? Memoised in memory; `fresh` re-probes (doctor).

    A skipped check answers immediately without touching Docker. Otherwise the verdict is computed
    once and held; `fresh=True` recomputes AND refreshes the memo, so an operator who fixes Rosetta
    and re-runs `doctor` unblocks subsequent runs without restarting the server.
    """
    if settings.nested_container_check == "skip":
        return NestedSupport(True, "nested-container check skipped by configuration")
    global _memo
    with _MEMO_LOCK:
        if _memo is not None and not fresh:
            return _memo
        support = _probe(client_factory)
        _memo = support
        _log_support(support)
        return support


def prewarm_nested_support(settings: Settings, client_factory: Callable[[], object]) -> None:
    """Compute the verdict ahead of the first run-create (called at boot, best-effort).

    Warming the memo at startup means run-create reads it instantly instead of the first real run
    paying the 20-40 s probe under a client timeout. Never raises: a warm-up failure just leaves
    the memo cold, and the first run recomputes it."""
    try:
        nested_support(settings, client_factory)
    except Exception as exc:  # noqa: BLE001 — a safety-net warm-up must never break boot
        log.debug("nested-support pre-warm skipped: %s", exc)


def require_nested_support(
    settings: Settings,
    client_factory: Callable[[], object],
) -> None:
    """Raise NestedContainersUnavailableError unless this host can nest containers.

    Call this BEFORE anything stateful or expensive (subnet reservation, control-plane fence,
    image pull) so a host that cannot run missions costs an error message rather than a
    half-built run that has to be torn down.
    """
    support = nested_support(settings, client_factory)
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


def require_base_compatible(
    image_ref: str,
    *,
    label_lookup: Callable[[str], Mapping[str, str] | None] | None = None,
    origin: str | None = None,
) -> None:
    """Raise BaseImageIncompatibleError if a fused image's base MAJOR is not what this XORCISE runs.

    Resolution ladder: the inherited `ai.xorcise.base.version` label (via `label_lookup`, when a
    Docker inspect is available — the only signal a local `:local` fuse carries), else the `-baseN`
    tag suffix (every published/pulled artifact), else undetermined → allow with a warning (a
    pre-versioning local fuse, where "re-pull" is not even the right advice).
    """
    major = base_major_from_labels(label_lookup(image_ref)) if label_lookup else None
    if major is None:
        major = base_major_from_ref(image_ref)
    # ONE verdict shared with the catalog browse surface (base_compat), so a card never says
    # "runnable" for something a run would refuse. The remediation here is command-level; the
    # frontend renders the shorter compat.hint.
    compat = base_compat(major)
    if compat.compatible is None:
        # CG4/LEG3: every published artifact carries the base label AND the -baseN tag suffix,
        # so a LIBRARY install with neither predates the versioned image format — refuse with
        # the one update action, rather than parsing legacy shapes forever. A your_own local
        # fuse keeps the allowance: "update from the catalog" is not even the right advice.
        if origin == "library":
            raise BaseImageIncompatibleError(
                f"mission image {image_ref!r} was installed using an older XORCISE image "
                "format (it carries no base-generation metadata) — update it: "
                "xorcise mission update <mission>"
            )
        log.warning("could not determine the base generation of %s — allowing the run", image_ref)
        return
    if compat.compatible:
        return
    if compat.base_major is not None and compat.base_major < REQUIRED_BASE_MAJOR:
        fix = (
            "this mission was built on an older base — update it: xorcise mission update <mission>"
        )
    else:
        fix = "this mission needs a newer XORCISE — upgrade it (e.g. pip install -U xorcise)"
    raise BaseImageIncompatibleError(
        f"mission image {image_ref!r} was built on base generation {compat.base_major}, but this "
        f"XORCISE runs base {REQUIRED_BASE_MAJOR}. {fix}"
    )
