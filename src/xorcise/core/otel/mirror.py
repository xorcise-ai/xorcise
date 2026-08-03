"""Reserved, default-off customer-OTel mirror seam (PART-ISLAND).

The product ships local-only telemetry: nothing leaves the host by default.
The customer-OTel mirror is the natural otelcol second-exporter and is a future
addition. This module is only the reserved config seam + a fail-fast guard — there is
no exporter and no forwarding code path. When the exporter lands later, resolve_mirror
becomes the single place that returns a configured exporter instead of raising; the
operator config (otel_mirror_enabled + otel_mirror_endpoint) is already in place, so it
drops in without a rebuild.

Imports only the shared kernel (xorcise.core.config) — never a domain module (dependency rule).
"""

from __future__ import annotations

from xorcise.core.config import Settings


class MirrorConfigError(Exception):
    """The customer-OTel mirror was enabled, but it is a reserved, unimplemented seam."""


def resolve_mirror(settings: Settings) -> None:
    """Validate the mirror posture at boot.

    Off (default) -> returns None (no forwarding; the reserved happy path).
    Enabled (with or without an endpoint) -> raises MirrorConfigError: the mirror is a
    reserved future seam and is not implemented yet.
    """
    if not settings.otel_mirror_enabled:
        return
    raise MirrorConfigError(
        "XORCISE_OTEL_MIRROR_ENABLED is set, but the customer-OTel mirror is a reserved, "
        "unimplemented seam — keep it off; telemetry stays local-only "
        "(nothing leaves the host by default)."
    )
