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


BAD_NAMES_PAGE = """
<html><body><table>
<tr><td><a href="/m/aaa/">1.57</a></td></tr>
<tr><td><a href="/m/bbb/">Winner</a></td></tr>
<tr><td><a href="/m/ccc/">Alpha B. - Beta C.</a><span>1.50</span><span>2.50</span></td></tr>
</table></body></html>
"""


def test_bad_name_anchors_are_sampled_in_logs(caplog):
    with caplog.at_level("INFO", logger="racketfactory.sources._page_odds"):
        rows = po.parse_listing_page(BAD_NAMES_PAGE, source_label="T", is_match_link=_is_match,
                                     page_date="2026-09-13")
    assert len(rows) == 1
    assert "bad-names sample" in caplog.text
    assert "1.57" in caplog.text


def test_split_match_names_handles_separator():
    assert po.split_match_names("Alpha B. - Beta C.") == ("Alpha B.", "Beta C.")


def test_split_match_names_handles_fused_names():
    # Run #222: BetExplorer runner markup joins names with a bare space.
    assert po.split_match_names("Tiafoe F. Shelton B.") == ("Tiafoe F.", "Shelton B.")
    assert po.split_match_names("Lepchenko V. Melichar-Martinez N.") == (
        "Lepchenko V.", "Melichar-Martinez N.")
    assert po.split_match_names("Del Potro J. M. Smith J.") == (
        "Del Potro J. M.", "Smith J.")
    assert po.split_match_names("Van De Zandschulp B. Jones A.") == (
        "Van De Zandschulp B.", "Jones A.")


def test_split_match_names_rejects_scores_and_doubles():
    assert po.split_match_names("1:3") == ("", "")
    assert po.split_match_names("6:1, 6:3") == ("", "")
    # Single-team doubles (one slash) must still be rejected
    assert po.split_match_names("Britto L./Remondy Pagotto V. H.") == ("", "")
    # Two-team doubles without dash: now parsed when two slashes present
    # (BetExplorer results 2026-09-13 fused variant)
    home, away = po.split_match_names(
        "Britto L./Remondy Pagotto V. H. Tosetto R./Zanellato N.")
    assert home and away and "/" in home and "/" in away
    assert po.split_match_names("Winner") == ("", "")
    # Trailing score is stripped so finished rows reach the finished check.
    assert po.split_match_names("Tiafoe F. Shelton B. 1:3") == ("Tiafoe F.", "Shelton B.")


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


TWO_ANCHOR_ROW = """
<html><body><table>
<tr>
<td><a href="/m/xyz/">Borisiouk M.</a> vs <a href="/m/xyz/">Kim D. J.</a></td>
<td>1.45</td><td>2.80</td>
</tr>
<tr><td><a href="/m/other/">Alpha B. - Beta C.</a></td><td>1.90</td><td>1.90</td></tr>
</table></body></html>
"""


def test_two_sibling_anchors_same_href_parse_as_one_match():
    rows = po.parse_listing_page(TWO_ANCHOR_ROW, source_label="T", is_match_link=_is_match,
                                 page_date="2026-09-18")
    assert len(rows) == 2
    by_home = {r["player_home"]: r for r in rows}
    twin = by_home["Borisiouk M."]
    assert twin["player_away"] == "Kim D. J."
    assert (twin["odds_home"], twin["odds_away"]) == (1.45, 2.80)


TWO_ANCHOR_DIFFERENT_HREFS = """
<html><body><table>
<tr>
<td><a href="/m/aaa/">Borisiouk M.</a> vs <a href="/m/bbb/">Kim D. J.</a></td>
<td>1.45</td><td>2.80</td>
</tr>
</table></body></html>
"""


def test_two_anchors_different_hrefs_not_merged():
    rows = po.parse_listing_page(TWO_ANCHOR_DIFFERENT_HREFS, source_label="T",
                                 is_match_link=_is_match, page_date="2026-09-18")
    assert rows == []


def test_two_anchor_match_box_not_rejected_by_link_count():
    from bs4 import BeautifulSoup

    # The tr holds two anchors of one match: link count must be 1, not 2,
    # or _row_container would refuse the box and the row would be lost.
    tr = BeautifulSoup(TWO_ANCHOR_ROW, "html.parser").find("tr")
    assert po._match_link_count(tr, _is_match) == 1
