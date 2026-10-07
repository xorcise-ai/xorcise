"""Real free-library catalog client (PART-ISLAND). Imports only contracts + kernel + httpx.

Fulfils the CatalogSource seam against the live catalog API:
  GET  /v1/catalog                     → browse list
  GET  /v1/missions/{id}             → {manifest, image_ref}
  POST /v1/missions/{id}/pull-token  → {registry, username, password, image_ref, expires_at}
  GET  /v1/announcements/active        → {announcements: [...]} (404 on an older deployment)
The pull-token broker mints short-lived, single-repo ECR creds, so the client needs no static
AWS credentials (published = pullable).
"""

from __future__ import annotations

import json
import logging
import time

import httpx
from pydantic import ValidationError

from xorcise.core.catalog.source import (
    CatalogSource,
    DeliveryBundle,
    LibraryItem,
    MissionBaseRelease,
    MissionDetail,
    PlatformImage,
    PullToken,
)
from xorcise.core.contracts.announcements import (
    MAX_BODY_CHARS,
    MAX_ID_CHARS,
    PLACEMENTS,
    TONES,
    Announcement,
)
from xorcise.core.contracts.catalog import CatalogStatus
from xorcise.core.contracts.errors import (
    NotFoundError,
    PullError,
    UnsupportedManifestVersionError,
)
from xorcise.core.contracts.mission import SUPPORTED_SCHEMA_VERSIONS, MissionManifest

log = logging.getLogger(__name__)

_TIMEOUT = 10.0
_DOWNLOAD_TIMEOUT = 60.0  # attachment bundles (pcaps, binaries) can be larger than JSON
# Deliberately shorter than _TIMEOUT: announcements are fetched on the PAGE-LOAD path, and a
# banner nobody has published yet is not worth ten seconds of a blank app.
_ANNOUNCEMENTS_TIMEOUT = 3.0
# How many rows we are willing to look at. The contract allows at most one announcement per
# placement, i.e. two, so anything approaching this is already a server defect — the bound
# exists so the REMOTE cannot choose how much work the local app does on its page-load path.
# Comfortably above any legitimate response, so it can never truncate a well-formed one.
_MAX_ANNOUNCEMENT_ROWS = 8
# How many BYTES OFF THE WIRE we are willing to read, counted while streaming. The row and
# string limits below cannot do this job: they run on an object that is already in memory, so on
# their own they bound what we keep and not what the remote costs us — one valid announcement
# beside an ignored 50 MB field was read in full and served. A maximal legitimate response is two
# rows of a 600-character body, comfortably under 2 KiB, so this is ~30x any real one.
#
# WIRE bytes is the whole point, and the identity refusal is what makes the count one: reading
# with `iter_bytes` runs the content decoder first, so a counter behind it measures what the
# REMOTE chose to expand to, not what it sent. Measured against a gzipped body, 407,698 bytes on
# the wire produced a 33,578,960-byte first chunk — a 512x overshoot already allocated before any
# limit could look at it. The request asks for `identity` and `_read_bounded` refuses a body that
# arrives encoded anyway, which leaves the decoder an identity decoder and the two counts equal.
# (`iter_raw` would make that structural rather than conditional; see `_read_bounded` for why it
# cannot be used here.)
_MAX_ANNOUNCEMENT_BYTES = 64 * 1024
# An OVERALL wall-clock budget for the whole call. `timeout=` is not one: HTTPX's timeout is
# PER-OPERATION (per connect, per read), so a remote that sends a byte just inside it holds
# this page-load call open for as long as it likes — a server trickling chunks 1.5 s apart ran
# for 13.5 s against the 3 s "timeout" above. This is the bound that actually ends the call.
#
# What it promises, precisely, because the earlier wording here claimed more than it holds:
# the deadline is tested once the response HEADERS are in and then between body reads, and a
# read already in flight cannot be interrupted. So from the first byte of the body onward the
# call returns within the deadline plus at most one _ANNOUNCEMENTS_TIMEOUT.
#
# It does NOT bound the connect-and-headers phase, and nothing synchronous here can: every
# individual read is inside _ANNOUNCEMENTS_TIMEOUT, so a remote dribbling one header just under
# it is never late by httpx's reckoning and never reaches a check of ours. Measured at 22.5 s
# with one header every 2.5 s, rising linearly with header count (h11 caps total header BYTES at
# ~16 KiB, which bounds the data but not the time). Tracked separately; a true total bound needs
# a watchdog outside the calling thread, which costs more on a page-load path than it buys.
_ANNOUNCEMENTS_DEADLINE = 5.0


