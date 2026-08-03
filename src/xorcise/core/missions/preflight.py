"""Bundle preflight: parse + validate mission.json against the contract,
verify referenced files exist, and raise a NAMED error on any problem. No side-effects."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from xorcise.core.contracts.mission import MissionManifest
from xorcise.core.missions.errors import PreflightError

_SUPPORTED = "2.0"


def preflight(bundle_dir: Path) -> MissionManifest:
    cj = bundle_dir / "mission.json"
    if not cj.is_file():
        raise PreflightError(f"missing mission.json in bundle: {cj}")
    try:
        raw = json.loads(cj.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise PreflightError(f"mission.json is not readable JSON: {e}") from e
    if not isinstance(raw, dict):
        raise PreflightError("mission.json must be a JSON object")

    version = raw.get("schema_version")
    if version is None:
        raise PreflightError("missing required field: schema_version")
    if version != _SUPPORTED:
        raise PreflightError(f"unsupported schema version: {version} (supported: {_SUPPORTED})")

    try:
        manifest = MissionManifest.model_validate(raw)
    except ValidationError as e:
        # Name each offending field by its loc; a model-level validator (e.g. the lab/static
        # execution-contract check) has an empty loc, so fall back to its message so the reason
        # (e.g. "static mission requires at least one attachment") is not lost. A NESTED
        # model-validator failure (e.g. a Check's op arg-shape rule) has both loc and reason —
        # keep both, else "checks.0" alone would hide why the check is invalid.
        parts: list[str] = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"])
            if not loc:
                parts.append(err["msg"])
            elif err["type"] == "value_error":
                parts.append(f"{loc}: {err['msg']}")
            else:
                parts.append(loc)
        raise PreflightError(f"invalid mission.json: {', '.join(parts)}") from e

    # A lab mission deploys an environment, so its compose file must exist on disk; a static
    # mission has no runtime and no compose file (the contract already required its attachments).
    if manifest.is_lab:
        assert manifest.environment is not None  # is_lab ⇒ environment present (contract-enforced)
        compose = bundle_dir / manifest.environment.compose_file
        if not compose.is_file():
            raise PreflightError(f"environment.compose_file not found: {compose}")
    for att in manifest.attachments:
        if not (bundle_dir / att.path).is_file():
            raise PreflightError(f"attachment '{att.name}' file not found: {bundle_dir / att.path}")

    return manifest
