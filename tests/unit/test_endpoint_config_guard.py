"""Guard: serving modules must resolve endpoints via get_settings(), never the
bare config constants. This is the invariant whose absence let endpoints drift from config."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "xorcise"

# Modules that bind / probe / advertise / dial an endpoint. Boot modules are
# globbed so a newly-added role is covered automatically; the CLI modules are
# explicit. NOTE: this is a deliberately blunt source text scan (it will flag a
# constant named even in a comment) — a false positive is safe, it just forces a
# rewording; under-coverage is the real risk we guard against.
_BOOT = sorted(str(p.relative_to(SRC)) for p in (SRC / "core/roles/boot").glob("role_*.py"))
assert _BOOT, "boot glob resolved nothing — guard would silently under-cover"
SERVING = _BOOT + [
    "core/cli/commands/lifecycle.py",
    "core/cli/rest_client.py",
]

CONSTANTS = ("HOST", "REST_PORT", "OTLP_PORT", "RUNNER_PORT", "HEADSCALE_PORT")
_PATTERN = re.compile(r"\b(" + "|".join(CONSTANTS) + r")\b")


def test_serving_modules_do_not_reference_endpoint_constants():
    offenders = {}
    for rel in SERVING:
        text = (SRC / rel).read_text()
        hits = sorted(set(_PATTERN.findall(text)))
        if hits:
            offenders[rel] = hits
    assert offenders == {}, f"serving modules must use get_settings(), not constants: {offenders}"


def test_serving_modules_import_get_settings():
    missing = [rel for rel in SERVING if "get_settings" not in (SRC / rel).read_text()]
    assert missing == [], f"serving modules must import get_settings: {missing}"
