"""Nested-Rosetta capability probe + macOS container-runtime decision (runner part-island).

WHY THIS EXISTS. On macOS the runner mounts Docker Desktop's socket into the fused image and
composes the mission stack as HOST-DAEMON SIBLINGS (`driver.py`), on the premise that "Rosetta
fails for nested DinD children". The OBSERVATION was real; the diagnosis was wrong, and nesting
was never the cause. Root-caused empirically:

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

Sibling mode is the direct cause of three defects that all vanish under DinD: mission-authored
`ports:` binding on the operator's Mac, teardown leaking un-labelled mission containers, and
mission bind mounts being denied because the path lives inside the outer container.

WHY A PROBE, NOT A VERSION CHECK. Four independent gates must all hold and only one is a macOS
version: Apple Silicon, macOS >= 13, the VMM being Apple's Virtualization framework (Docker VMM
has no Rosetta at all), and the Rosetta toggle being on. Gates 3 and 4 — and regressions like
docker/for-mac#7322 — all present identically as "amd64 won't run" and are invisible in any
version string. Rosetta is also slated for removal in macOS 28, with the Linux-VM path's fate
publicly unresolved. A behavioural probe is correct across all of that; a version check is not.

Everything here fails CLOSED: any error, timeout or ambiguity yields ok=False, so an unknown host
takes the proven sibling path.

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
from typing import Any, Literal

# Host-arch image used to read the VM's binfmt registration. Tiny (~4 MB) and pulled on demand.
BINFMT_PROBE_IMAGE = "alpine:3.20"
# DinD image used for the ground-truth nested check, run at `linux/amd64` — the shape XORCISE
# ships today. This MUST track the `FROM` in containers/mission-base/Dockerfile (asserted by
# tests/topology/test_dind_base_parity.py).
#
# The capability is NOT a property of the host. It fails only when BOTH hold: the wrapper is
# amd64 (so its dockerd runs under Rosetta) AND the engine is <= 27 (so it installs the
# `/proc/<pid>/exe` prestart hook Rosetta cannot exec — see the module docstring). Break either
# and nesting works; verified on an unchanged host:
#     amd64 wrapper + engine 27  -> fails
#     amd64 wrapper + engine 28  -> works   (hook removed upstream, moby#47406)
#     arm64 wrapper + engine 27  -> works   (dockerd is native; Rosetta never sees the hook)
# So probing a different image than the one we ship — different engine OR different arch —
# reports a capability the fused image does not have. Prefer passing the fused mission image
# itself when one is available: exactly the shipped artifact, and already local.
NESTED_PROBE_IMAGE = "docker:27-dind"
# Ceiling for the Tier 2 container: inner dockerd boot + an inner amd64 pull + exec.
NESTED_PROBE_TIMEOUT = 180

RuntimeMode = Literal["dind", "host-daemon"]

_SENTINEL = "XORCISE_NESTED_ARCH="
_ERR_SENTINEL = "XORCISE_NESTED_ERR="
_NO_HANDLER = "XORCISE_NO_HANDLER"

_BINFMT_SCRIPT = (
    # binfmt_misc is usually unmounted inside a container; mounting it is why this needs
    # --privileged. Never fail the script — an absent handler must read as "not available",
    # not as a container error we cannot tell apart from a broken daemon.
    "mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc 2>/dev/null || true; "
    f"cat /proc/sys/fs/binfmt_misc/rosetta 2>/dev/null || echo {_NO_HANDLER}"
)

_NESTED_SCRIPT = (
    # The docker:dind image ships DOCKER_HOST=tcp://docker:2375 (plus TLS vars) for the
    # docker-compose "dind sidecar" pattern. Left set, the inner CLI dials a host named `docker`
    # that does not exist here and never reaches the daemon it just started — the probe then
    # times out and reports "not available" on a host where nesting works perfectly. Clear them
    # so the CLI falls back to the local unix socket.
    "unset DOCKER_HOST DOCKER_TLS_VERIFY DOCKER_CERT_PATH; "
    "dockerd-entrypoint.sh dockerd >/tmp/dockerd.log 2>&1 & "
    "i=0; while ! docker info >/dev/null 2>&1; do "
    "  i=$((i+1)); [ $i -gt 90 ] && exit 1; sleep 1; done; "
    # Emit BOTH lines unconditionally. The two ways this fails need different fixes and look
    # identical from an empty arch alone: the inner daemon never starting, versus the daemon
    # being fine while the amd64 child dies in the runtime (e.g. "rosetta error: failed to open
    # elf"). Reaching the sentinel at all proves the daemon came up, so the presence of these
    # lines is what separates the two — and the captured stderr names the real cause.
    # -q suppresses the inner pull's progress, which is written to stderr and would otherwise
    # bury the actual error; the tail (not head) is taken for the same reason — the runtime's
    # failure is the LAST thing on stderr.
    f"arch=$(docker run --rm -q --platform linux/amd64 {BINFMT_PROBE_IMAGE} uname -m 2>/tmp/err); "
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
class RuntimeDecision:
    mode: RuntimeMode
    reason: str
    probe: RosettaProbe | None = None

    @property
    def use_host_daemon(self) -> bool:
        return self.mode == "host-daemon"


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


def verify_nested_amd64(
    client: Any,
    *,
    image: str = NESTED_PROBE_IMAGE,
    timeout: int = NESTED_PROBE_TIMEOUT,
) -> RosettaProbe:
    """Tier 2 (~20-40 s). The only check that proves the WHOLE chain: start a throwaway
    `--platform linux/amd64` DinD, wait for its inner daemon, run an amd64 child inside it and
    assert the child reports `x86_64`.

    Slow by nature, so the caller caches the verdict against the Tier 1 fingerprint. The container
    is always removed, including on timeout — a stranded privileged DinD would be worse than a
    wrong answer."""
    container = None
    try:
        container = client.containers.run(
            image,
            command=["sh", "-c", _NESTED_SCRIPT],
            privileged=True,
            detach=True,
            platform="linux/amd64",  # the outer amd64 layer — Rosetta's first hop
        )
        result = container.wait(timeout=timeout)
        logs = container.logs()
        text = logs.decode(errors="replace") if isinstance(logs, bytes) else str(logs)
    except Exception as exc:  # noqa: BLE001 — fail closed
        return RosettaProbe(False, f"nested amd64 probe failed: {type(exc).__name__}: {exc}")
    finally:
        if container is not None:
            # best effort; already-gone is the common case
            with contextlib.suppress(Exception):
                container.remove(force=True)

    arch = ""
    err = ""
    reached_child = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(_SENTINEL):
            reached_child = True
            arch = line[len(_SENTINEL) :].strip()
        elif line.startswith(_ERR_SENTINEL):
            err = line[len(_ERR_SENTINEL) :].strip()

    if arch == "x86_64":
        return RosettaProbe(True, "nested amd64 verified (x86_64 inside an amd64 DinD)")
    status = (result or {}).get("StatusCode") if isinstance(result, dict) else result
    if not reached_child:
        return RosettaProbe(
            False, f"the DinD probe's inner daemon never came up (exit {status})"
        )
    if not arch:
        # The daemon was healthy and the amd64 child still failed — the interesting case, and
        # the one whose cause is only in the runtime's stderr.
        return RosettaProbe(
            False, f"nested amd64 container failed to start: {err or 'no error output'}"
        )
    return RosettaProbe(False, f"nested child reported {arch!r}, expected 'x86_64'")


# ----------------------------------------------------------------------- decision (pure)


def decide(
    setting: str,
    *,
    is_macos: bool,
    probe_tier1: Callable[[], RosettaProbe],
    probe_tier2: Callable[[RosettaProbe], RosettaProbe],
) -> RuntimeDecision:
    """Resolve the container runtime for this host. Pure: both tiers are injected, so every
    branch is unit-testable without a daemon.

    Linux never reaches the sibling path at all — DinD is the original design and the only path
    there — so the probe is skipped outright rather than answered.

    Under `auto` the tiers are ordered cheap-first and BOTH must pass: Tier 1 is a per-deploy
    guard, Tier 2 is ground truth. Falling back to host-daemon on a Tier 2 failure is deliberate
    (see the plan's open question 2): silently degrading is safer than failing a run outright, and
    `doctor` surfaces the verdict so a misconfigured host is still visible.
    """
    if not is_macos:
        return RuntimeDecision("dind", "not macOS — DinD is the only path")
    if setting == "host-daemon":
        return RuntimeDecision("host-daemon", "pinned by macos_container_runtime")
    if setting == "dind":
        return RuntimeDecision("dind", "pinned by macos_container_runtime")

    tier1 = probe_tier1()
    if not tier1.ok:
        return RuntimeDecision("host-daemon", tier1.detail, tier1)
    tier2 = probe_tier2(tier1)
    if not tier2.ok:
        return RuntimeDecision("host-daemon", tier2.detail, tier2)
    return RuntimeDecision("dind", tier2.detail, tier2)
