"""`xorcise config` group — view + set local config (the BYOM judge + terrain models).

Routes through the running server: `show` GETs /config and every setter PUTs its
/config/* endpoint, so the controller that OWNS runtime config applies the change (and clears its
own settings cache) immediately — no server restart. The server persists it to ~/.xorcise/.env.
Like every other stateful CLI command, these require the server to be up.

CLI/GUI parity: the Settings page's judge-test and terrain-model cards each have a command here,
over the same REST endpoints (no new backend surface). `set-network` is the ONE deliberate
exception in the other direction — CLI-only, because a two-field GUI form for multi-machine
addresses looked finished while mostly producing an unbootable server. The GUI shows those values
read-only once set (Environment card) so the state is still visible, just not editable there.
"""

from __future__ import annotations

from typing import Any

import typer

from xorcise.core.cli._shared import app, console, emit_json, err_console
from xorcise.core.cli._ux import DASH, fail, field
from xorcise.core.cli.rest_client import RestClient

config_app = typer.Typer(
    help="View and set evaluation settings (judge model, terrain model, network).",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config", rich_help_panel="Configuration")

# A live model test actually calls the provider, so it can take as long as the judge timeout
# (model_timeout_seconds, default 120s) plus slack — well past the 5s control-call default.
_LIVE_TEST_TIMEOUT_SECONDS = 180.0


_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_W = 16  # one value column for every section


def _section(title: str, state: str = "") -> None:
    console.print(f"\n[bold]{title}[/bold]{'  ' + state if state else ''}", highlight=False)


def _managed_headscale_url() -> str:
    """The headscale_url OUR OWN `xorcise up` provisioning wrote, or "" if we never wrote one.

    Needed because `headscale_url` being SET does not mean "distributed": `up` writes it (and
    the advertise host) for the Headscale it provisions locally, so a truthiness test read a
    perfectly healthy single-host install as a remote deployment — and then offered to clear
    the values `up` itself depends on. Comparing against what our managed block wrote is the
    same discriminator `up` and the /system headscale probe use (provision.managed_url).
    """
    try:
        from pathlib import Path

        from xorcise.core.headscale import provision
        from xorcise.core.home import xorcise_home

        return provision.managed_url(Path(xorcise_home()) / "config.toml")
    except Exception:
        # A config we cannot read must not be reported as a remote deployment — "Local" is the
        # non-alarming reading, and the addresses themselves are printed either way.
        return ""


def _render_config(view: dict[str, Any]) -> None:
    """Render the full ConfigView — judge, terrain, library, network, defaults.

    Sectioned key/value blocks on one value column: the flat run of `key: value`
    lines mixed five unrelated subjects at three different indents, and a leaf
    (`default budget:`) looked identical to a section header. Keys stay masked;
    `--json` (the machine contract) never reaches here."""
    console.print("[bold]XORCISE settings[/bold]")

    j = view["judge"]
    _section("Judge model", "Configured" if j["configured"] else "Not configured")
    field("Model", j.get("model_name") or DASH, width=_W)
    base = j.get("base_url")
    field("Base URL", base or f"{_DEFAULT_BASE_URL}   (default)", width=_W)
    field("API key", j.get("key_hint") or DASH, width=_W)
    field("Timeout", f"{j.get('timeout_seconds') or 120}s", width=_W)
    tcap = j.get("transcript_max_tokens") or 0
    field(
        "Transcript cap",
        "Off (rely on model limit)" if tcap == 0 else f"{tcap} tokens",
        width=_W,
    )
    span_cap = j.get("span_max_tokens")
    span_cap = 2000 if span_cap is None else span_cap
    field("Per-span cap", "Disabled" if span_cap == 0 else f"{span_cap} tokens", width=_W)
    field("Tokenizer", j.get("tokenizer") or "o200k_base", width=_W)
    if not j["configured"]:
        console.print("  [dim]set it: xorcise config set-model --name <model> --key <key>[/dim]")

    t = view.get("terrain") or {}
    inherits = t.get("uses_judge_default", True)
    _section("Terrain model", "Judge default" if inherits else "Custom override")
    if inherits:
        # Nothing of its own — say so once instead of repeating "(judge default)".
        console.print("  [dim]inherits the judge model above[/dim]")
    else:
        field("Model", t.get("model_name") or DASH, width=_W)
        field("Base URL", t.get("base_url") or f"{_DEFAULT_BASE_URL}   (default)", width=_W)
        field("API key", t.get("key_hint") or DASH, width=_W)
    field("Transcript cap", f"{t.get('transcript_max_tokens') or 256000} tokens", width=_W)

    c = view.get("catalog") or {}
    # Configuration, NOT liveness — `catalog status` does the live check.
    _section("Mission library", "Enabled" if c.get("connected") else "Disabled")
    field("Endpoint", c.get("url") or DASH, width=_W)
    console.print(
        "  [dim]live check: xorcise catalog status[/dim]"
        if c.get("connected")
        else "  [dim]enable it: xorcise catalog connect[/dim]"
    )

    n = view.get("network") or {}
    url = (n.get("headscale_url") or "").strip()
    external = bool(url) and url != _managed_headscale_url()
    _section("Network", "Remote control plane (experimental)" if external else "Local")
    field("Headscale URL", url or DASH, width=_W)
    field("Advertise host", n.get("advertise_host") or DASH, width=_W)
    if external:
        # Only an EXTERNAL control plane earns this: it is the known cause of a server that boots
        # but cannot start a run, and `set-network` is its only editor — so the undo belongs
        # right next to the readout.
        console.print("  [dim]applies on the next xorcise up[/dim]")
        console.print('  [dim]back to local: xorcise config set-network --headscale-url ""[/dim]')

    _section("Defaults")
    field("Run budget", f"{view['default_budget_seconds']}s", width=_W)


def _report_test(result: dict[str, Any], label: str, *, as_json: bool) -> None:
    """Report a JudgeTestResult and exit non-zero when the model did not answer.

    The exit code is the same in both output modes, so a script can gate on `config test`
    whether it reads the rendered line or the JSON body.
    """
    name = result.get("model_name") or "—"
    if as_json is True:
        emit_json(result)
    elif result.get("ok"):
        console.print(f"[ok]{label} ok[/ok] — model={name}")
    elif result.get("status") == "not_configured":
        # Step 3 of the golden path — the answer is the command that fixes it.
        err_console.print(f"[err]error[/err]: {label} is not configured")
        err_console.print(
            "set it: [value]xorcise config set-model --name <model> --key <key>[/value]"
        )
    else:
        # A configured model that did not answer: name it, keep the server's reason.
        err_console.print(
            f"[err]error[/err]: {label} did not answer — model={name}: "
            f"{result.get('message') or 'no reason given'}",
            highlight=False,
        )
        err_console.print("check the key/base URL: [value]xorcise config show[/value]")
    if not result.get("ok"):
        raise typer.Exit(1)


@config_app.command("show")
def show(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw config view as JSON (for scripting)."
    ),
) -> None:
    """Show the current evaluation settings: judge + terrain models, catalog, network, budget."""
    view: dict[str, Any] = RestClient().get("/config")
    # `is True` (not a bare truth test) so a direct, non-CLI call — where the typer default is a
    # truthy OptionInfo — still renders the human view.
    if as_json is True:
        emit_json(view)
        return
    _render_config(view)


