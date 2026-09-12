# Racket Factory — Recent picks audit (2026-08-14 to 2026-09-12)

## Overall

- archived pick rows: 17
- archived pick dates: 1
- settled picks: 10
- wins: 9
- hit rate: 0.9
- priced picks: 0
- ROI: None
- ROI (real-priced): None (n=0)
- ROI (paper-priced): None (n=0)
- pending picks: 7
- void picks: 0
- conflict picks: 0
- total picks: 17
- set diagnostic picks: 10
- selected won any set: 9 (0.9)
- selected won set 1: 6 (0.6)
- selected won set 2: 7 (0.7)
- selected won set 3: 5 (1.0)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-12
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Per-pick audit (won/lost/pending)

- 2026-09-12 Aryna Sabalenka vs Elena Rybakina selected=Aryna Sabalenka winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-12 Frances Tiafoe vs Ben Shelton selected=Ben Shelton winner=Shelton B. status=won basis=Challenger_results@2026-09-12:4-6 6-3 6-3 7-5
- 2026-09-12 Alevtina Ibragimova vs Alicia Herrero Linana selected=Alevtina Ibragimova winner=Ibragimova A. status=won basis=Challenger_results@2026-09-12:4-6 6-2 6-3
- 2026-09-12 Cengiz / Oz vs Kucmova / Laboutkova selected=Kucmova / Laboutkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-12 Claire Liu vs Anna Blinkova selected=Claire Liu winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-12 Dalma Galfi vs Fiona Ferro selected=Fiona Ferro winner=Ferro F. status=won basis=Challenger_results@2026-09-12:6-1 2-6 6-0
- 2026-09-12 Fossa Huergo / Havlickova vs Cascino / Feng selected=Fossa Huergo / Havlickova winner=Fossa Huergo N. / Havlickova L. status=won basis=Challenger_results@2026-09-12:6-3 6-7 10-8
- 2026-09-12 Harrison / Skupski vs Krawietz / Puetz selected=Harrison / Skupski winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-12 Jaume Munar vs Facundo Diaz Acosta selected=Facundo Diaz Acosta winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-12 Joao Lucas Reis da Silva vs Alex Barrena selected=Joao Lucas Reis da Silva winner=Reis Da Silva J. status=won basis=Challenger_results@2026-09-12:6-2 6-3
- 2026-09-12 Luca Castelnuovo vs Ilya Ivashka selected=Ilya Ivashka winner=Ivashka I. status=won basis=Challenger_results@2026-09-12:6-3 6-4
- 2026-09-12 Max Alcala Gurri vs Dusan Lajovic selected=Dusan Lajovic winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-12 McAdoo / Rogers vs Hewitt / Smith selected=Hewitt / Smith winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-12 Noma Noha Akugue vs Guiomar Maristany Zuleta De Reales selected=Noma Noha Akugue winner=Noha Akugue N. status=won basis=Challenger_results@2026-09-12:7-6 6-3
- 2026-09-12 Pavel Kotov vs Marat Sharipov selected=Pavel Kotov winner=Sharipov M. status=lost basis=Challenger_results@2026-09-12:6-3 7-5
- 2026-09-12 Rositsa Dencheva vs Irene Burillo selected=Rositsa Dencheva winner=Dencheva R. status=won basis=Challenger_results@2026-09-12:4-6 6-4 6-4
- 2026-09-12 Vitaliy Sachko vs Mika Brunold selected=Mika Brunold winner=Brunold M. status=won basis=Challenger_results@2026-09-12:6-0 6-2

## By Tour

- `ATP`: settled=1, wins=1, hit_rate=1.0, ROI=None
- `CHALLENGER`: settled=4, wins=3, hit_rate=0.75, ROI=None
- `WTA`: settled=5, wins=5, hit_rate=1.0, ROI=None

## By Series

- `Challenger`: settled=4, wins=3, hit_rate=0.75, ROI=None
- `Grand Slam`: settled=1, wins=1, hit_rate=1.0, ROI=None
- `International`: settled=5, wins=5, hit_rate=1.0, ROI=None

## By Surface

- `Grass`: settled=4, wins=3, hit_rate=0.75, ROI=None
- `Hard`: settled=6, wins=6, hit_rate=1.0, ROI=None

## By Bucket

- `WATCHLIST_NO_ODDS`: settled=10, wins=9, hit_rate=0.9, ROI=None

## By Source

- `BetClan`: settled=9, wins=8, hit_rate=0.888889, ROI=None
- `BetClan, PredixSport`: settled=1, wins=1, hit_rate=1.0, ROI=None
