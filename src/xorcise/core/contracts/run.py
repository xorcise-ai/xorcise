"""Run wire DTOs (LEAF). A run is created for a registered agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# How a terminal run ended — the ONE vocabulary every surface classifies by. Every terminal_trigger
# write goes through runs.mark_terminal, and these five are the only literals ever passed to it: a
# budget kill is recorded as "timeout" (there is no "budget" trigger). Server grading, the CLI
# leaderboard and `run export --genuine-only` all import from here, because two copies of this
# rule is how the leaderboard came to disagree with the server about which runs count (#159).

#: The run was cut short by an environment failure: the readiness gate's close-out
#: (`deploy_failed`) and the boot reconcile's abort (`crashed`). Either can land before the lab is
#: up or mid-run, after the agent has worked for a while. Never graded: the failure is ours, not
#: the agent's, so no score from such a run may count in any aggregate — and a score that cannot
#: count is not worth a judge call. A 0.00 here was a phantom score against the agent (#109).
UNGRADED_TRIGGERS: frozenset[str] = frozenset({"deploy_failed", "crashed"})

#: Graded, but the run did not end on the agent's own terms — a budget timeout or an operator's
#: kill. Recorded with `partial=True` and left out of every score aggregate.
PARTIAL_TRIGGERS: frozenset[str] = frozenset({"timeout", "operator"})

#: Finished on the agent's own terms. `completed` has never been written by any server; it stays
#: for parity with run_state_label and the GUI's run-state map, which both accept it.
COMPLETED_TRIGGERS: frozenset[str] = frozenset({"done", "completed"})


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
    # Why the run ended, when the trigger alone does not say. Set by the readiness gate on a
    # `deploy_failed` close-out: the environment's last observed state plus the evidence (the
    # outer container's log tail, and the inner daemon's where it could still be read), captured
    # BEFORE the environment was released — once released, those logs are gone for good.
    terminal_detail: str | None = None
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
