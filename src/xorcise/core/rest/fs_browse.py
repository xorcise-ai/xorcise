"""Read-only local directory listing for the GUI file picker (application layer, local-only).

Lists a directory's entries (name + is_dir) so the operator can browse the server
host's filesystem and pick a local agent file in the GUI. It NEVER reads file
contents. XORCISE runs as a local, single-user application served on the loopback
interface (deployment_topology="local"), so surfacing the local tree to the GUI is
by design, not a leak.
"""

from __future__ import annotations

from pathlib import Path

from xorcise.core.contracts.fs import FsEntry, FsListing


class BrowseError(ValueError):
    """A path can't be listed: missing, not a directory, or unreadable."""


def list_directory(path: str | None = None) -> FsListing:
    """List one directory. Defaults to the user's home when *path* is omitted.

    Entries are sorted directories-first, then files, case-insensitively. Children
    that error on stat (broken symlinks, races) are skipped rather than failing the
    whole listing. Raises BrowseError for a missing / non-dir / unreadable target.
    """
    target = Path(path).expanduser() if path else Path.home()
    try:
        target = target.resolve()
    except OSError as exc:  # pragma: no cover - resolve rarely raises on listing
        raise BrowseError(f"cannot resolve path: {path}") from exc
    if not target.exists():
        raise BrowseError(f"no such directory: {target}")
    if not target.is_dir():
        raise BrowseError(f"not a directory: {target}")

    try:
        children = list(target.iterdir())
    except PermissionError as exc:
        raise BrowseError(f"permission denied: {target}") from exc

    entries: list[FsEntry] = []
    for child in children:
        try:
            is_dir = child.is_dir()
        except OSError:
            continue  # broken symlink / race — skip, never read contents
        entries.append(FsEntry(name=child.name, path=str(child), is_dir=is_dir))
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))

    parent = str(target.parent) if target.parent != target else None
    return FsListing(path=str(target), parent=parent, entries=entries)
