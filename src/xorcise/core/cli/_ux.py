"""Shared CLI presentation kit — one visual + behavioural language (cli).

Every command renders through these helpers so the CLI reads as one product:
clean borderless tables, humanized local timestamps, short ids, friendly labels
for internal vocabularies (run states, agent kinds, mission sources), and
compact actionable errors. Rendering only — resolvers that call the server live
in ``_resolve``.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NoReturn

import typer
from rich import box
from rich.table import Table

from xorcise.core.cli._shared import console, err_console

if TYPE_CHECKING:
    from rich.console import Console
    from rich.progress import Progress

DASH = "—"

# Agent kind slugs → readable harness names (mirrors the GUI's vocabulary).
_KIND_LABELS = {
    "claude-code": "Claude Code",
    "codex": "Codex",
    "openhands": "OpenHands",
    "langchain": "LangChain",
    "gemini": "Gemini CLI",
    "goose": "Goose",
    "cai": "CAI",
}

# Mission source slugs → the labels the GUI catalog uses.
_SOURCE_LABELS = {"library": "Library", "your_own": "Your Own"}

# Internal plane/service keys → the one human-facing vocabulary (raw keys stay
# in JSON and verbose diagnostics only).
_SERVICE_LABELS = {
    "rest": "REST API",
    "otlp": "OTLP receiver",
    "docker": "Docker",
    "runner": "Runner",
    "headscale": "Headscale",
}


def service_label(key: str | None) -> str:
    return _SERVICE_LABELS.get(key or "", key or DASH)


def command_path_from_argv() -> str:
    """Best-effort 'xorcise <group> <command>' for the CURRENT invocation, by
    walking argv down the real command tree. Unknown tokens stop the walk, so a
    CliRunner under pytest degrades safely to plain 'xorcise'. Used for
    operation-aware error guidance ('retry: <the command you just ran>')."""
    try:
        from typer.main import get_command

        from xorcise.core.cli._shared import app as _app

        node: object = get_command(_app)
    except Exception:  # noqa: BLE001 — guidance must never be the thing that fails
        return "xorcise"
    parts = ["xorcise"]
    for tok in sys.argv[1:]:
        sub = getattr(node, "commands", {}).get(tok)
        if sub is None:
            break
        parts.append(tok)
        node = sub
    return " ".join(parts)


# Columns that may wrap on a narrow terminal — prose-ish text a reader can
# follow over two lines. Everything else is copy-critical (ids, scores, states,
# timestamps) and must never truncate or wrap.
_FLEX_COLUMNS = frozenset({"Name", "Agent", "Mission", "Model", "Endpoint", "Address"})


def ux_table(*columns: str, title: str | None = None) -> Table:
    """A clean, borderless table: bold headers over a single rule, no box walls.

    One factory so every list view (agents, missions, runs, status, leaderboard)
    shares the exact same look. Copy-critical cells (ids, scores, states) never
    wrap or truncate; prose columns may wrap on a narrow terminal (print with
    :func:`print_table`)."""
    table = Table(
        *columns,
        box=box.SIMPLE_HEAD,
        title=title,
        title_style="bold",
        title_justify="left",
        header_style="bold",
        pad_edge=False,
    )
    for column in table.columns:
        column.no_wrap = str(column.header) not in _FLEX_COLUMNS
        if not column.no_wrap:
            # A squeezed prose cell folds onto a second line — nothing is ever
            # lost to an ellipsis (agent names feed `run create --agent`).
            column.overflow = "fold"
    return table


def _pin_column_widths(table: Table) -> None:
    """Give every no-wrap column its full content width (capped), so a narrow
    terminal shrinks the wrappable prose columns instead of truncating ids,
    scores, or headers to 'process-of-eliminati…' / '0.…' / 'Ru…'."""
    from rich.text import Text

    for column in table.columns:
        if not column.no_wrap:
            continue
        cells = list(getattr(column, "_cells", ()) or ())
        widest = max(
            (Text.from_markup(c).cell_len for c in cells if isinstance(c, str)),
            default=0,
        )
        column.min_width = min(30, max(widest, Text.from_markup(str(column.header)).cell_len))


def print_table(table: Table) -> None:
    """Print a table width-aware: the real terminal width interactively, a wide
    virtual console when piped — never a guessed 80 columns that wraps rows."""
    _pin_column_widths(table)
    if console.is_terminal:
        console.print(table)
        return
    from rich.console import Console

    from xorcise.core.cli._shared import XORCISE_THEME

    Console(theme=XORCISE_THEME, width=200, soft_wrap=True).print(table)


def short_id(full: str) -> str:
    """First 8 chars — enough to be unique in any realistic local run list."""
    return full[:8]


def kind_label(kind: str | None) -> str:
    """Agent kind slug → readable label; unregistered/no harness reads as Custom."""
    if not kind:
        return "Custom"
    return _KIND_LABELS.get(kind, kind.replace("-", " ").title())


def source_label(source: str | None) -> str:
    return _SOURCE_LABELS.get(source or "", (source or DASH).replace("_", " ").title())


def difficulty_label(proficiency: str | None) -> str:
    return proficiency.title() if proficiency else DASH


def size_label(size_bytes: int | None) -> str:
    """Byte count → the download size a person reads, or a dash when it is unknown.

    Decimal units, mirroring the GUI's formatBytes (frontend missions/queries.ts) so the
    CLI and the console quote the same mission with the same number — the parity rule
    run_state_label follows for run outcomes.

    None means UNKNOWN, not zero: an installed mission carries no size (it is already
    downloaded) and a catalog predating the size fields serves none. Rendering "0 B"
    there would state a falsehood, so it reads as a dash.
    """
    if size_bytes is None:
        return DASH
    n = float(size_bytes)
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.1f} MB"
    if n >= 1e3:
        return f"{n / 1e3:.1f} KB"
    return f"{max(0, round(n))} B"


def run_state_label(state: str | None, trigger: str | None = None) -> str:
    """Server-side run state (+ terminal trigger) → the user-facing result word.

    Mirrors the GUI's runPresentation vocabulary (frontend run-state.ts) so both
    surfaces describe the same run the same way."""
    if state == "created":
        return "Created"
    if state == "active":
        return "Running"
    if state == "terminal":
        return {
            "done": "Completed",
            "completed": "Completed",
            "timeout": "Timed out",
            "budget": "Partial",
            "error": "Failed",
            "crashed": "Crashed",
            "operator": "Terminated",
        }.get(trigger or "", "Ended")
    return (state or DASH).title()


# Status words → semantic theme styles (functional colours stay status-only).
_STATE_STYLES = {
    "Completed": "ok",
    "Running": "accent",
    "Partial": "warn",
    "Timed out": "err",
    "Crashed": "err",
    "Failed": "err",
}


def run_state_markup(state: str | None, trigger: str | None = None) -> str:
    """The state label wrapped in its status colour, for table cells."""
    label = run_state_label(state, trigger)
    style = _STATE_STYLES.get(label, "dim")
    return f"[{style}]{label}[/{style}]"


# Prose is wrapped to the terminal, capped for readability — 200-char lines on a
# wide terminal are as unreadable as an unwrapped 1,500-char objective is on a
# narrow one. Data (ids, commands, filenames) is never wrapped.
PROSE_MEASURE = 100


def measure() -> int:
    """Width to wrap prose at: the live terminal, 80 when piped, capped at 100."""
    return min(console.width, PROSE_MEASURE)


def heading(text: str) -> None:
    console.print(f"\n[bold]{text}[/bold]")


def prose(body: str, *, indent: int = 2) -> None:
    """Print a paragraph wrapped to the terminal width.

    Pre-wrapped here (not left to Rich) because the shared consoles are
    ``soft_wrap=True`` — without this a 1,500-character objective printed as one
    physical line that broke `| less`, editors and pasted tickets."""
    import textwrap

    from rich.text import Text

    pad = " " * indent
    for para in str(body).split("\n"):
        if not para.strip():
            console.print("")
            continue
        for line in textwrap.wrap(
            para.strip(), width=max(20, measure() - indent), break_long_words=False
        ):
            # Text(), not markup: manifest prose is author-supplied and must never
            # be parsed for markup NOR repainted by Rich's repr highlighter.
            console.print(Text(pad + line))


def field(label: str, value: object, *, width: int = 14, indent: int = 2) -> None:
    """One aligned `Label   value` line, value wrapped under a hanging indent."""
    import textwrap

    from rich.text import Text

    pad = " " * indent
    head = f"{pad}{str(label).ljust(width)}  "
    text = str(value)
    room = max(20, measure() - len(head))
    lines = textwrap.wrap(text, width=room, break_long_words=False) or [""]
    console.print(Text(head + lines[0]))
    for extra in lines[1:]:
        console.print(Text(" " * len(head) + extra))


def fmt_score(value: object) -> str:
    """A score for the human view: 2 decimals, consistent with run list + leaderboard.

    Never leaks a raw float repr (0.40700000000000003); non-numbers pass through as
    their string. --json keeps the exact value — this is display only."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    return f"{value:.2f}"


