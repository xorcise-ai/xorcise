"""The seal must be tamper-EVIDENT, not just a timestamp (#116).

Sealing recorded only `sealed_at` — "no more telemetry accepted after this time". That is a
lifecycle marker: it says when the evidence stopped growing, and nothing about whether the bytes
still say what they said. Anyone with database access could rewrite a span after the seal and no
read would notice, and a regrade could not prove it graded the same evidence as the first pass.

For a platform whose claim is "grade the evidence, not the claim", the evidence itself needs to be
checkable. These tests pin the property that matters — a changed byte changes the digest — rather
than the particular hash construction, which is free to change as long as it stays deterministic
and covers everything the judge reads.
"""

from __future__ import annotations

import pytest

from xorcise.core.contracts.telemetry import TraceRecord

pytestmark = pytest.mark.unit


def _record(run_id: str, seq: int, payload: str) -> TraceRecord:
    return TraceRecord(run_id=run_id, seq=seq, payload=payload)


def _seed(run_id: str, *, payload: str = '{"span":"recon"}') -> None:
    """One run's worth of evidence across all three sources the judge reads."""
    from xorcise.core.otel.store import SqliteLogStore, SqliteTraceStore
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    SqliteTraceStore().append(_record(run_id, 1, payload))
    SqliteLogStore().append(_record(run_id, 1, '{"log":"started"}'))
    SqliteSubmissionStore().record(run_id, "flag", "flag", "XORCISE{found}")


def test_the_digest_is_stable_for_unchanged_evidence(migrated_home) -> None:
    """Recomputing must be deterministic, or verification is useless — a digest that drifts on its
    own would cry tampering on every honest run."""
    from xorcise.core.rest.evidence_seal import compute_evidence_digest

    _seed("r1")

    assert compute_evidence_digest("r1") == compute_evidence_digest("r1")


def test_altering_a_sealed_span_changes_the_digest(migrated_home) -> None:
    """The property the whole feature exists for: edited evidence stops matching its seal."""
    from xorcise.core.rest.evidence_seal import compute_evidence_digest

    _seed("r1", payload='{"span":"recon"}')
    before = compute_evidence_digest("r1")

    # Rewrite the span in place, exactly as someone with database access could.
    from xorcise.core.db import session_scope
    from xorcise.core.otel.store.models import TraceRow

    with session_scope() as s:
        row = s.query(TraceRow).filter_by(run_id="r1", seq=1).one()
        row.payload = '{"span":"recon","flag":"XORCISE{forged}"}'

    assert compute_evidence_digest("r1") != before


def test_a_submitted_artifact_is_covered_too(migrated_home) -> None:
    """Artifacts are graded evidence — the judge reads them — so a digest over spans alone would
    leave the most directly claim-bearing part of a run unprotected."""
    from xorcise.core.rest.evidence_seal import compute_evidence_digest
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    _seed("r1")
    before = compute_evidence_digest("r1")

    SqliteSubmissionStore().record("r1", "artifact", "notes.md", "late addition")

    assert compute_evidence_digest("r1") != before


def test_two_runs_with_identical_evidence_do_not_share_a_digest(migrated_home) -> None:
    """The run id is bound in: a digest cannot be lifted from one run and shown for another."""
    from xorcise.core.rest.evidence_seal import compute_evidence_digest

    _seed("r1")
    _seed("r2")

    assert compute_evidence_digest("r1") != compute_evidence_digest("r2")


def test_sealing_records_the_digest_and_verifies_clean(migrated_home) -> None:
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.evidence_seal import seal_with_digest, verify_evidence

    _seed("r1")
    seal_with_digest("r1")

    assert SqliteSealStore().evidence_digest("r1")
    assert verify_evidence("r1") is True


def test_verification_fails_once_the_evidence_is_edited(migrated_home) -> None:
    from xorcise.core.db import session_scope
    from xorcise.core.otel.store.models import TraceRow
    from xorcise.core.rest.evidence_seal import seal_with_digest, verify_evidence

    _seed("r1")
    seal_with_digest("r1")

    with session_scope() as s:
        s.query(TraceRow).filter_by(run_id="r1", seq=1).one().payload = '{"span":"tampered"}'

    assert verify_evidence("r1") is False


