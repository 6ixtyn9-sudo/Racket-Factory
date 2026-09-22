# Racket Factory — Recent picks audit (2026-09-20 to 2026-09-23)

## Overall

- current regime: genesis-2026-09-20 (top-level stats are scoped to this regime; per-regime split in By Regime)
- archived pick rows: 93
- archived pick dates: 4
- ledger pick rows (in window): 93
- duplicate rows merged (same match + selection re-picked): 0
- stale history rows pruned (pick no longer in ledger): 0
- settled picks: 68
- wins: 51
- hit rate: 0.75
- priced picks: 25
- ROI: -0.0768
- ROI (real-priced): -0.0768 (n=25)
- ROI (paper-priced): None (n=0)
- pending picks: 25
- void picks: 0
- conflict picks: 0
- total picks: 93
- set diagnostic picks: 68
- selected won any set: 58 (0.852941)
- selected won set 1: 45 (0.661765)
- selected won set 2: 49 (0.720588)
- selected won set 3: 14 (0.636364)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-23
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Ledger reconciliation

- ledger rows in window (official): 93
- duplicate rows merged (same match + selection in multiple daily ledgers): 0
- stale history rows pruned (pick no longer in any archived ledger): 0
- audited rows: 93
- identity check: ledger rows - duplicate rows merged = audited rows; By-* tables cover every pick (total = settled + pending + void + conflict)
- unresolved rows by reason:
  - pending_no_result: result not final: live/in-progress markers present: 23
  - pending_no_result: result not final: score not final: incomplete final set 1-1: 1
  - pending_no_result: result not final: score not final: incomplete final set 5-2: 1

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
- 2026-09-21 Christie / Silva vs Ayukawa / Webley-Smith selected=Christie / Silva winner=Ayukawa M. / Webley-Smith E. status=lost basis=Challenger_results@2026-09-21:6-3 6-3
- 2026-09-21 Daria Egorova vs Melisa Ercan selected=Daria Egorova winner=Egorova D. status=won basis=Challenger_results@2026-09-21:7-5 6-1
- 2026-09-21 David Goffin vs Mark Lajal selected=Mark Lajal winner=Lajal M. status=won basis=Challenger_results@2026-09-21:6-3 6-4
- 2026-09-21 Ferro / Fruhvirtova vs Tang / Xu selected=Tang / Xu winner=Tang Q. / Xu Y. status=won basis=Challenger_results@2026-09-21:6-4 6-2
- 2026-09-21 Guiomar Maristany Zuleta De Reales vs Despina Papamichail selected=Guiomar Maristany Zuleta De Reales winner=Maristany Zuleta De Reales G. status=won basis=Challenger_results@2026-09-21:6-3 6-3
- 2026-09-21 Hodzic / Malygina vs Jorge / Jorge selected=Jorge / Jorge winner=Jorge F. / Jorge M. status=won basis=Challenger_results@2026-09-21:6-1 3-6 14-12
- 2026-09-21 Katie Volynets vs Elvina Kalieva selected=Katie Volynets winner=Volynets K. status=won basis=Challenger_results@2026-09-21:6-3 6-0
- 2026-09-21 Kato / Perez vs Sakkari / Vekic selected=Kato / Perez winner=Kato M. / Perez E. status=won basis=Challenger_results@2026-09-21:7-5 6-1
- 2026-09-21 Kristina Mladenovic vs Nao Hibino selected=Nao Hibino winner=Hibino N. status=won basis=Challenger_results@2026-09-21:7-5 3-6 6-4
- 2026-09-21 Ksenia Zaytseva vs Ilay Yoruk selected=Ksenia Zaytseva winner=Yoruk I. status=lost basis=Challenger_results@2026-09-21:2-6 6-4 7-5
- 2026-09-21 Kylie Collins vs Valentina Steiner selected=Kylie Collins winner=Steiner V. status=lost basis=Challenger_results@2026-09-21:6-4 1-6 6-4
- 2026-09-21 Laura Samson vs Noemi Basiletti selected=Laura Samson winner=Basiletti N. status=lost basis=Challenger_results@2026-09-21:6-3 6-4
- 2026-09-21 Lee / Park vs Lee / Ye selected=Lee / Ye winner=Lee Y. / Ye Q. status=won basis=Challenger_results@2026-09-21:6-3 7-5
- 2026-09-21 Lisa Zaar vs Enola Chiesa selected=Lisa Zaar winner=Zaar L. status=won basis=Challenger_results@2026-09-21:3-6 6-3 7-6
- 2026-09-21 Lizette Cabrera vs Sinja Kraus selected=Sinja Kraus winner=Kraus S. status=won basis=Challenger_results@2026-09-21:6-1 6-4
- 2026-09-21 Marie Vogt vs Ekaterina Yashina selected=Marie Vogt winner=Vogt M. status=won basis=Challenger_results@2026-09-21:6-4 6-2
- 2026-09-21 Xinxin Yao vs Maya Joint selected=Maya Joint winner=Joint M. status=won basis=Challenger_results@2026-09-21:2-6 7-6 6-3
- 2026-09-21 Yue Yuan vs Dayeon Back selected=Yue Yuan winner=Back D. status=lost basis=Challenger_results@2026-09-21:6-3 4-6 6-1
- 2026-09-22 Alice Tubello vs Dalila Spiteri selected=Alice Tubello winner=Tubello A. status=won basis=Challenger_results@2026-09-22:6-2 6-2
- 2026-09-22 Anastasia Tikhonova vs Anastasia Gasanova selected=Anastasia Gasanova winner=Gasanova A. status=won basis=Challenger_results@2026-09-22:6-2 6-4
- 2026-09-22 Chiesa / Tubello vs Allen / Leon selected=Chiesa / Tubello winner=Chiesa E. / Tubello A. status=won basis=Challenger_results@2026-09-22:6-3 6-4
- 2026-09-22 Daniil Glinka vs Daniel Jade selected=Daniil Glinka winner=Glinka D. status=won basis=Challenger_results@2026-09-22:6-3 7-5
- 2026-09-22 David Jorda Sanchis vs Harry Wendelken selected=Harry Wendelken winner=Jorda Sanchis D. status=lost basis=Challenger_results@2026-09-22:6-2 6-4
- 2026-09-22 Dino Prizmic vs Daniel Rincon selected=Dino Prizmic winner=Prizmic D. status=won basis=Challenger_results@2026-09-22:6-4 6-3
- 2026-09-22 Elena Micic vs Julia Riera selected=Julia Riera winner=Micic E. status=lost basis=Challenger_results@2026-09-22:6-1 6-3
- 2026-09-22 Elena Pridankina vs Daria Egorova selected=Elena Pridankina winner=Pridankina E. status=won basis=Challenger_results@2026-09-22:7-6 1-6 7-5
- 2026-09-22 Fiona Crawley vs Carole Monnet selected=Fiona Crawley winner=Monnet C. status=lost basis=Challenger_results@2026-09-22:6-1 7-6
- 2026-09-22 Francisca Jorge vs Mia Pohankova selected=Mia Pohankova winner=Pohankova M. status=won basis=Challenger_results@2026-09-22:6-2 6-1
- 2026-09-22 Grigor Dimitrov vs Edas Butvilas selected=Grigor Dimitrov winner=Butvilas E. status=lost basis=Challenger_results@2026-09-22:7-6 6-3
- 2026-09-22 Harold Mayot vs Justin Boulais selected=Harold Mayot winner=Mayot H. status=won basis=Challenger_results@2026-09-22:6-4 2-6 7-6
- 2026-09-22 Hunter / Krawczyk vs Ninomiya / Radisic selected=Hunter / Krawczyk winner=Hunter S. / Krawczyk D. status=won basis=Challenger_results@2026-09-22:7-6 7-6
- 2026-09-22 Jennifer Ruggeri vs Julia Grabher selected=Julia Grabher winner=Grabher J. status=won basis=Challenger_results@2026-09-22:4-6 7-5 6-3
- 2026-09-22 Julie Struplova vs Samira De Stefano selected=Samira De Stefano winner=De Stefano S. status=won basis=Challenger_results@2026-09-22:6-0 6-2
- 2026-09-22 Katarina Kuzmova vs Cagla Buyukakcay selected=Katarina Kuzmova winner=Buyukakcay C. status=lost basis=Challenger_results@2026-09-22:2-6 6-4 6-4
- 2026-09-22 Katherine Sebov vs Gabriela Knutson selected=Gabriela Knutson winner=Knutson G. status=won basis=Challenger_results@2026-09-22:6-4 6-2
- 2026-09-22 Kucmova / Laboutkova vs Basiletti / Urgesi selected=Kucmova / Laboutkova winner=Kucmova A. / Laboutkova A. status=won basis=Challenger_results@2026-09-22:6-4 6-3
- 2026-09-22 Lisa Zaar vs Rebeka Masarova selected=Rebeka Masarova winner=Masarova R. status=won basis=Challenger_results@2026-09-22:5-7 6-1 7-5
- 2026-09-22 Maria Sakkari vs Linda Fruhvirtova selected=Maria Sakkari winner=Sakkari M. status=won basis=Challenger_results@2026-09-22:6-4 6-1
- 2026-09-22 Maria Timofeeva vs Sofia Johnson selected=Maria Timofeeva winner=Timofeeva M. status=won basis=Challenger_results@2026-09-22:6-4 6-2
- 2026-09-22 Martyna Kubka vs Berfu Cengiz selected=Martyna Kubka winner=Cengiz B. status=lost basis=Challenger_results@2026-09-22:6-2 6-4
- 2026-09-22 Matilde Jorge vs Celia Cervino Ruiz selected=Matilde Jorge winner=Jorge M. status=won basis=Challenger_results@2026-09-22:6-3 6-4
- 2026-09-22 Matteo Martineau vs Tristan Schoolkate selected=Tristan Schoolkate winner=Martineau M. status=lost basis=Challenger_results@2026-09-22:7-6 6-7 7-6
- 2026-09-22 Pridankina / Tikhonova vs Drazic / Zaytseva selected=Pridankina / Tikhonova winner=Pridankina E. / Tikhonova A. status=won basis=Challenger_results@2026-09-22:6-7 7-6 10-7
- 2026-09-22 Remy Bertola vs Jesper De Jong selected=Jesper De Jong winner=De Jong J. status=won basis=Challenger_results@2026-09-22:7-6 7-6
- 2026-09-22 Sahaja Yamalapalli vs Katie Swan selected=Katie Swan winner=Swan K. status=won basis=Challenger_results@2026-09-22:6-1 6-0
- 2026-09-22 Solana Sierra vs Deniz Dilek selected=Solana Sierra winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-22 Teodora Kostovic vs Carolyn Ansari selected=Teodora Kostovic winner=Kostovic T. status=won basis=Challenger_results@2026-09-22:6-3 6-1
- 2026-09-22 Titouan Droguet vs Karl Poling selected=Titouan Droguet winner=Droguet T. status=won basis=Challenger_results@2026-09-22:6-7 6-4 7-6
- 2026-09-22 Vadym Ursu vs Borna Gojo selected=Borna Gojo winner=Gojo B. status=won basis=Challenger_results@2026-09-22:7-5 6-3
- 2026-09-22 Valentina Steiner vs Caroline Werner selected=Caroline Werner winner=Steiner V. status=lost basis=Challenger_results@2026-09-22:6-2 7-5
- 2026-09-22 Veronika Erjavec vs Katerina Tsygourova selected=Veronika Erjavec winner=Erjavec V. status=won basis=Challenger_results@2026-09-22:4-6 6-3 6-4
- 2026-09-22 Victoria Jimenez Kasintseva vs Naiktha Bains selected=Victoria Jimenez Kasintseva winner=? status=pending_no_result reason=result not final: score not final: incomplete final set 5-2
- 2026-09-22 Vivian Wolff vs Oleksandra Oliynykova selected=Oleksandra Oliynykova winner=Oliynykova O. status=won basis=Challenger_results@2026-09-22:6-1 7-6
- 2026-09-22 Weronika Falkowska vs Ilay Yoruk selected=Weronika Falkowska winner=Falkowska W. status=won basis=Challenger_results@2026-09-22:6-2 6-0
- 2026-09-23 Aleksandar Vukic vs Kyrian Jacquet selected=Aleksandar Vukic winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Alina Korneeva vs Darya Astakhova selected=Alina Korneeva winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Alycia Parks vs Leylah Fernandez selected=Leylah Fernandez winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Ansari / Shinikova vs Jakupovic / Karamoko selected=Jakupovic / Karamoko winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Arslan / Ulueren vs Crawley / Daniel selected=Crawley / Daniel winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Berfu Cengiz vs Aysegul Mert selected=Berfu Cengiz winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Dayeon Back vs Maya Joint selected=Maya Joint winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Detiuc / Khromacheva vs Preston / Tararudee selected=Detiuc / Khromacheva winner=Detiuc A. / Khromacheva I. status=won basis=Challenger_results@2026-09-22:4-6 6-4 10-4
- 2026-09-23 Dilek / Mert vs Haverlag / Kubka selected=Haverlag / Kubka winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Eikeri / Gleason vs Lansere / Prozorova selected=Eikeri / Gleason winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Elena Pridankina vs Ksenia Zaytseva selected=Elena Pridankina winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Garland / Hsieh vs Mihalikova / Nicholls selected=Mihalikova / Nicholls winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Lanlana Tararudee vs Sohyun Park selected=Lanlana Tararudee winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Lee / Ye vs Joint / Ruse selected=Joint / Ruse winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Mirra Andreeva vs Aliaksandra Sasnovich selected=Mirra Andreeva winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Sebastian Baez vs Jenson Brooksby selected=Jenson Brooksby winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Serban / Struplova vs Fossa Huergo / Herrero Linana selected=Fossa Huergo / Herrero Linana winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Talia Gibson vs Amanda Anisimova selected=Amanda Anisimova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Valentin Royer vs Adam Walton selected=Valentin Royer winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Weronika Falkowska vs Polina Iatcenko selected=Polina Iatcenko winner=? status=pending_no_result reason=result not final: live/in-progress markers present

