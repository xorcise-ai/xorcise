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

import logging

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
from xorcise.core.contracts.announcements import Announcement, announcement_from_remote
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
        or an item that had to be dropped, is a SERVER-side defect an operator should see —
        one warning, not one per item.
        """
        try:
            resp = self._client.get(
                f"{self._base}/v1/announcements/active", timeout=_ANNOUNCEMENTS_TIMEOUT
            )
            if resp.status_code == 404:
                return ()  # a deployment that predates announcements — normal, not an error
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            # ValueError covers a JSON decode failure (a captive portal serving HTML).
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
            ann = announcement_from_remote(row)
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
