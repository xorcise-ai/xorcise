"""Composition point for the nested-container precondition and base-generation gate.

The mission stack always runs INSIDE the run's own container. The former host-daemon "sibling"
topology is gone (see runner/docker/rosetta.py for why), so "can this host nest containers?" is
no longer a choice between two runtimes — it is a yes/no precondition for deploying a lab
mission, and a "no" must fail the run.

Two separate questions live here:

  * HOST capability — can this machine run a container inside a container AT A GIVEN PLATFORM?
    A behavioural probe (runner/docker/rosetta.py), asked per execution platform: the native one
    for a mission that pulled a native image, `linux/amd64` for one running under emulation on an
    arm64 host. Expensive (~10-40 s for Tier 2), so each verdict is memoised IN MEMORY per
    platform for the server's lifetime (host capability does not change without a Docker
    restart, which restarts the server). `doctor` bypasses the memo (fresh=True) to report
    current health. There is deliberately NO on-disk cache: a persisted verdict turned a
    transient probe failure (a registry blip) into a permanent refusal keyed on a fingerprint
    that never cleared.

  * ARTIFACT compatibility — was THIS mission's fused image built on a base generation this
    XORCISE can run? A cheap label/tag check (require_base_compatible), no container.

It lives in `rest` because `runner` is a part-island that must not import `home`, and `config` is
shared-kernel that must not import upward — `rest` sits above both.

The Docker client arrives as a FACTORY, not a client, so a check that is configured off never
opens a connection, and the memo builds exactly one client per (re)probe.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

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
    canonical_platform,
    check_nested_support,
    verify_nested,
)

log = logging.getLogger(__name__)

# In-memory verdicts, one per execution platform (None = the daemon's native platform), memoised
# for the process lifetime. The lock serialises the (slow) probe so concurrent first run-creates
# queue behind ONE probe instead of each launching a privileged DinD and racing a cache file —
# the failure the removed on-disk cache used to have.
_MEMO_LOCK = threading.Lock()
_memo: dict[str | None, NestedSupport] = {}
#: The last verdict line logged, so a long-lived server logs only when the verdict CHANGES
#: (a single value, not an unbounded set of every distinct failure string ever seen).
_last_logged: str | None = None


def _log_support(support: NestedSupport) -> None:
    global _last_logged
    line = (
        f"{'ok' if support.ok else 'unavailable'} [{support.platform or 'native'}]: "
        f"{support.detail}"
    )
    if line == _last_logged:
        return
    _last_logged = line
    (log.info if support.ok else log.warning)("nested containers — %s", line)


def _daemon_platform(client: object) -> str | None:
    """What the daemon executes natively, from its own version report — or None if unknowable."""
    try:
        version = cast(Any, client).version() or {}
        os_name, arch = version.get("Os"), version.get("Arch")
    except Exception:  # noqa: BLE001 — unknown, never a crash; only sharpens the memo + message
        return None
    return canonical_platform(f"{os_name}/{arch}") if os_name and arch else None


def _probe(
    client_factory: Callable[[], object], platform: str | None
) -> tuple[NestedSupport, str | None]:
    """Run both tiers for `platform` against one client, closing it after. Never memoises — the
    caller decides. Also returns the daemon's native platform, so a native verdict can be filed
    under its explicit spelling too."""
    client = client_factory()  # may raise (daemon down); the caller wraps it into a typed error
    try:
        native = _daemon_platform(client)
        # Probe with an EXPLICIT platform whenever one is known: "native" spelt out lets the
        # wrapper insist on the right image when the local tag holds another platform's copy
        # (docker keeps one per tag under overlay2), instead of silently running whatever is there.
        support = check_nested_support(
            skip=False,
            probe_tier1=lambda: binfmt_signal(client),
            probe_tier2=lambda _t1: verify_nested(client, platform=platform or native),
            platform=platform,
            host_platform=native,
        )
        return support, native
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()  # release the connection pool — one client per probe, not per run


def nested_support(
    settings: Settings,
    client_factory: Callable[[], object],
    *,
    platform: str | None = None,
    fresh: bool = False,
) -> NestedSupport:
    """Can this host nest containers at `platform`? Memoised in memory; `fresh` re-probes (doctor).

    `platform` is the execution platform of the mission about to run — the one its install
    recorded — or None for the daemon's native platform (what boot pre-warms and `doctor`
    reports). The two spellings of "native" share one verdict: a probe made with None is also
    filed under the platform the daemon reported, so the first run of a native mission reads the
    pre-warmed answer instead of paying a second probe.

    A skipped check answers immediately without touching Docker. Otherwise the verdict is computed
    once per platform and held; `fresh=True` recomputes AND refreshes the memo, so an operator who
    fixes Rosetta and re-runs `doctor` unblocks subsequent runs without restarting the server.
    """
    key = canonical_platform(platform)
    if settings.nested_container_check == "skip":
        return NestedSupport(
            True, "nested-container check skipped by configuration", platform=key or ""
        )
    with _MEMO_LOCK:
        hit = _memo.get(key)
        if hit is not None and not fresh:
            return hit
        support, native = _probe(client_factory, key)
        _memo[key] = support
        if key is None and native is not None:
            _memo[native] = support  # "native" and its explicit spelling are the same question
        elif key is not None and key == native:
            _memo[None] = support
        _log_support(support)
        return support


def prewarm_nested_support(settings: Settings, client_factory: Callable[[], object]) -> None:
    """Compute the NATIVE verdict ahead of the first run-create (called at boot, best-effort).

    Warming the memo at startup means run-create reads it instantly instead of the first real run
    paying the 10-40 s probe under a client timeout. Only the native platform is warmed: it is
    what every mission with a native image runs at, and probing a foreign platform at every boot
    would start a privileged emulated DinD for a case most operators never hit. Never raises: a
    warm-up failure just leaves the memo cold, and the first run recomputes it."""
    try:
        nested_support(settings, client_factory)
    except Exception as exc:  # noqa: BLE001 — a safety-net warm-up must never break boot
        log.debug("nested-support pre-warm skipped: %s", exc)


def require_nested_support(
    settings: Settings,
    client_factory: Callable[[], object],
    *,
    platform: str | None = None,
) -> None:
    """Raise NestedContainersUnavailableError unless this host can nest containers at `platform`.

    Call this BEFORE anything stateful or expensive (subnet reservation, control-plane fence,
    image pull) so a host that cannot run missions costs an error message rather than a
    half-built run that has to be torn down.
    """
    support = nested_support(settings, client_factory, platform=platform)
    if support.ok:
        return
    # The probe's detail often already ends in a period (it quotes a runtime error verbatim), so
    # only add one where it is missing — a stray ".." reads like a typo in the one message an
    # operator sees when nothing works.
    detail = support.detail.rstrip(". ")
    where = f" ({support.platform})" if support.platform else ""
    raise NestedContainersUnavailableError(
        f"this host cannot run a mission's containers inside the run container{where} — "
        f"{detail}. {support.remediation}. "
        # Both spellings of the escape hatch, and the restart: the server reads its settings
        # once at boot, so editing config.toml (or exporting the variable) while it runs changes
        # nothing until `xorcise down && xorcise up`.
        "To bypass this check on a host you know is fine, set "
        "XORCISE_NESTED_CONTAINER_CHECK=skip in the server's environment or "
        'nested_container_check = "skip" in ~/.xorcise/config.toml, then restart it '
        "(xorcise down && xorcise up)"
    )


def reset_nested_support_memo() -> None:
    """Drop every memoised nesting verdict (tests)."""
    global _last_logged
    with _MEMO_LOCK:
        _memo.clear()
    _last_logged = None


# Memoised daemon platform: the arch of a running daemon cannot change, but "docker was down,
# try again" can — so a short TTL rather than a process-lifetime latch. Shared by the catalog
# view (per-row `emulated`) and the system view (`host_platform`), both of which poll.
_HOST_PLATFORM_TTL = 60.0
_host_platform_memo: tuple[float, str | None] | None = None


def host_platform(settings: Settings) -> str | None:
    """The `os/arch` the local daemon executes natively (AS1), or None when unknowable.

    None in stub mode (no daemon by design), when docker is absent/unreachable, and on any
    probe failure — unknown, never guessed. A subprocess probe (not the SDK) so a browse call
    never pays a docker client construction; memoised because every page polls the views that
    read this."""
    global _host_platform_memo
    if settings.use_stubs:
        return None
    now = time.monotonic()
    if _host_platform_memo is not None and now - _host_platform_memo[0] < _HOST_PLATFORM_TTL:
        return _host_platform_memo[1]
    value: str | None = None
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"],
            capture_output=True,
            timeout=5,
            text=True,
        )
        raw = result.stdout.strip()
        if result.returncode == 0 and _both_halves(raw):
            value = raw
    except (OSError, subprocess.SubprocessError):
        value = None
    _host_platform_memo = (now, value)
    return value


def _both_halves(raw: str) -> bool:
    """Whether `raw` is a usable `os/arch`, i.e. BOTH halves are present.

    `"/" in raw` is not enough. Under the containerd snapshotter, `docker image inspect
    --format '{{.Os}}/{{.Architecture}}'` exits 0 and prints a bare "/" for a foreign-arch local
    image — both fields empty. That passed the old guard, so an unknown platform was recorded as
    the literal "/" and surfaced to the operator as an architecture: the run form warned "This
    install is /, not native ARM64", and the detail page painted a "/" tag. Unknown must stay
    None, which the callers already render as "no claim".
    """
    os_, _, arch = raw.partition("/")
    return bool(os_ and arch)


def reset_host_platform_memo() -> None:
    """Drop the platform memos (tests)."""
    global _host_platform_memo
    _host_platform_memo = None
    _image_platform_memo.clear()


_IMAGE_PLATFORM_TTL = 60.0
_image_platform_memo: dict[str, tuple[float, str | None]] = {}


def local_image_platform(settings: Settings, image: str) -> str | None:
    """The `os/arch` of a LOCAL image (docker image inspect), or None when unknowable.

    The §30 install record normally carries the platform a pull selected — but installs that
    predate the record (and your_own fuses) have nothing recorded, and their runs still deserve
    the emulation warning. The local image itself is the honest fallback: it IS what a run of
    this install executes. Memoised per ref (browse polls; an image's arch only changes when
    its tag is re-pointed by an update/re-fuse, which the short TTL absorbs); None in stub mode
    and on any probe failure — unknown, never guessed."""
    if settings.use_stubs or not image:
        return None
    now = time.monotonic()
    hit = _image_platform_memo.get(image)
    if hit is not None and now - hit[0] < _IMAGE_PLATFORM_TTL:
        return hit[1]
    value: str | None = None
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image],
            capture_output=True,
            timeout=5,
            text=True,
        )
        raw = result.stdout.strip()
        if result.returncode == 0 and _both_halves(raw):
            value = raw
    except (OSError, subprocess.SubprocessError):
        value = None
    _image_platform_memo[image] = (now, value)
    return value


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