class HttpCatalogSource(CatalogSource):
    def __init__(self, base_url: str, *, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=_TIMEOUT)

    def list_library(self) -> tuple[LibraryItem, ...]:
        try:
            resp = self._client.get(f"{self._base}/v1/catalog")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise PullError(f"catalog list failed: {exc}") from exc
        return tuple(_to_item(row) for row in resp.json().get("catalog", []))

    def fetch_manifest(self, mission_id: str) -> MissionManifest:
        return self.fetch_detail(mission_id).manifest

    def fetch_detail(self, mission_id: str) -> MissionDetail:
        """One GET serves both the manifest and the artifact-identity siblings.

        A pre-contract deployment (prod today) sends only {manifest, image_ref}; every
        identity field degrades to None/empty and the caller behaves exactly as before."""
        resp = self._client.get(f"{self._base}/v1/missions/{mission_id}")
        if resp.status_code == 404:
            raise NotFoundError(mission_id)
        resp.raise_for_status()
        body = resp.json()
        manifest = self._validate_manifest(mission_id, body.get("manifest"))
        image = body.get("image") if isinstance(body.get("image"), dict) else {}
        base = body.get("mission_base") if isinstance(body.get("mission_base"), dict) else {}
        digests = base.get("platform_digests")
        return MissionDetail(
            manifest=manifest,
            mission_version=_opt_str(body.get("mission_version")),
            mission_base_version=_opt_str(body.get("mission_base_version")),
            content_hash=_opt_str(body.get("content_hash")),
            pull_ref=_opt_str(image.get("pull_ref")),
            release_ref=_opt_str(image.get("release_ref")),
            index_digest=_opt_str(image.get("index_digest")),
            platforms=_platform_images(image.get("platforms")),
            base_index_digest=_opt_str(base.get("index_digest")),
            base_platform_digests=(
                {str(k): str(v) for k, v in digests.items() if v is not None}
                if isinstance(digests, dict)
                else {}
            ),
        )

    def _validate_manifest(self, mission_id: str, payload: object) -> MissionManifest:
        try:
            return MissionManifest.model_validate(payload)
        except ValidationError as exc:
            # Typed, actionable — never a pydantic traceback. The overwhelmingly likely cause is
            # a catalog serving a manifest schema newer than this client (the cloud moved first);
            # the remedy in both arms is the same: upgrade XORCISE.
            raw = payload.get("schema_version") if isinstance(payload, dict) else None
            served = raw if isinstance(raw, str) else None
            if served not in SUPPORTED_SCHEMA_VERSIONS:
                raise UnsupportedManifestVersionError(
                    f"mission '{mission_id}' serves manifest schema {served!r}; this XORCISE "
                    f"reads {', '.join(SUPPORTED_SCHEMA_VERSIONS)} — upgrade XORCISE "
                    "(e.g. pip install -U xorcise) to pull it",
                    served=served,
                    supported=SUPPORTED_SCHEMA_VERSIONS,
                ) from exc
            raise UnsupportedManifestVersionError(
                f"mission '{mission_id}' serves a schema {served} manifest this XORCISE cannot "
                f"validate ({exc.error_count()} field error(s)) — the catalog and this client "
                "disagree about the shape; upgrading XORCISE may resolve it",
                served=served,
                supported=SUPPORTED_SCHEMA_VERSIONS,
            ) from exc

    def mission_base(self) -> MissionBaseRelease | None:
        """GET /v1/mission-base — 404 (a pre-contract deployment) and any transport failure
        both degrade to None: the promoted base is display/diagnostic data, and its absence
        must never take a settings page or doctor run down."""
        try:
            resp = self._client.get(f"{self._base}/v1/mission-base")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            return None
        version = _opt_str(body.get("mission_base_version"))
        if version is None:
            return None
        image = body.get("image") if isinstance(body.get("image"), dict) else {}
        raw_major = body.get("required_base_major")
        return MissionBaseRelease(
            version=version,
            required_base_major=int(raw_major) if isinstance(raw_major, int) else None,
            ref=_opt_str(image.get("ref")),
            index_digest=_opt_str(image.get("index_digest")),
            platforms=_platform_images(image.get("platforms")),
        )

    def announcements(self) -> tuple[Announcement, ...]:
        """GET /v1/announcements/active — the active banners, or () on the failures this
        method can see: a transport error, a timeout, a non-2xx status, a 404, an undecodable
        body, or a payload whose shape or items are wrong.

        That is NOT "any failure at all", and the narrow `except (httpx.HTTPError, ValueError)`
        below is deliberate. Anything outside those two still escapes — `httpx.InvalidURL`
        derives from Exception, not HTTPError, so a malformed `XORCISE_CATALOG_URL` (try
        "::::") propagates straight out of here. What makes the no-failure-reaches-the-browser
        guarantee TOTAL is the broad `except Exception` in
        `rest/announcements_view.list_announcements`, which is the boundary the router calls.
        Narrow here and broad at the boundary is the right shape and each covers what the
        other does not: this method absorbs the failures that are EXPECTED of a remote call,
        so they stay quiet, while a genuinely unexpected bug still travels up to the one place
        that logs it as unexpected. Do not widen this catch, and do not delete the view's as
        redundant.

        Same degrade shape as mission_base(), and for a stronger reason: this runs on the
        page-load path, so nothing it can do may keep the app from rendering. 404 is the
        normal answer from every deployment that predates the feature, an offline laptop is
        the normal state of a local install, and a server that grew a field is expected —
        none of those is an error here.

        Logging splits along "whose defect is it": a transport failure is DEBUG (an offline
        laptop would otherwise WARN on every single page load, which teaches operators to
        ignore the log), while a response that parsed as JSON and was still the wrong shape,
        an item that had to be dropped, or a response that blew one of the bounds below, is a
        SERVER-side defect an operator should see — one warning, not one per item.

        The response is STREAMED rather than fetched whole, because every limit in this method
        is worthless if it only runs on an object that is already in memory. Two bounds are
        applied while the bytes are still arriving: a cap on WIRE bytes, and a wall-clock
        deadline covering the body. Neither is complete on its own — see `_read_bounded` for what
        each one does and does not cover, and `_ANNOUNCEMENTS_DEADLINE` for the phase the
        deadline cannot reach.
        """
        deadline = time.monotonic() + _ANNOUNCEMENTS_DEADLINE
        try:
            with self._client.stream(
                "GET",
                f"{self._base}/v1/announcements/active",
                timeout=_ANNOUNCEMENTS_TIMEOUT,
                # So the byte cap counts the same bytes the remote sent. httpx advertises gzip
                # by default, which would let the remote pick how far past the cap one chunk
                # expands; `_read_bounded` refuses a body that comes back encoded regardless.
                headers={"Accept-Encoding": "identity"},
            ) as resp:
                if resp.status_code == 404:
                    return ()  # a deployment that predates announcements — normal, not an error
                resp.raise_for_status()
                if time.monotonic() > deadline:
                    # The headers alone outlasted the budget. Checked here because the body loop
                    # below would otherwise start afresh on an already-spent deadline.
                    log.warning(
                        "catalog took longer than %.0fs to answer with its announcements; "
                        "the fetch was abandoned",
                        _ANNOUNCEMENTS_DEADLINE,
                    )
                    return ()
                raw = _read_bounded(resp, deadline)
            if raw is None:
                return ()  # over a bound; `_read_bounded` has already said which
            # json.loads rather than resp.json(): the body is already in hand as bytes, and
            # going back through the response would mean reading a stream that is now closed.
            # Both decode UTF-8 JSON identically, which is the only thing this endpoint serves.
            body = json.loads(raw)
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError covers a JSON decode failure (a captive portal serving HTML);
            # json.JSONDecodeError is a subclass of it.
            log.debug("announcements fetch failed: %s", exc)
            return ()

        rows = body.get("announcements") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            log.warning("catalog served an unexpected announcements shape; ignoring it")
            return ()

        # Bound the work BEFORE the loop: a legitimate response is at most two rows, so this
        # can never truncate a well-formed one, and the first-wins rule below is untouched by
        # it. Slicing rather than iterating whatever arrives keeps a compromised or simply
        # buggy remote from deciding how long this page-load call runs.
        if len(rows) > _MAX_ANNOUNCEMENT_ROWS:
            log.warning(
                "catalog served %d announcements (at most %d are meaningful); "
                "only the first %d were read",
                len(rows),
                _MAX_ANNOUNCEMENT_ROWS,
                _MAX_ANNOUNCEMENT_ROWS,
            )
            rows = rows[:_MAX_ANNOUNCEMENT_ROWS]

        # At most one banner per placement. The contract promises that, but a server that
        # breaks the promise must still yield ONE deterministic banner per placement rather
        # than a stack of them — first wins, and later duplicates are quietly discarded.
        kept: dict[str, Announcement] = {}
        dropped = 0
        for row in rows:
            ann = _announcement_from_remote(row)
            if ann is None:
                dropped += 1
                continue
            kept.setdefault(ann.placement, ann)
        if dropped:
            log.warning("catalog served %d unreadable announcement(s); they were dropped", dropped)
        return tuple(kept.values())

    def pull_token(self, mission_id: str) -> PullToken | None:
        resp = self._client.post(f"{self._base}/v1/missions/{mission_id}/pull-token")
        if resp.status_code == 403:
            raise PullError(f"mission '{mission_id}' is not published (no pull token)")
        resp.raise_for_status()
        b = resp.json()
        return PullToken(
            registry=b["registry"],
            username=b["username"],
            password=b["password"],
            image_ref=b["image_ref"],
            expires_at=b["expires_at"],
        )

    def fetch_delivery(self, mission_id: str) -> DeliveryBundle | None:
        """Download the attachment bundle: GET /{id}/download → signed URL +
        integrity metadata, then fetch the zip bytes. 404 ⇒ no bundle (returns None)."""
        resp = self._client.get(f"{self._base}/v1/missions/{mission_id}/download")
        if resp.status_code == 404:
            return None  # published mission with no attachment bundle
        resp.raise_for_status()
        meta = resp.json()
        try:
            blob = self._client.get(meta["download_url"], timeout=_DOWNLOAD_TIMEOUT)
            blob.raise_for_status()
        except httpx.HTTPError as exc:
            raise PullError(f"delivery bundle download failed for '{mission_id}': {exc}") from exc
        return DeliveryBundle(
            content=blob.content,
            sha256=meta.get("dist_sha256"),
            delivery_version=meta.get("delivery_version"),
        )

    def status(self) -> CatalogStatus:
        try:
            resp = self._client.get(f"{self._base}/v1/health")
        except httpx.HTTPError as exc:
            return CatalogStatus(state="error", message=str(exc))
        if resp.status_code == 200:
            return CatalogStatus(state="connected")
        return CatalogStatus(state="error", message=f"catalog returned {resp.status_code}")


