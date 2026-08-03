"""tiktoken-backed judge token counter (orchestration): the ONLY judge code that imports tiktoken.

Tokenizers are NOT interchangeable — the same text costs a different number of tokens under a
different BPE — so the encoding is chosen explicitly (settings.judge_tokenizer) and recorded, never
guessed. An unknown encoding name, or a missing local tiktoken vocab when the server is offline,
degrades to the coarse chars/4 heuristic (eval.judge.estimate_tokens_heuristic) rather than failing
grading. It satisfies eval.judge.TokenCounter structurally; the eval part-island never imports it.
"""

from __future__ import annotations

import logging

import tiktoken

from xorcise.core.eval.judge import TokenCounter, estimate_tokens_heuristic

log = logging.getLogger(__name__)

_DEFAULT_ENCODING = "o200k_base"


def build_token_counter(tokenizer: str) -> TokenCounter:
    """Return a token counter for the named tiktoken encoding, or the heuristic if unavailable."""
    name = tokenizer or _DEFAULT_ENCODING
    try:
        enc = tiktoken.get_encoding(name)
    except Exception as exc:  # noqa: BLE001 — unknown name OR offline download failure: degrade
        log.warning("judge tokenizer %r unavailable (%s); using chars/4 heuristic", name, exc)
        return estimate_tokens_heuristic

    def count(text: str) -> int:
        # disallowed_special=() => treat control strings (<|endoftext|>, ...) as ordinary text.
        return len(enc.encode(text, disallowed_special=()))

    return count
