"""E2E (skip-guarded): the local-Headscale provisioner stands up a healthy, correctly-wired
control plane and tears it down.

This is the lighter single-leg proof: it exercises provision.ensure_up / teardown directly (the
novel provisioning infra) on a real Docker host — that the rendered config.yaml + certs yield a
HEALTHY air-gapped Headscale, the orchestrator user is created, and the managed config block is
written then removed. That a run actually joins such a control plane is covered by
tests/e2e/test_run_isolation.py (which uses the same rendered config). Skips cleanly where Docker
/ openssl / the runner extra are absent, so the e2e lane stays green on CI/unprovisioned hosts."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tomllib

import pytest

if shutil.which("docker") is None or shutil.which("openssl") is None:
    pytest.skip("docker + openssl required", allow_module_level=True)


def _sdk_present() -> bool:
    return importlib.util.find_spec("docker") is not None


pytestmark = pytest.mark.skipif(not _sdk_present(), reason="needs the runner extra (docker SDK)")


def _healthy() -> bool:
    from xorcise.core.headscale import provision

    return (
        subprocess.run(
            ["docker", "exec", provision.CONTAINER, "headscale", "health"], capture_output=True
        ).returncode
        == 0
    )


def test_up_provisions_then_teardown_removes(tmp_path):
    from xorcise.core.headscale import provision

    wd = tmp_path / "headscale"
    conf = tmp_path / "config.toml"
    try:
        res = provision.ensure_up(wd, conf)  # real: certs + compose up + health + user + block
        assert provision.is_owned(wd)
        assert res.url.startswith("https://") and res.advertise_host
        assert _healthy()
        # the orchestrator user was created on the provisioned control plane
        users = subprocess.run(
            ["docker", "exec", provision.CONTAINER, "headscale", "users", "list", "-o", "json"],
            capture_output=True,
            text=True,
        ).stdout
        assert "orchestrator" in users
        # the managed block was written with the wiring the server reads
        data = tomllib.loads(conf.read_text())
        assert data["headscale_url"] == res.url
        assert data["headscale_advertise_host"] == res.advertise_host
        assert data["headscale_ca_cert"] == res.ca_cert
    finally:
        torn = provision.teardown(wd, conf)
    assert torn is True
    assert not _healthy()  # container gone
    assert "headscale_url" not in conf.read_text()  # block stripped
    assert not provision.is_owned(wd)
