"""Integration: the real fused-image build. Skips cleanly without Docker + the base image.

NOTE: authored to run on a Docker host; requires `xorcise/mission-base` to be built
first (`docker build -t xorcise/mission-base containers/mission-base`). It is
skip-guarded so CI unit/adapters lanes stay green where Docker is absent.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

if shutil.which("docker") is None:
    pytest.skip("docker not available", allow_module_level=True)


def _base_image_present() -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", "xorcise/mission-base"],
            capture_output=True,
        ).returncode
        == 0
    )


@pytest.mark.skipif(not _base_image_present(), reason="xorcise/mission-base not built")
def test_builds_a_local_fused_image(tmp_path):
    from xorcise.core.contracts.mission import (
        EnvironmentSpec,
        MissionManifest,
        MissionMetadata,
    )
    from xorcise.core.runner.docker.build import FusedImageBuilder, fused_tag

    bundle = tmp_path / "bundle"
    (bundle / "services" / "web").mkdir(parents=True)
    (bundle / "docker-compose.yml").write_text("services:\n  web:\n    build: ./services/web\n")
    (bundle / "services" / "web" / "Dockerfile").write_text('FROM busybox\nCMD ["true"]\n')
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id="itest", name="itest", objective="x", type="lab"),
        environment=EnvironmentSpec(),
    )
    ref = FusedImageBuilder().build(bundle, manifest)
    assert ref.image == fused_tag("itest")  # stable per-mission tag
    inspect = subprocess.run(["docker", "image", "inspect", ref.image], capture_output=True)
    assert inspect.returncode == 0
