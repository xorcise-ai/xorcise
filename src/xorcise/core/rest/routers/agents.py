"""Agents router — real wiring over the thin registry + per-agent history (REST surface)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

from xorcise.core import agents, reporting, runs
from xorcise.core.contracts.agent import AgentDeclaration, AgentEntry, kind_warning
from xorcise.core.contracts.reporting import AgentHistoryEntry
from xorcise.core.contracts.run import RunStatus, run_status

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentHistoryView(AgentHistoryEntry):
    """A history row plus the run's status wording (contracts.run.run_status), joined here because
    contracts never import one another. A row whose run has since been deleted still reads exactly
    as the run did — the GUI used to invent a "budget" trigger for it instead (#165)."""

    status: RunStatus


class AgentSaveView(AgentEntry):
    """A register/update result plus anything the operator should know about what was saved.
    The save itself is never refused for these: a kind no harness answers to is the sanctioned
    way to label your own CLI, so it is disclosed here rather than rejected (#141)."""

    warnings: list[str] = []


def _kind_warnings(kind: str | None) -> list[str]:
    """Disclose a `kind` no registered adapter answers to (contracts.agent.kind_warning, the
    wording the CLI gate prints too). A blank kind is stored as `generic` and stays silent.

    Checked here, not in the agents domain: the registry holds only `generic` until
    `load_adapters()` runs, and only the delivery tier may populate it (as
    rest/routers/harnesses.py does). Lazy imports keep the otel plane off this module's path."""
    from xorcise.core.harness_adapters import load_adapters
    from xorcise.core.otel.adapters import registry

    load_adapters()
    warning = kind_warning(kind, registry.registered_names())
    return [warning] if warning else []


@router.get("")
def list_agents() -> list[AgentEntry]:
    return agents.list_agents()


@router.post("", status_code=201)
def register_agent(declaration: AgentDeclaration) -> AgentSaveView:
    warnings = _kind_warnings(declaration.kind)  # before the write: a save is never half-done
    try:
        entry = agents.register(
            name=declaration.name,
            endpoint=declaration.endpoint,
            otel=declaration.otel,
            model=declaration.model,
            kind=declaration.kind,
            launch_command_template=declaration.launch_command_template,
            launch_tips=declaration.launch_tips,
            mission_preamble=declaration.mission_preamble,
            launch_mode=declaration.launch_mode,
        )
    except agents.DuplicateAgentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AgentSaveView(**entry.model_dump(), warnings=warnings)


@router.put("/{name}")
def update_agent(name: str, declaration: AgentDeclaration) -> AgentSaveView:
    """Update an agent's declaration and bump its version; a differing body name
    renames the agent (409 when taken). 404 when absent."""
    warnings = _kind_warnings(declaration.kind)
    try:
        entry = agents.update_agent(
            name,
            new_name=declaration.name,
            endpoint=declaration.endpoint,
            otel=declaration.otel,
            model=declaration.model,
            kind=declaration.kind,
            launch_command_template=declaration.launch_command_template,
            launch_tips=declaration.launch_tips,
            mission_preamble=declaration.mission_preamble,
            launch_mode=declaration.launch_mode,
        )
    except agents.DuplicateAgentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no agent named '{name}'")
    return AgentSaveView(**entry.model_dump(), warnings=warnings)


@router.get("/{name}/history")
def agent_history(name: str) -> list[AgentHistoryView]:
    agent = agents.get(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"no agent named '{name}'")
    # Fill disclosure provenance from the run-control submission store (the delivery layer owns
    # this cross-module join; lazy import keeps that module off the module-import path). No
    # results-table migration — intel_disclosed is computed per run at read time.
    from xorcise.core.runcontrol.store import disclosed_intel_count

    return [
        AgentHistoryView(
            **entry.model_dump(exclude={"conditions"}),
            conditions=entry.conditions.model_copy(
                update={"intel_disclosed": disclosed_intel_count(entry.run_id)}
            ),
            # A recorded result is either the agent's own completion or a partial one.
            status=run_status("terminal", entry.partial_trigger if entry.partial else "done"),
        )
        for entry in reporting.agent_history(agent.id)
    ]


@router.delete("/{name}", status_code=204)
def remove_agent(name: str, force: bool = False) -> Response:
    """Remove an agent and cascade-delete its runs + results.

    The runs' rows are the release sweeper's only handle on their environments (#173), so the
    cascade is refused (409) while any of the agent's runs is still running or being graded, and
    when a finished run's environment cannot be released. `force=true` overrides only the last,
    accepting that a container or tailnet nodes may be left running. The 409 body carries
    `reason`, `run_ids` and `forceable` beside `detail`."""
    agent = agents.get(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"no agent named '{name}'")
    from xorcise.core.rest.run_teardown import check_deletable

    blocked = check_deletable(
        [r.run_id for r in runs.list_runs() if r.agent_id == agent.id], force=force
    )
    if blocked is not None:
        body = blocked.body()
        body["detail"] = f"cannot remove agent '{name}': {body['detail']}"
        return JSONResponse(status_code=409, content=body)
    reporting.delete_for_agent(agent.id)
    runs.delete_for_agent(agent.id)
    agents.remove(name)
    return Response(status_code=204)
