"""Installed-mission representation + install-store read access.

Consumers read their slice through the typed manifest here — never by re-parsing files
(AC: no ad-hoc file parsing). A bundle without intel/terrain/attachments resolves to
empty/None and the run proceeds (graceful degrade)."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import (
    Attachment,
    Check,
    EnvironmentSpec,
    Intel,
    MissionManifest,
    RubricCriterion,
    TerrainSpec,
)

log = logging.getLogger(__name__)

INSTALLED_FILE = "installed.json"

# Where an installed mission came from: "library" = pulled from the remote
# XORCISE catalog; "your_own" = built locally from a bundle via ingest. Drives which browse tab it
# shows under — a pulled library mission stays under XORCISE Remote, not Your Own.
Origin = Literal["your_own", "library"]


@dataclass(frozen=True)
class InstalledMission:
    slug: str
    root: Path
    manifest: MissionManifest
    mission_ref: MissionRef
    version: int = 1  # monotonic; bumped on re-install of the same slug
    origin: Origin = "your_own"  # legacy records (no key) resolve to your_own

    @property
    def is_static(self) -> bool:
        return self.manifest.is_static

    @property
    def is_lab(self) -> bool:
        return self.manifest.is_lab

    @property
    def environment(self) -> EnvironmentSpec | None:
        # None for a static mission (no runtime); callers branch on is_static/is_lab first.
        return self.manifest.environment

    @property
    def intel(self) -> tuple[Intel, ...]:
        return self.manifest.intel

    @property
    def attachments(self) -> tuple[Attachment, ...]:
        return self.manifest.attachments

    @property
    def terrain(self) -> TerrainSpec | None:
        return self.manifest.terrain

    @property
    def rubric(self) -> tuple[RubricCriterion, ...]:
        return self.manifest.rubric

    @property
    def checks(self) -> tuple[Check, ...]:
        return self.manifest.checks

    def to_record(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "origin": self.origin,
                "manifest": self.manifest.model_dump(mode="json"),
                "mission_ref": self.mission_ref.model_dump(mode="json"),
            },
            indent=2,
        )

    @classmethod
    def from_root(cls, root: Path) -> InstalledMission:
        data = json.loads((root / INSTALLED_FILE).read_text())
        return cls(
            slug=root.name,
            root=root,
            manifest=MissionManifest.model_validate(data["manifest"]),
            mission_ref=MissionRef.model_validate(data["mission_ref"]),
            version=int(data.get("version", 1)),  # legacy records without key → 1
            origin=cast(Origin, data.get("origin", "your_own")),  # legacy records → your_own
        )


def get_installed(slug: str, install_root: Path) -> InstalledMission | None:
    root = install_root / slug
    if not (root / INSTALLED_FILE).is_file():
        return None
    try:
        return InstalledMission.from_root(root)
    except (ValidationError, KeyError):
        # A contract tightening (e.g. Check.op becoming the CheckOp Literal) can invalidate an
        # ALREADY-installed record. Degrade to "not installed" — run-create 4xxs, catalog views
        # skip it — instead of raising from every consumer that reads the manifest.
        # KeyError covers a record missing a key the current contract requires: it degrades the
        # same way rather than crashing the caller.
        log.warning(
            "installed mission %r no longer validates against the manifest contract; "
            "treating it as not installed (re-ingest the bundle to repair)",
            slug,
            exc_info=True,
        )
        return None


def list_installed(install_root: Path) -> tuple[str, ...]:
    if not install_root.is_dir():
        return ()
    return tuple(sorted(p.name for p in install_root.iterdir() if (p / INSTALLED_FILE).is_file()))


def delete_installed(slug: str, install_root: Path) -> bool:
    """Remove an installed mission's directory. True if it was installed, else False.

    Uninstalls the local copy — the same for a locally-ingested "your_own" mission and a pulled
    "library" one (a pulled mission has no remote state to reach beyond its local install). The
    fused image is intentionally left in the local store; pruning it is a separate concern. Guarded
    on the INSTALLED_FILE marker so it never removes an unrelated directory.
    """
    root = install_root / slug
    if not (root / INSTALLED_FILE).is_file():
        return False
    shutil.rmtree(root)
    return True
