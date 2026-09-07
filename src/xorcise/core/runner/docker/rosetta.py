"""Nested-container capability probe (runner part-island).

WHAT THIS GATES. The mission stack always runs INSIDE the fused container (DinD). The former
host-daemon "sibling" topology — which composed the mission stack on the OPERATOR's own daemon —
has been REMOVED, because everything it touched was shared global state: parallel runs collided
on missions' fixed `container_name`s and published host ports, teardown leaked un-labelled
containers onto the operator's daemon, and mission bind mounts resolved against the wrong
filesystem. Every one of those is invisible with one run and fatal with several.

Removing the fallback makes nesting a PRECONDITION rather than a preference, which is why this
module now answers one question — can this host run a container inside a container AT THE
PLATFORM OF THE MISSION ABOUT TO RUN? — and why a "no" has to fail a run loudly instead of
silently degrading it. The platform matters: an arm64 Linux host nests arm64 natively and can
run the 19-of-27 catalog missions that publish arm64 images, yet has no way to nest amd64 at
all (no binfmt handler ⇒ `exec format error`; a qemu handler ⇒ the inner dockerd cannot init
nftables). A probe hard-pinned to amd64 therefore refused that host EVERY mission, including
the ones it could run, and blamed privileged containers. The probe is per platform now.

WHY IT CAN FAIL AT ALL. Historically the runner mounted Docker Desktop's socket on macOS, on the
premise that "Rosetta fails for nested DinD children". The OBSERVATION was real; the diagnosis
was wrong, and nesting was never the cause. Root-caused empirically:

  * Rosetta cannot exec a binary reached through the `/proc/<pid>/exe` magic symlink. Minimal
    repro, in a PLAIN amd64 container with no DinD anywhere:
        /bin/busybox true    -> ok
        /proc/<pid>/exe true -> "rosetta error: failed to open elf at true"
    It reports argv[1] as the ELF it tried to open, i.e. the path never resolved and its
    argument parsing shifted by one.
  * Docker <= 27 installs an OCI **prestart hook** on every container it creates:
        {"path": "/proc/<dockerd-pid>/exe",
         "args": ["libnetwork-setkey", "-exec-root=/var/run/docker", ...]}
    which is exactly that unsupported exec — hence the observed
    "failed to open elf at -exec-root=/var/run/docker".
  * Docker 28 removed the hook. Verified across engines: 27.5.1 hook present -> nested amd64
    fails; 28.5.2 and 29.7.1 no hook -> nested amd64 works.

So sibling mode never "avoided nesting" — it moved container creation off the inner Docker 27
daemon (which has the hook) onto Docker Desktop's daemon (which does not). Nesting itself is
fine: the VM registers Rosetta in binfmt_misc with the `F` (fix binary) flag, so the kernel opens
the interpreter at REGISTRATION time and holds the fd — the interpreter needs no presence in any
container's mount namespace, and every nested container inherits the registration because
binfmt_misc is per-kernel.

The base image is now pinned >= 28, so that specific cause is fixed. The probe remains because
the OTHER gates are host properties no base image can fix: Apple Silicon, macOS >= 13, the VMM
being Apple's Virtualization framework (Docker VMM has no Rosetta at all), and the Rosetta toggle
being on. Those — and regressions like docker/for-mac#7322 — all present identically as "amd64
won't run" and are invisible in any version string. Rosetta is also slated for removal in
macOS 28, with the Linux-VM path's fate publicly unresolved. A behavioural probe is correct
across all of that; a version check is not.

Everything here fails CLOSED: any error, timeout or ambiguity yields ok=False. With the sibling
fallback gone, failing closed means failing the RUN — which is the point. A run that cannot
isolate its containers is worse than no run, because it lands them on the operator's daemon
where the next parallel run collides with them.

LAYER: part-island (`runner`). stdlib only at module scope; `docker` is never imported here — the
caller injects a client. Persistence of the Tier 2 verdict is the CALLER's job (`rest`), because
`runner` must not import `home`.
"""

from __future__ import annotations

