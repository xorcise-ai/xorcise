"""The nested-container precondition: setting + probe + in-memory memo + base-compat gate.

Four things carry the weight here. A check that is configured off must open no daemon
connection. The expensive probe must run at most once per process (memoised in memory — there is
no on-disk cache: a persisted verdict turned a transient probe failure into a permanent refusal),
while `doctor` can force a fresh re-probe. An unsupported host must RAISE, since the host-daemon
fallback it used to degrade to no longer exists. And an artifact fused on an incompatible base
generation must be refused up front, with direction-aware advice.
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
    """The verdict is process-global; reset it around each test so they do not leak."""
    dr._memo = None
    dr._last_logged = None
    yield
    dr._memo = None
    dr._last_logged = None


# ------------------------------------------------------------------------------- memo


def test_skip_never_touches_docker() -> None:
    assert dr.nested_support(_settings("skip"), _boom).ok is True


def test_probe_runs_once_then_serves_the_memo(monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    calls: list[int] = []

    def _tier2(_client, **_kw):
        calls.append(1)
        return RosettaProbe(True, "nested amd64 verified")

    monkeypatch.setattr(dr, "verify_nested_amd64", _tier2)

    first = dr.nested_support(_settings(), lambda: object())
    second = dr.nested_support(_settings(), lambda: object())
    assert first.ok and second.ok
    assert calls == [1]  # the slow tier ran exactly once — memoised in memory


def test_fresh_forces_a_reprobe_and_refreshes_the_memo(monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    verdicts = iter(
        [RosettaProbe(False, "child died"), RosettaProbe(True, "nested amd64 verified")]
    )
    monkeypatch.setattr(dr, "verify_nested_amd64", lambda *_a, **_k: next(verdicts))

    assert dr.nested_support(_settings(), lambda: object()).ok is False
    # fresh=True re-probes (doctor reporting current health) and updates the memo,
    assert dr.nested_support(_settings(), lambda: object(), fresh=True).ok is True
    # so a subsequent plain read now sees the refreshed verdict.
    assert dr.nested_support(_settings(), lambda: object()).ok is True


def test_the_probe_client_is_closed(monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    monkeypatch.setattr(dr, "verify_nested_amd64", lambda *_a, **_k: RosettaProbe(True, "ok"))
    closed: list[int] = []

    class _Client:
        def close(self) -> None:
            closed.append(1)

    dr.nested_support(_settings(), _Client)
    assert closed == [1]  # one client per probe, released after — not leaked per run


# ----------------------------------------------------------------- enforcement


def test_require_passes_silently_when_supported(monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    monkeypatch.setattr(dr, "verify_nested_amd64", lambda *_a, **_k: RosettaProbe(True, "ok"))
    dr.require_nested_support(_settings(), lambda: object())  # must not raise


def test_require_raises_a_typed_error_when_unsupported(monkeypatch) -> None:
    """There is no fallback topology left, so this MUST raise rather than degrade. On macOS the
    error names Rosetta; the ContractError renders as one clean CLI line."""
    monkeypatch.setattr(rosetta, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(False, "disabled", "fp-a"))
    monkeypatch.setattr(
        dr, "verify_nested_amd64", lambda *_a, **_k: RosettaProbe(False, "child died")
    )
    with pytest.raises(NestedContainersUnavailableError) as exc:
        dr.require_nested_support(_settings(), lambda: object())
    message = str(exc.value)
    assert "child died" in message  # what went wrong
    assert "Rosetta" in message  # how to fix it (macOS)
    assert "XORCISE_NESTED_CONTAINER_CHECK=skip" in message  # how to bypass it


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
    assert "mission pull" in str(exc.value)  # re-pull the newer artifact


def test_base_compat_refuses_a_newer_artifact_with_upgrade_advice() -> None:
    with pytest.raises(BaseImageIncompatibleError) as exc:
        dr.require_base_compatible("reg/xorcise/mis-x:abc123-base9")
    assert "upgrade" in str(exc.value).lower()  # the client is behind


def test_base_compat_allows_when_generation_is_undeterminable() -> None:
    # A pre-versioning local fuse: no suffix, no label. Allow rather than block on a signal we
    # cannot read — "re-pull" is not even the right advice for a local ingest.
    dr.require_base_compatible("xorcise/mission-x:local")
