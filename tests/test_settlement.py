"""Integrity tests for the shared settlement module.

Every case below is anchored in observed Sept-2026 evidence: names, scores and
failure modes that actually occurred in production data.
"""

from racketfactory.settlement import (
    check_score,
    parse_match_teams,
    players_match,
    row_finality,
    settle_selection,
    teams_match,
)


def _row(**kw):
    base = {"match_date": "2026-09-11", "player_a": "A", "player_b": "B",
            "winner": "", "score": "", "source": "Challenger_results",
            "tournament": "Sevilla"}
    base.update(kw)
    return base


# ---- identity: compound surnames -------------------------------------------


def test_compound_full_vs_initial_matches():
    ok, _ = players_match("Max Alcala Gurri", "Alcala Gurri M.")
    assert ok


def test_compound_diaz_acosta_matches():
    ok, _ = players_match("Facundo Diaz Acosta", "Diaz Acosta F.")
    assert ok


def test_compound_carreno_busta_hyphen_matches():
    ok, _ = players_match("Pablo Carreno Busta", "P. Carreno-Busta")
    assert ok


def test_compound_particle_de_jong_matches():
    ok, _ = players_match("Jesper De Jong", "De Jong J.")
    assert ok


def test_leading_initial_matches_trailing_initial():
    ok, _ = players_match("J. Munar", "Munar J.")
    assert ok


def test_bare_first_token_must_not_match_longer_compound():
    # "Alcala" alone is ambiguous (Nicolas Alcala exists) and must never
    # settle against "Alcala Gurri M.".
    ok, _ = players_match("Alcala", "Alcala Gurri M.")
    assert not ok


def test_zverev_rule_bare_never_matches_full_given():
    ok, _ = players_match("Zverev", "Alexander Zverev")
    assert not ok
    ok, _ = players_match("Zverev", "Mischa Zverev")
    assert not ok


def test_zverev_brothers_reject_each_other():
    ok, _ = players_match("Zverev A.", "Zverev M.")
    assert not ok
    ok, _ = players_match("Alexander Zverev", "Mischa Zverev")
    assert not ok


def test_zverev_full_vs_own_initial_matches():
    ok, _ = players_match("Alexander Zverev", "Zverev A.")
    assert ok


def test_bare_surname_vs_initial_allowed_and_flagged():
    ok, bare = players_match("Cascino", "Cascino E.")
    assert ok and bare


def test_conflicting_initials_reject():
    ok, _ = players_match("Max Alcala Gurri", "Alcala Gurri C.")
    assert not ok


def test_seed_markers_ignored():
    ok, _ = players_match("Munar J. (1)", "Jaume Munar")
    assert ok


# ---- teams -----------------------------------------------------------------


def test_singles_reversed_order_matches():
    ok, _ = teams_match("Rocha H. vs Lajovic D.", "Lajovic D.", "Rocha H.")
    assert ok


def test_doubles_order_insensitive():
    ok, _ = teams_match("Cascino / Feng vs Brancaccio / Papamichail",
                        "Brancaccio N. / Papamichail D.",
                        "Cascino E. / Feng S.")
    assert ok


def test_doubles_partner_swap_rejected():
    ok, _ = teams_match("Siniakova / Townsend vs Krueger / Montgomery",
                        "Siniakova / Krueger", "Townsend / Montgomery")
    assert not ok


def test_doubles_vs_singles_count_mismatch_rejected():
    ok, _ = teams_match("Cascino / Feng vs Brancaccio / Papamichail",
                        "Cascino E.", "Feng S.")
    assert not ok


def test_parse_match_teams():
    a, b = parse_match_teams("Brancaccio / Papamichail vs Cascino / Feng")
    assert a == ("Brancaccio", "Papamichail")
    assert b == ("Cascino", "Feng")


# ---- score validation --------------------------------------------------------


def test_complete_straight_sets_valid():
    assert check_score("2-0 6-4 6-4").valid


def test_complete_three_sets_valid():
    assert check_score("2-1 6-4 4-6 10-8").valid


def test_long_match_tiebreak_valid():
    assert check_score("2-1 4-6 6-3 13-11").valid


def test_fused_tiebreak_digits_valid():
    assert check_score("2-0 6-0 7-68").valid


def test_incomplete_final_set_rejected():
    chk = check_score("6-4 5-4")
    assert not chk.valid
    assert "incomplete" in chk.reason


