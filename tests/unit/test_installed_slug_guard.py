"""Hostile mission slugs must stay inside the install root (unit lane).

Slugs reach the install store from REST path params, request bodies and bundle/catalog
manifests. A slug carrying separators, '..' or an absolute path must read as "not
installed" (reads) or fail preflight (installs) — never as a file read, write or rmtree
outside the install root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.missions.errors import PreflightError
from xorcise.core.missions.ingest import install_pulled
from xorcise.core.missions.runtime import (
    INSTALLED_FILE,
    delete_installed,
    get_installed,
    resolve_install_dir,
)

pytestmark = pytest.mark.unit

HOSTILE_SLUGS = (
    "",
    ".",
    "..",
    "../outside",
    "a/../../outside",
    "/etc",
    "nested/child",
)


def _plant_marker(dirpath: Path) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / INSTALLED_FILE).write_text("{}")


@pytest.mark.parametrize("slug", HOSTILE_SLUGS)
def test_resolve_install_dir_rejects_escaping_slugs(tmp_path: Path, slug: str) -> None:
    assert resolve_install_dir(slug, tmp_path / "installs") is None


def test_resolve_install_dir_accepts_a_plain_slug(tmp_path: Path) -> None:
    root = resolve_install_dir("basic-pivot", tmp_path / "installs")
    assert root == (tmp_path / "installs" / "basic-pivot").resolve()


@pytest.mark.parametrize("slug", HOSTILE_SLUGS)
def test_get_installed_rejects_escaping_slugs(tmp_path: Path, slug: str) -> None:
    install_root = tmp_path / "installs"
    _plant_marker(install_root)  # a marker in the root itself must not read as a mission
    _plant_marker(tmp_path / "outside")  # reachable via '..' — must stay invisible
    assert get_installed(slug, install_root) is None


@pytest.mark.parametrize("slug", HOSTILE_SLUGS)
def test_delete_installed_never_leaves_the_install_root(tmp_path: Path, slug: str) -> None:
    install_root = tmp_path / "installs"
    outside = tmp_path / "outside"
    _plant_marker(install_root)
    _plant_marker(outside)
    assert delete_installed(slug, install_root) is False
    assert outside.exists() and install_root.exists()


def _manifest(mission_id: str) -> MissionManifest:
    return MissionManifest.model_validate(
        {
            "schema_version": "2.0",
            "metadata": {
                "mission_id": mission_id,
                "name": mission_id or "x",
                "objective": "do the thing",
                "type": "lab",
            },
            "environment": {},
        }
    )


@pytest.mark.parametrize("slug", HOSTILE_SLUGS)
def test_install_refuses_a_path_shaped_mission_id(tmp_path: Path, slug: str) -> None:
    install_root = tmp_path / "installs"
    with pytest.raises(PreflightError):
        install_pulled(
            manifest=_manifest(slug),
            mission_ref=MissionRef(mission_id=slug, image=""),
            install_root=install_root,
        )
    # nothing may have been written outside (or half-written inside) the install root
    assert list(install_root.iterdir()) == []
    assert not (tmp_path / "outside").exists()
