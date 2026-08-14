"""Runner-side fused per-mission image builder. Runner-only (Docker).

Satisfies the missions.BundleBuilder structural Protocol WITHOUT importing it — both
sides speak only the contracts DTOs (MissionManifest in, MissionRef out). Builds a
single self-contained image FROM xorcise/mission-base with the mission's inner stack
baked in as /mission/images.tar (loaded on boot — no inner pull at deploy). Local build
only: tags xorcise/mission-<slug>:<version>, no registry push.
Lifts the PoC's ensure_images/docker save.
"""

from __future__ import annotations

import re
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

# The base generation this XORCISE builds and can run. Only the MAJOR gates compatibility (a
# major bump is breaking — e.g. the inner engine 27→29 move). This integer MUST equal the value
# in `LABEL ai.xorcise.base.version` in containers/mission-base/Dockerfile (asserted by
# tests/topology/test_dind_base_parity.py) and match the cloud's `base_version` generation.
BASE_VERSION = "2"
REQUIRED_BASE_MAJOR = int(BASE_VERSION)
#: The label the base declares and every fused image inherits via `FROM`.
BASE_VERSION_LABEL = "ai.xorcise.base.version"


def base_major_from_ref(ref: str) -> int | None:
    """The base MAJOR encoded in a fused image tag suffix (`…-base2`, `…-base2.1`), or None.

    Covers every published/pulled artifact. A local fuse (`xorcise/mission-<slug>:local`) carries
    no suffix — the label is read instead (base_major_from_labels)."""
    m = re.search(r"-base(\d+)", ref or "")
    return int(m.group(1)) if m else None


def base_major_from_labels(labels: Mapping[str, str] | None) -> int | None:
    """The base MAJOR from an image's inherited `ai.xorcise.base.version` label, or None."""
    raw = (labels or {}).get(BASE_VERSION_LABEL)
    if raw is None:
        return None
    try:
        return int(str(raw).split(".", 1)[0])
    except ValueError:
        return None


@dataclass(frozen=True)
class BaseCompat:
    """Whether an artifact's base generation is runnable by this XORCISE.

    ONE verdict for two audiences: the run-create gate (which raises) and the catalog browse
    surface (which shows a warning before the user commits) both derive `compatible` from here,
    so a card can never say "runnable" for something a run would refuse. `hint` is a short,
    UI-facing remediation; the run error composes its own command-level detail from the same
    verdict."""

    base_major: int | None  # the artifact's base generation, or None if undeterminable
    compatible: bool | None  # None = undeterminable (no signal) → treat as allowed
    hint: str | None  # short remediation when incompatible; None otherwise


def base_compat(major: int | None) -> BaseCompat:
    """The compatibility verdict for a resolved base major (pure). None major ⇒ undeterminable."""
    if major is None:
        return BaseCompat(None, None, None)
    if major == REQUIRED_BASE_MAJOR:
        return BaseCompat(major, True, None)
    if major < REQUIRED_BASE_MAJOR:
        return BaseCompat(major, False, "Reinstall this mission to get the current base.")
    return BaseCompat(major, False, "Update XORCISE to run this mission.")


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


def _image_label(ref: str, key: str) -> str | None:
    """Read one label off a local image, or None if the image/label is absent."""
    out = subprocess.run(
        ["docker", "image", "inspect", "--format", f"{{{{index .Config.Labels {key!r}}}}}", ref],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    value = out.stdout.strip()
    # docker prints "<no value>" for an absent label under this format.
    return value or None if value != "<no value>" else None


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

    Rebuilds when a present base is the WRONG generation — a bare presence check let an operator
    upgrading XORCISE keep an old base (e.g. the engine-27 base whose Rosetta prestart hook this
    generation exists to escape), silently defeating the fix. The packaged context's declared
    version (BASE_VERSION) is the source of truth; a present base whose `ai.xorcise.base.version`
    label differs (or is absent, i.e. pre-labeling) is rebuilt.
    """
    if _image_present(BASE_IMAGE) and _image_label(BASE_IMAGE, BASE_VERSION_LABEL) == BASE_VERSION:
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
        #
        # Pull the PIN, bake under the CANONICAL tag. netoverride.ROUTER_IMAGE (:stable) is what
        # the per-run override asks compose for, so images.tar must carry the router under that
        # tag or the mission dies at `up` on the hermetic inner daemon. But pulling a floating
        # tag means re-fusing an old mission bakes whatever :stable means that day, so what gets
        # pulled is pinned and then re-tagged. The cloud fuse (buildspec.fuse.yml in the SEPARATE
        # cloud-infrastructure/xorcise-ai repo) documents itself as a mirror of this function.
        #
        # The pin is an immutable version tag, so skip the pull when it is already local — a
        # per-fuse registry round-trip is pure waste, and it lets a re-fuse work offline.
        if not _image_present(ROUTER_PIN):
            subprocess.run(["docker", "pull", ROUTER_PIN], check=True)
        subprocess.run(["docker", "tag", ROUTER_PIN, ROUTER_IMAGE], check=True)
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
