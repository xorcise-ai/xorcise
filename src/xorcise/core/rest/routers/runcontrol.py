"""Run-scoped REST run-control adapter. REST, not MCP.

Thin transport over xorcise.core.runcontrol: a bearer dependency authenticates the caller
as the owner of {run_id} (per-run key, distinct from the tailnet join key), then delegates
to the service. Cross-module wiring (runs auth, mission manifest) is assembled here in the
rest layer — the service module never imports a sibling domain module (dependency rule)."""

from __future__ import annotations

import pathlib
import time
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Path, Request
from fastapi.responses import FileResponse, PlainTextResponse

from xorcise.core.config import Settings, get_settings
from xorcise.core.contracts.runcontrol import (
    CompleteResponse,
    IntelResponse,
    MissionInfo,
    SubmitArtifactRequest,
    SubmitArtifactResponse,
    TailnetJoinResponse,
)
from xorcise.core.runcontrol.errors import (
    MissionOverError,
    MissionUnavailableError,
    UnknownAttachmentError,
)
from xorcise.core.runcontrol.service import RunControlDeps, RunControlService

router = APIRouter(prefix="/runs", tags=["run-control"])

# Self-reaper hard-cap backstop (join.sh). Bound to the run budget + this margin so the
# authoritative 409 always fires first; a run with no budget falls back to a day.
_REAP_CAP_MARGIN_SECONDS = 600
_REAP_CAP_FALLBACK_SECONDS = 86400


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="run-control bearer required")
    return authorization.split(" ", 1)[1].strip()


def require_run(run_id: str, authorization: str | None) -> str:
    """Authenticate the bearer as the owner of run_id; return run_id or raise 401."""
    from xorcise.core import runs  # lazy: keep the runs module off this module's import path

    if not runs.authenticate(run_id, _bearer(authorization)):
        raise HTTPException(status_code=401, detail="invalid run-control credential")
    return run_id


def _guard[T](fn: Callable[[], T]) -> T:
    """Map the run-over gate to 409 and unavailable mission to 404 for any endpoint."""
    try:
        return fn()
    except MissionOverError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MissionUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_runcontrol_deps(settings: Settings) -> RunControlDeps:
    """Wire the service deps from settings.

    Uses real readers; gate wired via make_gate."""
    from pathlib import Path as _Path

    from xorcise.core.contracts.mission import MissionManifest
    from xorcise.core.missions import get_installed
    from xorcise.core.rest.run_terminate import make_gate
    from xorcise.core.runcontrol.store import SqliteSubmissionStore

    install_root = _Path(settings.missions_root)

    def slug_for(run_id: str) -> str:
        from xorcise.core import runs

        run = runs.get(run_id)
        return run.mission if run else ""

    def manifest_for(run_id: str) -> MissionManifest | None:
        inst = get_installed(slug_for(run_id), install_root)
        return inst.manifest if inst else None

    def targets_for(run_id: str) -> dict[str, str]:
        # read the run's persisted target name->IP facts so /mission resolves the
        # <name-target-ip-> placeholder (recorded by run_create at deploy time).
        from xorcise.core.runs.observed import SqliteObservedFactsStore

        facts = SqliteObservedFactsStore().list_for_run(run_id)
        return {f.name: f.value for f in facts if f.kind == "target"}

    def intel_policy_for(run_id: str) -> str:
        # Read the run row's persisted per-run intel policy (chosen at create time), mirroring the
        # slug_for run-row read. Absent run / empty ⇒ "all" (every authored intel may be disclosed).
        from xorcise.core import runs

        run = runs.get(run_id)
        return run.intel_policy if run else "all"

    return RunControlDeps(
        store=SqliteSubmissionStore(),
        manifest_for=manifest_for,
        slug_for=slug_for,
        signing_secret=settings.run_control_signing_secret,
        attachment_ttl=settings.attachment_ttl_seconds,
        now=lambda: int(time.time()),
        gate=make_gate(lambda: datetime.now(UTC)),
        targets_for=targets_for,
        intel_policy_for=intel_policy_for,
        # This router is include_router'd at "/api" (roles/boot/role_all.build_rest_app), so the
        # minted signed URL must carry that prefix to be reachable verbatim by the agent.
        attachment_url_prefix="/api",
    )


