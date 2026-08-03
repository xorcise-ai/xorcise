"""One Typer app + two Rich consoles, shared by every command module (cli).

``console`` renders data and human feedback on stdout; ``err_console`` carries
error text on stderr so scripted callers can separate the streams. Both share
the XORCISE theme: amber is the brand accent, green/yellow/red stay status-only,
and semantic style names keep commands from hardcoding colors.
"""

from __future__ import annotations

import json
from typing import Any

import typer
from rich.console import Console
from rich.theme import Theme

XORCISE_THEME = Theme(
    {
        "accent": "#e8b84b",
        "accent.dim": "#a8842f",
        "ok": "green",
        "warn": "yellow",
        "err": "bold red",
        "dim": "grey58",
        "value": "bold default",
    }
)

# THE golden path — one canonical list, rendered both by the root epilog and by
# `up`'s ready banner, so the product never shows a user two different step lists
# (and a mandatory step like the judge model can't be missing from one of them).
GOLDEN_PATH: tuple[tuple[str, str], ...] = (
    ("Start XORCISE", "xorcise up"),
    ("Register an agent", "xorcise agent register --name my-agent"),
    ("Add a judge model", "xorcise config set-model --name <model> --key <key>"),
    ("Pick a mission", "xorcise mission list"),
    ("Install it", "xorcise mission pull <id>"),
    ("Create a run", "xorcise run create --agent my-agent --mission <id>"),
    ("Launch your agent", "xorcise run launch-cmd <run-id>"),
)


def golden_path_steps(*, skip_first: bool = False) -> list[str]:
    """The golden path as numbered, column-aligned lines (80-col safe).

    ``skip_first`` drops "Start XORCISE" — for `up`, which just did it."""
    steps = GOLDEN_PATH[1:] if skip_first else GOLDEN_PATH
    width = max(len(label) for label, _ in steps)
    return [f"  {i}. {label.ljust(width)}  {cmd}" for i, (label, cmd) in enumerate(steps, 1)]


# Root epilog: the golden path as readable steps. The full exit-code reference
# lives on the command groups that need it (run --help, status --help) — the
# root screen stays a first-run guide, not a contract document.
# Typer joins single newlines inside an epilog paragraph, so each step is its
# own paragraph (\n\n) to render as a list.
_EPILOG = "Get started:\n\n" + "\n\n".join(golden_path_steps()) + "\n\nTrust Evidence, not Claims."

# The machine contract, referenced from the command groups where it matters.
EXIT_CODES_EPILOG = (
    "Exit codes: 0 success · 1 runtime failure · 2 usage error · "
    "3 operation still in progress · 130 interrupted"
)

app = typer.Typer(
    name="xorcise",
    help="XORCISE — evaluate cyber AI agents.",
    epilog=_EPILOG,
    # `-h` is the near-universal help short form; accept it everywhere (click
    # propagates help_option_names to subcommands through the context).
    context_settings={"help_option_names": ["-h", "--help"]},
)
# soft_wrap: never hard-wrap at a guessed width — piped/captured output stays verbatim.
console = Console(theme=XORCISE_THEME, soft_wrap=True)
err_console = Console(theme=XORCISE_THEME, stderr=True, soft_wrap=True)


def emit_json(data: Any) -> None:
    """Print ``data`` as JSON on stdout, bypassing Rich — parseable, never wrapped."""
    typer.echo(json.dumps(data, indent=2, default=str))