@config_app.command("set-model")
def set_model(
    key: str = typer.Option(
        None,
        "--key",
        help="Judge API key — bring your own model (stored locally in ~/.xorcise/.env).",
    ),
    base_url: str = typer.Option(None, "--base-url", help="OpenAI-compatible base URL."),
    name: str = typer.Option(None, "--name", help="Model name, e.g. gpt-4o-mini."),
    timeout: float | None = typer.Option(
        None, "--timeout", help="Judge HTTP timeout in seconds (default 120)."
    ),
    transcript_max_tokens: int | None = typer.Option(
        None,
        "--transcript-max-tokens",
        help="OPTIONAL pre-flight cap (tokens) on the estimated judge prompt. Default 0 = off: the "
        "judge attempts the call and relies on the model's own context limit (a failed judge is "
        "re-gradeable). Set a positive value only to pre-reject oversized evidence on a "
        "small-context model.",
    ),
    span_max_tokens: int | None = typer.Option(
        None,
        "--span-max-tokens",
        help="Per-span body cap (tokens) on the distilled transcript (default 2000). A few giant "
        "tool-output spans dominate long transcripts; capping each to a head+tail window keeps "
        "every span but collapses the tail. 0 disables the cap.",
    ),
    tokenizer: str | None = typer.Option(
        None,
        "--tokenizer",
        help="tiktoken encoding used to count the transcript budget (e.g. o200k_base, "
        "cl100k_base). Tokenizers are not interchangeable — set it to match your judge.",
    ),
) -> None:
    """Set the judge model — bring your own model (BYOM). \
Only the fields you pass change; --key '' clears it.

    Applies immediately on the running instance (no restart); \
verify it answers with: xorcise config test."""
    if all(v is None for v in (key, base_url, name, timeout, transcript_max_tokens, tokenizer)):
        fail(
            "nothing to set — pass at least one of --name / --base-url / --key / "
            "--timeout / --transcript-max-tokens / --tokenizer",
            example="xorcise config set-model --name gpt-4o-mini --key sk-…",
            code=2,
        )
    view = RestClient().put(
        "/config/model",
        {
            "key": key,
            "base_url": base_url,
            "model_name": name,
            "timeout_seconds": timeout,
            "transcript_max_tokens": transcript_max_tokens,
            "span_max_tokens": span_max_tokens,
            "tokenizer": tokenizer,
        },
    )
    j = view["judge"]
    state = "configured" if j["configured"] else "not configured"
    console.print(
        f"judge model {state} — model={j.get('model_name') or '—'}, key={j.get('key_hint') or '—'}"
    )


