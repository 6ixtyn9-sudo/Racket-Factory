"""Forebet parser tests anchored in the observed Sept-12 page format."""

from racketfactory.sources.forebet import (
    ForebetPredictor,
    _forebet_price_to_decimal,
    _forebet_prices_from_text,
)
from racketfactory.settlement import home_is_player_a

FIXTURE = """
<html><body>
<div class="rcnt">
<div class="heading">Sevilla Challenger</div>
<a class="tnmscn" href="/en/tennis/matches/atp-challenger/sevilla/max-alcala-gurri-vs-dusan-lajovic/">
<span class="homeTeam">Max Alcala Gurri</span>
<span class="awayTeam">Dusan Lajovic</span>
<span class="date_bah">09/12/2026 5:30 PM</span>
</a>
<div class="fprc"><span>62</span><span>38</span></div>
<div class="predict"><span class="forepr"><span>1</span></span></div>
<div class="haodd">1.57 2.35</div>
</div>
<div class="rcnt">
<div class="heading">Barranquilla WTA</div>
<a class="tnmscn" href="/en/tennis/matches/wta/barranquilla/elena-rybakina-vs-anna-blinkova/">
<span class="homeTeam">Elena Rybakina</span>
<span class="awayTeam">Anna Blinkova</span>
<span class="date_bah">09/12/2026 6:10 AM</span>
</a>
<div class="fprc"><span>79</span><span>21</span></div>
<div class="predict"><span class="forepr"><span>1</span></span></div>
<div class="haodd">-227 +160</div>
</div>
</body></html>
"""


def test_observed_row_format_parses():
    rows = ForebetPredictor().parse_page(FIXTURE, expected_day="2026-09-12")
    assert len(rows) == 2
    first = rows[0]
    assert first["player_home"] == "Max Alcala Gurri"
    assert first["player_away"] == "Dusan Lajovic"
    assert first["match_date"] == "2026-09-12"
    assert first["match_time"] == "17:30"
    assert (first["prob_home"], first["prob_away"]) == (62, 38)
    assert first["predicted_winner"] == "1"
    assert (first["odds_home"], first["odds_away"]) == (1.57, 2.35)
    assert first["tournament"] == "Sevilla Challenger"


def test_american_odds_converted():
    rows = ForebetPredictor().parse_page(FIXTURE)
    second = rows[1]
    assert second["odds_home"] == round(1.0 + 100.0 / 227.0, 6)
    assert second["odds_away"] == 2.60


def test_probabilities_never_become_odds():
    assert _forebet_price_to_decimal("79") is None
    assert _forebet_price_to_decimal("7") is None
    assert _forebet_price_to_decimal("+160") == 2.60
    assert _forebet_price_to_decimal("-227") == round(1.0 + 100.0 / 227.0, 6)
    assert _forebet_price_to_decimal("1.57") == 1.57
    assert _forebet_prices_from_text("79 21") == []


def test_calendar_day_accepted():
    predictor = ForebetPredictor()
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return "<html></html>"

    predictor._fetch = fake_fetch
    predictor.fetch_daily_predictions("2026-09-12")
    # Slash shape: the old dash-shaped explicit URL 404s (verified 2026-09-13).
    assert seen["url"].endswith("/tennis/predictions/2026-09-12")


def test_day_labels_resolve_to_dated_urls():
    from datetime import date, timedelta

    predictor = ForebetPredictor()
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return "<html></html>"

    predictor._fetch = fake_fetch
    predictor.fetch_daily_predictions("today")
    assert seen["url"].endswith(f"/tennis/predictions/{date.today().isoformat()}")
    predictor.fetch_daily_predictions("tomorrow")
    assert seen["url"].endswith(f"/tennis/predictions/{(date.today() + timedelta(days=1)).isoformat()}")
    predictor.fetch_daily_predictions("yesterday")
    assert seen["url"].endswith(f"/tennis/predictions/{(date.today() - timedelta(days=1)).isoformat()}")


def test_orientation_strict_and_zverev_safe():
    assert home_is_player_a("Zverev A.", "Nadal R.",
                            "Alexander Zverev", "Rafael Nadal") is True
    assert home_is_player_a("Nadal R.", "Zverev A.",
                            "Alexander Zverev", "Rafael Nadal") is False
    # Brothers facing each other resolve by initial instead of colliding.
    assert home_is_player_a("Zverev A.", "Zverev M.",
                            "Alexander Zverev", "Mischa Zverev") is True
    assert home_is_player_a("Zverev A.", "Nadal R.",
                            "Mischa Zverev", "Rafael Nadal") is None


def test_strict_signature_separates_brothers():
    from racketfactory.sources.forebet import name_signature_strict as strict

    assert strict("Alexander Zverev") == "zverev|a"
    assert strict("Mischa Zverev") == "zverev|m"
    assert strict("Zverev A.") == strict("Alexander Zverev")
    assert strict("Bergs Z.") == strict("Zizou Bergs")
    assert strict("Alcala Gurri M.") == strict("Max Alcala Gurri")
    assert strict("P. Carreno-Busta") == strict("Pablo Carreno Busta")


# Jina markdown shape for a tournament page (no tnmscn anchors in the relay
# HTML — the 2026-09-18 "Parsed 0 predictions" case). fetch_tournament_predictions
# must fall back to the markdown parser and still yield predictions.
TOURNAMENT_JINA_MD = """
ATP Bastad - Round of 16
[F. Tiafoe B. Shelton 12/09/2026 01:45](https://www.forebet.com/en/tennis/matches/atp-singles/atp-bastad/tiafoe-ben-shelton-ben/)
41 59
2 1-3
10.2
-152
[A. Zverev H. Rune 13/09/2026 03:00](https://www.forebet.com/en/tennis/matches/atp-singles/atp-bastad/zverev-alexander-rune-holger/)
55 45
1 2-1
10.4
-110
"""


def test_fetch_tournament_predictions_jina_markdown_fallback(monkeypatch):
    p = ForebetPredictor()
    monkeypatch.setattr(p, "_fetch_tournament_page", lambda tour, tourn: TOURNAMENT_JINA_MD)
    preds = p.fetch_tournament_predictions("ATP", "Bastad")
    assert len(preds) == 2
    tiaofoe = next(x for x in preds if "Tiafoe" in (x.get("player_home") or ""))
    assert tiaofoe["player_away"] and "Shelton" in tiaofoe["player_away"]
    assert tiaofoe["tournament"] == "Bastad"
    assert tiaofoe["prob_home"] == 41
    assert tiaofoe["prob_away"] == 59
    assert tiaofoe["predicted_winner"] == "2"
    zverev = next(x for x in preds if "Zverev" in (x.get("player_home") or ""))
    assert zverev["predicted_winner"] == "1"


def test_fetch_tournament_predictions_html_with_tnmscn_uses_parse_page(monkeypatch):
    p = ForebetPredictor()
    monkeypatch.setattr(p, "_fetch_tournament_page", lambda tour, tourn: FIXTURE)
    preds = p.fetch_tournament_predictions("ATP", "Sevilla")
    assert len(preds) == 2
    assert preds[0]["tournament"] == "Sevilla"
