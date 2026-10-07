"""Announcements router — banners published by XORCISE Remote, relayed locally."""

from __future__ import annotations

from fastapi import APIRouter, Response

from xorcise.core.config import get_settings
from xorcise.core.contracts.announcements import AnnouncementsResponse
from xorcise.core.rest.announcements_view import list_announcements

router = APIRouter(prefix="/announcements", tags=["announcements"])


@router.get("")
def active(response: Response) -> AnnouncementsResponse:
    """Active announcements for this install; an empty list whenever there are none."""
    # Never cached: a publish or a withdrawal must reach the operator on a manual refresh,
    # and a banner a browser keeps serving from cache is worse than no banner at all.
    response.headers["Cache-Control"] = "no-store"
    return list_announcements(get_settings())
