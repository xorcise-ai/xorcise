"""`doctor`'s macOS container-runtime check.

The check exists to collect real verdicts from real hosts ahead of the default flip, so the
properties that matter are: it never blocks the verdict for the shipped default, it DOES warn on
the one configuration that is actually broken (pinned dind on a host without nested Rosetta),
and it survives a probe that explodes — an advisory line must never take `doctor` down with it.
"""

from __future__ import annotations

from xorcise.core.cli import _diagnostics as diag
from xorcise.core.rest import docker_runtime as dr
from xorcise.core.runner.docker.rosetta import RosettaProbe, RuntimeDecision

OK = RosettaProbe(True, "nested amd64 verified", "fp-a")
BAD = RosettaProbe(False, "rosetta binfmt handler is registered but disabled", "fp-a")


def _inspect(decision: RuntimeDecision, probe: RosettaProbe | None):
    return lambda *_a, **_k: (decision, probe)


def test_host_daemon_with_a_working_probe_is_advisory_and_passing(monkeypatch) -> None:
    monkeypatch.setattr(
        dr, "inspect_runtime", _inspect(RuntimeDecision("host-daemon", "pinned by setting"), OK)
    )
    check = diag.container_runtime()
    assert check.ok is True
    assert check.level == "warning"  # never flips the exit code
    assert "host-daemon" in check.detail
    assert "nested amd64 verified" in check.detail


def test_the_probe_verdict_is_reported_even_when_it_is_negative(monkeypatch) -> None:
    """A host-daemon host that CANNOT do nested Rosetta is perfectly healthy — but the reason is
    exactly the data point the rollout needs, so it must reach the operator's screen."""
    monkeypatch.setattr(
        dr, "inspect_runtime", _inspect(RuntimeDecision("host-daemon", "pinned by setting"), BAD)
    )
    check = diag.container_runtime()
    assert check.ok is True
    assert "disabled" in check.detail


def test_pinned_dind_on_a_host_without_nested_rosetta_warns(monkeypatch) -> None:
    """The one genuinely broken configuration: it would otherwise surface as mission services
    that mysteriously fail to start, with nothing pointing at the setting that caused it."""
    monkeypatch.setattr(
        dr, "inspect_runtime", _inspect(RuntimeDecision("dind", "pinned by setting"), BAD)
    )
    check = diag.container_runtime()
    assert check.ok is False
    assert check.level == "warning"
    assert "XORCISE_MACOS_CONTAINER_RUNTIME=host-daemon" in check.remediation


def test_dind_with_a_working_probe_passes(monkeypatch) -> None:
    monkeypatch.setattr(
        dr, "inspect_runtime", _inspect(RuntimeDecision("dind", "nested amd64 verified"), OK)
    )
    assert diag.container_runtime().ok is True


def test_an_exploding_probe_does_not_break_doctor(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("docker socket vanished")

    monkeypatch.setattr(dr, "inspect_runtime", _boom)
    check = diag.container_runtime()
    assert check.ok is True
    assert check.level == "warning"
    assert "undetermined" in check.detail


def test_no_probe_reads_as_not_probed(monkeypatch) -> None:
    monkeypatch.setattr(
        dr, "inspect_runtime", _inspect(RuntimeDecision("dind", "not macOS"), None)
    )
    assert "probe: not run" in diag.container_runtime().detail


def test_a_long_runtime_error_keeps_its_tail(monkeypatch) -> None:
    """An OCI failure chain is ~300 chars whose meaning ("rosetta error: …") is at the very end;
    clipping the head would leave a line that says nothing."""
    chain = (
        "failed to create shim task: OCI runtime create failed: runc create failed: unable to "
        "start container process: error during container init: error running prestart hook #0: "
        "signal: trace/breakpoint trap, stdout: , stderr: rosetta error: failed to open elf"
    )
    monkeypatch.setattr(
        dr,
        "inspect_runtime",
        _inspect(RuntimeDecision("host-daemon", "pinned"), RosettaProbe(False, chain, "fp-a")),
    )
    detail = diag.container_runtime().detail
    assert "rosetta error: failed to open elf" in detail
    assert len(detail) < 220


def test_a_verdict_that_is_also_the_reason_is_not_printed_twice(monkeypatch) -> None:
    """Under `auto` the probe verdict IS why the mode was chosen; repeating it truncates away
    the half the reader needs."""
    monkeypatch.setattr(
        dr, "inspect_runtime", _inspect(RuntimeDecision("host-daemon", BAD.detail), BAD)
    )
    assert diag.container_runtime().detail.count(BAD.detail) == 1


def test_doctor_omits_the_check_off_macos(monkeypatch) -> None:
    """It starts a container, so it is confined to macOS AND to `doctor` — the check list `up`
    runs on every start must stay free of it."""
    from xorcise.core.cli.commands import lifecycle

    def _must_not_run():
        raise AssertionError("the container-runtime probe must not run from `up`'s check list")

    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr(lifecycle, "container_runtime", _must_not_run)
    names = {c.name for c in lifecycle._environment_checks()}
    assert "container runtime" not in names
