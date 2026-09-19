# Racket Factory — Recent picks audit (2026-09-13 to 2026-09-19)

## Overall

- archived pick rows: 131
- archived pick dates: 7
- ledger pick rows (in window): 132
- duplicate rows merged (same match + selection re-picked): 1
- stale history rows pruned (pick no longer in ledger): 9
- settled picks: 56
- wins: 29
- hit rate: 0.517857
- priced picks: 48
- ROI: -0.173958
- ROI (real-priced): -0.202609 (n=46)
- ROI (paper-priced): 0.485 (n=2)
- pending picks: 74
- void picks: 0
- conflict picks: 1
- total picks: 131
- set diagnostic picks: 56
- selected won any set: 42 (0.75)
- selected won set 1: 30 (0.535714)
- selected won set 2: 30 (0.535714)
- selected won set 3: 11 (0.458333)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-19
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Ledger reconciliation

- ledger rows in window (official): 132
- duplicate rows merged (same match + selection in multiple daily ledgers): 1
- stale history rows pruned (pick no longer in any archived ledger): 9
- audited rows: 131
- identity check: ledger rows - duplicate rows merged = audited rows; By-* tables cover every pick (total = settled + pending + void + conflict)
- unresolved rows by reason:
  - pending_no_result: result not final: live/in-progress markers present: 64
  - pending_no_result: no matching result rows found: 10
  - conflict: conflicting final results: Challenger_results@2026-09-18:Badosa P.(6-4 6-2); Forebet_results@2026-09-18:Badosa G. P.(4-6 2-6): 1

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
- 2026-09-17 Samira De Stefano vs Alice Tubello selected=Samira De Stefano winner=De Stefano S. status=won basis=Challenger_results@2026-09-18:4-6 7-6 6-4
- 2026-09-18 F. Auger-Aliassime vs Q. Halys selected=F. Auger-Aliassime winner=F. Auger-Aliassime status=won basis=Forebet_results@2026-09-18:7-6 4-6 6-3
- 2026-09-18 M. Bobichon vs T. Schoolkate selected=T. Schoolkate winner=Schoolkate T. status=won basis=Challenger_results@2026-09-18:6-2 6-1
- 2026-09-18 M. Stoiana vs P. Badosa selected=P. Badosa winner=? status=conflict reason=conflicting final results: Challenger_results@2026-09-18:Badosa P.(6-4 6-2); Forebet_results@2026-09-18:Badosa G. P.(4-6 2-6)
- 2026-09-19 A. Dougaz vs M. Murtaza selected=A. Dougaz winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 A. Fery vs A. Guillen Meza selected=A. Fery winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 A. Herrero Linana vs Y. Kabbaj selected=A. Herrero Linana winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 A. Ibragimova vs R. Saigo selected=A. Ibragimova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 A. Moro Canas vs J. Clarke selected=A. Moro Canas winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 A. Shevchenko vs K. Samrej selected=K. Samrej winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 A. Tabilo vs D. Merida Aguilar selected=A. Tabilo winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 A. Zverev vs M. Dodig selected=A. Zverev winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 B. Artnak vs O. Kimhi selected=B. Artnak winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 C. A. Caniato vs A. Barrena selected=A. Barrena winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 C. Burel vs L. S. Steur J. selected=L. S. Steur J. winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 C. Cruz vs J. Faria selected=J. Faria winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 C. Garin vs R. Jodar selected=R. Jodar winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 C. Zhao vs Y. Ma selected=C. Zhao winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Cascino / Feng vs Kucmova / Laboutkova selected=Kucmova / Laboutkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Clara Burel vs Joelle Lilly Sophie Steur selected=Clara Burel winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Cristina Bucsa vs Iva Jovic selected=Iva Jovic winner=Jovic I. status=won basis=Challenger_results@2026-09-19:6-4 6-1
- 2026-09-19 D. Altmaier vs D. Prizmic selected=D. Altmaier winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 D. Duran vs N. Borges selected=D. Duran winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 D. Phillips vs A. Magadan selected=D. Phillips winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Da Silva Fick / Kulambayeva vs Bhosale / Micic selected=Da Silva Fick / Kulambayeva winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 E. M. Desvignes vs K. Okamura selected=E. M. Desvignes winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 E. S. Liang vs S. Saito selected=E. S. Liang winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 F. Auger-Aliassime vs A. Rinderknech selected=F. Auger-Aliassime winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 F. J. Planinsek vs A. Vales selected=A. Vales winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 F. Roncadelli vs M. Molder selected=F. Roncadelli winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 G. Trismuwantara vs R. M. Papoe selected=R. M. Papoe winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 H. Chung vs D. Suresh selected=D. Suresh winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 H. Kaji vs D. Astakhova selected=D. Astakhova winner=Astakhova D. status=won basis=Challenger_results@2026-09-19:6-2 6-7 6-2
- 2026-09-19 J. Garland vs E. Perez selected=J. Garland winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 J.J. Wolf vs Sebastian Gorzny selected=J.J. Wolf winner=Wolf J. status=won basis=Challenger_results@2026-09-19:7-6 1-6 7-5
- 2026-09-19 J. Lehecka vs L. Tien selected=J. Lehecka winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 J. Mensik vs B. Shelton selected=B. Shelton winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 J. Rodionov vs Z. Bergs selected=J. Rodionov winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 J. Wang vs A. Kulikova selected=A. Kulikova winner=Kulikova A. status=won basis=Challenger_results@2026-09-19:6-4 7-5
- 2026-09-19 Jakupovic / Karamoko vs Romero Gormaz / Selekhmeteva selected=Jakupovic / Karamoko winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 K. Bennani vs C. Abua selected=K. Bennani winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 K. Montsi vs D. Dzumhur selected=D. Dzumhur winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Kaitlin Quevedo vs Anna Blinkova selected=Anna Blinkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 L. Neumayer vs G. Onclin selected=G. Onclin winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 L. Pigato vs J. Vandromme selected=J. Vandromme winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 M. Barthel vs N. Basiletti selected=M. Barthel winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 M. Echargui vs M. Shoaib selected=M. Echargui winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 M. Efstathiou vs O. Krutykh selected=O. Krutykh winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 M. Pohankova vs G. Knutson selected=M. Pohankova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 M. Yamaguchi vs U. Eikeri selected=M. Yamaguchi winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Marat Sharipov vs Petr Bar Biryukov selected=Marat Sharipov winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 N. Hardt vs A. Elsayed selected=N. Hardt winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 N. Podoroska vs P. Badosa selected=P. Badosa winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 N. Radisic vs V. Morvayova selected=V. Morvayova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Nikolas Sanchez Izquierdo vs Luka Mikrut selected=Luka Mikrut winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 P. Henning vs A. Nedic selected=A. Nedic winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 P. Photiades vs G. Kravchenko selected=P. Photiades winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Pavel Kotov vs Bernard Tomic selected=Pavel Kotov winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 R. Cid Subervi vs K. Mabrouk selected=R. Cid Subervi winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 R. Da Costa vs C. Cretu selected=C. Cretu winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 R. Mallory vs A. Hernandez selected=A. Hernandez winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 S. De Stefano vs L. Samsonova selected=S. De Stefano winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 S. Kwon vs S. Nagal selected=S. Kwon winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Samira De Stefano vs Laura Samson selected=Laura Samson winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Sascha Gueymard Wayenburg vs Titouan Droguet selected=Titouan Droguet winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Stoiana / Valdmannova vs Dang / You selected=Stoiana / Valdmannova winner=Dang Y. / You X. status=lost basis=Challenger_results@2026-09-19:4-6 6-3 10-7
- 2026-09-19 T. Samuel vs A. Andrade selected=T. Samuel winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 T. Skatov vs M. Jones selected=M. Jones winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 T. Zidansek vs B. Jeong selected=T. Zidansek winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Thiago Monteiro vs Marco Trungelliti selected=Marco Trungelliti winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Tristan Schoolkate vs Harold Mayot selected=Tristan Schoolkate winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 X. Yao vs P. Hon selected=P. Hon winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-19 Y. Dlimi vs D. Adeleye selected=Y. Dlimi winner=? status=pending_no_result reason=result not final: live/in-progress markers present

