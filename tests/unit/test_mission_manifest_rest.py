"""GET /api/missions/{id}/manifest — full mission.json (installed or library)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._helpers import install_mission
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    return TestClient(build_rest_app())


def test_manifest_for_installed_mission(migrated_home):
    install_mission(migrated_home, slug="myown")
    r = _client().get("/api/missions/myown/manifest")
    assert r.status_code == 200
    body = r.json()
    assert body["metadata"]["mission_id"] == "myown"
    assert body["schema_version"] == "2.0"


def _stub_catalog(monkeypatch) -> None:
    """Force the fixture-backed StubCatalogSource so the library path is hermetic.

    Mirrors tests/unit/test_missions_rest.py; FREE_LIBRARY_MANIFESTS contains 'sqli-login'. The
    real HttpCatalogSource + its selection are covered by test_http_catalog_source.
    """
    from xorcise.core.catalog import StubCatalogSource
    from xorcise.core.rest import catalog_view

    monkeypatch.setattr(
        catalog_view, "build_catalog_source", lambda settings: StubCatalogSource(enabled=True)
    )


def test_manifest_for_library_mission_when_connected(migrated_home, monkeypatch):
    # Hermetic: the fixture library resolves the manifest, no live remote.
    _stub_catalog(monkeypatch)
    r = _client().get("/api/missions/sqli-login/manifest")
    assert r.status_code == 200
    assert r.json()["metadata"]["objective"]  # the agent mission text is present


def test_manifest_unknown_is_404(migrated_home):
    r = _client().get("/api/missions/does-not-exist/manifest")
    assert r.status_code == 404


def test_manifest_ingest_bad_dir_errors_on_the_job(migrated_home):
    # ingest is now async — POST accepts (202 + job_id); a bad dir fails the JOB, not the
    # POST. (Was a synchronous 400 before the async redesign.)
    c = _client()
    r = c.post("/api/missions/ingest", json={"bundle_dir": "/no/such/bundle/dir"})
    assert r.status_code == 202
    job = c.get(f"/api/missions/ingest/{r.json()['job_id']}").json()
    assert job["status"] == "error"
    assert job["detail"]
