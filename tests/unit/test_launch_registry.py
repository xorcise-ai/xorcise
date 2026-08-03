from __future__ import annotations

from xorcise.core.runs.launch.base import HarnessLaunchProvider, LaunchContext
from xorcise.core.runs.launch.registry import register, registered_names, select


class _Fake(HarnessLaunchProvider):
    name = "fake-harness"
    version = "1"
    launch_command_template = "fake {mission}"

    def tips(self, ctx: LaunchContext) -> tuple[str, ...]:
        return ("tip-one",)


def test_generic_is_the_registered_floor():
    assert "generic" in registered_names()
    provider, fallback = select("no-such-harness")
    assert provider.name == "generic" and fallback is True
    assert provider.launch_command_template is None
    assert provider.tips(LaunchContext("r", "no-such-harness", "host")) == ()
    assert provider.mission_preamble(LaunchContext("r", "no-such-harness", "host")) == ()


def test_generic_advertises_both_launch_modes():
    # The base default (inherited by the generic floor) offers both host and container; a
    # harness narrows it (Claude Code → host-only).
    provider, _ = select("no-such-harness")
    assert provider.launch_modes == ("host", "container")


def test_exact_match_is_not_a_fallback():
    register(_Fake())
    provider, fallback = select("fake-harness")
    assert provider.name == "fake-harness" and fallback is False
    assert provider.launch_command_template == "fake {mission}"