def _service() -> RunControlService:
    return RunControlService(build_runcontrol_deps(get_settings()))


@router.get("/{run_id}/mission")
def get_mission(run_id: str = Path(...), authorization: str | None = Header(None)) -> MissionInfo:
    rid = require_run(run_id, authorization)
    info = _guard(lambda: _service().get_mission(rid))
    # Record a first-party fact that the agent fetched its brief from run-control, so the v2 terrain
    # lights rc:prompt + m:agent-rc deterministically (recorded once per run; the infra plane reads
    # it in terrain_updates_infra.infra_updates). Best-effort: a display concern must never break
    # serving the brief.
    try:
        from xorcise.core.contracts.telemetry import ObservedFact
        from xorcise.core.runs.observed import SqliteObservedFactsStore

        store = SqliteObservedFactsStore()
        already = any(
            f.kind == "runcontrol-lifecycle" and f.name == "prompt" for f in store.list_for_run(rid)
        )
        if not already:
            store.record(
                ObservedFact(
                    run_id=rid, kind="runcontrol-lifecycle", name="prompt", value="fetched"
                )
            )
    except Exception:  # pragma: no cover - defensive; fact recording must not break the brief
        pass
    return info


@router.get("/{run_id}/connect")
def get_connect(
    run_id: str = Path(...), authorization: str | None = Header(None)
) -> TailnetJoinResponse:
    """Hand the agent its tailnet join bundle over an authenticated call instead of the
    prompt inlining the CA PEM + one-time key. login_server is pre-rewritten to by-IP."""
    from pathlib import Path as _Path

    from xorcise.core import runs
    from xorcise.core.runs.prompt import login_server_by_ip

    rid = require_run(run_id, authorization)
    settings = get_settings()
    ca = _Path(settings.headscale_ca_cert).read_text() if settings.headscale_ca_cert else ""
    base = settings.headscale_url or (
        f"http://{settings.headscale_advertise_host or settings.host}:{settings.headscale_port}"
    )
    _, _, ip = settings.headscale_host_alias.partition(":")
    login = login_server_by_ip(base, ip) if (ca and ip) else base  # by-IP only when air-gapped

    # The agent is fetching its join bundle to join the tailnet NOW — start the join-confirm
    # reconciler so the v2 terrain's agent<->Headscale edge activates once the agent's node comes
    # online, WITHOUT depending on anyone viewing /terrain2 at that instant. Best-effort: a
    # display-plane concern must never break the join handshake.
    try:
        from xorcise.core.rest.join_reconcile import maybe_reconcile_join

        maybe_reconcile_join(rid)
    except Exception:  # pragma: no cover - defensive; the kicker is already internally safe
        pass

    return TailnetJoinResponse(
        login_server=login, join_key=runs.get_join_key(rid) or "", ca_cert=ca
    )


def _runcontrol_base(request: Request) -> str:
    """The run-control base URL the caller used to reach /join.sh (strip the suffix + query), so the
    served script can call sibling endpoints (e.g. /tailscale.tgz) at the exact same base + auth."""
    url = str(request.url).split("?", 1)[0]
    suffix = "/join.sh"
    return url[: -len(suffix)] if url.endswith(suffix) else url


