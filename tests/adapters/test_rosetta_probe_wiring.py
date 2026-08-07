"""What the Rosetta probes hand docker-py, and how they behave when it misbehaves.

Daemon-free: both tiers take an injected client. The load-bearing property under test is that
EVERY failure mode fails CLOSED — an unknown host must take the proven sibling path, so a probe
that raised, or that returned something unparseable, can never be read as "nested Rosetta works".
"""

from __future__ import annotations

from typing import Any

import pytest

from xorcise.core.runner.docker import rosetta

pytestmark = pytest.mark.adapters

GOOD_REG = (
    "enabled\ninterpreter /run/rosetta/rosetta\nflags: POCF\noffset 0\n"
    "magic 7f454c4602010100000000000000000002003e00\n"
)


class _Containers:
    def __init__(self, result: Any = b"", *, raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.kwargs: dict[str, Any] = {}
        self.removed = 0

    def run(self, image, **kwargs):
        self.kwargs = {"image": image, **kwargs}
        if self.raises is not None:
            raise self.raises
        return self.result


class _Client:
    def __init__(self, containers: _Containers, version: str = "29.6.2") -> None:
        self.containers = containers
        self._version = version

    def version(self):
        return {"Version": self._version}


class _NestedContainer:
    """A detached container whose logs the probe reads after wait()."""

    def __init__(self, logs: bytes, status: int = 0) -> None:
        self._logs = logs
        self._status = status
        self.removed = False

    def wait(self, timeout=None):
        return {"StatusCode": self._status}

    def logs(self):
        return self._logs

    def remove(self, force=False):
        self.removed = True


# ------------------------------------------------------------------------ tier 1 wiring


def test_binfmt_signal_reads_the_registration_privileged_on_the_host_arch() -> None:
    containers = _Containers(GOOD_REG.encode())
    probe = rosetta.binfmt_signal(_Client(containers))
    assert probe.ok
    # privileged is required to mount binfmt_misc; platform must stay None so that READING the
    # registration does not itself depend on the thing being measured.
    assert containers.kwargs["privileged"] is True
    assert containers.kwargs["platform"] is None
    assert containers.kwargs["remove"] is True


def test_binfmt_signal_stamps_a_fingerprint() -> None:
    probe = rosetta.binfmt_signal(_Client(_Containers(GOOD_REG.encode())))
    assert probe.fingerprint
    assert probe.fingerprint == rosetta.fingerprint(
        docker_version="29.6.2",
        macos_version=__import__("platform").mac_ver()[0],
        binfmt_raw=GOOD_REG,
    )


def test_binfmt_signal_fingerprint_tracks_the_docker_version() -> None:
    a = rosetta.binfmt_signal(_Client(_Containers(GOOD_REG.encode()), version="29.6.2"))
    b = rosetta.binfmt_signal(_Client(_Containers(GOOD_REG.encode()), version="30.1.0"))
    assert a.fingerprint != b.fingerprint


def test_binfmt_signal_fails_closed_when_the_container_cannot_run() -> None:
    probe = rosetta.binfmt_signal(_Client(_Containers(raises=RuntimeError("no such image"))))
    assert probe.ok is False
    assert "binfmt probe failed" in probe.detail


def test_binfmt_signal_survives_a_client_without_a_version() -> None:
    """A missing version only weakens the cache key — it must not turn into a probe failure."""

    class _NoVersion(_Client):
        def version(self):
            raise RuntimeError("nope")

    probe = rosetta.binfmt_signal(_NoVersion(_Containers(GOOD_REG.encode())))
    assert probe.ok is True
    assert probe.fingerprint


def test_binfmt_signal_accepts_str_output() -> None:
    assert rosetta.binfmt_signal(_Client(_Containers(GOOD_REG))).ok is True


# ------------------------------------------------------------------------ tier 2 wiring


def _nested_client(logs: bytes, status: int = 0) -> tuple[_Client, _NestedContainer]:
    container = _NestedContainer(logs, status)
    return _Client(_Containers(container)), container


def test_verify_nested_amd64_accepts_an_x86_64_child() -> None:
    client, container = _nested_client(b"some noise\nXORCISE_NESTED_ARCH=x86_64\n")
    probe = rosetta.verify_nested_amd64(client)
    assert probe.ok
    assert client.containers.kwargs["platform"] == "linux/amd64"
    assert client.containers.kwargs["privileged"] is True
    assert client.containers.kwargs["detach"] is True
    assert container.removed  # never strand a privileged DinD


def test_verify_nested_amd64_rejects_a_non_x86_child() -> None:
    client, _ = _nested_client(b"XORCISE_NESTED_ARCH=aarch64\n")
    probe = rosetta.verify_nested_amd64(client)
    assert probe.ok is False
    assert "aarch64" in probe.detail


def test_a_failed_child_is_reported_with_the_runtime_error() -> None:
    """The load-bearing diagnostic. A healthy inner daemon whose amd64 child still dies is the
    real-world failure on the base XORCISE actually ships (`rosetta error: failed to open elf`),
    and the cause exists ONLY in the runtime's stderr — an "unavailable" with no reason sends
    the reader looking at Rosetta settings that are perfectly fine."""
    client, _ = _nested_client(
        b"XORCISE_NESTED_ARCH=\nXORCISE_NESTED_ERR=rosetta error: failed to open elf\n"
    )
    probe = rosetta.verify_nested_amd64(client)
    assert probe.ok is False
    assert "rosetta error: failed to open elf" in probe.detail


def test_a_failed_child_with_no_stderr_still_says_so() -> None:
    client, _ = _nested_client(b"XORCISE_NESTED_ARCH=\nXORCISE_NESTED_ERR=\n")
    assert "no error output" in rosetta.verify_nested_amd64(client).detail


def test_a_daemon_that_never_started_is_distinguished_from_a_failed_child() -> None:
    """Different causes, different fixes: nothing to do with Rosetta, so it must not be reported
    as a Rosetta verdict. Reaching the sentinel at all is what proves the daemon came up."""
    client, _ = _nested_client(b"dockerd: exiting\n", status=1)
    probe = rosetta.verify_nested_amd64(client)
    assert probe.ok is False
    assert "inner daemon never came up" in probe.detail


def test_verify_nested_amd64_removes_the_container_even_when_wait_raises() -> None:
    container = _NestedContainer(b"")

    def _boom(timeout=None):
        raise TimeoutError("read timed out")

    container.wait = _boom  # type: ignore[method-assign]
    client = _Client(_Containers(container))
    probe = rosetta.verify_nested_amd64(client)
    assert probe.ok is False
    assert container.removed


def test_verify_nested_amd64_honours_an_explicit_image() -> None:
    """Callers point Tier 2 at the fused mission image — the exact artifact the decision is
    about, and already local — instead of pulling a generic dind."""
    client, _ = _nested_client(b"XORCISE_NESTED_ARCH=x86_64\n")
    rosetta.verify_nested_amd64(client, image="xorcise/fused-breachpoint:1")
    assert client.containers.kwargs["image"] == "xorcise/fused-breachpoint:1"
