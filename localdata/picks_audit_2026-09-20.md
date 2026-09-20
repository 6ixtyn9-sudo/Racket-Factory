# Racket Factory — Recent picks audit (2026-09-20 to 2026-09-20)

## Overall

- current regime: genesis-2026-09-20 (top-level stats are scoped to this regime; per-regime split in By Regime)
- archived pick rows: 19
- archived pick dates: 1
- ledger pick rows (in window): 19
- duplicate rows merged (same match + selection re-picked): 0
- stale history rows pruned (pick no longer in ledger): 0
- settled picks: 4
- wins: 3
- hit rate: 0.75
- priced picks: 0
- ROI: None
- ROI (real-priced): None (n=0)
- ROI (paper-priced): None (n=0)
- pending picks: 15
- void picks: 0
- conflict picks: 0
- total picks: 19
- set diagnostic picks: 4
- selected won any set: 3 (0.75)
- selected won set 1: 3 (0.75)
- selected won set 2: 3 (0.75)
- selected won set 3: 0 (None)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-20
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Ledger reconciliation

- ledger rows in window (official): 19
- duplicate rows merged (same match + selection in multiple daily ledgers): 0
- stale history rows pruned (pick no longer in any archived ledger): 0
- audited rows: 19
- identity check: ledger rows - duplicate rows merged = audited rows; By-* tables cover every pick (total = settled + pending + void + conflict)
- unresolved rows by reason:
  - pending_no_result: result not final: live/in-progress markers present: 15

## Per-pick audit (won/lost/pending)

- 2026-09-20 Alja Senica vs Enola Chiesa selected=Enola Chiesa winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Aneta Laboutkova vs Maria Toma selected=Aneta Laboutkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Angelina Voloshchuk vs Jana Otzipka selected=Angelina Voloshchuk winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Cascino / Feng vs Ciric Bagaric / Moratelli selected=Cascino / Feng winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Dalila Spiteri vs Laura Boehner selected=Dalila Spiteri winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Gabriella Da Silva Fick vs Valentina Steiner selected=Valentina Steiner winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Harold Mayot vs Sascha Gueymard Wayenburg selected=Harold Mayot winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jakupovic / Karamoko vs Burillo / Fossa Huergo selected=Jakupovic / Karamoko winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jamilah Snells vs Iva Primorac Pavicic selected=Iva Primorac Pavicic winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jasmijn Gimbrere vs Reese Brantmeier selected=Reese Brantmeier winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jessica Pieri vs Yana Morderger selected=Jessica Pieri winner=Pieri J. status=won basis=Challenger_results@2026-09-20:7-5 6-3
- 2026-09-20 Michael Mmoh vs J.J. Wolf selected=J.J. Wolf winner=Mmoh M. status=lost basis=Challenger_results@2026-09-20:6-2 6-2
- 2026-09-20 Mona Barthel vs Samira De Stefano selected=Mona Barthel winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Nikolas Sanchez Izquierdo vs Thiago Monteiro selected=Nikolas Sanchez Izquierdo winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Pavel Kotov vs Marat Sharipov selected=Marat Sharipov winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Peyton Stearns vs Iva Jovic selected=Iva Jovic winner=Jovic I. status=won basis=Challenger_results@2026-09-20:6-4 6-2
- 2026-09-20 Sahaja Yamalapalli vs Maria Martinez Vaquero selected=Sahaja Yamalapalli winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Semra Aksu vs Ekaterina Yashina selected=Ekaterina Yashina winner=Yashina E. status=won basis=Challenger_results@2026-09-20:6-2 6-4
- 2026-09-20 Tayisiya Morderger vs Katerina Tsygourova selected=Katerina Tsygourova winner=? status=pending_no_result reason=result not final: live/in-progress markers present

## By Tour

- `CHALLENGER`: total=4, settled=1, wins=0, hit_rate=0.0, ROI=None, pending=3
- `WTA`: total=15, settled=3, wins=3, hit_rate=1.0, ROI=None, pending=12

## By Series

- `Challenger`: total=4, settled=1, wins=0, hit_rate=0.0, ROI=None, pending=3
- `International`: total=15, settled=3, wins=3, hit_rate=1.0, ROI=None, pending=12

## By Surface

- `Grass`: total=4, settled=1, wins=0, hit_rate=0.0, ROI=None, pending=3
- `Hard`: total=15, settled=3, wins=3, hit_rate=1.0, ROI=None, pending=12

## By Bucket

- `WATCHLIST_NO_ODDS`: total=7, settled=4, wins=3, hit_rate=0.75, ROI=None, pending=3
- `WATCHLIST`: total=3, settled=0, wins=0, hit_rate=None, ROI=None, pending=3
- `SKIPPED_DEAD_EDGE`: total=9, settled=0, wins=0, hit_rate=None, ROI=None, pending=9

## By Source

- `BetClan`: total=19, settled=4, wins=3, hit_rate=0.75, ROI=None, pending=15

## By Regime

- `genesis-2026-09-20`: total=19, settled=4, wins=3, hit_rate=0.75, ROI=None, pending=15
