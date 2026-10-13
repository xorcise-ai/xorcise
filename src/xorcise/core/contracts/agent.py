"""Agent registry wire DTOs (LEAF). A thin declaration — never source analysis."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

GENERIC_KIND = "generic"
"""The kind of an agent that names no harness. Its runs replay with the generic adapter."""


def normalise_kind(kind: str | None) -> str:
    """The kind an agent is stored with: its surrounding whitespace dropped, and `generic` when
    it is missing or blank. Every agent has a kind (#141)."""
    return (kind or "").strip() or GENERIC_KIND


def closest_kind(kind: str, known: Iterable[str]) -> str | None:
    """The known kind closest to `kind` (difflib, case-insensitive, cutoff 0.6), or None.

    The register page ports this as closestKind (frontend harness-selector.tsx), so the GUI
    suggests what the server and the CLI suggest for the same typo."""
    close = difflib.get_close_matches(kind.lower(), sorted(known), n=1, cutoff=0.6)
    return close[0] if close else None


def kind_warning(kind: str | None, known: Iterable[str]) -> str | None:
    """What to tell an operator who saves `kind`, or None when it names a registered harness.

    Judged as it will be stored, so a blank kind (saved as `generic`) and a padded real one stay
    silent. `generic` is always registered. Shared by the REST response and the CLI gate so the
    two cannot word it differently. "May": the replay adapter is picked by exact kind, then the
    trace's resource kind, then a span fingerprint (otel/adapters/registry.py), so a mislabelled
    run of a real harness can still replay with that harness's adapter."""
    stored = normalise_kind(kind)
    names = sorted({*known, GENERIC_KIND})
    if stored in names:
        return None
    suggestion = closest_kind(stored, names)
    hint = f"; did you mean '{suggestion}'?" if suggestion else "."
    return (
        f"'{stored}' is not a registered harness (known: {', '.join(names)}){hint} "
        "Runs may replay with the generic adapter."
    )


class AgentDeclaration(BaseModel):
    """Register request: name + how it connects + how it emits its OTel trace."""

    name: str
    endpoint: str | None = None
    otel: str | None = None
    model: str | None = (
        None  # the agent's model, DISCLOSED by the user; XORCISE can't introspect it
    )
    # Framework id for adapter selection, e.g. "openhands". Optional here: leaving it out (or
    # sending it blank) saves GENERIC_KIND — see normalise_kind.
    kind: str | None = None
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

    kind: str  # always set: a declaration that left it out was stored as GENERIC_KIND
    id: str
    created_at: datetime
    version: int = 1
