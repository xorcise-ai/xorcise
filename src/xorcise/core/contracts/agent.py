"""Agent registry wire DTOs (LEAF). A thin declaration — never source analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator


class AgentDeclaration(BaseModel):
    """Register request: name + how it connects + how it emits its OTel trace."""

    name: str
    endpoint: str | None = None
    otel: str | None = None
    model: str | None = (
        None  # the agent's model, DISCLOSED by the user; XORCISE can't introspect it
    )
    kind: str | None = None  # framework id for adapter selection; e.g. "openhands"
    # Per-agent launch overrides. None inherits the selected launch provider; an empty tuple
    # deliberately clears the provider's tips / preamble for this agent.
    launch_command_template: str | None = None
    launch_tips: tuple[str, ...] | None = None
    mission_preamble: tuple[str, ...] | None = None
    # Where this agent's generated launch command executes. This is operational, not decorative:
    # it selects loopback vs container-reachable run-control and OTLP addresses. None inherits
    # the harness provider's launch-mode policy.
    launch_mode: Literal["host", "container"] | None = None

    @field_validator("launch_command_template")
    @classmethod
    def validate_launch_template(cls, value: str | None) -> str | None:
        """Reject placeholders the launch-profile renderer cannot safely fill."""
        if value is None:
            return None
        import string

        allowed = {"mission", "otlp_traces_endpoint", "otlp_logs_endpoint"}
        try:
            fields = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(value)
                if field_name is not None
            }
        except ValueError as exc:
            raise ValueError("launch command template has invalid braces") from exc
        unknown = fields - allowed
        if unknown:
            raise ValueError(
                f"unsupported launch command placeholder(s): {', '.join(sorted(unknown))}"
            )
        return value


class AgentEntry(AgentDeclaration):
    """A stored registry entry as returned over the wire."""

    id: str
    created_at: datetime
    version: int = 1
