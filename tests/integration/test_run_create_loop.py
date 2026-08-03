"""Integration: install (real fused build) → run create → connect prompt. Skip-guarded.

Authored to run on a Docker host with `xorcise/mission-base` pre-built
(`docker build -t xorcise/mission-base containers/mission-base`). Skips cleanly where
Docker or the base image is absent, so CI unit/adapters stay green.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess

import pytest

if shutil.which("docker") is None:
    pytest.skip("docker not available", allow_module_level=True)


def _base_image_present() -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", "xorcise/mission-base"], capture_output=True
        ).returncode
        == 0
    )


def _docker_sdk_present() -> bool:
    # The real run-create deps need the `runner` extra (the docker SDK). Without it the deploy
    # fails loud rather than stubbing, so guard on it to SKIP — not error.
    return importlib.util.find_spec("docker") is not None


def _headscale_running() -> bool:
    # create_run probes the control plane (_probe_headscale), so a running headscale is a
    # hard prerequisite — guard on it to SKIP, not error, when it's absent (e.g. before xorcise up).
    return (
        subprocess.run(
            ["docker", "exec", "headscale", "headscale", "version"], capture_output=True
        ).returncode
        == 0
    )


pytestmark = pytest.mark.skipif(
    not (_base_image_present() and _docker_sdk_present() and _headscale_running()),
    reason="needs xorcise/mission-base + the runner extra + a running headscale (xorcise up)",
)


def test_install_then_run_create_emits_prompt(migrated_home, real_headscale_unshared):
    from pathlib import Path

    from xorcise.core import agents
    from xorcise.core.config import get_settings
    from xorcise.core.missions import ingest
    from xorcise.core.rest.run_create import build_run_create_deps, create_run
    from xorcise.core.runner.docker.build import FusedImageBuilder

    # 1. author a tiny bundle and ingest it with the REAL builder (real local fused image)
    bundle = Path(migrated_home) / "bundle"
    (bundle / "services" / "web").mkdir(parents=True)
    (bundle / "docker-compose.yml").write_text("services:\n  web:\n    build: ./services/web\n")
    (bundle / "services" / "web" / "Dockerfile").write_text('FROM busybox\nCMD ["true"]\n')
    (bundle / "mission.json").write_text(
        '{"schema_version":"2.0",'
        '"metadata":{"mission_id":"itest","name":"itest","objective":"x","type":"lab"},'
        '"environment":{"compose_file":"docker-compose.yml"}}'
    )
    install_root = Path(get_settings().missions_root)
    ingest(bundle, builder=FusedImageBuilder(), install_root=install_root)

    # 2. register an agent, 3. create the run via the real deps, 4. assert a prompt
    agents.register("alice", endpoint="http://a")
    deps = build_run_create_deps(get_settings(), use_docker=True)
    run, prompt = create_run(
        agent_name="alice", mission_slug="itest", budget_seconds=300, deps=deps
    )
    assert run.mission == "itest"
    assert prompt.join_key
    assert prompt.run_id == run.run_id
