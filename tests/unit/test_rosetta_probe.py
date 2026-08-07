"""Nested-Rosetta probe parsing + the macOS runtime decision — pure, no Docker.

The parse is the whole of Tier 1: everything the cheap tier concludes, it concludes from this
text. The decision function is the only thing that chooses a topology, so every branch of it is
pinned here rather than exercised through a driver.
"""

from __future__ import annotations

import pytest

from xorcise.core.runner.docker.rosetta import (
    RosettaProbe,
    decide,
    fingerprint,
    parse_binfmt,
)

# A real registration, as read from Docker Desktop's VM on Apple Silicon.
REAL = """enabled
interpreter /run/rosetta/rosetta
flags: POCF
offset 0
magic 7f454c460201010000000000000000000200
mask ffffffffffffff00fffffffffffffffffeffffff
"""


def _reg(*, enabled: str = "enabled", flags: str = "POCF", magic: str | None = None) -> str:
    magic = "7f454c46020101000000000000000000" + "0200" + "3e00" if magic is None else magic
    return f"{enabled}\ninterpreter /run/rosetta/rosetta\nflags: {flags}\noffset 0\nmagic {magic}\n"


# ----------------------------------------------------------------------- tier 1 parsing


def test_accepts_a_real_enabled_x86_64_registration() -> None:
    probe = parse_binfmt(_reg())
    assert probe.ok
    assert "POCF" in probe.detail


def test_no_handler_is_not_available() -> None:
    assert parse_binfmt("XORCISE_NO_HANDLER").ok is False
    assert parse_binfmt("").ok is False
    assert parse_binfmt("   \n ").ok is False


def test_disabled_registration_is_rejected() -> None:
    """Docker Desktop leaves the registration behind when the Rosetta toggle goes off, so
    presence alone must never count as availability."""
    probe = parse_binfmt(_reg(enabled="disabled"))
    assert probe.ok is False
    assert "disabled" in probe.detail


def test_missing_F_flag_is_rejected() -> None:
    """The fix-binary flag is the ENTIRE mechanism that makes nesting work — without it the
    interpreter must exist in each container's mount namespace, which is exactly the failure the
    old 'Rosetta fails for nested DinD children' premise described."""
    probe = parse_binfmt(_reg(flags="POC"))
    assert probe.ok is False
    assert "F (fix binary)" in probe.detail


def test_wrong_architecture_magic_is_rejected() -> None:
    # e_machine 0x28 (ARM) rather than 0x3e (x86-64)
    probe = parse_binfmt(_reg(magic="7f454c4602010100000000000000000002002800"))
    assert probe.ok is False
    assert "ELF64 x86-64" in probe.detail


def test_malformed_text_is_rejected_not_raised() -> None:
    for raw in ("enabled", "flags: POCF", "\x00\xff garbage", "enabled\nflags:\n"):
        assert parse_binfmt(raw).ok is False


def test_truncated_magic_without_e_machine_is_rejected() -> None:
    """The upstream sample above stops before e_machine; it must not be read as x86-64."""
    assert parse_binfmt(REAL).ok is False


# --------------------------------------------------------------------------- fingerprint


def test_fingerprint_changes_with_docker_version() -> None:
    a = fingerprint(docker_version="29.6.2", macos_version="15.7.7", binfmt_raw=_reg())
    b = fingerprint(docker_version="30.0.0", macos_version="15.7.7", binfmt_raw=_reg())
    assert a != b


def test_fingerprint_changes_with_macos_version() -> None:
    a = fingerprint(docker_version="29.6.2", macos_version="15.7.7", binfmt_raw=_reg())
    b = fingerprint(docker_version="29.6.2", macos_version="26.0", binfmt_raw=_reg())
    assert a != b


def test_fingerprint_changes_when_the_registration_changes() -> None:
    """Toggling Rosetta off, or switching VMM, rewrites the handler — a cached Tier 2 verdict
    must not survive that."""
    a = fingerprint(docker_version="29.6.2", macos_version="15.7.7", binfmt_raw=_reg())
    b = fingerprint(
        docker_version="29.6.2", macos_version="15.7.7", binfmt_raw=_reg(flags="POC")
    )
    assert a != b


def test_fingerprint_is_stable_for_an_unchanged_host() -> None:
    args = {"docker_version": "29.6.2", "macos_version": "15.7.7", "binfmt_raw": _reg()}
    assert fingerprint(**args) == fingerprint(**args)


# ----------------------------------------------------------------------------- decision

OK = RosettaProbe(True, "nested amd64 verified")
BAD = RosettaProbe(False, "no rosetta binfmt handler in the Docker VM")


def _never(*_a: object, **_k: object) -> RosettaProbe:
    raise AssertionError("probe must not run")


@pytest.mark.parametrize("setting", ["auto", "dind", "host-daemon"])
def test_linux_is_always_dind_and_never_probes(setting: str) -> None:
    """Linux never had a sibling path — probing there would be pure cost."""
    d = decide(setting, is_macos=False, probe_tier1=_never, probe_tier2=_never)
    assert d.mode == "dind"
    assert d.use_host_daemon is False


@pytest.mark.parametrize("setting,mode", [("host-daemon", "host-daemon"), ("dind", "dind")])
def test_a_pinned_setting_short_circuits_the_probe(setting: str, mode: str) -> None:
    """The support escape hatch has to work on a host where the probe itself misbehaves."""
    d = decide(setting, is_macos=True, probe_tier1=_never, probe_tier2=_never)
    assert d.mode == mode
    assert "pinned" in d.reason


def test_auto_takes_dind_when_both_tiers_pass() -> None:
    d = decide("auto", is_macos=True, probe_tier1=lambda: OK, probe_tier2=lambda _t: OK)
    assert d.mode == "dind"
    assert d.use_host_daemon is False
    assert d.probe == OK


def test_auto_falls_back_when_tier1_fails_and_never_pays_for_tier2() -> None:
    d = decide("auto", is_macos=True, probe_tier1=lambda: BAD, probe_tier2=_never)
    assert d.mode == "host-daemon"
    assert d.reason == BAD.detail


def test_auto_falls_back_when_tier2_fails() -> None:
    """Tier 1 can pass on a host where the whole chain still does not work — ground truth wins."""
    d = decide("auto", is_macos=True, probe_tier1=lambda: OK, probe_tier2=lambda _t: BAD)
    assert d.mode == "host-daemon"
    assert d.probe == BAD


def test_auto_passes_the_tier1_verdict_into_tier2() -> None:
    """Tier 2's cache key is Tier 1's fingerprint, so the handoff must actually happen."""
    seen: list[RosettaProbe] = []
    tier1 = RosettaProbe(True, "handler present", "fp-123")

    def _tier2(t: RosettaProbe) -> RosettaProbe:
        seen.append(t)
        return OK

    decide("auto", is_macos=True, probe_tier1=lambda: tier1, probe_tier2=_tier2)
    assert seen == [tier1]
