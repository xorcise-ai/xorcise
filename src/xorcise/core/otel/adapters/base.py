"""Adapter framework (part-island): the AgentTraceAdapter seam + context. Imports
only contracts + otel.flatten (stdlib beyond that)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from xorcise.core.contracts.agent_event import (
    AgentEvent,
    AgentEventKind,
    HarnessCapabilityProfile,
    KindSupport,
)
from xorcise.core.otel.flatten import FlatLogRecord, FlatSpan


@dataclass(frozen=True)
class AdapterContext:
    run_id: str
    source_agent: str
    mission_id: str
    created_at: datetime


def profile_from(
    name: str,
    version: str,
    *,
    supported: Iterable[AgentEventKind] = (),
    partial: Iterable[AgentEventKind] = (),
    message_roles: Mapping[str, KindSupport | str] | None = None,
    notes: Mapping[str, str] | None = None,
    verified: bool = True,
) -> HarnessCapabilityProfile:
    """Build a TOTAL profile: listed kinds get their level, every other kind is unsupported."""
    kinds = {k.value: KindSupport.unsupported for k in AgentEventKind}
    kinds.update({k.value: KindSupport.supported for k in supported})
    kinds.update({k.value: KindSupport.partial for k in partial})
    return HarnessCapabilityProfile(
        adapter_name=name,
        adapter_version=version,
        verified=verified,
        kinds=kinds,
        message_roles={role: KindSupport(level) for role, level in (message_roles or {}).items()},
        notes=dict(notes or {}),
    )


class AgentTraceAdapter(ABC):
    name: str
    version: str

    @property
    @abstractmethod
    def capabilities(self) -> HarnessCapabilityProfile:
        """This adapter's declared telemetry profile (total over AgentEventKind)."""

    @abstractmethod
    def normalize(self, spans: list[FlatSpan], ctx: AdapterContext) -> list[AgentEvent]: ...

    def normalize_logs(self, logs: list[FlatLogRecord], ctx: AdapterContext) -> list[AgentEvent]:
        """Map OTLP LOG records to AgentEvents. Default: none — a harness whose logs
        carry no display content (or that emits no logs) simply overrides nothing. Pure + total."""
        return []
