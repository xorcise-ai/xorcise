"""The nested-container precondition: setting + probe + cached verdict.

Three things carry the weight here. The Tier 2 cache must be keyed on the HOST, not merely
present — a verdict that survives a Docker upgrade or a Rosetta toggle is worse than no cache,
because it is confidently wrong. A check that is configured off must open no daemon connection.
And an unsupported host must RAISE, since the host-daemon fallback it used to degrade to no
longer exists.
"""

from __future__ import annotations

import json

import pytest

from xorcise.core.config import Settings
from xorcise.core.contracts.errors import NestedContainersUnavailableError
from xorcise.core.rest import docker_runtime as dr
from xorcise.core.runner.docker.rosetta import RosettaProbe

OK = RosettaProbe(True, "nested amd64 verified", "fp-a")
BAD = RosettaProbe(False, "no rosetta binfmt handler in the Docker VM", "fp-a")


def _settings(mode: str = "enforce") -> Settings:
    return Settings(_env_file=None, nested_container_check=mode)  # type: ignore[arg-type,call-arg]


def _boom() -> object:
    raise AssertionError("no daemon connection may be opened")


# ------------------------------------------------------------------------------- cache


def test_cache_roundtrips(tmp_path) -> None:
    dr.write_cached(OK, home=tmp_path)
    assert dr.read_cached("fp-a", home=tmp_path) == OK


def test_cache_misses_on_a_different_fingerprint(tmp_path) -> None:
    """A Docker/macOS upgrade or a Rosetta toggle changes the fingerprint; the stale verdict
    must not be served."""
    dr.write_cached(OK, home=tmp_path)
    assert dr.read_cached("fp-b", home=tmp_path) is None


def test_cache_miss_on_an_absent_file(tmp_path) -> None:
    assert dr.read_cached("fp-a", home=tmp_path) is None


@pytest.mark.parametrize("junk", ["", "not json", "[]", '"a string"', "null"])
def test_corrupt_cache_re_probes_rather_than_raising(tmp_path, junk: str) -> None:
    (tmp_path / dr.CACHE_NAME).write_text(junk)
    assert dr.read_cached("fp-a", home=tmp_path) is None


def test_cache_records_a_negative_verdict_too(tmp_path) -> None:
    """The probe cost is the same whichever way the answer goes, so a 'no' is worth caching —
    and it is what makes every subsequent run fail FAST instead of re-probing."""
    dr.write_cached(BAD, home=tmp_path)
    cached = dr.read_cached("fp-a", home=tmp_path)
    assert cached is not None and cached.ok is False


def test_an_unwritable_home_does_not_break_the_run(tmp_path) -> None:
    target = tmp_path / "file-not-a-dir"
    target.write_text("x")
    dr.write_cached(OK, home=target / "nested")  # must not raise


# -------------------------------------------------------------------------- support


def test_skip_never_touches_docker() -> None:
    support = dr.nested_support(_settings("skip"), _boom)
    assert support.ok is True


def test_caches_the_tier2_verdict_and_reuses_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    calls: list[int] = []

    def _tier2(_client, **_kw):
        calls.append(1)
        return RosettaProbe(True, "nested amd64 verified")

    monkeypatch.setattr(dr, "verify_nested_amd64", _tier2)

    first = dr.nested_support(_settings(), lambda: object(), home=tmp_path)
    second = dr.nested_support(_settings(), lambda: object(), home=tmp_path)
    assert first.ok and second.ok
    assert calls == [1]  # the slow tier ran exactly once
    record = json.loads((tmp_path / dr.CACHE_NAME).read_text())
    assert record["fingerprint"] == "fp-a" and record["ok"] is True


def test_a_changed_fingerprint_reruns_tier2(tmp_path, monkeypatch) -> None:
    fps = iter(["fp-a", "fp-b"])
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", next(fps)))
    calls: list[int] = []

    def _tier2(_client, **_kw):
        calls.append(1)
        return RosettaProbe(True, "ok")

    monkeypatch.setattr(dr, "verify_nested_amd64", _tier2)
    dr.nested_support(_settings(), lambda: object(), home=tmp_path)
    dr.nested_support(_settings(), lambda: object(), home=tmp_path)
    assert calls == [1, 1]


def test_the_client_factory_is_called_at_most_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    monkeypatch.setattr(dr, "verify_nested_amd64", lambda *_a, **_k: RosettaProbe(True, "ok"))
    made: list[int] = []

    def _factory() -> object:
        made.append(1)
        return object()

    dr.nested_support(_settings(), _factory, home=tmp_path)
    assert made == [1]


def test_nested_image_is_forwarded_to_tier2(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    seen: dict[str, object] = {}

    def _tier2(_client, **kw):
        seen.update(kw)
        return RosettaProbe(True, "ok")

    monkeypatch.setattr(dr, "verify_nested_amd64", _tier2)
    dr.nested_support(
        _settings(), lambda: object(), home=tmp_path, nested_image="xorcise/fused:1"
    )
    assert seen == {"image": "xorcise/fused:1"}


# ----------------------------------------------------------------- enforcement


def test_require_passes_silently_when_supported(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    monkeypatch.setattr(dr, "verify_nested_amd64", lambda *_a, **_k: RosettaProbe(True, "ok"))
    dr.require_nested_support(_settings(), lambda: object(), home=tmp_path)  # must not raise


def test_require_raises_a_typed_error_when_unsupported(tmp_path, monkeypatch) -> None:
    """There is no fallback topology left, so this MUST raise rather than degrade. The error is a
    ContractError so the CLI guard renders it as one clean line."""
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(False, "disabled", "fp-a"))
    monkeypatch.setattr(
        dr, "verify_nested_amd64", lambda *_a, **_k: RosettaProbe(False, "child died")
    )
    with pytest.raises(NestedContainersUnavailableError) as exc:
        dr.require_nested_support(_settings(), lambda: object(), home=tmp_path)
    message = str(exc.value)
    assert "child died" in message  # what went wrong
    assert "Rosetta" in message  # how to fix it
    assert "XORCISE_NESTED_CONTAINER_CHECK=skip" in message  # how to bypass it


def test_require_is_a_no_op_when_the_check_is_skipped() -> None:
    dr.require_nested_support(_settings("skip"), _boom)  # must not raise, must not probe
