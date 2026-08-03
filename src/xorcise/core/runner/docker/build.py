"""Runner-side fused per-mission image builder. Runner-only (Docker).

Satisfies the missions.BundleBuilder structural Protocol WITHOUT importing it — both
sides speak only the contracts DTOs (MissionManifest in, MissionRef out). Builds a
single self-contained image FROM xorcise/mission-base with the mission's inner stack
baked in as /mission/images.tar (loaded on boot — no inner pull at deploy). Local build
only: tags xorcise/mission-<slug>:<version>, no registry push.
Lifts the PoC's ensure_images/docker save.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import yaml

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.runner.netoverride import ROUTER_IMAGE


@dataclass(frozen=True)
class BuildSpec:
    service: str
    image: str | None = None
    build_context: str | None = None


BASE_IMAGE = "xorcise/mission-base"

# The fused image is tagged with a STABLE per-mission tag, NOT the setuptools-scm
# code version: pinning the tag to the code version changed it on every commit, so a mission
# ingested at one commit recorded an image ref that a later commit (and a prune) stranded —
# `run create` then tried to pull a local-only image and failed cryptically. A stable tag means
# the install record stays valid across code commits; re-ingest overwrites it in place.
FUSED_TAG = "local"


def fused_tag(slug: str) -> str:
    """The local fused-image tag for a mission (stable per-mission, no registry)."""
    return f"xorcise/mission-{slug}:{FUSED_TAG}"


def _image_present(ref: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", ref], capture_output=True).returncode == 0


def _base_context() -> Path:
    """Locate the mission-base build context (Dockerfile + entrypoint.sh).

    Built wheel: shipped as package data under the runner.docker package (force-include).
    Editable/dev checkout: the canonical containers/mission-base at the repo root.
    """
    packaged = Path(str(files("xorcise.core.runner.docker").joinpath("_mission_base")))
    if (packaged / "Dockerfile").is_file():
        return packaged
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "containers" / "mission-base"
        if (candidate / "Dockerfile").is_file():
            return candidate
    raise RuntimeError(
        "mission-base build context not found (neither packaged as "
        "xorcise/core/runner/docker/_mission_base nor containers/mission-base)"
    )


def ensure_base_image() -> None:
    """Build xorcise/mission-base from the bundled context if it isn't already present.

    The fused per-mission image is FROM this base; a fresh host has no copy (no published
    registry — PRD-0017), so building on demand removes the manual `docker build` prerequisite.
    Building requires network egress (the base installs Tailscale); a failure is surfaced
    verbatim (CalledProcessError) rather than silently producing a half-built base.
    """
    if _image_present(BASE_IMAGE):
        return
    subprocess.run(["docker", "build", "-t", BASE_IMAGE, str(_base_context())], check=True)


def plan_inner_images(compose: Mapping[str, object]) -> tuple[BuildSpec, ...]:
    """Split compose services into build-from-context vs pull-by-image specs (pure)."""
    services = compose.get("services") or {}
    if not isinstance(services, dict):
        return ()
    out: list[BuildSpec] = []
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        build = spec.get("build")
        if build is not None:
            if isinstance(build, str):
                ctx = build
            elif isinstance(build, dict):
                ctx = str(build.get("context", "."))
            else:
                ctx = "."
            out.append(BuildSpec(service=str(name), build_context=ctx))
        else:
            image = spec.get("image")
            out.append(
                BuildSpec(service=str(name), image=str(image) if image is not None else None)
            )
    return tuple(out)


def _write_fused_dockerfile(ctx: Path) -> None:
    (ctx / "Dockerfile").write_text(
        "FROM xorcise/mission-base\nCOPY bundle /mission\nCOPY images.tar /mission/images.tar\n"
    )


class FusedImageBuilder:
    """Builds the fused per-mission OCI image locally (no registry)."""

    def build(self, bundle_dir: Path, manifest: MissionManifest) -> MissionRef:
        ensure_base_image()  # fused image is FROM xorcise/mission-base — build it if absent
        slug = manifest.metadata.mission_id
        # Only lab missions are fused; ingest never calls build() for a static manifest.
        assert manifest.environment is not None, (
            "FusedImageBuilder.build requires a lab environment"
        )
        compose = yaml.safe_load((bundle_dir / manifest.environment.compose_file).read_text())
        specs = plan_inner_images(compose if isinstance(compose, dict) else {})

        inner_tags: list[str] = []
        for s in specs:
            if s.build_context:
                tag = f"xorcise-inner/{slug}-{s.service}:build"
                subprocess.run(
                    ["docker", "build", "-t", tag, str(bundle_dir / s.build_context)], check=True
                )
                inner_tags.append(tag)
            elif s.image:
                subprocess.run(["docker", "pull", s.image], check=True)
                inner_tags.append(s.image)

        # Bake the Tailscale router image too — the per-run net-override runs it as a separate
        # inner container, so it must be in the tar to avoid a run-time pull at deploy.
        subprocess.run(["docker", "pull", ROUTER_IMAGE], check=True)
        inner_tags.append(ROUTER_IMAGE)

        with tempfile.TemporaryDirectory() as td:
            ctx = Path(td)
            subprocess.run(
                ["docker", "save", "-o", str(ctx / "images.tar"), *inner_tags], check=True
            )
            subprocess.run(["cp", "-r", str(bundle_dir), str(ctx / "bundle")], check=True)
            _write_fused_dockerfile(ctx)
            tag = fused_tag(slug)
            subprocess.run(["docker", "build", "-t", tag, str(ctx)], check=True)
        return MissionRef(mission_id=slug, image=tag)
