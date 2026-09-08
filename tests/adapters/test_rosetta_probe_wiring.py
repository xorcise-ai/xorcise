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

    def run(self, image: str, **kwargs: Any) -> Any:
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

    def remove(self, force=False, v=False):
        self.removed = True
        self.removed_volumes = v


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


AMD64 = "linux/amd64"


def test_verify_nested_accepts_an_x86_64_child_for_an_amd64_platform() -> None:
    client, container = _nested_client(b"some noise\nXORCISE_NESTED_ARCH=x86_64\n")
    probe = rosetta.verify_nested(client, platform=AMD64)
    assert probe.ok
    assert client.containers.kwargs["platform"] == "linux/amd64"
    assert client.containers.kwargs["privileged"] is True
    assert client.containers.kwargs["detach"] is True
    assert container.removed  # never strand a privileged DinD
    assert container.removed_volumes  # …nor the anonymous /var/lib/docker volume dind declares


def test_verify_nested_runs_the_wrapper_at_the_platform_it_is_asked_about() -> None:
    """The #79 fix. The wrapper used to be hard-pinned to linux/amd64, so an arm64 Linux host
    with no x86 emulation could not exec it and was refused EVERY mission — including the native
    arm64 ones it runs perfectly. The wrapper and the inner child now follow the platform of the
    mission under test."""
    client, _ = _nested_client(b"XORCISE_OUTER_ARCH=aarch64\nXORCISE_NESTED_ARCH=aarch64\n")
    probe = rosetta.verify_nested(client, platform="linux/arm64")
    assert probe.ok
    assert client.containers.kwargs["platform"] == "linux/arm64"
    assert "--platform linux/arm64" in client.containers.kwargs["command"][-1]
    assert "linux/arm64" in probe.detail


def test_verify_nested_native_pins_nothing_and_trusts_the_wrapper_arch() -> None:
    """No platform ⇒ the daemon's native one: no `--platform` anywhere (docker's default), and
    the child is right when it matches the wrapper it runs inside — whatever that is."""
    client, _ = _nested_client(b"XORCISE_OUTER_ARCH=aarch64\nXORCISE_NESTED_ARCH=aarch64\n")
    probe = rosetta.verify_nested(client)
    assert probe.ok
    assert client.containers.kwargs["platform"] is None
    assert "--platform" not in client.containers.kwargs["command"][-1]
    assert "linux/arm64" in probe.detail  # named from the arch actually observed


def test_verify_nested_normalises_platform_spellings() -> None:
    client, _ = _nested_client(b"XORCISE_NESTED_ARCH=aarch64\n")
    assert rosetta.verify_nested(client, platform="linux/arm64/v8").ok
    assert client.containers.kwargs["platform"] == "linux/arm64"


def test_verify_nested_rejects_a_child_of_the_wrong_arch() -> None:
    client, _ = _nested_client(b"XORCISE_NESTED_ARCH=aarch64\n")
    probe = rosetta.verify_nested(client, platform=AMD64)
    assert probe.ok is False
    assert "aarch64" in probe.detail
    assert "x86_64" in probe.detail


def test_a_failed_child_is_reported_with_the_runtime_error() -> None:
    """The load-bearing diagnostic. A healthy inner daemon whose amd64 child still dies is the
    real-world failure on the base XORCISE actually ships (`rosetta error: failed to open elf`),
    and the cause exists ONLY in the runtime's stderr — an "unavailable" with no reason sends
    the reader looking at Rosetta settings that are perfectly fine."""
    client, _ = _nested_client(
        b"XORCISE_NESTED_ARCH=\nXORCISE_NESTED_ERR=rosetta error: failed to open elf\n"
    )
    probe = rosetta.verify_nested(client, platform=AMD64)
    assert probe.ok is False
    assert "rosetta error: failed to open elf" in probe.detail


def test_a_failed_child_with_no_stderr_still_says_so() -> None:
    client, _ = _nested_client(b"XORCISE_NESTED_ARCH=\nXORCISE_NESTED_ERR=\n")
    assert "no error output" in rosetta.verify_nested(client, platform=AMD64).detail


def test_a_daemon_that_never_started_is_distinguished_from_a_failed_child() -> None:
    """Different causes, different fixes: nothing to do with Rosetta, so it must not be reported
    as a Rosetta verdict. Reaching the sentinel at all is what proves the daemon came up."""
    client, _ = _nested_client(b"dockerd: exiting\n", status=1)
    probe = rosetta.verify_nested(client, platform=AMD64)
    assert probe.ok is False
    assert "inner daemon never came up" in probe.detail


