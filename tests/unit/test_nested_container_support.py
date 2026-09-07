"""The nested-container precondition: setting + probe + in-memory memo + base-compat gate.

Four things carry the weight here. A check that is configured off must open no daemon
connection. The expensive probe must run at most once per process AND PLATFORM (memoised in
memory — there is no on-disk cache: a persisted verdict turned a transient probe failure into a
permanent refusal), while `doctor` can force a fresh re-probe. An unsupported host must RAISE,
since the host-daemon fallback it used to degrade to no longer exists. And an artifact fused on
an incompatible base generation must be refused up front, with direction-aware advice.
"""

from __future__ import annotations

import pytest

from xorcise.core.config import Settings
from xorcise.core.contracts.errors import (
    BaseImageIncompatibleError,
    NestedContainersUnavailableError,
)
from xorcise.core.rest import docker_runtime as dr
from xorcise.core.runner.docker import rosetta
from xorcise.core.runner.docker.rosetta import RosettaProbe


def _settings(mode: str = "enforce") -> Settings:
    return Settings(_env_file=None, nested_container_check=mode)  # type: ignore[arg-type,call-arg]


def _boom() -> object:
    raise AssertionError("no daemon connection may be opened")


@pytest.fixture(autouse=True)
def _clear_memo():
    """The verdicts are process-global; reset them around each test so they do not leak."""
    dr.reset_nested_support_memo()
    yield
    dr.reset_nested_support_memo()


class _Daemon:
    """A client whose version report names the platform it executes natively."""

    def __init__(self, arch: str = "amd64") -> None:
        self._arch = arch

    def version(self) -> dict[str, str]:
        return {"Os": "linux", "Arch": self._arch}


# ------------------------------------------------------------------------------- memo


def test_skip_never_touches_docker() -> None:
    assert dr.nested_support(_settings("skip"), _boom).ok is True
    assert dr.nested_support(_settings("skip"), _boom, platform="linux/amd64").ok is True


