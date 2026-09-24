# Racket Factory — Recent picks audit (2026-09-20 to 2026-09-24)

## Overall

- current regime: genesis-2026-09-20 (top-level stats are scoped to this regime; per-regime split in By Regime)
- archived pick rows: 127
- archived pick dates: 5
- ledger pick rows (in window): 129
- duplicate rows merged (same match + selection re-picked): 2
- stale history rows pruned (pick no longer in ledger): 19
- settled picks: 111
- wins: 80
- hit rate: 0.720721
- priced picks: 48
- ROI: -0.16375
- ROI (real-priced): -0.16375 (n=48)
- ROI (paper-priced): None (n=0)
- pending picks: 15
- void picks: 1
- conflict picks: 0
- total picks: 127
- set diagnostic picks: 111
- selected won any set: 92 (0.828829)
- selected won set 1: 76 (0.684685)
- selected won set 2: 77 (0.693694)
- selected won set 3: 18 (0.580645)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-24
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Ledger reconciliation

- ledger rows in window (official): 129
- duplicate rows merged (same match + selection in multiple daily ledgers): 2
- stale history rows pruned (pick no longer in any archived ledger): 19
- audited rows: 127
- identity check: ledger rows - duplicate rows merged = audited rows; By-* tables cover every pick (total = settled + pending + void + conflict)
- unresolved rows by reason:
  - pending_no_result: result not final: live/in-progress markers present: 13
  - pending_no_result: result not final: score not final: incomplete final set 1-1: 1
  - pending_no_result: result not final: score not final: incomplete final set 5-2: 1
  - void: walkover: stake returned: 1

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
- 2026-09-22 Solana Sierra vs Deniz Dilek selected=Solana Sierra winner=Dilek D. status=lost basis=Challenger_results@2026-09-23:6-3 6-3
- 2026-09-22 Teodora Kostovic vs Carolyn Ansari selected=Teodora Kostovic winner=Kostovic T. status=won basis=Challenger_results@2026-09-22:6-3 6-1
- 2026-09-22 Titouan Droguet vs Karl Poling selected=Titouan Droguet winner=Droguet T. status=won basis=Challenger_results@2026-09-22:6-7 6-4 7-6
- 2026-09-22 Vadym Ursu vs Borna Gojo selected=Borna Gojo winner=Gojo B. status=won basis=Challenger_results@2026-09-22:7-5 6-3
- 2026-09-22 Valentina Steiner vs Caroline Werner selected=Caroline Werner winner=Steiner V. status=lost basis=Challenger_results@2026-09-22:6-2 7-5
- 2026-09-22 Veronika Erjavec vs Katerina Tsygourova selected=Veronika Erjavec winner=Erjavec V. status=won basis=Challenger_results@2026-09-22:4-6 6-3 6-4
- 2026-09-22 Victoria Jimenez Kasintseva vs Naiktha Bains selected=Victoria Jimenez Kasintseva winner=? status=pending_no_result reason=result not final: score not final: incomplete final set 5-2
- 2026-09-22 Vivian Wolff vs Oleksandra Oliynykova selected=Oleksandra Oliynykova winner=Oliynykova O. status=won basis=Challenger_results@2026-09-22:6-1 7-6
- 2026-09-22 Weronika Falkowska vs Ilay Yoruk selected=Weronika Falkowska winner=Falkowska W. status=won basis=Challenger_results@2026-09-22:6-2 6-0
- 2026-09-23 Alexander Shevchenko vs Hubert Hurkacz selected=Hubert Hurkacz winner=Hurkacz H. status=won basis=Challenger_results@2026-09-24:6-3 7-6
- 2026-09-23 Ansari / Shinikova vs Jakupovic / Karamoko selected=Jakupovic / Karamoko winner=Jakupovic D. / Karamoko N. status=won basis=Challenger_results@2026-09-23:6-3 7-5
- 2026-09-23 Arslan / Ulueren vs Crawley / Daniel selected=Crawley / Daniel winner=Crawley F. / Daniel J. status=won basis=Challenger_results@2026-09-23:6-4 6-4
- 2026-09-23 Berfu Cengiz vs Aysegul Mert selected=Berfu Cengiz winner=Cengiz B. status=won basis=Challenger_results@2026-09-23:7-6 6-2
- 2026-09-23 Da Silva Fick / Mcgiffin vs Isakova / Pereira De Aguiar selected=Da Silva Fick / Mcgiffin winner=Da Silva Fick G. / McGiffin T. status=won basis=Challenger_results@2026-09-23:6-4 6-1
- 2026-09-23 Dane Sweeny vs Taro Daniel selected=Dane Sweeny winner=Daniel T. status=lost basis=Challenger_results@2026-09-24:7-6 6-2
- 2026-09-23 Detiuc / Khromacheva vs Preston / Tararudee selected=Detiuc / Khromacheva winner=Detiuc A. / Khromacheva I. status=won basis=Challenger_results@2026-09-22:4-6 6-4 10-4
- 2026-09-23 Dilek / Mert vs Haverlag / Kubka selected=Haverlag / Kubka winner=Haverlag I. / Kubka M. status=won basis=Challenger_results@2026-09-23:6-1 7-6
- 2026-09-23 Dino Prizmic vs David Jorda Sanchis selected=Dino Prizmic winner=Prizmic D. status=won basis=Challenger_results@2026-09-23:6-1 7-6
- 2026-09-23 Eikeri / Gleason vs Lansere / Prozorova selected=Eikeri / Gleason winner=Lansere S. / Prozorova T. status=lost basis=Challenger_results@2026-09-23:6-3 2-6 10-5
- 2026-09-23 Elena Pridankina vs Ksenia Zaytseva selected=Elena Pridankina winner=Zaytseva K. status=lost basis=Challenger_results@2026-09-23:2-6 7-6 6-1
- 2026-09-23 Fabian Marozsan vs Alex Bolt selected=Fabian Marozsan winner=Marozsan F. status=won basis=Challenger_results@2026-09-24:6-4 6-2
- 2026-09-23 Fajing Sun vs Roman Safiullin selected=Roman Safiullin winner=Safiullin R. status=won basis=Challenger_results@2026-09-24:6-2 6-0
- 2026-09-23 Franco Dias / Hinojosa Gomez vs Brantmeier / Collins selected=Brantmeier / Collins winner=Brantmeier R. / Collins K. status=won basis=Challenger_results@2026-09-23:6-3 6-0
- 2026-09-23 Garland / Hsieh vs Mihalikova / Nicholls selected=Mihalikova / Nicholls winner=Garland J. / Hsieh S. status=lost basis=Challenger_results@2026-09-23:6-2 7-6
- 2026-09-23 Guiomar Maristany Zuleta De Reales vs Marta Lombardini selected=Guiomar Maristany Zuleta De Reales winner=Maristany Zuleta De Reales G. status=won basis=Challenger_results@2026-09-23:6-2 6-3
- 2026-09-23 Isabella Maria Serban vs Rebeka Masarova selected=Rebeka Masarova winner=Masarova R. status=won basis=Challenger_results@2026-09-23:5-7 6-2 6-0
- 2026-09-23 Jaime Faria vs Terence Atmane selected=Terence Atmane winner=Faria J. status=lost basis=Challenger_results@2026-09-24:3-6 7-6 6-4
- 2026-09-23 James Duckworth vs Lorenzo Sonego selected=Lorenzo Sonego winner=Sonego L. status=won basis=Challenger_results@2026-09-24:6-4 6-4
- 2026-09-23 Jessica Pieri vs Leyre Romero Gormaz selected=Leyre Romero Gormaz winner=Pieri J. status=lost basis=Challenger_results@2026-09-23:3-6 6-4 6-1
- 2026-09-23 Jie Cui vs Adolfo Daniel Vallejo selected=Adolfo Daniel Vallejo winner=Vallejo A. status=won basis=Challenger_results@2026-09-24:6-1 6-7 6-4 dup_merged=[2026-09-24 WATCHLIST_NO_ODDS (won)]
- 2026-09-23 Juncheng Shang vs Adrian Mannarino selected=Juncheng Shang winner=Mannarino A. status=lost basis=Challenger_results@2026-09-24:7-6 6-2
- 2026-09-23 Lee / Ye vs Joint / Ruse selected=Joint / Ruse winner=Lee Y. / Ye Q. status=lost basis=Challenger_results@2026-09-23:1-6 6-3 14-12
- 2026-09-23 Lloyd Harris vs Aleksandar Kovacevic selected=Aleksandar Kovacevic winner=Harris L. status=lost basis=Challenger_results@2026-09-24:6-3 6-4
- 2026-09-23 Maria Timofeeva vs Kajsa Rinaldo Persson selected=Maria Timofeeva winner=Timofeeva M. status=won basis=Challenger_results@2026-09-23:6-3 6-0
- 2026-09-23 Mattia Bellucci vs Kamil Majchrzak selected=Kamil Majchrzak winner=Majchrzak K. status=won basis=Challenger_results@2026-09-24:6-3 6-4
- 2026-09-23 Miomir Kecmanovic vs Nikoloz Basilashvili selected=Miomir Kecmanovic winner=Basilashvili N. status=lost basis=Challenger_results@2026-09-24:7-6 6-4
- 2026-09-23 Nuno Borges vs Camilo Ugo Carabelli selected=Nuno Borges winner=? status=pending_no_result reason=result not final: live/in-progress markers present dup_merged=[2026-09-24 WATCHLIST_NO_ODDS (pending_no_result)]
- 2026-09-23 Rinky Hijikata vs Dalibor Svrcina selected=Dalibor Svrcina winner=Hijikata R. status=lost basis=Challenger_results@2026-09-24:7-6 6-4
- 2026-09-23 Serban / Struplova vs Fossa Huergo / Herrero Linana selected=Fossa Huergo / Herrero Linana winner=Fossa Huergo N. / Herrero Linana A. status=won basis=Challenger_results@2026-09-23:6-2 6-4
- 2026-09-23 Sho Shimabukuro vs Hugo Gaston selected=Sho Shimabukuro winner=Gaston H. status=lost basis=Challenger_results@2026-09-24:6-4 6-4
- 2026-09-23 Talia Gibson vs Amanda Anisimova selected=Amanda Anisimova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-23 Tallon Griekspoor vs Denis Shapovalov selected=Denis Shapovalov winner=Shapovalov D. status=won basis=Challenger_results@2026-09-24:6-4 7-6
- 2026-09-23 Thomas Faurel vs Mark Lajal selected=Mark Lajal winner=Lajal M. status=won basis=Challenger_results@2026-09-23:6-1 6-2
- 2026-09-23 Weronika Falkowska vs Polina Iatcenko selected=Polina Iatcenko winner=Iatcenko P. status=won basis=Challenger_results@2026-09-23:6-3 6-4
- 2026-09-24 Andrea Guerrieri vs Jesper De Jong selected=Jesper De Jong winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-24 Arends / Pel vs Stalder / Reyes-Varela selected=Arends / Pel winner=Arends S. / Pel D. status=won basis=Challenger_results@2026-09-24:6-4 6-4
- 2026-09-24 Chan / Wu vs Back / Jang selected=Chan / Wu winner=Chan H. / Wu F. status=won basis=Challenger_results@2026-09-24:6-1 6-2
- 2026-09-24 Ciric Bagaric / Pigossi vs Chiesa / Tubello selected=Ciric Bagaric / Pigossi winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-24 Ercan / Naito vs Monnet / Riera selected=Monnet / Riera winner=? status=void reason=walkover: stake returned
- 2026-09-24 Fabian Marozsan vs Taro Daniel selected=Fabian Marozsan winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-24 Jaime Faria vs Hugo Gaston selected=Jaime Faria winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-24 Kucmova / Laboutkova vs Morderger / Morderger selected=Kucmova / Laboutkova winner=Kucmova A. / Laboutkova A. status=won basis=Challenger_results@2026-09-24:6-1 6-2
- 2026-09-24 Kyrian Jacquet vs Tomas Martin Etcheverry selected=Tomas Martin Etcheverry winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-24 Maria Sakkari vs Nao Hibino selected=Maria Sakkari winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-24 Monnet / Riera vs Jakupovic / Karamoko selected=Jakupovic / Karamoko winner=Jakupovic D. / Karamoko N. status=won basis=Challenger_results@2026-09-24:6-4 2-6 10-8
- 2026-09-24 Naiktha Bains vs Reese Brantmeier selected=Reese Brantmeier winner=Brantmeier R. status=won basis=Challenger_results@2026-09-24:6-4 6-4
- 2026-09-24 Pridankina / Tikhonova vs Kostovic / Micic selected=Pridankina / Tikhonova winner=Kostovic T. / Micic E. status=lost basis=Challenger_results@2026-09-24:7-6 6-4
- 2026-09-24 Snells / Walker vs Moratelli / Ruggeri selected=Moratelli / Ruggeri winner=Moratelli A. / Ruggeri J. status=won basis=Challenger_results@2026-09-24:6-3 6-1
- 2026-09-24 Teodora Kostovic vs Elena Ruxandra Bertea selected=Teodora Kostovic winner=Kostovic T. status=won basis=Challenger_results@2026-09-24:6-3 6-1
- 2026-09-24 Valentina Steiner vs Katie Swan selected=Katie Swan winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-24 Veronika Podrez vs Mia Pohankova selected=Mia Pohankova winner=Pohankova M. status=won basis=Challenger_results@2026-09-24:6-3 6-4
- 2026-09-24 Yeonwoo Ku vs Elena-Gabriela Ruse selected=Elena-Gabriela Ruse winner=Ruse E. status=won basis=Challenger_results@2026-09-24:2-6 7-6 6-1
- 2026-09-24 Zhizhen Zhang vs Coleman Wong selected=Coleman Wong winner=? status=pending_no_result reason=result not final: live/in-progress markers present

