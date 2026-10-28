"""Run wire DTOs (LEAF). A run is created for a registered agent."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast, get_args

from pydantic import BaseModel, computed_field, field_validator

# How a terminal run ended — the ONE vocabulary every surface classifies and labels by. Every
# terminal_trigger write goes through runs.mark_terminal or runs.end_unstarted, and these seven are
# the only values ever written: a budget kill is recorded as "timeout" (there is no "budget"
# trigger), and no server has ever written "completed" or "error". The server classifies a run once
# (`run_outcome`), labels it once (`run_status`) and ships both on every RunEntry; no client
# restates any of it, because a private copy is how the leaderboard and the GUI came to disagree
# with the server (#159, #165).
TerminalTrigger = Literal[
    "done", "timeout", "operator", "cancelled", "never_started", "deploy_failed", "crashed"
]

#: A run's lifecycle: `created` until the agent first shows it is at work, then `xorcising` until
#: it ends, then `terminal`. A run can go straight from `created` to `terminal`:
#: an environment that never came up, or an operator's stop before the agent started (#209).
RunState = Literal["created", "xorcising", "terminal"]

#: What moved a run from `created` to `xorcising` — the first thing the agent did (#209):
#:   telemetry    its first OTel export (a trace or log batch) was admitted to the run's record.
#:                The receiver takes no credential, so this one is not proof that the sender
#:                was the agent (see rest/run_start.py)
#:   run_control  it called run-control with the run's key: the brief, an attachment, intel, a
#:                flag or artifact, or completion
#:   tailnet_key  it fetched its tailnet join key (`/connect` or `/join.sh`), on a lab mission
StartTrigger = Literal["telemetry", "run_control", "tailnet_key"]


#: How each start trigger reads beside the start time, on every surface that shows it.
START_TRIGGER_LABELS: dict[str, str] = {
    "telemetry": "first OTel export",
    "run_control": "first run-control call",
    "tailnet_key": "fetched its tailnet key",
}


def as_run_state(value: str) -> RunState:
    """A stored state, narrowed to the vocabulary. The column is free text, and a run list must
    not fail over one row: a value outside it (none has ever been written) reads as `created`,
    which is what any unfinished run meant before #209."""
    return cast(RunState, value) if value in get_args(RunState) else "created"


def as_start_trigger(value: str | None) -> StartTrigger | None:
    """A stored start trigger, narrowed to the vocabulary; anything else reads as not recorded."""
    return cast(StartTrigger, value) if value in get_args(StartTrigger) else None


def as_terminal_trigger(value: str | None) -> TerminalTrigger | None:
    """A stored trigger, narrowed to the vocabulary. The column is free text, and a run list must
    not fail over one row: a value outside the vocabulary (none has ever been written) reads as an
    ending with no known cause — the run shows "Ended" — rather than failing every read."""
    return cast(TerminalTrigger, value) if value in get_args(TerminalTrigger) else None


#: Never graded. Two of these are an environment failure: the readiness gate's close-out
#: (`deploy_failed`) and the boot reconcile's abort (`crashed`). Either can land before the lab is
#: up or mid-run, after the agent has worked for a while. The failure is ours, not the agent's, so
#: no score from such a run may count in any aggregate — and a score that cannot count is not
#: worth a judge call. A 0.00 here was a phantom score against the agent (#109).
#:
#: A run whose agent never started (UNSTARTED_TRIGGERS) is never graded either: there is no
#: attempt to score, and a 0.00 there would be the same phantom score.
UNGRADED_TRIGGERS: frozenset[str] = frozenset(
    {"cancelled", "never_started", "deploy_failed", "crashed"}
)

#: Ended before the agent ever started (#187): an operator's stop (`cancelled`), or the budget
#: running out (`never_started`). Not a run of the agent's at all, so beyond going ungraded it is
#: left out of every performance view.
UNSTARTED_TRIGGERS: frozenset[str] = frozenset({"cancelled", "never_started"})

