ZERO HOUR EARTH v0.53.5
======================
Generátor reálnych máp z OpenStreetMap pre C&C Generals: Zero Hour.
Čo je nové: pozri CHANGELOG_SK.txt

SPUSTENIE
  1. Rozbaľ celý priečinok.
  2. Dvojklik na run.bat. Ak chýba Python, nainštaluje sa sám; potom spusti run.bat ešte raz.
  3. Na mape vyber oblasť (červený štvorec), v kartách vpravo nastav mapu.
  4. Klikni "VYGENEROVAŤ MAPU".
  5. Mapa sa uloží do Documents\Command and Conquer Generals Zero Hour Data\Maps\<názov>\
  6. Reštartuj hru a vyber mapu v skirmish.

  Šírku panela zmeníš potiahnutím deliacej lišty vedľa mapy.
  Hodnoty pri posuvníkoch môžeš napísať a potvrdiť Enterom. Pri ručných štartoch
  prerušované štvorce ukazujú priestor pre základne. Generátor môže štart posunúť
  na blízku suchú plochu.

  V bežnom skirmishi generátor pripraví vyrovnané štartovacie plochy
  pre AI a hľadá miesto pre zásoby s prístupom po suchej zemi.
  V Mesto → Pokročilé môžeš upraviť hustotu drobnej zástavby, náhradný
  počet poschodí bytoviek a skladanie výškových budov.

  V Režimy hry → Zombíci → Sústavne sa dá vpísať limit živých zombíkov
  v rozsahu 1–3000 (predvolene 180). Tempo a skladba hordy dosiahnu posledný stupeň
  vo fáze 16. Počítadlo fáz pokračuje aj po úrovni 40.

  V Zombie Survival a Defense použi po uplynutí času respawnu ikonu
  Respawn Ambush v ľavom paneli schopností. Klikni na voľné miesto v aréne
  a hneď sa tam objaví Humvee. Sólo Survival umožňuje návrat aj poslednému
  hráčovi. V kooperácii končí Survival po vyradení celého tímu z arény.

INVASION
  V Režimy hry zapni Invasion. Vyber útočiacu frakciu (China, GLA alebo USA).
  Predvolene máš 180 sekúnd na stavbu a $7 500 na hráča. Dá sa nastaviť tempo,
  veľkosť skupín, limit útočníkov, príjem aj letectvo. Po pechote prídu
  vozidlá, tanky, obliehanie a od úrovne 5 stíhačky USA alebo China.
  V Hráčoch zadaj počet obrancov + 1. V lobby daj všetkých ľudí do jedného tímu.
  Na posledný štart daj obyčajnú GLA AI do iného tímu. Tá spúšťa skripty mapy.
  Frakciu útočiacich jednotiek určuje nastavenie v generátore.
  Každý obranca začína s jedným staviteľom
  pri spoločnom veliteľstve. Osobné veliteľstvá sú mimo hracej plochy.
  Zápas končí zničením spoločného veliteľstva.

JEDEN .EXE SÚBOR NA ZDIEĽANIE: dvojklik na build_exe.bat. Vznikne dist\ZeroHourEarth.exe
  a ZeroHourEarth_v<verzia>_Windows.zip (exe + anglický návod), ktorý môžeš poslať ďalej.

ZDROJOVÝ KÓD: všetky súbory .py sú v tomto priečinku, popis v DEVELOPERS.txt (anglicky).
  Balík z build_exe.bat obsahuje okrem .exe aj priečinok "source".

Z príkazového riadku: python app.py --cli "Poltár" --km 1.5 --scale 1

ČO PROGRAM ROBÍ
  - budovy číta priamo z tvojho INIZH.big (správne rozmery, funguje aj s módmi)
  - cesty, budovy, vodu, lesy a detaily sťahuje z OpenStreetMap, terén z verejných výškových dlaždíc
  - typ budovy určí z OSM: obchod, krčma, škola či úrad v budove, dvor statku, fabrika, garáže
  - ploty, múry a živé ploty z OSM
  - štarty hráčov, zdroje a AI trasy pre botov
  - stiahnuté dáta sa ukladajú do ~/.cncearth/cache, opakované generovanie tej istej oblasti je rýchle

ZNÁME OBMEDZENIA
  - hra má na výšku len 256 krokov: pri mierke 1:1 max. cca 38 m; režim "Skutočné sklony" drží kopce 1:1 a zmenší len celkové stúpanie
  - hra vykreslí najviac 4000 stromov (limit enginu), rozdelia sa do všetkých lesov
  - hra nemá európsky kostol, používa sa náhrada (GermanTownHall)
  - poschodovanie budov, obrovské mapy a nepriateľská pevnosť sú experimentálne
  - zima mení len stromy, zem ostáva zelená
