# tests/unit/test_openhands_launch_provider.py
"""OpenHands launch provider (GUI plane) — the headless-CLI peer of the Claude Code one:
`openhands --headless -t <mission>` run on the host, so host-only like Claude Code."""

from __future__ import annotations

from xorcise.core.runs.launch.base import LaunchContext


def test_openhands_launch_provider_registers_and_exposes_command():
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import registered_names, select

    load_launch_providers()
    assert "openhands" in registered_names()
    provider, fallback = select("openhands")
    assert fallback is False
    template = provider.launch_command_template
    assert template is not None
    assert template.startswith("openhands ") and template.endswith("-t {mission}")


def test_openhands_is_host_only():
    # The `openhands` headless CLI runs on the host — advertise only "host" so the GUI drops the
    # container launch option (mirrors Claude Code).
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import select

    load_launch_providers()
    provider, _ = select("openhands")
    assert provider.launch_modes == ("host",)


def test_openhands_tips_mention_headless():
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import select

    load_launch_providers()
    provider, _ = select("openhands")
    blob = "\n".join(provider.tips(LaunchContext("r1", "openhands", "host")))
    assert "headless" in blob


def test_openhands_mission_preamble_is_nonempty():
    from xorcise.core.harness_adapters import load_launch_providers
    from xorcise.core.runs.launch.registry import select

    load_launch_providers()
    provider, _ = select("openhands")
    pre = provider.mission_preamble(LaunchContext("r1", "openhands", "host"))
    assert pre and isinstance(pre, tuple)
