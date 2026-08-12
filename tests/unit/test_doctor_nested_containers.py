"""`doctor`'s nested-container check.

This is now a BLOCKER, not an advisory line: with the host-daemon fallback removed, a host that
cannot nest containers cannot run a lab mission at all. So the properties that matter are that it
fails the verdict when nesting is genuinely unavailable, that it carries the fix, and that it
still does NOT fail when the probe itself is what broke — those are different claims.
"""

from __future__ import annotations

from xorcise.core.cli import _diagnostics as diag
from xorcise.core.rest import docker_runtime as dr
from xorcise.core.runner.docker.rosetta import NestedSupport

OK = NestedSupport(True, "nested amd64 verified (x86_64 inside an amd64 DinD)", "fp-a")
BAD = NestedSupport(False, "nested amd64 container failed to start", "fp-a", "enable Rosetta …")


def _support(value: NestedSupport):
    return lambda *_a, **_k: value


def test_supported_host_passes(monkeypatch) -> None:
    monkeypatch.setattr(dr, "nested_support", _support(OK))
    check = diag.nested_containers()
    assert check.ok is True
    assert check.level == "blocker"
    assert "nested amd64 verified" in check.detail


def test_unsupported_host_fails_the_verdict(monkeypatch) -> None:
    """The whole point of promoting this from a warning: `doctor` must exit non-zero on a host
    where every lab run is going to be refused."""
    monkeypatch.setattr(dr, "nested_support", _support(BAD))
    check = diag.nested_containers()
    assert check.ok is False
    assert check.level == "blocker"
    assert check.remediation == "enable Rosetta …"


def test_a_broken_probe_is_a_warning_not_a_failure(monkeypatch) -> None:
    """ "The probe blew up" and "this host cannot nest" are different claims. Only the second
    should fail the verdict — otherwise an unrelated Docker hiccup makes `doctor` exit 1 and
    accuse the host of something it was never shown to be guilty of."""

    def _boom(*_a, **_k):
        raise RuntimeError("docker socket vanished")

    monkeypatch.setattr(dr, "nested_support", _boom)
    check = diag.nested_containers()
    assert check.ok is True
    assert check.level == "warning"
    assert "undetermined" in check.detail


def test_a_long_runtime_error_keeps_its_tail(monkeypatch) -> None:
    """An OCI failure chain is ~300 chars whose meaning ("rosetta error: …") is at the very end;
    clipping the head would leave a line that says nothing."""
    chain = (
        "failed to create shim task: OCI runtime create failed: runc create failed: unable to "
        "start container process: error during container init: error running prestart hook #0: "
        "signal: trace/breakpoint trap, stdout: , stderr: rosetta error: failed to open elf"
    )
    monkeypatch.setattr(dr, "nested_support", _support(NestedSupport(False, chain, "fp-a", "fix")))
    detail = diag.nested_containers().detail
    assert "rosetta error: failed to open elf" in detail
    assert len(detail) < 220


def test_doctor_runs_the_nested_probe_only_when_docker_is_up(monkeypatch) -> None:
    """The gate in lifecycle.doctor: the privileged nested probe is appended ONLY when the docker
    env checks pass. Invokes the REAL command (catch_exceptions=False, a recording stub) — the
    previous version asserted against its own stub and never called doctor, so an inverted gate
    would still have passed."""
    from typer.testing import CliRunner

    from xorcise.core.cli._shared import app
    from xorcise.core.cli.commands import lifecycle

    monkeypatch.delenv("XORCISE_USE_STUBS", raising=False)  # isolate the DOCKER gate from stubs
    monkeypatch.setenv("XORCISE_ROLE", "all")
    probed: list[int] = []

    def _record() -> diag.Check:
        probed.append(1)
        return diag.Check("nested containers", True, "ok")

    monkeypatch.setattr(lifecycle, "nested_containers", _record)

    monkeypatch.setattr(
        lifecycle,
        "_environment_checks",
        lambda: [diag.Check("docker", False, "unreachable", "start Docker")],
    )
    CliRunner().invoke(app, ["doctor"], catch_exceptions=False)
    assert probed == []  # docker down → gate keeps the privileged probe out

    monkeypatch.setattr(
        lifecycle, "_environment_checks", lambda: [diag.Check("docker", True, "reachable")]
    )
    CliRunner().invoke(app, ["doctor"], catch_exceptions=False)
    assert probed == [1]  # docker up → probe runs


def test_stub_mode_skips_the_nested_probe(monkeypatch) -> None:
    """Stub mode deploys nothing, so it has no nesting precondition — and probing anyway would
    start a real privileged container. Uses a RECORDING stub + catch_exceptions=False, so a probe
    that slipped through is a hard failure rather than one CliRunner silently swallows."""
    from typer.testing import CliRunner

    from xorcise.core.cli._shared import app
    from xorcise.core.cli.commands import lifecycle

    probed: list[int] = []

    def _record() -> diag.Check:
        probed.append(1)
        return diag.Check("nested containers", True, "ok")

    monkeypatch.setattr(lifecycle, "nested_containers", _record)
    # docker UP, but stubs are the suite default: the gate's `not s.use_stubs` must still skip.
    monkeypatch.setattr(
        lifecycle, "_environment_checks", lambda: [diag.Check("docker", True, "reachable")]
    )
    CliRunner().invoke(app, ["doctor"], catch_exceptions=False)
    assert probed == []


def test_up_never_runs_the_nested_probe(monkeypatch) -> None:
    """`up` must work on a host that cannot nest: static missions, the UI and past runs need
    nothing nested, so a Rosetta-less Mac must still be able to start XORCISE."""
    from xorcise.core.cli.commands import lifecycle

    monkeypatch.setattr(
        lifecycle,
        "nested_containers",
        lambda: (_ for _ in ()).throw(AssertionError("`up` must not probe")),
    )
    names = {c.name for c in lifecycle._environment_checks()}
    assert "nested containers" not in names