#: Graded, but the run did not end on its own: a budget timeout or an operator's kill. Recorded
#: with `partial=True`. The two count differently in the performance summaries
#: (contracts.reporting.ScoreBucket): a timeout is the agent's failure to finish within its budget
#: and is scored at what it earned; an operator's stop says nothing about the agent and is left
#: out.
PARTIAL_TRIGGERS: frozenset[str] = frozenset({"timeout", "operator"})

#: Finished on the agent's own terms.
COMPLETED_TRIGGERS: frozenset[str] = frozenset({"done"})

#: How a terminal run ended. The three sets above are disjoint, so a run has at most one:
#: `completed` (the agent finished), `partial` (graded, but it did not end on its own; see
#: PARTIAL_TRIGGERS for how a timeout and a stop each count), `infra_failed` (cut short by an
#: environment failure: never graded, and not one of the agent's attempts — in no score and no
#: rate) or `not_started` (ended before the agent started, UNSTARTED_TRIGGERS: never graded, and
#: not a run of the agent's at all, so the performance views leave it out entirely).
RunOutcome = Literal["completed", "partial", "infra_failed", "not_started"]


def run_outcome(terminal_trigger: str | None) -> RunOutcome | None:
    """Classify a run by how it ended. None for a run that has not ended."""
    if terminal_trigger in COMPLETED_TRIGGERS:
        return "completed"
    if terminal_trigger in PARTIAL_TRIGGERS:
        return "partial"
    if terminal_trigger in UNSTARTED_TRIGGERS:
        return "not_started"
    if terminal_trigger in UNGRADED_TRIGGERS:
        return "infra_failed"
    return None


#: The status colour, by meaning: the CLI maps it to a theme style, the GUI to its badge tone.
RunTone = Literal["ok", "active", "err", "muted"]


class RunStatus(BaseModel):
    """How a run is SHOWN — the one wording the CLI, the GUI and the report all render, so the two
    surfaces cannot describe the same run differently (#165)."""

    label: str  # the short status word for a badge or a table cell
    tone: RunTone
    # One sentence saying why a run ended the way it did, for every ending but the agent's own
    # completion. The run page, the result page, `run status` and the report all print it.
    detail: str | None = None


_TERMINAL_STATUS: dict[str, RunStatus] = {
    "done": RunStatus(label="Completed", tone="ok"),
    "timeout": RunStatus(
        label="Timed out",
        tone="err",
        detail="The agent reached its time budget before completing the mission, so the result "
        "is partial.",
    ),
    "operator": RunStatus(
        label="Stopped",
        tone="muted",
        detail="An operator stopped this run before it finished, so the result is partial.",
    ),
    "cancelled": RunStatus(
        label="Cancelled",
        tone="muted",
        detail="An operator stopped this run before the agent started, so there was nothing to "
        "grade. It is not graded or counted in any average.",
    ),
    "never_started": RunStatus(
        label="Never started",
        tone="muted",
        detail="The run's time budget ran out before the agent started, so there was nothing to "
        "grade. It is not graded or counted in any average.",
    ),
    "deploy_failed": RunStatus(
        label="Deploy failed",
        tone="err",
        detail="The mission environment failed — it never became ready, or exited while the run "
        "was live — so the run was cut short. It is not graded or counted in any average.",
    ),
    "crashed": RunStatus(
        label="Crashed",
        tone="err",
        detail="The run's environment was gone when the server restarted, so the run was cut "
        "short. It is not graded or counted in any average.",
    ),
}


def run_status(state: str, terminal_trigger: str | None) -> RunStatus:
    """The one status wording for a run in `state`, ended by `terminal_trigger` if terminal."""
    if state == "created":
        return RunStatus(label="Created", tone="active")
    if state == "xorcising":
        return RunStatus(label="Xorcising", tone="active")
    if state == "terminal":
        return _TERMINAL_STATUS.get(terminal_trigger or "", RunStatus(label="Ended", tone="muted"))
    return RunStatus(label=state.title(), tone="muted")


