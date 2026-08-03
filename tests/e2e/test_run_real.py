"""E2E: real-by-default register → ingest → run → deploy → connect. Skip-guarded.

Runs on a provisioned single host:
  - a Docker daemon,
  - `docker build -t xorcise/mission-base containers/mission-base`,
  - a running Headscale container named per XORCISE_HEADSCALE_CONTAINER (default 'headscale').
Skips cleanly otherwise (CI/dev/unprovisioned), so the unit/adapters lanes stay green. This is
the standing live proof of the real-by-default loop (control + fence + pull all real)."""

from __future__ import annotations

import contextlib
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

if shutil.which("docker") is None:
    pytest.skip("docker not available", allow_module_level=True)


def _image_present(ref: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode == 0


def _headscale_up(container: str = "headscale") -> bool:
    return (
        subprocess.run(
            ["docker", "exec", container, "headscale", "version"], capture_output=True
        ).returncode
        == 0
    )


def _docker_sdk_present() -> bool:
    # Real-by-default deps need the `runner` extra (the docker SDK); without it the deploy fails
    # loud instead of stubbing, so guard on it to SKIP rather than error.
    return importlib.util.find_spec("docker") is not None


pytestmark = pytest.mark.skipif(
    not (_image_present("xorcise/mission-base") and _headscale_up() and _docker_sdk_present()),
    reason="needs xorcise/mission-base built + a running headscale container + the runner extra",
)


def _write_bundle(root: Path) -> Path:
    bundle = root / "bundle"
    (bundle / "services" / "web").mkdir(parents=True)
    (bundle / "docker-compose.yml").write_text("services:\n  web:\n    build: ./services/web\n")
    (bundle / "services" / "web" / "Dockerfile").write_text('FROM busybox\nCMD ["true"]\n')
    (bundle / "mission.json").write_text(
        '{"schema_version":"2.0",'
        '"metadata":{"mission_id":"e2e","name":"e2e","objective":"x","type":"lab"},'
        '"environment":{"compose_file":"docker-compose.yml"}}'
    )
    return bundle


def test_real_by_default_register_ingest_run_connect(
    migrated_home, monkeypatch, real_headscale_unshared
):
    from xorcise.core import agents
    from xorcise.core.config import get_settings
    from xorcise.core.rest.ingest import ingest_bundle
    from xorcise.core.rest.run_create import build_run_create_deps, create_run

    # real-by-default: NOT use_docker=True; the role + (absent) use_stubs derive real adapters.
    monkeypatch.delenv("XORCISE_USE_STUBS", raising=False)
    monkeypatch.setenv("XORCISE_ROLE", "all")
    get_settings.cache_clear()

    ingest_bundle(_write_bundle(Path(migrated_home)), get_settings())  # real FusedImageBuilder
    agents.register("alice", endpoint="http://a")
    deps = build_run_create_deps(get_settings())  # derived real control + fence + driver
    run, prompt = create_run(agent_name="alice", mission_slug="e2e", budget_seconds=300, deps=deps)
    try:
        assert run.mission == "e2e"
        assert prompt.join_key and prompt.run_control_url
        assert prompt.run_id == run.run_id
    finally:
        with contextlib.suppress(Exception):
            deps.control.teardown(run.run_id, credential=deps.api_key)
