ZERO HOUR EARTH v0.53.29
=======================
Real-world map generator for C&C Generals: Zero Hour.
Pick any place on Earth, and the program builds a playable skirmish map from
OpenStreetMap (roads, buildings, rivers, forests, details) and real elevation data.

HOW TO USE
  1. Extract the source folder and open run.bat. If you built the executable,
     you can also start ZeroHourEarth.exe.
  2. Search a place or drag the map, the red square is the map area.
  3. Set up the map in the tabs on the right (Area, Players, Landscape, Town, Enemy, Export).
  4. Click GENERATE MAP. An internet connection is needed.
  5. The map is saved to Documents\Command and Conquer Generals Zero Hour Data\Maps\<name>\
  6. Restart the game and pick the map in Skirmish.

  Drag the divider next to the map to change the settings panel width.
  To enter a value beside a slider, type it and press Enter. Dashed squares show
  the build space for manual starts. The generator may move a start to nearby dry land.

  Standard skirmish prepares level build pads for the AI and looks for supply
  docks with a dry approach. Town → Advanced controls small-building density,
  the fallback apartment floor count and vertical stacking.

  In Game modes → Zombies → Continuous, enter a cap of 1–3000 active zombies
  (default 180). The spawn rate and unit mix reach their final stage at phase 16.
  The phase counter keeps advancing beyond 40.

  In Zombie Survival and Defense, use Respawn Ambush in the left sidebar when
  the respawn timer ends. Click open ground in the arena to spawn a Humvee
  immediately. Solo Survival allows you to respawn. Co-op Survival ends if the
  whole team is out of the arena.

INVASION
  Enable Invasion in Game modes and select China, GLA or USA as the attackers.
  The default build period is 180 seconds with $7,500 per defender. Configure
  the pace, squad size, attacker cap, income and aircraft. Infantry are followed
  by vehicles, tanks, siege units and USA or China fighters from level 5.
  Set Players to defenders + 1. In the lobby put all humans on one team.
  Put a base GLA AI on the last start on a different team to run the scripts.
  The generator setting selects the attacking units' faction.
  Each defender starts with one
  builder by the shared headquarters. Personal command centres are off the
  playable map. The match ends if the shared headquarters is destroyed.

  The program finds the game folder by itself; if not, choose the folder that contains INIZH.big
  in the Export tab. Language: the box in the top right corner (English / Slovenčina).

FEATURES
  - buildings are read from your own INIZH.big (correct sizes, works with mods)
  - European style: German houses, chalets, Soviet-style apartment blocks, churches (substitute),
    petrol stations, football pitches, cemeteries, parks, bus stops, street lamps, railways, bridges
  - dense European forests (48 tree species), willows by rivers, seasons and time of day
  - civilians, parked cars, tractors and oxen on fields, ambient sounds
  - 2 - 12 players, start layouts incl. manual placement, resources, AI attack paths
  - 1:1 scale (1 tile = 2.5 m), experimental huge maps up to 2000 x 2000 tiles
  - town clearing: insurgents hidden in houses, plus an optional insurgent stronghold (GLA / China / USA)

DATA
  (c) OpenStreetMap contributors (ODbL) - roads, buildings, water, land use
  Elevation: Mapzen / AWS Terrain Tiles
  Downloaded data is cached in %USERPROFILE%\.cncearth\cache

KNOWN LIMITS
  - the game stores height in 256 steps: at 1:1 the relief is max. ~38 m; "Real slopes" keeps hills 1:1 and compresses only the overall rise
  - the game has no European church model, GermanTownHall is used instead
  - stacked buildings, huge maps and the enemy fortress are experimental
  - the game draws at most 4000 trees (engine limit), they are spread over all forests
  - the unmodded game shows max. 8 players in skirmish
  - winter changes only the trees, the ground stays green

Fan-made tool, not affiliated with EA. Command & Conquer is a trademark of Electronic Arts.

SOURCE CODE
  The full Python source is in the "source" folder (or next to this file). Read DEVELOPERS.txt for how to run it,
  what each file does and how the generator works.
 BATTLE ROYALE
   From Game Modes, open the separate Battle Royale page and enable the mode
   (or pass --battle-royale on the command line).
   Set 2 to 32 slots on the Players tab. Use the same GeneralsX build on every PC.
   Host the exported map as free-for-all and leave unused slots closed. No controller AI is needed.
   Keep map.ini and map.str next to the .map file. For a 32-slot map, start with a large urban area.

   The page lets you edit character stats, upgrade prices, ability costs and damage,
   zone phases and radiation, population, rewards and elite crates. Values save with profiles.
   Each player gets one faction rifleman. Check the minimap to locate your unit.
   Civilians and farmers drop cash to pick up. You can set up to 2,000 civilians,
   1,200 rebels and 500 civilian vehicles. Civilian cars appear by roads, parking lots and starts.
   Board a neutral vehicle for free. A civilian car returns to neutral after the
   last passenger leaves unless you buy it from its command panel. Purchased cars
   can scout without passengers. Three Nuke Trucks are placed by default.
   Up to 64 Humvees are placed separately. Elite salvage crates stay where they land,
   even after the zone shrinks. Walk over one with your hero to gain one rank, up to Heroic.
   The radar marker refreshes while the crate is on the ground.
   Open WEAPONS to buy the RPG or sniper rifle and equip it using the same slot.
   Ability Shop is below WEAPONS. Open Killstreaks to buy and target Precision
   Airstrike or Cluster Strike. Buy and place each C4 charge
   separately. Up to five can be planted before detonating them together.
   The countdown shows when the next radioactive zone closes. Infantry recover after
   the configured delay without damage. The last player with infantry wins.