def humanize_when(iso: str | None, *, now: datetime | None = None) -> str:
    """ISO timestamp → 'Today 06:53' / 'Yesterday 15:39' / 'Jul 23 22:28' local time.

    Human views only — JSON output always carries the raw ISO timestamps."""
    if not iso:
        return DASH
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    if dt.tzinfo is None:  # server timestamps are UTC; older rows may lack the zone
        dt = dt.replace(tzinfo=UTC)
    local = dt.astimezone()
    ref = (now or datetime.now(UTC)).astimezone()
    days = (ref.date() - local.date()).days
    if days == 0:
        return f"Today {local:%H:%M}"
    if days == 1:
        return f"Yesterday {local:%H:%M}"
    if local.year == ref.year:
        return f"{local:%b %d %H:%M}"
    return f"{local:%Y-%m-%d}"


def fail(
    message: str,
    *,
    example: str | None = None,
    see: tuple[str, ...] = (),
    code: int = 1,
) -> NoReturn:
    """Print a compact, actionable error on stderr and exit.

    What happened → what to do now: an optional copy-pasteable example plus
    'see:' pointers to the commands that answer the follow-up question."""
    err_console.print(f"[err]error[/err]: {message}", markup=True)
    if example:
        err_console.print(f"\ntry:\n  [value]{example}[/value]")
    for pointer in see:
        err_console.print(f"see: [value]{pointer}[/value]")
    raise typer.Exit(code)