@config_app.command("test")
def test_model(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw test result as JSON (for scripting)."
    ),
) -> None:
    """Live-test the saved judge model by actually calling it (the Settings 'Test' button).

    `config show` only reports whether a key is PRESENT; this proves the key works. \
Exits non-zero when the model is unreachable or unconfigured, so it can gate a script.
    """
    result: dict[str, Any] = RestClient().post(
        "/config/model/test", json={}, timeout=_LIVE_TEST_TIMEOUT_SECONDS
    )
    _report_test(result, "judge model", as_json=as_json)


@config_app.command("set-terrain-model")
def set_terrain_model(
    key: str = typer.Option(None, "--key", help="Terrain-model API key ('' clears the override)."),
    base_url: str = typer.Option(None, "--base-url", help="OpenAI-compatible base URL."),
    name: str = typer.Option(None, "--name", help="Model name, e.g. gpt-4o-mini."),
    transcript_max_tokens: int | None = typer.Option(
        None,
        "--transcript-max-tokens",
        help="Max tokens of the transcript fed to the terrain attribution model (default 256000).",
    ),
) -> None:
    """Set the terrain-attribution model override (falls back to the judge model).

    Only the fields you pass change; passing an empty string ('') clears that field \
so it falls back to the judge model. Applies immediately (no restart), like `set-model`.
    """
    if all(v is None for v in (key, base_url, name, transcript_max_tokens)):
        fail(
            "nothing to set — pass at least one of --name / --base-url / --key / "
            "--transcript-max-tokens",
            example="xorcise config set-terrain-model --name gpt-4o-mini",
            code=2,
        )
    view: dict[str, Any] = RestClient().put(
        "/config/terrain-model",
        {
            "key": key,
            "base_url": base_url,
            "model_name": name,
            "transcript_max_tokens": transcript_max_tokens,
        },
    )
    t = view.get("terrain") or {}
    origin = "judge default" if t.get("uses_judge_default", True) else "custom override"
    console.print(f"terrain model: {t.get('model_name') or '—'} ({origin})")


@config_app.command("test-terrain")
def test_terrain_model(
    as_json: bool = typer.Option(
        False, "--json", help="Emit the raw test result as JSON (for scripting)."
    ),
) -> None:
    """Live-test the EFFECTIVE terrain-attribution model (the override if set, else the judge)."""
    result: dict[str, Any] = RestClient().post(
        "/config/terrain-model/test", json={}, timeout=_LIVE_TEST_TIMEOUT_SECONDS
    )
    _report_test(result, "terrain model", as_json=as_json)


@config_app.command("set-network")
def set_network(
    headscale_url: str = typer.Option(
        None, "--headscale-url", help="Remote Headscale control-plane URL ('' reverts to local)."
    ),
    advertise_host: str = typer.Option(
        None,
        "--advertise-host",
        help="Address other XORCISE machines use to reach this one ('' reverts).",
    ),
) -> None:
    """Set the multi-machine network addresses (EXPERIMENTAL — no GUI equivalent).

    EXPERIMENTAL: multi-machine deployment is unfinished. Pointing a local-only server at a
    remote control plane misconfigures it — leave both unset unless you are testing that.

    This is the ONLY place these are set: the Settings page shows them read-only (under
    Environment, once set) and offers no editor, deliberately. Two addresses do not make a
    distributed deployment, so a GUI form for them promised a finished feature and mostly
    produced an unbootable server.

    Only the fields you pass change; '' unsets one. These are read at boot — \
restart XORCISE (`xorcise down && xorcise up`) for them to take effect.
    """
    if all(v is None for v in (headscale_url, advertise_host)):
        fail(
            "nothing to set — pass --headscale-url and/or --advertise-host",
            example="xorcise config set-network --headscale-url https://hs.example.com",
            code=2,
        )
    view: dict[str, Any] = RestClient().put(
        "/config/network",
        {"headscale_url": headscale_url, "advertise_host": advertise_host},
    )
    n = view.get("network") or {}
    console.print(
        f"network: headscale_url={n.get('headscale_url') or '— (local)'}, "
        f"advertise_host={n.get('advertise_host') or '— (local)'}"
    )
    console.print("[yellow]applies on the next `xorcise up`[/yellow]")
