"""The rest mission_pull spine: local-store-first -> pull -> install."""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from xorcise.core.catalog import StubCatalogSource
from xorcise.core.catalog.http import HttpCatalogSource
from xorcise.core.catalog.source import PullToken
from xorcise.core.missions import get_installed
from xorcise.core.rest.mission_pull import (
    MissionNotInCatalogError,
    PullDeps,
    PullError,
    pull_mission,
)
from xorcise.core.runner.docker import PullProgress, StubDockerDriver


def _deps(tmp_path: Path, driver: StubDockerDriver | None = None) -> PullDeps:
    return PullDeps(
        source=StubCatalogSource(enabled=True),
        driver=driver or StubDockerDriver(),
        install_root=tmp_path,
    )


def test_pull_absent_image_pulls_and_installs(tmp_path: Path) -> None:
    d = StubDockerDriver()
    ic = pull_mission("sqli-login", _deps(tmp_path, d))
    assert d.pulled == ["xorcise/mission-sqli-login:1"]
    assert get_installed("sqli-login", tmp_path) is not None
    assert ic.mission_ref.image == "xorcise/mission-sqli-login:1"


def test_pull_present_image_skips_pull(tmp_path: Path) -> None:
    d = StubDockerDriver()
    d.present.add("xorcise/mission-sqli-login:1")
    pull_mission("sqli-login", _deps(tmp_path, d))
    assert d.pulled == []  # already local
    assert get_installed("sqli-login", tmp_path) is not None


def test_pull_unknown_id_raises_not_in_catalog(tmp_path: Path) -> None:
    with pytest.raises(MissionNotInCatalogError):
        pull_mission("nope", _deps(tmp_path))


def test_pull_failure_leaves_not_installed(tmp_path: Path) -> None:
    class _Boom(StubDockerDriver):
        def pull(
            self,
            image: str,
            *,
            auth: tuple[str, str] | None = None,
            progress: Callable[[PullProgress], None] | None = None,
            platform: str | None = None,
        ) -> None:
            raise RuntimeError("no registry")

    with pytest.raises(PullError):
        pull_mission("sqli-login", _deps(tmp_path, _Boom()))
    assert get_installed("sqli-login", tmp_path) is None


def test_pull_idempotent(tmp_path: Path) -> None:
    pull_mission("sqli-login", _deps(tmp_path))
    again = pull_mission("sqli-login", _deps(tmp_path))  # already installed
    assert again.slug == "sqli-login"


class _FakeAuthedSource(StubCatalogSource):
    """Stub library data, but advertises a pull token (like the HTTP source) to prove auth flows."""

    def pull_token(self, mission_id: str) -> PullToken:
        return PullToken(
            registry="r",
            username="AWS",
            password="pw",
            image_ref="xorcise/mission-sqli-login:1",
            expires_at="t",
        )


def test_pull_passes_registry_auth_to_driver(tmp_path: Path) -> None:
    d = StubDockerDriver()
    deps = PullDeps(source=_FakeAuthedSource(enabled=True), driver=d, install_root=tmp_path)
    pull_mission("sqli-login", deps)
    assert d.pulled == ["xorcise/mission-sqli-login:1"]
    assert d.pulled_auth == ("AWS", "pw")


