import json

from scripts import capture_betexplorer_evidence as probe
from racketfactory.rebuild_diagnostics import capture_betexplorer_fragment
from racketfactory.sources._page_odds import PageFetchError


def test_evidence_probe_is_bounded_and_stops_on_rate_limit(monkeypatch):
    rows = [{"event_id": str(i), "match_url": f"/tennis/test/test/test/{i}/"} for i in range(4)]
    monkeypatch.setattr(probe.betexplorer, "_fetch_live", lambda *a, **kw: (rows, "complete"))
    calls = []
    def fake(row, **kwargs):
        calls.append((row, kwargs))
        raise PageFetchError("rate_limit", "blocked")
    monkeypatch.setattr(probe.betexplorer, "fetch_fixture_comparison", fake)
    result = probe.capture("2026-10-29")
    assert len(calls) == 1 and calls[0][1]["refresh_evidence"] is True
    assert result["publication_attempted"] is False and result["failures"][0]["kind"] == "rate_limit"


def test_evidence_bookmaker_labels_are_bounded_and_secret_filtered(tmp_path, monkeypatch):
    monkeypatch.setenv("RACKET_FACTORY_REBUILD_DIAGNOSTICS_DIR", str(tmp_path))
    capture_betexplorer_fragment('<div></div>', 'detail', event_id='abc', observed_at='now', status=200)
    html = '<table><tr data-bookie="1"><td><img title="bet365"></td></tr><tr data-bookie="2"><td><img title="APIKEY_SUPERSECRET"></td></tr></table>'
    capture_betexplorer_fragment(html, 'comparison', event_id='abc', observed_at='now', status=200, response_body='full response')
    text = (tmp_path / 'betexplorer_fragments.json').read_text()
    assert json.loads(text)['fragments'][1]['bookmaker_labels'] == ['bet365']
    assert 'APIKEY_SUPERSECRET' not in text and 'full response' not in text
