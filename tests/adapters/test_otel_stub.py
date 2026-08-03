from __future__ import annotations

import pytest

from tests.adapters._contracts import OtelIngestContract, TraceStoreContract
from xorcise.core.otel.ingest import StubOtelIngest
from xorcise.core.otel.ports import OtelIngest, TraceStore
from xorcise.core.otel.store import InMemoryTraceStore


class TestStubOtelIngest(OtelIngestContract):
    @pytest.fixture
    def ingest(self) -> OtelIngest:
        return StubOtelIngest()


class TestInMemoryTraceStore(TraceStoreContract):
    @pytest.fixture
    def store(self) -> TraceStore:
        return InMemoryTraceStore()
