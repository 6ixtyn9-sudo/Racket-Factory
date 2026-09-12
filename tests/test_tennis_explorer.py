"""Fixture tests for the TennisExplorer rewrite.

The fixture mirrors the structure observed on TE `/results/` pages on
2026-09-11/12: tournament links with tour segments, time-anchored paired
rows, S columns, sup tiebreaks, truncated doubles text with full titles,
H/A reference odds, match-detail ids, walkover rows and header rows.
"""

from racketfactory.sources import tennis_explorer as te

FIXTURE = """
<html><head><title>Results 11.09.2026</title></head><body>
<h1>Tennis Results 11.09.2026</h1>
<div class="box">
<a href="https://www.tennisexplorer.com/sevilla-challenger/2026/atp-men/">Sevilla challenger</a>
<table class="result">
<tr><td></td><td></td><td>S</td><td>1</td><td>2</td><td>3</td><td>H</td><td>A</td><td></td></tr>
<tr><td>20:20</td><td class="t-name"><a href="/player/alcala-gurri/">Alcala Gurri M.</a></td>
    <td>2</td><td>6</td><td>6</td><td></td><td>1.44</td><td>2.62</td>
    <td><a href="/match-detail/?id=3320999">info</a></td></tr>
<tr><td></td><td class="t-name"><a href="/player/olivieri/">Olivieri G.</a></td>
    <td>0</td><td>4</td><td>2</td><td></td><td></td><td></td><td></td></tr>
<tr><td>18:10</td><td class="t-name"><a href="/player/munar/">Munar J.</a></td>
    <td>2</td><td>2</td><td>6</td><td>6</td><td>1.30</td><td>3.30</td>
    <td><a href="/match-detail/?id=3321001">info</a></td></tr>
<tr><td></td><td class="t-name"><a href="/player/brancaccio/">Brancaccio R.</a></td>
    <td>1</td><td>6</td><td>1</td><td>1</td><td></td><td></td><td></td></tr>
</table>
</div>
<div class="box">
<a href="https://www.tennisexplorer.com/montreux-wta/2026/wta-women/?type=double">Montreux WTA</a>
<table class="result">
<tr><td></td><td></td><td>S</td><td>1</td><td>2</td><td>H</td><td>A</td><td></td></tr>
<tr><td>20:10</td><td class="t-name"><a href="/doubles-team/cascino/feng-40405/" title="Cascino E. / Feng S.">Cascino E / Feng S.</a> (2)</td>
    <td>2</td><td>6</td><td>6</td><td>1.63</td><td>2.15</td>
    <td><a href="/match-detail/?id=3320653">info</a></td></tr>
<tr><td></td><td class="t-name"><a href="/doubles-team/brancaccio-a1afd/papamichail/" title="Brancaccio N. / Papamichail D.">Brancacci / Papamicha</a></td>
    <td>0</td><td>3</td><td>2</td><td></td><td></td><td></td></tr>
</table>
</div>
<div class="box">
<a href="https://www.tennisexplorer.com/barranquilla/2026/wta-women/?type=double">Barranquilla</a>
<table class="result">
<tr><td></td><td></td><td>S</td><td>1</td><td>2</td><td>H</td><td>A</td><td></td></tr>
<tr><td>02:35</td><td class="t-name"><a href="/doubles-team/strakhova/tikhonova/" title="Strakhova V. / Tikhonova A.">Strakhova / Tikhonova</a> (1)</td>
    <td>1</td><td></td><td></td><td>1.54</td><td>2.31</td>
    <td><a href="/match-detail/?id=3319827">info</a></td></tr>
<tr><td></td><td class="t-name"><a href="/doubles-team/jacquemot/quevedo-e94b5/" title="Jacquemot E. / Quevedo K.">Jacquemot / Quevedo K</a></td>
    <td>0</td><td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>22:15</td><td class="t-name"><a href="/doubles-team/hewitt-0f840/smith-6ecab/">Hewitt D. / Smith A.</a> (2)</td>
    <td>2</td><td>6</td><td>3</td><td>10</td><td>1.77</td><td>1.94</td>
    <td><a href="/match-detail/?id=3321129">info</a></td></tr>
<tr><td></td><td class="t-name"><a href="/doubles-team/cavalle-reimers/salden/" title="Cavalle-Reimers I. / Salden L.">Cavalle-R / Salden L.</a></td>
    <td>1</td><td>1</td><td>6</td><td>8</td><td></td><td></td><td></td></tr>
</table>
</div>
<div class="box">
<a href="https://www.tennisexplorer.com/us-open/2026/wta-women/?type=double">US Open</a>
<table class="result">
<tr><td>18:05</td><td class="t-name"><a href="/doubles-team/siniakova/townsend/" title="Siniakova K. / Townsend T.">Siniakova / Townsend</a> (1)</td>
    <td>2</td><td>5</td><td>6</td><td>6</td><td>1.09</td><td>6.72</td>
    <td><a href="/match-detail/?id=3320737">info</a></td></tr>
<tr><td></td><td class="t-name"><a href="/doubles-team/krueger-7fc13/montgomery-8a7c9/" title="Krueger A. / Montgomery R.">Krueger A / Montgomer</a></td>
    <td>1</td><td>7</td><td>0</td><td>2</td><td></td><td></td><td></td></tr>
</table>
</div>
<div class="box">
<a href="https://www.tennisexplorer.com/antalya-5-wta/2026/wta-women/?type=double">Antalya 5 WTA</a>
<table class="result">
<tr><td>17:50</td><td class="t-name"><a href="/doubles-team/ibragimova-ef6ec/zaytseva-e6a32/" title="Ibragimova A. / Zaytseva K.">Ibragimov / Zaytseva</a></td>
    <td>2</td><td>6</td><td>7</td><td>1.86</td><td>1.86</td>
    <td><a href="/match-detail/?id=3320913">info</a></td></tr>
<tr><td></td><td class="t-name"><a href="/doubles-team/bhosale/kulambayeva/" title="Bhosale R. / Kulambayeva Z.">Bhosale R / Kulambaye</a> (3)</td>
    <td>0</td><td>4</td><td>6<sup>1</sup></td><td></td><td></td><td></td></tr>
</table>
</div>
</body></html>
"""


