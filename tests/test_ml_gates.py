"""Capital-protection gate defaults (prompt 2026-09-18) + env overrides."""


def test_min_ev_real_default_is_two_percent():
    from racketfactory import ml

    assert ml.get_min_ev_real() == 0.02


def test_min_ev_real_env_override_restores_red_day_tuning(monkeypatch):
    from racketfactory import ml

    monkeypatch.setenv("RACKET_FACTORY_MIN_EV", "0.01")
    assert ml.get_min_ev_real() == 0.01
    monkeypatch.setenv("RACKET_FACTORY_MIN_EV", "not-a-number")
    assert ml.get_min_ev_real() == 0.02  # fail-soft back to default


def test_min_odds_base_default_is_130():
    from racketfactory import ml

    assert ml.get_min_odds_base() == 1.30


def test_min_odds_base_env_override(monkeypatch):
    from racketfactory import ml

    monkeypatch.setenv("RACKET_FACTORY_MIN_ODDS", "1.20")
    assert ml.get_min_odds_base() == 1.20
    monkeypatch.setenv("RACKET_FACTORY_MIN_ODDS", "garbage")
    assert ml.get_min_odds_base() == 1.30


def test_monitor_performance_emits_base_min_odds(monkeypatch):
    from racketfactory import ml

    monkeypatch.setenv("RACKET_FACTORY_MIN_ODDS", "1.32")
    health = ml.monitor_performance()
    adj = health.get("adjustments", {})
    # High-hit branch emits the base; otherwise 1.35. Both must respect the base floor.
    assert adj.get("min_odds_per_leg") in (1.32, 1.35)


# --- monitor_performance self-tightening loop (reads clv_rolling.json's real
# "calibration_by_confidence" table; the old "by_bucket" key never existed) ---


def _isolated_monitor(monkeypatch, tmp_path, clv):
    """monitor_performance() with only load_clv_rolling feeding it."""
    from racketfactory import ml

    monkeypatch.delenv("RACKET_FACTORY_MIN_ODDS", raising=False)  # base 1.30
    monkeypatch.setattr(ml, "LOCALDATA", tmp_path)  # no auto_tickets_state.json
    monkeypatch.setattr(ml, "load_audit_rolling", lambda: {})
    monkeypatch.setattr(ml, "load_performance", lambda: {})
    monkeypatch.setattr(ml, "load_clv_rolling", lambda: clv)
    return ml.monitor_performance()["adjustments"]


def _clv_high(hit_rate, n=333):
    # Shape written by scripts/audit_clv.py.
    return {"calibration_by_confidence": {
        "High (70%+)": {"hit_rate": hit_rate, "n": n, "wins": 251, "wilson_lb": 0.7043},
        "Medium (60-70%)": {"hit_rate": 0.6887, "n": 559, "wins": 385, "wilson_lb": 0.6492},
    }}


def test_monitor_tightens_when_real_high_band_below_80(monkeypatch, tmp_path):
    adj = _isolated_monitor(monkeypatch, tmp_path, _clv_high(0.7538))
    assert adj["min_odds_per_leg"] == 1.35
    assert "75.4%" in adj["reason"] and "n=333" in adj["reason"]


def test_monitor_keeps_base_when_real_high_band_at_or_above_80(monkeypatch, tmp_path):
    adj = _isolated_monitor(monkeypatch, tmp_path, _clv_high(0.80, n=120))
    assert adj["min_odds_per_leg"] == 1.30
    assert "80.0%" in adj["reason"] and "n=120" in adj["reason"]


def test_monitor_missing_or_non_numeric_high_band_keeps_prior(monkeypatch, tmp_path):
    for clv in (
        {},                                                   # no clv file
        {"by_bucket": {"High": {"hit_rate": 0.50}}},          # legacy key: not read
        {"calibration_by_confidence": {}},                    # band missing
        {"calibration_by_confidence": None},
        _clv_high(None, n=0),                                 # audit_clv n=0 output
        _clv_high("0.50"),                                    # not numeric
        _clv_high(float("nan")),
        _clv_high(True),
    ):
        adj = _isolated_monitor(monkeypatch, tmp_path, clv)
        assert adj["min_odds_per_leg"] == 1.30, clv
        assert "prior" in adj["reason"], clv
