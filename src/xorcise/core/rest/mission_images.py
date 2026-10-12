"""Mission image lifecycle: which local images XORCISE owns, and which are safe to remove.

Every pulled or fused mission image is 1-3 GB, and nothing removed them: an uninstall kept the
image, and an update left the previous generation's tag behind. This module decides what
`xorcise mission prune` and `mission delete --purge` may delete.

An image is OURS only when it carries the mission-base lineage AND every one of its tags names a
mission repository. Either alone is not enough: base images and anything built FROM one carry
the lineage, and a tag is just a name anyone can apply. An OWNED image is removable unless
something still needs it: an install record, a live run, a container, or a pull in flight.
Gathering those protections fails closed — an install record that cannot be read aborts the
prune rather than leaving its image unprotected.

LAYER: delivery (rest). Composes the install store's on-disk records (missions), the run table
(runs) and the image store (runner) — the same reasoning as mission_pull.py. The missions router
imports this module lazily, inside its handlers, so the runner island stays off the control
role's boot path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from xorcise.core.contracts.errors import ContractError, ImageInUseError
from xorcise.core.runner.docker import DockerDriver, ImageRecord
from xorcise.core.runner.docker.build import BASE_VERSION_LABEL

_TITLE_LABEL = "org.opencontainers.image.title"
# Both spellings the base has shipped under: the cloud's base1 generation carried only the
# title, the OSS base2 Dockerfile carries the title and BASE_VERSION_LABEL.
_BASE_TITLES = frozenset({"xorcise-mission-base", "XORCISE mission-base"})

_SLUG = r"(?P<slug>[a-z0-9][a-z0-9._-]*)"
# A registry host is told apart from a Docker Hub namespace the way docker does it: it holds a
# '.' or a ':'. Without that rule `someuser/xorcise/mis-x` would match.
_REGISTRY_HOST = r"(?:[^/]*[.:][^/]*/)?"
# Docker Hub names one image several ways; the image list reports the shortest.
_HUB_PREFIX = re.compile(
    r"^(?:docker\.io/|index\.docker\.io/|registry-1\.docker\.io/)(?:library/)?"
)
_MISSION_REPOS = (
    # A local fuse (FusedImageBuilder): xorcise/mission-<slug>:local
    re.compile(rf"xorcise/mission-{_SLUG}"),
    # A catalog pull: [<registry>/]xorcise/[<env>/]mis-<slug>:<tag>
    re.compile(rf"{_REGISTRY_HOST}xorcise/(?:[a-z0-9-]+/)?mis-{_SLUG}"),
)


class Reason(StrEnum):
    """Why an owned image is a prune candidate."""

    NOT_INSTALLED = "not installed"
    SUPERSEDED = "superseded"  # its mission is installed, under a different image


class Keep(StrEnum):
    """Why an owned image must stay. Checked in this order; the first that applies is named."""

    INSTALLED = "used by an installed mission"
    ACTIVE_RUN = "used by an active run"
    CONTAINER = "used by a container"
    PULLING = "its mission is being pulled"


class ImageOutcome(StrEnum):
    """What `mission delete --purge` did with the uninstalled mission's image."""

    REMOVED = "removed"
    KEPT = "kept"
    FAILED = "failed"
    NONE = "none"  # nothing to remove: a static mission, or the image is already gone


class InstallRecordUnreadable(ContractError):
    """An install record could not be read, so the image it pins is unknown. Pruning anyway
    could delete an installed mission's image, so the prune is refused."""


@dataclass(frozen=True)
class Candidate:
    image_id: str
    tags: tuple[str, ...]
    slug: str
    size_bytes: int
    reason: Reason


@dataclass(frozen=True)
class Failure:
    image_id: str
    tags: tuple[str, ...]
    error: str


@dataclass(frozen=True)
class Removal:
    removed: tuple[str, ...]  # image ids
    failed: tuple[Failure, ...]


@dataclass(frozen=True)
class PrunePlan:
    applied: bool
    candidates: tuple[Candidate, ...]
    removed: tuple[str, ...]
    failed: tuple[Failure, ...]

    @property
    def reclaimable_bytes(self) -> int:
        """An upper bound: images built on the same base share layers, which stay on disk
        while any image still uses them."""
        return sum(c.size_bytes for c in self.candidates)


