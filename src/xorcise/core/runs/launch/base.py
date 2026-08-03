"""Harness launch-provider framework — the GUI-facing peer of runs/telemetry/base.py.

A HarnessLaunchProvider supplies, for ONE harness selected by source_agent: the canonical host
launch command (e.g. ``claude -p {mission}``), GUI launch tips shown in RunPromptCard, and a
bounded preamble baked into the agent-facing mission prompt. Decoupled from
TelemetryProfileProvider so GUI launch UX and OTel-emit config stay separate concerns. Pure (no
I/O); stdlib-only imports.
"""

from __future__ import annotations

import shlex
from abc import ABC
from dataclasses import dataclass
from typing import Literal

LaunchMode = Literal["host", "container"]


@dataclass(frozen=True)
class LaunchContext:
    """The run a launch profile is being surfaced for (GUI plane)."""

    run_id: str
    source_agent: str
    launch_mode: str  # "container" | "host"; delivery validates/clamps before construction


class HarnessLaunchProvider(ABC):
    """GUI launch command + tips + mission preamble for ONE harness, selected by source_agent."""

    name: str
    version: str
    # Registration-facing identity and non-authoritative model suggestions. Model intel are
    # shortcuts only: AgentDeclaration.model stays free text because XORCISE records what the
    # operator discloses and does not own the harness's model catalogue.
    display_name: str = "Custom harness"
    description: str = "Any CLI agent — launch behavior is supplied by the operator."
    model_hints: tuple[str, ...] = ()
    # Optional CLI switch inserted immediately before the mission when a registered agent
    # discloses a model. Keeping this provider-owned avoids teaching the UI CLI syntax.
    model_flag: str | None = None
    model_flag_anchor: str = "{mission}"
    # Single-line host launch command with a ``{mission}`` placeholder the delivery layer fills
    # (shell-quoted). None for launch-agnostic harnesses.
    launch_command_template: str | None = None
    # Launch modes this harness supports, in preference order. The delivery layer clamps a
    # requested mode to this set and the GUI shows a toggle only when there's more than one. A
    # host-only harness (e.g. Claude Code, run via ``claude -p`` on the host) narrows this to
    # ``("host",)`` so the container option disappears.
    launch_modes: tuple[LaunchMode, ...] = ("host", "container")

    def command_template_for(self, model: str | None) -> str | None:
        """Return the provider command with its optional model selection applied."""
        template = self.launch_command_template
        if not template or not self.model_flag or not model:
            return template
        option = f"{self.model_flag} {shlex.quote(model)}"
        marker = self.model_flag_anchor
        if marker in template:
            return template.replace(marker, f"{option} {marker}", 1)
        return f"{template.rstrip()} {option}"

    def tips(self, ctx: LaunchContext) -> tuple[str, ...]:
        """GUI launch guidance lines. Default: none."""
        return ()

    def mission_preamble(self, ctx: LaunchContext) -> tuple[str, ...]:
        """Lines baked into the agent-facing mission prompt. Default: none."""
        return ()
