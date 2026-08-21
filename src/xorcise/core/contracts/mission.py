"""Mission bundle schema — `mission.json` v2 (LEAF wire DTOs).

The single versioned contract every consumer (runner, run-control, evaluator, UI) reads.
Imports nothing internal (stdlib + pydantic only); contains NO I/O — file reading, preflight,
and the fused-image build are ingestion's job, not the schema's.

Boundary notes:
- This describes the AUTHORED bundle. `environment` is the authored container/network source;
  ingestion fuses it into the run-ready OCI image — `MissionRef.image` in
  `contracts/control.py`. `metadata.mission_id` == the `MissionRef.mission_id`.
- `rubric`/`checks` are AUTHOR declarations only; their scoring/execution semantics
  belong to the grading engine. `grading.py`'s `rubric_ref` /
  `check_set_ref` point at them at run time.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Manifest schema versions this client reads. 2.0 is the pre-versioning shape — still served
#: for history and by a pre-contract catalog (prod today) — and 3.0 adds the required
#: creator-owned `version` (mission-versioning contract, amendment A3).
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = ("2.0", "3.0")

#: Creator-owned mission SemVer: exactly MAJOR.MINOR.PATCH — no leading zeros, no
#: pre-release/build suffix. This is the served pattern verbatim (contract MV1 as amended by
#: A11); ingest refuses anything looser, so accepting more here would only defer the failure.
MISSION_VERSION_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MissionMetadata(_Frozen):
    mission_id: str  # stable id; == MissionRef.mission_id (contracts/control.py)
    name: str
    summary: str = ""  # USER-facing catalog description; NOT shown to the agent
    objective: str  # AGENT-facing mission, rendered into MissionPrompt.objective
    proficiency: str | None = None  # required skill level (was `difficulty`)
    specialty: str | None = None  # primary domain: "web" | "pwn" | "crypto" | "network" | …
    # EXECUTION classification (not a genre): "lab" needs a deployable environment; "static" is
    # attachment-only with no runtime. Required — it drives every runtime branch (preflight, ingest,
    # run-create, prompt). Replaces the old free-text ctf/scenario/boot2root genre label.
    type: Literal["lab", "static"]
    skills: tuple[str, ...] = ()  # techniques (was `competencies`); browse facet
    technologies: tuple[str, ...] = ()


class EnvironmentSpec(_Frozen):
    """Authored container/network SOURCE. Ingestion fuses this into the run-ready
    image (MissionRef.image). No file reading here — pure shape."""

    compose_file: str = "docker-compose.yml"
    entry_networks: tuple[str, ...] = ()  # player-facing nets; () => resolve at ingestion
    static_ips: dict[str, dict[str, int]] = Field(default_factory=dict)  # service->net->last octet
    # Escape hatch. Mission networks are confined (`internal: true`) so a mission's premise cannot
    # be undercut by real internet the author never intended — and so a compromised mission
    # container has no path off the tailnet. Prefer simulating an "internet" service inside the
    # mission over setting this; it is visible in the manifest precisely so it can be reviewed.
    allow_egress: bool = False
    # ACCEPTED AND IGNORED. Agent reachability is a property of the product now, not a per-mission
    # switch, so nothing reads this. It stays declared because this model is `extra="forbid"` and
    # the published catalog is versioned independently of this code: manifests carrying
    # `agent_ingress: true` are already out there, and simply deleting the field turned every one
    # of them into a hard install failure ("Extra inputs are not permitted") on a client that had
    # done nothing but upgrade. Removing it needs the catalog to stop emitting it first.
    agent_ingress: bool = True


class ArtifactSpec(_Frozen):
    """An artifact the agent is expected to submit (run-control collects it)."""

    name: str
    description: str | None = None
    required: bool = True


class RubricCriterion(_Frozen):
    """An author-declared grading criterion. Scoring is the eval engine's job, NOT here."""

    id: str
    text: str
    weight: float | None = None


# The deterministic-op vocabulary. The contract is the source of truth (this module is a LEAF —
# it cannot import the executable registry in eval/operations.py); test_eval_operations guards
# that OPERATIONS stays in sync, mirroring how Check.source ↔ eval RESOLVERS is kept honest.
CheckOp = Literal["equals", "matches_format", "observed", "lesser_than"]