def _page():
    return te.parse_results_page(FIXTURE, "2026-09-11")


def test_pair_count_and_no_header_pairing():
    page = _page()
    assert len(page.rows) == 7, page.stats
    for row in page.rows:
        assert "challenger" not in row["player_a"].lower()
        assert "challenger" not in row["player_b"].lower()
        assert row["player_a"] and row["player_b"]
    assert page.stats["skipped_header"] == 3


def test_tournament_and_tour_captured():
    rows = {r["_te_id"]: r for r in _page().rows}
    assert rows["3320999"]["tournament"] == "Sevilla challenger"
    assert rows["3320999"]["tour"] == "CHALLENGER"
    assert rows["3320653"]["tournament"] == "Montreux WTA"
    assert rows["3320653"]["tour"] == "WTA"
    assert rows["3320653"]["_match_type"] == "Doubles"
    assert rows["3320999"]["_match_type"] == "Singles"


def test_winner_from_s_column_and_row_order_scores():
    rows = {r["_te_id"]: r for r in _page().rows}
    r = rows["3320999"]
    assert r["winner"] == "Alcala Gurri M."
    assert r["score"] == "6-4 6-2"
    assert (r["_sets_a"], r["_sets_b"]) == (2, 0)


def test_doubles_title_names_not_truncated_text():
    rows = {r["_te_id"]: r for r in _page().rows}
    r = rows["3320653"]
    assert r["player_a"] == "Cascino E. / Feng S."
    assert r["player_b"] == "Brancaccio N. / Papamichail D."
    assert r["winner"] == "Cascino E. / Feng S."
    assert rows["3320737"]["player_b"] == "Krueger A. / Montgomery R."
    assert rows["3321129"]["player_b"] == "Cavalle-Reimers I. / Salden L."


def test_match_tiebreak_scores():
    rows = {r["_te_id"]: r for r in _page().rows}
    assert rows["3321129"]["score"] == "6-1 3-6 10-8"


def test_sup_tiebreak_split():
    rows = {r["_te_id"]: r for r in _page().rows}
    assert rows["3320913"]["score"] == "6-4 7-6"


def test_walkover_detected():
    rows = {r["_te_id"]: r for r in _page().rows}
    r = rows["3319827"]
    assert r["winner"] == "Strakhova V. / Tikhonova A."
    assert r["score"] == ""
    assert r["_result_status"] == "WALKOVER"


def test_reference_odds_and_ids():
    rows = {r["_te_id"]: r for r in _page().rows}
    r = rows["3320999"]
    assert r["_ref_odds_a"] == 1.44
    assert r["_ref_odds_b"] == 2.62
    assert r["_te_id"] == "3320999"


def test_visible_date_parsed():
    assert _page().visible_date == "2026-09-11"


def test_tour_mapping():
    assert te.tour_from_segment("atp-men", "sevilla-challenger") == "CHALLENGER"
    assert te.tour_from_segment("wta-women", "reus-itf") == "ITF-W"
    assert te.tour_from_segment("atp-men", "cassis-challenger") == "CHALLENGER"
    assert te.tour_from_segment("wta-women", "us-open") == "WTA"
    assert te.tour_from_segment("atp-men", "us-open") == "ATP"
    assert te.tour_from_segment("wta-women", "utr-pro-tennis-series") == "UTR"


def test_merge_keeps_earliest_date_per_id():
    old = [{"_te_id": "1", "match_date": "2026-09-12", "player_a": "A",
            "player_b": "B", "winner": "A", "score": "6-4 6-4",
            "tour": "ATP", "tournament": "X"}]
    new = [{"_te_id": "1", "match_date": "2026-09-11", "player_a": "A",
            "player_b": "B", "winner": "A", "score": "6-4 6-4",
            "tour": "ATP", "tournament": "X"}]
    merged = te.merge_rows(old, new)
    assert len(merged) == 1
    assert merged[0]["match_date"] == "2026-09-11"
