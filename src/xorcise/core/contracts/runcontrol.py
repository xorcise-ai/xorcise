"""Run-control wire DTOs (LEAF). The agent-facing REST run-control surface.

REST, not MCP. Distinct from contracts/control.py (the
server↔runner control contract). Imports nothing internal.

REST is the ONLY agent control surface — the MCP mirror contracts/mcp.py once reserved was
removed with the MCP server."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MissionInfo(BaseModel):
    """What `GET /runs/{id}/mission` returns: the run's single mission, unlocked."""

    mission: str
    objective: str
    attachments: tuple[str, ...] = ()  # declared attachment names (bytes via get_attachment)


class SubmitArtifactRequest(BaseModel):
    name: str
    content: str  # inline text/JSON; binary upload is out of scope


class SubmitArtifactResponse(BaseModel):
    accepted: bool = True
    name: str


class IntelResponse(BaseModel):
    intel: str | None = None  # None when no more intel remain
    remaining: int = 0


class CompleteResponse(BaseModel):
    run_id: str = ""  # populated by the router; service constructs without it
    state: str = "terminal"  # sealed by the terminal state machine


class TailnetJoinResponse(BaseModel):
    """What GET /runs/{id}/connect returns: the tailnet join bundle the agent fetches
    instead of the prompt inlining it. login_server is pre-rewritten to by-IP so it is
    directly usable; ca_cert is empty when the deployment is not air-gapped."""

    login_server: str
    join_key: str
    ca_cert: str = ""


class GetAttachmentResponse(BaseModel):
    name: str
    url: str = Field(..., description="Short-lived signed download path (fetch with X-Run-Key).")
    expires_at: datetime
    media_type: str | None = None
    sha256: str | None = None
