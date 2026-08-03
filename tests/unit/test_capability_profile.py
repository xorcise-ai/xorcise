"""HarnessCapabilityProfile contract: totality over AgentEventKind + partial-needs-note."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.agent_event import (
    AgentEventKind,
    HarnessCapabilityProfile,
    KindSupport,
)


def _total_kinds(**overrides: KindSupport) -> dict[str, KindSupport]:
    kinds = {k.value: KindSupport.unsupported for k in AgentEventKind}
    kinds.update({k: v for k, v in overrides.items()})
    return kinds


def test_profile_accepts_a_total_kind_map() -> None:
    profile = HarnessCapabilityProfile(
        adapter_name="claude-code",
        adapter_version="1",
        kinds=_total_kinds(message=KindSupport.supported),
    )
    assert profile.kinds["message"] is KindSupport.supported
    assert profile.verified is True


def test_profile_rejects_a_missing_kind() -> None:
    kinds = _total_kinds()
    del kinds[AgentEventKind.thinking.value]
    with pytest.raises(ValidationError, match="thinking"):
        HarnessCapabilityProfile(adapter_name="x", adapter_version="1", kinds=kinds)


def test_profile_rejects_an_unknown_kind_key() -> None:
    kinds = _total_kinds()
    kinds["telepathy"] = KindSupport.supported
    with pytest.raises(ValidationError, match="telepathy"):
        HarnessCapabilityProfile(adapter_name="x", adapter_version="1", kinds=kinds)


def test_partial_requires_a_note() -> None:
    kinds = _total_kinds(message=KindSupport.partial)
    with pytest.raises(ValidationError, match="partial.*note"):
        HarnessCapabilityProfile(adapter_name="codex", adapter_version="1", kinds=kinds)
    note = "User prompts only — Codex CLI does not export agent-authored chat messages."
    ok = HarnessCapabilityProfile(
        adapter_name="codex",
        adapter_version="1",
        kinds=kinds,
        notes={"message": note},
    )
    assert ok.kinds["message"] is KindSupport.partial
