import pytest

from xorcise.core.eval.judge import estimate_tokens_heuristic
from xorcise.core.orchestration.clients.token_counter import build_token_counter

pytestmark = pytest.mark.unit


def test_builds_a_counter_returning_positive_deterministic_counts():
    count = build_token_counter("o200k_base")
    n = count("the agent exploited an IDOR to read another user's record")
    assert isinstance(n, int) and n > 0
    assert count("same text") == count("same text")  # deterministic
    # monotone under any real tokenizer OR the heuristic fallback (network-robust assertion)
    assert count("a b c d e f g h i j") >= count("a b")


def test_counter_does_not_raise_on_embedded_model_control_strings():
    # A transcript can contain control strings like <|endoftext|>; counting must treat them as
    # ordinary text (disallowed_special=()) rather than raising.
    count = build_token_counter("o200k_base")
    assert count("before <|endoftext|> after") > 0


def test_unknown_tokenizer_degrades_to_the_heuristic():
    count = build_token_counter("definitely-not-a-real-encoding")
    text = "abcd" * 25  # 100 chars
    assert count(text) == estimate_tokens_heuristic(text)


def test_empty_tokenizer_name_falls_back_to_the_default_encoding():
    # An unset/empty config value must still yield a working counter, not crash.
    count = build_token_counter("")
    assert count("hello world") > 0


def test_heuristic_is_ceil_chars_over_four():
    assert estimate_tokens_heuristic("") == 0
    assert estimate_tokens_heuristic("abcd") == 1
    assert estimate_tokens_heuristic("abcde") == 2  # ceil(5/4)
