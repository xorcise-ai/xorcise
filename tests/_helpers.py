"""Shared test helpers."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import EnvironmentSpec, MissionManifest, MissionMetadata
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission


def agent_nodes(
    nodes_json: Sequence[Mapping[str, object]],
    orchestrator_user: str,
    router_tag: str = "tag:router",
) -> list[str]:
    """Names of AGENT nodes on a Headscale control plane (real-Headscale guard).

    An agent node is any node that is NOT the orchestrator user and is NOT a router (router_tag).
    A non-empty result means a live XORCISE run is using this control plane — the real-Headscale
    tests must refuse to run against it rather than clobber its ACL."""
    out: list[str] = []
    for n in nodes_json:
        raw_tags = n.get("tags")
        tags = raw_tags if isinstance(raw_tags, list) else []
        user = n.get("user")
        uname = user.get("name", "") if isinstance(user, dict) else str(user)
        if router_tag in tags or uname == orchestrator_user:
            continue
        out.append(str(n.get("name", "")))
    return out


def stray_agent_nodes(
    container: str,
    orchestrator_user: str = "orchestrator",
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    """Agent nodes currently live on the Headscale *container* (real-Headscale guard).

    Queries `headscale nodes list` and returns agent-node names via agent_nodes(). An unreachable
    control plane (non-zero exit) returns [] — the caller's skip-guards already handle absence."""
    proc = runner(
        ["docker", "exec", container, "headscale", "nodes", "list", "-o", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    # headscale prints the JSON literal `null` (not []) for an empty node set.
    return agent_nodes(json.loads(proc.stdout or "[]") or [], orchestrator_user)


def install_mission(home: Path, slug: str = "c1") -> None:
    """Write a minimal installed-mission record under <home>/missions/<slug>/."""
    root = Path(home) / "missions" / slug
    root.mkdir(parents=True, exist_ok=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="Solve it.", type="lab"),
        environment=EnvironmentSpec(),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())
