"""HttpCatalogSource + the pull_token seam on CatalogSource.

The live contract is mocked with httpx.MockTransport (no network in CI):
  GET  /v1/catalog                     → {"catalog": [...]}
  GET  /v1/missions/{id}             → {"manifest": {...}, "image_ref": "..."}
  POST /v1/missions/{id}/pull-token  → {"registry","username","password","image_ref","expires_at"}
  GET  /v1/health                      → 200 when reachable
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import httpx
import pytest

from xorcise.core.catalog import StubCatalogSource
from xorcise.core.catalog.http import HttpCatalogSource
from xorcise.core.contracts.errors import (
    NotFoundError,
    PullError,
    UnsupportedManifestVersionError,
)

pytestmark = pytest.mark.unit

_IMAGE = "registry.example.com/xorcise/chal-segmented-pivot:abc-base1"

_CATALOG = {
    "catalog": [
        {
            "id": "segmented-pivot",
            "name": "Segmented Pivot",
            "objective": "Pivot to the internal service.",
            "difficulty": "hard",
            "competencies": ["network"],
            "technologies": ["http", "postgres"],
            "image": _IMAGE,
        }
    ]
}

_MANIFEST = {
    "manifest": {
        "schema_version": "2.0",
        "metadata": {
            "mission_id": "segmented-pivot",
            "name": "Segmented Pivot",
            "summary": "Pivot.",
            "objective": "Pivot to the internal service.",
            "proficiency": "hard",
            "specialty": "network",
            "type": "lab",
            "skills": ["pivoting"],
            "technologies": ["http"],
        },
        "environment": {
            "compose_file": "docker-compose.yml",
            "entry_networks": ["default"],
            "static_ips": {},
        },
    },
    "image_ref": _IMAGE,
}

_TOKEN = {
    "registry": "registry.example.com",
    "username": "AWS",
    "password": "pw",
    "expires_at": "2026-07-01T22:00:00+00:00",
    "image_ref": _IMAGE,
}


def _source(handler: Callable[[httpx.Request], httpx.Response]) -> HttpCatalogSource:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://cat.example")
    return HttpCatalogSource("https://cat.example", client=client)


def test_stub_source_has_no_pull_token() -> None:
    # The stub's images need no registry auth → pull_token is a no-op.
    assert StubCatalogSource(enabled=True).pull_token("sqli-login") is None


def test_list_library_maps_catalog_rows() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/catalog"
        return httpx.Response(200, json=_CATALOG)

    items = _source(h).list_library()
    assert [i.mission_id for i in items] == ["segmented-pivot"]
    it = items[0]
    assert it.proficiency == "hard"
    assert it.specialty == "network"
    assert it.technologies == ("http", "postgres")
    assert it.image == _IMAGE


def test_list_library_http_error_raises_pull_error() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(PullError):
        _source(h).list_library()


def test_fetch_manifest_deserializes_schema_2_0() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/missions/segmented-pivot"
        return httpx.Response(200, json=_MANIFEST)

    m = _source(h).fetch_manifest("segmented-pivot")
    assert m.schema_version == "2.0"
    assert m.metadata.mission_id == "segmented-pivot"
    assert m.metadata.skills == ("pivoting",)


def test_fetch_manifest_404_raises_not_found() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not found"})

    with pytest.raises(NotFoundError):
        _source(h).fetch_manifest("nope")


def test_pull_token_parses_broker_creds() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path.endswith("/pull-token")
        return httpx.Response(200, json=_TOKEN)

    tok = _source(h).pull_token("segmented-pivot")
    assert tok is not None
    assert tok.username == "AWS"
    assert tok.password == "pw"
    assert tok.image_ref == _IMAGE


def test_pull_token_not_published_raises_pull_error() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Not published"})

    with pytest.raises(PullError):
        _source(h).pull_token("segmented-pivot")


def test_stub_source_has_no_delivery() -> None:
    # Stub fixtures declare no attachments → no out-of-band bundle to fetch.
    assert StubCatalogSource(enabled=True).fetch_delivery("sqli-login") is None


def test_fetch_delivery_404_returns_none() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/missions/x/download"
        return httpx.Response(404, json={"error": "Not found"})

    assert _source(h).fetch_delivery("x") is None


def test_fetch_delivery_downloads_bundle_and_metadata() -> None:
    zip_bytes = b"PK\x03\x04-fake-zip"
    dl_url = "https://cdn.example/dl/dist/x/mission.zip?sig=abc"

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/missions/x/download":
            return httpx.Response(
                200,
                json={"download_url": dl_url, "dist_sha256": "deadbeef", "delivery_version": 1},
            )
        if req.url.path.startswith("/dl/"):  # the signed URL on the CDN host
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404, json={"error": "Not found"})

    bundle = _source(h).fetch_delivery("x")
    assert bundle is not None
    assert bundle.content == zip_bytes
    assert bundle.sha256 == "deadbeef"
    assert bundle.delivery_version == 1


def test_status_connected_on_health_200() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/health"
        return httpx.Response(200, json={"status": "ok"})

    assert _source(h).status().state == "connected"


def test_status_error_on_unreachable() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    assert _source(h).status().state == "error"


def test_list_library_carries_mission_type_when_the_catalog_emits_it() -> None:
    """lab-vs-static must reach the card BEFORE a pull, so an operator knows what they get.

    The remote /v1/catalog now projects `type` from the stored manifest metadata; this pins the
    mapping so every AVAILABLE card labels itself. Absent stays None, which renders no badge
    rather than a wrong one — so an older catalog still degrades cleanly.
    """

    def typed(req: httpx.Request) -> httpx.Response:
        row = {**_CATALOG["catalog"][0], "type": "static"}
        return httpx.Response(200, json={"catalog": [row]})

    def untyped(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_CATALOG)

    assert _source(typed).list_library()[0].type == "static"
    assert _source(untyped).list_library()[0].type is None


def test_list_library_carries_skills_when_the_catalog_emits_it() -> None:
    """Security skills must reach the card/detail BEFORE a pull too — same contract as `type`.

    The remote /v1/catalog now projects `skills` from the manifest metadata; this pins the
    mapping. Absent stays () (renders "no skills listed"), so an older catalog degrades cleanly.
    """

    def with_skills(req: httpx.Request) -> httpx.Response:
        row = {**_CATALOG["catalog"][0], "skills": ["pivoting", "lateral-movement"]}
        return httpx.Response(200, json={"catalog": [row]})

    def without(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_CATALOG)

    assert _source(with_skills).list_library()[0].skills == ("pivoting", "lateral-movement")
    assert _source(without).list_library()[0].skills == ()


def test_fetch_manifest_deserializes_schema_3_0() -> None:
    doc = {
        "manifest": {**cast("dict[str, object]", _MANIFEST["manifest"]), "version": "1.0.0"}
        | {"schema_version": "3.0"},
        "image_ref": _IMAGE,
    }

    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=doc)

    m = _source(h).fetch_manifest("segmented-pivot")
    assert m.schema_version == "3.0"
    assert m.version == "1.0.0"


def test_fetch_manifest_unknown_schema_raises_typed_upgrade_error() -> None:
    # A catalog newer than this client (e.g. a future 4.0) must surface as an actionable
    # "upgrade XORCISE" error — never a pydantic traceback (UnsupportedManifestVersionError).
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"manifest": {"schema_version": "4.0"}})

    with pytest.raises(UnsupportedManifestVersionError) as exc:
        _source(h).fetch_manifest("segmented-pivot")
    assert exc.value.served == "4.0"
    assert exc.value.supported == ("2.0", "3.0")
    assert "upgrade XORCISE" in str(exc.value)
    assert isinstance(exc.value, PullError)  # rides the existing REST/CLI PullError mapping


def test_fetch_manifest_invalid_supported_schema_is_typed_too() -> None:
    # A supported-version document that fails validation (client and catalog disagree about
    # the shape) is the same typed error, not a crash.
    doc = {
        "manifest": {**cast("dict[str, object]", _MANIFEST["manifest"]), "version": "01.0.0"}
        | {"schema_version": "3.0"},
        "image_ref": _IMAGE,
    }

    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=doc)

    with pytest.raises(UnsupportedManifestVersionError) as exc:
        _source(h).fetch_manifest("segmented-pivot")
    assert exc.value.served == "3.0"
    assert "cannot validate" in str(exc.value)