def test_probe_runs_once_then_serves_the_memo(monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    calls: list[int] = []

    def _tier2(_client, **_kw):
        calls.append(1)
        return RosettaProbe(True, "nested amd64 verified")

    monkeypatch.setattr(dr, "verify_nested", _tier2)

    first = dr.nested_support(_settings(), lambda: object())
    second = dr.nested_support(_settings(), lambda: object())
    assert first.ok and second.ok
    assert calls == [1]  # the slow tier ran exactly once — memoised in memory


def test_the_memo_is_per_platform(monkeypatch) -> None:
    """Nesting is a property of (host, platform): an arm64 host nests arm64 and cannot nest amd64.
    One verdict per platform, each probed at most once, and the probe is asked about the platform
    the caller named."""
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    asked: list[str | None] = []

    def _tier2(_client, *, platform=None, **_kw):
        asked.append(platform)
        return RosettaProbe(platform != "linux/amd64", f"probe of {platform}")

    monkeypatch.setattr(dr, "verify_nested", _tier2)
    daemon = lambda: _Daemon("arm64")  # noqa: E731

    assert dr.nested_support(_settings(), daemon, platform="linux/arm64").ok is True
    assert dr.nested_support(_settings(), daemon, platform="linux/amd64").ok is False
    assert dr.nested_support(_settings(), daemon, platform="linux/arm64").ok is True
    assert dr.nested_support(_settings(), daemon, platform="linux/amd64").ok is False
    assert asked == ["linux/arm64", "linux/amd64"]  # one probe per platform, then the memo
    assert dr.nested_support(_settings(), daemon, platform="linux/amd64").platform == "linux/amd64"


def test_a_native_prewarm_answers_for_the_explicit_native_platform(monkeypatch) -> None:
    """Boot pre-warms with no platform (the daemon's native one); the first run then asks about
    the platform its install recorded — the same spelling of the same question. It must read the
    warm verdict, not pay a second probe (the "first run hangs" symptom pre-warm exists for)."""
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    calls: list[str | None] = []

    def _tier2(_client, *, platform=None, **_kw):
        calls.append(platform)
        return RosettaProbe(True, "ok")

    monkeypatch.setattr(dr, "verify_nested", _tier2)
    daemon = lambda: _Daemon("arm64")  # noqa: E731

    dr.prewarm_nested_support(_settings(), daemon)
    assert dr.nested_support(_settings(), daemon, platform="linux/arm64").ok
    assert dr.nested_support(_settings(), daemon, platform="linux/arm64/v8").ok
    # The warm-up was the only probe — asked with the native platform spelt out, so the wrapper
    # insists on the right image even when the local tag holds another platform's copy.
    assert calls == ["linux/arm64"]


def test_fresh_forces_a_reprobe_and_refreshes_the_memo(monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    verdicts = iter(
        [RosettaProbe(False, "child died"), RosettaProbe(True, "nested amd64 verified")]
    )
    monkeypatch.setattr(dr, "verify_nested", lambda *_a, **_k: next(verdicts))

    assert dr.nested_support(_settings(), lambda: object()).ok is False
    # fresh=True re-probes (doctor reporting current health) and updates the memo,
    assert dr.nested_support(_settings(), lambda: object(), fresh=True).ok is True
    # so a subsequent plain read now sees the refreshed verdict.
    assert dr.nested_support(_settings(), lambda: object()).ok is True


def test_the_probe_client_is_closed(monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    monkeypatch.setattr(dr, "verify_nested", lambda *_a, **_k: RosettaProbe(True, "ok"))
    closed: list[int] = []

    class _Client:
        def close(self) -> None:
            closed.append(1)

    dr.nested_support(_settings(), _Client)
    assert closed == [1]  # one client per probe, released after — not leaked per run


# ----------------------------------------------------------------- enforcement


def test_require_passes_silently_when_supported(monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    monkeypatch.setattr(dr, "verify_nested", lambda *_a, **_k: RosettaProbe(True, "ok"))
    dr.require_nested_support(_settings(), lambda: object())  # must not raise
    dr.require_nested_support(_settings(), lambda: object(), platform="linux/amd64")


def test_require_raises_a_typed_error_when_unsupported(monkeypatch) -> None:
    """There is no fallback topology left, so this MUST raise rather than degrade. On macOS the
    error names Rosetta for an amd64 mission; the ContractError renders as one clean CLI line.
    Both spellings of the bypass are named, and the restart it needs — the server reads its
    settings once at boot, so a config.toml edit alone changes nothing (#79 finding 3)."""
    monkeypatch.setattr(rosetta, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(False, "disabled", "fp-a"))
    monkeypatch.setattr(dr, "verify_nested", lambda *_a, **_k: RosettaProbe(False, "child died"))
    with pytest.raises(NestedContainersUnavailableError) as exc:
        dr.require_nested_support(_settings(), lambda: _Daemon("arm64"), platform="linux/amd64")
    message = str(exc.value)
    assert "(linux/amd64)" in message  # which platform the refusal is about
    assert "child died" in message  # what went wrong
    assert "Rosetta" in message  # how to fix it (macOS, amd64 on Apple Silicon)
    assert "XORCISE_NESTED_CONTAINER_CHECK=skip" in message  # how to bypass it (env)
    assert 'nested_container_check = "skip"' in message  # how to bypass it (config.toml)
    assert "xorcise down && xorcise up" in message  # and that either needs a restart


def test_require_names_the_emulation_problem_for_a_foreign_platform_on_linux(monkeypatch) -> None:
    """The #79 message. An arm64 Linux host refused an amd64 mission must be told THAT, not to
    check privileged containers."""
    monkeypatch.setattr(rosetta, "host_is_macos", lambda: False)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(False, "no handler", "fp"))
    monkeypatch.setattr(
        dr,
        "verify_nested",
        lambda *_a, **_k: RosettaProbe(
            False,
            "the linux/amd64 DinD probe's inner daemon never came up (exit 255): exec "
            "/usr/local/bin/dockerd-entrypoint.sh: exec format error",
        ),
    )
    with pytest.raises(NestedContainersUnavailableError) as exc:
        dr.require_nested_support(_settings(), lambda: _Daemon("arm64"), platform="linux/amd64")
    message = str(exc.value)
    assert "exec format error" in message
    assert "emulation" in message
    assert "privileged" not in message


def test_require_is_a_no_op_when_the_check_is_skipped() -> None:
    dr.require_nested_support(_settings("skip"), _boom)  # must not raise, must not probe


# ----------------------------------------------------------------- base-compat gate


def test_base_compat_allows_the_supported_generation_via_tag() -> None:
    dr.require_base_compatible("reg/xorcise/mis-x:abc123-base2")  # must not raise


def test_base_compat_allows_the_supported_generation_via_label() -> None:
    dr.require_base_compatible(
        "xorcise/mission-x:local",
        label_lookup=lambda _ref: {"ai.xorcise.base.version": "2"},
    )


def test_base_compat_refuses_an_older_artifact_with_repull_advice() -> None:
    with pytest.raises(BaseImageIncompatibleError) as exc:
        dr.require_base_compatible("reg/xorcise/mis-x:abc123-base1")
    assert "mission update" in str(exc.value)  # the ONE update action (§35)


def test_base_compat_refuses_a_newer_artifact_with_upgrade_advice() -> None:
    with pytest.raises(BaseImageIncompatibleError) as exc:
        dr.require_base_compatible("reg/xorcise/mis-x:abc123-base9")
    assert "upgrade" in str(exc.value).lower()  # the client is behind


def test_base_compat_allows_when_generation_is_undeterminable() -> None:
    # A pre-versioning local fuse: no suffix, no label. Allow rather than block on a signal we
    # cannot read — "re-pull" is not even the right advice for a local ingest.
    dr.require_base_compatible("xorcise/mission-x:local")


def test_base_compat_refuses_a_metadata_less_library_install() -> None:
    # CG4/LEG3: every published artifact carries the base label and the -baseN suffix, so a
    # LIBRARY install with neither predates the versioned image format — refused with the one
    # update action, not parsed forever.
    with pytest.raises(BaseImageIncompatibleError) as exc:
        dr.require_base_compatible("reg/xorcise/mis-x:oldformat", origin="library")
    assert "older XORCISE image format" in str(exc.value)
    assert "mission update" in str(exc.value)


def test_base_compat_keeps_the_allowance_for_your_own_fuses() -> None:
    # A local fuse has no catalog upstream; "update from the catalog" is not even the right
    # advice, so the undeterminable-base allowance stays.
    dr.require_base_compatible("xorcise/mission-x:local", origin="your_own")
