"""Declarative base shared by domain-module models (SHARED-KERNEL)."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Root of all ORM models. Domain modules subclass this (application layer -> kernel)."""
