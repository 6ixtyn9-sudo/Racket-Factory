# Racket Factory — Recent picks audit (2026-09-20 to 2026-09-21)

## Overall

- current regime: genesis-2026-09-20 (top-level stats are scoped to this regime; per-regime split in By Regime)
- archived pick rows: 37
- archived pick dates: 2
- ledger pick rows (in window): 37
- duplicate rows merged (same match + selection re-picked): 0
- stale history rows pruned (pick no longer in ledger): 2
- settled picks: 29
- wins: 22
- hit rate: 0.758621
- priced picks: 6
- ROI: -0.033333
- ROI (real-priced): -0.033333 (n=6)
- ROI (paper-priced): None (n=0)
- pending picks: 8
- void picks: 0
- conflict picks: 0
- total picks: 37
- set diagnostic picks: 29
- selected won any set: 27 (0.931034)
- selected won set 1: 21 (0.724138)
- selected won set 2: 21 (0.724138)
- selected won set 3: 6 (0.5)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-21
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Ledger reconciliation

- ledger rows in window (official): 37
- duplicate rows merged (same match + selection in multiple daily ledgers): 0
- stale history rows pruned (pick no longer in any archived ledger): 2
- audited rows: 37
- identity check: ledger rows - duplicate rows merged = audited rows; By-* tables cover every pick (total = settled + pending + void + conflict)
- unresolved rows by reason:
  - pending_no_result: result not final: live/in-progress markers present: 7
  - pending_no_result: result not final: score not final: incomplete final set 1-1: 1

## Per-pick audit (won/lost/pending)

- 2026-09-20 Alja Senica vs Enola Chiesa selected=Enola Chiesa winner=Chiesa E. status=won basis=Challenger_results@2026-09-20:4-6 6-4 6-3
- 2026-09-20 Aneta Laboutkova vs Maria Toma selected=Aneta Laboutkova winner=Laboutkova A. status=won basis=Challenger_results@2026-09-20:6-2 6-1
- 2026-09-20 Angelina Voloshchuk vs Jana Otzipka selected=Angelina Voloshchuk winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Cascino / Feng vs Ciric Bagaric / Moratelli selected=Cascino / Feng winner=Ciric Bagaric L. / Moratelli A. status=lost basis=Challenger_results@2026-09-20:6-7 6-4 10-5
- 2026-09-20 Dalila Spiteri vs Laura Boehner selected=Dalila Spiteri winner=Spiteri D. status=won basis=Challenger_results@2026-09-20:6-4 6-2
- 2026-09-20 Gabriella Da Silva Fick vs Valentina Steiner selected=Valentina Steiner winner=? status=pending_no_result reason=result not final: score not final: incomplete final set 1-1
- 2026-09-20 Harold Mayot vs Sascha Gueymard Wayenburg selected=Harold Mayot winner=Gueymard Wayenburg S. status=lost basis=Challenger_results@2026-09-20:4-6 6-3 7-5
- 2026-09-20 Jakupovic / Karamoko vs Burillo / Fossa Huergo selected=Jakupovic / Karamoko winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jamilah Snells vs Iva Primorac Pavicic selected=Iva Primorac Pavicic winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-20 Jasmijn Gimbrere vs Reese Brantmeier selected=Reese Brantmeier winner=Brantmeier R. status=won basis=Challenger_results@2026-09-20:6-1 6-0
- 2026-09-20 Jessica Pieri vs Yana Morderger selected=Jessica Pieri winner=Pieri J. status=won basis=Challenger_results@2026-09-20:7-5 6-3
- 2026-09-20 Michael Mmoh vs J.J. Wolf selected=J.J. Wolf winner=Mmoh M. status=lost basis=Challenger_results@2026-09-20:6-2 6-2
- 2026-09-20 Mona Barthel vs Samira De Stefano selected=Mona Barthel winner=De Stefano S. status=lost basis=Challenger_results@2026-09-20:3-6 6-1 7-5
- 2026-09-20 Nikolas Sanchez Izquierdo vs Thiago Monteiro selected=Nikolas Sanchez Izquierdo winner=Sanchez Izquierdo N. status=won basis=Challenger_results@2026-09-20:4-6 6-3 7-6
- 2026-09-20 Pavel Kotov vs Marat Sharipov selected=Marat Sharipov winner=Sharipov M. status=won basis=Challenger_results@2026-09-20:6-0 6-3
- 2026-09-20 Peyton Stearns vs Iva Jovic selected=Iva Jovic winner=Jovic I. status=won basis=Challenger_results@2026-09-20:6-4 6-2
- 2026-09-20 Sahaja Yamalapalli vs Maria Martinez Vaquero selected=Sahaja Yamalapalli winner=Yamalapalli S. status=won basis=Challenger_results@2026-09-20:6-0 6-3
- 2026-09-20 Semra Aksu vs Ekaterina Yashina selected=Ekaterina Yashina winner=Yashina E. status=won basis=Challenger_results@2026-09-20:6-2 6-4
- 2026-09-20 Tayisiya Morderger vs Katerina Tsygourova selected=Katerina Tsygourova winner=Tsygourova K. status=won basis=Challenger_results@2026-09-20:6-3 6-3
- 2026-09-21 Christie / Silva vs Ayukawa / Webley-Smith selected=Christie / Silva winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-21 Daria Egorova vs Melisa Ercan selected=Daria Egorova winner=Egorova D. status=won basis=Challenger_results@2026-09-21:7-5 6-1
- 2026-09-21 David Goffin vs Mark Lajal selected=Mark Lajal winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-21 Ferro / Fruhvirtova vs Tang / Xu selected=Tang / Xu winner=Tang Q. / Xu Y. status=won basis=Challenger_results@2026-09-21:6-4 6-2
- 2026-09-21 Guiomar Maristany Zuleta De Reales vs Despina Papamichail selected=Guiomar Maristany Zuleta De Reales winner=Maristany Zuleta De Reales G. status=won basis=Challenger_results@2026-09-21:6-3 6-3
- 2026-09-21 Hodzic / Malygina vs Jorge / Jorge selected=Jorge / Jorge winner=Jorge F. / Jorge M. status=won basis=Challenger_results@2026-09-21:6-1 3-6 14-12
- 2026-09-21 Katie Volynets vs Elvina Kalieva selected=Katie Volynets winner=Volynets K. status=won basis=Challenger_results@2026-09-21:6-3 6-0
- 2026-09-21 Kato / Perez vs Sakkari / Vekic selected=Kato / Perez winner=Kato M. / Perez E. status=won basis=Challenger_results@2026-09-21:7-5 6-1
- 2026-09-21 Kristina Mladenovic vs Nao Hibino selected=Nao Hibino winner=Hibino N. status=won basis=Challenger_results@2026-09-21:7-5 3-6 6-4
- 2026-09-21 Ksenia Zaytseva vs Ilay Yoruk selected=Ksenia Zaytseva winner=Yoruk I. status=lost basis=Challenger_results@2026-09-21:2-6 6-4 7-5
- 2026-09-21 Kylie Collins vs Valentina Steiner selected=Kylie Collins winner=Steiner V. status=lost basis=Challenger_results@2026-09-21:6-4 1-6 6-4
- 2026-09-21 Laura Samson vs Noemi Basiletti selected=Laura Samson winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-21 Lee / Park vs Lee / Ye selected=Lee / Ye winner=Lee Y. / Ye Q. status=won basis=Challenger_results@2026-09-21:6-3 7-5
- 2026-09-21 Lisa Zaar vs Enola Chiesa selected=Lisa Zaar winner=Zaar L. status=won basis=Challenger_results@2026-09-21:3-6 6-3 7-6
- 2026-09-21 Lizette Cabrera vs Sinja Kraus selected=Sinja Kraus winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-21 Marie Vogt vs Ekaterina Yashina selected=Marie Vogt winner=Vogt M. status=won basis=Challenger_results@2026-09-21:6-4 6-2
- 2026-09-21 Xinxin Yao vs Maya Joint selected=Maya Joint winner=Joint M. status=won basis=Challenger_results@2026-09-21:2-6 7-6 6-3
- 2026-09-21 Yue Yuan vs Dayeon Back selected=Yue Yuan winner=Back D. status=lost basis=Challenger_results@2026-09-21:6-3 4-6 6-1

