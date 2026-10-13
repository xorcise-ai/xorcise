"""GET /api/missions browses Your Own + the free library (real wiring)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests._helpers import install_mission
from xorcise.core.roles.boot.role_all import build_rest_app
from xorcise.core.runner.docker import ImageRecord, StubDockerDriver

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def _stub_catalog(monkeypatch) -> None:
    """Force the in-memory fixture library at the shared source seam so these REST tests exercise
    the browse/pull wiring without a live remote catalog. The real HttpCatalogSource and
    its selection are covered by test_http_catalog_source / test_build_pull_deps."""
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest import catalog_view

    monkeypatch.setattr(
        catalog_view, "build_catalog_source", lambda settings: StubCatalogSource(enabled=True)
    )


def _write_bundle(root: Path, slug: str = "myown") -> Path:
    bundle = root / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "2.0",
        "metadata": {"mission_id": slug, "name": slug, "objective": "Solve it.", "type": "lab"},
        "environment": {"compose_file": "docker-compose.yml", "entry_networks": ["default"]},
    }
    (bundle / "mission.json").write_text(json.dumps(manifest))
    (bundle / "docker-compose.yml").write_text("services: {}\n")
    return bundle


def test_ingest_job_installs_a_local_bundle(migrated_home, tmp_path):
    """POST ingest returns 202 + job_id; the job installs async (with progress logs)."""
    bundle = _write_bundle(tmp_path)
    c = _client()
    r = c.post("/api/missions/ingest", json={"bundle_dir": str(bundle)})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    job = c.get(f"/api/missions/ingest/{job_id}").json()  # bg task ran inline under TestClient
    assert job["status"] == "installed"
    assert job["slug"] == "myown"
    assert "myown" in job["image"]
    assert any("installed" in line for line in job["logs"])  # progress logs present
    listed = {e["mission_id"]: e for e in c.get("/api/missions").json()}
    assert listed["myown"]["installed"] is True


def test_ingest_job_missing_bundle_errors(migrated_home, tmp_path):
    """A missing bundle → the job ends in error with a clear detail (POST still 202)."""
    c = _client()
    r = c.post("/api/missions/ingest", json={"bundle_dir": str(tmp_path / "nope")})
    assert r.status_code == 202
    job = c.get(f"/api/missions/ingest/{r.json()['job_id']}").json()
    assert job["status"] == "error"
    assert job["detail"]


def test_ingest_job_invalid_bundle_preflight_errors(migrated_home, tmp_path):
    """A present-but-invalid bundle (failed preflight) → error job, not a crash."""
    empty = tmp_path / "empty_bundle"
    empty.mkdir()  # exists, but no mission.json → preflight fails
    c = _client()
    r = c.post("/api/missions/ingest", json={"bundle_dir": str(empty)})
    assert r.status_code == 202
    job = c.get(f"/api/missions/ingest/{r.json()['job_id']}").json()
    assert job["status"] == "error"


def test_ingest_job_logs_since_cursor(migrated_home, tmp_path):
    """The job endpoint supports a since-index cursor for incremental log reads."""
    bundle = _write_bundle(tmp_path)
    c = _client()
    job_id = c.post("/api/missions/ingest", json={"bundle_dir": str(bundle)}).json()["job_id"]
    full = c.get(f"/api/missions/ingest/{job_id}").json()["logs"]
    assert len(full) >= 1
    tail = c.get(f"/api/missions/ingest/{job_id}?since={len(full) - 1}").json()["logs"]
    assert tail == full[len(full) - 1 :]


def test_ingest_job_unknown_id_404(migrated_home):
    """An unknown job id is a 404."""
    assert _client().get("/api/missions/ingest/does-not-exist").status_code == 404


def test_missions_lists_your_own_and_library(migrated_home, monkeypatch):
    _stub_catalog(monkeypatch)
    install_mission(migrated_home, slug="myown")

    resp = _client().get("/api/missions")
    assert resp.status_code == 200
    data = resp.json()
    assert {e["source"] for e in data} == {"your_own", "library"}
    assert all({"source", "mission_id", "name", "installed"} <= e.keys() for e in data)
    own = next(e for e in data if e["mission_id"] == "myown")
    assert own["source"] == "your_own" and own["installed"] is True


def test_missions_degrades_to_your_own_when_catalog_disabled(migrated_home, monkeypatch):
    from xorcise.core.config import get_settings

    # The remote is connected by default; flip the switch off to test the degrade path.
    monkeypatch.setenv("XORCISE_CATALOG_ENABLED", "false")
    get_settings.cache_clear()
    install_mission(migrated_home, slug="myown")
    resp = _client().get("/api/missions")
    assert resp.status_code == 200
    assert {e["source"] for e in resp.json()} == {"your_own"}


def test_pull_installs_a_library_mission(migrated_home, monkeypatch):
    _stub_catalog(monkeypatch)
    c = _client()
    r = c.post("/api/missions/sqli-login/pull")
    assert r.status_code == 200 and r.json()["installed"] is True
    # The /pull response itself must report the library origin — the CLI prints this field
    # verbatim, so a hardcoded "your_own" mislabels remote pulls.
    assert r.json()["source"] == "library"
    listed = {e["mission_id"]: e for e in c.get("/api/missions").json()}
    assert listed["sqli-login"]["installed"] is True
    # A pulled library mission stays under XORCISE Remote, not Your Own.
    assert listed["sqli-login"]["source"] == "library"


def test_pull_unknown_mission_404(migrated_home, monkeypatch):
    _stub_catalog(monkeypatch)
    r = _client().post("/api/missions/nope/pull")
    assert r.status_code == 404


# a mission_id belongs to one source; cross-source installs never clobber ---------


def test_pull_over_your_own_conflicts_409(migrated_home, monkeypatch):
    """A library id already owned by a your_own install → 409, not a 500 or silent swap."""
    _stub_catalog(monkeypatch)
    install_mission(migrated_home, slug="sqli-login")  # your_own
    r = _client().post("/api/missions/sqli-login/pull")
    assert r.status_code == 409
    assert "sqli-login" in r.json()["detail"]


def test_ingest_local_id_that_shadows_library_errors(migrated_home, tmp_path, monkeypatch):
    """The terminal symptom: ingesting a local bundle whose id the library owns is refused."""
    _stub_catalog(monkeypatch)  # fixture library contains "sqli-login"
    bundle = _write_bundle(tmp_path, slug="sqli-login")
    c = _client()
    job_id = c.post("/api/missions/ingest", json={"bundle_dir": str(bundle)}).json()["job_id"]
    job = c.get(f"/api/missions/ingest/{job_id}").json()
    assert job["status"] == "error"
    assert "library" in (job["detail"] or "")


def test_pull_then_ingest_same_id_preserves_pulled(migrated_home, tmp_path, monkeypatch):
    """Ticket repro end-to-end: pull library sqli-login, then ingest a local sqli-login → the
    pulled install survives (job errors, catalog list still shows the library one)."""
    _stub_catalog(monkeypatch)
    c = _client()
    assert c.post("/api/missions/sqli-login/pull").status_code == 200
    bundle = _write_bundle(tmp_path, slug="sqli-login")
    job_id = c.post("/api/missions/ingest", json={"bundle_dir": str(bundle)}).json()["job_id"]
    assert c.get(f"/api/missions/ingest/{job_id}").json()["status"] == "error"
    listed = {e["mission_id"]: e for e in c.get("/api/missions").json()}
    assert listed["sqli-login"]["source"] == "library" and listed["sqli-login"]["installed"] is True


# delete/uninstall an installed mission -------------------------------------------


def test_delete_mission_removes_a_your_own_install(migrated_home, monkeypatch):
    """DELETE /missions/{id} uninstalls a your_own mission so it leaves the catalog."""
    _stub_catalog(monkeypatch)
    install_mission(migrated_home, slug="myown")
    c = _client()
    assert any(e["mission_id"] == "myown" for e in c.get("/api/missions").json())

    assert c.delete("/api/missions/myown").status_code == 204
    assert not any(e["mission_id"] == "myown" for e in c.get("/api/missions").json())
    # gone for good: deleting again is a clean 404
    assert c.delete("/api/missions/myown").status_code == 404


def test_delete_mission_removes_a_pulled_library_install(migrated_home, monkeypatch):
    """A pulled library mission can be uninstalled too (removes the local copy)."""
    _stub_catalog(monkeypatch)
    c = _client()
    assert c.post("/api/missions/sqli-login/pull").status_code == 200
    assert c.delete("/api/missions/sqli-login").status_code == 204
    listed = {e["mission_id"]: e for e in c.get("/api/missions").json()}
    # still offered by the remote library, but no longer installed locally
    assert listed["sqli-login"]["installed"] is False


def test_delete_mission_unknown_404(migrated_home):
    assert _client().delete("/api/missions/ghost").status_code == 404


# image cleanup: prune + delete --purge -------------------------------------------------------

_LINEAGE = {"ai.xorcise.base.version": "2"}


def _image(image_id: str, *tags: str, size: int = 1) -> ImageRecord:
    return ImageRecord(id=image_id, tags=tags, labels=_LINEAGE, size_bytes=size)


def _seed_driver(monkeypatch: pytest.MonkeyPatch, *images: ImageRecord) -> StubDockerDriver:
    """Serve one seeded stub image store to every handler that builds a Docker driver."""
    from xorcise.core.rest import mission_pull

    driver = StubDockerDriver()
    driver.images = {i.id: i for i in images}
    monkeypatch.setattr(mission_pull, "image_store_driver", lambda settings: driver)
    return driver


def test_prune_dry_run_reports_candidates_and_removes_nothing(migrated_home, monkeypatch):
    install_mission(migrated_home, slug="vault")  # pins xorcise/mission-vault:0
    driver = _seed_driver(
        monkeypatch,
        _image("sha256:cur", "xorcise/mission-vault:0", size=3),
        _image("sha256:old", "xorcise/mis-vault:1", size=5),
        _image("sha256:gone", "xorcise/mission-gone:local", size=7),
    )
    r = _client().post("/api/missions/prune", json={})
    assert r.status_code == 200
    assert r.json() == {
        "applied": False,
        "candidates": [
            {
                "image_id": "sha256:old",
                "tags": ["xorcise/mis-vault:1"],
                "slug": "vault",
                "size_bytes": 5,
                "reason": "superseded",
            },
            {
                "image_id": "sha256:gone",
                "tags": ["xorcise/mission-gone:local"],
                "slug": "gone",
                "size_bytes": 7,
                "reason": "not installed",
            },
        ],
        "removed": [],
        "failed": [],
        "skipped": [],
        "reclaimable_bytes": 12,
    }
    assert len(driver.list_images()) == 3


def test_prune_without_a_body_is_a_dry_run(migrated_home, monkeypatch):
    driver = _seed_driver(monkeypatch, _image("sha256:gone", "xorcise/mission-gone:local"))
    r = _client().post("/api/missions/prune")
    assert (r.status_code, r.json()["applied"]) == (200, False)
    assert driver.removed == []


def test_prune_apply_removes_candidates_and_reports_failures(migrated_home, monkeypatch):
    from xorcise.core.contracts.errors import ImageInUseError

    driver = _seed_driver(
        monkeypatch,
        _image("sha256:gone", "xorcise/mission-gone:local"),
        _image("sha256:raced", "xorcise/mission-raced:local"),
    )
    remove = driver.remove_image

    def remove_unless_raced(ref: str) -> None:
        if ref == "xorcise/mission-raced:local":  # a container started after the listing
            raise ImageInUseError(ref)
        remove(ref)

    driver.remove_image = remove_unless_raced  # type: ignore[method-assign]
    r = _client().post("/api/missions/prune", json={"apply": True})
    assert r.status_code == 200
    body = r.json()
    assert (body["applied"], body["removed"], body["failed"]) == (
        True,
        ["sha256:gone"],
        [{"image_id": "sha256:raced", "tags": ["xorcise/mission-raced:local"], "error": "in use"}],
    )
    assert [i.id for i in driver.list_images()] == ["sha256:raced"]


def test_prune_apply_with_image_ids_removes_only_the_confirmed_images_still_unused(
    migrated_home, monkeypatch
):
    # The GUI confirms a dry run's list, then applies it. Between the two, one confirmed image
    # vanished and a new one became unused: neither the vanished one nor the unseen one moves.
    driver = _seed_driver(
        monkeypatch,
        _image("sha256:a", "xorcise/mission-a:local", size=4),
        _image("sha256:unseen", "xorcise/mission-unseen:local"),
    )
    r = _client().post(
        "/api/missions/prune",
        json={"apply": True, "image_ids": ["sha256:a", "sha256:vanished"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["removed"], body["skipped"]) == (["sha256:a"], ["sha256:vanished"])
    assert [c["image_id"] for c in body["candidates"]] == ["sha256:a"]
    assert body["reclaimable_bytes"] == 4
    assert [i.id for i in driver.list_images()] == ["sha256:unseen"]


def test_prune_refuses_while_an_install_record_is_unreadable(migrated_home, monkeypatch):
    driver = _seed_driver(monkeypatch, _image("sha256:gone", "xorcise/mission-gone:local"))
    broken = migrated_home / "missions" / "broken"
    broken.mkdir(parents=True)
    (broken / "installed.json").write_text("{not json")
    r = _client().post("/api/missions/prune", json={"apply": True})
    assert r.status_code == 409
    assert "broken" in r.json()["detail"]
    assert driver.removed == []


def test_delete_without_purge_is_still_204_and_keeps_the_image(migrated_home, monkeypatch):
    install_mission(migrated_home, slug="vault")
    driver = _seed_driver(monkeypatch, _image("sha256:v", "xorcise/mission-vault:0"))
    r = _client().delete("/api/missions/vault")
    assert (r.status_code, r.content) == (204, b"")
    assert [i.id for i in driver.list_images()] == ["sha256:v"]


def test_delete_with_purge_removes_the_image(migrated_home, monkeypatch):
    install_mission(migrated_home, slug="vault")
    driver = _seed_driver(monkeypatch, _image("sha256:v", "xorcise/mission-vault:0"))
    c = _client()
    r = c.delete("/api/missions/vault?purge=true")
    assert r.status_code == 200
    assert r.json() == {
        "mission_id": "vault",
        "image": "xorcise/mission-vault:0",
        "image_outcome": "removed",
        "detail": "removed xorcise/mission-vault:0",
    }
    assert driver.list_images() == []
    assert c.delete("/api/missions/vault").status_code == 404  # uninstalled too


def test_purge_keeps_an_image_an_active_run_uses(migrated_home, monkeypatch):
    from xorcise.core.runs import create_run

    install_mission(migrated_home, slug="vault")
    create_run("agent", "vault", sandbox_ref="xorcise/mission-vault:0")
    driver = _seed_driver(monkeypatch, _image("sha256:v", "xorcise/mission-vault:0"))
    r = _client().delete("/api/missions/vault?purge=true")
    assert r.status_code == 200
    assert (r.json()["image_outcome"], r.json()["detail"]) == (
        "kept",
        "kept xorcise/mission-vault:0: used by an active run",
    )
    assert [i.id for i in driver.list_images()] == ["sha256:v"]


def test_purge_of_a_static_mission_has_no_image_to_remove(migrated_home, monkeypatch):
    install_mission(migrated_home, slug="notes")
    record = migrated_home / "missions" / "notes" / "installed.json"
    record.write_text(record.read_text().replace("xorcise/mission-notes:0", ""))
    _seed_driver(monkeypatch)
    r = _client().delete("/api/missions/notes?purge=true")
    assert r.status_code == 200
    assert (r.json()["image"], r.json()["image_outcome"]) == (None, "none")


def _docker_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from xorcise.core.rest import mission_pull

    def unreachable(settings: object) -> StubDockerDriver:
        raise RuntimeError("Docker daemon not reachable — start Docker")

    monkeypatch.setattr(mission_pull, "image_store_driver", unreachable)


def test_prune_with_docker_unreachable_is_a_503_naming_the_cause(migrated_home, monkeypatch):
    _docker_unreachable(monkeypatch)
    r = _client().post("/api/missions/prune", json={})
    assert (r.status_code, r.json()["detail"]) == (
        503,
        "Docker daemon not reachable — start Docker",
    )


def test_purge_with_docker_unreachable_still_reports_the_uninstall(migrated_home, monkeypatch):
    install_mission(migrated_home, slug="vault")
    _docker_unreachable(monkeypatch)
    c = _client()
    r = c.delete("/api/missions/vault?purge=true")
    assert r.status_code == 200
    assert r.json() == {
        "mission_id": "vault",
        "image": "xorcise/mission-vault:0",
        "image_outcome": "failed",
        "detail": "Docker daemon not reachable — start Docker",
    }
    assert c.delete("/api/missions/vault").status_code == 404  # the uninstall stood


_NOT_MANAGED = "this server does not manage Docker images"


def test_prune_on_a_server_without_docker_says_so_instead_of_reporting_a_clean_disk(
    migrated_home,
):
    r = _client().post("/api/missions/prune", json={})
    assert r.status_code == 409
    assert r.json()["detail"].startswith(_NOT_MANAGED)


def test_purge_on_a_server_without_docker_uninstalls_and_says_the_image_was_not_seen(
    migrated_home,
):
    install_mission(migrated_home, slug="vault")
    c = _client()
    r = c.delete("/api/missions/vault?purge=true")
    assert r.status_code == 200
    body = r.json()
    assert (body["image"], body["image_outcome"]) == ("xorcise/mission-vault:0", "none")
    assert body["detail"].startswith(_NOT_MANAGED)
    assert c.delete("/api/missions/vault").status_code == 404


def test_purge_of_a_static_mission_with_docker_down_is_not_a_failure(migrated_home, monkeypatch):
    install_mission(migrated_home, slug="notes")
    record = migrated_home / "missions" / "notes" / "installed.json"
    record.write_text(record.read_text().replace("xorcise/mission-notes:0", ""))
    _docker_unreachable(monkeypatch)
    r = _client().delete("/api/missions/notes?purge=true")
    assert r.status_code == 200
    assert r.json() == {
        "mission_id": "notes",
        "image": None,
        "image_outcome": "none",
        "detail": "a static mission has no image",
    }


def test_pull_job_refresh_repulls_an_installed_mission(migrated_home, monkeypatch):
    import dataclasses

    from xorcise.core.rest.mission_pull import build_pull_deps

    _stub_catalog(monkeypatch)
    driver = StubDockerDriver()
    monkeypatch.setattr(
        "xorcise.core.rest.routers.missions.build_pull_deps",
        lambda settings: dataclasses.replace(build_pull_deps(settings), driver=driver),
    )
    c = _client()

    def pull(json: dict[str, object] | None = None) -> str:
        r = c.post("/api/missions/sqli-login/pull-jobs", json=json)
        assert r.status_code == 202
        job = c.get(f"/api/missions/pull-jobs/{r.json()['job_id']}").json()
        return str(job["status"])

    assert pull(json={}) == "installed"
    assert pull() == "installed"  # no body: already installed, nothing pulled
    assert driver.pulled == ["xorcise/mission-sqli-login:1"]
    assert pull(json={"refresh": True}) == "installed"
    assert driver.pulled == ["xorcise/mission-sqli-login:1", "xorcise/mission-sqli-login:1"]