@dataclass(frozen=True)
class Purge:
    outcome: ImageOutcome
    detail: str | None = None


def owned_slug(image: ImageRecord) -> str | None:
    """The mission this image belongs to, or None when it is not a XORCISE mission image."""
    if not _has_lineage(image.labels) or not image.tags:
        return None
    slugs = {_mission_slug(tag) for tag in image.tags}
    if len(slugs) != 1:
        return None
    (slug,) = slugs
    return slug


def plan_prune(
    images: Iterable[ImageRecord], protected_ids: set[str], installed_slugs: set[str]
) -> list[Candidate]:
    """The owned, unprotected images, in listing order."""
    candidates = []
    for image in images:
        slug = owned_slug(image)
        if slug is None or image.id in protected_ids:
            continue
        reason = Reason.SUPERSEDED if slug in installed_slugs else Reason.NOT_INSTALLED
        candidates.append(Candidate(image.id, image.tags, slug, image.size_bytes, reason))
    return candidates


def apply_prune(driver: DockerDriver, candidates: Iterable[Candidate]) -> Removal:
    """Remove each candidate by untagging every tag (never forced). A failure is recorded and
    the rest still run: one busy image must not keep the others on disk."""
    removed: list[str] = []
    failed: list[Failure] = []
    for candidate in candidates:
        try:
            for tag in candidate.tags:
                driver.remove_image(tag)
        except ImageInUseError:
            failed.append(Failure(candidate.image_id, candidate.tags, "in use"))
        except Exception as exc:  # noqa: BLE001 — per-image boundary: report it, keep going
            failed.append(Failure(candidate.image_id, candidate.tags, str(exc) or repr(exc)))
        else:
            removed.append(candidate.image_id)
    return Removal(tuple(removed), tuple(failed))


def prune(driver: DockerDriver, install_root: Path, *, apply: bool) -> PrunePlan:
    """Plan (and with `apply`, carry out) the removal of every owned, unprotected image.

    Raises InstallRecordUnreadable before anything is removed when an install record cannot
    be read."""
    images = driver.list_images()
    installs = _read_installs(install_root)
    protected = _protected(images, driver, installs)
    installed_slugs = {name for name in installs if not name.startswith(".")}
    candidates = tuple(plan_prune(images, set(protected), installed_slugs))
    removal = apply_prune(driver, candidates) if apply else Removal((), ())
    return PrunePlan(apply, candidates, removal.removed, removal.failed)


def purge_image(ref: str, *, driver: DockerDriver, install_root: Path) -> Purge:
    """Remove the image a just-uninstalled mission used, under the same rules as prune.

    Call it AFTER the uninstall: the remaining install records then protect only images that
    some OTHER install still uses."""
    if not ref:
        return Purge(ImageOutcome.NONE, "a static mission has no image")
    images = driver.list_images()
    image_id = _resolve(images)(ref)
    image = next((i for i in images if i.id == image_id), None)
    if image is None:
        return Purge(ImageOutcome.NONE, f"{ref} is not in the local image store")
    slug = owned_slug(image)
    if slug is None:
        return Purge(ImageOutcome.KEPT, f"kept {ref}: not a XORCISE mission image")
    try:
        protected = _protected(images, driver, _read_installs(install_root))
    except InstallRecordUnreadable as exc:
        return Purge(ImageOutcome.FAILED, str(exc))
    if image.id in protected:
        return Purge(ImageOutcome.KEPT, f"kept {ref}: {protected[image.id]}")
    candidate = Candidate(image.id, image.tags, slug, image.size_bytes, Reason.NOT_INSTALLED)
    removal = apply_prune(driver, [candidate])
    if removal.failed:
        return Purge(ImageOutcome.FAILED, removal.failed[0].error)
    return Purge(ImageOutcome.REMOVED, f"removed {', '.join(image.tags)}")


def installed_image_ref(slug: str, install_root: Path) -> str | None:
    """The image an install record pins, read raw (a record too old or too new to validate
    still names its image). None when the record is missing or unreadable."""
    from xorcise.core.missions.runtime import INSTALLED_FILE, resolve_install_dir

    root = resolve_install_dir(slug, install_root)
    if root is None:
        return None
    try:
        return _record_image(root / INSTALLED_FILE)
    except InstallRecordUnreadable:
        return None


