from __future__ import annotations

from xorcise.core.contracts.connect import LaunchProfile, MissionPrompt
from xorcise.core.rest.run_create import build_startup_tips
from xorcise.core.runs.prompt import render_prompt_text


def _mission() -> MissionPrompt:
    return MissionPrompt(
        run_id="r1",
        mission="demo",
        objective="do the thing",
        login_server="http://ls",
        join_key="jk",
        run_control_url="http://rc",
        run_control_key="rk",
    )


def test_startup_tips_carries_launch_tips():
    profile = LaunchProfile(env=(("A", "1"),))
    tips = build_startup_tips(profile, "claude -p {mission}", "solve it", ("tip-A", "tip-B"))
    assert tips.tips == ("tip-A", "tip-B")
    assert tips.command == "claude -p 'solve it'"


def test_render_prompt_text_inserts_preamble_before_objective():
    text = render_prompt_text(_mission(), preamble=("NOTE: headless sandbox.",))
    assert "NOTE: headless sandbox." in text
    assert text.index("NOTE: headless sandbox.") < text.index("Objective:")


def test_render_prompt_text_without_preamble_unchanged():
    text = render_prompt_text(_mission())
    assert "NOTE:" not in text
    assert text.startswith("Run r1 — mission: demo")
