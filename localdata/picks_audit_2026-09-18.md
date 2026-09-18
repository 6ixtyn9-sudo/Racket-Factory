# Racket Factory — Recent picks audit (2026-09-13 to 2026-09-18)

## Overall

- archived pick rows: 64
- archived pick dates: 6
- ledger pick rows (in window): 65
- duplicate rows merged (same match + selection re-picked): 1
- stale history rows pruned (pick no longer in ledger): 3
- settled picks: 48
- wins: 22
- hit rate: 0.458333
- priced picks: 43
- ROI: -0.246512
- ROI (real-priced): -0.246512 (n=43)
- ROI (paper-priced): None (n=0)
- pending picks: 16
- void picks: 0
- conflict picks: 0
- total picks: 64
- set diagnostic picks: 48
- selected won any set: 34 (0.708333)
- selected won set 1: 23 (0.479167)
- selected won set 2: 26 (0.541667)
- selected won set 3: 7 (0.368421)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-18
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Ledger reconciliation

- ledger rows in window (official): 65
- duplicate rows merged (same match + selection in multiple daily ledgers): 1
- stale history rows pruned (pick no longer in any archived ledger): 3
- audited rows: 64
- identity check: ledger rows - duplicate rows merged = audited rows; By-* tables cover every pick (total = settled + pending + void + conflict)
- unresolved rows by reason:
  - pending_no_result: no matching result rows found: 10
  - pending_no_result: result not final: live/in-progress markers present: 6

## Per-pick audit (won/lost/pending)

