"""role:collector composition root — OTLP receiver + trace store only."""

from __future__ import annotations

from xorcise.core.config import get_settings
from xorcise.core.roles.boot import AppSpec


def apps() -> list[AppSpec]:
    from xorcise.core.otel.ingest.drops import drop_recorder_from_settings
    from xorcise.core.otel.ingest.embedded import create_otel_app
    from xorcise.core.otel.mirror import resolve_mirror
    from xorcise.core.otel.store import SqliteSealStore
    from xorcise.core.rest.run_start import telemetry_started

    resolve_mirror(get_settings())  # fail fast if the reserved mirror is enabled
    return [
        AppSpec(
            create_otel_app(
                seal_store=SqliteSealStore(),
                drops=drop_recorder_from_settings(get_settings()),
                on_admitted=telemetry_started,
            ),
            get_settings().otlp_port,
            "warning",
        )
    ]
