"""Tamper-evident sealing: a digest over everything a run is graded from (rest layer).

Sealing recorded only `sealed_at` — "no more telemetry accepted after this time". That is a
lifecycle marker: it says when the evidence stopped growing and nothing about whether the bytes
still say what they said. A span edited afterwards read back clean, and a regrade could not show
it had graded the same evidence as the first pass. For a platform whose claim is "grade the
evidence, not the claim", the evidence itself has to be checkable.

LAYER. This lives in the rest layer because the digest must span three owners — raw traces and
raw logs in the otel part-island, submitted artifacts in runcontrol — and part-islands may not
import each other (the dependency rule). The rest layer is the one place allowed to read across
them, so the hash is assembled here and handed to the seal store, which stays durable storage
rather than acquiring a hashing policy. Imports stay lazy for the same reason every other
finalisation path keeps them lazy: plane isolation.

WHAT THIS IS NOT. A digest detects modification; it does not prevent it, and it is not a
signature. Anyone able to edit a span can also recompute and rewrite the digest beside it. The
guarantee is that evidence cannot be altered SILENTLY — a regrade or an export can now show that
the bytes changed. Binding it to a key so the digest cannot be re-forged is the next step and is
deliberately not attempted here.
"""

from __future__ import annotations

import hashlib

# Version tag inside the hash input. A future change to what is covered (or how it is framed)
# MUST bump this, so old digests read as a different construction rather than silently comparing
# unequal and being reported as tampering.
_DIGEST_VERSION = "xorcise-evidence-v1"


def _feed(h: hashlib._Hash, *parts: str) -> None:
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

    Covers the three things a grade is derived from — raw spans, raw logs, submitted artifacts —
    each read in its own stable order (`seq` for telemetry, the store's recorded order for
    submissions) so the same evidence always hashes the same.

    `run_id` is bound in so a digest cannot be lifted from one run and presented for another.
    Server-side metadata (`received_at`) is excluded on purpose: it is not evidence, and including
    a value the stores populate on read would make the digest depend on how it was fetched.
    """
    from xorcise.core.otel.store import SqliteLogStore, SqliteTraceStore
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    h = hashlib.sha256()
    _feed(h, _DIGEST_VERSION, run_id)

    for label, store in (("trace", SqliteTraceStore()), ("log", SqliteLogStore())):
        records = sorted(store.read(run_id), key=lambda r: r.seq)
        _feed(h, label, str(len(records)))
        for record in records:
            _feed(h, str(record.seq), record.payload)

    submissions = SqliteSubmissionStore().list_for_run(run_id)
    _feed(h, "submission", str(len(submissions)))
    for sub in submissions:
        _feed(h, sub.kind, sub.name, sub.payload)

    return h.hexdigest()


def seal_with_digest(run_id: str) -> None:
    """Seal the run, recording the digest of what it is being sealed over. Idempotent.

    Best-effort on the digest ONLY: if hashing fails, the run is still sealed, because refusing to
    seal would leave telemetry admissible after terminal — a correctness regression traded for a
    hardening feature. A run then verifies as `None` (unknown), never as tampered.
    """
    import logging

    from xorcise.core.otel.store import SqliteSealStore

    digest: str | None = None
    try:
        digest = compute_evidence_digest(run_id)
    except Exception:  # noqa: BLE001 — sealing must not depend on hashing succeeding
        logging.getLogger(__name__).warning(
            "evidence digest failed for %s; sealing without one", run_id, exc_info=True
        )
    SqliteSealStore().seal(run_id, digest)


def verify_evidence(run_id: str) -> bool | None:
    """Does the run's evidence still match the digest taken when it was sealed?

    True / False / **None**, and the third is the one that matters: None means no digest was
    recorded — the run is unsealed, or was sealed before this existed. Collapsing that into False
    would report every pre-existing run as tampered, which is the loudest possible false
    accusation and would make the signal worthless.
    """
    from xorcise.core.otel.store import SqliteSealStore

    recorded = SqliteSealStore().evidence_digest(run_id)
    if not recorded:
        return None
    return compute_evidence_digest(run_id) == recorded