def _to_item(row: dict[str, object]) -> LibraryItem:
    competencies = _str_tuple(row.get("competencies"))
    return LibraryItem(
        mission_id=str(row["id"]),
        name=str(row["name"]),
        summary=str(row.get("objective", "")),
        proficiency=_opt_str(row.get("difficulty")),
        specialty=competencies[0] if competencies else None,
        technologies=_str_tuple(row.get("technologies")),
        image=_opt_str(row.get("image")),
        # lab vs static + the security skills, so the catalog can say what a mission IS and
        # teaches BEFORE it is pulled. The remote /v1/catalog now projects these from the stored
        # manifest metadata (to_library_item), so every AVAILABLE card labels + lists skills the
        # moment it renders, with no client release. Absent -> None/() (renders no badge / "no
        # skills listed"), so an older catalog still degrades cleanly.
        type=_opt_str(row.get("type")),
        skills=_str_tuple(row.get("skills")),
        # Pull cost, so the card can quote the download before the user commits. Absent on
        # a catalog deployed before these fields existed -> None ("size unknown"), so an
        # older remote still browses cleanly.
        image_size_bytes=_opt_int(row.get("image_size_bytes")),
        attachments_size_bytes=_opt_int(row.get("attachments_size_bytes")),
        download_size_bytes=_opt_int(row.get("download_size_bytes")),
        # Artifact identity (API1). Absent on a pre-contract catalog -> None/() and the row
        # browses exactly as before; the values power update detection and platform selection.
        mission_version=_opt_str(row.get("mission_version")),
        mission_base_version=_opt_str(row.get("mission_base_version")),
        index_digest=_opt_str(row.get("index_digest")),
        platforms=_str_tuple(row.get("platforms")),
    )


