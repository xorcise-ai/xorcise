"""Agents router — real wiring over the thin registry + per-agent history (REST surface)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from xorcise.core import agents, reporting, runs
from xorcise.core.contracts.agent import AgentDeclaration, AgentEntry
from xorcise.core.contracts.reporting import AgentHistoryEntry
from xorcise.core.contracts.run import RunStatus, run_status

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentHistoryView(AgentHistoryEntry):
    """A history row plus the run's status wording (contracts.run.run_status), joined here because
    contracts never import one another. A row whose run has since been deleted still reads exactly
    as the run did — the GUI used to invent a "budget" trigger for it instead (#165)."""

    status: RunStatus


@router.get("")
def list_agents() -> list[AgentEntry]:
    return agents.list_agents()


@router.post("", status_code=201)
def register_agent(declaration: AgentDeclaration) -> AgentEntry:
    try:
        return agents.register(
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


@router.put("/{name}")
def update_agent(name: str, declaration: AgentDeclaration) -> AgentEntry:
    """Update an agent's declaration and bump its version; a differing body name
    renames the agent (409 when taken). 404 when absent."""
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
    return entry


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
def remove_agent(name: str) -> Response:
    """Remove an agent and cascade-delete its runs + results."""
    agent = agents.get(name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"no agent named '{name}'")
    reporting.delete_for_agent(agent.id)
    runs.delete_for_agent(agent.id)
    agents.remove(name)
    return Response(status_code=204)
