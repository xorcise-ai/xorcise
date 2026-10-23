"""Record that a run has started: the move from `created` to `xorcising` (#209).

A run starts the first time the agent does something only the agent can do: its first OTel export
is admitted, it calls run-control with the run's key, or it fetches its tailnet join key. The
run-control router and the OTLP receiver both report here. The receiver is a part-island that may
not import the runs module, so the role that boots it hands it `note_started` as a callback.

`runs.mark_started` is first-wins, so a repeat is harmless, but it is a database write, and a run
exports a batch every few seconds. This process remembers the runs it has already seen start and
skips them. The memory is bounded: run ids reach it from OTLP resource attributes, which name any
id an exporter likes, and the receiver admits a batch for a run that does not exist.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from datetime import UTC, datetime

from xorcise.core import runs
from xorcise.core.contracts.run import StartTrigger

log = logging.getLogger(__name__)

# Most recently seen last. Forgetting the oldest only costs that run one more no-op UPDATE.
_SEEN_MAX = 10_000
_seen_lock = threading.Lock()
_seen: OrderedDict[str, None] = OrderedDict()


def note_started(run_id: str, trigger: StartTrigger) -> None:
    """Move *run_id* to `xorcising` if it is still `created`, recording *trigger* and now.

    Best-effort: a failure is logged, never raised, because the call it rides on (an export, a
    flag submission) must not fail over a status. The run is only remembered once the write has
    gone through, so a failed write is retried by the agent's next signal."""
    with _seen_lock:
        if run_id in _seen:
            _seen.move_to_end(run_id)
            return
    try:
        if runs.mark_started(run_id, trigger, datetime.now(UTC)):
            log.info("run %s is xorcising (first signal: %s)", run_id, trigger)
    except Exception:
        log.warning("could not record that run %s started", run_id, exc_info=True)
        return
    with _seen_lock:
        _seen[run_id] = None
        while len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False)


def telemetry_started(run_id: str) -> None:
    """The OTLP receiver's `on_admitted`: an admitted export is the agent at work."""
    note_started(run_id, "telemetry")