def _read_bounded(resp: httpx.Response, deadline: float) -> bytes | None:
    """The response body, or None when it crossed a bound (which is logged here, once).

    Both bounds are checked per chunk, because that is the only place they mean anything: a
    buffered read has already paid for every byte and every second by the time any limit could
    look at them. There is no check after the loop — a body that arrived complete is already
    in hand, and refusing to parse 64 KiB we are holding would cost more than it saves.

    The encoding check above is what makes the byte count a WIRE count, and it has to come
    first. `iter_bytes` runs the content decoder before it yields, so behind a compressed body
    the counter sees whatever the remote chose to expand to, not what it sent — measured at a
    33,578,960-byte first chunk from 407,698 bytes on the wire, a 512x overshoot already
    allocated before any limit could look at it. Refusing every non-identity encoding leaves the
    decoder an identity decoder, so from here the two counts are the same number.

    (`iter_raw` would make that structural rather than conditional, but it cannot be used: an
    `httpx.MockTransport` response built from in-memory content reports its stream as already
    consumed and raises `StreamConsumed`, which is how every test for this source is written.
    The equivalence above is the thing to keep true — if the refusal is ever relaxed, this count
    stops being a wire count on the same line.)

    The deadline covers this loop, not the connect-and-headers phase that precedes it — see
    `_ANNOUNCEMENTS_DEADLINE` for why nothing synchronous here can bound that.
    """
    encoding = resp.headers.get("content-encoding", "").strip().lower()
    if encoding and encoding != "identity":
        log.warning(
            "catalog encoded its announcements as %r despite a request for identity; "
            "the fetch was abandoned",
            encoding,
        )
        return None
    chunks: list[bytes] = []
    size = 0
    for chunk in resp.iter_bytes():
        size += len(chunk)
        if size > _MAX_ANNOUNCEMENT_BYTES:
            log.warning(
                "catalog served over %d bytes of announcements; the fetch was abandoned",
                _MAX_ANNOUNCEMENT_BYTES,
            )
            return None
        if time.monotonic() > deadline:
            log.warning(
                "catalog took longer than %.0fs to answer with its announcements; "
                "the fetch was abandoned",
                _ANNOUNCEMENTS_DEADLINE,
            )
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _announcement_from_remote(row: object) -> Announcement | None:
    """One remote announcement row -> a trusted Announcement, or None when it cannot be trusted.

    Here rather than beside the DTO in `contracts`, because this is a WIRE parser and every
    other wire parser in this feature is in this file: `_to_item` reads a `/v1/catalog` row and
    `_platform_images` reads a detail-response platform entry, both with exactly this contract
    — read the keys we know, ignore the rest, drop the row rather than raise. The frozen model
    in `contracts` stays what it is good at being: the shape we SERVE.

    LENIENT by design, and deliberately NOT `extra="forbid"`: a strict remote contract has
    already broken every client of this project once, when the server added a field. The
    `Announcement` model is the LOCAL wire shape — what we serve to our own frontend, where an
    unknown key is our own bug. This payload is untrusted input from a service that ships
    independently of this client, so it is read key by key: exactly the six known keys, every
    other key ignored, and anything unreadable returns None instead of raising. A malformed
    item costs one banner, never the response.

    `revision` rejects `bool` explicitly because `isinstance(True, int)` is True in Python —
    without that check a `revision: true` server bug would be served on to the browser as a
    valid revision.

    `id` and `body_md` are LENGTH-bounded, not merely type-checked, because both are relayed
    into the browser: `body_md` is rendered, and `id` becomes a localStorage dismissal key.
    Type-checking alone would let a broken or hostile remote hand the local app an unbounded
    string to store or draw. Over the bound is a rejection like any other — one banner lost,
    nothing else.

    `body_md` must also have something in it. A blank body is not a quieter banner: it renders
    as a tone word and a close button with no sentence under them, which tells a reader there
    is news and then refuses to say what it is.

    An `incident` is then FORCED undismissible whatever the server said: an active incident
    banner is not something a server bug gets to let an operator click away.
    """
    if not isinstance(row, dict):
        return None
    raw: dict[str, object] = row

    ident = raw.get("id")
    if not isinstance(ident, str) or not ident or len(ident) > MAX_ID_CHARS:
        return None

    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        return None

    # `PLACEMENTS`/`TONES` are typed tuples of the Literal members, so this membership test
    # both validates at runtime and narrows the type — no cast needed below.
    placement = raw.get("placement")
    if placement not in PLACEMENTS:
        return None

    tone = raw.get("tone")
    if tone not in TONES:
        return None

    body_md = raw.get("body_md")
    if not isinstance(body_md, str) or len(body_md) > MAX_BODY_CHARS:
        return None
    if not body_md.strip():
        return None

    dismissible = raw.get("dismissible")
    if not isinstance(dismissible, bool):
        return None

    return Announcement(
        id=ident,
        revision=revision,
        placement=placement,
        tone=tone,
        body_md=body_md,
        dismissible=False if tone == "incident" else dismissible,
    )


def _str_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(v) for v in value) if isinstance(value, list) else ()


def _platform_images(value: object) -> tuple[PlatformImage, ...]:
    """Detail-response platform entries ({os, architecture, digest, variant?}) — entries
    missing the identifying pair are dropped, never fatal (browse/pull must survive a
    malformed row)."""
    if not isinstance(value, list):
        return ()
    out: list[PlatformImage] = []
    for entry in value:
        if not isinstance(entry, dict) or "os" not in entry or "architecture" not in entry:
            continue
        out.append(
            PlatformImage(
                os=str(entry["os"]),
                architecture=str(entry["architecture"]),
                digest=_opt_str(entry.get("digest")),
                variant=_opt_str(entry.get("variant")),
            )
        )
    return tuple(out)


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _opt_int(value: object) -> int | None:
    """A byte count from the wire, or None if absent/unusable.

    Browse must survive a malformed row: a non-numeric size degrades this one field to
    "unknown" rather than raising and taking the whole catalog view down."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
