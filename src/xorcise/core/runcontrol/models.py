"""The run_submissions table (domain module; subclasses the kernel Base)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from xorcise.core.db import Base


class RunSubmissionRow(Base):
    __tablename__ = "run_submissions"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # flag | artifact | intel | complete
    name: Mapped[str] = mapped_column(String(255), default="")
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