@router.get("/{run_id}/join.sh")
def get_join_script(
    request: Request, run_id: str = Path(...), authorization: str | None = Header(None)
) -> PlainTextResponse:
    """Serve the runnable one-shot tailnet join script (join ergonomics). Same bundle as GET
    /connect (by-IP login server, per-run key, control-plane CA), rendered into a self-contained
    script the agent runs with ``curl -fsS -H 'Authorization: Bearer <key>' <base>/join.sh | sh``.
    Collapses the reconstruct-the-join-by-hand friction into one command; works non-root. The
    script is handed the same base + bearer so it can pull the pinned client from /tailscale.tgz
    (air-gapped) before falling back to the public CDN."""
    from pathlib import Path as _Path

    from xorcise.core import runs
    from xorcise.core.runs.join import render_join_script
    from xorcise.core.runs.prompt import login_server_by_ip

    rid = require_run(run_id, authorization)
    settings = get_settings()
    ca = _Path(settings.headscale_ca_cert).read_text() if settings.headscale_ca_cert else ""
    base = settings.headscale_url or (
        f"http://{settings.headscale_advertise_host or settings.host}:{settings.headscale_port}"
    )
    _, _, ip = settings.headscale_host_alias.partition(":")
    login = login_server_by_ip(base, ip) if (ca and ip) else base  # by-IP only when air-gapped
    # Bind the self-reaper's hard-cap backstop to the run budget (+ a margin so the authoritative
    # 409 fires first), falling back to a day when the run is unbudgeted — see render_join_script.
    entry = runs.get(rid)
    budget = entry.budget_seconds if entry else 0
    reap_cap = budget + _REAP_CAP_MARGIN_SECONDS if budget else _REAP_CAP_FALLBACK_SECONDS
    script = render_join_script(
        login_server=login,
        join_key=runs.get_join_key(rid) or "",
        ca_cert=ca,
        run_control_base=_runcontrol_base(request),
        run_control_key=_bearer(authorization),
        reap_cap_seconds=reap_cap,
    )

    # Fetching the join script means the agent is about to join the tailnet (the headless harness
    # does `curl .../join.sh | sh`). Kick the join-confirm reconciler so the terrain
    # agent<->Headscale edge activates once the node comes online, independent of any viewer.
    # Best-effort: a display-plane concern must never break the join handshake.
    try:
        from xorcise.core.rest.join_reconcile import maybe_reconcile_join

        maybe_reconcile_join(rid)
    except Exception:  # pragma: no cover - defensive; the kicker is already internally safe
        pass

    return PlainTextResponse(script, media_type="text/x-shellscript")


@router.get("/{run_id}/tailscale.tgz")
def get_tailscale_tarball(
    run_id: str = Path(...), arch: str = "amd64", authorization: str | None = Header(None)
) -> FileResponse:
    """Serve the version-pinned static tailscale client tarball for *arch* (run-control-served
    binary). The server caches it (downloading once per arch), so an air-gapped agent installs
    tailscale without public egress. 400 on an unsupported arch; 502 when the server can't obtain
    it — the served join.sh treats 502 as its cue to fall back to the public CDN."""
    from pathlib import Path as _Path

    from xorcise.core.runs.tailscale_dist import (
        SUPPORTED_ARCHES,
        TailscaleDistError,
        ensure_tarball,
    )

    require_run(run_id, authorization)
    if arch not in SUPPORTED_ARCHES:
        raise HTTPException(status_code=400, detail=f"unsupported arch {arch!r}")
    try:
        path = ensure_tarball(_Path(get_settings().tailscale_cache_root), arch)
    except TailscaleDistError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return FileResponse(path, media_type="application/gzip", filename=path.name)


@router.post("/{run_id}/artifacts")
def submit_artifact(
    payload: SubmitArtifactRequest,
    run_id: str = Path(...),
    authorization: str | None = Header(None),
) -> SubmitArtifactResponse:
    rid = require_run(run_id, authorization)
    return _guard(lambda: _service().submit_artifact(rid, payload.name, payload.content))


@router.get("/{run_id}/intel")
def get_intel(run_id: str = Path(...), authorization: str | None = Header(None)) -> IntelResponse:
    rid = require_run(run_id, authorization)
    return _guard(lambda: _service().get_intel(rid))


