"""`xorcise config` group — routes through the server.

set-model PUTs /config/model and show GETs /config, so the running server applies the change
(and clears its own settings cache) instead of the CLI poking ~/.xorcise/.env in its own process.
The .env persistence + key masking are the server's job now (the config_view unit tests +
tests/integration/test_config_live_update.py cover that)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import xorcise.core.cli.app  # noqa: F401  -- registers the command groups
from tests._helpers import invoke_cli_with_stdin
from xorcise.core.cli._shared import app

pytestmark = pytest.mark.unit


def test_show_reads_from_server(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    got = []

    class _C:
        def get(self, path):
            got.append(path)
            return {
                "judge": {
                    "configured": True,
                    "base_url": "http://xorcise-cluster01.local:8000/v1",
                    "model_name": "Qwen3.6-27B-FP8",
                    "key_hint": "…cdef",
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.show()
    out = capsys.readouterr().out
    assert got == ["/config"]  # hit the service, not the local file
    assert "Configured" in out
    assert "Qwen3.6-27B-FP8" in out


def test_set_model_puts_to_server(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    calls = {}

    class _C:
        def put(self, path, json):
            calls["path"] = path
            calls["json"] = json
            return {
                "judge": {
                    "configured": True,
                    "base_url": json.get("base_url"),
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.set_model(
        key="sk-x",
        base_url="http://h:8000/v1",
        name="Qwen3.6-27B-FP8",
        timeout=None,
        transcript_max_tokens=None,
        span_max_tokens=None,
        tokenizer=None,
    )
    assert calls["path"] == "/config/model"
    assert calls["json"] == {
        "key": "sk-x",
        "base_url": "http://h:8000/v1",
        "model_name": "Qwen3.6-27B-FP8",
        "timeout_seconds": None,
        "transcript_max_tokens": None,
        "span_max_tokens": None,
        "tokenizer": None,
    }
    assert "configured" in capsys.readouterr().out


def test_set_model_sends_transcript_max_tokens_and_tokenizer(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    calls = {}

    class _C:
        def put(self, path, json):
            calls["json"] = json
            return {
                "judge": {
                    "configured": True,
                    "base_url": json.get("base_url"),
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                    "transcript_max_tokens": json.get("transcript_max_tokens"),
                    "tokenizer": json.get("tokenizer"),
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.set_model(
        key="k",
        base_url="http://h/v1",
        name="m",
        transcript_max_tokens=8000,
        tokenizer="cl100k_base",
    )
    assert calls["json"]["transcript_max_tokens"] == 8000
    assert calls["json"]["tokenizer"] == "cl100k_base"


def test_show_renders_transcript_max_tokens_and_tokenizer(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    class _C:
        def get(self, path):
            return {
                "judge": {
                    "configured": True,
                    "base_url": "http://h/v1",
                    "model_name": "m",
                    "key_hint": "…cdef",
                    "timeout_seconds": 120.0,
                    "transcript_max_tokens": 8000,
                    "tokenizer": "cl100k_base",
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.show()
    out = capsys.readouterr().out
    assert "8000" in out and "cl100k_base" in out


def test_set_model_sends_span_max_tokens(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    calls = {}

    class _C:
        def put(self, path, json):
            calls["json"] = json
            return {
                "judge": {
                    "configured": True,
                    "base_url": json.get("base_url"),
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                    "span_max_tokens": json.get("span_max_tokens"),
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.set_model(key="k", base_url="http://h/v1", name="m", span_max_tokens=500)
    assert calls["json"]["span_max_tokens"] == 500


def test_show_renders_span_cap_and_disabled_state(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    class _C:
        def __init__(self, span: int) -> None:
            self._span = span

        def get(self, path):
            return {
                "judge": {
                    "configured": True,
                    "base_url": "http://h/v1",
                    "model_name": "m",
                    "key_hint": "…cdef",
                    "span_max_tokens": self._span,
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C(500))
    cfg_cmd.show()
    assert "500" in capsys.readouterr().out
    # 0 reads as an explicit "disabled", not "0 tokens"
    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C(0))
    cfg_cmd.show()
    assert "disabled" in capsys.readouterr().out.lower()


def test_set_model_sends_timeout(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    calls = {}

    class _C:
        def put(self, path, json):
            calls["json"] = json
            return {
                "judge": {
                    "configured": True,
                    "base_url": json.get("base_url"),
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                    "timeout_seconds": json.get("timeout_seconds"),
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.set_model(key="k", base_url="http://h/v1", name="m", timeout=180.0)
    assert calls["json"]["timeout_seconds"] == 180.0


def test_show_renders_timeout(monkeypatch, capsys):
    from xorcise.core.cli.commands import config as cfg_cmd

    class _C:
        def get(self, path):
            return {
                "judge": {
                    "configured": True,
                    "base_url": "http://h/v1",
                    "model_name": "m",
                    "key_hint": "…cdef",
                    "timeout_seconds": 180.0,
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())
    cfg_cmd.show()
    assert "180" in capsys.readouterr().out


# ── the key must not have to travel on argv (#115) ───────────────────────────────────────────
#
# `--key <secret>` is the ONLY input path today, and argv is a broadcast channel: the value lands
# in ~/.zsh_history and is readable from /proc/<pid>/cmdline by any local user for as long as the
# command runs. The stored key was already fine (0600 ~/.xorcise/.env, masked by `config show`) —
# the leak is purely on the way in.
#
# `--key-stdin` is the safe path, and one flag covers both uses: a terminal gets a no-echo prompt,
# a pipe is read straight through, so `printf %s "$KEY" | xorcise config set-model --key-stdin`
# works unchanged in CI.


class _Recorder:
    """Captures the PUT body so a test can assert on the key that reached the server."""

    def __init__(self) -> None:
        self.json: dict[str, object] = {}

    def put(self, path, json):
        self.json = json
        return {
            "judge": {"configured": True, "model_name": json.get("model_name"), "key_hint": "…ef"},
            "terrain": {"configured": True, "model_name": json.get("model_name")},
            "default_budget_seconds": 3600,
        }


def test_set_model_reads_the_key_from_a_pipe(monkeypatch, capsys):
    """`printf %s "$KEY" | … --key-stdin` — the scripted path, with nothing on argv."""
    import io

    from xorcise.core.cli.commands import config as cfg_cmd

    rec = _Recorder()
    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: rec)
    monkeypatch.setattr(cfg_cmd, "_stdin_is_interactive", lambda: False)
    # A pipe from `echo` carries the trailing newline; it is not part of the credential.
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-from-a-pipe\n"))

    cfg_cmd.set_model(key=None, name="m", key_stdin=True)

    assert rec.json["key"] == "sk-from-a-pipe"


def test_set_model_prompts_without_echo_on_a_terminal(monkeypatch, capsys):
    """An operator at a prompt must not have to type the secret where it will be recorded."""
    from xorcise.core.cli.commands import config as cfg_cmd

    rec = _Recorder()
    asked: list[str] = []
    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: rec)
    monkeypatch.setattr(cfg_cmd, "_stdin_is_interactive", lambda: True)

    # getpass, not input(): the point of the fix is that the value is never echoed.
    def _fake_getpass(prompt: str = "") -> str:
        asked.append(prompt)
        return "sk-typed"

    monkeypatch.setattr(cfg_cmd, "getpass", _fake_getpass)

    cfg_cmd.set_model(key=None, name="m", key_stdin=True)

    assert rec.json["key"] == "sk-typed"
    assert asked, "the terminal path must prompt via getpass, never echo the key"


def test_set_model_refuses_both_key_and_key_stdin(monkeypatch):
    """Two sources for one credential is a user error worth naming, not a silent precedence rule."""
    import typer

    from xorcise.core.cli.commands import config as cfg_cmd

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _Recorder())

    with pytest.raises(typer.Exit) as exc:
        cfg_cmd.set_model(key="sk-on-argv", key_stdin=True)

    assert exc.value.exit_code == 2  # usage error, per the CLI's exit contract


def test_set_model_without_key_stdin_still_leaves_the_key_alone(monkeypatch):
    """Regression guard: `--key-stdin` must not turn every other setter into a prompt.

    `set-model --name m` changes only the name — the server reads key=None as 'unchanged'. If the
    new flag defaulted to prompting, every unrelated config change would block on a secret.
    """
    from xorcise.core.cli.commands import config as cfg_cmd

    rec = _Recorder()
    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: rec)
    monkeypatch.setattr(cfg_cmd, "_stdin_is_interactive", lambda: True)

    cfg_cmd.set_model(key=None, name="m")

    assert rec.json["key"] is None


def test_set_terrain_model_reads_the_key_from_a_pipe(monkeypatch, capsys):
    """The terrain setter takes the same credential the same way — #115 names only `set-model`,
    but the identical `--key` on argv is right beside it and leaks identically."""
    import io

    from xorcise.core.cli.commands import config as cfg_cmd

    rec = _Recorder()
    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: rec)
    monkeypatch.setattr(cfg_cmd, "_stdin_is_interactive", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO("sk-terrain\n"))

    cfg_cmd.set_terrain_model(key=None, name="m", key_stdin=True)

    assert rec.json["key"] == "sk-terrain"


# ── an empty --key-stdin must not clear the key (#125 review) ────────────────────────────────
#
# `_key_from_stdin()` strips its input, and an empty result was forwarded as key="" — which the
# server reads as an explicit CLEAR. So `printf %s "$KEY" | xorcise config set-model --key-stdin`
# silently unconfigured the judge whenever $KEY was unset in CI: the command that exists to set a
# key deleted one instead, and exited 0.
#
# `--key ''` stays the way to clear deliberately. Driven through CliRunner rather than by calling
# the command function, because the parser is where the empty value actually arrives.

_RUNNER = CliRunner()


class _NoServer:
    """Any request here is a failure: the guard must trip before the network."""

    def put(self, path, json):  # pragma: no cover - reaching this IS the failure
        raise AssertionError(f"request made despite empty key: {path} {json}")


@pytest.mark.parametrize("piped", ["", "   ", "\n", "  \n  "])
def test_an_empty_key_stdin_is_refused_before_any_request(monkeypatch, piped):
    from xorcise.core.cli.commands import config as cfg_cmd

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _NoServer())
    monkeypatch.setattr(cfg_cmd, "_stdin_is_interactive", lambda: False)

    result = _RUNNER.invoke(app, ["config", "set-model", "--key-stdin", "--name", "m"], input=piped)

    # Exit 2 is the usage-error contract; _NoServer guarantees nothing was sent, so the existing
    # key is untouched. Asserted on behaviour rather than on the wording of the message.
    assert result.exit_code == 2, result.output
    assert "stdin" in result.output.lower()


def test_an_empty_key_stdin_is_refused_for_the_terrain_setter_too(monkeypatch):
    from xorcise.core.cli.commands import config as cfg_cmd

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _NoServer())
    monkeypatch.setattr(cfg_cmd, "_stdin_is_interactive", lambda: False)

    result = _RUNNER.invoke(app, ["config", "set-terrain-model", "--key-stdin"], input="")

    assert result.exit_code == 2, result.output


def test_clearing_the_key_still_works_through_the_explicit_flag(monkeypatch):
    """`--key ''` is the deliberate clear and must keep working — it is the whole reason an empty
    pipe cannot be allowed to mean the same thing."""
    from xorcise.core.cli.commands import config as cfg_cmd

    sent: dict[str, object] = {}

    class _C:
        def put(self, path, json):
            sent.update(json)
            return {
                "judge": {"configured": False, "model_name": None, "key_hint": None},
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())

    result = _RUNNER.invoke(app, ["config", "set-model", "--key", ""])

    assert result.exit_code == 0, result.output
    assert sent["key"] == ""


def test_setting_only_the_name_never_touches_the_key(monkeypatch):
    """The ordinary path: no key flag at all means key=None, i.e. 'leave it alone'."""
    from xorcise.core.cli.commands import config as cfg_cmd

    sent: dict[str, object] = {}

    class _C:
        def put(self, path, json):
            sent.update(json)
            return {
                "judge": {
                    "configured": True,
                    "model_name": json.get("model_name"),
                    "key_hint": "…cdef",
                },
                "default_budget_seconds": 3600,
            }

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _C())

    result = _RUNNER.invoke(app, ["config", "set-model", "--name", "gpt-4o-mini"])

    assert result.exit_code == 0, result.output
    assert sent["key"] is None


# ── a multi-line --key-stdin must not corrupt .env (#125 review) ──────────────────────────────
#
# `_key_from_stdin()` strips only the ENDS, so `cat keyfile | … --key-stdin` on a file that
# carries a second line sends key="line1\nline2". `set_env_vars` writes the value unquoted, the
# newline splits the XORCISE_MODEL_KEY= line in ~/.xorcise/.env, dotenv reads back only "line1"
# — and the remainder stays for ever, because the .env writer preserves every line it does not
# recognise. An API key is one line; anything else is refused at the door rather than written
# into a file that then has to be repaired by hand.


@pytest.mark.parametrize(
    "piped",
    [
        "line1\nline2\n",  # a key file with a trailing comment or a second secret
        "sk-abc\n\nsk-def\n",  # a blank line in the middle survives the end-strip
    ],
)
def test_a_multi_line_key_stdin_is_refused_before_any_request(monkeypatch, piped):
    from xorcise.core.cli.commands import config as cfg_cmd

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _NoServer())
    monkeypatch.setattr(cfg_cmd, "_stdin_is_interactive", lambda: False)

    result = _RUNNER.invoke(app, ["config", "set-model", "--key-stdin", "--name", "m"], input=piped)

    assert result.exit_code == 2, result.output
    assert "line" in result.output.lower()


def test_a_multi_line_key_stdin_is_refused_for_the_terrain_setter_too(monkeypatch):
    from xorcise.core.cli.commands import config as cfg_cmd

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _NoServer())
    monkeypatch.setattr(cfg_cmd, "_stdin_is_interactive", lambda: False)

    result = _RUNNER.invoke(app, ["config", "set-terrain-model", "--key-stdin"], input="a\nb\n")

    assert result.exit_code == 2, result.output


# ── stdin that is None or carries a bare CR (#125 review) ─────────────────────────────────────
#
# Two inputs CliRunner cannot express, because it always builds its own text stream from
# `input=` and that stream translates universal newlines: a CLOSED fd 0 (`… --key-stdin <&-`),
# where CPython leaves sys.stdin as None, and a bare CR. Driven through the real click command
# instead, so the parser still supplies the true `None`/`False` option defaults — calling the
# command function directly would hand it typer's OptionInfo objects and prove nothing.


def test_a_closed_stdin_is_no_key_rather_than_a_crash(monkeypatch, capsys):
    """`xorcise config set-model --key-stdin --name m <&-` used to exit 1 with 'unexpected
    error: NoneType object has no attribute isatty'. There is no stream, so there is no key:
    that is the same 'nothing was read' usage error an empty pipe gets."""
    from xorcise.core.cli.commands import config as cfg_cmd

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _NoServer())

    assert invoke_cli_with_stdin(["config", "set-model", "--key-stdin", "--name", "m"], None) == 2
    assert "stdin" in capsys.readouterr().err.lower()


def test_a_bare_cr_in_the_piped_key_is_refused(monkeypatch):
    """The same one-line rule, for the carriage return a CRLF key file can carry."""
    import io

    from xorcise.core.cli.commands import config as cfg_cmd

    monkeypatch.setattr(cfg_cmd, "RestClient", lambda: _NoServer())

    stdin = io.StringIO("sk-abc\rsk-def")
    assert invoke_cli_with_stdin(["config", "set-model", "--key-stdin", "--name", "m"], stdin) == 2
