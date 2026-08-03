"""The agents table model (domain module; subclasses the kernel Base)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from xorcise.core.db import Base


class AgentRow(Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("name", name="uq_agents_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    endpoint: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    otel: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    kind: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    launch_command_template: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    launch_tips: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    mission_preamble: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, default=None)
    launch_mode: Mapped[Literal["host", "container"] | None] = mapped_column(
        String(16), nullable=True, default=None
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
