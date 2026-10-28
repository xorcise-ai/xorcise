"""Whether this server has a runner to drive, and the error when it does not (#168).

The runner is what launches a run's environment, pulls and builds mission images, and releases an
environment afterwards: the Docker and Headscale planes. Every builder that wires a real runner
adapter asks `runner_available` first and raises `RunnerUnavailableError` when the answer is no,
and the REST app maps that error to a 503. So a request that needs a runner is refused at the one
point it would have been handed a stub that answers success and does nothing, and the background
jobs that only make sense with a runner ask the same question before they start.

Today the only real runner is this host's own Docker and Headscale, owned by role `all` (and
`runner`). A remote runner (distributed-deployment story B.2) plugs in here: `runner_available`
becomes "…or a remote runner is configured", the builders return the remote adapter, and no
route or background job changes.
"""

from __future__ import annotations

from xorcise.core.config import Settings

# The roles that own the Docker and Headscale planes on their own host. Every real-vs-stub choice
# keys off this set.
RUNNER_ROLES = frozenset({"all", "runner"})


class RunnerUnavailableError(RuntimeError):
    """This server has no runner for the work asked of it. Mapped to 503 by the REST app.

    A RuntimeError, like the existing "Docker/Headscale not reachable" failures the run-create
    route already answers 503 for, so every handler that classifies those classifies this too."""


def runner_available(settings: Settings) -> bool:
    """True when this server can drive a runner, or when stubs are forced on purpose (tests and
    `--stub` want exactly those stubs)."""
    return settings.use_stubs or settings.role in RUNNER_ROLES


def runner_unavailable(settings: Settings, action: str) -> RunnerUnavailableError | None:
    """The error naming *action* when this server has no runner, else None. For a caller that
    reports the refusal rather than raising it."""
    if runner_available(settings):
        return None
    return RunnerUnavailableError(
        f"this server has no runner to {action}: role {settings.role!r} does not run Docker or "
        "Headscale itself, and a remote runner is not supported yet. Use role 'all' "
        "('xorcise up')."
    )


def require_runner(settings: Settings, action: str) -> None:
    """Raise RunnerUnavailableError naming *action* when this server has no runner."""
    if (error := runner_unavailable(settings, action)) is not None:
        raise error