def test_a_run_sealed_before_digests_existed_is_unknown_not_failed(migrated_home) -> None:
    """None, not False. Rows predate the column, and reporting an old run as TAMPERED because we
    simply never recorded a digest would be a false accusation — the loudest possible one."""
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.evidence_seal import verify_evidence

    _seed("r1")
    SqliteSealStore().seal("r1")  # the pre-#116 call shape: no digest

    assert verify_evidence("r1") is None


def test_sealing_twice_keeps_the_first_digest(migrated_home) -> None:
    """Seal is first-wins by design. If a later seal could overwrite the digest, anyone able to
    re-seal could launder edited evidence into a clean verification."""
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.evidence_seal import seal_with_digest

    _seed("r1")
    seal_with_digest("r1")
    first = SqliteSealStore().evidence_digest("r1")

    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    SqliteSubmissionStore().record("r1", "artifact", "added.md", "after the seal")
    seal_with_digest("r1")

    assert SqliteSealStore().evidence_digest("r1") == first


# ── review findings (#139) ───────────────────────────────────────────────────────────────────


def test_editing_an_observed_fact_breaks_verification(migrated_home) -> None:
    """Observed facts are GRADED — `grade_assembly` feeds them into SealedContext and deterministic
    checks resolve against them — but the digest never covered them, so altering one left the run
    verifying clean. Evidence that decides a score has to be inside the seal."""
    from xorcise.core.contracts.telemetry import ObservedFact
    from xorcise.core.db import session_scope
    from xorcise.core.rest.evidence_seal import seal_with_digest, verify_evidence
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    _seed("r1")
    SqliteObservedFactsStore().record(
        ObservedFact(run_id="r1", kind="run-control", name="flag_seen", value="no")
    )
    seal_with_digest("r1")
    assert verify_evidence("r1") is True

    from xorcise.core.runs.models import RunObservedFactRow

    with session_scope() as s:
        s.query(RunObservedFactRow).filter_by(run_id="r1", name="flag_seen").one().value = "yes"

    assert verify_evidence("r1") is False


def test_admission_is_closed_before_the_evidence_is_hashed(migrated_home, monkeypatch) -> None:
    """The digest was taken BEFORE `seal()`, so anything admitted in between was hashed out of
    existence and the freshly sealed run verified False immediately. Sealing must close the door
    first, then hash what is behind it."""
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest import evidence_seal

    _seed("r1")
    order: list[str] = []
    real_compute = evidence_seal.compute_evidence_digest
    real_seal = SqliteSealStore.seal

    def spy_compute(run_id: str) -> str:
        order.append("hash")
        return real_compute(run_id)

    def spy_seal(self: SqliteSealStore, run_id: str, digest: str | None = None) -> None:
        order.append("seal")
        real_seal(self, run_id, digest)

    monkeypatch.setattr(evidence_seal, "compute_evidence_digest", spy_compute)
    monkeypatch.setattr(SqliteSealStore, "seal", spy_seal)

    evidence_seal.seal_with_digest("r1")

    assert order[0] == "seal", f"hashed before closing admission: {order}"


def test_a_digest_from_an_unknown_scheme_reads_unknown_not_tampered(migrated_home) -> None:
    """Only the hex was stored, so verification always recomputed with the CURRENT construction,
    and bumping the version would report every untouched older run as modified. The stored value
    has to say which scheme produced it; an unrecognised one is unknown, never an accusation."""
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.evidence_seal import verify_evidence

    _seed("r1")
    SqliteSealStore().seal("r1", "xorcise-evidence-v99:" + "a" * 64)

    assert verify_evidence("r1") is None


def test_the_stored_digest_records_its_scheme(migrated_home) -> None:
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.evidence_seal import _DIGEST_VERSION, seal_with_digest

    _seed("r1")
    seal_with_digest("r1")

    stored = SqliteSealStore().evidence_digest("r1") or ""
    assert stored.startswith(f"{_DIGEST_VERSION}:")
