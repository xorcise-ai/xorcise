"""Compact, actionable usage-error rendering (cli).

Typer renders parser errors as a wide red panel; ``install_compact_errors()``
replaces the renderer (``typer.rich_utils.rich_format_error`` — the single
funnel both TyperCommand and TyperGroup route through, for the installed binary
and CliRunner alike) with a three-part answer: what happened, a correct example
or Did-you-mean, and where to get help. Exit codes stay click's own (usage
error = 2); ``NoArgsIsHelpError`` is downgraded to 0 — a bare group invocation
is a help request, not a mistake.

Typer 0.26 vendors click as ``typer._click``, so exceptions are matched by
class NAME (duck-typing) — an ``isinstance`` against the installed click
package would silently never match.
"""

from __future__ import annotations

import re
import sys
from typing import Any

from rich.console import Console

from xorcise.core.cli._shared import XORCISE_THEME
from xorcise.core.cli._ux import command_path_from_argv

# Curated copy-pasteable examples per command path, shown on missing input.
_EXAMPLES = {
    "xorcise agent register": "xorcise agent register --name my-agent",
    "xorcise agent update": "xorcise agent update --name my-agent --model claude-sonnet-5",
    "xorcise agent rename": "xorcise agent rename old-name new-name",
    "xorcise agent history": "xorcise agent history my-agent",
    "xorcise agent rm": "xorcise agent rm my-agent",
    "xorcise agent delete": "xorcise agent delete my-agent",
    "xorcise mission show": "xorcise mission show aviary-access",
    "xorcise mission pull": "xorcise mission pull aviary-access",
    "xorcise mission update": "xorcise mission update aviary-access",
    "xorcise mission delete": "xorcise mission delete aviary-access",
    "xorcise mission rm": "xorcise mission rm aviary-access",
    "xorcise mission ingest": "xorcise mission ingest ./my-mission-bundle",
    "xorcise run create": "xorcise run create --agent my-agent --mission aviary-access",
    "xorcise run status": "xorcise run status <run-id>",
    "xorcise run report": "xorcise run report <run-id>",
    "xorcise run traces": "xorcise run traces <run-id>",
    "xorcise run terminate": "xorcise run terminate <run-id>",
    "xorcise run delete": "xorcise run delete <run-id>",
    "xorcise run prompt": "xorcise run prompt <run-id>",
    "xorcise run launch-cmd": "xorcise run launch-cmd <run-id>",
    "xorcise run launch-profile": "xorcise run launch-profile <run-id>",
    "xorcise run events export": "xorcise run events export <run-id>",
    "xorcise config set-model": "xorcise config set-model --name gpt-4o-mini --key sk-…",
}

# Commands whose required inputs should be explained TOGETHER (not first-missing-only).
_REQUIRED_TOGETHER: dict[str, tuple[str, tuple[str, ...]]] = {
    "xorcise run create": (
        "an agent and a mission are required",
        ("xorcise agent list", "xorcise mission list"),
    ),
}

# Internal param names → the words a person reads ('missing new' is not a sentence).
_PARAM_PROSE = {
    "old": "current agent name",
    "new": "new agent name",
    "run_id": "run id",
    "mission_id": "mission id",
    "bundle_dir": "bundle directory",
}

# Run-id positional commands: point at where the ids live.
_SEE_BY_PARAM = {
    "run_id": "xorcise run list",
    "mission_id": "xorcise mission list",
    "name": "xorcise agent list",
    "old": "xorcise agent list",
}

_ENV_ASSIGN = re.compile(r"^[A-Z_][A-Z0-9_]*=")
# The LAST parenthesised group — the message itself contains "argument(s)".
_EXTRAS = re.compile(r"\(([^()]*)\)\s*$")


def _console() -> Console:
    # A fresh Console per call: rich resolves sys.stderr lazily, which keeps
    # CliRunner's stream swapping (and the real binary) both correct.
    return Console(theme=XORCISE_THEME, stderr=True, soft_wrap=True)


def _print_error(
    message: str,
    *,
    path: str,
    suggestions: tuple[str, ...] = (),
    example: str | None = None,
    see: tuple[str, ...] = (),
) -> None:
    console = _console()
    console.print(f"[err]error[/err]: {message}", highlight=False)
    if suggestions:
        console.print("\nDid you mean?")
        for s in suggestions:
            console.print(f"  [value]{s}[/value]")
    if example:
        console.print(f"\ntry:\n  [value]{example}[/value]")
    for pointer in see:
        console.print(f"see: [value]{pointer}[/value]")
    console.print(f"\n[dim]run '{path} --help' for all options[/dim]")


