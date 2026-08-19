"""install_pulled records a pulled prebuilt-image mission (no builder)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import (
    Attachment,
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
)
from xorcise.core.missions import get_installed, install_pulled
from xorcise.core.missions.errors import AttachmentBundleError


def _m(slug: str) -> MissionManifest:
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="o", type="lab"),
        environment=EnvironmentSpec(),
    )


def _m_att(slug: str, attachments: tuple[Attachment, ...]) -> MissionManifest:
    return MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="o", type="lab"),
        environment=EnvironmentSpec(),
        attachments=attachments,
    )


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_install_pulled_writes_record(tmp_path: Path) -> None:
    ref = MissionRef(mission_id="c1", image="xorcise/mission-c1:1")
    ic = install_pulled(manifest=_m("c1"), mission_ref=ref, install_root=tmp_path)
    assert ic.slug == "c1"
    again = get_installed("c1", tmp_path)
    assert again is not None
    assert again.mission_ref.image == "xorcise/mission-c1:1"
    assert again.manifest.metadata.objective == "o"


def test_install_pulled_records_library_origin(tmp_path: Path) -> None:
    # A pulled mission is from the remote library, not a local bundle.
    ref = MissionRef(mission_id="c1", image="xorcise/mission-c1:1")
    ic = install_pulled(manifest=_m("c1"), mission_ref=ref, install_root=tmp_path)
    assert ic.origin == "library"
    again = get_installed("c1", tmp_path)
    assert again is not None and again.origin == "library"


def test_install_pulled_overwrites_existing(tmp_path: Path) -> None:
    install_pulled(
        manifest=_m("c1"),
        mission_ref=MissionRef(mission_id="c1", image="xorcise/mission-c1:1"),
        install_root=tmp_path,
    )
    install_pulled(
        manifest=_m("c1"),
        mission_ref=MissionRef(mission_id="c1", image="xorcise/mission-c1:2"),
        install_root=tmp_path,
    )
    again = get_installed("c1", tmp_path)
    assert again is not None and again.mission_ref.image == "xorcise/mission-c1:2"


# version stamping ---------------------------------------------------


def test_install_pulled_first_install_version_is_1(tmp_path: Path) -> None:
    ref = MissionRef(mission_id="c2", image="xorcise/mission-c2:1")
    ic = install_pulled(manifest=_m("c2"), mission_ref=ref, install_root=tmp_path)
    assert ic.install_revision == 1
    again = get_installed("c2", tmp_path)
    assert again is not None
    assert again.install_revision == 1


def test_install_pulled_reinstall_bumps_version(tmp_path: Path) -> None:
    ref1 = MissionRef(mission_id="c3", image="xorcise/mission-c3:1")
    install_pulled(manifest=_m("c3"), mission_ref=ref1, install_root=tmp_path)
    ref2 = MissionRef(mission_id="c3", image="xorcise/mission-c3:2")
    ic2 = install_pulled(manifest=_m("c3"), mission_ref=ref2, install_root=tmp_path)
    assert ic2.install_revision == 2
    again = get_installed("c3", tmp_path)
    assert again is not None
    assert again.install_revision == 2


# delivery-bundle attachment materialization -------------------------


def test_install_pulled_unpacks_declared_attachments(tmp_path: Path) -> None:
    # The declared attachment lands at its att.path so runtime get_attachment resolves it.
    zip_bytes = _zip({"mission.json": b"{}", "files/notes.txt": b"hello"})
    manifest = _m_att("c1", (Attachment(name="notes", path="files/notes.txt"),))
    ref = MissionRef(mission_id="c1", image="xorcise/mission-c1:1")
    install_pulled(
        manifest=manifest, mission_ref=ref, install_root=tmp_path, delivery_zip=zip_bytes
    )
    assert (tmp_path / "c1" / "files" / "notes.txt").read_bytes() == b"hello"


def test_install_pulled_zip_attachment_stays_sealed(tmp_path: Path) -> None:
    # A zip-typed attachment is delivered byte-for-byte, NOT recursively exploded.
    inner = _zip({"secret.txt": b"top"})
    zip_bytes = _zip({"packet.zip": inner})
    manifest = _m_att("c1", (Attachment(name="pcap", path="packet.zip"),))
    ref = MissionRef(mission_id="c1", image="xorcise/mission-c1:1")
    install_pulled(
        manifest=manifest, mission_ref=ref, install_root=tmp_path, delivery_zip=zip_bytes
    )
    assert (tmp_path / "c1" / "packet.zip").read_bytes() == inner
    assert not (tmp_path / "c1" / "secret.txt").exists()


def test_install_pulled_missing_declared_attachment_rolls_back(tmp_path: Path) -> None:
    zip_bytes = _zip({"mission.json": b"{}"})  # declared file absent from the bundle
    manifest = _m_att("c1", (Attachment(name="notes", path="files/notes.txt"),))
    ref = MissionRef(mission_id="c1", image="xorcise/mission-c1:1")
    with pytest.raises(AttachmentBundleError):
        install_pulled(
            manifest=manifest, mission_ref=ref, install_root=tmp_path, delivery_zip=zip_bytes
        )
    assert get_installed("c1", tmp_path) is None  # atomic: nothing half-installed


def test_install_pulled_no_attachments_ignores_delivery_zip(tmp_path: Path) -> None:
    # No declared attachments → empty install even if a stray zip is passed (back-compat).
    ref = MissionRef(mission_id="c1", image="xorcise/mission-c1:1")
    ic = install_pulled(
        manifest=_m("c1"), mission_ref=ref, install_root=tmp_path, delivery_zip=b"ignored"
    )
    assert ic.slug == "c1"
    assert get_installed("c1", tmp_path) is not None
