"""BYOM judge model adapter: an OpenAI-compatible chat-completions client.

This is the ONLY judge code that touches httpx; it satisfies eval.judge.JudgeModel structurally
(it is NOT imported by the eval part-island — the server injects it). build_judge_model returns
None when no BYOM key is configured, so grade_judge takes the model-not-configured degrade path.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import httpx

from xorcise.core.config import Settings
from xorcise.core.eval.judge import JudgeError, JudgeModel, Message

# Secret-shaped runs to strip from anything a provider says back to us. Deliberately narrow so
# the useful prose survives: "…limit of 272000 tokens" must not be mangled by a pattern broad
# enough to treat any long word as a credential.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bsk-[A-Za-z0-9*_.\-]{4,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9*_.\-]{8,}"),
)


class OpenAiCompatibleJudgeModel:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        # Kept so a provider that quotes the rejected key back at us can be redacted against
        # the literal value — provider-agnostic, unlike prefix patterns.
        self._api_key = api_key
        self._http = httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)

    def _redact(self, text: str) -> str:
        """Strip credentials from provider prose before it is stored or displayed.

        Reading the response body is what makes a judge failure diagnosable, but it also makes
        it a credential sink: OpenAI answers a bad key with "Incorrect API key provided:
        sk-…", and this reason is persisted as GradeResult.judge_detail and rendered on the
        results page. Redact the configured key VERBATIM first (works for any provider, and
        catches a full echo), then anything secret-shaped."""
        out = text
        key = self._api_key
        if key:
            out = out.replace(key, "[redacted]")
            # A provider may echo only a leading slice of the key; 16+ chars makes a
            # coincidental match implausible.
            if len(key) >= 16:
                out = out.replace(key[:16], "[redacted]")
        for pattern in _SECRET_PATTERNS:
            out = pattern.sub("[redacted]", out)
        return out

    def _reason(self, resp: httpx.Response) -> str:
        """The PROVIDER's explanation of a rejection, not just its status line.

        `raise_for_status()` stringifies to "Client error '400 Bad Request' for url ..."
        and never reads the body, so a real grading outage degraded the run with a
        message that named no cause: the provider had said "Input tokens exceed the
        configured limit of 272000 tokens", which identifies both the problem and the
        setting to change (`--transcript-max-tokens`). Losing that sentence turns a
        five-second fix into an investigation.

        Every return goes through :meth:`_redact` — this string is persisted and displayed."""
        return self._redact(self._raw_reason(resp))

    @staticmethod
    def _raw_reason(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return resp.text.strip()[:500] or "<empty response body>"
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            parts = [str(err.get("message") or "").strip(), str(err.get("code") or "").strip()]
            joined = " ".join(p for p in parts if p)
            if joined:
                return joined
        return str(body)[:500]

    def score(self, messages: Sequence[Message]) -> str:
        # Message ORDER is chosen for prompt caching (rec 4): the stable [instructions, evidence]
        # prefix is byte-identical across a run's per-criterion calls, so an OpenAI-compatible
        # provider reuses the cached prefix and only the tiny trailing criterion message varies.
        # Roles preserve the injection hierarchy: trusted instructions/criterion ride the system
        # role, untrusted agent evidence rides the user role.
        try:
            resp = self._http.post(
                f"{self._base_url}/chat/completions",
                json={
                    "model": self._model,
                    "messages": [{"role": role, "content": content} for role, content in messages],
                },
            )
        except httpx.HTTPError as exc:  # transport failure — there is no response to read
            raise JudgeError(str(exc)) from exc
        if resp.is_error:
            raise JudgeError(f"{resp.status_code} from model {self._model!r}: {self._reason(resp)}")
        try:
            data = resp.json()
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as exc:
            raise JudgeError(f"model {self._model!r} returned an unusable response: {exc}") from exc


def build_judge_model(settings: Settings) -> JudgeModel | None:
    if not settings.model_configured():
        return None
    return OpenAiCompatibleJudgeModel(
        base_url=settings.model_base_url or "https://api.openai.com/v1",
        api_key=settings.model_key or "",
        model=settings.model_name or "gpt-4o-mini",
        timeout=settings.model_timeout_seconds,
    )


def build_terrain_model(settings: Settings) -> JudgeModel | None:
    """The terrain-attribution model — defaults to the judge config, with optional per-field
    override. None when neither a terrain nor a judge key is set."""
    if not settings.terrain_model_configured():
        return None
    key, base_url, name, timeout = settings.terrain_model_effective()
    return OpenAiCompatibleJudgeModel(
        base_url=base_url or "https://api.openai.com/v1",
        api_key=key or "",
        model=name or "gpt-4o-mini",
        timeout=timeout,
    )