# Exact arg keys each op's callable accepts — pure data, no eval import. A wrong shape would only
# surface as a TypeError at grade time (the "grading stuck forever" class), so it is gated here.
_CHECK_OP_ARGS: dict[str, frozenset[str]] = {
    "equals": frozenset({"expected"}),
    "matches_format": frozenset({"pattern"}),
    "observed": frozenset(),
    "lesser_than": frozenset({"value"}),
}


class Check(_Frozen):
    """A deterministic check: assert `op` over the value at `ref` in `source`.

    Fetching (source/ref) is decoupled from asserting (op/args). `source` names the resolver
    (artifacts | otel-stats | observed-facts); `op` names the pure operation; `args` are the op's
    arguments; `weight` is this check's slice of the deterministic half (None ⇒ equal split).
    `requires` names checks that must also pass before this check can earn its weight."""

    id: str
    source: Literal["artifacts", "otel-stats", "observed-facts"]
    ref: str
    op: CheckOp
    args: Mapping[str, object] = Field(default_factory=dict)
    weight: float | None = Field(default=None, gt=0, le=1)
    requires: tuple[Annotated[str, Field(min_length=1)], ...] = Field(
        default=(), json_schema_extra={"uniqueItems": True}
    )

    @model_validator(mode="after")
    def _check_args_shape(self) -> Check:
        """Reject an arg shape the op cannot execute. Missing/unexpected kwargs would otherwise
        raise a TypeError at grade time — the same hang class as an unknown op."""
        required = _CHECK_OP_ARGS[self.op]
        missing = sorted(required - set(self.args))
        unexpected = sorted(set(self.args) - required)
        if missing or unexpected:
            problems = []
            if missing:
                problems.append(f"missing args: {', '.join(missing)}")
            if unexpected:
                problems.append(f"unexpected args: {', '.join(unexpected)}")
            raise ValueError(f"check '{self.id}' op '{self.op}': {'; '.join(problems)}")
        if len(set(self.requires)) != len(self.requires):
            raise ValueError(f"check '{self.id}' requires contains duplicate check IDs")
        return self


class Intel(_Frozen):
    id: str
    text: str


class Attachment(_Frozen):
    """A mission companion file the agent downloads (binary, library, source, pcap).

    Bytes are never inlined in this DTO: at run time `get_attachment` mints a
    short-lived signed link the sandbox fetches over the tailnet. `path` existence is
    checked at preflight."""

    name: str
    path: str
    media_type: str | None = None
    sha256: str | None = None
    description: str | None = None


class TerrainSpec(_Frozen):
    """Optional map block. v2 authoring: permissive dict tuples for
    groups/nodes/edges so a minimal block validates; the v2 projector interprets the keys. The old
    role/gt_prev node keys are no longer read (missions re-authored; else static_ips fallback)."""

    summary: str | None = None
    nodes: tuple[dict[str, object], ...] = ()
    groups: tuple[dict[str, object], ...] = ()
    edges: tuple[dict[str, object], ...] = ()