## By Tour

- `ATP`: total=20, settled=15, wins=8, hit_rate=0.533333, ROI=-0.280714, pending=5
- `CHALLENGER`: total=17, settled=16, wins=11, hit_rate=0.6875, ROI=-0.365, pending=1
- `WTA`: total=90, settled=80, wins=61, hit_rate=0.7625, ROI=-0.062143, pending=9, void=1

## By Series

- `ATP250`: total=20, settled=15, wins=8, hit_rate=0.533333, ROI=-0.280714, pending=5
- `Challenger`: total=17, settled=16, wins=11, hit_rate=0.6875, ROI=-0.365, pending=1
- `International`: total=90, settled=80, wins=61, hit_rate=0.7625, ROI=-0.062143, pending=9, void=1

## By Surface

- `Grass`: total=18, settled=17, wins=12, hit_rate=0.705882, ROI=-0.277143, pending=1
- `Hard`: total=109, settled=94, wins=68, hit_rate=0.723404, ROI=-0.14439, pending=14, void=1

## By Bucket

- `CERTIFIED_CLEAN`: total=9, settled=8, wins=5, hit_rate=0.625, ROI=-0.1975, pending=1
- `SKIPPED_DEAD_EDGE`: total=21, settled=19, wins=11, hit_rate=0.578947, ROI=-0.229474, pending=2
- `SKIPPED_VETO`: total=17, settled=11, wins=10, hit_rate=0.909091, ROI=0.128182, pending=6
- `WATCHLIST`: total=11, settled=10, wins=4, hit_rate=0.4, ROI=-0.333, pending=1
- `WATCHLIST_NO_ODDS`: total=69, settled=63, wins=50, hit_rate=0.793651, ROI=None, pending=5, void=1

## By Source

- `BetClan`: total=108, settled=97, wins=73, hit_rate=0.752577, ROI=-0.115588, pending=10, void=1
- `PredixSport`: total=18, settled=14, wins=7, hit_rate=0.5, ROI=-0.280714, pending=4
- `BetClan, PredixSport`: total=1, settled=0, wins=0, hit_rate=None, ROI=None, pending=1

## By Regime

- `genesis-2026-09-20`: total=127, settled=111, wins=80, hit_rate=0.720721, ROI=-0.16375, pending=15
