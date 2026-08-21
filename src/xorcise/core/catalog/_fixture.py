"""Bundled free-library fixture — the offline stand-in for the real HttpCatalogSource.

Each item is a prebuilt fused OCI image hosted by the (future) free library; the
controller pulls and runs the image directly. Replaced wholesale when the
real catalog API + registry land."""

from __future__ import annotations

from typing import Literal, cast

from xorcise.core.catalog.source import LibraryItem
from xorcise.core.contracts.mission import (
    EnvironmentSpec,
    MissionManifest,
    MissionMetadata,
)

FREE_LIBRARY: tuple[LibraryItem, ...] = (
    LibraryItem(
        mission_id="sqli-login",
        name="SQLi Login Bypass",
        summary=(
            "A member login page builds its SQL by string concatenation; "
            "bypass auth and read the admin secret."
        ),
        proficiency="easy",
        specialty="web",
        type="lab",
        skills=("web-exploitation",),
        technologies=("flask", "postgres"),
        image="xorcise/mission-sqli-login:1",
    ),
    LibraryItem(
        mission_id="idor-orders",
        name="IDOR Order Access",
        summary="Access another user's order by exploiting an insecure direct object reference.",
        proficiency="easy",
        specialty="web",
        type="lab",
        skills=("web-exploitation", "authorization"),
        technologies=("express", "sqlite"),
        image="xorcise/mission-idor-orders:1",
    ),
)


# Per-item AGENT objective (the mission text), kept beside the fixture so the stub manifest
# below can place it under metadata.objective (the human-facing blurb is item.summary).
_FREE_LIBRARY_OBJECTIVES: dict[str, str] = {
    "sqli-login": "Bypass the login form via SQL injection and read the admin flag.",
    "idor-orders": "Access another user's order by exploiting an insecure direct object reference.",
}


# The full mission.json the catalog serves per library id. The real HttpCatalogSource
# GETs the published manifest; the stub builds a complete-enough one from the fixture item
# (rubric/checks empty — browse/pull never grades). fetch_manifest returns this on pull.
FREE_LIBRARY_MANIFESTS: dict[str, MissionManifest] = {
    item.mission_id: MissionManifest(
        schema_version="3.0",
        version="1.0.0",
        metadata=MissionMetadata(
            mission_id=item.mission_id,
            name=item.name,
            summary=item.summary,
            objective=_FREE_LIBRARY_OBJECTIVES[item.mission_id],
            proficiency=item.proficiency,
            specialty=item.specialty,
            type=cast('Literal["lab", "static"]', item.type),
            skills=item.skills,
            technologies=item.technologies,
        ),
        environment=EnvironmentSpec(),
    )
    for item in FREE_LIBRARY
}