class NotGradedView(BaseModel):
    """The FINAL answer /result, /report and /stats give for a run that is never graded: one cut
    short by an environment failure (`deploy_failed` / `crashed`, #109), or one an operator stopped
    before the agent started (`cancelled`), or one whose budget ran out before it did
    (`never_started`, #187). A 200, not a 202 —
    an answer, not progress — so a poll loop stops on it."""

    run_id: str
    status: Literal["not_graded"] = "not_graded"
    terminal_trigger: TerminalTrigger
    # The run's status wording (label, tone, why it ended) — what every surface prints for it.
    run_status: RunStatus
    # The evidence recorded when the run ended (the readiness gate's verdict and log tails).
    detail: str | None = None


class GradingView(BaseModel):
    """The 202 /result, /report and /stats give while a terminal run's grade is still being
    recorded — transient progress, not a failure: poll again."""

    run_id: str
    status: Literal["grading"] = "grading"


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
    state: RunState
    created_at: datetime
    # When the run moved to `xorcising`, and what showed the agent had started (StartTrigger).
    # Both None for a run the agent has not started, and for runs that predate #209.
    started_at: datetime | None = None
    start_trigger: StartTrigger | None = None
    budget_seconds: int = 0
    terminal_trigger: TerminalTrigger | None = None
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

    @field_validator("terminal_trigger", mode="before")
    @classmethod
    def _unknown_trigger_is_none(cls, value: object) -> object:
        return as_terminal_trigger(value) if value is None or isinstance(value, str) else value

    @field_validator("state", mode="before")
    @classmethod
    def _unknown_state_is_created(cls, value: object) -> object:
        return as_run_state(value) if isinstance(value, str) else value

    @field_validator("start_trigger", mode="before")
    @classmethod
    def _unknown_start_is_none(cls, value: object) -> object:
        return as_start_trigger(value) if value is None or isinstance(value, str) else value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def outcome(self) -> RunOutcome | None:
        """How this run counts in every aggregate (`run_outcome`), derived from terminal_trigger on
        every read and never persisted. Clients read this instead of re-deriving it (#159)."""
        return run_outcome(self.terminal_trigger)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def start_trigger_label(self) -> str | None:
        """How `start_trigger` reads beside `started_at` (START_TRIGGER_LABELS), so the GUI, the
        CLI and the report word it identically. None when the run has not started."""
        return START_TRIGGER_LABELS.get(self.start_trigger or "")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> RunStatus:
        """How this run is shown (`run_status`): label, tone and why it ended. Clients render this
        instead of mapping the trigger themselves, so the CLI and the GUI agree (#165)."""
        return run_status(self.state, self.terminal_trigger)


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
      releasing terminal run — the container or tailnet nodes are not torn down yet: grading has
                not finished, or a teardown failed and is being retried (#173)
      released  terminal run — the environment has been torn down
    """

    run_id: str
    state: Literal["none", "starting", "ready", "failed", "releasing", "released"]
    ready: bool = False
    detail: str = ""  # short human explanation, e.g. what it is still waiting for
    # Failed teardown attempts so far (releasing only). Non-zero means a plane refused the release,
    # not merely that grading is still running — the case a person should hear about.
    release_attempts: int = 0


DeleteRefusalReason = Literal["running", "grading", "unreleased"]


class DeleteRefusal(BaseModel):
    """The 409 body of DELETE /runs/{id} and DELETE /agents/{name} (#173).

    A run's row is the only handle anything has on its environment, so a delete is refused while
    that environment may still be up:
      running     a run has not finished (any state but terminal). Terminate it first.
      grading     its grade is in flight, and grading may still need the environment.
      unreleased  a finished run's environment could not be torn down (Docker or Headscale down).
    Only `unreleased` is `forceable`: `force=true` deletes anyway, accepting that a container or
    tailnet nodes may be left running."""

    detail: str
    reason: DeleteRefusalReason
    run_ids: tuple[str, ...]
    forceable: bool


class RunCreatedEntry(RunEntry):
    """The 201-create response shape — extends RunEntry with the freshly-minted bearer.

    Returned ONLY on POST /api/runs (once, like an API token on first issue). Never
    included in list or get responses so the key is not leaked to unauthenticated callers.
    """

    run_control_key: str