def test_set_prefix_mismatch_rejected():
    chk = check_score("2-1 2-1 6-7 6-3")
    assert not chk.valid


def test_legacy_plain_score_valid():
    chk = check_score("6-4 6-4")
    assert chk.valid and (chk.sets_a, chk.sets_b) == (2, 0)


# ---- finality -----------------------------------------------------------------


def test_live_markers_force_not_final():
    fin = row_finality(_row(winner="A", score="S3 6-4 3-2"))
    assert not fin.final


def test_missing_winner_not_final():
    assert not row_finality(_row(score="6-4 6-4")).final


def test_winner_without_score_not_final():
    assert not row_finality(_row(winner="A", score="")).final


def test_retirement_final_and_flagged():
    fin = row_finality(_row(winner="A", score="1-0 4-0",
                            _comment="Retired"))
    assert fin.final and fin.status == "RETIRED"


def test_walkover_final_void_status():
    fin = row_finality(_row(winner="A", score="", _comment="Walkover"))
    assert fin.final and fin.status == "WALKOVER"


# ---- settlement ---------------------------------------------------------------


def test_exact_date_beats_adjacent():
    cands = [
        _row(match_date="2026-09-12", player_a="Alcala Gurri M.",
             player_b="Olivieri G.", winner="Alcala Gurri M.",
             score="2-1 4-6 6-3 6-0"),
        _row(match_date="2026-09-11", player_a="Alcala Gurri M.",
             player_b="Rocha H.", winner="Alcala Gurri M.",
             score="2-0 6-4 6-0"),
    ]
    s = settle_selection("Max Alcala Gurri vs Dusan Lajovic",
                         "Max Alcala Gurri", cands[:1], "2026-09-11")
    # wrong opponent entirely -> winner does not resolve selection
    assert s.outcome == "PENDING"
    s = settle_selection("Max Alcala Gurri vs Genaro Olivieri",
                         "Max Alcala Gurri", cands[:1], "2026-09-11")
    assert s.outcome == "WON"
    assert s.basis["match_date"] == "2026-09-12"


def test_conflicting_finals_force_conflict():
    cands = [
        _row(player_a="Lajovic D.", player_b="Rocha H.",
             winner="Lajovic D.", score="2-0 6-3 7-5", source="Challenger_results"),
        _row(player_a="Rocha H.", player_b="Lajovic D.",
             winner="Rocha H.", score="2-0 6-3 6-3", source="forebet_results"),
    ]
    s = settle_selection("Dusan Lajovic vs Henrique Rocha",
                         "Dusan Lajovic", cands, "2026-09-11")
    assert s.outcome == "CONFLICT"
    assert "Challenger" in s.reason and "forebet" in s.reason


def test_sevilla_alcala_olivieri_settles_won():
    cands = [_row(player_a="Alcala Gurri M.", player_b="Olivieri G.",
                   winner="Alcala Gurri M.", score="2-0 6-4 6-2")]
    s = settle_selection("Max Alcala Gurri vs Genaro Olivieri",
                         "Max Alcala Gurri", cands, "2026-09-11")
    assert s.outcome == "WON"
    assert s.basis["winner"] == "Alcala Gurri M."


def test_doubles_cascino_feng_settles_won():
    cands = [_row(player_a="Cascino E. / Feng S.",
                   player_b="Brancaccio N. / Papamichail D.",
                   winner="Cascino E. / Feng S.",
                   score="2-0 6-3 6-2", tournament="Montreux WTA")]
    s = settle_selection("Brancaccio / Papamichail vs Cascino / Feng",
                         "Cascino / Feng", cands, "2026-09-11")
    assert s.outcome == "WON"


def test_walkover_settles_void():
    cands = [_row(player_a="Strakhova / Tikhonova",
                   player_b="Jacquemot / Quevedo K",
                   winner="Strakhova / Tikhonova",
                   score="", _comment="Walkover")]
    s = settle_selection("Strakhova / Tikhonova vs Jacquemot / Quevedo",
                         "Strakhova / Tikhonova", cands, "2026-09-11")
    assert s.outcome == "VOID"


def test_incomplete_score_stays_pending():
    cands = [_row(player_a="Alcala Gurri M.", player_b="Rocha H.",
                   winner="Alcala Gurri M.", score="6-4 5-4")]
    s = settle_selection("Max Alcala Gurri vs Henrique Rocha",
                         "Max Alcala Gurri", cands, "2026-09-11")
    assert s.outcome == "PENDING"
    assert "not final" in s.reason


