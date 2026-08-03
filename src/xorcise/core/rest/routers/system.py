"""System router — read-only Reflect view for the GUI System card."""

from __future__ import annotations

from fastapi import APIRouter

from xorcise.core.config import get_settings
from xorcise.core.contracts.config import SystemInfo
from xorcise.core.rest.system_view import build_system_info

router = APIRouter(prefix="/system", tags=["system"])


@router.get("")
def get_system() -> SystemInfo:
    return build_system_info(get_settings())
