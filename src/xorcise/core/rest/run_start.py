"""Record that a run has started: the move from `created` to `xorcising` (#209).

A run starts the first time the agent shows it is at work: its first OTel export is admitted, it
calls run-control with the run's key, or it fetches its tailnet join key. The run-control router
and the OTLP receiver both report here. The receiver is a part-island that may not import the runs
module, so the role that boots it hands it `note_started` as a callback.

Only the last two need the run's key. The OTLP receiver takes no credential and routes a batch by
the run id the batch names, and a run id is not a secret. So anything that can reach the OTLP port
can start any run whose id it knows. That run is then graded when it ends: a stop as `operator`
and a spent budget as `timeout`, where it would have ended `cancelled` or `never_started`,
ungraded. The same batch is admitted to the run's record, so its spans also reach the transcript
the judge grades. That was true of the receiver before runs had a start. This is a known
limitation, not a boundary. The OTLP port is bound to the same addresses as the REST API, which is
unauthenticated too, so whatever can reach it can already stop or delete the run outright. Closing
it means exporters carrying the run's key in their OTLP headers.

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

    A signal that arrives after the run's budget has run out does not start it. The run is over
    in all but the watchdog's next tick, or the run-over gate this same call is about to pass, and
    either one ends a run still `created` as `never_started`, ungraded. Marking it started first
    turned that into a graded 0.00 timeout for an agent that never had any budget to use.

    Best-effort: a failure is logged, never raised, because the call it rides on (an export, a
    flag submission) must not fail over a status. The run is only remembered once the write has
    gone through, so a failed write is retried by the agent's next signal."""
    with _seen_lock:
        if run_id in _seen:
            _seen.move_to_end(run_id)
            return
    now = datetime.now(UTC)
    try:
        if runs.is_budget_expired(run_id, now):
            return  # not remembered: nothing was written, and the run is about to end anyway
        if runs.mark_started(run_id, trigger, now):
            log.info("run %s is xorcising (first signal: %s)", run_id, trigger)
    except Exception:
        log.warning("could not record that run %s started", run_id, exc_info=True)
        return
    with _seen_lock:
        _seen[run_id] = None
        while len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False)


def telemetry_started(run_id: str) -> None:
    """The OTLP receiver's `on_admitted`: an admitted export is taken as the agent at work,
    although the receiver cannot tell who sent it (see the module docstring)."""
    note_started(run_id, "telemetry")