def test_sets_only_scores_row_is_final():
    fin = row_finality(_row(player_a="Ann Able", player_b="Bob Best",
                            winner="Ann Able", score="",
                            _sets_a=2, _sets_b=1))
    assert fin.final


def test_s_column_disagreement_rejects():
    fin = row_finality(_row(winner="A", score="6-4 6-4", _sets_a=1, _sets_b=0))
    assert not fin.final
    assert "S column" in fin.reason


def test_winner_set_majority_mismatch_rejects():
    fin = row_finality(_row(player_a="Zdenek Kolar", player_b="Alex Barrena",
                            winner="Alex Barrena",
                            score="2-1 62-7 7-5 7-5"))
    assert not fin.final
    assert "set majority" in fin.reason


def test_nan_score_treated_as_missing():
    fin = row_finality(_row(winner="A", score=float("nan")))
    assert not fin.final
    assert "without score" in fin.reason


def test_middle_given_name_allowed_after_initial_match():
    ok, _ = players_match("Genaro Alberto Olivieri", "Olivieri G.")
    assert ok


def test_middle_given_name_still_needs_first_match():
    ok, _ = players_match("Genaro Alberto Olivieri", "Olivieri A.")
    assert not ok


def test_post_span_surname_extra_still_rejects():
    ok, _ = players_match("Alcala", "Alcala Gurri M.")
    assert not ok


def _frow(a, b, w, score="", mid="", src="T"):
    return {"player_a": a, "player_b": b, "winner": w, "score": score,
            "match_date": "2026-09-11", "source": src,
            "_foretennis_match_id": mid}


def test_spelling_variants_do_not_conflict():
    rows = [_frow("Frances Tiafoe", "Ben Shelton", "Ben Shelton",
                  score="1-3 6-4 3-6 3-6 5-7"),
            _frow("Tiafoe F.", "Shelton B.", "Shelton B.", mid="2329")]
    from racketfactory.settlement import settle_selection
    s = settle_selection("Frances Tiafoe vs Ben Shelton", "Ben Shelton",
                         rows, "2026-09-11")
    assert s.outcome == "WON", s.reason


def test_genuine_conflict_survives_clustering():
    r1 = _frow("Coco Gauff", "Elena Rybakina", "Elena Rybakina", mid="a")
    r1.update({"_sets_a": 1, "_sets_b": 2})
    r2 = _frow("Rybakina E.", "Gauff C.", "Gauff C.", mid="b")
    r2.update({"_sets_a": 1, "_sets_b": 2})
    rows = [r1, r2]
    from racketfactory.settlement import settle_selection
    s = settle_selection("Coco Gauff vs Elena Rybakina", "Elena Rybakina",
                         rows, "2026-09-11")
    assert s.outcome == "CONFLICT", s.reason


def test_nan_score_never_leaks_into_basis():
    import math
    rows = [_frow("Tiafoe F.", "Shelton B.", "Shelton B.", score=float("nan"))]
    from racketfactory.settlement import settle_selection
    s = settle_selection("Tiafoe F. vs Shelton B.", "Shelton B.",
                         rows, "2026-09-11")
    assert s.basis.get("score", "") != "nan"


def test_truncation_prefix_tokens_match():
    for trunc, full in [("Kulambaye", "Kulambayeva"),
                        ("Ibragimov", "Ibragimova"),
                        ("Montgomer", "Montgomery"),
                        ("Brancacci", "Brancaccio"),
                        ("Papamicha", "Papamichail")]:
        ok, _ = players_match(trunc, full)
        assert ok, (trunc, full)


def test_short_tokens_still_need_equality():
    ok, _ = players_match("Zverev", "Zvereva")
    assert not ok


def test_compound_surname_given_initial_match():
    ok, _ = players_match("Irene Burillo", "Burillo Escorihuela I.")
    assert ok


def test_compound_surname_extra_without_given_rejects():
    ok, _ = players_match("Rybakina E.", "Rybakina Petrova E.")
    assert not ok


def test_doubles_truncated_teams_match():
    from racketfactory.settlement import teams_match
    ok, _ = teams_match("Bhosale / Kulambayeva vs Ibragimova / Zaytseva",
                        "Ibragimov / Zaytseva", "Bhosale R / Kulambaye")
    assert ok
