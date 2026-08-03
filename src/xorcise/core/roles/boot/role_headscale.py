"""role:headscale composition root — the coordinator plane (stub)."""

from __future__ import annotations

from fastapi import FastAPI

import xorcise.core.headscale as _headscale_plane  # noqa: F401  (marks the plane active)
from xorcise.core.config import get_settings
from xorcise.core.roles.boot import AppSpec


def _headscale_app() -> FastAPI:
    app = FastAPI(title="xorcise-headscale", version="0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "role": "headscale"}

    return app


def apps() -> list[AppSpec]:
    return [AppSpec(_headscale_app(), get_settings().headscale_port, "warning")]
