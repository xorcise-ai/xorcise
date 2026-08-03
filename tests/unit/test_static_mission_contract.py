"""Contract rules for the lab/static execution classification (static-mission-support).

`metadata.type` is the execution discriminator: `lab` (deployable, needs an environment) or
`static` (attachment-only, no runtime). The manifest validator enforces the conditional-environment
contract; a static manifest that still carries environment data warns (migration-compat) rather than
raising, and must never be deployed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from xorcise.core.contracts.mission import MissionManifest

_META = {"mission_id": "x", "name": "X", "objective": "o"}
_ENV = {"compose_file": "docker-compose.yml", "entry_networks": [], "static_ips": {}}
_ATT = [{"name": "attachment.zip", "path": "attachment.zip", "media_type": "application/zip"}]


def _manifest(**over: object) -> dict[str, object]:
    base: dict[str, object] = {"schema_version": "2.0", "metadata": dict(_META)}
    base.update(over)
    return base


def test_type_is_required() -> None:
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(_manifest(environment=_ENV))
    assert "type" in str(exc.value)


def test_type_rejects_ctf() -> None:
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(
            _manifest(metadata={**_META, "type": "ctf"}, environment=_ENV)
        )
    assert "type" in str(exc.value)


def test_lab_requires_environment() -> None:
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(_manifest(metadata={**_META, "type": "lab"}))
    assert "environment" in str(exc.value).lower()


def test_lab_with_environment_validates() -> None:
    m = MissionManifest.model_validate(
        _manifest(metadata={**_META, "type": "lab"}, environment=_ENV)
    )
    assert m.is_lab and not m.is_static
    assert m.static_environment_warning is None


def test_static_allows_missing_environment() -> None:
    m = MissionManifest.model_validate(
        _manifest(metadata={**_META, "type": "static"}, attachments=_ATT)
    )
    assert m.is_static and not m.is_lab
    assert m.environment is None
    assert m.static_environment_warning is None


def test_static_requires_attachment() -> None:
    with pytest.raises(ValidationError) as exc:
        MissionManifest.model_validate(_manifest(metadata={**_META, "type": "static"}))
    assert "attachment" in str(exc.value).lower()


def test_static_with_environment_warns_not_raises() -> None:
    m = MissionManifest.model_validate(
        _manifest(metadata={**_META, "type": "static"}, environment=_ENV, attachments=_ATT)
    )
    assert m.is_static
    assert m.static_environment_warning  # non-empty warning string, no raise


def test_terrain_projection_of_static_manifest_does_not_crash() -> None:
    # Regression: project_terrain_v2 fell through to the static_ips fallback for a manifest with
    # empty terrain nodes; a static mission has environment=None, so that path must not touch
    # environment.static_ips. A static bundle (terrain absent) degrades to the infra scaffold only.
    from xorcise.core.runs.terrain_v2 import project_terrain_v2

    m = MissionManifest.model_validate(
        _manifest(metadata={**_META, "type": "static"}, attachments=_ATT)
    )
    resolved = project_terrain_v2("run-1", "x", m)
    assert resolved.nodes  # infra scaffold present; no crash on the None environment