- 2026-09-13 A. Parks vs M. Sherif selected=M. Sherif winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-12 Mickoska S. vs Zylberman A. 0-6 6-4 7-5 [Challenger_results] (Mickoska S. side matches, other side unresolvable) ;; 2026-09-13 Mickoska S. vs Dodaj C. 3-6 6-1 6-1 [Challenger_results] (Mickoska S. side matches, other side unresolvable)
- 2026-09-13 D. E. Galan vs M. Vrbensky selected=D. E. Galan winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-13 Fakih K. vs Miroshnichenko V. 6-4 1-6 7-5 [Challenger_results] (Miroshnichenko V. side matches, other side unresolvable) ;; 2026-09-14 Wehnelt K. vs Vrbensky M. 5-7 2-6 [Forebet_results] (Vrbensky M. side matches, other side unresolvable)
- 2026-09-13 D. Martin vs A. Rybakov selected=A. Rybakov winner=Rybakov A. status=won basis=Challenger_results@2026-09-13:6-4 6-2
- 2026-09-13 J. Von der Schulenburg vs R. Molleker selected=R. Molleker winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-13 Lagutin P. vs Roehrich M. 6-1 6-0 [Challenger_results] (Roehrich M. side matches, other side unresolvable) ;; 2026-09-13 Johnson S. vs Rouvroy M. 6-4 6-2 [Challenger_results] (Rouvroy M. side matches, other side unresolvable)
- 2026-09-14 A. Gray vs P. Maloney selected=P. Maloney winner=Maloney P. status=won basis=Challenger_results@2026-09-14:6-3 5-7 7-6
- 2026-09-14 A. Marti Pujolras vs D. Sakellaridis selected=D. Sakellaridis winner=Marti Pujolras A. status=lost basis=Challenger_results@2026-09-14:6-1 6-1
- 2026-09-14 A. Moro Canas vs G. Blancaneaux selected=A. Moro Canas winner=Moro Canas A. status=won basis=Challenger_results@2026-09-14:6-2 7-6
- 2026-09-14 A. Shelbayh vs T. Zink selected=T. Zink winner=Shelbayh A. status=lost basis=Challenger_results@2026-09-14:6-4 3-6 6-3
- 2026-09-14 D. Blanch vs I. Gakhov selected=D. Blanch winner=Gakhov I. status=lost basis=Challenger_results@2026-09-14:6-2 1-6 6-2
- 2026-09-14 D. Masur vs D. De Jonge selected=D. Masur winner=Masur D. status=won basis=Challenger_results@2026-09-14:7-6 4-6 7-6
- 2026-09-14 E. Bennemann vs M. Bassols selected=M. Bassols winner=M. Bassols status=won basis=Forebet_results@2026-09-14:4-6 2-6
- 2026-09-14 F. Bass vs K. De Schepper selected=K. De Schepper winner=Bass F. status=lost basis=Challenger_results@2026-09-14:7-6 3-6 6-3
- 2026-09-14 F. Diaz Acosta vs M. Alcala Gurri selected=F. Diaz Acosta winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-13 Diaz Acosta F. vs Lajovic D. 6-2 6-1 [Challenger_results] (Diaz Acosta F. side matches, other side unresolvable) ;; 2026-09-13 Facundo Diaz Acosta vs Dusan Lajovic 6-2 530-115 [Forebet_results] (Facundo Diaz Acosta side matches, other side unresolvable)
- 2026-09-14 F. Pieczonka vs N. Mashtakov selected=N. Mashtakov winner=F. Pieczonka status=lost basis=Forebet_results@2026-09-14:6-3 6-2
- 2026-09-14 G. La Vela vs F. Iannaccone selected=F. Iannaccone winner=La Vela G. status=lost basis=Challenger_results@2026-09-14:7-5 2-6 7-6
- 2026-09-14 G. Oradini vs P. Basile selected=G. Oradini winner=Basile P. status=lost basis=Challenger_results@2026-09-14:6-4 7-6
- 2026-09-14 I. Almazan Valiente vs G. Ferrari selected=G. Ferrari winner=Almazan Valiente I. status=lost basis=Challenger_results@2026-09-14:6-1 6-1
- 2026-09-14 J. Boulais vs M. Bobichon selected=M. Bobichon winner=Bobichon M. status=won basis=Challenger_results@2026-09-14:7-6 6-3
- 2026-09-14 J. C. Martin Manzano vs G. Campana Lee selected=G. Campana Lee winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-13 Campana Lee G. vs Bosio G. 6-3 6-1 [Challenger_results] (Campana Lee G. side matches, other side unresolvable) ;; 2026-09-14 Firman A. vs Lee G. 7-5 1-6 6-0 [Challenger_results] (Lee G. side matches, other side unresolvable)
- 2026-09-14 J. Clarke vs J. Forejtek selected=J. Clarke winner=Clarke J. status=won basis=Challenger_results@2026-09-14:3-6 6-3 6-4
- 2026-09-14 J. Nikles vs M. Cerny selected=J. Nikles winner=Nikles J. status=won basis=Challenger_results@2026-09-14:6-3 6-2
- 2026-09-14 J. Wolf vs L. E. Ambrogi selected=J. Wolf winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-14 Wolf J. vs Ambrogi L. 6-2 6-0 [Forebet_results] (Wolf J. side matches, other side unresolvable) ;; 2026-09-14 Wolf J. vs Ambrogi L. 6-2 6-0 [Forebet_results] (Wolf J. side matches, other side unresolvable)
- 2026-09-14 L. Boskovic vs R. Serban selected=L. Boskovic winner=Boskovic L. status=won basis=Challenger_results@2026-09-14:6-7 7-6 6-2
- 2026-09-14 L. S. Steur J. vs D. Spiteri selected=D. Spiteri winner=L. S. Steur J. status=lost basis=Forebet_results@2026-09-14:6-1 7-6
- 2026-09-14 L. Staeheli vs R. Pascual Ferra selected=R. Pascual Ferra winner=Staeheli L. status=lost basis=Challenger_results@2026-09-14:4-6 6-2 6-3
- 2026-09-14 M. Petkovic vs F. Gill selected=F. Gill winner=Gill F. status=won basis=Challenger_results@2026-09-14:7-6 6-3
- 2026-09-14 M. Schoenhaus vs P. Brady selected=P. Brady winner=Schoenhaus M. status=lost basis=Challenger_results@2026-09-14:4-6 6-3 6-1
- 2026-09-14 M. Vrbensky vs K. Wehnelt selected=K. Wehnelt winner=Wehnelt K. status=won basis=Challenger_results@2026-09-14:7-5 6-2
- 2026-09-14 N. Slavic vs Y. Ghazouani Durand selected=Y. Ghazouani Durand winner=Ghazouani Durand Y. status=won basis=Challenger_results@2026-09-14:4-6 6-3 6-2
- 2026-09-14 O. Baris vs E. Zhu selected=O. Baris winner=Zhu E. status=lost basis=Challenger_results@2026-09-14:6-4 4-6 6-4
- 2026-09-14 P. Brunclik vs F. Romano selected=F. Romano winner=Brunclik P. status=lost basis=Challenger_results@2026-09-14:7-6 7-6
- 2026-09-14 P. Lagutin vs M. Marterer selected=M. Marterer winner=Lagutin P. status=lost basis=Challenger_results@2026-09-14:6-2 6-2
- 2026-09-14 P. Zahraj vs M. Janvier selected=M. Janvier winner=Janvier M. status=won basis=Challenger_results@2026-09-14:6-4 6-4
- 2026-09-14 Q. Vandecasteele vs B. N. Nakashima selected=Q. Vandecasteele winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-13 Boutleux N. vs Nobili R. 6-2 6-3 [Challenger_results] (Boutleux N. side matches, other side unresolvable) ;; 2026-09-13 Vandecasteele Q. vs Zamora N. 6-1 7-5 [Challenger_results] (Vandecasteele Q. side matches, other side unresolvable)
- 2026-09-14 R. Seggerman vs A. Rybakov selected=R. Seggerman winner=Seggerman R. status=won basis=Challenger_results@2026-09-14:7-6 6-3
- 2026-09-14 R. Strombachs vs S. Rodriguez Taverna selected=S. Rodriguez Taverna winner=Strombachs R. status=lost basis=Challenger_results@2026-09-14:6-0 6-2
- 2026-09-14 R. Zelnickova vs K. Najzer selected=R. Zelnickova winner=Zelnickova R. status=won basis=Challenger_results@2026-09-14:6-2 6-3
- 2026-09-14 S. Johnson vs E. Arutiunian selected=S. Johnson winner=Arutiunian E. status=lost basis=Challenger_results@2026-09-14:7-6 7-6
- 2026-09-14 T. Boyer vs E. Winter selected=T. Boyer winner=Boyer T. status=won basis=Challenger_results@2026-09-15:6-2 6-4
- 2026-09-14 T. Legout vs D. Ostapenkov selected=D. Ostapenkov winner=Legout T. status=lost basis=Challenger_results@2026-09-14:6-3 6-4
- 2026-09-14 T. Pereira vs R. Molleker selected=R. Molleker winner=Pereira T. status=lost basis=Challenger_results@2026-09-14:2-6 6-4 7-6
- 2026-09-14 U. Blanchet vs B. Harris selected=B. Harris winner=Blanchet U. status=lost basis=Challenger_results@2026-09-14:7-5 7-6
- 2026-09-15 A. Mayo vs R. Seggerman selected=A. Mayo winner=Seggerman R. status=lost basis=Challenger_results@2026-09-15:1-6 6-4 6-4
- 2026-09-15 B. Cengiz vs G. Maristany selected=G. Maristany winner=G. Maristany status=won basis=Forebet_results@2026-09-15:5-7 5-7
- 2026-09-15 B. N. Nakashima vs A. Ilagan selected=A. Ilagan winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-14 Boniel N. vs Adam-Gedge Z. 6-3 6-2 [Challenger_results] (Boniel N. side matches, other side unresolvable) ;; 2026-09-14 Boutleux N. vs Ngijol Carre P. 6-4 7-6 [Challenger_results] (Boutleux N. side matches, other side unresolvable)
- 2026-09-15 M. Sharipov vs T. J. Fancutt selected=M. Sharipov winner=M. Sharipov status=won basis=Forebet_results@2026-09-15:6-2 6-3
- 2026-09-15 P. Basile vs J. C. Martin Manzano selected=P. Basile winner=J. C. Martin Manzano status=lost basis=Forebet_results@2026-09-15:2-6 1-6
- 2026-09-15 P. Basile vs J. Reis Da Silva selected=P. Basile winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-15 Zabkova K. vs Pitchford B. 6-4 6-4 [Challenger_results] (Pitchford B. side matches, other side unresolvable) ;; 2026-09-15 Martin Manzano J. vs Basile P. 6-2 6-1 [Challenger_results] (Basile P. side matches, other side unresolvable)
- 2026-09-16 Brancaccio / Erjavec vs Burillo / Fossa Huergo selected=Brancaccio / Erjavec winner=? status=pending_no_result reason=no matching result rows found | near-miss candidates: 2026-09-16 Burillo Escorihuela I. / Fossa Huergo N. vs Brancaccio N. / Erjavec V. 4-6 6-3 10-5 [Challenger_results] (Brancaccio N. / Erjavec V. side matches, other side unresolvable) ;; 2026-09-16 Burillo Escorihuela I. / Fossa Huergo N. vs Brancaccio N. / Erjavec V. 4-6 6-3 10-5 [Challenger_results] (Brancaccio N. / Erjavec V. side matches, other side unresolvable)
- 2026-09-16 Cavalle-Reimers / Salden vs Kobori / Plipuech selected=Cavalle-Reimers / Salden winner=Kobori M. / Plipuech P. status=lost basis=Challenger_results@2026-09-16:6-3 6-4
- 2026-09-16 Jennifer Ruggeri vs Despina Papamichail selected=Jennifer Ruggeri winner=Papamichail D. status=lost basis=Challenger_results@2026-09-16:6-1 6-7 7-5
- 2026-09-16 Mikulskyte / Smith vs Strakhova / Tikhonova selected=Strakhova / Tikhonova winner=Strakhova V. / Tikhonova A. status=won basis=Challenger_results@2026-09-17:7-6 7-6 dup_merged=[2026-09-17 SKIPPED_VETO (won)]
- 2026-09-16 Noemi Basiletti vs Julie Struplova selected=Noemi Basiletti winner=Basiletti N. status=won basis=Challenger_results@2026-09-16:6-2 6-3
- 2026-09-16 Palicova / Struplova vs Novak / Sebestova selected=Palicova / Struplova winner=Novak K. / Sebestova I. status=lost basis=Challenger_results@2026-09-16:6-2 5-7 10-5
- 2026-09-16 Quevedo / Salkova vs Riera / Sierra selected=Quevedo / Salkova winner=Quevedo K. / Salkova D. status=won basis=Challenger_results@2026-09-17:6-3 6-7 10-6
- 2026-09-17 Elina Avanesyan vs Alina Charaeva selected=Elina Avanesyan winner=Charaeva A. status=lost basis=Challenger_results@2026-09-17:6-2 6-3
- 2026-09-17 F. Jorge vs V. Jimenez Kasintseva selected=F. Jorge winner=Jorge F. status=won basis=Challenger_results@2026-09-17:6-2 4-6 6-4
- 2026-09-17 Romero Gormaz / Selekhmeteva vs Christie / Silva selected=Christie / Silva winner=Romero Gormaz L. / Selekhmeteva O. status=lost basis=Challenger_results@2026-09-17:6-2 4-6 10-8
- 2026-09-17 Samira De Stefano vs Alice Tubello selected=Samira De Stefano winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-18 A. Shevchenko vs M. Jones selected=M. Jones winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-18 B. Tomic vs A. Binda selected=B. Tomic winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-18 L. Neumayer vs Z. Bergs selected=Z. Bergs winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-18 M. Bobichon vs T. Schoolkate selected=T. Schoolkate winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-18 M. Stoiana vs P. Badosa selected=P. Badosa winner=? status=pending_no_result reason=result not final: live/in-progress markers present