import contextlib
import hashlib
import platform as _platform
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Host-arch image used to read the VM's binfmt registration. Tiny (~4 MB) and pulled on demand.
BINFMT_PROBE_IMAGE = "alpine:3.20"
# DinD image used for the ground-truth nested check, run at the PLATFORM OF THE MISSION under
# test (host-native when none is given). This MUST track the `FROM` in
# containers/mission-base/Dockerfile (asserted by tests/topology/test_dind_base_parity.py); the
# tag is a multi-platform index, so the same constant serves every platform the base publishes.
#
# The capability is NOT a property of the host alone — it is a property of (host, platform).
# The historical amd64-on-Apple-Silicon failure needed BOTH: an amd64 wrapper (so its dockerd
# runs under Rosetta) AND an engine <= 27 (so it installs the `/proc/<pid>/exe` prestart hook
# Rosetta cannot exec — see the module docstring). Break either and nesting works; verified on
# an unchanged host:
#     amd64 wrapper + engine 27  -> fails
#     amd64 wrapper + engine 28  -> works   (hook removed upstream, moby#47406)
#     arm64 wrapper + engine 27  -> works   (dockerd is native; Rosetta never sees the hook)
# The base is now pinned >= 28, so this probe is expected to PASS on a Rosetta-capable host.
# It is kept rather than deleted because the other three gates (Apple Silicon, the Apple
# Virtualization VMM, the Rosetta toggle) are still host properties the base cannot fix, and
# because a Linux host has its own gate: a FOREIGN platform needs a binfmt emulator (qemu-user),
# and qemu cannot service the nf_tables netlink calls the inner dockerd needs, so amd64 DinD
# under emulation on arm64 Linux fails at `iptables: Failed to initialize nft` — invisible in any
# version string, visible only by trying. Base-generation compatibility of a specific fused
# artifact is a separate, cheaper check (build.base_major_from_ref/_labels).
NESTED_PROBE_IMAGE = "docker:29.7.1-dind"
# Ceiling for the Tier 2 container: inner dockerd boot + an inner child pull + exec. Under
# emulation every step is several times slower, so the inner daemon wait below is a WALL-CLOCK
# budget (not an iteration count) that stays under this ceiling — the container itself reports
# "daemon never came up" with its log tail, instead of the SDK timing out first and discarding
# everything the container had to say.
NESTED_PROBE_TIMEOUT = 180
_INNER_DAEMON_BUDGET = 90

# Reaper label (mirrors runner.docker.MANAGED_LABEL) stamped on the Tier 2 probe container so a
# crash mid-probe cannot strand a privileged DinD that `xorcise down` can never find.
_PROBE_LABEL = "xorcise.managed"

_SENTINEL = "XORCISE_NESTED_ARCH="
_ERR_SENTINEL = "XORCISE_NESTED_ERR="
_OUTER_SENTINEL = "XORCISE_OUTER_ARCH="
_DAEMON_LOG_SENTINEL = "XORCISE_DAEMON_LOG="
_NO_HANDLER = "XORCISE_NO_HANDLER"

# `uname -m` inside a container of the given platform. Only the two the base publishes; anything
# else is verified by agreement with the wrapper's own arch (the child must match its parent).
_MACHINE_FOR_PLATFORM = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}
_PLATFORM_FOR_MACHINE = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}
# Aliases docker and catalogs use for the same thing.
_PLATFORM_ALIASES = {
    "linux/x86_64": "linux/amd64",
    "linux/amd64/v2": "linux/amd64",
    "linux/amd64/v3": "linux/amd64",
    "linux/aarch64": "linux/arm64",
    "linux/arm64/v8": "linux/arm64",
}


def canonical_platform(platform: str | None) -> str | None:
    """`os/arch` in the one spelling the rest of this module compares against, or None."""
    if not platform:
        return None
    text = platform.strip().lower()
    return _PLATFORM_ALIASES.get(text, text)


_BINFMT_SCRIPT = (
    # binfmt_misc is usually unmounted inside a container; mounting it is why this needs
    # --privileged. Never fail the script — an absent handler must read as "not available",
    # not as a container error we cannot tell apart from a broken daemon.
    "mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc 2>/dev/null || true; "
    f"cat /proc/sys/fs/binfmt_misc/rosetta 2>/dev/null || echo {_NO_HANDLER}"
)


