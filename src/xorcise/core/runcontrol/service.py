"""The transport-agnostic run-control service (domain module).

All six operations + their run-scoped rules live here, not in the transport. Cross-module
reads (the run's mission manifest, the run→slug map) are injected via RunControlDeps so
this module never imports a sibling domain module (dependency rule). The terminal/seal/run-over
machine lives in the rest layer: the service only consults the injected `gate` seam
(default no-op)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.contracts.runcontrol import (
    CompleteResponse,
    GetAttachmentResponse,
    IntelResponse,
    MissionInfo,
    SubmitArtifactResponse,
)
from xorcise.core.runcontrol.errors import MissionUnavailableError, UnknownAttachmentError
from xorcise.core.runcontrol.intel_policy import allowed_intel
from xorcise.core.runcontrol.signing import signed_path
from xorcise.core.runcontrol.store import SubmissionStore


def _open_gate(_run_id: str) -> None:
    """Permissive default; the rest layer wires the real terminal predicate (make_gate)."""


def _no_targets(_run_id: str) -> dict[str, str]:
    return {}


def _all_intel(_run_id: str) -> str:
    """Back-compat default: no injected policy ⇒ ALL authored intel may be disclosed."""
    return "all"


@dataclass(frozen=True)
class RunControlDeps:
    store: SubmissionStore
    manifest_for: Callable[[str], MissionManifest | None]
    slug_for: Callable[[str], str]
    signing_secret: str
    now: Callable[[], int]
    attachment_ttl: int = 300
    # The REST router's mount prefix (e.g. "/api"), so the minted download URL is reachable
    # VERBATIM by the agent. Empty = module default (the composition root injects the real mount).
    attachment_url_prefix: str = ""
    gate: Callable[[str], None] = field(default=_open_gate)
    # run_id → {target-name: IP}, so /mission resolves the same <name-target-ip->
    # placeholder the connect prompt does. Default: none (back-compat).
    targets_for: Callable[[str], dict[str, str]] = field(default=_no_targets)
    # Per-run intel disclosure policy (run_id → policy string; see runcontrol.intel_policy).
    # Injected (never a runs-module import, mirroring targets_for). Default: "all" — all authored
    # intel may be disclosed, so a run created before intel-control keeps today's behavior.
    intel_policy_for: Callable[[str], str] = field(default=_all_intel)


class RunControlService:
    def __init__(self, deps: RunControlDeps) -> None:
        self._d = deps

    def _manifest(self, run_id: str) -> MissionManifest:
        m = self._d.manifest_for(run_id)
        if m is None:
            raise MissionUnavailableError(f"no mission for run {run_id!r}")
        return m

    def get_mission(self, run_id: str) -> MissionInfo:
        self._d.gate(run_id)
        m = self._manifest(run_id)
        # resolve <name-target-ip-> to the run's routed IP so the brief never leaks an
        # unresolvable service name (mirrors the connect prompt).
        objective = m.metadata.objective
        for name, ip in self._d.targets_for(run_id).items():
            objective = objective.replace(f"<{name}-target-ip->", ip)
        return MissionInfo(
            mission=m.metadata.mission_id,
            objective=objective,
            attachments=tuple(a.name for a in m.attachments),
        )

    def submit_artifact(self, run_id: str, name: str, content: str) -> SubmitArtifactResponse:
        self._d.gate(run_id)
        self._d.store.record(run_id, "artifact", name, content)
        return SubmitArtifactResponse(accepted=True, name=name)

    def get_intel(self, run_id: str) -> IntelResponse:
        self._d.gate(run_id)
        # Disclose sequentially over the run's POLICY-FILTERED intel (authored order), not the raw
        # authored set: a "none" policy discloses nothing; a subset policy walks only its ids.
        intel = allowed_intel(self._d.intel_policy_for(run_id), self._manifest(run_id).intel)
        granted = self._d.store.count(run_id, "intel")
        if granted >= len(intel):
            return IntelResponse(intel=None, remaining=0)
        nxt = intel[granted]
        self._d.store.record(run_id, "intel", nxt.id, nxt.text)
        return IntelResponse(intel=nxt.text, remaining=len(intel) - (granted + 1))

    def mark_done(self, run_id: str) -> CompleteResponse:
        self._d.gate(run_id)
        self._d.store.record(run_id, "complete", "", "")
        return CompleteResponse(state="terminal")  # sealing is the terminal machine's job

    def get_attachment(self, run_id: str, name: str) -> GetAttachmentResponse:
        self._d.gate(run_id)
        att = next((a for a in self._manifest(run_id).attachments if a.name == name), None)
        if att is None:
            raise UnknownAttachmentError(f"no attachment {name!r} for run {run_id!r}")
        exp = self._d.now() + self._d.attachment_ttl
        return GetAttachmentResponse(
            name=att.name,
            url=signed_path(
                self._d.signing_secret, run_id, att.name, exp, prefix=self._d.attachment_url_prefix
            ),
            expires_at=datetime.fromtimestamp(exp, tz=UTC),
            media_type=att.media_type,
            sha256=att.sha256,
        )
