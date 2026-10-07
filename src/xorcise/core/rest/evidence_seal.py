"""Tamper-evident sealing: a digest over everything a run is graded from (rest layer).

Sealing recorded only `sealed_at` — "no more telemetry accepted after this time". That is a
lifecycle marker: it says when the evidence stopped growing and nothing about whether the bytes
still say what they said. A span edited afterwards read back clean, and a regrade could not show
it had graded the same evidence as the first pass. For a platform whose claim is "grade the
evidence, not the claim", the evidence itself has to be checkable.

LAYER. This lives in the rest layer because the digest must span three modules — raw traces and
raw logs in the otel part-island, submitted artifacts in runcontrol, observed facts in runs — and
those modules may not import each other (the dependency rule). The rest layer is the one place
allowed to read across them, so the hash is assembled here and handed to the seal store, which
stays durable storage rather than acquiring a hashing policy. Imports stay lazy for the same
reason every other finalisation path keeps them lazy: plane isolation.

WHAT THIS IS NOT. A digest detects modification; it does not prevent it, and it is not a
signature. Anyone able to edit a span can also recompute and rewrite the digest beside it. The
guarantee is that evidence cannot be altered SILENTLY — a regrade or an export can now show that
the bytes changed. Binding it to a key so the digest cannot be re-forged is the next step and is
deliberately not attempted here.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Literal, Protocol

log = logging.getLogger(__name__)

# The seal's public surface. `evidence_seal_view` is the one every display path should reach for:
# it is guarded, it resolves the stored `scheme:value` into a bare digest, and its `status` is the
# only thing that tells the five "no verdict" cases apart — `none`, `recorded`, `unverifiable`,
# `unavailable` and `unreadable` all leave `verified` None. `verify_evidence` is the narrow
# tristate for callers that only want the answer.
__all__ = [
    "EvidenceSealStatus",
    "EvidenceSealView",
    "compute_evidence_digest",
    "evidence_seal_view",
    "seal_with_digest",
    "verify_evidence",
]

# Version tag inside the hash input. A future change to what is covered (or how it is framed)
# MUST bump this, so old digests read as a different construction rather than silently comparing
# unequal and being reported as tampering.
_DIGEST_VERSION = "xorcise-evidence-v1"

# Recorded IN PLACE OF a digest when hashing failed. Without it a hashing failure and a run sealed
# before digests existed are the same NULL forever — `attach_digest` is first-wins, so nothing
# backfills — and a build that could never hash would look like a harmless backlog of old runs
# rather than a feature that has stopped working. Shaped like a scheme tag so that a reader (and
# anything that only knows the `scheme:value` split) sees a construction it does not implement
# rather than a digest; `evidence_seal_view` recognises it by name and never treats it as one.
_DIGEST_UNAVAILABLE = "xorcise-evidence-unavailable"

# How much of a hashing failure's message is worth keeping beside the sentinel. The reason is
# stored and never rendered, so nothing downstream trims it, and `str(exc)` on what this catches is
# not a one-liner — a SQLAlchemy error carries its whole statement and its parameters.
_REASON_MAX = 120


class _Hasher(Protocol):
    """What `_feed` needs of a hash object — `hashlib._Hash` is a private name, not an API."""

    def update(self, data: bytes, /) -> None: ...


def _feed(h: _Hasher, *parts: str) -> None:
    """Append length-prefixed fields.

    Length prefixes, not separators: with a plain delimiter, evidence whose content happens to
    contain that delimiter could be re-partitioned into different fields that hash identically.
    Prefixing each field with its length makes the framing unambiguous.
    """
    for part in parts:
        raw = part.encode("utf-8", errors="surrogatepass")
        h.update(f"{len(raw)}:".encode())
        h.update(raw)


def compute_evidence_digest(run_id: str) -> str:
    """SHA-256 over the run's evidence, in a canonical order. Pure w.r.t. stored state.

    Covers the four things a grade is derived from — raw spans, raw logs, submitted artifacts and
    observed facts — each read in its own canonical order (`(seq, payload)` for telemetry, the
    store's recorded order for submissions, `(kind, name)` for facts) so the same evidence always
    hashes the same, whatever order the rows physically sit in.

    `run_id` is bound in so a digest cannot be lifted from one run and presented for another.
    Server-side receipt metadata is excluded on purpose — `TraceRow.created_at` is when XORCISE
    received a batch, not something the agent did — so re-ingesting the same evidence still
    verifies. That exclusion is why the seal claims only that the GRADED EVIDENCE is unchanged:
    a report line derived from stored timestamps can move while this still verifies.

    Reads the two covered columns rather than materialising `TraceRecord`s: the report re-hashes on
    every request, and building pydantic models for a 20k-batch run was ~95% of the cost of a hash
    that then discards `received_at` anyway.
    """
    from xorcise.core.otel.store import SqliteLogStore, SqliteTraceStore
    from xorcise.core.runcontrol.store import SqliteSubmissionStore
    from xorcise.core.runs.observed import SqliteObservedFactsStore

    h = hashlib.sha256()
    _feed(h, _DIGEST_VERSION, run_id)

    for label, store in (("trace", SqliteTraceStore()), ("log", SqliteLogStore())):
        # Streamed straight into the hasher, so a 66 MB run is never held in memory to be hashed.
        # The record count is still inside the digest — it just lands AFTER the records rather
        # than before them, which is the price of not buying the list to count it. Framing stays
        # unambiguous either way: every field is length-prefixed, including this one.
        count = 0
        _feed(h, label)
        for seq, payload in store.ordered_payloads(run_id):
            _feed(h, str(seq), payload)
            count += 1
        _feed(h, f"{label}-count", str(count))

    submissions = SqliteSubmissionStore().list_for_run(run_id)
    _feed(h, "submission", str(len(submissions)))
    for sub in submissions:
        _feed(h, sub.kind, sub.name, sub.payload)

    # Observed facts are GRADED — grade_assembly feeds them into SealedContext and deterministic
    # checks resolve against them — so evidence that decides a score has to be inside the seal.
    # Sorted by name: the store's order is not a guarantee, and the digest must be reproducible.
    facts = sorted(SqliteObservedFactsStore().list_for_run(run_id), key=lambda f: (f.kind, f.name))
    _feed(h, "observed", str(len(facts)))
    for fact in facts:
        _feed(h, fact.kind, fact.name, str(fact.value))

    return h.hexdigest()


def _unavailable_reason(exc: BaseException) -> str:
    """One bounded line to record beside the UNAVAILABLE sentinel.

    The whole of `str(exc)` used to go into the column verbatim, and the failures this catches are
    multi-line: a SQLAlchemy error brings its statement and its parameters with it. The first line
    carries what the operator needs — "database is locked" told apart from a missing import — and
    the type name is the fallback for an exception that says nothing at all (`raise KeyError()`),
    which would otherwise record the bare sentinel and reinstate the NULL it exists to replace.
    """
    lines = str(exc).strip().splitlines()
    reason = lines[0].strip() if lines else ""
    return reason[:_REASON_MAX] if reason else type(exc).__name__


def seal_with_digest(run_id: str) -> None:
    """Seal the run, recording the digest of what it is being sealed over. Idempotent.

    Best-effort on the digest ONLY: if hashing fails, the run is still sealed, because refusing to
    seal would leave telemetry admissible after terminal — a correctness regression traded for a
    hardening feature. A run then verifies as `None` (unknown), never as tampered.

    A failure records the UNAVAILABLE sentinel rather than leaving the column NULL, and logs at
    ERROR rather than WARNING. Left NULL, a hashing failure is indistinguishable from a run sealed
    before digests existed — for good, since `attach_digest` is first-wins and nothing backfills —
    so an import error or a lock hit on every finalisation would quietly make every new run
    unverifiable while the reports stayed silent about it.
    """
    from xorcise.core.otel.store import SqliteSealStore

    seals = SqliteSealStore()
    # Close admission BEFORE hashing. Hashing first meant anything the receiver admitted while the
    # digest was being computed was hashed out of existence, and the freshly sealed run verified
    # False immediately — the seal accusing itself. `seal()` is first-wins and idempotent, so this
    # is safe to reach twice.
    #
    # This narrows the window rather than closing it completely: a request that already passed
    # `is_sealed()` can still land while we hash. That is a receiver-level race needing a barrier,
    # not something ordering alone can fix — but it is now bounded by requests genuinely in flight
    # at the moment of sealing, instead of by however long hashing takes.
    seals.seal(run_id)
    try:
        digest = compute_evidence_digest(run_id)
    except Exception as exc:  # noqa: BLE001 — sealing must not depend on hashing succeeding
        log.error("evidence digest failed for %s; sealing unverifiable", run_id, exc_info=True)
        seals.attach_digest(run_id, f"{_DIGEST_UNAVAILABLE}:{_unavailable_reason(exc)}")
        return
    # Tagged with the construction that produced it, so a future change to what is covered reads
    # as a different scheme rather than as tampering.
    seals.attach_digest(run_id, f"{_DIGEST_VERSION}:{digest}")


# Everything a reader can be told about a run's seal, as ONE value. `verified` alone is a tristate
# whose None lumps five different situations together — and they are not the same situation: a run
# that predates the feature is rightly silent, a run whose hash FAILED at seal time is the feature
# breaking, and a seal row we could not read right now is this request failing. Collapsing them is
# what left `/result` answering `null/null` to all three.
#
#   none         nothing recorded — the run is unsealed, or was sealed before digests existed
#   recorded     a digest is on file; THIS read did not re-check it (the cheap /result path)
#   verified     re-hashed here, and the graded evidence still matches
#   mismatch     re-hashed here, and it does not — the only value that is ever an accusation
#   unverifiable a digest is on file this build cannot check: a scheme it does not implement, or
#                the re-hash failed on this request
#   unavailable  sealing could not hash the evidence at all, so there is nothing to check against
#   unreadable   the seal row itself could not be read on this request
EvidenceSealStatus = Literal[
    "none",
    "recorded",
    "verified",
    "mismatch",
    "unverifiable",
    "unavailable",
    "unreadable",
]


@dataclass(frozen=True)
class EvidenceSealView:
    """What a display surface needs to say about a run's seal, already resolved and guarded."""

    # The BARE hex digest — never the stored `scheme:hex` value. Callers short-form this for
    # display, and handing them the stored string printed 16 characters of the scheme tag: the
    # same constant on every report, which compares equal by eye no matter what changed.
    digest: str | None = None
    # True / False / None, where None is "this read did not answer" — not checked, no digest
    # recorded, a scheme this build cannot re-derive, or the re-hash itself failed. Never False on
    # any of those; `status` is what tells them apart.
    verified: bool | None = None
    # Which of the seven situations above this is. The field callers should branch on.
    status: EvidenceSealStatus = "none"

    @property
    def unavailable(self) -> bool:
        """Sealing recorded that it could not hash the evidence. Derived, so it cannot drift from
        `status`; kept as a name because the report renders this case with its own wording."""
        return self.status == "unavailable"

    @property
    def unreadable(self) -> bool:
        """The seal row itself could not be read on this request, so nothing else on this view
        means anything. Derived for the same reason `unavailable` is, and named for the same one:
        the report has its own sentence for it, and it is about the read, not about the run."""
        return self.status == "unreadable"


def evidence_seal_view(run_id: str, *, verify: bool = True) -> EvidenceSealView:
    """Read the recorded digest and, by default, re-verify against it. Never raises.

    GUARDED, unlike the bare calls it replaces. `GET /report` and `GET /result` reach this on every
    request, and it is the only join in those handlers that touches the evidence tables — the same
    shared SQLite file the gate, the budget watchdog and the REST handlers write to, where
    "database is locked" is a documented reality (#107). Unwrapped, a transient lock turned one
    missing report row into a 500 on the whole report; a bulk export that fetches a report per run
    inherits one re-hash each, so the failure mode is not rare.

    Verification is done HERE, at read time, not read from a verdict stored at seal time: a stored
    verdict would only ever say "matched when we wrote it", which is the one thing never in doubt.

    `verify=False` reads the seal ROW ONLY — one indexed lookup, no hash. The verdict costs the
    whole of a run's evidence through SHA-256 (3.3 ms per MB here), and the surfaces that fetch one
    result per run in a loop never show it: `run list`, the leaderboard roll-up and the results
    table would each have paid it on every row. The digest still comes back, because the row read
    already has it, and `status` says plainly that it was not checked.
    """
    from xorcise.core.otel.store import SqliteSealStore

    try:
        recorded = SqliteSealStore().evidence_digest(run_id)
    except Exception:  # noqa: BLE001 — a display join must never fail the request
        log.warning("could not read the evidence seal for %s", run_id, exc_info=True)
        # NOT the same empty view as an unsealed run: that one is silence, and this is a failure
        # happening now, which the reader has to be able to tell from a run that has no seal.
        return EvidenceSealView(status="unreadable")
    if not recorded:
        return EvidenceSealView(status="none")
    scheme, _, digest = recorded.partition(":")
    if scheme == _DIGEST_UNAVAILABLE:
        return EvidenceSealView(status="unavailable")
    if not digest or scheme != _DIGEST_VERSION:
        # Written by a construction this build does not implement. Unknown — never False: an old
        # seal we cannot re-derive is not evidence of tampering, and saying so would be the
        # loudest possible false accusation. The digest still comes back: dropping it made the
        # report's seal row (gated on having one) vanish entirely, which is the same silence as a
        # run that predates the feature — and the hex is still worth comparing by eye between two
        # reports of the same run, which is all the short form was ever for.
        return EvidenceSealView(digest=digest or None, status="unverifiable")
    if not verify:
        return EvidenceSealView(digest=digest, status="recorded")
    try:
        matches = compute_evidence_digest(run_id) == digest
    except Exception:  # noqa: BLE001 — could not verify is not an accusation
        log.warning("could not verify the evidence seal for %s", run_id, exc_info=True)
        return EvidenceSealView(digest=digest, status="unverifiable")
    return EvidenceSealView(
        digest=digest, verified=matches, status="verified" if matches else "mismatch"
    )


def verify_evidence(run_id: str) -> bool | None:
    """Does the run's GRADED EVIDENCE still match the digest taken when it was sealed?

    True / False / **None**, and the third is the one that matters. None covers every case we
    cannot answer: no digest recorded (the run is unsealed, or predates this), a scheme this build
    cannot re-derive, sealing having failed to hash at all, the seal row being unreadable on this
    request, or the re-hash failing now. Collapsing any of those into False would report an
    untouched run as tampered, which is the loudest possible false accusation and would make the
    signal worthless.

    Always re-hashes — a caller asking this question wants an answer, not a row read. Callers that
    need to tell those Nones apart read `evidence_seal_view` and branch on its `status`; there are
    five of them, and this function cannot distinguish them by construction.
    """
    return evidence_seal_view(run_id).verified
