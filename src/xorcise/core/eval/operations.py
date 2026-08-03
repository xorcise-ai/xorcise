"""Pure assertion operations — the reproducible anchor.

OPERATIONS maps an op key to a pure (value, **args) -> bool. New ops are added by registry entry,
never by changing the Check contract. The v1 vocabulary is a deliberately small seed.
"""

from __future__ import annotations

import re
from collections.abc import Callable


# The resolved value is POSITIONAL-ONLY (`/`) on every op: a check's `args` are passed as **kwargs,
# and some ops take an arg literally named `value` (lesser_than's threshold, per the spec reference
# args={"value": 25}). Positional-only keeps that kwarg from colliding with the tested value.
def _equals(value: object, /, *, expected: object) -> bool:
    return value == expected


def _matches_format(value: object, /, *, pattern: str) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _observed(value: object, /) -> bool:
    """Truthy presence: the value exists and is non-empty/non-false (e.g. a fact was recorded)."""
    return bool(value)


def _lesser_than(value: object, /, **kw: object) -> bool:
    threshold = kw.get("value")
    return (
        isinstance(value, (int, float))
        and isinstance(threshold, (int, float))
        and value < threshold
    )


OPERATIONS: dict[str, Callable[..., bool]] = {
    "equals": _equals,
    "matches_format": _matches_format,
    "observed": _observed,
    "lesser_than": _lesser_than,
}