@router.post("/{run_id}/complete")
def complete(
    background: BackgroundTasks,
    run_id: str = Path(...),
    authorization: str | None = Header(None),
) -> CompleteResponse:
    rid = require_run(run_id, authorization)
    from xorcise.core.rest.run_terminate import grade_and_record, seal_terminal

    def _do() -> CompleteResponse:
        _service().mark_done(rid)  # records the 'complete' submission + consults the gate
        seal_terminal(rid, "done", datetime.now(UTC))  # close control; OTLP drains in background
        # Grade off the request thread so the agent's /complete returns immediately;
        # the result surfaces via run status (202 grading → grade).
        background.add_task(grade_and_record, rid)
        return CompleteResponse(run_id=rid, state="terminal")

    return _guard(_do)


@router.get("/{run_id}/attachments/{name}")
def attachment(
    run_id: str = Path(...),
    name: str = Path(...),
    authorization: str | None = Header(None),
    x_run_key: str | None = Header(None),
    exp: int | None = None,
    sig: str | None = None,
) -> object:
    from fastapi.responses import FileResponse, JSONResponse

    settings = get_settings()
    # Download call: signed query present → validate X-Run-Key + signature + expiry, stream.
    if sig is not None and exp is not None:
        from xorcise.core.runcontrol.signing import verify

        if not (x_run_key and _runs_authenticate(run_id, x_run_key)):
            raise HTTPException(status_code=403, detail="invalid run key")
        if exp < int(time.time()):
            raise HTTPException(status_code=403, detail="link expired")
        if not verify(settings.run_control_signing_secret, run_id, name, exp, sig):
            raise HTTPException(status_code=403, detail="bad signature")
        path = _staged_attachment_path(settings, run_id, name)
        if path is None:
            raise HTTPException(status_code=404, detail="attachment not found")
        return FileResponse(path)
    # Mint call: authenticated bearer → return the signed URL JSON.
    rid = require_run(run_id, authorization)
    try:
        resp = _guard(lambda: _service().get_attachment(rid, name))
    except UnknownAttachmentError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Record a first-party fact that the agent fetched an attachment from run-control, so the v2
    # terrain lights rc:attachments + m:agent-rc anchored to this moment (recorded once per run;
    # the infra plane reads it in terrain_updates_infra.infra_updates — mirrors get_mission's
    # prompt fact). Best-effort: a display concern must never break serving the attachment.
    try:
        from xorcise.core.contracts.telemetry import ObservedFact
        from xorcise.core.runs.observed import SqliteObservedFactsStore

        store = SqliteObservedFactsStore()
        already = any(
            f.kind == "runcontrol-lifecycle" and f.name == "attachment"
            for f in store.list_for_run(rid)
        )
        if not already:
            store.record(
                ObservedFact(
                    run_id=rid, kind="runcontrol-lifecycle", name="attachment", value="fetched"
                )
            )
    except Exception:  # pragma: no cover - defensive; fact recording must not break the attachment
        pass
    return JSONResponse(resp.model_dump(mode="json"))


def _runs_authenticate(run_id: str, key: str) -> bool:
    from xorcise.core import runs

    return runs.authenticate(run_id, key)


def _staged_attachment_path(settings: Settings, run_id: str, name: str) -> pathlib.Path | None:
    from pathlib import Path as _Path

    from xorcise.core import runs
    from xorcise.core.missions import get_installed

    run = runs.get(run_id)
    if run is None:
        return None
    inst = get_installed(run.mission, _Path(settings.missions_root))
    if inst is None:
        return None
    att = next((a for a in inst.attachments if a.name == name), None)
    if att is None:
        return None
    candidate = inst.root / att.path
    if not candidate.resolve().is_relative_to(inst.root.resolve()):
        return None
    return candidate if candidate.is_file() else None
