"""Per-run intel disclosure policy (PURE; run-control module).

A run carries a ``intel_policy`` string chosen at create time (persisted on the run row). This
module is the single interpreter of that string against a mission's authored intel — no I/O,
no cross-module import (stdlib + the Intel contract only), so both the disclosure gate
(``RunControlService.get_intel``) and the prompt/provenance surfaces agree on exactly which
authored intel a run may disclose.

Grammar (backward compatible — a run created before this feature has policy "" ⇒ ALL):
- ``""`` or ``"all"`` → every authored intel may be disclosed (DEFAULT).
- ``"none"``          → no intel.
- ``"i1,i3"``         → only those intel ids, always in AUTHORED order (never the CSV order).
Unknown ids in the CSV are ignored; whitespace around ids is trimmed.
"""

from __future__ import annotations

from collections.abc import Sequence

from xorcise.core.contracts.mission import Intel


def allowed_intel(policy: str, intel: Sequence[Intel]) -> tuple[Intel, ...]:
    """The authored intel a run under *policy* may disclose, preserving authored order."""
    normalized = policy.strip().lower()
    if normalized in ("", "all"):
        return tuple(intel)
    if normalized == "none":
        return ()
    wanted = {piece.strip() for piece in policy.split(",") if piece.strip()}
    return tuple(h for h in intel if h.id in wanted)
