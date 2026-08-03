"""Error wire DTOs (LEAF). Typed errors shared across the contract seams.

Imports nothing internal — stdlib only.
"""

from __future__ import annotations


class ContractError(Exception):
    """Base for every error that crosses a contract seam."""


class AuthError(ContractError):
    """A control call presented a missing or invalid API key."""


class ConflictError(ContractError):
    """A run_id was replayed with a conflicting request (idempotency breach)."""


class NotFoundError(ContractError):
    """A run_id (or other keyed entity) is unknown to the adapter."""


class ImageNotInstalledError(ContractError):
    """A deploy referenced a fused image that is not in the local store. The image is local-only
    (no registry), so a missing image means the mission was never built or got pruned — the
    runner must fail loud (the caller re-ingests) rather than attempt a doomed registry pull."""


class PullError(ContractError):
    """Fetching the mission image failed; nothing was installed. Lives here (LEAF) so the
    catalog island can raise it without importing the delivery layer."""


class MissionNotInCatalogError(PullError):
    """The id is not installed and not in the catalog — nothing to pull."""


class PullCancelled(ContractError):
    """The in-flight pull was cancelled by request before install completed. NOT a failure:
    install_pulled is the only writer (atomic), so a cancel before it leaves the mission
    cleanly not-installed — the same state as a failed pull. Distinct from PullError so the
    delivery worker records the job as `cancelled`, not `error`. Raised by the pull spine when
    its `should_cancel` probe trips; deliberately NOT a PullError subclass so the spine's
    `except PullError`/`except Exception` wrappers don't relabel it (LEAF)."""