def _stdin_is_interactive() -> bool:
    """Seam for tests — CliRunner swaps sys.stdin, so isatty can't be patched directly."""
    return sys.stdin.isatty()


def confirm_or_abort(question: str, *, assume_yes: bool) -> None:
    """TTY-gated confirmation for destructive commands.

    --yes and non-interactive stdin both skip the prompt (a prompt would hang CI
    or an agent harness; scripts keep their historical no-prompt behaviour)."""
    if assume_yes or not _stdin_is_interactive():
        return
    if not typer.confirm(question):
        console.print("aborted")
        raise typer.Exit(1)


def next_step(command: str, label: str | None = None) -> None:
    """The single next action after a successful command, copy-pasteable."""
    prefix = f"{label} → " if label else "next: "
    console.print(f"{prefix}{command}", markup=False)


def live_spinner(target: Console | None = None) -> Progress:
    """The product's one indeterminate-progress look: amber spinner, label, elapsed.

    Honest progress only — no bar, no percentage — for steps whose length is
    unknown (subprocess builds, container teardowns). ``transient`` because the
    spinner is liveness, not record: it vanishes on completion and the caller's
    own permanent lines stand alone. Defaults to stderr — the mission-pull
    idiom — so a redirected stdout stays clean while an interactive terminal
    still sees life. ``target`` must carry the XORCISE theme (every product
    console does) so the "accent" spinner style resolves."""
    from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

    return Progress(
        SpinnerColumn(style="accent"),
        TextColumn("{task.description}"),
        TimeElapsedColumn(),
        console=target or err_console,
        transient=True,
    )


@contextmanager
def step_progress(label: str) -> Iterator[None]:
    """A transient spinner while a slow, opaque step runs (interactive stderr only).

    Renders on stderr like every live progress in the product (mission pull's
    _watch_pull), so ``xorcise down > log`` still shows liveness while the
    captured stdout stays byte-identical to the historical line-based output."""
    if not err_console.is_terminal:
        yield
        return
    with live_spinner() as progress:
        progress.add_task(label, total=None)
        yield