def _nested_script(platform: str | None) -> str:
    """The Tier 2 script for one platform: bring up an inner daemon, run a child of the SAME
    platform inside it, report both arches. `platform=None` ⇒ no `--platform` anywhere: the
    wrapper and the child are whatever the daemon runs natively."""
    child_platform = f"--platform {platform} " if platform else ""
    return (
        # The docker:dind image ships DOCKER_HOST=tcp://docker:2375 (plus TLS vars) for the
        # docker-compose "dind sidecar" pattern. Left set, the inner CLI dials a host named
        # `docker` that does not exist here and never reaches the daemon it just started — the
        # probe then times out and reports "not available" on a host where nesting works
        # perfectly. Clear them so the CLI falls back to the local unix socket.
        "unset DOCKER_HOST DOCKER_TLS_VERIFY DOCKER_CERT_PATH; "
        # The wrapper's own arch: what a `platform=None` child must agree with, and proof (when
        # present at all) that the wrapper could exec — a foreign-arch wrapper with no binfmt
        # handler dies before this line with "exec format error".
        f'echo "{_OUTER_SENTINEL}$(uname -m)"; '
        "dockerd-entrypoint.sh dockerd >/tmp/dockerd.log 2>&1 & "
        f"deadline=$(( $(date +%s) + {_INNER_DAEMON_BUDGET} )); "
        "while ! docker info >/dev/null 2>&1; do "
        "  if [ $(date +%s) -gt $deadline ]; then "
        # The daemon's own log is the ONLY place the cause lives (e.g. `iptables: Failed to
        # initialize nft: Protocol not supported` under qemu) — hand its tail out before dying.
        f"    echo \"{_DAEMON_LOG_SENTINEL}$(tr '\\n' ' ' </tmp/dockerd.log | tail -c 400)\"; "
        "    exit 1; "
        "  fi; sleep 1; "
        "done; "
        # Emit BOTH lines unconditionally. The two ways this fails need different fixes and look
        # identical from an empty arch alone: the inner daemon never starting, versus the daemon
        # being fine while the child dies in the runtime (e.g. "rosetta error: failed to open
        # elf"). Reaching the sentinel at all proves the daemon came up, so the presence of these
        # lines is what separates the two — and the captured stderr names the real cause.
        # -q suppresses the inner pull's progress, which is written to stderr and would
        # otherwise bury the actual error; the tail (not head) is taken for the same reason —
        # the runtime's failure is the LAST thing on stderr.
        f"arch=$(docker run --rm -q {child_platform}{BINFMT_PROBE_IMAGE} uname -m 2>/tmp/err); "
        f'echo "{_SENTINEL}$arch"; '
        f"echo \"{_ERR_SENTINEL}$(tr '\\n' ' ' </tmp/err | tail -c 300)\""
    )


@dataclass(frozen=True)
class RosettaProbe:
    """Verdict of one probe tier. `fingerprint` identifies the host state the verdict is about,
    so a cached Tier 2 result can be invalidated when anything underneath it moves."""

    ok: bool
    detail: str
    fingerprint: str = ""


@dataclass(frozen=True)
class NestedSupport:
    """Whether this host can run the mission stack inside the fused container.

    There is no longer a fallback: the host-daemon sibling topology was removed because it
    composed the mission stack on the OPERATOR's daemon, where parallel runs collide on fixed
    `container_name`s and published host ports, teardown leaks un-labelled containers, and
    mission bind mounts resolve against the wrong filesystem. So this is a PRECONDITION, and a
    negative verdict must fail a run loudly rather than silently degrade it.

    `detail` is written for the operator — it is what a failed run prints. `platform` is the
    execution platform the verdict is ABOUT ("" = the daemon's native one): nesting is a property
    of the (host, platform) pair, not of the host alone — an arm64 host nests arm64 natively and
    may or may not be able to nest amd64 under emulation.
    """

    ok: bool
    detail: str
    fingerprint: str = ""
    remediation: str = ""
    platform: str = ""


def host_is_macos() -> bool:
    """Whether this process runs on a macOS host (test seam)."""
    return _platform.system() == "Darwin"


# ---------------------------------------------------------------- tier 1: binfmt signal (pure)


