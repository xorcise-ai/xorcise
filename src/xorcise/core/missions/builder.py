"""The mission image-build SEAM.

`missions` does NOT touch Docker. It defines the builder PORT it needs (consumer-owned,
per the dependency-inversion rule) and calls it during ingestion. The real runner-backed
adapter (a real `docker save`-based builder) is future work; the stub here lets the
ingestion flow be exercised end-to-end with no Docker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import MissionManifest


@runtime_checkable
class BundleBuilder(Protocol):
    def build(self, bundle_dir: Path, manifest: MissionManifest) -> MissionRef: ...


class StubBundleBuilder:
    """No-Docker builder for tests/dev. Records calls; returns a placeholder MissionRef."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def build(self, bundle_dir: Path, manifest: MissionManifest) -> MissionRef:
        self.calls.append(manifest.metadata.mission_id)
        return MissionRef(
            mission_id=manifest.metadata.mission_id,
            image=f"xorcise-stub/{manifest.metadata.mission_id}:preflight",
        )
