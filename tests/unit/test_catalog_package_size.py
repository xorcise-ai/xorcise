"""Package size travels the catalog chain: catalog API → LibraryItem → CatalogEntry.

The browse card has to answer "what will this cost me?" BEFORE the user commits to a
pull, so the size has to survive every hop between the remote catalog and the UI. Each
field is independently nullable — a STATIC mission has no image, a LAB may declare no
attachments, and an older catalog serves none of them — so absent degrades to None
(render "size unknown") rather than 0.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401 — registers commands on the shared app
from xorcise.core.catalog.http import HttpCatalogSource
from xorcise.core.catalog.source import CatalogSource, LibraryItem
from xorcise.core.cli._shared import app
from xorcise.core.cli._ux import DASH, size_label
from xorcise.core.cli.rest_client import RestClient
from xorcise.core.contracts.catalog import CatalogStatus
from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.rest.catalog_view import CatalogViewDeps, list_catalog

pytestmark = pytest.mark.unit

runner = CliRunner()

_IMAGE = "registry.example.com/xorcise/mis-chrono-canary:abc-base1"


def _catalog(**extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "chrono-canary",
        "name": "Chrono Canary",
        "objective": "Own the time service.",
        "difficulty": "Expert",
        "competencies": ["Penetration"],
        "technologies": ["c"],
        "image": _IMAGE,
    }
    row.update(extra)
    return {"catalog": [row]}


def _source(payload: dict[str, Any]) -> HttpCatalogSource:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return HttpCatalogSource(
        "https://api.example.com", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_library_item_carries_the_sizes_the_catalog_serves():
    source = _source(
        _catalog(
            image_size_bytes=260306509,
            attachments_size_bytes=384284,
            download_size_bytes=260690793,
        )
    )

    item = source.list_library()[0]

    assert item.image_size_bytes == 260306509
    assert item.attachments_size_bytes == 384284
    assert item.download_size_bytes == 260690793


def test_catalog_without_size_fields_degrades_to_unknown():
    """An older catalog deployment serves no size keys; the client must still parse."""
    item = _source(_catalog()).list_library()[0]

    assert item.image_size_bytes is None
    assert item.attachments_size_bytes is None
    assert item.download_size_bytes is None


def test_static_mission_carries_only_an_attachment_size():
    source = _source(
        _catalog(
            image=None,
            attachments_size_bytes=41300000,
            download_size_bytes=41300000,
        )
    )

    item = source.list_library()[0]

    assert item.image_size_bytes is None
    assert item.attachments_size_bytes == 41300000
    assert item.download_size_bytes == 41300000


def test_non_numeric_size_is_ignored_rather_than_crashing_browse():
    """A malformed catalog row must not take the whole browse view down with it."""
    item = _source(_catalog(download_size_bytes="lots")).list_library()[0]

    assert item.download_size_bytes is None


class _FakeSource(CatalogSource):
    def __init__(self, items: tuple[LibraryItem, ...]) -> None:
        self._items = items

    def list_library(self) -> tuple[LibraryItem, ...]:
        return self._items

    def status(self) -> CatalogStatus:
        return CatalogStatus(state="connected")

    def fetch_manifest(self, mission_id: str) -> MissionManifest:  # pragma: no cover
        raise NotImplementedError


def test_browse_entry_exposes_the_size_to_the_ui(tmp_path: Path):
    """The last hop: catalog_view must carry the size onto the CatalogEntry the UI reads."""
    source = _FakeSource(
        (
            LibraryItem(
                mission_id="chrono-canary",
                name="Chrono Canary",
                image=_IMAGE,
                image_size_bytes=260306509,
                attachments_size_bytes=384284,
                download_size_bytes=260690793,
            ),
        )
    )

    entry = list_catalog(CatalogViewDeps(source=source, install_root=tmp_path))[0]

    assert entry.installed is False
    assert entry.image_size_bytes == 260306509
    assert entry.attachments_size_bytes == 384284
    assert entry.download_size_bytes == 260690793


# --- CLI surface -----------------------------------------------------------------
# `xorcise mission list` is the CLI half of the browse card, so it quotes the same
# number in the same vocabulary the GUI uses (frontend formatBytes) — the parity rule
# run_state_label already follows.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(260690793, "260.7 MB"), (1381434670, "1.4 GB"), (6534, "6.5 KB"), (512, "512 B")],
)
def test_size_label_matches_the_gui_vocabulary(raw: int, expected: str):
    assert size_label(raw) == expected


def test_size_label_of_unknown_is_a_dash_not_zero():
    assert size_label(None) == DASH


def _wire_missions(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    def fake_get(self: RestClient, path: str, **kwargs: Any) -> Any:
        assert path == "/missions"
        return rows

    monkeypatch.setattr(RestClient, "get", fake_get)


def test_mission_list_quotes_the_download_size(monkeypatch: pytest.MonkeyPatch):
    _wire_missions(
        monkeypatch,
        [
            {
                "source": "library",
                "mission_id": "chrono-canary",
                "name": "Chrono Canary",
                "proficiency": "expert",
                "installed": False,
                "download_size_bytes": 260690793,
            }
        ],
    )

    result = runner.invoke(app, ["mission", "list"])

    assert result.exit_code == 0, result.output
    assert "260.7 MB" in result.output


def test_mission_list_shows_a_dash_when_the_size_is_unknown(monkeypatch: pytest.MonkeyPatch):
    """An installed mission carries no size — the download already happened."""
    _wire_missions(
        monkeypatch,
        [
            {
                "source": "library",
                "mission_id": "chrono-canary",
                "name": "Chrono Canary",
                "proficiency": "expert",
                "installed": True,
            }
        ],
    )

    result = runner.invoke(app, ["mission", "list"])

    assert result.exit_code == 0, result.output
    assert "MB" not in result.output