def parse_binfmt(raw: str) -> RosettaProbe:
    """Parse `/proc/sys/fs/binfmt_misc/rosetta` into a verdict. Pure — the whole point of the
    tier is this parse, so it is unit-testable without a daemon.

    Requires all four:
      * a handler exists at all,
      * it is `enabled` (Docker Desktop leaves a disabled registration behind when the toggle
        is turned off, so presence alone proves nothing),
      * `flags:` contains `F` — the fix-binary flag is THE property that makes nesting work,
        so it is asserted explicitly rather than inferred,
      * the magic identifies ELF64 x86-64 (`e_machine == 0x3e` at byte 18), so we do not accept
        some other architecture's handler that happens to be named rosetta.
    """
    text = (raw or "").strip()
    if not text or _NO_HANDLER in text:
        return RosettaProbe(False, "no rosetta binfmt handler in the Docker VM")

    fields: dict[str, str] = {}
    enabled = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "enabled":
            enabled = True
            continue
        if line == "disabled":
            enabled = False
            continue
        key, _, value = line.partition(" ")
        fields[key.rstrip(":").lower()] = value.strip()

    if not enabled:
        return RosettaProbe(False, "rosetta binfmt handler is registered but disabled")

    flags = fields.get("flags", "")
    if "F" not in flags:
        return RosettaProbe(
            False,
            f"rosetta binfmt handler lacks the F (fix binary) flag (flags: {flags or 'none'}) — "
            "nested containers cannot reach the interpreter without it",
        )

    magic = fields.get("magic", "").lower()
    offset = fields.get("offset", "0")
    # e_machine sits at byte 18 of an ELF header => hex chars 36..40 of an offset-0 magic.
    machine = magic[36:40] if offset == "0" and len(magic) >= 40 else ""
    if machine != "3e00" and "003e00" not in magic:
        return RosettaProbe(
            False, f"rosetta binfmt magic is not ELF64 x86-64 (offset {offset}, magic {magic!r})"
        )

    return RosettaProbe(True, f"rosetta binfmt handler present and enabled (flags: {flags})")


def fingerprint(*, docker_version: str, macos_version: str, binfmt_raw: str) -> str:
    """Cache key for a Tier 2 verdict. Anything that could change the answer without changing
    the question goes in here: a Docker Desktop upgrade, a macOS upgrade, or a change to the
    Rosetta registration itself (toggling it off/on, or a VMM switch, both of which rewrite the
    handler). Only the flags+magic of the registration are used — the interpreter path and the
    enabled/disabled line are already decided by Tier 1, which runs every time."""
    probe = parse_binfmt(binfmt_raw)
    payload = "|".join((docker_version, macos_version, probe.detail))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def binfmt_signal(client: Any, *, image: str = BINFMT_PROBE_IMAGE) -> RosettaProbe:
    """Tier 1 (~1-2 s). Read the VM's binfmt registration and stamp a fingerprint on the verdict.

    Cheap enough to run on every deploy, which is what catches a mid-session Rosetta toggle or
    VMM switch without paying for Tier 2."""
    try:
        raw = client.containers.run(
            image,
            command=["sh", "-c", _BINFMT_SCRIPT],
            privileged=True,  # mounting binfmt_misc needs it
            remove=True,
            # deliberately host-arch: reading the registration must not itself depend on Rosetta
            platform=None,
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on ANY probe failure
        return RosettaProbe(False, f"binfmt probe failed: {type(exc).__name__}: {exc}")

    text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
    probe = parse_binfmt(text)
    try:
        docker_version = str((client.version() or {}).get("Version", ""))
    except Exception:  # noqa: BLE001 — a missing version only weakens the cache key
        docker_version = ""
    return RosettaProbe(
        probe.ok,
        probe.detail,
        fingerprint(
            docker_version=docker_version,
            macos_version=_platform.mac_ver()[0],
            binfmt_raw=text,
        ),
    )


# ------------------------------------------------------------- tier 2: nested ground truth


def _decode(raw: Any) -> str:
    return raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw or "")


