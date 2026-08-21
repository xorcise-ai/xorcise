"""Run wire DTOs (LEAF). A run is created for a registered agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RunCreate(BaseModel):
    """Create request: the registered agent's name + the mission ref.

    `name` is the operator's label for the run; when omitted (or blank) the spine mints a lazy
    unique default (`<mission> · <agent> #<n>`), so a run always has a name.
    """

    agent: str
    mission: str
    budget_seconds: int | None = None
    name: str | None = None
    # Per-run intel disclosure policy (runcontrol.intel_policy grammar): "" / "all" ⇒ all authored
    # intel, "none" ⇒ none, "i1,i3" ⇒ those ids. None ⇒ default "all" (persisted on the run).
    intel_policy: str | None = None


class RunEntry(BaseModel):
    """A run as returned over the wire for list/get — never carries the bearer."""

    run_id: str
    agent_id: str
    mission: str
    # The operator-facing run label. Always populated for runs created after XOR run-naming;
    # legacy rows (no stored name) fall back to the mission in the repository mapper.
    name: str = ""
    state: str
    created_at: datetime
    budget_seconds: int = 0
    terminal_trigger: str | None = None
    completed_at: datetime | None = None
    model: str | None = None  # disclosed model (agent's declared model at create time)
    sandbox_ref: str | None = None  # disclosed sandbox (mission image at create time)
    agent_version: int = 1  # agent monotonic version at create time
    install_revision: int = 1  # mission's monotonic LOCAL install counter at create time
    # Artifact provenance copied from installed.json at create time (contract §31): the
    # creator SemVer (the public mission_version — a string, per the naming contract §25),
    # the base SemVer, the bundle hash, and what exactly this machine pulled and executed.
    # All None for a your_own fuse or a pre-contract install.
    mission_version: str | None = None
    mission_base_version: str | None = None
    content_hash: str | None = None
    platform: str | None = None
    index_digest: str | None = None
    platform_digest: str | None = None
    source_agent: str = "generic"  # rendering-agent kind, snapshotted at create time
    intel_policy: str = "all"  # per-run intel disclosure policy (runcontrol.intel_policy grammar)
    # Server-side receipt time of the run's newest OTLP export (trace OR log signal). None until
    # the first export lands. Derived at read time by the delivery layer from the RAW stores —
    # never persisted on the run row. Clients use it to warn on an inactive/stalled agent.
    last_telemetry_at: datetime | None = None


class RunEnvironmentView(BaseModel):
    """The live state of a run's mission ENVIRONMENT, for the run page's Environment chip.

    Previously the UI inferred this from `sandbox_ref` alone, which could only ever say "Starting"
    or "Ready" and was wrong twice over: a static mission (no image ⇒ no sandbox_ref) read as a
    perpetual "Starting", and an environment that had DIED still read as "Ready". These states are
    observed from the runner + fence instead:

      none      static (attachment-only) run — there is no environment, and never will be
      starting  deployed; the mission services / subnet router are still coming up
      ready     every inner service is running and the run's router is on the tailnet
      failed    the environment exited or never came up (the run is closed out as deploy_failed)
      released  terminal run — the environment has been torn down
    """

    run_id: str
    state: Literal["none", "starting", "ready", "failed", "released"]
    ready: bool = False
    detail: str = ""  # short human explanation, e.g. what it is still waiting for


class RunCreatedEntry(RunEntry):
    """The 201-create response shape — extends RunEntry with the freshly-minted bearer.

    Returned ONLY on POST /api/runs (once, like an API token on first issue). Never
    included in list or get responses so the key is not leaked to unauthenticated callers.
    """

    run_control_key: str
