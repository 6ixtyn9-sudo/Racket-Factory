# Racket Factory — Recent picks audit (2026-08-17 to 2026-09-15)

## Overall

- archived pick rows: 164
- archived pick dates: 3
- settled picks: 41
- wins: 19
- hit rate: 0.463415
- priced picks: 38
- ROI: -0.257105
- ROI (real-priced): -0.244722 (n=36)
- ROI (paper-priced): -0.48 (n=2)
- pending picks: 123
- void picks: 0
- conflict picks: 0
- total picks: 164
- set diagnostic picks: 41
- selected won any set: 27 (0.658537)
- selected won set 1: 19 (0.463415)
- selected won set 2: 22 (0.55)
- selected won set 3: 5 (0.384615)

## Settlement policy

- ledger kind: official
- include same-day picks: True
- same-day cutoff date: 2026-09-15
- same-day rows excluded: 0
- settlement date tolerance: exact pick date preferred, warehouse match_date +/- 1 day allowed
- settlement finality guard: live/suspended/to-finish rows are rejected
- settlement conflicts: two final rows naming different winners force conflict (pending, never a guess)
- walkovers settle VOID (stake returned); retirements settle to the advancer and are flagged
- ROI uses the archived pick price; paper-priced (scrape/estimated) legs are split out as ROI (paper)

## Per-pick audit (won/lost/pending)

