import hashlib
from datetime import datetime, timezone

from racketfactory.sources.betclan import BetClanPredictor


class _Response:
    status_code = 200

    def __init__(self, text):
        self.text = text


def test_bound_odds_pair_requires_named_home_away_slots_and_preserves_orientation():
    from bs4 import BeautifulSoup

    forward = BeautifulSoup(
        '<div class="match-odds"><span>Alex Example</span><b>1.80</b>'
        '<span>Ben Sample</span><b>2.10</b></div>', "html.parser")
    reverse = BeautifulSoup(
        '<div class="match-odds"><span>Ben Sample</span><b>2.10</b>'
        '<span>Alex Example</span><b>1.80</b></div>', "html.parser")
    unrelated = BeautifulSoup(
        '<div class="match-odds"><span>Odds</span><b>1.80</b><b>2.10</b></div>', "html.parser")
    set_market = BeautifulSoup(
        '<div class="set1-odds"><span>Alex Example</span><b>1.80</b>'
        '<span>Ben Sample</span><b>2.10</b></div>', "html.parser")

    assert BetClanPredictor._extract_bound_match_odds(forward, "Alex Example", "Ben Sample") == (1.8, 2.1)
    assert BetClanPredictor._extract_bound_match_odds(reverse, "Alex Example", "Ben Sample") == (1.8, 2.1)
    assert BetClanPredictor._extract_bound_match_odds(unrelated, "Alex Example", "Ben Sample") == (None, None)
    assert BetClanPredictor._extract_bound_match_odds(set_market, "Alex Example", "Ben Sample") == (None, None)


def test_optional_odds_subpage_records_fetch_time_and_response_digest(monkeypatch):
    body = "<html><body><table><tr><td>Alex Example</td><td>1.80</td></tr></table></body></html>"
    predictor = BetClanPredictor()
    requests = []

    def fake_get(url, category, timeout):
        requests.append((url, category, timeout))
        return _Response(body)

    monkeypatch.setattr(predictor, "_get", fake_get)
    url = "https://www.betclan.com/tennis/odds/fixture-1"
    soup = predictor._fetch_odds_subpage(url)
    meta = predictor._odds_subpage_meta[url]

    assert soup is not None
    assert meta["response_sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert meta["source_url"] == url
    assert meta["evidence_kind"] == "betclan_odds_subpage"
    observed = datetime.fromisoformat(meta["observed_at"])
    assert observed.tzinfo is not None and observed.utcoffset().total_seconds() == 0

    # A cached parse retains the same fetch observation instead of restamping it.
    assert predictor._fetch_odds_subpage(url) is soup
    assert len(requests) == 1
    assert predictor._odds_subpage_meta[url] == meta