## By Tour

- `CHALLENGER`: total=50, settled=40, wins=19, hit_rate=0.475, ROI=-0.214054, pending=10
- `WTA`: total=12, settled=8, wins=3, hit_rate=0.375, ROI=-0.446667, pending=4
- `ATP`: total=2, settled=0, wins=0, hit_rate=None, ROI=None, pending=2

## By Series

- `Challenger`: total=50, settled=40, wins=19, hit_rate=0.475, ROI=-0.214054, pending=10
- `International`: total=12, settled=8, wins=3, hit_rate=0.375, ROI=-0.446667, pending=4
- `ATP250`: total=2, settled=0, wins=0, hit_rate=None, ROI=None, pending=2

## By Surface

- `Hard`: total=64, settled=48, wins=22, hit_rate=0.458333, ROI=-0.246512, pending=16

## By Bucket

- `SKIPPED_DEAD_EDGE`: total=32, settled=25, wins=15, hit_rate=0.6, ROI=-0.1428, pending=7
- `SKIPPED_VETO`: total=9, settled=3, wins=1, hit_rate=0.333333, ROI=-0.273333, pending=6
- `WATCHLIST`: total=16, settled=15, wins=4, hit_rate=0.266667, ROI=-0.414, pending=1
- `WATCHLIST_NO_ODDS`: total=7, settled=5, wins=2, hit_rate=0.4, ROI=None, pending=2

## By Source

- `BetClan`: total=9, settled=7, wins=3, hit_rate=0.428571, ROI=-0.336, pending=2
- `BetClan, Forebet`: total=1, settled=1, wins=0, hit_rate=0.0, ROI=-1.0, pending=0
- `Forebet`: total=54, settled=40, wins=19, hit_rate=0.475, ROI=-0.214054, pending=14
