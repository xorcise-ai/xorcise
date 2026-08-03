"""xorcise.core.missions — ingestion/preflight/seeder/runtime.

LAYER: PART-ISLAND. Imports only contracts + kernel; does filesystem I/O but NEVER Docker
(the build is the runner's, reached via the BundleBuilder seam).
"""

from __future__ import annotations

from xorcise.core.missions.builder import BundleBuilder, StubBundleBuilder
from xorcise.core.missions.errors import MissionCollisionError, PreflightError
from xorcise.core.missions.ingest import ingest, install_pulled
from xorcise.core.missions.preflight import preflight
from xorcise.core.missions.runtime import (
    INSTALLED_FILE,
    InstalledMission,
    get_installed,
    list_installed,
)

__all__ = [
    "INSTALLED_FILE",
    "BundleBuilder",
    "MissionCollisionError",
    "InstalledMission",
    "PreflightError",
    "StubBundleBuilder",
    "get_installed",
    "ingest",
    "install_pulled",
    "list_installed",
    "preflight",
]
