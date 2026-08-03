"""Detection logic for auto-building the /ui static export on `xorcise up`.

`frontend_build_status` decides whether `up` must (re)build the Next.js export.
It must return, in priority order: packaged (no source → installed wheel),
missing (fresh clone — no _next chunks), broken (index references chunks absent
on disk — the drift/partial-build blank-page bug), stale (source edited since
the last build), else fresh.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from xorcise.core.cli import _frontend

pytestmark = pytest.mark.unit


def _make_source(tmp_path: Path) -> Path:
    """A minimal frontend/ source tree (package.json marks it as source)."""
    fe = tmp_path / "frontend"
    (fe / "src").mkdir(parents=True)
    (fe / "package.json").write_text("{}")
    (fe / "next.config.ts").write_text("export default {}")
    (fe / "src" / "page.tsx").write_text("export default () => null")
    return fe


def _make_static(tmp_path: Path, *, with_next: bool, refs: list[str], present: list[str]) -> Path:
    """A fake _static export: index.html referencing `refs`, with `present` on disk."""
    sd = tmp_path / "_static"
    sd.mkdir()
    if with_next:
        (sd / "_next" / "static").mkdir(parents=True)
    for asset in present:
        p = sd / asset
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    body = "".join(f'<script src="/ui/{r}"></script>' for r in refs)
    (sd / "index.html").write_text(f"<!doctype html><html><body>{body}</body></html>")
    return sd


def test_packaged_when_no_frontend_source() -> None:
    # Installed wheel: no source tree → never rebuild the baked export.
    assert _frontend.frontend_build_status(None, Path("/nonexistent")) == "packaged"


def test_missing_when_no_next_dir(tmp_path: Path) -> None:
    # A partial export with index.html but no _next/ chunks must be rebuilt.
    fe = _make_source(tmp_path)
    sd = _make_static(tmp_path, with_next=False, refs=[], present=[])
    assert _frontend.frontend_build_status(fe, sd) == "missing"


def test_missing_when_static_export_absent(tmp_path: Path) -> None:
    # Fresh clone: the generated _static/ directory is VCS-ignored and absent.
    fe = _make_source(tmp_path)
    assert _frontend.frontend_build_status(fe, tmp_path / "_static") == "missing"


def test_missing_when_no_index_html(tmp_path: Path) -> None:
    fe = _make_source(tmp_path)
    sd = tmp_path / "_static"
    (sd / "_next").mkdir(parents=True)  # _next present but index.html absent
    assert _frontend.frontend_build_status(fe, sd) == "missing"


def test_broken_when_referenced_chunk_absent(tmp_path: Path) -> None:
    # The exact blank-page bug: index references a chunk that isn't on disk.
    fe = _make_source(tmp_path)
    sd = _make_static(
        tmp_path,
        with_next=True,
        refs=["_next/static/chunks/app/page-abc123.js"],
        present=[],  # referenced chunk NOT written
    )
    assert _frontend.frontend_build_status(fe, sd) == "broken"


def test_stale_when_source_newer_than_index(tmp_path: Path) -> None:
    fe = _make_source(tmp_path)
    asset = "_next/static/chunks/app/page-abc123.js"
    sd = _make_static(tmp_path, with_next=True, refs=[asset], present=[asset])
    index = sd / "index.html"
    # index built in the past; a source file edited after it.
    os.utime(index, (1_000, 1_000))
    os.utime(fe / "src" / "page.tsx", (2_000, 2_000))
    assert _frontend.frontend_build_status(fe, sd) == "stale"


def test_fresh_when_consistent_and_up_to_date(tmp_path: Path) -> None:
    fe = _make_source(tmp_path)
    asset = "_next/static/chunks/app/page-abc123.js"
    sd = _make_static(tmp_path, with_next=True, refs=[asset], present=[asset])
    # every source input older than the index build.
    for p in [fe / "package.json", fe / "next.config.ts", fe / "src" / "page.tsx"]:
        os.utime(p, (1_000, 1_000))
    os.utime(sd / "index.html", (2_000, 2_000))
    assert _frontend.frontend_build_status(fe, sd) == "fresh"


def test_referenced_and_missing_assets_helpers(tmp_path: Path) -> None:
    html = '<link href="/ui/_next/static/css/x.css"/><script src="/ui/_next/static/chunks/y.js">'
    assert _frontend.referenced_assets(html) == [
        "_next/static/chunks/y.js",
        "_next/static/css/x.css",
    ]
    sd = _make_static(
        tmp_path,
        with_next=True,
        refs=["_next/static/css/x.css", "_next/static/chunks/y.js"],
        present=["_next/static/css/x.css"],  # y.js missing
    )
    assert _frontend.missing_assets(sd) == ["_next/static/chunks/y.js"]


def test_find_frontend_source_walks_up_to_package_json(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    fe = _make_source(proj)  # proj/frontend/package.json
    deep = proj / "src" / "xorcise" / "core" / "cli"
    deep.mkdir(parents=True)
    assert _frontend.find_frontend_source(deep / "_frontend.py") == fe
    # a sibling tree with no frontend/package.json ancestor → None (installed wheel).
    other = tmp_path / "other"
    other.mkdir()
    assert _frontend.find_frontend_source(other / "x.py") is None
