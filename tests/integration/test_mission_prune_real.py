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


_LINEAGE = "ai.xorcise.base.version=2"


def _build(ctx: Path, tag: str, *labels: str) -> str:
    """Build a unique image under *tag* and return its id."""
    ctx.mkdir()
    (ctx / "f").write_text(uuid.uuid4().hex)  # unique content ⇒ an image id no one else has
    label = "".join(f"LABEL {kv}\n" for kv in labels)
    (ctx / "Dockerfile").write_text(f"FROM scratch\nCOPY f /f\n{label}")
    subprocess.run(["docker", "build", "-q", "-t", tag, str(ctx)], check=True, capture_output=True)
    return _image_id(tag)


def _image_id(ref: str) -> str:
    out = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", ref],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


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
        _build(tmp_path / "free", tags["free"], _LINEAGE)
        _build(tmp_path / "busy", tags["busy"], _LINEAGE)
        _build(tmp_path / "canary", tags["canary"])
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


@pytest.fixture
def dangling(tmp_path: Path) -> Iterator[dict[str, str]]:
    """Two mission images that lost their tags, one still used by a container, plus the
    unlabelled images their tags moved to.

    The tag is moved with `docker tag`, not a second `docker build -t`: under the containerd
    image store a build over an existing tag deletes the old image outright instead of
    leaving it dangling."""
    run = uuid.uuid4().hex[:10]
    made: dict[str, str] = {}
    tags: list[str] = []
    container = f"xorcise-prunetest-{run}"
    try:
        for name in ("free", "busy"):
            slug = f"prunetest{run}{name}"
            tag, newer = f"xorcise/mission-{slug}:local", f"xorcise-prunetest-newer{run}{name}:1"
            tags += [tag, newer]
            made[name] = _build(tmp_path / name, tag, _LINEAGE, f"ai.xorcise.mission.id={slug}")
            made[f"{name}_slug"] = slug
            if name == "busy":
                subprocess.run(
                    ["docker", "create", "--name", container, tag, "/f"],
                    check=True,
                    capture_output=True,
                )
            made[f"{name}_newer"] = _build(tmp_path / f"{name}-newer", newer)
            subprocess.run(["docker", "tag", newer, tag], check=True, capture_output=True)
            made[f"{name}_tag"] = tag
        yield made
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        for tag in tags:
            subprocess.run(["docker", "rmi", tag], capture_output=True)
        for key in ("free", "busy"):
            if key in made:
                subprocess.run(["docker", "rmi", made[key]], capture_output=True)


def test_prune_removes_our_untagged_image_by_id(dangling: dict[str, str]) -> None:
    from xorcise.core.rest.mission_images import apply_prune, owned_slug, plan_prune
    from xorcise.core.runner.docker.driver import DockerSdkDriver

    driver = DockerSdkDriver()
    every_image = driver.list_images()
    by_id = {image.id: image for image in every_image}
    free, busy = by_id[dangling["free"]], by_id[dangling["busy"]]
    assert (free.tags, busy.tags) == ((), ())  # the daemon lists both, untagged
    assert owned_slug(free) == dangling["free_slug"]
    assert owned_slug(by_id[dangling["free_newer"]]) is None  # the tag moved to an unlabelled one
    assert dangling["busy"] in driver.container_image_ids()

    ours = {dangling["free_slug"], dangling["busy_slug"]}
    mine = [c for c in plan_prune(every_image, set(), set()) if c.slug in ours]
    assert sorted((c.image_id, c.tags) for c in mine) == sorted(
        [(dangling["free"], ()), (dangling["busy"], ())]
    )
    result = apply_prune(driver, mine)

    assert result.removed == (dangling["free"],)
    assert [(f.image_id, f.error) for f in result.failed] == [(dangling["busy"], "in use")]
    assert _present(dangling["free"]) is False
    assert _present(dangling["busy"]) is True
    assert _present(dangling["free_tag"]) is True  # the newer image under the moved tag
