"""Mission bundle ingestion + preflight (no Docker; unit lane)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.missions import ingest
from xorcise.core.missions.builder import BundleBuilder, StubBundleBuilder
from xorcise.core.missions.errors import PreflightError
from xorcise.core.missions.preflight import preflight
from xorcise.core.missions.runtime import (
    INSTALLED_FILE,
    InstalledMission,
    delete_installed,
    get_installed,
    list_installed,
)


def _manifest(slug: str = "basic-pivot") -> MissionManifest:
    return MissionManifest.model_validate(
        {
            "schema_version": "2.0",
            "metadata": {
                "mission_id": slug,
                "name": slug,
                "objective": "do the thing",
                "type": "lab",
            },
            "environment": {},
        }
    )


def test_stub_builder_satisfies_protocol_and_returns_ref() -> None:
    builder = StubBundleBuilder()
    assert isinstance(builder, BundleBuilder)
    ref = builder.build(bundle_dir=None, manifest=_manifest("c1"))  # type: ignore[arg-type]
    assert ref.mission_id == "c1"
    assert ref.image  # non-empty image ref
    assert builder.calls == ["c1"]


def _write_bundle(
    root: Path,
    *,
    manifest_overrides: dict[str, object] | None = None,
    with_compose: bool = True,
    files: dict[str, str] | None = None,
) -> Path:
    """Write a bundle dir: mission.json (+ compose + extra files). Returns the dir."""
    bundle = root / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "schema_version": "2.0",
        "metadata": {
            "mission_id": "basic-pivot",
            "name": "Basic Pivot",
            "objective": "Map the subnet.",
            "type": "lab",
        },
        "environment": {"compose_file": "docker-compose.yml", "entry_networks": ["default"]},
    }
    if manifest_overrides:
        manifest.update(manifest_overrides)
    (bundle / "mission.json").write_text(json.dumps(manifest))
    if with_compose:
        (bundle / "docker-compose.yml").write_text("services: {}\n")
    for rel, body in (files or {}).items():
        p = bundle / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return bundle


def test_preflight_valid_returns_manifest(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    m = preflight(bundle)
    assert m.metadata.mission_id == "basic-pivot"


def test_preflight_missing_mission_json(tmp_path: Path) -> None:
    (tmp_path / "bundle").mkdir()
    with pytest.raises(PreflightError) as exc:
        preflight(tmp_path / "bundle")
    assert "mission.json" in str(exc.value)


def test_preflight_unsupported_schema_version(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, manifest_overrides={"schema_version": "1.0"})
    with pytest.raises(PreflightError) as exc:
        preflight(bundle)
    assert "unsupported schema version" in str(exc.value)


def test_preflight_missing_schema_version(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    raw = json.loads((bundle / "mission.json").read_text())
    del raw["schema_version"]
    (bundle / "mission.json").write_text(json.dumps(raw))
    with pytest.raises(PreflightError) as exc:
        preflight(bundle)
    assert "schema_version" in str(exc.value)


def test_preflight_missing_required_field_names_it(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    raw = json.loads((bundle / "mission.json").read_text())
    del raw["metadata"]["objective"]
    (bundle / "mission.json").write_text(json.dumps(raw))
    with pytest.raises(PreflightError) as exc:
        preflight(bundle)
    assert "objective" in str(exc.value)


def test_preflight_missing_compose_file(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, with_compose=False)
    with pytest.raises(PreflightError) as exc:
        preflight(bundle)
    assert "docker-compose.yml" in str(exc.value)


def test_preflight_declared_attachment_missing(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path,
        manifest_overrides={"attachments": [{"name": "vuln.bin", "path": "files/vuln.bin"}]},
    )
    with pytest.raises(PreflightError) as exc:
        preflight(bundle)
    assert "vuln.bin" in str(exc.value)


def test_preflight_present_attachment_passes(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path,
        manifest_overrides={"attachments": [{"name": "vuln.bin", "path": "files/vuln.bin"}]},
        files={"files/vuln.bin": "\x7fELF"},
    )
    assert preflight(bundle).attachments[0].name == "vuln.bin"


def _installed(root: Path, slug: str = "basic-pivot", **manifest_kw: object) -> InstalledMission:
    m = MissionManifest.model_validate(
        {
            "schema_version": "2.0",
            "metadata": {"mission_id": slug, "name": slug, "objective": "obj", "type": "lab"},
            "environment": {"entry_networks": ["default"]},
            **manifest_kw,
        }
    )
    return InstalledMission(
        slug=slug,
        root=root / slug,
        manifest=m,
        mission_ref=MissionRef(mission_id=slug, image=f"img/{slug}:1"),
    )


def test_installed_record_round_trips(tmp_path: Path) -> None:
    inst = _installed(tmp_path)
    inst.root.mkdir(parents=True)
    (inst.root / INSTALLED_FILE).write_text(inst.to_record())
    loaded = InstalledMission.from_root(inst.root)
    assert loaded.manifest == inst.manifest
    assert loaded.mission_ref == inst.mission_ref
    assert loaded.slug == "basic-pivot"


def test_resolution_props_expose_manifest_slices(tmp_path: Path) -> None:
    inst = _installed(
        tmp_path,
        intel=[{"id": "i1", "text": "t"}],
        attachments=[{"name": "a", "path": "files/a"}],
        terrain={"summary": "s"},
    )
    assert inst.intel[0].id == "i1"
    assert inst.attachments[0].name == "a"
    assert inst.terrain is not None and inst.terrain.summary == "s"
    assert inst.environment is not None and inst.environment.entry_networks == ("default",)


def test_resolution_degrades_without_optional_blocks(tmp_path: Path) -> None:
    inst = _installed(tmp_path)
    assert inst.intel == () and inst.attachments == () and inst.terrain is None


def test_get_and_list_installed(tmp_path: Path) -> None:
    assert get_installed("nope", tmp_path) is None
    assert list_installed(tmp_path) == ()
    inst = _installed(tmp_path)
    inst.root.mkdir(parents=True)
    (inst.root / INSTALLED_FILE).write_text(inst.to_record())
    assert get_installed("basic-pivot", tmp_path) is not None
    assert list_installed(tmp_path) == ("basic-pivot",)


class _BoomBuilder:
    def build(self, bundle_dir: Path, manifest: MissionManifest) -> MissionRef:
        raise RuntimeError("build blew up")


def test_ingest_valid_installs_and_is_selectable(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    root = tmp_path / "installed"
    inst = ingest(bundle, builder=StubBundleBuilder(), install_root=root)
    assert inst.slug == "basic-pivot"
    assert inst.mission_ref.image
    assert (root / "basic-pivot" / INSTALLED_FILE).is_file()
    assert (root / "basic-pivot" / "docker-compose.yml").is_file()  # bundle files copied
    assert get_installed("basic-pivot", root) is not None  # selectable


def test_ingest_preflight_failure_leaves_no_state(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path, manifest_overrides={"schema_version": "1.0"})
    root = tmp_path / "installed"
    with pytest.raises(PreflightError):
        ingest(bundle, builder=StubBundleBuilder(), install_root=root)
    assert list_installed(root) == ()
    assert not (root / "basic-pivot").exists()


def test_ingest_builder_failure_leaves_no_state(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    root = tmp_path / "installed"
    with pytest.raises(RuntimeError):
        ingest(bundle, builder=_BoomBuilder(), install_root=root)
    assert list_installed(root) == ()


def test_ingest_reinstall_is_atomic_replace(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    root = tmp_path / "installed"
    ingest(bundle, builder=StubBundleBuilder(), install_root=root)
    inst = ingest(bundle, builder=StubBundleBuilder(), install_root=root)
    assert inst.slug == "basic-pivot"
    assert list_installed(root) == ("basic-pivot",)  # single entry
    assert not list(root.glob(".basic-pivot.*"))  # no temp/backup dirs left


# version stamping ---------------------------------------------------


def test_ingest_first_install_version_is_1(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    root = tmp_path / "installed"
    inst = ingest(bundle, builder=StubBundleBuilder(), install_root=root)
    assert inst.install_revision == 1
    loaded = get_installed("basic-pivot", root)
    assert loaded is not None
    assert loaded.install_revision == 1


def test_ingest_reinstall_bumps_version(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    root = tmp_path / "installed"
    ingest(bundle, builder=StubBundleBuilder(), install_root=root)
    inst2 = ingest(bundle, builder=StubBundleBuilder(), install_root=root)
    assert inst2.install_revision == 2
    loaded = get_installed("basic-pivot", root)
    assert loaded is not None
    assert loaded.install_revision == 2


def test_legacy_record_without_version_reads_as_1(tmp_path: Path) -> None:
    """A legacy installed.json without the 'version' key should default to 1."""
    import json as _json

    slug = "basic-pivot"
    root = tmp_path / slug
    root.mkdir(parents=True)
    # Write a legacy record without the 'version' key
    manifest_data = {
        "schema_version": "2.0",
        "metadata": {"mission_id": slug, "name": slug, "objective": "obj", "type": "lab"},
        "environment": {},
    }
    legacy_record = {
        "manifest": MissionManifest.model_validate(manifest_data).model_dump(mode="json"),
        "mission_ref": MissionRef(mission_id=slug, image=f"img/{slug}:1").model_dump(mode="json"),
    }
    (root / INSTALLED_FILE).write_text(_json.dumps(legacy_record))
    loaded = InstalledMission.from_root(root)
    assert loaded.install_revision == 1


def test_record_missing_required_keys_degrades_to_not_installed(tmp_path: Path) -> None:
    """A record the current contract cannot read must not crash its readers.

    An installed record can go stale — written by an older build, or invalidated by a
    contract tightening — and here it is missing the top-level `mission_ref` key entirely.
    get_installed degrades it to "not installed" so catalog views and run-create surface a
    clean "re-ingest to repair" instead of a KeyError from every caller."""
    import json as _json

    slug = "basic-pivot"
    root = tmp_path / slug
    root.mkdir(parents=True)
    unreadable_record = {
        "version": 1,
        "origin": "your_own",
        "manifest": {
            "schema_version": "2.0",
            "metadata": {
                "mission_id": slug,
                "name": slug,
                "objective": "obj",
                "type": "lab",
            },
            "environment": {},
        },
        # `mission_ref` is required by the contract and absent here — the KeyError this
        # provokes is what get_installed has to absorb.
    }
    (root / INSTALLED_FILE).write_text(_json.dumps(unreadable_record))

    assert get_installed(slug, tmp_path) is None
    # still discoverable as a directory, so the operator sees it and can re-ingest
    assert slug in list_installed(tmp_path)


def test_delete_installed_removes_the_install(tmp_path: Path) -> None:
    """delete_installed atomically removes an installed mission's directory."""
    inst = _installed(tmp_path)
    inst.root.mkdir(parents=True)
    (inst.root / INSTALLED_FILE).write_text(inst.to_record())
    assert "basic-pivot" in list_installed(tmp_path)

    assert delete_installed("basic-pivot", tmp_path) is True
    assert get_installed("basic-pivot", tmp_path) is None
    assert "basic-pivot" not in list_installed(tmp_path)
    assert not (tmp_path / "basic-pivot").exists()


def test_delete_installed_absent_is_false(tmp_path: Path) -> None:
    """Deleting a mission that isn't installed is a no-op that reports False."""
    assert delete_installed("ghost", tmp_path) is False
