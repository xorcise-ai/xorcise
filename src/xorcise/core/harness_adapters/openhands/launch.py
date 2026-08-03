"""OpenHands launch provider (GUI plane): the `openhands --headless` command, launch tips, and
mission preamble — the headless-CLI peer of the Claude Code launch provider.

Self-registers at import; pulled in ONLY via harness_adapters.load_launch_providers() so the core
control path stays harness-free (guard: tests/unit/test_core_no_launch_provider_import.py).
"""

from __future__ import annotations

from xorcise.core.runs.launch.base import HarnessLaunchProvider, LaunchContext
from xorcise.core.runs.launch.registry import register


class OpenHandsLaunchProvider(HarnessLaunchProvider):
    name = "openhands"
    version = "1"
    display_name = "OpenHands"
    description = "Autonomous development agent with provider-configurable models."
    model_hints = ("claude-opus-4-8", "claude-sonnet-5", "gpt-5.6")
    # `openhands --headless -t "<task>"` runs OpenHands non-interactively with the mission as the
    # task; the delivery layer fills {mission} (shell-quoted).
    launch_command_template = "openhands --headless -t {mission}"
    # Host-only: the headless CLI runs on the host, where the collector is the local loopback —
    # host.docker.internal (the container address) doesn't resolve there.
    launch_modes = ("host",)

    def tips(self, ctx: LaunchContext) -> tuple[str, ...]:
        return (
            "`--headless` runs the task to completion without the interactive UI; configure your "
            "model/credentials for OpenHands beforehand (e.g. via its config.toml or env).",
        )

    def mission_preamble(self, ctx: LaunchContext) -> tuple[str, ...]:
        return (
            "You are launched non-interactively via `openhands --headless`. Work the objective "
            "directly; do not modify system networking or join any tailnet other than the "
            "one this run provides.",
            "Note: run tailscale in user-space and don't leave artifacts outside your workspace.",
        )


register(OpenHandsLaunchProvider())
