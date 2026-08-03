"""End-to-end integration: run-control loop + cross-run rejection.

Drives the full REST control loop against a real migrated DB with an installed
"webby" mission that declares a Intel and an Attachment, then asserts every
submission kind is persisted, and that a second run's bearer key cannot reach a
different run's flag endpoint.

Install helper used: tests/_helpers.py :: install_mission() is the pattern;
the fixture here extends it inline (same approach as
tests/adapters/test_runcontrol_attachments.py :: client_with_attachment) to add
a Intel + Attachment to the manifest.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture
def migrated_home_with_webby(migrated_home):
    """migrated DB + an installed 'webby' mission with intel + an attachment.

    Composed from the existing migrated_home fixture (tests/conftest.py) and
    the install pattern established in tests/adapters/test_runcontrol_attachments.py.
    """
    from pathlib import Path as _Path

    from xorcise.core.contracts.control import MissionRef
    from xorcise.core.contracts.mission import (
        Attachment,
        EnvironmentSpec,
        Intel,
        MissionManifest,
        MissionMetadata,
    )
    from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission

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
        intel=(Intel(id="i1", text="Try looking at the headers."),),
        attachments=(Attachment(name="dump.pcap", path="files/dump.pcap"),),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())

    # Write the staged attachment file so the attachment routes work if exercised.
    staged = root / "files" / "dump.pcap"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"PCAPDATA")

    yield migrated_home


def test_scripted_agent_runs_the_control_loop(migrated_home_with_webby) -> None:
    # migrated_home_with_webby: migrated DB + an installed "webby" mission with intel +
    # an attachment (compose from the existing install helper).
    from xorcise.core import runs
    from xorcise.core.roles.boot.role_all import build_rest_app

    run = runs.create_run(agent_id="a1", mission="webby", run_control_key="K")
    c = TestClient(build_rest_app())
    h = {"Authorization": "Bearer K"}
    rid = run.run_id

    assert c.get(f"/api/runs/{rid}/mission", headers=h).json()["objective"]
    assert (
        c.post(
            f"/api/runs/{rid}/artifacts", json={"name": "a", "content": "x"}, headers=h
        ).status_code
        == 200
    )
    # the flag is the artifact named "flag", submitted via the one extensible endpoint.
    assert (
        c.post(
            f"/api/runs/{rid}/artifacts", json={"name": "flag", "content": "FLAG{x}"}, headers=h
        ).json()["accepted"]
        is True
    )
    assert c.get(f"/api/runs/{rid}/intel", headers=h).json()["intel"]
    assert c.post(f"/api/runs/{rid}/complete", headers=h).json()["state"] == "terminal"

    # the submissions are persisted as evaluation context (sealed at terminal): two artifacts
    # ("a" + the "flag" artifact), the intel read, and the complete marker.
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    kinds = sorted([s.kind for s in SqliteSubmissionStore().list_for_run(rid)])
    assert kinds == ["artifact", "artifact", "complete", "intel"]


def test_a_second_runs_key_is_rejected(migrated_home_with_webby) -> None:
    from xorcise.core import runs
    from xorcise.core.roles.boot.role_all import build_rest_app

    a = runs.create_run(agent_id="a1", mission="webby", run_control_key="KA")
    runs.create_run(agent_id="a1", mission="webby", run_control_key="KB", run_id="rb")
    c = TestClient(build_rest_app())
    # KB is run rb's key; it must not reach run a's routes
    r = c.post(
        f"/api/runs/{a.run_id}/artifacts",
        json={"name": "flag", "content": "x"},
        headers={"Authorization": "Bearer KB"},
    )
    assert r.status_code == 401
