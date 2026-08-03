"""Mission-ingestion errors (part-island domain errors)."""

from __future__ import annotations


class PreflightError(Exception):
    """A named, actionable preflight failure — str(self) names the offending field/file."""


class MissionCollisionError(Exception):
    """A mission_id is already installed from a different source (remote library vs. local
    your_own). str(self) names the id + the owning source + how to resolve — no silent clobber."""


class AttachmentBundleError(Exception):
    """The pulled delivery bundle is missing a declared attachment or names an unsafe
    path. Raised during install_pulled's unpack; _atomic_install rolls the staging dir
    back so a bad bundle never leaves a half-installed mission."""
