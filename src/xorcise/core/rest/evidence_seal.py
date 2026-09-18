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
from typing import Protocol

log = logging.getLogger(__name__)

# Version tag inside the hash input. A future change to what is covered (or how it is framed)
# MUST bump this, so old digests read as a different construction rather than silently comparing
# unequal and being reported as tampering.
_DIGEST_VERSION = "xorcise-evidence-v1"

# Recorded IN PLACE OF a digest when hashing failed. Without it a hashing failure and a run sealed
# before digests existed are the same NULL forever — `attach_digest` is first-wins, so nothing
# backfills — and a build that could never hash would look like a harmless backlog of old runs
# rather than a feature that has stopped working. Deliberately shaped like a scheme tag so
# `verify_evidence` reads it as "unknown construction" and still never accuses.
_DIGEST_UNAVAILABLE = "xorcise-evidence-unavailable"


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
        seals.attach_digest(run_id, f"{_DIGEST_UNAVAILABLE}:{exc}")
        return
    # Tagged with the construction that produced it, so a future change to what is covered reads
    # as a different scheme rather than as tampering.
    seals.attach_digest(run_id, f"{_DIGEST_VERSION}:{digest}")


@dataclass(frozen=True)
class EvidenceSealView:
    """What a display surface needs to say about a run's seal, already resolved and guarded."""

    # The BARE hex digest — never the stored `scheme:hex` value. Callers short-form this for
    # display, and handing them the stored string printed 16 characters of the scheme tag: the
    # same constant on every report, which compares equal by eye no matter what changed.
    digest: str | None = None
    # True / False / None, where None is "could not verify" — no digest recorded, a scheme this
    # build cannot re-derive, or the re-hash itself failed. Never False on any of those.
    verified: bool | None = None
    # Sealing recorded that it could not hash the evidence at all. Distinct from "no digest": one
    # is a run that predates the feature, the other is the feature failing.
    unavailable: bool = False


def evidence_seal_view(run_id: str) -> EvidenceSealView:
    """Read the recorded digest and re-verify against it, for the read paths. Never raises.

    GUARDED, unlike the bare calls it replaces. `GET /report` and `GET /result` reach this on every
    request, and it is the only join in those handlers that touches the evidence tables — the same
    shared SQLite file the gate, the budget watchdog and the REST handlers write to, where
    "database is locked" is a documented reality (#107). Unwrapped, a transient lock turned one
    missing report row into a 500 on the whole report; a bulk export that fetches a report per run
    inherits one re-hash each, so the failure mode is not rare.

    Verification is done HERE, at read time, not read from a verdict stored at seal time: a stored
    verdict would only ever say "matched when we wrote it", which is the one thing never in doubt.
    """
    from xorcise.core.otel.store import SqliteSealStore

    try:
        recorded = SqliteSealStore().evidence_digest(run_id)
    except Exception:  # noqa: BLE001 — a display join must never fail the request
        log.warning("could not read the evidence seal for %s", run_id, exc_info=True)
        return EvidenceSealView()
    if not recorded:
        return EvidenceSealView()
    scheme, _, digest = recorded.partition(":")
    if scheme == _DIGEST_UNAVAILABLE:
        return EvidenceSealView(unavailable=True)
    if not digest or scheme != _DIGEST_VERSION:
        # Written by a construction this build does not implement. Unknown — never False: an old
        # seal we cannot re-derive is not evidence of tampering, and saying so would be the
        # loudest possible false accusation.
        return EvidenceSealView()
    try:
        return EvidenceSealView(digest=digest, verified=compute_evidence_digest(run_id) == digest)
    except Exception:  # noqa: BLE001 — could not verify is not an accusation
        log.warning("could not verify the evidence seal for %s", run_id, exc_info=True)
        return EvidenceSealView(digest=digest)


def verify_evidence(run_id: str) -> bool | None:
    """Does the run's GRADED EVIDENCE still match the digest taken when it was sealed?

    True / False / **None**, and the third is the one that matters. None covers every case we
    cannot answer: no digest recorded (the run is unsealed, or predates this), a scheme this build
    cannot re-derive, sealing having failed to hash at all, or the re-hash failing now. Collapsing
    any of those into False would report an untouched run as tampered, which is the loudest
    possible false accusation and would make the signal worthless.
    """
    return evidence_seal_view(run_id).verified
