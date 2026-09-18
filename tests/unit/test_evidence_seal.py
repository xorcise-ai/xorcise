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


# ── review round two (#139) ──────────────────────────────────────────────────────────────────


def test_hashing_never_materialises_trace_records(migrated_home, monkeypatch) -> None:
    """The report re-hashes on every GET, and ~95% of that went on `store.read()` building pydantic
    `TraceRecord`s whose `received_at` the digest then throws away. Hashing must read the two
    columns it covers, so the cost is the SHA-256 and not an ORM round trip."""
    from xorcise.core.otel.store import SqliteLogStore, SqliteTraceStore
    from xorcise.core.rest.evidence_seal import compute_evidence_digest

    _seed("r1")

    def forbidden(self: object, run_id: str) -> list[TraceRecord]:
        raise AssertionError("the digest materialised TraceRecords instead of reading columns")

    monkeypatch.setattr(SqliteTraceStore, "read", forbidden)
    monkeypatch.setattr(SqliteLogStore, "read", forbidden)

    assert compute_evidence_digest("r1")


def test_duplicate_seq_hashes_the_same_whatever_order_the_rows_arrived(migrated_home) -> None:
    """Ingest assigns `seq = len(read(run_id))` non-atomically and nothing makes `(run_id, seq)`
    unique, so two concurrent OTLP posts can collide. Sorting on `seq` alone then leaves the order
    to whatever the rows happen to sit in — stable today via rowid, but a dump/restore that
    reassigns ids flips it and an untouched run reads as tampered."""
    from xorcise.core.db import session_scope
    from xorcise.core.otel.store import SqliteTraceStore
    from xorcise.core.otel.store.models import TraceRow
    from xorcise.core.rest.evidence_seal import compute_evidence_digest

    store = SqliteTraceStore()
    store.append(_record("r1", 1, '{"span":"alpha"}'))
    store.append(_record("r1", 1, '{"span":"beta"}'))
    first = compute_evidence_digest("r1")

    # Re-insert the same two payloads in the opposite order, as a dump/restore would.
    with session_scope() as s:
        s.query(TraceRow).filter_by(run_id="r1").delete()
    store.append(_record("r1", 1, '{"span":"beta"}'))
    store.append(_record("r1", 1, '{"span":"alpha"}'))

    assert compute_evidence_digest("r1") == first


def test_an_edit_outside_the_hash_does_not_trip_verification(migrated_home) -> None:
    """The digest deliberately excludes server-side receipt metadata. That exclusion has to be
    real: touching `created_at` must not read as tampering, or the signal cries wolf."""
    from datetime import UTC, datetime

    from xorcise.core.db import session_scope
    from xorcise.core.otel.store.models import TraceRow
    from xorcise.core.rest.evidence_seal import seal_with_digest, verify_evidence

    _seed("r1")
    seal_with_digest("r1")

    with session_scope() as s:
        row = s.query(TraceRow).filter_by(run_id="r1", seq=1).one()
        row.created_at = datetime(2001, 1, 1, tzinfo=UTC)

    assert verify_evidence("r1") is True


