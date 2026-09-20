# Racket Factory — Recent picks audit (2026-09-20 to 2026-09-20)

## Overall

- current regime: genesis-2026-09-20 (top-level stats are scoped to this regime; per-regime split in By Regime)
- archived pick rows: 8
- archived pick dates: 1
- ledger pick rows (in window): 8
- duplicate rows merged (same match + selection re-picked): 0
- stale history rows pruned (pick no longer in ledger): 0
- settled picks: 0
- wins: 0
- hit rate: None
- priced picks: 0
- ROI: None
- ROI (real-priced): None (n=0)
- ROI (paper-priced): None (n=0)
- pending picks: 8
- void picks: 0
- conflict picks: 0
- total picks: 8
- set diagnostic picks: 0
- selected won any set: 0 (None)
- selected won set 1: 0 (None)
- selected won set 2: 0 (None)
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

- ledger rows in window (official): 8
- duplicate rows merged (same match + selection in multiple daily ledgers): 0
- stale history rows pruned (pick no longer in any archived ledger): 0
- audited rows: 8
- identity check: ledger rows - duplicate rows merged = audited rows; By-* tables cover every pick (total = settled + pending + void + conflict)
- unresolved rows by reason:
  - pending_no_result: result not final: live/in-progress markers present: 8

## Per-pick audit (won/lost/pending)

- 2026-09-20 Aneta Laboutkova vs Maria Toma selected=Aneta Laboutkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Angelina Voloshchuk vs Jana Otzipka selected=Angelina Voloshchuk winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Dalila Spiteri vs Laura Boehner selected=Dalila Spiteri winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jamilah Snells vs Iva Primorac Pavicic selected=Iva Primorac Pavicic winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jasmijn Gimbrere vs Reese Brantmeier selected=Reese Brantmeier winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jessica Pieri vs Yana Morderger selected=Jessica Pieri winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Semra Aksu vs Ekaterina Yashina selected=Ekaterina Yashina winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Tayisiya Morderger vs Katerina Tsygourova selected=Katerina Tsygourova winner=? status=pending_no_result reason=result not final: live/in-progress markers present

## By Tour

- `WTA`: total=8, settled=0, wins=0, hit_rate=None, ROI=None, pending=8

## By Series

- `International`: total=8, settled=0, wins=0, hit_rate=None, ROI=None, pending=8

## By Surface

- `Hard`: total=8, settled=0, wins=0, hit_rate=None, ROI=None, pending=8

## By Bucket

- `CERTIFIED_CLEAN`: total=6, settled=0, wins=0, hit_rate=None, ROI=None, pending=6
- `SKIPPED_DEAD_EDGE`: total=2, settled=0, wins=0, hit_rate=None, ROI=None, pending=2

## By Source

- `BetClan`: total=8, settled=0, wins=0, hit_rate=None, ROI=None, pending=8

## By Regime

- `genesis-2026-09-20`: total=8, settled=0, wins=0, hit_rate=None, ROI=None, pending=8
