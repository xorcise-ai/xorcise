"""role:runner composition root — runner control-service plane (stub).

Imports the runner plane to mark it active; real control-service behaviour is a
runner feature story. No core/REST, no otel.
"""

from __future__ import annotations

from fastapi import FastAPI

import xorcise.core.runner as _runner_plane  # noqa: F401  (marks the runner plane active)
from xorcise.core.config import get_settings
from xorcise.core.roles.boot import AppSpec


def _runner_app() -> FastAPI:
    app = FastAPI(title="xorcise-runner", version="0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "role": "runner"}

    return app


def apps() -> list[AppSpec]:
    return [AppSpec(_runner_app(), get_settings().runner_port, "warning")]