## By Tour

- `CHALLENGER`: total=14, settled=14, wins=9, hit_rate=0.642857, ROI=-0.365, pending=0
- `WTA`: total=76, settled=54, wins=42, hit_rate=0.777778, ROI=0.014211, pending=22
- `ATP`: total=3, settled=0, wins=0, hit_rate=None, ROI=None, pending=3

## By Series

- `Challenger`: total=14, settled=14, wins=9, hit_rate=0.642857, ROI=-0.365, pending=0
- `International`: total=76, settled=54, wins=42, hit_rate=0.777778, ROI=0.014211, pending=22
- `ATP250`: total=3, settled=0, wins=0, hit_rate=None, ROI=None, pending=3

## By Surface

- `Grass`: total=16, settled=15, wins=10, hit_rate=0.666667, ROI=-0.277143, pending=1
- `Hard`: total=77, settled=53, wins=41, hit_rate=0.773585, ROI=0.001111, pending=24

## By Bucket

- `CERTIFIED_CLEAN`: total=6, settled=5, wins=3, hit_rate=0.6, ROI=-0.232, pending=1
- `SKIPPED_DEAD_EDGE`: total=16, settled=8, wins=6, hit_rate=0.75, ROI=0.00125, pending=8
- `SKIPPED_VETO`: total=13, settled=6, wins=5, hit_rate=0.833333, ROI=0.048333, pending=7
- `WATCHLIST`: total=10, settled=6, wins=3, hit_rate=0.5, ROI=-0.176667, pending=4
- `WATCHLIST_NO_ODDS`: total=48, settled=43, wins=34, hit_rate=0.790698, ROI=None, pending=5

## By Source

- `BetClan`: total=93, settled=68, wins=51, hit_rate=0.75, ROI=-0.0768, pending=25

## By Regime

- `genesis-2026-09-20`: total=93, settled=68, wins=51, hit_rate=0.75, ROI=-0.0768, pending=25
