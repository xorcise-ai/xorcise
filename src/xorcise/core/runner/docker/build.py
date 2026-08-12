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

# The router build actually pulls. Deliberately NOT netoverride.ROUTER_IMAGE (`:stable`): that
# tag is the DEPLOY-time contract the per-run override resolves and must not change, whereas this
# is the BUILD-time pin that decides which router a mission is fused with. Bump it consciously.
#
# Note this is a different Tailscale than runs.join.TAILSCALE_CLIENT_VERSION, which pins the
# client handed to agents — the two are not currently kept in step.
ROUTER_PIN = "tailscale/tailscale:v1.102.2"


@dataclass(frozen=True)
class BuildSpec:
    service: str
    image: str | None = None
    build_context: str | None = None


BASE_IMAGE = "xorcise/mission-base"

# Every pull and build below is pinned to ONE platform, and it must be the platform the fused
# image itself runs as. Without the pin, `docker pull` resolves a multi-arch tag to the HOST's
# architecture: on Apple Silicon that bakes arm64 blobs into an amd64 mission image, and the
# inner daemon then fails at `up` with "failed to read config content: NotFound: content digest
# sha256:...: not found" — a manifest/config mismatch that reads like a corrupt tar rather than
# an architecture error. Single-arch images (amd64-only vendor images) are unaffected either
# way, which is why the gap only shows up on the multi-arch ones like the Tailscale router.
#
# Mirrors config.docker_platform (the runner's pull/run platform); the caller passes that in so
# the two cannot drift, and this default matches it for direct construction in tests.
DEFAULT_PLATFORM = "linux/amd64"

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


def _platform_args(platform: str) -> list[str]:
    """`--platform <p>` for docker build/pull, or nothing when the caller opted out."""
    return ["--platform", platform] if platform else []


def ensure_base_image(platform: str = DEFAULT_PLATFORM) -> None:
    """Build xorcise/mission-base from the bundled context if it isn't already present.

    The fused per-mission image is FROM this base; a fresh host has no copy (no published
    registry — PRD-0017), so building on demand removes the manual `docker build` prerequisite.
    Building requires network egress (the base installs Tailscale); a failure is surfaced
    verbatim (CalledProcessError) rather than silently producing a half-built base.
    """
    if _image_present(BASE_IMAGE):
        return
    subprocess.run(
        ["docker", "build", *_platform_args(platform), "-t", BASE_IMAGE, str(_base_context())],
        check=True,
    )


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
        image = spec.get("image")
        if build is not None:
            if isinstance(build, str):
                ctx = build
            elif isinstance(build, dict):
                ctx = str(build.get("context", "."))
            else:
                ctx = "."
            # Keep `image` alongside `build` when the service declares BOTH. It is the tag
            # `docker compose up` resolves the service by at deploy, so the builder must bake the
            # built image under it as well — dropping it here (the previous behaviour) meant the
            # loaded images.tar was ignored and compose rebuilt from the bundle at deploy time,
            # which cannot work on the hermetic inner daemon. Mirrors the cloud fuse pipeline,
            # which passes the same `spec.get('image') or ''` through as its alias.
            out.append(
                BuildSpec(
                    service=str(name),
                    build_context=ctx,
                    image=str(image) if image is not None else None,
                )
            )
        else:
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

    def __init__(self, platform: str = DEFAULT_PLATFORM) -> None:
        self._platform = platform

    def build(self, bundle_dir: Path, manifest: MissionManifest) -> MissionRef:
        plat = _platform_args(self._platform)
        ensure_base_image(self._platform)  # fused image is FROM mission-base — build if absent
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
                    ["docker", "build", *plat, "-t", tag, str(bundle_dir / s.build_context)],
                    check=True,
                )
                inner_tags.append(tag)
                if s.image:
                    # ALSO bake it under the tag the compose file references, so `compose up` at
                    # deploy finds it in the loaded tar and uses it AS-IS. Without this the
                    # service is rebuilt at deploy — which pulls its FROM base from docker.io on
                    # an inner daemon that is meant to be hermetic, and on Apple Silicon also
                    # trips the Rosetta prestart-hook bug that still affects BuildKit. Same image,
                    # second tag: no extra build, no extra layer in images.tar.
                    subprocess.run(["docker", "tag", tag, s.image], check=True)
                    inner_tags.append(s.image)
            elif s.image:
                subprocess.run(["docker", "pull", *plat, s.image], check=True)
                inner_tags.append(s.image)

        # Bake the Tailscale router image too — the per-run net-override runs it as a separate
        # inner container, so it must be in the tar to avoid a run-time pull at deploy.
        #
        # Pull the PIN, bake under the CANONICAL tag. netoverride.ROUTER_IMAGE (:stable) is what
        # the per-run override asks compose for, so images.tar must carry the router under that
        # tag or the mission dies at `up` on the hermetic inner daemon. But pulling a floating
        # tag means re-fusing an old mission bakes whatever :stable means that day, so what gets
        # pulled is pinned and then re-tagged. The cloud fuse (buildspec.fuse.yml) documents
        # itself as a mirror of this function and does the same.
        #
        # The re-tag is a one-line FROM BUILD, not `docker tag`, and that is load-bearing on any
        # host whose architecture differs from the fused image's. The router is the only
        # MULTI-ARCH image in the fuse (the mission images are single-platform, which is why they
        # never showed this). Under Docker's containerd image store a tag keeps pointing at the
        # whole multi-arch INDEX, and `pull --platform` fetches only the one platform's blobs, so
        # the index ends up referencing manifests whose content was never downloaded. Both export
        # routes then dead-end: a plain `docker save` fails with "unable to create manifests file:
        # NotFound: content digest sha256:...: not found" on the absent manifest, and
        # `docker save --platform` rejects the image outright ("does not provide the specified
        # platform") even though that platform IS present locally. Building FROM the pin
        # materialises a genuine single-platform image, which exports cleanly. It adds no layers
        # — only a fresh config — and is what makes images.tar loadable by the inner daemon.
        subprocess.run(["docker", "pull", *plat, ROUTER_PIN], check=True)
        subprocess.run(
            ["docker", "build", *plat, "-t", ROUTER_IMAGE, "-"],
            input=f"FROM {ROUTER_PIN}\n".encode(),
            check=True,
        )
        inner_tags.append(ROUTER_IMAGE)

        with tempfile.TemporaryDirectory() as td:
            ctx = Path(td)
            # `save` is platform-pinned too, not just the pulls. Under Docker Desktop's
            # containerd image store a tag keeps pointing at the multi-arch INDEX even after a
            # `pull --platform`, and only the requested platform's blobs are fetched — so an
            # unpinned save tries to write manifests for platforms whose content was never
            # downloaded and dies with "unable to create manifests file: NotFound: content
            # digest sha256:...: not found". Pinning here makes images.tar single-platform,
            # which is also what the inner daemon needs.
            subprocess.run(
                ["docker", "save", *plat, "-o", str(ctx / "images.tar"), *inner_tags], check=True
            )
            subprocess.run(["cp", "-r", str(bundle_dir), str(ctx / "bundle")], check=True)
            _write_fused_dockerfile(ctx)
            tag = fused_tag(slug)
            subprocess.run(["docker", "build", *plat, "-t", tag, str(ctx)], check=True)
        return MissionRef(mission_id=slug, image=tag)
