"""Name/ID resolvers — accept what the user remembers, find what the API needs (cli).

Users think in agent names, mission display names, and short run-id prefixes;
the REST contract wants canonical names, mission slugs, and full run ids. Each
resolver fetches the live list once, matches generously (exact → case-insensitive
→ unique prefix), and fails with the candidates when a match is ambiguous —
never silently picking one.
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

from xorcise.core.cli._ux import fail
from xorcise.core.cli.rest_client import RestClient

# Subcommand names of the owning groups: a token like 'list' failing name
# resolution is almost always a transposed command, not a resource name — and
# the answer must never be a mutating suggestion.
_AGENT_COMMANDS = frozenset({"list", "register", "update", "rename", "history", "rm", "delete"})
_MISSION_COMMANDS = frozenset({"list", "show", "pull", "delete", "rm", "ingest"})


def _require_value(given: str, noun: str, see: str) -> None:
    """An empty / whitespace-only id is a MISSING argument, not a prefix.

    Unguarded it matched every id: ambiguous in a busy home (exit 1, confusing),
    and — far worse — a silent exact resolve in a home holding exactly one item,
    so `xorcise run delete "$RID"` with an unset variable deleted that one run
    with exit 0. Fail as the usage error it is, before any lookup."""
    if not given.strip():
        fail(f"missing {noun}", see=(see,), code=2)


def _closest(given: str, names: list[str]) -> str | None:
    matches = get_close_matches(given.lower(), [n.lower() for n in names], n=1, cutoff=0.6)
    if not matches:
        return None
    return next((n for n in names if n.lower() == matches[0]), None)


def resolve_run_id(client: RestClient, given: str) -> str:
    """Full run id, or a unique short prefix of one → the full id."""
    _require_value(given, "run id", "xorcise run list")
    runs: list[dict[str, Any]] = client.get("/runs")
    ids = [str(r.get("run_id") or "") for r in runs]
    if given in ids:
        return given
    matches = [i for i in ids if i.startswith(given)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        listing = ", ".join(sorted(i[:12] for i in matches))
        fail(
            f"run id '{given}' is ambiguous — it matches {len(matches)} runs: {listing}",
            see=("xorcise run list",),
        )
    fail(f"no run matching '{given}'", see=("xorcise run list",))


def resolve_agent_name(client: RestClient, given: str) -> str:
    """Exact agent name, else unique case-insensitive / prefix match → canonical name."""
    _require_value(given, "agent name", "xorcise agent list")
    agents: list[dict[str, Any]] = client.get("/agents")
    names = [str(a.get("name") or "") for a in agents]
    if given in names:
        return given
    folded = [n for n in names if n.lower() == given.lower()]
    if len(folded) == 1:
        return folded[0]
    prefixed = [n for n in names if n.lower().startswith(given.lower())]
    if len(prefixed) == 1:
        return prefixed[0]
    if len(prefixed) > 1:
        fail(
            f"agent '{given}' is ambiguous — matches: {', '.join(sorted(prefixed))}",
            see=("xorcise agent list",),
        )
    if given in _AGENT_COMMANDS:
        fail(
            f"'{given}' is a command, not an agent name",
            example=f"xorcise agent {given}",
        )
    guess = _closest(given, names)
    message = f"no agent named '{given}'"
    if guess:
        message += f" — did you mean '{guess}'?"
    fail(message, see=("xorcise agent list",))


def resolve_mission(client: RestClient, given: str) -> dict[str, Any]:
    """Mission slug, display name, or unique prefix of either → the catalog entry.

    Returns the whole entry so callers can also check `installed` before acting."""
    _require_value(given, "mission id", "xorcise mission list")
    missions: list[dict[str, Any]] = client.get("/missions")

    def _entry(cid: str) -> dict[str, Any]:
        return next(c for c in missions if c.get("mission_id") == cid)

    ids = [str(c.get("mission_id") or "") for c in missions]
    if given in ids:
        return _entry(given)
    by_name = [c for c in missions if str(c.get("name") or "").lower() == given.lower()]
    if len(by_name) == 1:
        return by_name[0]
    lowered = given.lower()
    prefixed = [
        c
        for c in missions
        if str(c.get("mission_id") or "").startswith(lowered)
        or str(c.get("name") or "").lower().startswith(lowered)
    ]
    if len(prefixed) == 1:
        return prefixed[0]
    if len(prefixed) > 1:
        listing = ", ".join(sorted(str(c.get("mission_id")) for c in prefixed))
        fail(
            f"mission '{given}' is ambiguous — it matches: {listing}",
            see=("xorcise mission list",),
        )
    if given in _MISSION_COMMANDS:
        fail(
            f"'{given}' is a command, not a mission",
            example=f"xorcise mission {given}",
        )
    guess = _closest(given, ids) or _closest(given, [str(c.get("name") or "") for c in missions])
    message = f"no mission matching '{given}'"
    if guess:
        message += f" — did you mean '{guess}'?"
    fail(message, see=("xorcise mission list",))


def agent_names_by_id(client: RestClient) -> dict[str, str]:
    """agent id → name, so run views read in operator terms."""
    return {a["id"]: a["name"] for a in client.get("/agents")}


def mission_names_by_id(client: RestClient) -> dict[str, str]:
    """mission slug → display name, best-effort (an empty map when offline lists slugs)."""
    return {c["mission_id"]: c["name"] for c in client.get("/missions")}