## By Tour

- `CHALLENGER`: total=5, settled=4, wins=2, hit_rate=0.5, ROI=-1.0, pending=1
- `WTA`: total=32, settled=25, wins=20, hit_rate=0.8, ROI=0.16, pending=7

## By Series

- `Challenger`: total=5, settled=4, wins=2, hit_rate=0.5, ROI=-1.0, pending=1
- `International`: total=32, settled=25, wins=20, hit_rate=0.8, ROI=0.16, pending=7

## By Surface

- `Grass`: total=5, settled=4, wins=2, hit_rate=0.5, ROI=-1.0, pending=1
- `Hard`: total=32, settled=25, wins=20, hit_rate=0.8, ROI=0.16, pending=7

## By Bucket

- `SKIPPED_DEAD_EDGE`: total=5, settled=5, wins=3, hit_rate=0.6, ROI=-0.212, pending=0
- `WATCHLIST`: total=3, settled=1, wins=1, hit_rate=1.0, ROI=0.86, pending=2
- `WATCHLIST_NO_ODDS`: total=26, settled=23, wins=18, hit_rate=0.782609, ROI=None, pending=3
- `SKIPPED_VETO`: total=1, settled=0, wins=0, hit_rate=None, ROI=None, pending=1
- `CERTIFIED_CLEAN`: total=2, settled=0, wins=0, hit_rate=None, ROI=None, pending=2

## By Source

- `BetClan`: total=37, settled=29, wins=22, hit_rate=0.758621, ROI=-0.033333, pending=8

## By Regime

- `genesis-2026-09-20`: total=37, settled=29, wins=22, hit_rate=0.758621, ROI=-0.033333, pending=8
