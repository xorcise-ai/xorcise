"""System router — the Reflect view for the GUI System card, and the nested-container re-probe
`doctor` asks for."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from xorcise.core.config import get_settings
from xorcise.core.contracts.config import NestedSupportView, NestedVerdictView, SystemInfo
from xorcise.core.rest.system_view import build_system_info

if TYPE_CHECKING:  # the runner plane is forbidden to roles that do not run missions
    from xorcise.core.runner.docker.rosetta import NestedSupport

router = APIRouter(prefix="/system", tags=["system"])


@router.get("")
def get_system() -> SystemInfo:
    return build_system_info(get_settings())


@router.post("/nested-support/refresh")
def refresh_nested_support() -> NestedSupportView:
    """Re-probe nested containers in THIS process, where run creation reads its verdicts (#195).

    `doctor` calls this after the operator fixes the host (enables Rosetta, say), so the refusal
    the server cached clears without a restart. A sync route: the probe starts a privileged
    container and can take a minute, so it runs on the threadpool, not the event loop."""
    from xorcise.core.rest.docker_runtime import docker_client
    from xorcise.core.rest.docker_runtime import refresh_nested_support as refresh
    from xorcise.core.rest.mission_pull import _use_real_docker

    settings = get_settings()
    # The same gate run creation probes under: a role that deploys nothing never reads a
    # verdict, so it has none worth re-probing (and no business starting a privileged probe).
    if not _use_real_docker(settings):
        raise HTTPException(
            status_code=409, detail="this server deploys no runs, so there is nothing to probe"
        )
    try:
        native, foreign = refresh(settings, docker_client)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return NestedSupportView(
        native=_verdict(native.platform, native),
        foreign=tuple(_verdict(p, v) for p, v in sorted(foreign.items())),
    )


def _verdict(platform: str, support: NestedSupport) -> NestedVerdictView:
    return NestedVerdictView(
        platform=platform, ok=support.ok, detail=support.detail, remediation=support.remediation
    )