def _output_tail(text: str, limit: int = 300) -> str:
    """The last `limit` chars of a container's non-sentinel output, whitespace-collapsed. This
    is where a wrapper that never ran its script says why (`exec format error`)."""
    sentinels = (_SENTINEL, _ERR_SENTINEL, _OUTER_SENTINEL, _DAEMON_LOG_SENTINEL)
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith(sentinels)
    ]
    joined = " ".join(" ".join(lines).split())
    return joined if len(joined) <= limit else "…" + joined[-(limit - 1) :]


_PLATFORM_MISMATCH = "does not match the specified platform"


def _start_wrapper(client: Any, image: str, wanted: str | None) -> Any:
    """Start the detached DinD wrapper at `wanted`, pulling the right platform's image if the
    local tag holds another's. Docker keeps ONE image per tag under overlay2, and docker-py's
    `run` re-pulls only on a missing image — a present-but-wrong-platform tag surfaces as a 404
    ("… was found but its platform (linux/amd64) does not match the specified platform
    (linux/arm64)") that would otherwise read as "this host cannot nest"."""
    kwargs: dict[str, Any] = {
        "command": ["sh", "-c", _nested_script(wanted)],
        "privileged": True,
        "detach": True,
        # The outer layer runs at the platform of the mission it stands in for. None ⇒ the
        # daemon's native platform (docker's default when no platform is requested).
        "platform": wanted,
        # Labelled so a crash before the finally-remove leaves a container `xorcise down` can
        # still reap — a stranded PRIVILEGED DinD is the worst thing to leak. Deliberately
        # UNNAMED: a fixed name would 409 the next probe if a crashed one lingers; the reaper
        # filters on the label, not the name.
        "labels": {_PROBE_LABEL: "true"},
    }
    try:
        return client.containers.run(image, **kwargs)
    except Exception as exc:
        if not wanted or _PLATFORM_MISMATCH not in str(exc):
            raise
        client.images.pull(image, platform=wanted)  # docker-py splits repo:tag itself
        return client.containers.run(image, **kwargs)