def _has_lineage(labels: Mapping[str, str]) -> bool:
    return BASE_VERSION_LABEL in labels or labels.get(_TITLE_LABEL) in _BASE_TITLES


def _mission_slug(tag: str) -> str | None:
    repo = _repository(tag)
    if repo.endswith("mission-base"):
        return None
    for pattern in _MISSION_REPOS:
        match = pattern.fullmatch(repo)
        if match:
            return match["slug"]
    return None


def _repository(ref: str) -> str:
    """`host:5000/ns/repo:tag` → `host:5000/ns/repo` (a ':' before the last '/' is a port)."""
    name = ref.split("@", 1)[0]
    if ":" in name.rsplit("/", 1)[-1]:
        return name.rsplit(":", 1)[0]
    return name


def _resolve(images: Iterable[ImageRecord]) -> Callable[[str], str | None]:
    """A lookup from a ref (tag, repo digest, or id) to the local image id it names."""
    index: dict[str, str] = {}
    for image in images:
        index[image.id] = image.id
        for name in (*image.tags, *image.digests):
            index[name] = image.id

    def lookup(ref: str) -> str | None:
        ref = _HUB_PREFIX.sub("", ref)
        if "@" not in ref and ":" not in ref.rsplit("/", 1)[-1]:
            ref = f"{ref}:latest"  # docker's implicit tag
        return index.get(ref)

    return lookup


def _read_installs(install_root: Path) -> dict[str, str]:
    """Directory name → image ref, for every install record under the root.

    Hidden directories are read too: an install swap briefly holds the previous record in
    `.<slug>.bak`, and the image it names is still in use until the swap completes."""
    from xorcise.core.missions.runtime import INSTALLED_FILE

    if not install_root.is_dir():
        return {}
    return {
        child.name: _record_image(child / INSTALLED_FILE)
        for child in sorted(install_root.iterdir())
        if (child / INSTALLED_FILE).is_file()
    }


def _record_image(path: Path) -> str:
    try:
        image = json.loads(path.read_text())["mission_ref"]["image"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InstallRecordUnreadable(
            f"cannot read the image from {path} ({type(exc).__name__}: {exc}) — "
            "refusing to remove images while an installed mission's image is unknown; "
            "repair or delete that install first"
        ) from exc
    if not isinstance(image, str):
        raise InstallRecordUnreadable(f"{path} names no image (mission_ref.image is {image!r})")
    return image


def _protected(
    images: list[ImageRecord], driver: DockerDriver, installs: Mapping[str, str]
) -> dict[str, Keep]:
    """Image id → why it must stay.

    An install whose ref names no listed image fails closed: it may be spelled in a way the
    image list does not show, so every image of that mission stays."""
    from xorcise.core.rest.pull_jobs import pull_job_store
    from xorcise.core.runs import active_sandbox_refs

    resolve = _resolve(images)
    pulling = pull_job_store().active_mission_ids()
    unresolved = {_install_slug(name) for name, ref in installs.items() if ref and not resolve(ref)}
    sources: tuple[tuple[Keep, Iterable[str | None]], ...] = (
        (Keep.INSTALLED, (resolve(ref) for ref in installs.values() if ref)),
        (Keep.INSTALLED, (i.id for i in images if owned_slug(i) in unresolved)),
        (Keep.ACTIVE_RUN, (resolve(ref) for ref in active_sandbox_refs())),
        (Keep.CONTAINER, driver.container_image_ids()),
        (Keep.PULLING, (i.id for i in images if owned_slug(i) in pulling)),
    )
    protected: dict[str, Keep] = {}
    for why, ids in sources:
        for image_id in ids:
            if image_id is not None:
                protected.setdefault(image_id, why)
    return protected


def _install_slug(dir_name: str) -> str:
    """`vault`, `.vault.tmp` and `.vault.bak` (an install mid-swap) all hold vault's record."""
    return dir_name.lstrip(".").removesuffix(".tmp").removesuffix(".bak")
