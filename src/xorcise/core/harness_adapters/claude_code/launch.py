"""Claude Code launch provider (GUI plane): the `claude -p` command, launch tips, mission preamble.

Self-registers at import; pulled in ONLY via harness_adapters.load_launch_providers() so the core
control path stays harness-free (guard: tests/unit/test_core_no_launch_provider_import.py).
"""

from __future__ import annotations

from xorcise.core.runs.launch.base import HarnessLaunchProvider, LaunchContext
from xorcise.core.runs.launch.registry import register


class ClaudeCodeLaunchProvider(HarnessLaunchProvider):
    name = "claude-code"
    version = "1"
    display_name = "Claude Code CLI"
    description = "Anthropic's non-interactive coding CLI."
    model_hints = (
        "claude-opus-4-8",
        "claude-opus-5",
        "claude-fable-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    )
    model_flag = "--model"
    # Include the mission token so this cannot accidentally match the "-p" prefix in
    # "--permission-mode".
    model_flag_anchor = "-p {mission}"
    # bypassPermissions is the only mode a headless `claude -p` run can complete under: there is
    # no human and no canUseTool callback to answer prompts. `auto` is wrong here — it is
    # unavailable on Haiku (silently degrading to default, where every non-read command is denied),
    # and even on a supported model its classifier blocks "download and execute code", which is
    # exactly what a reverse-engineering mission does. Runs are expected to be sandboxed (host).
    launch_command_template = "claude --permission-mode bypassPermissions -p {mission}"
    # Host-only: `claude -p` runs on the host, where the collector is the local loopback —
    # host.docker.internal (the container address) doesn't resolve there.
    launch_modes = ("host",)

    def tips(self, ctx: LaunchContext) -> tuple[str, ...]:
        return (
            "The generated run-control and telemetry addresses match this agent's saved execution "
            "context; keep that context aligned with any wrapper or container you add.",
            "The command runs headless with --permission-mode bypassPermissions so shell tools "
            "execute without interactive approval; run it inside a sandbox, as it grants full "
            "host access.",
        )

    def mission_preamble(self, ctx: LaunchContext) -> tuple[str, ...]:
        return (
            "You are launched non-interactively via `claude -p`. Work the objective directly; "
            "do not modify system networking or join any tailnet other than "
            "the one this run provides.",
            "Note: run tailscale in user-space and don't leave artifacts outside your workspace.",
        )


register(ClaudeCodeLaunchProvider())
