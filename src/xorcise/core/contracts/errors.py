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


class NestedContainersUnavailableError(ContractError):
    """This host cannot run a container inside a container, so a lab mission cannot be deployed.

    XORCISE runs every mission's stack INSIDE the run's own container. There is no fallback: the
    old host-daemon topology composed the stack on the operator's own daemon, where parallel runs
    collide on fixed `container_name`s and published ports and teardown leaks containers, so a
    silent degrade is worse than a clean refusal.

    Raised BEFORE any subnet reservation, control-plane fence or image pull, so a host that cannot
    run missions costs the operator an error message rather than a half-built run. `str(exc)`
    carries the probe's verdict AND the remediation — the CLI guard renders ContractError verbatim.
    """


class BaseImageIncompatibleError(ContractError):
    """The mission's fused image was built on a base generation this XORCISE cannot run.

    The fused image is `FROM xorcise/mission-base`, and a base MAJOR bump is breaking (e.g. the
    inner engine 27→29 move that removed the `/proc/<pid>/exe` prestart hook). An artifact fused
    on an incompatible base majors will die at deploy — now with no host-daemon fallback — so this
    refuses it up front with a direction-aware remediation (re-pull if the artifact is older,
    upgrade XORCISE if it is newer). `str(exc)` carries both."""


class PullError(ContractError):
    """Fetching the mission image failed; nothing was installed. Lives here (LEAF) so the
    catalog island can raise it without importing the delivery layer."""


class MissionNotInCatalogError(PullError):
    """The id is not installed and not in the catalog — nothing to pull."""


class PlatformUnsupportedError(PullError):
    """No execution path exists for this mission on this host (contract AS4).

    Raised BEFORE any image download, when the host's platform is not among the mission's
    validated platforms and the AMD64 emulation fallback is not available either. A host/artifact
    condition, not a registry fault: REST surfaces map it 409 (like BaseImageIncompatibleError),
    and `str(exc)` carries which platforms the mission does support."""


class UnsupportedManifestVersionError(PullError):
    """The catalog served a mission manifest this XORCISE cannot validate.

    Either the manifest declares a schema_version outside SUPPORTED_SCHEMA_VERSIONS (a newer
    cloud than this client — the remedy is upgrading XORCISE), or a supported-version document
    failed contract validation (catalog and client disagree about the shape). Typed so the
    CLI/REST surfaces render the remedy instead of a pydantic traceback; `served`/`supported`
    carry the versions for programmatic use. A PullError: nothing was installed."""

    def __init__(self, message: str, *, served: str | None, supported: tuple[str, ...]) -> None:
        super().__init__(message)
        self.served = served
        self.supported = supported


class PullCancelled(ContractError):
    """The in-flight pull was cancelled by request before install completed. NOT a failure:
    install_pulled is the only writer (atomic), so a cancel before it leaves the mission
    cleanly not-installed — the same state as a failed pull. Distinct from PullError so the
    delivery worker records the job as `cancelled`, not `error`. Raised by the pull spine when
    its `should_cancel` probe trips; deliberately NOT a PullError subclass so the spine's
    `except PullError`/`except Exception` wrappers don't relabel it (LEAF)."""
