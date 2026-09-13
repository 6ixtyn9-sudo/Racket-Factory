"""Tests for the shared odds-page scanner: container climb and 429 retry."""

from racketfactory.sources import _page_odds as po


def _is_match(href: str):
    return {"tournament": "t", "tour_hint": "h"} if href.startswith("/m/") else None


NESTED_SINGLE = """
<html><body><div class="card"><div class="names">
<a href="/m/aaa/">Alpha B. - Beta C.</a></div>
<div class="prices"><span>1.50</span><span>2.50</span></div></div>
</body></html>
"""

NESTED_SIBLINGS = """
<html><body><div class="card">
<div><a href="/m/aaa/">Alpha B. - Beta C.</a></div>
<div><a href="/m/bbb/">Gamma D. - Delta E.</a></div>
<div class="prices"><span>1.50</span><span>2.50</span></div></div>
</body></html>
"""


def test_climb_finds_prices_in_single_match_card():
    rows = po.parse_listing_page(NESTED_SINGLE, source_label="T", is_match_link=_is_match,
                                 page_date="2026-09-13")
    assert len(rows) == 1
    assert (rows[0]["player_home"], rows[0]["odds_home"], rows[0]["odds_away"]) == (
        "Alpha B.", 1.50, 2.50)


def test_climb_never_enters_multi_match_box():
    rows = po.parse_listing_page(NESTED_SIBLINGS, source_label="T", is_match_link=_is_match,
                                 page_date="2026-09-13")
    # Both links' boxes hold 2 matches at the price level: no misattribution.
    assert rows == []


def test_retry_after_429_then_success(monkeypatch):
    calls = []

    def fake_curl(url, source_label, timeout=30):
        calls.append("curl")
        if len([c for c in calls if c == "curl"]) == 1:
            return "", 429, 7
        return "<html></html>", 200, 0

    def fake_plain(url, source_label, timeout=30):
        calls.append("plain")
        return "", 429, 0

    sleeps = []
    monkeypatch.setattr(po, "_curl_fetch", fake_curl)
    monkeypatch.setattr(po, "_plain_fetch", fake_plain)
    monkeypatch.setattr(po.time, "sleep", lambda s: sleeps.append(s))
    html = po.fetch_page_html("http://x/", "T")
    assert html == "<html></html>"
    assert sleeps == [7]  # honors Retry-After


def test_prefer_plain_tries_plain_first(monkeypatch):
    calls = []

    def fake_plain(url, source_label, timeout=30):
        calls.append("plain")
        return "<html></html>", 200, 0

    def fake_curl(url, source_label, timeout=30):
        calls.append("curl")
        return "", None, 0

    monkeypatch.setattr(po, "_curl_fetch", fake_curl)
    monkeypatch.setattr(po, "_plain_fetch", fake_plain)
    assert po.fetch_page_html("http://x/", "T", prefer_plain=True) == "<html></html>"
    assert calls == ["plain"]


def test_curl_first_by_default(monkeypatch):
    calls = []

    def fake_plain(url, source_label, timeout=30):
        calls.append("plain")
        return "", None, 0

    def fake_curl(url, source_label, timeout=30):
        calls.append("curl")
        return "<html></html>", 200, 0

    monkeypatch.setattr(po, "_curl_fetch", fake_curl)
    monkeypatch.setattr(po, "_plain_fetch", fake_plain)
    assert po.fetch_page_html("http://x/", "T") == "<html></html>"
    assert calls == ["curl"]


def test_throttle_enforces_per_host_interval(monkeypatch):
    now = [1000.0]
    sleeps = []
    monkeypatch.setattr(po.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(po.time, "sleep", lambda s: (sleeps.append(s), now.__setitem__(0, now[0] + s)))
    po._last_request_time.clear()
    po._throttle("example.com")
    po._throttle("example.com")
    assert len(sleeps) == 1
    assert abs(sleeps[0] - 3.0) < 0.01
    po._throttle("other.com")
    assert len(sleeps) == 1  # separate host: no wait


def test_no_retry_without_429(monkeypatch):
    def fake_curl(url, source_label, timeout=30):
        return "", 403, 0

    def fake_plain(url, source_label, timeout=30):
        return "", 403, 0

    sleeps = []
    monkeypatch.setattr(po, "_curl_fetch", fake_curl)
    monkeypatch.setattr(po, "_plain_fetch", fake_plain)
    monkeypatch.setattr(po.time, "sleep", lambda s: sleeps.append(s))
    assert po.fetch_page_html("http://x/", "T") == ""
    assert sleeps == []
