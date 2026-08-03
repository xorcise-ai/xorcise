"""GET /runs/{id}/mission records a first-party 'brief fetched' fact (drives v2 terrain's
rc:prompt / m:agent-rc). Exercises the router function directly (auth + service monkeypatched) so
it stays in the hermetic unit lane."""

from __future__ import annotations

import pytest

from xorcise.core.contracts.runcontrol import MissionInfo
from xorcise.core.rest.routers import runcontrol
from xorcise.core.runs.observed import SqliteObservedFactsStore

pytestmark = pytest.mark.unit


def _prompt_facts(run_id: str) -> list[str]:
    return [
        f.value
        for f in SqliteObservedFactsStore().list_for_run(run_id)
        if f.kind == "runcontrol-lifecycle" and f.name == "prompt"
    ]


def test_get_mission_records_prompt_fetched_fact_once(migrated_home, monkeypatch):
    rid = "r-brief"
    monkeypatch.setattr(runcontrol, "require_run", lambda run_id, authorization: rid)

    class _Svc:
        def get_mission(self, _rid: str) -> MissionInfo:
            return MissionInfo(mission="c1", objective="pop it")

    monkeypatch.setattr(runcontrol, "_service", lambda: _Svc())

    assert _prompt_facts(rid) == []  # nothing before the fetch
    info = runcontrol.get_mission(run_id=rid, authorization="Bearer x")
    assert info.mission == "c1"  # the brief is still served
    assert _prompt_facts(rid) == ["fetched"]  # fact recorded

    runcontrol.get_mission(run_id=rid, authorization="Bearer x")  # second fetch
    assert _prompt_facts(rid) == ["fetched"]  # deduped — still exactly one
