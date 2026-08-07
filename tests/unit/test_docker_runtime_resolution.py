"""The macOS container-runtime composition point: setting + probe + cached verdict.

Two things carry the weight here. First, the Tier 2 cache must be keyed on the HOST, not merely
present — a verdict that survives a Docker Desktop upgrade or a Rosetta toggle is worse than no
cache, because it is confidently wrong. Second, the resolution must open no daemon connection
when the setting alone decides, or `xorcise` gains a new way to fail on a Docker-less host.
"""

from __future__ import annotations

import json

import pytest

from xorcise.core.config import Settings
from xorcise.core.rest import docker_runtime as dr
from xorcise.core.runner.docker.rosetta import RosettaProbe

OK = RosettaProbe(True, "nested amd64 verified", "fp-a")
BAD = RosettaProbe(False, "no rosetta binfmt handler in the Docker VM", "fp-a")


def _settings(mode: str) -> Settings:
    return Settings(macos_container_runtime=mode)  # type: ignore[arg-type]


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
    """The 20-40 s cost is the same whichever way the answer goes, so a 'no' is worth caching."""
    dr.write_cached(BAD, home=tmp_path)
    cached = dr.read_cached("fp-a", home=tmp_path)
    assert cached is not None and cached.ok is False


def test_an_unwritable_home_does_not_break_the_run(tmp_path) -> None:
    target = tmp_path / "file-not-a-dir"
    target.write_text("x")
    dr.write_cached(OK, home=target / "nested")  # must not raise


# -------------------------------------------------------------------------- resolution


def test_pinned_host_daemon_never_touches_docker(monkeypatch) -> None:
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    decision = dr.resolve_runtime(_settings("host-daemon"), _boom)
    assert decision.mode == "host-daemon"
    assert decision.use_host_daemon is True


def test_non_macos_never_touches_docker(monkeypatch) -> None:
    monkeypatch.setattr(dr, "host_is_macos", lambda: False)
    assert dr.resolve_runtime(_settings("auto"), _boom).mode == "dind"


def test_auto_caches_the_tier2_verdict_and_reuses_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    calls: list[int] = []

    def _tier2(_client, **_kw):
        calls.append(1)
        return RosettaProbe(True, "nested amd64 verified")

    monkeypatch.setattr(dr, "verify_nested_amd64", _tier2)

    first = dr.resolve_runtime(_settings("auto"), lambda: object(), home=tmp_path)
    second = dr.resolve_runtime(_settings("auto"), lambda: object(), home=tmp_path)
    assert first.mode == second.mode == "dind"
    assert calls == [1]  # the slow tier ran exactly once
    record = json.loads((tmp_path / dr.CACHE_NAME).read_text())
    assert record["fingerprint"] == "fp-a" and record["ok"] is True


def test_a_changed_fingerprint_reruns_tier2(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    fps = iter(["fp-a", "fp-b"])
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", next(fps)))
    calls: list[int] = []

    def _tier2(_client, **_kw):
        calls.append(1)
        return RosettaProbe(True, "ok")

    monkeypatch.setattr(dr, "verify_nested_amd64", _tier2)
    dr.resolve_runtime(_settings("auto"), lambda: object(), home=tmp_path)
    dr.resolve_runtime(_settings("auto"), lambda: object(), home=tmp_path)
    assert calls == [1, 1]


def test_tier1_failure_skips_tier2_entirely(tmp_path, monkeypatch) -> None:
    """Tier 1 is the per-deploy guard: a Rosetta toggle flipped off mid-session must fall back
    immediately, without a cached 'yes' or a 40 s re-probe getting in the way."""
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(False, "disabled", "fp-a"))
    monkeypatch.setattr(
        dr, "verify_nested_amd64", lambda *_a, **_k: pytest.fail("tier 2 must not run")
    )
    dr.write_cached(OK, home=tmp_path)  # a stale positive that must NOT be consulted
    decision = dr.resolve_runtime(_settings("auto"), lambda: object(), home=tmp_path)
    assert decision.mode == "host-daemon"


def test_the_client_factory_is_called_at_most_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    monkeypatch.setattr(dr, "verify_nested_amd64", lambda *_a, **_k: RosettaProbe(True, "ok"))
    made: list[int] = []

    def _factory() -> object:
        made.append(1)
        return object()

    dr.resolve_runtime(_settings("auto"), _factory, home=tmp_path)
    assert made == [1]


def test_nested_image_is_forwarded_to_tier2(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "h", "fp-a"))
    seen: dict[str, object] = {}

    def _tier2(_client, **kw):
        seen.update(kw)
        return RosettaProbe(True, "ok")

    monkeypatch.setattr(dr, "verify_nested_amd64", _tier2)
    dr.resolve_runtime(
        _settings("auto"), lambda: object(), home=tmp_path, nested_image="xorcise/fused:1"
    )
    assert seen == {"image": "xorcise/fused:1"}


# ----------------------------------------------------------------- doctor's view


def test_inspect_probes_even_when_the_setting_pins_the_mode(tmp_path, monkeypatch) -> None:
    """Shipping the probe ahead of the default flip is only useful if it actually runs on real
    hosts — and the shipped default pins host-daemon, which resolves without probing."""
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    decision, probe = dr.inspect_runtime(_settings("host-daemon"), lambda: object(), home=tmp_path)
    assert decision.mode == "host-daemon"
    assert probe is not None and probe.ok


def test_inspect_never_pays_for_tier2(tmp_path, monkeypatch) -> None:
    """`doctor` is interactive — it may read the cache, never refresh it."""
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    monkeypatch.setattr(
        dr, "verify_nested_amd64", lambda *_a, **_k: pytest.fail("tier 2 must not run")
    )
    dr.inspect_runtime(_settings("host-daemon"), lambda: object(), home=tmp_path)


def test_inspect_prefers_the_cached_tier2_verdict(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "host_is_macos", lambda: True)
    monkeypatch.setattr(dr, "binfmt_signal", lambda _c: RosettaProbe(True, "handler", "fp-a"))
    dr.write_cached(RosettaProbe(False, "nested child reported 'aarch64'", "fp-a"), home=tmp_path)
    _decision, probe = dr.inspect_runtime(_settings("host-daemon"), lambda: object(), home=tmp_path)
    assert probe is not None and probe.ok is False


def test_inspect_reports_no_probe_off_macos(monkeypatch) -> None:
    monkeypatch.setattr(dr, "host_is_macos", lambda: False)
    decision, probe = dr.inspect_runtime(_settings("auto"), _boom)
    assert decision.mode == "dind"
    assert probe is None