## By Tour

- `ATP`: total=35, settled=1, wins=1, hit_rate=1.0, ROI=0.25, pending=34
- `CHALLENGER`: total=64, settled=42, wins=21, hit_rate=0.5, ROI=-0.202895, pending=22
- `WTA`: total=32, settled=13, wins=7, hit_rate=0.538462, ROI=-0.098889, pending=18, conflict=1

## By Series

- `ATP250`: total=35, settled=1, wins=1, hit_rate=1.0, ROI=0.25, pending=34
- `Challenger`: total=64, settled=42, wins=21, hit_rate=0.5, ROI=-0.202895, pending=22
- `International`: total=32, settled=13, wins=7, hit_rate=0.538462, ROI=-0.098889, pending=18, conflict=1

## By Surface

- `Grass`: total=7, settled=1, wins=1, hit_rate=1.0, ROI=None, pending=6
- `Hard`: total=124, settled=55, wins=28, hit_rate=0.509091, ROI=-0.173958, pending=68, conflict=1

## By Bucket

- `SKIPPED_DEAD_EDGE`: total=78, settled=28, wins=18, hit_rate=0.642857, ROI=-0.083929, pending=49, conflict=1
- `SKIPPED_VETO`: total=12, settled=5, wins=3, hit_rate=0.6, ROI=0.042, pending=7
- `WATCHLIST`: total=23, settled=15, wins=4, hit_rate=0.266667, ROI=-0.414, pending=8
- `WATCHLIST_NO_ODDS`: total=18, settled=8, wins=4, hit_rate=0.5, ROI=None, pending=10

## By Source

- `BetClan`: total=17, settled=11, wins=6, hit_rate=0.545455, ROI=-0.143333, pending=6
- `BetClan, Forebet`: total=8, settled=1, wins=0, hit_rate=0.0, ROI=-1.0, pending=7
- `Forebet`: total=106, settled=44, wins=23, hit_rate=0.522727, ROI=-0.158293, pending=61, conflict=1
