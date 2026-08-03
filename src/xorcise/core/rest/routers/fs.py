"""Local filesystem browse router — read-only, powers the GUI file picker (local-only)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from xorcise.core.contracts.fs import FsListing
from xorcise.core.rest.fs_browse import BrowseError, list_directory

router = APIRouter(prefix="/fs", tags=["fs"])


@router.get("/list")
def list_fs(path: str | None = None) -> FsListing:
    """List one directory (names + is_dir). Defaults to the user's home directory."""
    try:
        return list_directory(path)
    except BrowseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
