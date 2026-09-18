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
