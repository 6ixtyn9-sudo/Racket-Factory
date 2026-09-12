# Odds and settlement investigation — 12 September 2026

## Conclusion

The missing outcomes are **not simply absent from the web**. The prediction and result sources have overlapping Challenger/WTA coverage. There are capture, date, player-identity and price-orientation failures between those pages and the ledger.

Local recovery now yields **30 settled archived rows out of 45 (20W / 10L), 15 pending**. All four September 11 accas resolve; September 12 remains open. This includes the existing Tiafoe–Shelton entry on both archive dates: 30 settled rows must not be represented as 30 distinct matches.

The 25 newly imported result records were **reviewed and transcribed from public TennisExplorer result tables through the page-fetch tool**, not downloaded by the Python scraper. Each retains its match ID, detail URL, results-page URL and `capture_method=reviewed_public_result_table`. The reviewed extract is retained in `tests/fixtures/tennisexplorer_verified_2026-09-11_12.json`; normalized records are in `localdata/challenger_results_tennis_2026-09.csv.gz`.

## Source evidence

### BetClan: predictions are not final scores

44 of the 45 archived rows include BetClan as a prediction source (the remaining row is PredixSport-only). Examples:

- [Ivashka–Zhou](https://www.betclan.com/tennis/predictionsdetails/201041526/Ilya-Ivashka-vs-Yi-Zhou-prediction-h2h-tip-and-match-preview/): predicts Ivashka, 55%, set betting 2–1.
- [Soto–Sachko](https://www.betclan.com/tennis/predictionsdetails/200993410/Matias-Soto-vs-Vitaliy-Sachko-prediction-h2h-tip-and-match-preview/): predicts Sachko, 71%, set betting 2–0.

Neither rendered page inspected supplied a confirmed result or an identifiable decimal moneyline pair. In fact, the real results were Ivashka 2–0 and Sachko 2–1, demonstrating why the predicted score cannot be used as the result. Sponsor logos and percentage probabilities are not prices.

### Forebet: both the prices and results are visible

[September 11 results/predictions](https://www.forebet.com/en/tennis/predictions-yesterday) include Tulln, Shanghai, Seville, Antalya, Montreux and Barranquilla. For example:

| Match | Source price pair | Confirmed result |
|---|---|---|
| M. Soto – V. Sachko | +175 / −250 = 2.75 / 1.40 | 1–2, FT |
| M. Brunold – G. Heide | +200 / −278 | 2–0, FT |
| E. Jacquemot – A. Blinkova | +160 / −200 = 2.60 / 1.50 | 0–2, FT |

The fetched rendered page uses dates such as `09/11/2026 6:15 AM`. The old parser accepted only `%d/%m/%Y %H:%M`; that displayed format produces `match_date=None`, after which the backfill drops the row and live-card date filtering drops its prices. The corrected parser accepts both formats. A reduced source-schema fixture now exercises parsing → warehouse orientation → prediction/result CSV output → settlement.

**Verification limit:** the exact HTML returned to GitHub Actions was not downloadable here, so this is a reproduced parser failure against the current displayed format, not a claim that we have inspected the original failing CI response. New `source_capture_forebet.json` diagnostics distinguish zero parsed rows from missing dates, missing price pairs and missing FT results on the next run.

### TennisExplorer: independent result and price confirmation

- [September 11 all results](https://www.tennisexplorer.com/results/?type=all&year=2026&month=09&day=11)
- [September 11 WTA singles](https://www.tennisexplorer.com/results/?type=wta-single&year=2026&month=09&day=11)
- [September 11 WTA doubles](https://www.tennisexplorer.com/results/?type=wta-double&year=2026&month=09&day=11)
- [September 12 WTA singles](https://www.tennisexplorer.com/results/?type=wta-single&year=2026&month=09&day=12)

| Match | Source identifier | Result | Displayed reference prices |
|---|---|---|---|
| Ivashka I. – Zhou Y. | 3320881 | 2–0 | 1.73 / 2.03 |
| Sachko V. – Soto M. | 3320449 | 2–1 | 1.38 / 2.87 |
| Kotov P. – Galarneau A. | 3320261 | 2–1 | 2.13 / 1.66 |
| Cascino E. / Feng S. – Brancaccio N. / Papamichail D. | 3320653 | 2–0 | 1.63 / 2.15 |
| Blinkova A. – Jacquemot E. | 3321137 | 2–0 | 1.43 / 2.69 |

The doubles display truncates names (`Brancacci / Papamicha`) but link titles contain full names. The corrected parser uses those titles and `/doubles-team/` links, not only `/player/` links. It reads paired player rows rather than imaginary winner/loser cells. A secondary previous-day table must not be assigned the primary page's date.

Blinkova–Jacquemot is September 11 on Forebet and September 12 at 01:00 on TennisExplorer. The grader now allows a unique next-day result after exact-date matching, rather than searching arbitrary historical meetings. Prior-day results are not allowed to settle a newly dated ticket in this fallback.

### ForeTennis: literal “Challenger” was classified as Cloudflare

The fetch guard contained `"challenge" in resp.text.lower()`. That also matches `Challenger`, rejecting legitimate content. It now checks specific Cloudflare markers. A missing warehouse also used to abort capture before standalone results could be generated; that dependency is removed.

ForeTennis compact set totals need to preserve leading zeros (`03`, `02`). CSV string loading and strict score parsing now avoid dropping these away wins or treating a float such as `2.0` as a 2–0 home win. Matched rows also reorient the actual score with the players.

### PredixSport: published predictions were relabelled as today's fixtures

[The predictions index](https://www.predixsport.com/tennis_predictions) inspected on September 12 says `Last updated: 2026-09-11` and shows Zverev–Khachanov and Tiafoe–Shelton. The adapter previously stamped `date.today()` onto every fetch, and rejected decimal probabilities such as 70.5% with `isdigit()`.

It now preserves the publication date, retains LOW date confidence, accepts decimal probabilities, and does not scrape predicted games/aces as bookmaker odds. The publication date is still **not a verified match kickoff**. Historical duplicate archive rows were not silently rewritten.

### Bzzoiro and The Odds API

The Bzzoiro adapter retains prediction probabilities but does not ingest result/odds fields. Its capture requests today/tomorrow, not a result recovery window. There are no Bzzoiro-sourced picks in these 45 rows. Its authenticated live payload was not verified in this investigation; no unverified schema or guessed winner field was added.

The committed The Odds API score file contains US Open results, not the missing Challenger events. API prices already carry player labels; prediction probability is not grounds to reverse those labels.

## Implemented fixes beyond capture

1. Shared player matching across pricing, audit and ticket grading supports surname-first initials, compound surnames, accents, and full doubles teams. Conflicting initials and partial-team matches are rejected.
2. A reviewed alias maps `Irene Burillo` to `Irene Burillo Escorihuela`, supported by [the player profile](https://www.tennisexplorer.com/player/burillo-escorihuela/), rather than broadening all surname matches.
3. Forebet prices were mapped using the **predicted winner** instead of whether source home matched warehouse player A. Both daily and tournament paths now orient prices by identity. Reversed result scores follow the same orientation.
4. Prediction-probability-based price swapping is removed. A model disagreeing with a market does not prove an inverted market.
5. Match-only text parsing now accepts `vs` as well as `v` and `vs.` in audit and local OddsPortal lookup.
6. Invalid scrape pairs are rejected consistently rather than being accepted by warehouse enrichment and rejected later by the miner.
7. The daily result window includes today plus seven previous days. A TennisExplorer fixture-price capture path is wired in, with page-date validation, a short snapshot lifetime, and separation of pre-match prices from result-reference prices.
8. Raw prediction archives/source URLs and compact capture-health JSON are retained so cache eviction does not erase the evidence needed to diagnose matches.
9. Warehouse deduplication now preserves a finished result when a newly appended live prediction has the same match key; `keep="last"` previously allowed an empty winner to overwrite it.
10. Previous-turn fixes remain: reconstructed state saves even on frozen runs, accas settle independently, recorded weighted stakes are used, and regrading is idempotent.

## Ledger integrity and current outcome

- **Audit:** 30 settled rows, 20 wins, 10 losses, 15 pending; same-day inclusion remains enabled.
- **September 11:** 29 of 30 archived picks have ordinary completed results matched. The remaining Strakhova/Tikhonova–Jacquemot/Quevedo row has a nonstandard 1–0 with no played-set scores on the inspected source. It is deliberately not guessed as a conventional win/void.
- **September 12:** 14 picks remain pending. The settled Tiafoe–Shelton entry is the pre-existing repeated archive entry, not a newly completed September 12 match.
- **Tickets:** all four September 11 accas are in history; only September 12 remains open. Four wins, one bet-day. Regrading makes no further bank adjustment.
- **Paper bank:** 169.059468% under the existing archived ticket prices/stakes. **Three of the four accas contain estimated prices**, so this is not verified bookmaker PnL. Performance output now labels this explicitly.
- **Audit ROI:** still 0.37, based on only **one priced settled pick**. The 29 other settled rows are not retroactively assigned reference prices. More matched winners do not create historical executable odds.

## Validation and remaining work

`PYTHONPATH=src .venv/bin/pytest -q` — **78 tests passed** at the time of this report. Tests include real reviewed result identities, reduced HTML layouts, source dates, reversed prices/scores, FT guards, leading zeros, exact/overnight settlement, estimated-price labeling paths and repeat grading.

Python HTTP requests to the source sites fail SSL/connection setup in this sandbox. The separate page-fetch tool could inspect them. GitHub run metadata is accessible, but logs/artifact downloads fail at the storage endpoint. Therefore:

- The recovered ledger is verified locally against reviewed public results.
- The adapters and pipeline wiring are tested locally, but **successful autonomous CI capture is not yet verified**.
- Do not replace original archived pick odds with the result-page reference prices.
- On the next run using this branch's changes, inspect `source_capture_forebet.json`, `source_capture_tennisexplorer.json`, the result CSVs, and the expanded audit's per-pick result source/URL.
- No push or workflow dispatch was performed in this investigation.
