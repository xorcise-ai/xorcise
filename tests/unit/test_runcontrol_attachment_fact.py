"""GET /runs/{id}/attachments/{name} (mint call) records a first-party 'attachment fetched' fact
(drives v2 terrain's rc:attachments / m:agent-rc). Exercises the router function directly (auth +
service monkeypatched) so it stays in the hermetic unit lane. Mirrors the prompt-fact test."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from xorcise.core.contracts.runcontrol import GetAttachmentResponse
from xorcise.core.rest.routers import runcontrol
from xorcise.core.runs.observed import SqliteObservedFactsStore

pytestmark = pytest.mark.unit


def _attachment_facts(run_id: str) -> list[str]:
    return [
        f.value
        for f in SqliteObservedFactsStore().list_for_run(run_id)
        if f.kind == "runcontrol-lifecycle" and f.name == "attachment"
    ]


def test_mint_attachment_records_fetched_fact_once(migrated_home, monkeypatch):
    rid = "r-att"
    monkeypatch.setattr(runcontrol, "require_run", lambda run_id, authorization: rid)

    class _Svc:
        def get_attachment(self, _rid: str, name: str) -> GetAttachmentResponse:
            return GetAttachmentResponse(
                name=name,
                url="/api/runs/x/attachments/a1?exp=1&sig=z",
                expires_at=datetime.now(UTC),
                media_type="text/plain",
                sha256="d" * 64,
            )

    monkeypatch.setattr(runcontrol, "_service", lambda: _Svc())

    assert _attachment_facts(rid) == []  # nothing before the mint
    runcontrol.attachment(run_id=rid, name="a1", authorization="Bearer x")
    assert _attachment_facts(rid) == ["fetched"]  # fact recorded

    runcontrol.attachment(run_id=rid, name="a1", authorization="Bearer x")  # second mint
    assert _attachment_facts(rid) == ["fetched"]  # deduped — still exactly one
