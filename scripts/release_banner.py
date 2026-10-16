#!/usr/bin/env python
"""Draw the banner that opens a release's notes, for one release tag.

The release workflow renders it at the tag, publishes it to the `release-assets` branch and puts
it at the top of the notes (.github/workflows/release.yml), so nothing per release is committed
to main.

Every glyph is a path. A GitHub release page shows the banner through an <img>, and an <img>
cannot load web fonts, so text set in a font would fall back to whatever the viewer has. The
outlines come from JetBrains Mono, the brand's monospace (SIL Open Font License 1.1), and live in
`release_banner_glyphs.json` beside this script. Standard library only, so the release build needs
nothing installed to run it.

Usage:  python scripts/release_banner.py v0.2.0 > banner.svg
        python scripts/release_banner.py --extract-glyphs FONT_DIR   # needs fontTools
Exit:   0 = written; 2 = not a release tag.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# The glyph file's shape: {"units_per_em", "weights": {weight: {char: [advance, path]}}, "lockup"}.
Glyphs = dict[str, Any]

GLYPHS_FILE = Path(__file__).with_name("release_banner_glyphs.json")
README_BANNER = Path(__file__).resolve().parents[1] / "assets" / "readme-banner.svg"

# The same rule the release workflow validates a tag with.
TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$")

WIDTH, HEIGHT = 880, 300
RIGHT = 834  # the right column's edge
VERSION_MAX_WIDTH = 440  # keeps a long tag clear of the lockup on the left
VERSION_SIZE = 92.0

TAGLINE = "Trust Evidence, not Claims."
PROMPT, COMMAND = "$ ", "pip install -U xorcise"
SUBLINE = "EVIDENCE · ENVIRONMENTS · RUNS"
LABELS = {False: "RELEASE", True: "PRE-RELEASE"}

# Which characters each weight needs. The version weight covers everything TAG_RE accepts.
CHARSETS = {
    "Regular": TAGLINE + PROMPT + SUBLINE + "".join(LABELS.values()),
    "Medium": COMMAND,
    "ExtraBold": "v0123456789.abrc",
}


def _text(
    glyphs: Glyphs, weight: str, s: str, size: float, spacing: float = 0.0
) -> tuple[str, float]:
    """`s` as glyph paths in font units at the origin, and its width in px at `size`."""
    font = glyphs["weights"][weight]
    scale = size / glyphs["units_per_em"]
    parts, pen = [], 0.0
    for ch in s:
        advance, d = font[ch]
        if d:
            parts.append(f'<path transform="translate({pen:g},0)" d="{d}"/>')
        pen += advance + spacing / scale
    width = (pen - spacing / scale) * scale if s else 0.0
    return "".join(parts), width


def _place(
    paths: str, x: float, y: float, size: float, upem: int, fill: str, attrs: str = ""
) -> str:
    scale = size / upem
    return (
        f'<g{attrs} fill="{fill}" transform="translate({x:.2f},{y:.2f}) '
        f'scale({scale:.5f},{-scale:.5f})">{paths}</g>'
    )


def render(tag: str, glyphs: Glyphs | None = None) -> str:
    """The banner for `tag` as a standalone SVG document."""
    if not TAG_RE.match(tag):
        raise ValueError(f"not a release tag: {tag!r}")
    g = glyphs if glyphs is not None else json.loads(GLYPHS_FILE.read_text())
    upem = g["units_per_em"]
    prerelease = bool(re.search(r"(a|b|rc)[0-9]+$", tag))
    title = f"XORCISE.AI {tag}" + (" (pre-release)" if prerelease else "")

    tagline, _ = _text(g, "Regular", TAGLINE, 13, 0.9)
    prompt, prompt_w = _text(g, "Regular", PROMPT, 12.5, 0.3)
    command, command_w = _text(g, "Medium", COMMAND, 12.5, 0.3)
    subline, sub_w = _text(g, "Regular", SUBLINE, 11, 2.4)
    label, label_w = _text(g, "Regular", LABELS[prerelease], 10.5, 3.2)

    # The version fits a fixed box: a long pre-release tag shrinks rather than reach the lockup.
    _, natural = _text(g, "ExtraBold", tag, VERSION_SIZE, -2.0)
    size = VERSION_SIZE * min(1.0, VERSION_MAX_WIDTH / natural)
    vee, vee_w = _text(g, "ExtraBold", "v", size, -2.0 * size / VERSION_SIZE)
    number, number_w = _text(g, "ExtraBold", tag[1:], size, -2.0 * size / VERSION_SIZE)
    gap = -2.0 * size / VERSION_SIZE
    left = RIGHT - number_w - gap - vee_w
    baseline = 190.0
    box_w = label_w + 40
    cmd_x = 44 + prompt_w + 2

    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'width="{WIDTH}" height="{HEIGHT}" role="img" aria-label="{title}">',
            f"<title>{title}</title>",
            "<defs>",
            '<pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">'
            '<circle cx="1" cy="1" r="1" fill="#e8b84b" fill-opacity=".085"/></pattern>',
            '<radialGradient id="bloom"><stop offset="0" stop-color="#e8b84b" stop-opacity=".16"/>'
            '<stop offset=".65" stop-color="#e8b84b" stop-opacity="0"/></radialGradient>',
            '<filter id="glow" x="-20%" y="-40%" width="140%" height="180%">'
            '<feGaussianBlur in="SourceGraphic" stdDeviation="9" result="near"/>'
            '<feGaussianBlur in="SourceGraphic" stdDeviation="22" result="far"/>'
            '<feComponentTransfer in="near" result="near2"><feFuncA type="linear" slope=".55"/>'
            "</feComponentTransfer>"
            '<feComponentTransfer in="far" result="far2"><feFuncA type="linear" slope=".3"/>'
            "</feComponentTransfer>"
            '<feMerge><feMergeNode in="far2"/><feMergeNode in="near2"/>'
            '<feMergeNode in="SourceGraphic"/></feMerge></filter>',
            "</defs>",
            f'<rect width="{WIDTH}" height="{HEIGHT}" rx="6" fill="#0f0c07"/>',
            f'<rect width="{WIDTH}" height="{HEIGHT}" rx="6" fill="url(#grid)"/>',
            '<circle cx="178" cy="110" r="150" fill="url(#bloom)"/>',
            '<circle cx="710" cy="170" r="230" fill="url(#bloom)"/>',
            # The brand mark's geometry as a faint scope: the cross stays inside the ring.
            '<g transform="translate(592,20) scale(6.5)" opacity=".16" fill="none" '
            'stroke="#e8b84b">'
            '<circle cx="20" cy="20" r="15" stroke-width=".55"/>'
            '<circle cx="20" cy="20" r="10.5" stroke-width=".25" stroke-dasharray=".6 1.2"/>'
            '<path d="M20 8V32 M8 20H32" stroke-width=".55" stroke-linecap="round"/></g>',
            f'<g transform="translate(28,70)">{g["lockup"]}</g>',
            _place(tagline, 44, 170, 13, upem, "#c7bb9f"),
            _place(prompt, 44, 209, 12.5, upem, "#9a8a5e"),
            _place(command, cmd_x, 209, 12.5, upem, "#e8b84b"),
            f'<rect x="{cmd_x + command_w + 4:.1f}" y="198" width="7" height="13" '
            'fill="#e8b84b" opacity=".85"/>',
            f'<g id="version" filter="url(#glow)">'
            f"{_place(vee, left, baseline, size, upem, '#e8b84b')}"
            f"{_place(number, left + vee_w + gap, baseline, size, upem, '#ffcf6a')}</g>",
            _place(subline, RIGHT - sub_w, 226, 11, upem, "#6e6144"),
            f'<rect x="{RIGHT - box_w:.1f}" y="88" width="{box_w:.1f}" height="26" rx="4" '
            'fill="#0f0c07" fill-opacity=".6" stroke="#e8b84b" stroke-opacity=".28"/>',
            f'<circle cx="{RIGHT - box_w + 15:.1f}" cy="101" r="3.4" fill="#e8b84b"/>',
            _place(label, RIGHT - 12 - label_w, 106, 10.5, upem, "#9a8a5e"),
            f'<rect x=".5" y=".5" width="{WIDTH - 1}" height="{HEIGHT - 1}" rx="6" fill="none" '
            'stroke="#e8b84b" stroke-opacity=".14"/>',
            "</svg>",
            "",
        ]
    )


def _lockup_from_readme_banner() -> str:
    """The mark + wordmark group from the README banner, the brand's outlined lockup."""
    svg = README_BANNER.read_text()
    start = svg.index('<g transform="translate(248.125,46.000) scale(1.55)">')
    body = svg.index(">", start) + 1
    depth, i = 1, body
    while depth:
        opening, closing = svg.find("<g", i), svg.find("</g>", i)
        if opening != -1 and opening < closing:
            depth, i = depth + 1, opening + 2
        else:
            depth, i = depth - 1, closing + 4
    return " ".join(svg[body : i - 4].split())