def test_a_wrapper_that_could_not_exec_reports_the_exec_error() -> None:
    """The reporter's failure (#79): a foreign-platform wrapper with no binfmt handler dies
    before its script runs, with `exec format error` and status 255. That text was read and
    discarded, leaving "inner daemon never came up (exit 255)" — true, and useless. The
    output tail is the diagnosis, so it rides along."""
    client, _ = _nested_client(
        b"exec /usr/local/bin/dockerd-entrypoint.sh: exec format error\n", status=255
    )
    probe = rosetta.verify_nested(client, platform=AMD64)
    assert probe.ok is False
    assert "exit 255" in probe.detail
    assert "exec format error" in probe.detail


def test_a_daemon_that_died_hands_out_its_own_log_tail() -> None:
    """Under qemu the inner dockerd starts and dies at iptables init; only its log says so."""
    client, _ = _nested_client(
        b"XORCISE_OUTER_ARCH=x86_64\n"
        b"XORCISE_DAEMON_LOG=... iptables: Failed to initialize nft: Protocol not supported\n",
        status=1,
    )
    probe = rosetta.verify_nested(client, platform=AMD64)
    assert probe.ok is False
    assert "Failed to initialize nft" in probe.detail


def test_verify_nested_removes_the_container_even_when_wait_raises() -> None:
    container = _NestedContainer(b"")

    def _boom(timeout=None):
        raise TimeoutError("read timed out")

    container.wait = _boom  # type: ignore[method-assign]
    client = _Client(_Containers(container))
    probe = rosetta.verify_nested(client, platform=AMD64)
    assert probe.ok is False
    assert container.removed


def test_verify_nested_reads_the_output_even_when_wait_times_out() -> None:
    """The 180 s ceiling used to surface as a bare `ConnectionError: Read timed out` with every
    line the container printed thrown away. The container is still there at that point; what it
    said so far is the only diagnosis there will be."""
    container = _NestedContainer(
        b'XORCISE_OUTER_ARCH=x86_64\nlevel=warning msg="iptables: Protocol not supported"\n'
    )

    def _boom(timeout=None):
        raise TimeoutError("read timed out")

    container.wait = _boom  # type: ignore[method-assign]
    client = _Client(_Containers(container))
    probe = rosetta.verify_nested(client, platform=AMD64, timeout=7)
    assert probe.ok is False
    assert "did not finish within 7s" in probe.detail
    assert "Protocol not supported" in probe.detail
    assert container.removed


def test_verify_nested_honours_an_explicit_image() -> None:
    """Callers point Tier 2 at the fused mission image — the exact artifact the decision is
    about, and already local — instead of pulling a generic dind."""
    client, _ = _nested_client(b"XORCISE_NESTED_ARCH=x86_64\n")
    rosetta.verify_nested(client, platform=AMD64, image="xorcise/fused-breachpoint:1")
    assert client.containers.kwargs["image"] == "xorcise/fused-breachpoint:1"


def test_a_wrong_platform_local_tag_is_re_pulled_for_the_wanted_platform() -> None:
    """Docker holds ONE image per tag under overlay2, and docker-py only re-pulls on a MISSING
    image. A local `docker:29.7.1-dind` of the other platform therefore 404s the create with
    "platform … does not match", which read as "this host cannot nest". Pull the right one and
    retry once, instead."""
    container = _NestedContainer(b"XORCISE_NESTED_ARCH=aarch64\n")
    attempts: list[str | None] = []
    pulled: list[tuple[str, str | None]] = []

    class _Once(_Containers):
        def run(self, image: str, **kwargs: Any) -> Any:
            attempts.append(kwargs.get("platform"))
            if len(attempts) == 1:
                raise RuntimeError(
                    "404 Client Error: image with reference docker:29.7.1-dind was found but "
                    "its platform (linux/amd64) does not match the specified platform (linux/arm64)"
                )
            return super().run(image, **kwargs)

    class _Images:
        def pull(self, image, platform=None, **_kw):
            pulled.append((image, platform))

    client = _Client(_Once(container))
    client.images = _Images()  # type: ignore[attr-defined]
    probe = rosetta.verify_nested(client, platform="linux/arm64")
    assert probe.ok
    assert pulled == [("docker:29.7.1-dind", "linux/arm64")]
    assert attempts == ["linux/arm64", "linux/arm64"]


def test_other_create_failures_are_not_retried() -> None:
    client = _Client(_Containers(raises=RuntimeError("permission denied")))
    probe = rosetta.verify_nested(client, platform="linux/arm64")
    assert probe.ok is False
    assert "permission denied" in probe.detail
