"""Shared test helpers."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from xorcise.core.contracts.control import MissionRef
from xorcise.core.contracts.mission import EnvironmentSpec, MissionManifest, MissionMetadata
from xorcise.core.missions.runtime import INSTALLED_FILE, InstalledMission


def agent_nodes(
    nodes_json: Sequence[Mapping[str, object]],
    orchestrator_user: str,
    router_tag: str = "tag:router",
) -> list[str]:
    """Names of AGENT nodes on a Headscale control plane (real-Headscale guard).

    An agent node is any node that is NOT the orchestrator user and is NOT a router (router_tag).
    A non-empty result means a live XORCISE run is using this control plane — the real-Headscale
    tests must refuse to run against it rather than clobber its ACL."""
    out: list[str] = []
    for n in nodes_json:
        raw_tags = n.get("tags")
        tags = raw_tags if isinstance(raw_tags, list) else []
        user = n.get("user")
        uname = user.get("name", "") if isinstance(user, dict) else str(user)
        if router_tag in tags or uname == orchestrator_user:
            continue
        out.append(str(n.get("name", "")))
    return out


def stray_agent_nodes(
    container: str,
    orchestrator_user: str = "orchestrator",
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    """Agent nodes currently live on the Headscale *container* (real-Headscale guard).

    Queries `headscale nodes list` and returns agent-node names via agent_nodes(). An unreachable
    control plane (non-zero exit) returns [] — the caller's skip-guards already handle absence."""
    proc = runner(
        ["docker", "exec", container, "headscale", "nodes", "list", "-o", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    # headscale prints the JSON literal `null` (not []) for an empty node set.
    return agent_nodes(json.loads(proc.stdout or "[]") or [], orchestrator_user)


def install_mission(home: Path, slug: str = "c1") -> None:
    """Write a minimal installed-mission record under <home>/missions/<slug>/."""
    root = Path(home) / "missions" / slug
    root.mkdir(parents=True, exist_ok=True)
    manifest = MissionManifest(
        schema_version="2.0",
        metadata=MissionMetadata(mission_id=slug, name=slug, objective="Solve it.", type="lab"),
        environment=EnvironmentSpec(),
    )
    ref = MissionRef(mission_id=slug, image=f"xorcise/mission-{slug}:0")
    (root / INSTALLED_FILE).write_text(InstalledMission(slug, root, manifest, ref).to_record())


class CliArgvRejected(AssertionError):
    """click rejected argv, so the command never ran and there is no verdict to report.

    Kept OFF the exit-code channel deliberately. A parser error exits 2 — the code these
    commands choose for a usage error, and the code every refusal test here asserts — so
    returning it conflates "the guard refused this input" with "that flag does not exist".
    Measured: mapped to its code, `config set-model --key-stdinX` returned 2 and printed
    "No such option: --key-stdinX (Possible options: --key-stdin)", which passed the `== 2`
    and the then-current `"stdin" in stderr` of the closed-stdin test alike — click's own
    suggestion line carries the word. A renamed or deleted flag read as a passing guard.
    """


def invoke_cli_with_stdin(argv: list[str], stdin: object) -> int:
    """Run the real CLI parser with an EXACT ``sys.stdin``; returns the exit code.

    CliRunner always builds its own text stream from ``input=``, so the two inputs that matter
    here are the two it cannot express: the ``None`` CPython leaves when fd 0 is CLOSED
    (`xorcise … <&-`), and a bare CR, which only the runner's stream rewrites into ``\\n``.
    Everything else still goes through click — the options arrive with their true ``None``/
    ``False`` defaults, where calling a Typer command function directly would hand it
    ``OptionInfo`` objects and prove nothing.

    The exit code is the COMMAND's own outcome, and a test can now assert a pass and not only
    a refusal: ``standalone_mode=False`` returns click's ``Exit.exit_code`` for `fail()` and
    ``None`` for a command that simply finished (0 here — ``int(None)`` used to raise instead,
    so a guard that refused every key would have passed the whole section). Anything click
    raises out of the parser is NOT that, and arrives as `CliArgvRejected`; anything else
    propagates untouched.
    """
    import sys

    import typer.main

    from xorcise.core.cli._shared import app

    command = typer.main.get_command(app)
    real, sys.stdin = sys.stdin, stdin
    try:
        rv = command.main(args=argv, prog_name="xorcise", standalone_mode=False)
    except Exception as exc:
        # Identified by the ClickException CONTRACT — carries an exit code, and a `show()` that
        # would have printed it — rather than by the class: typer vendors its own click, so the
        # parser raises `typer._click.exceptions.NoSuchOption`, which is NOT a subclass of the
        # installed `click.ClickException`, and `except click.ClickException` would catch none
        # of it. `show` is a probe here and is never called: the message goes into the failure
        # instead, because RETURNING `exc.exit_code` hands back 2 — the same code, and in the
        # closed-stdin case the same "stdin" in the text, as the refusals these tests assert.
        # Anything that fails the probe is some other bug and must reach the test untouched.
        code, show = getattr(exc, "exit_code", None), getattr(exc, "show", None)
        if code is None or not callable(show):
            raise
        raise CliArgvRejected(f"click rejected {argv} (exit {int(code)}): {exc}") from exc
    finally:
        sys.stdin = real
    return 0 if rv is None else int(rv)