def _group_commands(command: Any) -> dict[str, Any]:
    return dict(getattr(command, "commands", None) or {})


def _all_option_names(command: Any) -> list[str]:
    names: list[str] = []
    for param in getattr(command, "params", ()):
        names += list(getattr(param, "opts", ())) + list(getattr(param, "secondary_opts", ()))
    return [n for n in names if n.startswith("--")]


def _render_no_such_option(exc: Any, ctx: Any, path: str) -> None:
    opt = str(getattr(exc, "option_name", "") or "")
    suggestions: list[str] = []
    # `xorcise agent --list`: the "option" is really a sibling subcommand.
    sub = opt.lstrip("-")
    if ctx is not None and sub in _group_commands(ctx.command):
        suggestions.append(f"{path} {sub}")
    for p in getattr(exc, "possibilities", None) or ():
        suggestions.append(f"{path} … {p}")
    if not suggestions and ctx is not None:
        from difflib import get_close_matches

        # Match on the BARE names — the shared '--' prefix donates free ratio and
        # would turn --bogus into a "close" match for --timeout. No plausible
        # match → no suggestion (a wrong suggestion is anti-actionable).
        by_bare = {n.lstrip("-"): n for n in _all_option_names(ctx.command)}
        for close in get_close_matches(opt.lstrip("-"), list(by_bare), n=2, cutoff=0.6):
            suggestions.append(f"{path} … {by_bare[close]}")
    _print_error(f"no such option: {opt}", path=path, suggestions=tuple(suggestions))


def _render_missing_parameter(exc: Any, ctx: Any, path: str) -> None:
    # `agent register history` (no --name): click reports the missing option before
    # it notices 'history' is really a sibling subcommand — recover the intent first.
    sibling = _argv_sibling_suggestion()
    if sibling is not None:
        _print_error(f"unexpected argument on {path}", path=path, suggestions=(sibling,))
        return
    combined = _REQUIRED_TOGETHER.get(path)
    if combined is not None:
        message, see = combined
        _print_error(message, path=path, example=_EXAMPLES.get(path), see=see)
        return
    param = getattr(exc, "param", None)
    param_name = str(getattr(param, "name", "") or "value")
    opts = list(getattr(param, "opts", ()) or ())
    if opts and str(opts[0]).startswith("-"):
        message = f"missing option {opts[0]}"
    else:
        message = f"missing {_PARAM_PROSE.get(param_name, param_name.replace('_', ' '))}"
    pointer = _SEE_BY_PARAM.get(param_name)
    if path.endswith(" register"):  # a NEW name — pointing at the existing list is noise
        pointer = None
    _print_error(
        message,
        path=path,
        example=_EXAMPLES.get(path),
        see=(pointer,) if pointer else (),
    )


def _first_positional_of(command: Any) -> Any:
    """The command's first positional parameter (its ARGUMENT), if it has one."""
    for param in getattr(command, "params", ()):
        opts = list(getattr(param, "opts", ()) or ())
        if opts and not str(opts[0]).startswith("-"):
            return param
    return None


def _first_positional_param(ctx: Any) -> Any:
    return _first_positional_of(getattr(ctx, "command", None))


def _first_positional_value(ctx: Any) -> str | None:
    param = _first_positional_param(ctx)
    if param is None:
        return None
    value = (getattr(ctx, "params", {}) or {}).get(param.name)
    return value if isinstance(value, str) else None


def _argv_sibling_suggestion() -> str | None:
    """When a trailing token is a sibling subcommand of the resolved command's
    parent group ('agent register history' → the user meant 'agent history'),
    return the corrected 'xorcise <group> <sibling>'. Works from argv so it fires
    even on the MissingParameter path, where click reports the missing option
    before it ever notices the stray subcommand token."""
    try:
        from typer.main import get_command

        from xorcise.core.cli._shared import app as _app

        node: Any = get_command(_app)
    except Exception:  # noqa: BLE001 — guidance must never be the thing that fails
        return None
    parts = ["xorcise"]
    parent: Any = node
    trailing: list[str] = []
    for tok in (t for t in sys.argv[1:] if not t.startswith("-")):
        sub = getattr(node, "commands", {}).get(tok)
        if sub is not None and not trailing:
            parent, node = node, sub
            parts.append(tok)
        else:
            trailing.append(tok)
    if trailing and trailing[0] in _group_commands(parent):
        group = " ".join(parts[:-1])  # drop the resolved leaf, keep 'xorcise agent'
        return f"{group} {trailing[0]}"
    return None