def test_pull_resolves_non_fixture_id_via_http_source(tmp_path: Path) -> None:
    image = "reg/xorcise/mission-segmented-pivot:base1"
    catalog = {
        "catalog": [
            {
                "id": "segmented-pivot",
                "name": "Segmented Pivot",
                "objective": "x",
                "difficulty": "hard",
                "competencies": ["network"],
                "technologies": ["http"],
                "image": image,
            }
        ]
    }
    manifest = {
        "manifest": {
            "schema_version": "2.0",
            "metadata": {
                "mission_id": "segmented-pivot",
                "name": "Segmented Pivot",
                "summary": "x",
                "objective": "x",
                "proficiency": "hard",
                "specialty": "network",
                "type": "lab",
                "skills": [],
                "technologies": ["http"],
            },
            "environment": {},
        },
        "image_ref": image,
    }
    token = {
        "registry": "reg",
        "username": "AWS",
        "password": "pw",
        "expires_at": "t",
        "image_ref": image,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/v1/catalog":
            return httpx.Response(200, json=catalog)
        if p == "/v1/missions/segmented-pivot":
            return httpx.Response(200, json=manifest)
        if p.endswith("/pull-token"):
            return httpx.Response(200, json=token)
        return httpx.Response(404, json={"error": "Not found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://cat.example")
    src = HttpCatalogSource("https://cat.example", client=client)
    deps = PullDeps(source=src, driver=StubDockerDriver(), install_root=tmp_path)
    ic = pull_mission("segmented-pivot", deps)
    assert ic.slug == "segmented-pivot"
    assert get_installed("segmented-pivot", tmp_path) is not None


# attachment delivery over the HTTP source ---------------------------

_ATT_IMAGE = "reg/xorcise/mission-att:base1"


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _att_handler(
    *, download_status: int, dist_sha256: str, zip_bytes: bytes
) -> Callable[[httpx.Request], httpx.Response]:
    catalog = {
        "catalog": [
            {
                "id": "att",
                "name": "Att",
                "objective": "x",
                "difficulty": "easy",
                "competencies": [],
                "technologies": [],
                "image": _ATT_IMAGE,
            }
        ]
    }
    manifest = {
        "manifest": {
            "schema_version": "2.0",
            "metadata": {
                "mission_id": "att",
                "name": "Att",
                "summary": "x",
                "objective": "x",
                "proficiency": "easy",
                "specialty": "web",
                "type": "lab",
                "skills": [],
                "technologies": [],
            },
            "environment": {},
            "attachments": [{"name": "notes", "path": "files/notes.txt"}],
        },
        "image_ref": _ATT_IMAGE,
    }
    token = {
        "registry": "reg",
        "username": "AWS",
        "password": "pw",
        "expires_at": "t",
        "image_ref": _ATT_IMAGE,
    }
    dl_url = "https://cdn.example/dl/dist/att/mission.zip?sig=x"

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/v1/catalog":
            return httpx.Response(200, json=catalog)
        if p == "/v1/missions/att":
            return httpx.Response(200, json=manifest)
        if p.endswith("/pull-token"):
            return httpx.Response(200, json=token)
        if p == "/v1/missions/att/download":
            if download_status != 200:
                return httpx.Response(download_status, json={"error": "Not found"})
            return httpx.Response(
                200,
                json={
                    "download_url": dl_url,
                    "dist_sha256": dist_sha256,
                    "delivery_version": 1,
                },
            )
        if p.startswith("/dl/"):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404, json={"error": "Not found"})

    return handler


def _att_deps(handler: Callable[[httpx.Request], httpx.Response], tmp_path: Path) -> PullDeps:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://cat.example")
    src = HttpCatalogSource("https://cat.example", client=client)
    return PullDeps(source=src, driver=StubDockerDriver(), install_root=tmp_path)


def test_pull_materializes_attachments_via_delivery(tmp_path: Path) -> None:
    zip_bytes = _zip_bytes({"files/notes.txt": b"secret-notes"})
    sha = hashlib.sha256(zip_bytes).hexdigest()
    handler = _att_handler(download_status=200, dist_sha256=sha, zip_bytes=zip_bytes)
    deps = _att_deps(handler, tmp_path)
    pull_mission("att", deps)
    assert (tmp_path / "att" / "files" / "notes.txt").read_bytes() == b"secret-notes"


def test_pull_delivery_sha_mismatch_leaves_not_installed(tmp_path: Path) -> None:
    zip_bytes = _zip_bytes({"files/notes.txt": b"secret-notes"})
    handler = _att_handler(download_status=200, dist_sha256="wrong", zip_bytes=zip_bytes)
    deps = _att_deps(handler, tmp_path)
    with pytest.raises(PullError):
        pull_mission("att", deps)
    assert get_installed("att", tmp_path) is None


def test_pull_attachments_declared_but_no_bundle_raises(tmp_path: Path) -> None:
    zip_bytes = _zip_bytes({"files/notes.txt": b"x"})
    handler = _att_handler(download_status=404, dist_sha256="", zip_bytes=zip_bytes)
    deps = _att_deps(handler, tmp_path)
    with pytest.raises(PullError):
        pull_mission("att", deps)
    assert get_installed("att", tmp_path) is None


# static-mission-support: an attachment-only (imageless) mission must pull too --------------


def _static_handler(
    *, zip_bytes: bytes, dist_sha256: str
) -> Callable[[httpx.Request], httpx.Response]:
    """A remote catalog serving a STATIC mission: no image (image=None), type=static, one
    attachment. Mirrors the live /v1/catalog shape for derelict-manifest."""
    catalog = {
        "catalog": [
            {
                "id": "derelict",
                "name": "Derelict",
                "objective": "x",
                "difficulty": "expert",
                "competencies": ["reverse-engineering"],
                "technologies": ["pe32"],
                "image": None,  # static → no image
            }
        ]
    }
    manifest = {
        "manifest": {
            "schema_version": "2.0",
            "metadata": {
                "mission_id": "derelict",
                "name": "Derelict",
                "summary": "x",
                "objective": "x",
                "proficiency": "expert",
                "specialty": "reverse-engineering",
                "type": "static",
                "skills": ["reverse-engineering"],
                "technologies": ["pe32"],
            },
            "attachments": [{"name": "artifact", "path": "files/artifact.bin"}],
        },
        "image_ref": None,
    }
    dl_url = "https://cdn.example/dl/dist/derelict/mission.zip?sig=x"

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/v1/catalog":
            return httpx.Response(200, json=catalog)
        if p == "/v1/missions/derelict":
            return httpx.Response(200, json=manifest)
        if p == "/v1/missions/derelict/download":
            return httpx.Response(
                200,
                json={"download_url": dl_url, "dist_sha256": dist_sha256, "delivery_version": 1},
            )
        if p.startswith("/dl/"):
            return httpx.Response(200, content=zip_bytes)
        return httpx.Response(404, json={"error": "Not found"})

    return handler


def test_pull_static_imageless_mission_installs_without_docker_pull(tmp_path: Path) -> None:
    # The reported bug: pulling a static mission failed "no image for mission". A static
    # (attachment-only) mission has no image — the pull must skip docker and install its bundle.
    zip_bytes = _zip_bytes({"files/artifact.bin": b"offline-artifact"})
    sha = hashlib.sha256(zip_bytes).hexdigest()
    d = StubDockerDriver()
    client = httpx.Client(
        transport=httpx.MockTransport(_static_handler(zip_bytes=zip_bytes, dist_sha256=sha)),
        base_url="https://cat.example",
    )
    deps = PullDeps(
        source=HttpCatalogSource("https://cat.example", client=client),
        driver=d,
        install_root=tmp_path,
    )
    ic = pull_mission("derelict", deps)
    assert d.pulled == []  # imageless: NO docker pull attempted (the bug raised before here)
    assert ic.mission_ref.image == ""  # inert empty ref — the run path branches on is_static
    assert ic.manifest.is_static
    assert get_installed("derelict", tmp_path) is not None
    assert (tmp_path / "derelict" / "files" / "artifact.bin").read_bytes() == b"offline-artifact"


def test_image_for_returns_none_for_imageless_static_item(tmp_path: Path) -> None:
    from xorcise.core.catalog.source import LibraryItem
    from xorcise.core.rest.mission_pull import _image_for

    class _Src(StubCatalogSource):
        def list_library(self) -> tuple[LibraryItem, ...]:
            return (LibraryItem(mission_id="static-x", name="Static X", type="static"),)

    # present-but-imageless → None (NOT a "not in catalog" error); absent → raises.
    assert _image_for(_Src(enabled=True), "static-x") is None
    with pytest.raises(MissionNotInCatalogError):
        _image_for(_Src(enabled=True), "absent")


# ── Pull PHASE reporting (the "stuck at 0 bytes" bug) ────────────────────────────────────────────
#
# A docker pull spends long stretches moving no bytes. Counting only "Downloading" events made
# three healthy situations indistinguishable from a hung download, all shown as "Downloading… 0 B":
# every layer already local, the opening negotiation, and extraction (minutes on a multi-GB image,
# AFTER the last byte lands). Measured on a live pull of one mission image: 344 Downloading
# events and 6 Extracting ones, the latter all discarded; a fully-cached pull of another emitted
# three status-only events and no byte events at all. An operator cancelled a working pull over it.


def test_phase_is_preparing_before_any_bytes_move() -> None:
    from xorcise.core.rest.mission_pull import PHASE_PREPARING_IMAGE, _pull_phase

    assert _pull_phase({}, {}) == (PHASE_PREPARING_IMAGE, 0, 0)


def test_phase_is_downloading_while_bytes_are_outstanding() -> None:
    from xorcise.core.rest.mission_pull import PHASE_PULLING_IMAGE, _pull_phase

    assert _pull_phase({"l1": (10, 100), "l2": (5, 50)}, {}) == (PHASE_PULLING_IMAGE, 15, 150)


def test_download_outranks_interleaved_extraction() -> None:
    # docker unpacks one layer while others still download. The phase must NOT flip back and
    # forth: while any download is outstanding the readout stays on the download.
    from xorcise.core.rest.mission_pull import PHASE_PULLING_IMAGE, _pull_phase

    assert _pull_phase({"l1": (10, 100)}, {"l0": (100, 100)}) == (PHASE_PULLING_IMAGE, 10, 100)


def test_phase_becomes_extracting_once_the_bytes_are_all_in() -> None:
    # The long tail: the download bar would otherwise sit frozen at 100% for minutes with no
    # indication that anything is still happening.
    from xorcise.core.rest.mission_pull import PHASE_EXTRACTING_IMAGE, _pull_phase

    assert _pull_phase({"l1": (100, 100)}, {"l1": (30, 100)}) == (PHASE_EXTRACTING_IMAGE, 30, 100)


def test_a_fully_cached_pull_reports_preparing_not_a_stalled_download(tmp_path: Path) -> None:
    """The live reproduction: every layer already in the local store, so docker emits only
    status-only events. The pull is healthy and finishes in under a second, but it must never be
    described as a download sitting at zero bytes."""
    from xorcise.core.rest.mission_pull import PHASE_PREPARING_IMAGE

    class _CachedLayers(StubDockerDriver):
        def pull(
            self,
            image: str,
            *,
            auth: tuple[str, str] | None = None,
            progress: Callable[[PullProgress], None] | None = None,
            platform: str | None = None,
        ) -> None:
            self.pulled.append(image)
            if progress is not None:
                for lid in ("l0", "l1", "l2"):
                    progress(
                        PullProgress(layer_id=lid, status="Already exists", current=0, total=0)
                    )

    seen: list[tuple[str, int, int]] = []
    pull_mission(
        "sqli-login",
        _deps(tmp_path, _CachedLayers()),
        progress=lambda phase, cur, tot: seen.append((phase, cur, tot)),
    )
    image_phases = [p for p, _, _ in seen if p.endswith("_image")]
    assert image_phases == [PHASE_PREPARING_IMAGE] * 4  # the pre-pull report + one per layer event
    assert get_installed("sqli-login", tmp_path) is not None  # and it still installed
