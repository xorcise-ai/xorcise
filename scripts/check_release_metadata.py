#!/usr/bin/env python
"""Release-only gate: the built artifacts must match the tag and carry no placeholders.

PyPI versions are immutable. Once `xorcise 0.1.0` is published it can never be replaced —
only yanked and superseded. That makes two classes of mistake permanent, so both are checked
here BEFORE anything is uploaded:

1. **Tag/version drift.** hatch-vcs derives the version from git. A shallow clone, a missing
   tag, or a tag pushed to the wrong commit yields a `0.1.dev…` version that would publish
   under a name nobody asked for. We compare the built version against the pushed tag using
   PEP 440 normalisation, so `v0.1.0rc1` and `0.1.0rc1` compare equal.

2. **Unresolved placeholders.** `pyproject.toml` ships `xorcise-ai/xorcise` placeholders until the
   real GitHub org exists. Package metadata is baked at build time, so a forgotten substitution
   would put a dead `https://github.com/xorcise-ai/xorcise` link on the PyPI sidebar of a release
   that can never be edited. Fail loudly instead.

Usage:  python scripts/check_release_metadata.py --tag v0.1.0rc1 [DIST_DIR]
Exit:   0 = safe to publish; 1 = do not publish.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path

# Any `<WORD>` token surviving into metadata means an unsubstituted placeholder.
PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*>")

# Metadata fields worth scanning. Body (the long description / README) is included because the
# README is rendered on the PyPI project page and carries badge + install URLs.
SCANNED_HEADERS = ("Home-page", "Project-URL", "Author-email", "Maintainer-email", "Summary")


def _fail(msg: str) -> None:
    print(f"RELEASE-GUARD: {msg}")


def read_metadata(wheel: Path):
    with zipfile.ZipFile(wheel) as zf:
        name = next(
            (n for n in zf.namelist() if n.endswith(".dist-info/METADATA")),
            None,
        )
        if name is None:
            raise RuntimeError(f"no .dist-info/METADATA inside {wheel.name}")
        with zf.open(name) as fh:
            return BytesParser().parse(fh)


def normalise(version: str) -> str:
    """PEP 440 normalisation so `v0.1.0rc1`, `0.1.0-rc1` and `0.1.0rc1` all compare equal."""
    from packaging.version import InvalidVersion, Version

    try:
        return str(Version(version))
    except InvalidVersion as exc:  # pragma: no cover - surfaced as a clear failure below
        raise RuntimeError(f"not a valid PEP 440 version: {version!r} ({exc})") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True, help="the pushed git tag, e.g. v0.1.0rc1")
    ap.add_argument("dist", nargs="?", default="dist", help="directory holding the artifacts")
    args = ap.parse_args(argv)

    dist = Path(args.dist)
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        _fail(f"expected exactly 1 wheel in {dist}, found {len(wheels)}")
        return 1
    wheel = wheels[0]

    errors: list[str] = []

    # --- 1. tag vs built version -------------------------------------------------------
    tag = args.tag
    if not tag.startswith("v"):
        errors.append(f"tag {tag!r} does not start with 'v' (expected e.g. v0.1.0rc1)")
    tag_version = tag[1:] if tag.startswith("v") else tag

    meta = read_metadata(wheel)
    built_version = meta.get("Version", "")
    print(f"wheel:         {wheel.name}")
    print(f"tag:           {tag}")
    print(f"built version: {built_version}")

    try:
        if normalise(tag_version) != normalise(built_version):
            errors.append(
                f"version mismatch: tag {tag!r} -> {normalise(tag_version)} but the built "
                f"package is {normalise(built_version)}. This usually means the checkout was "
                "shallow (hatch-vcs needs full history + tags) or the tag is not on this commit."
            )
        else:
            print(f"version match:  {normalise(built_version)} ✓")
    except RuntimeError as exc:
        errors.append(str(exc))

    # A dev/local version must never reach PyPI even if the tag somehow agrees.
    if any(marker in built_version for marker in (".dev", "+g", "+d")):
        errors.append(
            f"built version {built_version!r} is a development version (contains .dev/+local). "
            "Release builds must come from an exact tagged commit with full git history."
        )

    # --- 2. unresolved placeholders ----------------------------------------------------
    # `get_all` yields `email.header.Header` objects (not `str`) whenever a value was folded or
    # carries non-ASCII, so every value is coerced before it is scanned. Skipping that raises a
    # TypeError mid-check — which still fails the release, but with a traceback instead of the
    # actionable message a maintainer needs at that moment.
    for header in SCANNED_HEADERS:
        for raw in meta.get_all(header) or []:
            value = str(raw)
            for hit in PLACEHOLDER.findall(value):
                errors.append(
                    f"unresolved placeholder {hit} in metadata field {header}: {value!r}. "
                    "Substitute the real GitHub owner/repo in pyproject.toml before releasing."
                )
    body = meta.get_payload()
    if body is not None and not isinstance(body, list):
        hits = sorted(set(PLACEHOLDER.findall(str(body))))
        if hits:
            errors.append(
                f"unresolved placeholder(s) {hits} in the long description (README.md), which is "
                "rendered on the PyPI project page. Substitute them before releasing."
            )

    if errors:
        for e in errors:
            _fail(e)
        return 1

    print("release metadata OK — version matches the tag and no placeholders remain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
