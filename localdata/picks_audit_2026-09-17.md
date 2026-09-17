# Racket Factory — Recent picks audit (2026-08-19 to 2026-09-17)

## Overall

- archived pick rows: 19
- archived pick dates: 3
- settled picks: 8
- wins: 3
- hit rate: 0.375
- priced picks: 4
- ROI: -0.6675
- ROI (real-priced): -0.6675 (n=4)
- ROI (paper-priced): None (n=0)
- pending picks: 11
- void picks: 0
- conflict picks: 0
- total picks: 19
- set diagnostic picks: 8
- selected won any set: 6 (0.75)
- selected won set 1: 4 (0.5)
- selected won set 2: 5 (0.625)
- selected won set 3: 0 (0.0)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-17
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Per-pick audit (won/lost/pending)

- 2026-09-15 P. Basile vs J. Reis Da Silva selected=P. Basile winner=? status=pending_no_result reason=no matching result rows found
- 2026-09-15 B. N. Nakashima vs A. Ilagan selected=A. Ilagan winner=? status=pending_no_result reason=no matching result rows found
- 2026-09-15 A. Mayo vs R. Seggerman selected=A. Mayo winner=Seggerman R. status=lost basis=Challenger_results@2026-09-15:1-6 6-4 6-4
- 2026-09-15 B. Cengiz vs G. Maristany selected=G. Maristany winner=G. Maristany status=won basis=Forebet_results@2026-09-15:5-7 5-7
- 2026-09-15 M. Sharipov vs T. J. Fancutt selected=M. Sharipov winner=M. Sharipov status=won basis=Forebet_results@2026-09-15:6-2 6-3
- 2026-09-15 P. Basile vs J. C. Martin Manzano selected=P. Basile winner=J. C. Martin Manzano status=lost basis=Forebet_results@2026-09-15:2-6 1-6
- 2026-09-16 Palicova / Struplova vs Novak / Sebestova selected=Palicova / Struplova winner=Novak K. / Sebestova I. status=lost basis=Challenger_results@2026-09-16:6-2 5-7 10-5
- 2026-09-16 Cavalle-Reimers / Salden vs Kobori / Plipuech selected=Cavalle-Reimers / Salden winner=Kobori M. / Plipuech P. status=lost basis=Challenger_results@2026-09-16:6-3 6-4
- 2026-09-16 Brancaccio / Erjavec vs Burillo / Fossa Huergo selected=Brancaccio / Erjavec winner=? status=pending_no_result reason=no matching result rows found
- 2026-09-16 Mikulskyte / Smith vs Strakhova / Tikhonova selected=Strakhova / Tikhonova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-16 Quevedo / Salkova vs Riera / Sierra selected=Quevedo / Salkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-16 Jennifer Ruggeri vs Despina Papamichail selected=Jennifer Ruggeri winner=Papamichail D. status=lost basis=Challenger_results@2026-09-16:6-1 6-7 7-5
- 2026-09-16 Noemi Basiletti vs Julie Struplova selected=Noemi Basiletti winner=Basiletti N. status=won basis=Challenger_results@2026-09-16:6-2 6-3
- 2026-09-17 Francisca Jorge vs Victoria Jimenez Kasintseva selected=Francisca Jorge winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-17 Samira De Stefano vs Alice Tubello selected=Samira De Stefano winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-17 Elina Avanesyan vs Alina Charaeva selected=Elina Avanesyan winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-17 Mikulskyte / Smith vs Strakhova / Tikhonova selected=Strakhova / Tikhonova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-17 Quevedo / Salkova vs Riera / Sierra selected=Quevedo / Salkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-17 Da Silva Fick / Kulambayeva vs Hruncakova / Kraus selected=Hruncakova / Kraus winner=? status=pending_no_result reason=result not final: live/in-progress markers present

## By Tour

- `CHALLENGER`: settled=4, wins=2, hit_rate=0.5, ROI=-0.335
- `WTA`: settled=4, wins=1, hit_rate=0.25, ROI=-1.0

## By Series

- `Challenger`: settled=4, wins=2, hit_rate=0.5, ROI=-0.335
- `International`: settled=4, wins=1, hit_rate=0.25, ROI=-1.0

## By Surface

- `Hard`: settled=8, wins=3, hit_rate=0.375, ROI=-0.6675

## By Bucket

- `SKIPPED_DEAD_EDGE`: settled=2, wins=1, hit_rate=0.5, ROI=-0.335
- `WATCHLIST`: settled=2, wins=0, hit_rate=0.0, ROI=-1.0
- `WATCHLIST_NO_ODDS`: settled=4, wins=2, hit_rate=0.5, ROI=None

## By Source

- `BetClan`: settled=4, wins=1, hit_rate=0.25, ROI=-1.0
- `Forebet`: settled=4, wins=2, hit_rate=0.5, ROI=-0.335
