"""Run-control service errors. MissionOverError is the terminal-gate seam."""

from __future__ import annotations


class UnknownAttachmentError(Exception):
    """The run's mission declares no attachment of the requested name."""


class MissionUnavailableError(Exception):
    """The run's mission manifest could not be resolved (e.g. not installed)."""


class MissionOverError(Exception):
    """The run is terminal; the run-over gate rejects the call."""