def _carry_value(ctx: Any, sibling: Any) -> str | None:
    """A value the user already typed that the suggested sibling can take as its
    positional argument (`register --name codex` → `history codex`)."""
    positional = _first_positional_of(sibling)
    if positional is None or ctx is None:
        return None
    params = getattr(ctx, "params", {}) or {}
    value = params.get(getattr(positional, "name", "")) or params.get("name")
    return value if isinstance(value, str) and value else None


def _render_extra_args(message: str, ctx: Any, path: str) -> None:
    extras_match = _EXTRAS.search(message)
    extras = extras_match.group(1).split() if extras_match else []
    # `XORCISE_DEBUG=1` after the command: explain env-var placement.
    if extras and _ENV_ASSIGN.match(extras[0]):
        _print_error(
            f"{extras[0]} is an environment variable — it must come before the command",
            path=path,
            example=f"{extras[0]} {path}",
        )
        return
    # `xorcise agent register history --name codex`: the extra arg is a sibling
    # subcommand — suggest it WITH the value the user already typed, so the
    # suggestion is paste-and-run rather than a second usage error.
    parent = getattr(ctx, "parent", None)
    if extras and parent is not None and extras[0] in _group_commands(parent.command):
        suggestion = f"{parent.command_path} {extras[0]}"
        carried = _carry_value(ctx, _group_commands(parent.command)[extras[0]])
        if carried:
            suggestion += f" {carried}"
        _print_error(
            f"unexpected argument: {' '.join(extras)}",
            path=path,
            suggestions=(suggestion,),
        )
        return
    # A run id never contains spaces — quoting the tokens together would only
    # yield a second 'no run matching' error. Say it's malformed, point at the list.
    first_param = _first_positional_param(ctx) if ctx is not None else None
    first_name = str(getattr(first_param, "name", "") or "")
    if extras and first_name == "run_id":
        _print_error(
            f"unexpected argument: {' '.join(extras)} — a run id has no spaces",
            path=path,
            see=("xorcise run list",),
        )
        return
    # `xorcise mission show Aviary Access`: a NAME with spaces needs quoting.
    first = _first_positional_value(ctx) if ctx is not None else None
    if extras and first is not None:
        quoted = " ".join([first, *extras])
        _print_error(
            f"unexpected argument: {' '.join(extras)} — names with spaces must be quoted",
            path=path,
            example=f'{path} "{quoted}"',
        )
        return
    _print_error(f"unexpected argument: {' '.join(extras) or message}", path=path)


def compact_usage_error(exc: Any) -> None:
    """Replacement for typer.rich_utils.rich_format_error — compact + actionable."""
    name = type(exc).__name__
    if name == "NoArgsIsHelpError":
        # The group's help is already on stdout (printed when the exception was
        # built); a bare group invocation is a help request → success, not error.
        exc.exit_code = 0
        return
    ctx = getattr(exc, "ctx", None)
    path = getattr(ctx, "command_path", None) or command_path_from_argv()
    if name == "NoSuchOption":
        _render_no_such_option(exc, ctx, path)
        return
    if name == "MissingParameter":
        _render_missing_parameter(exc, ctx, path)
        return
    message = str(getattr(exc, "message", "") or exc.format_message())
    if "unexpected extra argument" in message.lower():
        _render_extra_args(message, ctx, path)
        return
    # BadParameter carries the offending param — name it, or the message reads headless.
    param_opts = list(getattr(getattr(exc, "param", None), "opts", ()) or ())
    if param_opts:
        message = f"invalid value for {param_opts[0]}: {message}"
    # Everything else (unknown command — typer already appends its own
    # "Did you mean …?" — bad parameter values, missing option values, …):
    # keep the text, drop the box, add the command's example when we have one.
    _print_error(message, path=path, example=_EXAMPLES.get(path))


def install_compact_errors() -> None:
    """Swap typer's rich error panel for the compact renderer (idempotent)."""
    import typer.rich_utils

    typer.rich_utils.rich_format_error = compact_usage_error  # type: ignore[assignment]
