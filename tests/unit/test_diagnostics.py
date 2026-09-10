import shutil
import subprocess

import httpx

from xorcise.core.cli import _diagnostics as diag


class _Completed:
    def __init__(self, returncode: int):
        self.returncode = returncode


def test_docker_daemon_missing_binary(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    c = diag.docker_daemon()
    assert c.ok is False
    assert c.detail == "missing"
    assert "install Docker" in c.remediation


def test_docker_daemon_ok(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(0))
    assert diag.docker_daemon().ok is True


def test_docker_daemon_unreachable_on_nonzero_returncode(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(1))
    c = diag.docker_daemon()
    assert c.ok is False
    assert c.detail == "unreachable"
    assert "start Docker" in c.remediation


def test_docker_daemon_unreachable_on_subprocess_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")

    def boom(*a, **k):
        raise OSError("no docker socket")

    monkeypatch.setattr(subprocess, "run", boom)
    c = diag.docker_daemon()
    assert c.ok is False
    assert c.detail == "unreachable"


def test_home_present_true_for_existing_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    assert diag.home_present().ok is True


def test_probe_channel_down_on_wrong_status(monkeypatch):
    class Resp:
        status_code = 500

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    c = diag.probe_channel("rest", "http://x")
    assert c.ok is False
    assert c.detail == "down"


def test_docker_present_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    c = diag.docker_present()
    assert c.ok is False
    assert c.name == "docker"
    assert "install Docker" in c.remediation


def test_docker_present_found(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    assert diag.docker_present().ok is True


def test_home_present_false_for_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path / "nope"))
    c = diag.home_present()
    assert c.ok is False
    assert "xorcise up" in c.remediation  # bootstrap is on `up`, not the removed `init`


def test_probe_channel_down_on_connect_error(monkeypatch):
    def refused(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refused)
    c = diag.probe_channel("rest", "http://127.0.0.1:3001/api/health")
    assert c.ok is False
    assert c.detail == "down"


def test_probe_channel_never_uses_the_proxy_environment(monkeypatch):
    """With HTTP_PROXY set, `status` and `ui` reported a healthy server as stopped: httpx routes a
    loopback GET through the proxy (no implicit loopback bypass), the proxy fails, and the probe
    reads "down". The loopback plane probe must not consult the environment at all."""
    seen: list[dict[str, object]] = []

    class Resp:
        status_code = 200

    def _get(url, **kwargs):
        seen.append(kwargs)
        return Resp()

    monkeypatch.setattr(httpx, "get", _get)
    assert diag.probe_channel("rest", "http://x/api/health").ok is True
    assert seen and seen[0].get("trust_env") is False


def test_probe_channel_ok(monkeypatch):
    class Resp:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    assert diag.probe_channel("rest", "http://x").ok is True


# --- Control plane -------------------------------------------------------------
# The gap that let `doctor` print "No problems found" while EVERY run creation
# failed with a 503: run creation depends on the Headscale control plane, and
# nothing in doctor ever looked at it.


def test_control_plane_ok(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(0))
    c = diag.control_plane()
    assert c.ok is True
    assert c.name == "control plane"


def test_control_plane_unreachable_points_at_up(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(1))
    c = diag.control_plane()
    assert c.ok is False
    assert "xorcise up" in c.remediation
    # The stale-remote case is the one that wedges a working install, so the fix
    # must name it — clearing the saved URL is not discoverable otherwise.
    assert "--headscale-url" in c.remediation


def test_control_plane_probes_the_configured_container(monkeypatch):
    """It must probe EXACTLY what run creation probes (docker exec <container>
    headscale version) — a URL probe would pass while run creation still 503s."""
    seen: list[list[str]] = []

    def _run(cmd, *a, **k):
        seen.append(list(cmd))
        return _Completed(0)

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _run)
    diag.control_plane("hs-custom")
    assert seen[0][:5] == ["docker", "exec", "hs-custom", "headscale", "version"]


# --- Control plane ADDRESS -----------------------------------------------------
# #62: the container check above verifies the container, not the address routers dial.
# When the host's address changes after `up` (a laptop moving networks), the container
# stays healthy, nothing answers at the recorded URL, every run dies at the readiness
# window — and doctor said "No problems found".


def test_control_plane_address_any_http_reply_counts_as_answering(monkeypatch):
    class Resp:
        status_code = 404  # Headscale answers / with a 404 — "something is listening"

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    c = diag.control_plane_address("https://172.17.0.1:443", current_ip="172.17.0.1")
    assert c.ok is True
    assert "https://172.17.0.1:443" in c.detail


def test_control_plane_address_unreachable_names_the_moved_host_and_the_fix(monkeypatch):
    def refused(*a, **k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "get", refused)
    c = diag.control_plane_address("https://192.168.0.5:443", current_ip="192.168.1.20")
    assert c.ok is False
    assert "https://192.168.0.5:443" in c.detail and "not answering" in c.detail
    # The diagnosis itself: the address `up` recorded is not the host's address any more.
    assert "now 192.168.1.20" in c.detail and "not 192.168.0.5" in c.detail
    assert "xorcise down && xorcise up" in c.remediation


def test_control_plane_address_unreachable_without_a_current_ip_stays_factual(monkeypatch):
    def refused(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refused)
    c = diag.control_plane_address("https://192.168.0.5:443", current_ip=None)
    assert c.ok is False
    assert "now" not in c.detail  # no claim about the current address when it is unknown
    same = diag.control_plane_address("https://192.168.0.5:443", current_ip="192.168.0.5")
    assert "now" not in same.detail  # the address did not move — say only that it is silent


def test_control_plane_address_dials_directly(monkeypatch):
    """A host-local address the routers dial straight from their containers: no TLS verification
    (self-signed local CA) and no proxy environment (see #86 for what that does to a probe)."""
    seen: list[dict[str, object]] = []

    class Resp:
        status_code = 404

    def _get(url, **kwargs):
        seen.append(kwargs)
        return Resp()

    monkeypatch.setattr(httpx, "get", _get)
    diag.control_plane_address("https://172.17.0.1:443")
    assert seen[0].get("verify") is False
    assert seen[0].get("trust_env") is False


def test_external_control_plane_unreachable(monkeypatch):
    def refused(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refused)
    c = diag.external_control_plane("https://172.17.0.1:443")
    assert c.ok is False
    assert "172.17.0.1" in c.detail


def test_external_control_plane_any_http_response_counts_as_reachable(monkeypatch):
    """Headscale answers / with a 404 — 'something is listening' is the question."""

    class Resp:
        status_code = 404

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Resp())
    assert diag.external_control_plane("https://x:443").ok is True
