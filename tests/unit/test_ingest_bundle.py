"""ingest_bundle installs a Your-Own bundle (stub build path; Docker-free)."""

from __future__ import annotations

import json
from pathlib import Path

from xorcise.core.config import get_settings
from xorcise.core.missions import get_installed
from xorcise.core.rest.ingest import ingest_bundle


def _write_bundle(root: Path, slug: str = "myown") -> Path:
    bundle = root / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "2.0",
        "metadata": {
            "mission_id": slug,
            "name": slug,
            "objective": "Map the subnet.",
            "type": "lab",
        },
        "environment": {"compose_file": "docker-compose.yml", "entry_networks": ["default"]},
    }
    (bundle / "mission.json").write_text(json.dumps(manifest))
    (bundle / "docker-compose.yml").write_text("services: {}\n")
    return bundle


def test_ingest_bundle_installs_runnable(migrated_home, tmp_path) -> None:
    # conftest forces XORCISE_USE_STUBS=1 → StubBundleBuilder (Docker-free)
    bundle = _write_bundle(tmp_path)
    ic = ingest_bundle(bundle, get_settings())
    assert ic.slug == "myown"
    again = get_installed("myown", Path(get_settings().missions_root))
    assert again is not None and again.mission_ref.mission_id == "myown"
