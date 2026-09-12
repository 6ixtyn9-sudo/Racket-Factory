"""Bzzoiro odds/best read-through static disk cache."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scripts import mine_edges as me


def test_static_cache_serves_without_token(tmp_path, monkeypatch):
    payload = {"date": "2026-09-11",
               "results": [{"match": {"player1": {"name": "A"},
                                           "player2": {"name": "B"}}}]}
    cache = tmp_path / "bzzoiro_odds_2026-09-11.json"
    cache.write_text(json.dumps(payload))
    monkeypatch.setattr(me, "_bzzoiro_cache_path", lambda d: cache)
    monkeypatch.delenv("BZZOIRO_TOKEN", raising=False)
    me.BZZOIRO_ODDS_CACHE.clear()
    try:
        assert me._bzzoiro_date_payload("2026-09-11") == payload["results"]
    finally:
        me.BZZOIRO_ODDS_CACHE.clear()


def test_no_token_no_cache_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_bzzoiro_cache_path",
                        lambda d: tmp_path / "missing.json")
    monkeypatch.delenv("BZZOIRO_TOKEN", raising=False)
    me.BZZOIRO_ODDS_CACHE.clear()
    try:
        assert me._bzzoiro_date_payload("2026-09-11") is None
    finally:
        me.BZZOIRO_ODDS_CACHE.clear()
