"""role:control composition root — the control plane only (REST).

No otel receiver, no runner, no headscale plane on this box. Reuses role_all's
REST-app builder; build_otel_app is lazy in role_all, so importing this module
never drags in the otel plane.
"""

from __future__ import annotations

from xorcise.core.config import get_settings
from xorcise.core.roles.boot import AppSpec
from xorcise.core.roles.boot.role_all import build_rest_app


def apps() -> list[AppSpec]:
    s = get_settings()
    return [AppSpec(build_rest_app(), s.rest_port)]
