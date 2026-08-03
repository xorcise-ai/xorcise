"""Adapter tests for the attachment mint + signed-download route.

One path `GET /runs/{id}/attachments/{name}` serves two roles:
- Mint call  (Bearer, no sig/exp)   → returns GetAttachmentResponse JSON
- Download   (X-Run-Key + sig + exp) → streams staged file bytes

The fixture installs a minimal "webby" mission that declares an Attachment
(name="dump.pcap", path="files/dump.pcap") and writes the staged file
b"PCAPDATA" under <install_root>/webby/files/dump.pcap.

Install helper used: tests/_helpers.py :: install_mission() — extended here
with an Attachment + the staged file write.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.adapters


@pytest.fixture
def client_with_attachment(migrated_home):
    from pathlib import Path as _Path

    from xorcise.core import runs
    from xorcise.core.contracts.control import MissionRef
    from xorcise.core.contracts.mission import (
        Attachment,
        EnvironmentSpec,
        MissionManifest,
        MissionMetadata,
    )
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission
    from xorcise.core.roles.boot.role_all import build_rest_app

    # Install a "webby" mission that declares an Attachment.
    slug = "webby"
    install_root = _Path(migrated_home) / "missions"
    root = install_root / slug
    root.mkdir(parents=True, exist_ok=True)

    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(
            mission_id=slug, name=slug, objective="Capture the flag.", type="lab"
        ),
        environment=EnvironmentSpec(),
        attachments=(Attachment(name="dump.pcap", path="files/dump.pcap"),),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())

    # Write the staged file that will be streamed on download.
    staged = root / "files" / "dump.pcap"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"PCAPDATA")

    run = runs.create_run(agent_id="a1", mission="webby", run_control_key="key-1")
    return TestClient(build_rest_app()), run.run_id


def test_get_attachment_mints_signed_url(client_with_attachment) -> None:
    c, run_id = client_with_attachment
    r = c.get(
        f"/api/runs/{run_id}/attachments/dump.pcap", headers={"Authorization": "Bearer key-1"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "dump.pcap" and "sig=" in body["url"] and "exp=" in body["url"]


def test_signed_download_streams_bytes(client_with_attachment) -> None:
    c, run_id = client_with_attachment
    minted = c.get(
        f"/api/runs/{run_id}/attachments/dump.pcap", headers={"Authorization": "Bearer key-1"}
    ).json()["url"]
    # The minted url MUST be reachable verbatim: the mission brief tells the agent to fetch it
    # as-is, so it has to carry the mounted /api prefix (the router is include_router'd at /api).
    assert minted.startswith(f"/api/runs/{run_id}/attachments/dump.pcap?")
    r = c.get(minted, headers={"X-Run-Key": "key-1"})
    assert r.status_code == 200 and r.content == b"PCAPDATA"


def test_forged_signature_is_403(client_with_attachment) -> None:
    c, run_id = client_with_attachment
    r = c.get(
        f"/api/runs/{run_id}/attachments/dump.pcap?exp=9999999999&sig=deadbeef",
        headers={"X-Run-Key": "key-1"},
    )
    assert r.status_code == 403


def test_unknown_attachment_is_404(client_with_attachment) -> None:
    c, run_id = client_with_attachment
    r = c.get(f"/api/runs/{run_id}/attachments/nope.bin", headers={"Authorization": "Bearer key-1"})
    assert r.status_code == 404


def test_expired_link_is_403(client_with_attachment) -> None:
    """Expiry guard fires even when the signature is valid for the given (past) exp."""
    from xorcise.core.config import get_settings
    from xorcise.core.runcontrol.signing import sign

    c, run_id = client_with_attachment
    exp = 1  # far in the past
    secret = get_settings().run_control_signing_secret
    sig = sign(secret, run_id, "dump.pcap", exp)
    r = c.get(
        f"/api/runs/{run_id}/attachments/dump.pcap?exp={exp}&sig={sig}",
        headers={"X-Run-Key": "key-1"},
    )
    assert r.status_code == 403
