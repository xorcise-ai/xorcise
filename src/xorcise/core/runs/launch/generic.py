"""The generic launch provider — the run-AGNOSTIC floor: no command, no tips, no preamble."""

from __future__ import annotations

from xorcise.core.runs.launch.base import HarnessLaunchProvider


class GenericLaunchProvider(HarnessLaunchProvider):
    name = "generic"
    version = "1"
    display_name = "Custom"
    description = "Any CLI agent — replayed and launched through generic fallbacks."
