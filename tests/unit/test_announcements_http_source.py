"""HttpCatalogSource.announcements() against GET /v1/announcements/active.

The live contract is mocked with httpx.MockTransport (no network in CI):
  GET /v1/announcements/active → {"announcements": [{id, revision, placement, tone,
                                                     body_md, dismissible}, ...]}

Every failure in this file — transport, status, JSON, shape, item — must produce `()`.
Announcements are decoration; a remote that is down, old or wrong must cost the operator
a banner and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from xorcise.core.catalog.http import HttpCatalogSource

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