class MissionManifest(_Frozen):
    """The `mission.json` object (schema 2.0 or 3.0) — the shared contract all consumers import.

    Required: schema_version, metadata (incl. metadata.type ∈ {lab, static}; the agent objective
    lives under metadata.objective) and — on schema 3.0 — the creator-owned SemVer `version`
    (2.0 predates the field and must not carry it; see _check_version_contract). `environment` is
    conditional: required for lab, omitted for static (see _check_execution_contract). Everything
    else defaults empty/None so a minimal bundle validates (terrain/intel absent is valid; lab
    needs environment, static needs attachments)."""

    schema_version: Literal["2.0", "3.0"]
    # The creator-owned mission SemVer (contract MV1): REQUIRED on schema 3.0, absent on 2.0.
    # Projected by the platform as the public `mission_version`; the runtime's own base SemVer
    # travels separately as `mission_base_version` and is never authored here.
    version: str | None = None
    metadata: MissionMetadata
    # Conditionally optional (static-mission-support): a "lab" mission REQUIRES this (the
    # deployable container/network source); a "static" mission omits it entirely. Enforced by
    # _check_execution_contract below.
    environment: EnvironmentSpec | None = None
    artifacts: tuple[ArtifactSpec, ...] = ()
    rubric: tuple[RubricCriterion, ...] = ()
    checks: tuple[Check, ...] = ()
    intel: tuple[Intel, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    terrain: TerrainSpec | None = None
    # Reserved premium-capable fields: declared so extra="forbid" accepts them,
    # inert for free consumers.
    source: str | None = None
    delivery: str | None = None

    @property
    def is_static(self) -> bool:
        """True for an attachment-only mission with no runtime environment."""
        return self.metadata.type == "static"

    @property
    def is_lab(self) -> bool:
        """True for a deployable mission (has an environment to fuse + spin up)."""
        return self.metadata.type == "lab"

    @property
    def static_environment_warning(self) -> str | None:
        """Non-None when a static manifest still carries environment data (migration-compat).

        The environment is IGNORED — a static mission is never deployed — but rather than fail a
        legacy bundle we surface a warning so authors know the block is dead weight. Callers must
        NEVER deploy a static mission on the strength of a lingering environment block."""
        if self.is_static and self.environment is not None:
            return (
                "static mission declares an 'environment' block; it is ignored and never "
                "deployed — remove it from the manifest"
            )
        return None

    @model_validator(mode="after")
    def _check_version_contract(self) -> MissionManifest:
        """Enforce the schema/version pairing: 3.0 requires the creator SemVer, 2.0 forbids it.

        2.0 must refuse the field for parity with every other 2.0 consumer (`extra="forbid"`
        everywhere): a 2.0 document carrying `version` never existed in the wild and accepting
        one here would mint a shape no other validator reads."""
        if self.schema_version == "3.0" and self.version is None:
            raise ValueError("manifest schema 3.0 requires 'version' (the mission's SemVer)")
        if self.schema_version == "2.0" and self.version is not None:
            raise ValueError("'version' requires manifest schema 3.0 (2.0 predates the field)")
        if self.version is not None and not re.fullmatch(MISSION_VERSION_PATTERN, self.version):
            raise ValueError(
                "version must be MAJOR.MINOR.PATCH with no leading zeros and no "
                f"pre-release/build suffix (e.g. 1.4.2), got {self.version!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_execution_contract(self) -> MissionManifest:
        """Enforce the lab/static execution contract (static-mission-support).

        `lab` needs a deployable environment; `static` needs at least one attachment and no
        environment is required (a lingering one only warns — see static_environment_warning)."""
        if self.is_lab and self.environment is None:
            raise ValueError("lab mission requires an 'environment' block")
        if self.is_static and not self.attachments:
            raise ValueError("static mission requires at least one attachment")
        return self

    @model_validator(mode="after")
    def _check_weights(self) -> MissionManifest:
        """Check weights are the deterministic half's slices: ALL declare or NONE
        (equal split), and declared weights sum to 1.0. Lives on the manifest because the rule
        needs all checks at once. Empty checks is valid (the scoring math has a fallback)."""
        ws = [c.weight for c in self.checks]
        if not ws:
            return self
        declared = [w for w in ws if w is not None]
        if declared and len(declared) != len(ws):
            raise ValueError("checks must ALL declare weight or NONE declare it (no mix)")
        if declared and abs(sum(declared) - 1.0) > 1e-6:
            raise ValueError(f"check weights must sum to 1.0, got {sum(declared):.4f}")
        return self

    @model_validator(mode="after")
    def _check_dependencies(self) -> MissionManifest:
        """Dependencies must name a unique, acyclic set of checks in this manifest."""
        checks_by_id = {check.id: check for check in self.checks}
        if len(checks_by_id) != len(self.checks):
            raise ValueError("check IDs must be unique")

        for check in self.checks:
            for required_id in check.requires:
                if required_id not in checks_by_id:
                    raise ValueError(f"check '{check.id}' requires unknown check '{required_id}'")
                if required_id == check.id:
                    raise ValueError(f"check '{check.id}' cannot require itself")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(check_id: str) -> None:
            if check_id in visiting:
                raise ValueError(f"check dependency cycle includes '{check_id}'")
            if check_id in visited:
                return
            visiting.add(check_id)
            for required_id in checks_by_id[check_id].requires:
                visit(required_id)
            visiting.remove(check_id)
            visited.add(check_id)

        for check_id in checks_by_id:
            visit(check_id)
        return self