def extract_glyphs(font_dir: Path) -> Glyphs:
    """Read the outlines `CHARSETS` names from JetBrains Mono TTFs in `font_dir`."""
    from fontTools.pens.svgPathPen import SVGPathPen  # type: ignore[import-not-found]
    from fontTools.ttLib import TTFont  # type: ignore[import-not-found]

    weights: dict[str, dict[str, list[Any]]] = {}
    upem = 0
    for weight, chars in CHARSETS.items():
        font = TTFont(font_dir / f"JetBrainsMono-{weight}.ttf")
        upem = font["head"].unitsPerEm
        glyph_set, cmap = font.getGlyphSet(), font.getBestCmap()
        table: dict[str, list[Any]] = {}
        for ch in sorted(set(chars)):
            glyph = glyph_set[cmap[ord(ch)]]
            pen = SVGPathPen(glyph_set)
            glyph.draw(pen)
            table[ch] = [glyph.width, pen.getCommands()]
        weights[weight] = table
    return {
        "source": "JetBrains Mono 2.304, SIL Open Font License 1.1 "
        "(github.com/JetBrains/JetBrainsMono)",
        "units_per_em": upem,
        "weights": weights,
        "lockup": _lockup_from_readme_banner(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("tag", nargs="?", help="A release tag, such as v0.2.0 or v0.2.1rc1.")
    ap.add_argument(
        "--extract-glyphs",
        metavar="FONT_DIR",
        type=Path,
        help="Regenerate release_banner_glyphs.json from JetBrains Mono TTFs.",
    )
    args = ap.parse_args()
    if args.extract_glyphs:
        GLYPHS_FILE.write_text(json.dumps(extract_glyphs(args.extract_glyphs), indent=1) + "\n")
        print(f"wrote {GLYPHS_FILE}", file=sys.stderr)
        return 0
    if not args.tag or not TAG_RE.match(args.tag):
        print(
            f"error: not a release tag: {args.tag!r} (expected v0.2.0 or v0.2.1rc1)",
            file=sys.stderr,
        )
        return 2
    sys.stdout.write(render(args.tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
