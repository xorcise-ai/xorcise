"""Read-only filesystem browse: domain listing + GET /api/fs/list wiring."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xorcise.core.rest.fs_browse import BrowseError, list_directory
from xorcise.core.roles.boot.role_all import build_rest_app

pytestmark = pytest.mark.unit


def _tree(root: Path) -> None:
    (root / "beta").mkdir()
    (root / "Alpha").mkdir()
    (root / "agent.py").write_text("print('hi')\n")
    (root / "config.yaml").write_text("k: v\n")


def test_lists_dirs_first_then_files_case_insensitive(tmp_path: Path) -> None:
    _tree(tmp_path)
    listing = list_directory(str(tmp_path))
    assert listing.path == str(tmp_path.resolve())
    assert listing.parent == str(tmp_path.resolve().parent)
    names = [(e.name, e.is_dir) for e in listing.entries]
    # Dirs first (Alpha, beta), then files (agent.py, config.yaml).
    assert names == [
        ("Alpha", True),
        ("beta", True),
        ("agent.py", False),
        ("config.yaml", False),
    ]
    # Paths are absolute; contents are never exposed (FsEntry has no body field).
    assert all(Path(e.path).is_absolute() for e in listing.entries)
    assert set(listing.entries[0].model_dump()) == {"name", "path", "is_dir"}


def test_default_path_is_home() -> None:
    listing = list_directory(None)
    assert listing.path == str(Path.home().resolve())


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(BrowseError):
        list_directory(str(tmp_path / "does-not-exist"))


def test_file_target_raises(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(BrowseError):
        list_directory(str(f))


def test_rest_list_ok(tmp_path: Path) -> None:
    _tree(tmp_path)
    r = TestClient(build_rest_app()).get("/api/fs/list", params={"path": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(tmp_path.resolve())
    assert [e["name"] for e in body["entries"]] == ["Alpha", "beta", "agent.py", "config.yaml"]


def test_rest_bad_path_is_400(tmp_path: Path) -> None:
    r = TestClient(build_rest_app()).get("/api/fs/list", params={"path": str(tmp_path / "nope")})
    assert r.status_code == 400
