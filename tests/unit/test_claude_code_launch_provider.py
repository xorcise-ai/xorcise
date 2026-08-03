from __future__ import annotations

from xorcise.core.runs.launch.base import LaunchContext


def test_claude_code_launch_provider_registers_and_exposes_command():
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import registered_names, select

    load_launch_providers()
    assert "claude-code" in registered_names()
    provider, fallback = select("claude-code")
    assert fallback is False
    # Robust to launch flags (e.g. --permission-mode ...): a `claude … -p {mission}` command.
    template = provider.launch_command_template
    assert template is not None
    assert template.startswith("claude ") and template.endswith("-p {mission}")


def test_claude_code_tips_mention_permission_mode():
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import select

    load_launch_providers()
    provider, _ = select("claude-code")
    tips = provider.tips(LaunchContext("r1", "claude-code", "host"))
    blob = "\n".join(tips)
    assert "permission-mode" in blob


def test_claude_code_is_host_only():
    # Claude Code runs on the host (`claude -p`), not in a container — it advertises only "host"
    # so the GUI drops the container launch option.
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import select

    load_launch_providers()
    provider, _ = select("claude-code")
    assert provider.launch_modes == ("host",)


def test_claude_code_disclosed_model_is_inserted_before_mission():
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import select

    load_launch_providers()
    provider, _ = select("claude-code")
    assert provider.command_template_for("claude-sonnet-5") == (
        "claude --permission-mode bypassPermissions --model claude-sonnet-5 -p {mission}"
    )


def test_claude_code_mission_preamble_is_nonempty():
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import select

    load_launch_providers()
    provider, _ = select("claude-code")
    pre = provider.mission_preamble(LaunchContext("r1", "claude-code", "container"))
    assert pre and isinstance(pre, tuple)
