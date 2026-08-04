"""Local filesystem browse router — read-only, powers the GUI file picker (local-only)."""

from __future__ import annotations

from ipaddress import ip_address

from fastapi import APIRouter, HTTPException, Request

from xorcise.core.contracts.fs import FsListing
from xorcise.core.rest.fs_browse import BrowseError, list_directory

router = APIRouter(prefix="/fs", tags=["fs"])


def _is_loopback_client(request: Request) -> bool:
    """True only for a client connecting over this host's loopback. The picker surfaces
    the server host's directory tree, so it answers the local operator alone — not LAN
    peers under an explicit 0.0.0.0 bind, and not agent containers reaching /api over
    the docker bridge gateway."""
    if request.client is None:
        return False
    try:
        return ip_address(request.client.host).is_loopback
    except ValueError:  # not an IP literal (e.g. a test client placeholder) — fail closed
        return False


@router.get("/list")
def list_fs(request: Request, path: str | None = None) -> FsListing:
    """List one directory (names + is_dir). Defaults to the user's home directory."""
    if not _is_loopback_client(request):
        raise HTTPException(status_code=403, detail="filesystem browse is local-only")
    try:
        return list_directory(path)
    except BrowseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
