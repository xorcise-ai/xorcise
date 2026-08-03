"""Local filesystem browse DTOs (LEAF) — entry names + kind only, never file contents."""

from __future__ import annotations

from pydantic import BaseModel


class FsEntry(BaseModel):
    """One entry in a listed directory. `path` is absolute; contents are never read."""

    name: str
    path: str
    is_dir: bool


class FsListing(BaseModel):
    """A read-only listing of one directory for the GUI file picker."""

    path: str  # absolute, resolved directory being listed
    parent: str | None  # absolute parent dir, or None at the filesystem root
    entries: list[FsEntry]
