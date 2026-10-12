"""Integration: prune against the real Docker daemon, touching only images this test builds.

A dev box can hold real orphaned mission images, so the plan is filtered to this test's own
random slugs before anything is removed. Every image here is built from scratch with random
content, so its id is unique to this run, and cleanup removes only those tags.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

if shutil.which("docker") is None:
    pytest.skip("docker not available", allow_module_level=True)


def _daemon_up() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _daemon_up(), reason="docker daemon not reachable")


def _build(ctx: Path, tag: str, *, lineage: bool) -> None:
    ctx.mkdir()
    (ctx / "f").write_text(uuid.uuid4().hex)  # unique content ⇒ an image id no one else has
    label = "LABEL ai.xorcise.base.version=2\n" if lineage else ""
    (ctx / "Dockerfile").write_text(f"FROM scratch\nCOPY f /f\n{label}")
    subprocess.run(["docker", "build", "-q", "-t", tag, str(ctx)], check=True, capture_output=True)


@pytest.fixture
def images(tmp_path: Path) -> Iterator[dict[str, str]]:
    run = uuid.uuid4().hex[:10]
    tags = {
        "free": f"xorcise/mission-prunetest{run}a:local",
        "busy": f"xorcise/mission-prunetest{run}b:local",
        "canary": f"xorcise-prune-canary{run}:1",
    }
    container = f"xorcise-prunetest-{run}"
    try:
        _build(tmp_path / "free", tags["free"], lineage=True)
        _build(tmp_path / "busy", tags["busy"], lineage=True)
        _build(tmp_path / "canary", tags["canary"], lineage=False)
        # A created, never-started container pins its image exactly like a running one.
        subprocess.run(
            ["docker", "create", "--name", container, tags["busy"], "/f"],
            check=True,
            capture_output=True,
        )
        yield tags
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        for tag in tags.values():
            subprocess.run(["docker", "rmi", tag], capture_output=True)


def _present(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


def test_prune_removes_only_our_unused_image(images: dict[str, str]) -> None:
    from xorcise.core.rest.mission_images import apply_prune, owned_slug, plan_prune
    from xorcise.core.runner.docker.driver import DockerSdkDriver

    driver = DockerSdkDriver()
    every_image = driver.list_images()
    listed = {tag: image for image in every_image for tag in image.tags}
    assert owned_slug(listed[images["canary"]]) is None  # no lineage label: never ours
    ours = {owned_slug(listed[images["free"]]), owned_slug(listed[images["busy"]])}
    busy_id = listed[images["busy"]].id
    assert busy_id in driver.container_image_ids()

    # Protection is deliberately left empty so the daemon's own refusal is what keeps "busy".
    mine = [c for c in plan_prune(every_image, set(), set()) if c.slug in ours]
    assert sorted(c.tags[0] for c in mine) == sorted([images["free"], images["busy"]])
    result = apply_prune(driver, mine)

    assert result.removed == (listed[images["free"]].id,)
    assert [(f.image_id, f.error) for f in result.failed] == [(busy_id, "in use")]
    assert _present(images["free"]) is False
    assert _present(images["busy"]) is True
    assert _present(images["canary"]) is True
