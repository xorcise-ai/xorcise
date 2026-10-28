"""HttpCatalogSource.announcements() against GET /v1/announcements/active.

The live contract is mocked with httpx.MockTransport (no network in CI):
  GET /v1/announcements/active → {"announcements": [{id, revision, placement, tone,
                                                     body_md, dismissible}, ...]}

Every failure in this file — transport, status, JSON, shape, item — must produce `()`.
Announcements are decoration; a remote that is down, old or wrong must cost the operator
a banner and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest

from xorcise.core.catalog.http import HttpCatalogSource, _announcement_from_remote

pytestmark = pytest.mark.unit

_APP = {
    "id": "app-1",
    "revision": 2,
    "placement": "application",
    "tone": "maintenance",
    "body_md": "Maintenance window on **Sunday**.",
    "dismissible": True,
}
_CAT = {
    "id": "cat-1",
    "revision": 1,
    "placement": "catalog",
    "tone": "information",
    "body_md": "New missions landed.",
    "dismissible": True,
}


def _source(handler: Callable[[httpx.Request], httpx.Response]) -> HttpCatalogSource:
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://cat.example")
    return HttpCatalogSource("https://cat.example", client=client)


def _ok(body: object) -> Callable[[httpx.Request], httpx.Response]:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/announcements/active"
        return httpx.Response(200, json=body)

    return h


def test_happy_path_returns_both_placements() -> None:
    anns = _source(_ok({"announcements": [_APP, _CAT]})).announcements()
    assert [a.placement for a in anns] == ["application", "catalog"]
    assert anns[0].id == "app-1" and anns[0].revision == 2
    assert anns[1].body_md == "New missions landed."


def test_it_asks_the_contracted_path() -> None:
    seen: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(200, json={"announcements": []})

    assert _source(h).announcements() == ()
    assert seen == ["/v1/announcements/active"]


def test_404_is_a_deployment_that_predates_the_feature_not_an_error() -> None:
    # Every currently-deployed remote answers 404 here. That is the NORMAL state, so it
    # degrades silently to empty — a warning on every page load would train operators to
    # ignore the log.
    assert _source(lambda req: httpx.Response(404)).announcements() == ()


def test_server_error_degrades_to_empty() -> None:
    assert _source(lambda req: httpx.Response(500, json={"error": "boom"})).announcements() == ()


def test_a_timeout_degrades_to_empty() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=req)

    assert _source(h).announcements() == ()


def test_a_connect_failure_degrades_to_empty() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    assert _source(h).announcements() == ()


def test_a_non_json_body_degrades_to_empty() -> None:
    # e.g. a captive portal or a proxy serving HTML — json() raises ValueError.
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    assert _source(h).announcements() == ()


def test_a_non_mapping_body_degrades_to_empty() -> None:
    assert _source(_ok([_APP])).announcements() == ()


def test_announcements_not_a_list_degrades_to_empty() -> None:
    assert _source(_ok({"announcements": {"id": "app-1"}})).announcements() == ()


def test_a_missing_announcements_key_degrades_to_empty() -> None:
    assert _source(_ok({})).announcements() == ()


def test_a_malformed_item_is_dropped_while_a_good_sibling_survives() -> None:
    # One bad row must not blank the other placement's banner.
    bad = {**_CAT, "tone": "urgent"}
    anns = _source(_ok({"announcements": [bad, _APP]})).announcements()
    assert [a.id for a in anns] == ["app-1"]


def test_only_the_first_item_per_placement_is_kept() -> None:
    # The contract promises at most one per placement; if the server breaks that promise the
    # client picks deterministically rather than rendering a stack of banners.
    second = {**_APP, "id": "app-2"}
    anns = _source(_ok({"announcements": [_APP, second, _CAT]})).announcements()
    assert [a.id for a in anns] == ["app-1", "cat-1"]


def test_an_absurdly_long_list_is_truncated_rather_than_fully_processed() -> None:
    # The contract promises at most one per placement, so a list of any real length is ALREADY
    # a server defect. Bound the work instead of parsing whatever arrives — this path exists to
    # stop the remote hurting the local app, and an unbounded loop is the remote setting the
    # local cost. The good row sits past the bound, so a truncation that did not happen would
    # show up as a banner here.
    from xorcise.core.catalog.http import _MAX_ANNOUNCEMENT_ROWS

    junk = [{"id": f"junk-{i}"} for i in range(500)]
    anns = _source(_ok({"announcements": [*junk, _APP]})).announcements()
    assert anns == ()
    assert len(junk) > _MAX_ANNOUNCEMENT_ROWS


def test_the_bound_never_truncates_a_well_formed_response() -> None:
    # A well-formed response is at most two rows, so first-wins-per-placement is untouched by
    # the bound — this pins that the two rules do not interact.
    anns = _source(_ok({"announcements": [_APP, _CAT]})).announcements()
    assert [a.id for a in anns] == ["app-1", "cat-1"]


def test_truncation_warns_once() -> None:
    import logging

    from xorcise.core.catalog.http import _MAX_ANNOUNCEMENT_ROWS

    rows = [_APP] + [_CAT] * (_MAX_ANNOUNCEMENT_ROWS + 5)
    caplog_rows = []
    logger = logging.getLogger("xorcise.core.catalog.http")

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            caplog_rows.append(record)

    handler = _Collect(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        anns = _source(_ok({"announcements": rows})).announcements()
    finally:
        logger.removeHandler(handler)

    # Still one banner per placement, and exactly one line telling an operator the remote
    # served a list it had no business serving.
    assert [a.id for a in anns] == ["app-1", "cat-1"]
    assert len([r for r in caplog_rows if r.levelno >= logging.WARNING]) == 1


def test_an_incident_is_undismissible_even_if_the_server_says_otherwise() -> None:
    incident = {**_APP, "tone": "incident", "dismissible": True}
    anns = _source(_ok({"announcements": [incident]})).announcements()
    assert anns[0].tone == "incident" and anns[0].dismissible is False


def test_transport_failures_stay_quiet_but_a_bad_shape_warns(caplog) -> None:
    # An offline laptop must not WARN on every page load — that is the operator's normal
    # state, not a defect. A response that parsed as JSON and was still the wrong shape IS a
    # server-side defect, and an operator should see exactly one line about it.
    import logging

    def offline(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    with caplog.at_level(logging.DEBUG, logger="xorcise.core.catalog.http"):
        _source(offline).announcements()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="xorcise.core.catalog.http"):
        _source(_ok({"announcements": "nope"})).announcements()
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="xorcise.core.catalog.http"):
        _source(_ok({"announcements": [{**_CAT, "tone": "urgent"}]})).announcements()
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


def test_the_announcements_timeout_is_shorter_than_the_general_one() -> None:
    # This call sits on the page-load path, so a slow remote must not delay the app for the
    # full catalog timeout.
    from xorcise.core.catalog.http import _ANNOUNCEMENTS_TIMEOUT, _TIMEOUT

    assert _ANNOUNCEMENTS_TIMEOUT < _TIMEOUT


def test_the_overall_budget_is_what_bounds_the_page_load_not_the_per_read_timeout() -> None:
    # `timeout=` is HTTPX's PER-OPERATION (inactivity) timeout, so a server that trickles a
    # byte just inside it holds the request open for as long as it likes. The deadline is the
    # bound that actually survives that, so it has to exist and be the larger of the two.
    from xorcise.core.catalog.http import (
        _ANNOUNCEMENTS_DEADLINE,
        _ANNOUNCEMENTS_TIMEOUT,
        _TIMEOUT,
    )

    # At least one read's worth of patience, and still shorter than the general catalog
    # timeout — a page-load call may not cost more than an explicit browse does.
    assert _ANNOUNCEMENTS_TIMEOUT <= _ANNOUNCEMENTS_DEADLINE < _TIMEOUT


def _streaming(chunks: Iterator[bytes]) -> Callable[[httpx.Request], httpx.Response]:
    """A 200 whose body is produced lazily, so a test can count what was actually pulled."""

    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunks, headers={"content-type": "application/json"})

    return h


def test_an_oversized_body_is_abandoned_mid_stream_not_buffered_then_rejected() -> None:
    # The row and string limits run AFTER the body is in memory, so on their own they bound
    # nothing the remote actually costs us: a valid announcement beside a 50 MB field was
    # parsed in full. The cap has to be applied to the BYTES, while they stream.
    pulled = 0

    def chunks() -> Iterator[bytes]:
        nonlocal pulled
        for _ in range(4096):  # 32 MB if anything reads it all
            pulled += 1
            yield b"x" * 8192

    assert _source(_streaming(chunks())).announcements() == ()
    # 8 KiB a chunk, so crossing a cap in the tens of KiB is a handful of chunks. The number
    # is loose on purpose — what it pins is "a bounded prefix", not the exact cap.
    assert pulled < 64, f"read {pulled} chunks ({pulled * 8} KiB) before giving up"


def test_a_well_formed_response_is_nowhere_near_the_byte_cap() -> None:
    # The non-vacuity half of the test above: the cap must never truncate a real response, so
    # pin that a maximal legitimate one (two placements, both bodies at the published limit)
    # is still served in full.
    from xorcise.core.contracts.announcements import MAX_BODY_CHARS

    big = {**_APP, "body_md": "x" * MAX_BODY_CHARS}
    other = {**_CAT, "body_md": "y" * MAX_BODY_CHARS}
    anns = _source(_ok({"announcements": [big, other]})).announcements()
    assert [a.id for a in anns] == ["app-1", "cat-1"]
    assert len(anns[0].body_md) == MAX_BODY_CHARS


def test_a_trickling_response_is_abandoned_at_the_overall_deadline(monkeypatch) -> None:
    # The reproduction from review, scaled down: every individual read lands well inside the
    # per-operation timeout, so only a wall-clock budget can end this. Without one the call
    # runs for as long as the remote cares to keep typing.
    import time

    monkeypatch.setattr("xorcise.core.catalog.http._ANNOUNCEMENTS_DEADLINE", 0.3)
    pulled = 0

    def chunks() -> Iterator[bytes]:
        nonlocal pulled
        for _ in range(200):  # 10 s at this rate
            pulled += 1
            time.sleep(0.05)
            yield b" "

    started = time.monotonic()
    assert _source(_streaming(chunks())).announcements() == ()
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"ran for {elapsed:.2f}s against a 0.3s budget"
    assert pulled < 200


def test_an_abandoned_fetch_warns_once_so_an_operator_can_see_it() -> None:
    # A remote that overruns a bound is a SERVER-side defect, which is the same split the
    # shape/item warnings already use: quiet for the failures a laptop causes, one line for
    # the ones the remote causes.
    import logging

    def chunks() -> Iterator[bytes]:
        for _ in range(4096):
            yield b"x" * 8192

    records: list[logging.LogRecord] = []
    logger = logging.getLogger("xorcise.core.catalog.http")

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        assert _source(_streaming(chunks())).announcements() == ()
    finally:
        logger.removeHandler(handler)
    assert len([r for r in records if r.levelno >= logging.WARNING]) == 1


# --- the inbound row parser ---------------------------------------------------------------
#
# `_announcement_from_remote` reads ONE untrusted row. It moved here from `contracts` with the
# code: it is a wire parser like `_to_item`, and the frozen model it builds is pinned
# separately in `test_announcements_contract.py`.

_GOOD: dict[str, object] = {
    "id": "a1",
    "revision": 3,
    "placement": "application",
    "tone": "information",
    "body_md": "Scheduled maintenance on **Sunday**.",
    "dismissible": True,
}


def test_the_parser_reads_a_well_formed_row() -> None:
    ann = _announcement_from_remote(_GOOD)
    assert ann is not None
    assert ann.id == "a1"
    assert ann.revision == 3
    assert ann.placement == "application"
    assert ann.tone == "information"
    assert ann.dismissible is True


def test_the_parser_ignores_a_key_the_server_added_later() -> None:
    # The whole point of the lenient path: a server that grows a field must not blank the
    # banner on every client that predates it.
    ann = _announcement_from_remote({**_GOOD, "starts_at": "2026-09-01T00:00:00Z", "colour": "red"})
    assert ann is not None and ann.id == "a1"


def test_the_parser_rejects_a_non_mapping() -> None:
    assert _announcement_from_remote(["not", "a", "mapping"]) is None
    assert _announcement_from_remote(None) is None
    assert _announcement_from_remote("a1") is None


def test_the_parser_rejects_a_missing_key() -> None:
    for key in _GOOD:
        payload = {k: v for k, v in _GOOD.items() if k != key}
        assert _announcement_from_remote(payload) is None, key


def test_the_parser_rejects_an_empty_or_non_string_id() -> None:
    assert _announcement_from_remote({**_GOOD, "id": ""}) is None
    assert _announcement_from_remote({**_GOOD, "id": 7}) is None


def test_the_parser_rejects_an_oversized_id() -> None:
    # The browser uses `id` as a localStorage dismissal key, so an unbounded identifier from a
    # broken or hostile remote would be written verbatim into the user's browser storage.
    from xorcise.core.contracts.announcements import MAX_ID_CHARS

    assert _announcement_from_remote({**_GOOD, "id": "a" * MAX_ID_CHARS}) is not None
    assert _announcement_from_remote({**_GOOD, "id": "a" * (MAX_ID_CHARS + 1)}) is None


def test_the_parser_rejects_a_bad_placement() -> None:
    assert _announcement_from_remote({**_GOOD, "placement": "sidebar"}) is None


def test_the_parser_rejects_a_bad_tone() -> None:
    assert _announcement_from_remote({**_GOOD, "tone": "urgent"}) is None


def test_the_parser_rejects_a_body_over_the_cap() -> None:
    from xorcise.core.contracts.announcements import MAX_BODY_CHARS

    assert _announcement_from_remote({**_GOOD, "body_md": "x" * MAX_BODY_CHARS}) is not None
    assert _announcement_from_remote({**_GOOD, "body_md": "x" * (MAX_BODY_CHARS + 1)}) is None


def test_the_parser_rejects_a_blank_body() -> None:
    # A banner with no words in it is not a quieter banner: it draws the tone word and a close
    # button and tells the reader there is news without saying what it is. The length bound
    # only ever looked at the top end, so "" and a line of spaces were both served end to end.
    assert _announcement_from_remote({**_GOOD, "body_md": ""}) is None
    assert _announcement_from_remote({**_GOOD, "body_md": "   "}) is None
    assert _announcement_from_remote({**_GOOD, "body_md": " \n\t "}) is None
    # …and a body whose only content is padded still renders, because it has content.
    ann = _announcement_from_remote({**_GOOD, "body_md": "  Back up.  "})
    assert ann is not None and ann.body_md == "  Back up.  "


def test_a_blank_body_never_reaches_the_browser() -> None:
    # The end-to-end half: the row is dropped by the source, not merely by the parser.
    assert _source(_ok({"announcements": [{**_APP, "body_md": "  "}]})).announcements() == ()


def test_the_parser_rejects_a_non_string_body() -> None:
    assert _announcement_from_remote({**_GOOD, "body_md": 42}) is None


def test_the_parser_rejects_a_bool_revision() -> None:
    # isinstance(True, int) is True in Python, so a naive int check would let a bool
    # through and serve `revision: true` to the browser. A bool here is a server bug.
    assert _announcement_from_remote({**_GOOD, "revision": True}) is None
    assert _announcement_from_remote({**_GOOD, "revision": "3"}) is None


def test_the_parser_rejects_a_non_bool_dismissible() -> None:
    assert _announcement_from_remote({**_GOOD, "dismissible": "yes"}) is None
    assert _announcement_from_remote({**_GOOD, "dismissible": 1}) is None


def test_the_parser_forces_an_incident_to_be_undismissible() -> None:
    # An incident banner is never dismissible. Enforcing it locally means a server bug
    # cannot hide an active incident from an operator with one click.
    ann = _announcement_from_remote({**_GOOD, "tone": "incident", "dismissible": True})
    assert ann is not None and ann.dismissible is False


def test_the_parser_leaves_other_tones_dismissible_as_published() -> None:
    for tone in ("information", "maintenance", "resolved"):
        ann = _announcement_from_remote({**_GOOD, "tone": tone, "dismissible": True})
        assert ann is not None and ann.dismissible is True, tone


# ── the byte cap must bound the WIRE, not what the remote expands to (#137 review) ────────────
#
# The cap counted bytes handed back by `iter_bytes`, which runs the content decoder first — so a
# compressed body let the REMOTE choose how far past the cap one chunk went. Measured against a
# gzipped pad: 407,698 bytes on the wire produced a 33,578,960-byte first chunk, already
# allocated before any limit could look at it, and an 81 MB peak for a 204 KB response.


def test_a_compressed_body_is_refused_rather_than_decoded(caplog) -> None:
    """The fetch asks for `identity`; a body that comes back encoded anyway is the remote
    choosing our allocation, so it is refused before a decoder ever sees it."""
    import gzip
    import json as _json

    bomb = gzip.compress(_json.dumps({"announcements": [], "pad": "A" * 20_000_000}).encode())
    assert len(bomb) < 64 * 1024, "the point is that it is SMALL on the wire"

    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=bomb,
            headers={"content-encoding": "gzip", "content-type": "application/json"},
        )

    with caplog.at_level("WARNING"):
        assert _source(h).announcements() == ()
    assert any("despite a request for identity" in r.message for r in caplog.records)


def test_the_fetch_asks_for_an_unencoded_body() -> None:
    """Without this header httpx advertises gzip, and the cap silently stops being a wire cap."""
    seen: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("accept-encoding", ""))
        return httpx.Response(200, json={"announcements": []})

    _source(h).announcements()
    assert seen == ["identity"]


def test_headers_that_outlast_the_budget_stop_the_fetch_before_the_body(monkeypatch) -> None:
    """The deadline used to be tested only inside the body loop, which does not start until the
    headers are complete — so a slow header phase was never looked at, and the body then began
    afresh on an already-spent budget.

    Asserts the body was never READ, not merely that the call returned (): the body loop carries
    its own deadline check, so a test that only asserts the empty result passes with this guard
    removed and proves nothing.
    """
    import time

    import xorcise.core.catalog.http as http_mod

    clock = iter([0.0] + [99.0] * 8)
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    entered = False
    real = http_mod._read_bounded

    def spy(resp: httpx.Response, deadline: float) -> bytes | None:
        nonlocal entered
        entered = True
        return real(resp, deadline)

    monkeypatch.setattr(http_mod, "_read_bounded", spy)

    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"announcements": []})

    assert _source(h).announcements() == ()
    assert not entered, "the body phase must not start on an already-spent deadline"
