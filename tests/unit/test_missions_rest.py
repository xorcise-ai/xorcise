"""GET /api/missions browses Your Own + the free library (real wiring)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests._helpers import install_mission
from xorcise.core.roles.boot.role_all import build_rest_app

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