def verify_nested(
    client: Any,
    *,
    platform: str | None = None,
    image: str = NESTED_PROBE_IMAGE,
    timeout: int = NESTED_PROBE_TIMEOUT,
) -> RosettaProbe:
    """Tier 2 (~10-40 s natively). The only check that proves the WHOLE chain for ONE platform:
    start a throwaway DinD wrapper at `platform`, wait for its inner daemon, run a child of the
    same platform inside it and assert the child reports the machine that platform implies.

    `platform=None` probes the daemon's NATIVE platform — no `--platform` anywhere, the child
    must agree with the wrapper's own `uname -m`. A mission that pulled a native image is gated
    on exactly this; a mission that pulled a foreign one (amd64 under emulation on an arm64 host)
    is gated on `platform="linux/amd64"`, which is the only way to learn whether THAT works here.
    Before this took a platform, the wrapper was hard-pinned to amd64 — so an arm64 Linux host
    with no x86 emulation was refused every mission, including the native-arm64 ones it could
    run perfectly, and told to check privileged containers.

    Slow by nature, so the caller caches the verdict per platform. The container is always
    removed (with its anonymous /var/lib/docker volume), including on timeout — a stranded
    privileged DinD would be worse than a wrong answer. On timeout the container's output is
    still read first: it is the diagnosis."""
    wanted = canonical_platform(platform)
    label = wanted or "host-native"
    expected = _MACHINE_FOR_PLATFORM.get(wanted or "", "")
    container = None
    text = ""
    result: Any = None
    failure: str | None = None
    try:
        container = _start_wrapper(client, image, wanted)
        try:
            result = container.wait(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — the SDK read timed out (or the daemon blinked)
            failure = f"{type(exc).__name__}: {exc}"
        # Read the output EVEN after a timeout: the container is still there, and what it printed
        # so far (the daemon log tail, an exec error) is the only diagnosis there will be.
        with contextlib.suppress(Exception):
            text = _decode(container.logs())
    except Exception as exc:  # noqa: BLE001 — fail closed
        return RosettaProbe(False, f"nested {label} probe failed: {type(exc).__name__}: {exc}")
    finally:
        if container is not None:
            # best effort; already-gone is the common case. v=True: the dind image declares
            # VOLUME /var/lib/docker, so a plain remove leaks one anonymous volume per probe.
            with contextlib.suppress(Exception):
                container.remove(force=True, v=True)

    arch = ""
    err = ""
    outer = ""
    daemon_log = ""
    reached_child = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(_SENTINEL):
            reached_child = True
            arch = line[len(_SENTINEL) :].strip()
        elif line.startswith(_ERR_SENTINEL):
            err = line[len(_ERR_SENTINEL) :].strip()
        elif line.startswith(_OUTER_SENTINEL):
            outer = line[len(_OUTER_SENTINEL) :].strip()
        elif line.startswith(_DAEMON_LOG_SENTINEL):
            daemon_log = line[len(_DAEMON_LOG_SENTINEL) :].strip()

    if not expected:
        # No platform requested (or one this table does not know): the child must match the
        # wrapper it runs inside — the wrapper's own arch is the ground truth for "native".
        expected = outer
    if arch and arch == expected:
        shown = wanted or _PLATFORM_FOR_MACHINE.get(arch, label)
        return RosettaProbe(True, f"nested {shown} verified ({arch} inside a {shown} DinD)")

    if failure is not None:
        tail = daemon_log or _output_tail(text)
        why = f"; last output: {tail}" if tail else ""
        return RosettaProbe(
            False, f"the {label} DinD probe did not finish within {timeout}s ({failure}){why}"
        )
    status = (result or {}).get("StatusCode") if isinstance(result, dict) else result
    if not reached_child:
        # Two very different causes share this branch, and only the output tells them apart: a
        # wrapper that could not exec at all (`exec format error` — a foreign platform with no
        # binfmt handler), versus one whose inner dockerd started and died (its log tail).
        tail = daemon_log or _output_tail(text)
        why = f": {tail}" if tail else ""
        return RosettaProbe(
            False, f"the {label} DinD probe's inner daemon never came up (exit {status}){why}"
        )
    if not arch:
        # The daemon was healthy and the child still failed — the interesting case, and the one
        # whose cause is only in the runtime's stderr.
        return RosettaProbe(
            False, f"nested {label} container failed to start: {err or 'no error output'}"
        )
    return RosettaProbe(
        False, f"nested child reported {arch!r}, expected {expected or 'the wrapper arch'!r}"
    )


# --------------------------------------------------------------- precondition (pure)

# Remediation is split from the verdict so `doctor` and a failed run print the SAME fix, and so
# the wording lives next to the check that produces it rather than at three call sites.
_FIX_ROSETTA = (
    "enable Rosetta for x86/amd64 emulation in Docker Desktop "
    "(Settings → General → 'Use Rosetta for x86_64/amd64 emulation on Apple Silicon'), and make "
    "sure the VM is the Apple Virtualization framework — Docker VMM does not support Rosetta. "
    "Then restart Docker Desktop"
)
_FIX_GENERIC = (
    "XORCISE runs each mission's containers INSIDE its own container, which this host could not "
    "do. Check that Docker can run privileged containers, then re-run 'xorcise doctor'"
)
# A Linux host asked to nest a platform it does not execute natively. There is no toggle for
# this: a qemu binfmt handler runs foreign user-space but cannot service the nf_tables netlink
# calls the inner dockerd makes, so amd64 DinD under emulation dies at iptables init.
_FIX_EMULATION = (
    "this mission's image is {platform}, but this host executes {host} natively and cannot run "
    "{platform} containers nested — running a foreign platform's Docker-in-Docker needs CPU "
    "emulation (qemu binfmt), which cannot bring up the inner Docker daemon on Linux. Choose a "
    "mission that publishes a {host} image (see 'xorcise mission list'), or run XORCISE on a "
    "{platform} host"
)


def _emulation_fix(platform: str | None, host: str | None) -> str:
    return _FIX_EMULATION.format(platform=platform or "a foreign platform", host=host or "another")


def clip_detail(text: str, limit: int = 160) -> str:
    """Keep a verdict readable. A nested-runtime failure arrives as a whole OCI error chain whose
    meaning ("rosetta error: …") is at the END, so the TAIL is what survives the clip — head-
    clipping would leave a line of runc boilerplate that says nothing. Clipped here, at the
    source, so `doctor` and a failed run show the same readable string."""
    text = " ".join(text.split())
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


def check_nested_support(
    *,
    skip: bool,
    probe_tier1: Callable[[], RosettaProbe],
    probe_tier2: Callable[[RosettaProbe], RosettaProbe],
    on_macos: bool | None = None,
    platform: str | None = None,
    host_platform: str | None = None,
) -> NestedSupport:
    """Can this host run the mission stack nested AT `platform`? Pure: both tiers, the OS and the
    platforms are injected.

    Tier 2 — actually starting a container inside a container — is the ONLY gate, because it is
    the thing we need and it means the same on every platform. Tier 1 (the Rosetta binfmt
    registration) is deliberately NOT a gate: Linux has no such handler at all, so gating on it
    would fail every Linux host. It earns its keep twice over anyway — as the cache fingerprint
    (so toggling Rosetta off invalidates a stale positive) and as the explanation for WHY Tier 2
    failed, which is otherwise buried in an OCI error chain.

    `skip` is the escape hatch for hosts where the PROBE cannot run but nesting is known good
    (restricted CI, no privileged containers). It skips the check; it can never re-enable the
    removed sibling topology.

    `platform` is the execution platform the verdict is about (None = the daemon's native one)
    and `host_platform` what the daemon executes natively (None = unknown). Together they pick
    the remediation, because the three ways nesting fails need three different fixes:

      * a FOREIGN platform on a Linux host — amd64 asked of an arm64 machine — is not a
        privileged-container problem, it is "this host cannot run that platform's DinD at all"
        (no binfmt handler ⇒ `exec format error`; a qemu handler ⇒ the inner dockerd dies at
        iptables init). The fix is a different mission or a different host, and saying
        "check privileged containers" sends the operator to fix something that is not broken;
      * amd64 on Apple Silicon is the Rosetta case: on macOS a Tier 1 failure (or a "rosetta"
        error from Tier 2) is the actionable cause, and only there — Tier 1 ALWAYS fails on
        Linux (no rosetta binfmt handler exists there), so choosing the fix on `not tier1.ok`
        alone handed every Linux nesting failure the "enable Rosetta in Docker Desktop" advice
        for settings that do not exist. Rosetta is also irrelevant to a NATIVE arm64 failure on
        the same Mac;
      * everything else is the host's ability to nest its own platform: privileged containers.

    `on_macos` defaults to the real host OS; injected in tests.
    """
    if skip:
        return NestedSupport(True, "nested-container check skipped by configuration")

    macos = host_is_macos() if on_macos is None else on_macos
    wanted = canonical_platform(platform)
    native = canonical_platform(host_platform)
    tier1 = probe_tier1()  # cheap; needed for the fingerprint whatever the verdict
    tier2 = probe_tier2(tier1)
    if tier2.ok:
        return NestedSupport(True, tier2.detail, tier1.fingerprint, platform=wanted or "")

    lowered = tier2.detail.lower()
    # Foreign = a platform was asked for AND it is not what the daemon runs natively. When the
    # native platform is unknown, a wrapper that could not even exec is proof enough.
    foreign = wanted is not None and (
        (native is not None and wanted != native) or "exec format error" in lowered
    )
    amd64_wanted = wanted == "linux/amd64"
    # Rosetta is the amd64-on-Apple-Silicon path and nothing else: a native failure on the same
    # Mac, or an arm64 probe, cannot be a Rosetta problem whatever Tier 1 says.
    rosetta_at_fault = (
        macos
        and (amd64_wanted or (wanted is None and native is None))
        and native != "linux/amd64"
        and (not tier1.ok or "rosetta" in lowered)
    )
    detail = clip_detail(tier2.detail)
    if rosetta_at_fault and not tier1.ok:
        # append the actionable half AFTER clipping, so it is never cut
        detail = f"{detail} (rosetta: {tier1.detail})"
    if rosetta_at_fault:
        fix = _FIX_ROSETTA
    elif foreign and not macos:
        fix = _emulation_fix(wanted, native)
    else:
        fix = _FIX_GENERIC
    return NestedSupport(False, detail, tier1.fingerprint, fix, platform=wanted or "")