- 2026-09-13 J. Von der Schulenburg vs R. Molleker selected=R. Molleker winner=? status=pending_no_result reason=no matching result rows found
- 2026-09-13 D. Martin vs A. Rybakov selected=A. Rybakov winner=Rybakov A. status=won basis=Challenger_results@2026-09-13:6-4 6-2
- 2026-09-13 D. E. Galan vs M. Vrbensky selected=D. E. Galan winner=? status=pending_no_result reason=no matching result rows found
- 2026-09-13 A. Parks vs M. Sherif selected=M. Sherif winner=? status=pending_no_result reason=no matching result rows found
- 2026-09-14 T. Legout vs D. Ostapenkov selected=D. Ostapenkov winner=Legout T. status=lost basis=Challenger_results@2026-09-14:6-3 6-4
- 2026-09-14 A. Gray vs P. Maloney selected=P. Maloney winner=Maloney P. status=won basis=Challenger_results@2026-09-14:6-3 5-7 7-6
- 2026-09-14 A. Marti Pujolras vs D. Sakellaridis selected=D. Sakellaridis winner=Marti Pujolras A. status=lost basis=Challenger_results@2026-09-14:6-1 6-1
- 2026-09-14 A. Shelbayh vs T. Zink selected=T. Zink winner=Shelbayh A. status=lost basis=Challenger_results@2026-09-14:6-4 3-6 6-3
- 2026-09-14 M. Schoenhaus vs P. Brady selected=P. Brady winner=Schoenhaus M. status=lost basis=Challenger_results@2026-09-14:4-6 6-3 6-1
- 2026-09-14 J. Boulais vs M. Bobichon selected=M. Bobichon winner=Bobichon M. status=won basis=Challenger_results@2026-09-14:7-6 6-3
- 2026-09-14 G. Oradini vs P. Basile selected=G. Oradini winner=Basile P. status=lost basis=Challenger_results@2026-09-14:6-4 7-6
- 2026-09-14 P. Brunclik vs F. Romano selected=F. Romano winner=Brunclik P. status=lost basis=Challenger_results@2026-09-14:7-6 7-6
- 2026-09-14 P. Lagutin vs M. Marterer selected=M. Marterer winner=Lagutin P. status=lost basis=Challenger_results@2026-09-14:6-2 6-2
- 2026-09-14 U. Blanchet vs B. Harris selected=B. Harris winner=Blanchet U. status=lost basis=Challenger_results@2026-09-14:7-5 7-6
- 2026-09-14 P. Zahraj vs M. Janvier selected=M. Janvier winner=Janvier M. status=won basis=Challenger_results@2026-09-14:6-4 6-4
- 2026-09-14 F. Pieczonka vs N. Mashtakov selected=N. Mashtakov winner=F. Pieczonka status=lost basis=Forebet_results@2026-09-14:6-3 6-2
- 2026-09-14 Q. Vandecasteele vs B. N. Nakashima selected=Q. Vandecasteele winner=B. N. Nakashima status=lost basis=Forebet_results@2026-09-14:2-6 4-6
- 2026-09-14 J. Nikles vs M. Cerny selected=J. Nikles winner=Nikles J. status=won basis=Challenger_results@2026-09-14:6-3 6-2
- 2026-09-14 I. Almazan Valiente vs G. Ferrari selected=G. Ferrari winner=Almazan Valiente I. status=lost basis=Challenger_results@2026-09-14:6-1 6-1
- 2026-09-14 L. Boskovic vs R. Serban selected=L. Boskovic winner=Boskovic L. status=won basis=Challenger_results@2026-09-14:6-7 7-6 6-2
- 2026-09-14 R. Seggerman vs A. Rybakov selected=R. Seggerman winner=Seggerman R. status=won basis=Challenger_results@2026-09-14:7-6 6-3
- 2026-09-14 M. Vrbensky vs K. Wehnelt selected=K. Wehnelt winner=Wehnelt K. status=won basis=Challenger_results@2026-09-14:7-5 6-2
- 2026-09-14 J. Clarke vs J. Forejtek selected=J. Clarke winner=Clarke J. status=won basis=Challenger_results@2026-09-14:3-6 6-3 6-4
- 2026-09-14 R. Strombachs vs S. Rodriguez Taverna selected=S. Rodriguez Taverna winner=Strombachs R. status=lost basis=Challenger_results@2026-09-14:6-0 6-2
- 2026-09-14 N. Slavic vs Y. Ghazouani Durand selected=Y. Ghazouani Durand winner=Ghazouani Durand Y. status=won basis=Challenger_results@2026-09-14:4-6 6-3 6-2
- 2026-09-14 J. C. Martin Manzano vs G. Campana Lee selected=G. Campana Lee winner=G. Campana Lee status=won basis=Forebet_results@2026-09-14:2-6 5-7
- 2026-09-14 D. Masur vs D. De Jonge selected=D. Masur winner=Masur D. status=won basis=Challenger_results@2026-09-14:7-6 4-6 7-6
- 2026-09-14 G. La Vela vs F. Iannaccone selected=F. Iannaccone winner=La Vela G. status=lost basis=Challenger_results@2026-09-14:7-5 2-6 7-6
- 2026-09-14 T. Pereira vs R. Molleker selected=R. Molleker winner=Pereira T. status=lost basis=Challenger_results@2026-09-14:2-6 6-4 7-6
- 2026-09-14 D. Blanch vs I. Gakhov selected=D. Blanch winner=Gakhov I. status=lost basis=Challenger_results@2026-09-14:6-2 1-6 6-2
- 2026-09-14 F. Bass vs K. De Schepper selected=K. De Schepper winner=Bass F. status=lost basis=Challenger_results@2026-09-14:7-6 3-6 6-3
- 2026-09-14 F. Diaz Acosta vs M. Alcala Gurri selected=F. Diaz Acosta winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-14 M. Petkovic vs F. Gill selected=F. Gill winner=Gill F. status=won basis=Challenger_results@2026-09-14:7-6 6-3
- 2026-09-14 A. Moro Canas vs G. Blancaneaux selected=A. Moro Canas winner=Moro Canas A. status=won basis=Challenger_results@2026-09-14:6-2 7-6
- 2026-09-14 O. Baris vs E. Zhu selected=O. Baris winner=Zhu E. status=lost basis=Challenger_results@2026-09-14:6-4 4-6 6-4
- 2026-09-14 E. Bennemann vs M. Bassols selected=M. Bassols winner=M. Bassols status=won basis=Forebet_results@2026-09-14:4-6 2-6
- 2026-09-14 T. Boyer vs E. Winter selected=T. Boyer winner=Boyer T. status=won basis=Challenger_results@2026-09-15:6-2 6-4
- 2026-09-14 L. Staeheli vs R. Pascual Ferra selected=R. Pascual Ferra winner=Staeheli L. status=lost basis=Challenger_results@2026-09-14:4-6 6-2 6-3
- 2026-09-14 S. Johnson vs E. Arutiunian selected=S. Johnson winner=Arutiunian E. status=lost basis=Challenger_results@2026-09-14:7-6 7-6
- 2026-09-14 J. Wolf vs L. E. Ambrogi selected=J. Wolf winner=J. Wolf status=won basis=Forebet_results@2026-09-14:6-2 6-0
- 2026-09-14 R. Zelnickova vs K. Najzer selected=R. Zelnickova winner=Zelnickova R. status=won basis=Challenger_results@2026-09-14:6-2 6-3
- 2026-09-14 L. S. Steur J. vs D. Spiteri selected=D. Spiteri winner=L. S. Steur J. status=lost basis=Forebet_results@2026-09-14:6-1 7-6
- 2026-09-15 M. Janvier vs S. Gueymard Wayenburg selected=M. Janvier winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Jazmin Ortenzi vs Chloe Paquet selected=Chloe Paquet winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 A. Marti Pujolras vs S. Napolitano selected=A. Marti Pujolras winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 A. Mintegi Del Olmo vs A. Geerlings Martinez selected=A. Mintegi Del Olmo winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 A. Weber vs K. Matsuda selected=K. Matsuda winner=Weber A. status=lost basis=Challenger_results@2026-09-15:6-1 6-2
- 2026-09-15 A. Ito vs G. Knutson selected=G. Knutson winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 P. Basile vs J. Reis Da Silva selected=P. Basile winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 K. Rinaldo Persson vs A. Voloshchuk selected=A. Voloshchuk winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Clement Tabur vs Harold Mayot selected=Harold Mayot winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Darja Semenistaja vs Hayu Kinoshita selected=Darja Semenistaja winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Tiago Pereira vs Filip Pieczonka selected=Tiago Pereira winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Matisse Bobichon vs Eliakim Coulibaly selected=Eliakim Coulibaly winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 M. Sharipov vs I. Ivashka selected=I. Ivashka winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 L. Romero Gormaz vs M. Trevisan selected=M. Trevisan winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 G. Cadenasso vs F. Forti selected=G. Cadenasso winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 P. Lagutin vs E. Dalla Valle selected=P. Lagutin winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Jesse Delaney vs Petr Bar Biryukov selected=Petr Bar Biryukov winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Marvin Moeller vs Daniel Michalski selected=Marvin Moeller winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 O. Selekhmeteva vs N. Schunk selected=N. Schunk winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 B. Ellis vs H. Moriya selected=B. Ellis winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Darwin Blanch vs Stefan Kozlov selected=Darwin Blanch winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Xiaodi You vs Alina Charaeva selected=Alina Charaeva winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 J. Ruggeri vs E. Vedder selected=J. Ruggeri winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Alexandr Binda vs Aoran Wang selected=Alexandr Binda winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Diane Parry vs Peyton Stearns selected=Diane Parry winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Dali Blanch vs Nikolas Sanchez Izquierdo selected=Nikolas Sanchez Izquierdo winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Lucrezia Stefanini vs Nadia Podoroska selected=Lucrezia Stefanini winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Madison Sieg vs Irene Burillo selected=Irene Burillo winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 L. S. Steur J. vs C. Esquiva Banuls selected=L. S. Steur J. winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Mitchell Krueger vs Keegan Smith selected=Keegan Smith winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 M. Brunold vs M. Giunta selected=M. Brunold winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Leo Raquillet vs Yanis Ghazouani Durand selected=Yanis Ghazouani Durand winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Lucija Ciric Bagaric vs Laura Samson selected=Laura Samson winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 I. Almazan Valiente vs F. Agamenone selected=I. Almazan Valiente winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Ristic / Rus vs Chiesa / Nagy selected=Chiesa / Nagy winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Ane Mintegi Del Olmo vs Ariana Geerlings selected=Ariana Geerlings winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Ayukawa / Webley-Smith vs Akli / Cross selected=Akli / Cross winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Mitsuki Wei Kang Leong vs Sergey Fomin selected=Sergey Fomin winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Feistel / Serban vs Drazic / Mattel selected=Feistel / Serban winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Kucmova / Laboutkova vs Morderger / Morderger selected=Kucmova / Laboutkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 N. Noha Akugue vs A. Akli selected=N. Noha Akugue winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Sloane Stephens vs Janice Tjen selected=Janice Tjen winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Anna Blinkova vs Carol Young Suh Lee selected=Anna Blinkova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Shintaro Mochizuki vs Pavel Kotov selected=Shintaro Mochizuki winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Luca Castelnuovo vs Omar Jasika selected=Omar Jasika winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 O. Roca Batalla vs A. Sanchez Quilez selected=O. Roca Batalla winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Vendula Valdmannova vs Laura Pigossi selected=Vendula Valdmannova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Berfu Cengiz vs Guiomar Maristany Zuleta De Reales selected=Guiomar Maristany Zuleta De Reales winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 G. Piraino vs S. Pieri selected=G. Piraino winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Vitaliy Sachko vs Filip Cristian Jianu selected=Vitaliy Sachko winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Clara Burel vs Aran Teixido Garcia selected=Clara Burel winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Anastasiia Sobolieva vs Lea Boskovic selected=Anastasiia Sobolieva winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 A. Barrena vs M. Chazal selected=A. Barrena winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 M. L. Carle vs E. Gorgodze selected=M. L. Carle winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 H. Barton vs R. Brancaccio selected=H. Barton winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Alycia Parks vs Sara Bejlek selected=Sara Bejlek winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 L. Havlickova vs A. Zantedeschi selected=L. Havlickova winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- 2026-09-15 Julia Riera vs Elina Avanesyan selected=Elina Avanesyan winner=? status=pending_no_result reason=result not final: live/in-progress markers present
- ... and 64 more

## By Tour

- `CHALLENGER`: settled=41, wins=19, hit_rate=0.463415, ROI=-0.257105

## By Series

- `Challenger`: settled=41, wins=19, hit_rate=0.463415, ROI=-0.257105

## By Surface

- `Hard`: settled=41, wins=19, hit_rate=0.463415, ROI=-0.257105

## By Bucket

- `SKIPPED_DEAD_EDGE`: settled=23, wins=14, hit_rate=0.608696, ROI=-0.154783
- `WATCHLIST`: settled=15, wins=4, hit_rate=0.266667, ROI=-0.414
- `WATCHLIST_NO_ODDS`: settled=3, wins=1, hit_rate=0.333333, ROI=None

## By Source

- `Forebet`: settled=41, wins=19, hit_rate=0.463415, ROI=-0.257105