def test_the_digest_is_the_same_in_a_separate_process(migrated_home) -> None:
    """Determinism has to hold ACROSS processes, not just within one: a regrade, an export and the
    report all run in different interpreters from the one that sealed. Anything iteration-order or
    hash-randomisation dependent would pass the in-process test and fail here."""
    import subprocess
    import sys

    from xorcise.core.rest.evidence_seal import compute_evidence_digest

    _seed("r1")
    here = compute_evidence_digest("r1")

    out = subprocess.run(  # noqa: S603 — fixed argv, this interpreter
        [
            sys.executable,
            "-c",
            "from xorcise.core.rest.evidence_seal import compute_evidence_digest;"
            "print(compute_evidence_digest('r1'))",
        ],
        capture_output=True,
        text=True,
        env={"XORCISE_HOME": str(migrated_home), "PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "1"},
        check=True,
    )

    assert out.stdout.strip() == here


def test_a_hashing_failure_is_told_apart_from_a_run_that_predates_digests(
    migrated_home, monkeypatch, caplog
) -> None:
    """A failed hash logged one WARNING and left the column NULL — indistinguishable, forever, from
    a run sealed before digests existed, because `attach_digest` is first-wins and nothing
    backfills. An import error or a lock at every finalisation would quietly make every new run
    unverifiable and the report would say nothing at all."""
    import logging

    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest import evidence_seal

    _seed("r1")

    def boom(run_id: str) -> str:
        raise RuntimeError("no hash for you")

    monkeypatch.setattr(evidence_seal, "compute_evidence_digest", boom)
    with caplog.at_level(logging.ERROR):
        evidence_seal.seal_with_digest("r1")

    assert SqliteSealStore().is_sealed("r1"), "sealing must not depend on hashing"
    recorded = SqliteSealStore().evidence_digest("r1")
    assert recorded and recorded.startswith(evidence_seal._DIGEST_UNAVAILABLE)
    assert any(r.levelno >= logging.ERROR for r in caplog.records), "a failed hash must log ERROR"
    # Still unknown, never an accusation.
    assert evidence_seal.verify_evidence("r1") is None


def test_the_report_view_says_unavailable_rather_than_nothing(migrated_home) -> None:
    """The point of the sentinel: a post-migration run whose hash failed renders "unavailable"
    instead of looking exactly like a run that predates the feature."""
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.evidence_seal import _DIGEST_UNAVAILABLE, evidence_seal_view

    _seed("r1")
    SqliteSealStore().seal("r1", f"{_DIGEST_UNAVAILABLE}:hash failed")

    view = evidence_seal_view("r1")
    assert view.unavailable is True
    assert view.digest is None and view.verified is None


def test_the_report_view_hands_over_the_bare_digest_not_the_scheme_tag(migrated_home) -> None:
    """The stored value is `scheme:hex`; the report short-forms the first 16 characters of what it
    is given. Handing it the raw stored value printed 16 characters of the SCHEME on every report —
    the same string for every run, which compares equal by eye no matter what changed."""
    from xorcise.core.rest.evidence_seal import (
        _DIGEST_VERSION,
        compute_evidence_digest,
        evidence_seal_view,
        seal_with_digest,
    )

    _seed("r1")
    seal_with_digest("r1")

    view = evidence_seal_view("r1")
    assert view.digest == compute_evidence_digest("r1")
    assert view.verified is True
    assert not (view.digest or "").startswith(_DIGEST_VERSION)


def test_the_report_view_degrades_instead_of_raising_when_the_store_is_unreadable(
    migrated_home, monkeypatch
) -> None:
    """Every other display join in `assemble_report` is wrapped; this one was not, so a transient
    "database is locked" — documented as real on this shared file — 500'd `GET /report` instead of
    dropping one row."""
    from xorcise.core.rest import evidence_seal

    _seed("r1")
    evidence_seal.seal_with_digest("r1")

    def locked(run_id: str) -> str:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(evidence_seal, "compute_evidence_digest", locked)

    view = evidence_seal.evidence_seal_view("r1")
    assert view.digest, "the recorded digest is still readable"
    assert view.verified is None, "could not verify — never an accusation"


def test_the_result_json_carries_the_digest_and_the_verdict(migrated_home) -> None:
    """#116 asked for the digest to be SURFACED so consumers can validate. Nothing
    machine-readable carried it: `/result`, `/runs/{id}` and the GUI had neither the digest nor the
    verdict, and the only place it appeared was 16 characters of text inside a rendered report."""
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from xorcise.core import runs
    from xorcise.core.rest.evidence_seal import compute_evidence_digest
    from xorcise.core.rest.run_terminate import terminate_run
    from xorcise.core.roles.boot.role_all import build_rest_app

    run = runs.create_run(agent_id="a1", mission="m1", budget_seconds=60)
    _seed(run.run_id)
    terminate_run(run.run_id, "done", datetime(2026, 9, 17, tzinfo=UTC))

    body = TestClient(build_rest_app()).get(f"/api/runs/{run.run_id}/result").json()

    assert body["evidence_digest"] == compute_evidence_digest(run.run_id)
    assert body["evidence_verified"] is True


def test_grading_warns_loudly_before_it_scores_evidence_that_moved(migrated_home, caplog) -> None:
    """A regrade re-scores ALREADY-SEALED evidence. If that evidence no longer matches its seal,
    the new grade is derived from something other than what was sealed — and that has to be in the
    log at the moment it happens, not only in a report someone may never open."""
    import logging
    from datetime import UTC, datetime

    from xorcise.core import reporting, runs
    from xorcise.core.db import session_scope
    from xorcise.core.otel.store.models import TraceRow
    from xorcise.core.rest.run_terminate import grade_and_record, terminate_run

    run = runs.create_run(agent_id="a1", mission="m1", budget_seconds=60)
    _seed(run.run_id)
    terminate_run(run.run_id, "done", datetime(2026, 9, 17, tzinfo=UTC))

    with session_scope() as s:
        s.query(TraceRow).filter_by(run_id=run.run_id, seq=1).one().payload = '{"span":"forged"}'

    reporting.delete_result(run.run_id)  # what POST /runs/{id}/regrade does first
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        grade_and_record(run.run_id)

    assert any(
        "no longer matches" in r.getMessage() and r.levelno >= logging.WARNING
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]
