"""Offline Battle Royale check using the stock game catalog and a synthetic town."""
import argparse
import re
from itertools import combinations
from math import dist
from pathlib import Path
from tempfile import TemporaryDirectory

from catalog import Catalog, find_game_dir
from g_brhero import CAPTURE, HERO_BUTTONS
from g_brini import POI
from g_brscript import RADAR_BEACON
from g_brsettings import BR_COUNTS, BR_SETTINGS, FIRST_RADIATION_WARNING, PHASES
from g_brvehicles import CIVIL_VEHICLES, HUMVEES, NUKE_TRUCK, START_CAR_DISTANCE, VEHICLE_BUY_UPGRADE
from g_invasion import timer
from generator import generate
from geo import Proj
from mapio import MapFile, parse_sides
from selftest import TEMPLATE, synthetic_dem, synthetic_osm


def check(cat, root, slots):
    size, tiles = 3200, 360
    lat, lon = 48.43, 19.80
    path = root / f'br{slots}.map'
    requested = {key: maximum + 1 if slots == 32 else 0 for key, (_, maximum, _) in BR_COUNTS.items()}
    requested.update(br_civilian_reward=17, br_farmer_reward=123, br_rebel_reward=57)
    if slots == 2:
        requested.update(br_hp_usa=987, br_move_usa=27, br_price_air=1575, br_zone_phase1=180, br_zone_phases=3,
                         br_zone_beacons=6, br_zone_damage=93, br_drop_count=2, br_enable_cluster=False)
    g = generate(synthetic_osm(lat, lon, size), synthetic_dem, Proj(lat, lon, size, tiles),
                 tiles, cat, TEMPLATE, path, players=slots, mode_battle_royale=True,
                 seed=7, log=lambda _: None, language='en', **requested)
    assert len(g.starts) == slots == len(g.br_drops)
    assert all(not g.inside(*p) for p in g.starts)
    assert min(dist(a, b) for a, b in combinations(g.br_drops, 2)) >= g.br_spawn_min
    assert all(dist(a, b) >= g.br_enemy_min for a in g.br_drops for _, b in g.br_rebels)
    if slots == 32:
        civilian_positions = [position for _, model, position in g.br_vehicles if model in CIVIL_VEHICLES]
        assert 0 < len(civilian_positions) <= 500
        assert all(min(dist(drop, pos) for pos in civilian_positions) <= START_CAR_DISTANCE for drop in g.br_drops)
    else:
        assert g.stats['br_civil_vehicles'] == 0
    if slots == 32:
        assert 1000 <= g.stats['br_pedestrians'] <= 2000
    else:
        assert g.stats['br_pedestrians'] == 0
    assert 0 <= g.stats['br_farmers'] <= g.stats['br_pedestrians'] // 20
    assert 'MoneyProvided = 17' in g.map_ini and 'MoneyProvided = 123' in g.map_ini
    assert g.stats['br_rebels'] == (1200 if slots == 32 else 0)
    assert g.stats['br_humvees'] == BR_SETTINGS['br_humvee_count'][0]
    assert g.stats['br_nuke_trucks'] == 3
    assert 'SupplyWave' not in g.map_ini and 'MoneyProvided = 500' not in g.map_ini
    if slots == 32:
        assert {v[1] for v in g.br_vehicles} == set((*CIVIL_VEHICLES, *HUMVEES, NUKE_TRUCK)) & cat.objects
    assert g.map_ini.count('PassengersAllowedToFire = Yes') == len(g.br_transport_models)
    assert g.map_ini.count('Slots = 4') + g.map_ini.count('ContainMax = 4') == len(g.br_transport_models)
    assert 'InitialPayload = NONE' not in g.map_ini
    assert all(f'CommandButton {button}\n' in g.map_ini for button in HERO_BUTTONS.values())
    sedan = re.search(r'^Object CarSedan01\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
    assert sedan.index('Behavior = TransportAIUpdate') < sedan.index('Locomotor = SET_NORMAL EARTH_BR_CivilDrive')
    assert 'MobileGarrison = Yes' in sedan and 'AllowNeutralInside = Yes' in sedan
    assert f'TriggeredBy = {VEHICLE_BUY_UPGRADE}' in sedan
    assert f'Upgrade {VEHICLE_BUY_UPGRADE}\n  Type = OBJECT\n  BuildTime = 0\n  BuildCost = 1000' in g.map_ini
    assert 'Command_DetonateConvoyTruckNuke' in g.map_ini
    assert g.map_ini.count('NumberOfExitPaths = 0') == len(g.br_transport_models)
    crate = re.search(r'^Object EARTH_BR_EliteDrop\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
    assert 'SalvageCrateCollide' in crate and 'LevelChance = 100%' in crate
    assert 'Lifetime' not in crate and 'PickupScience' not in crate
    assert g.map_ini.count('KindOf = +HERO +SALVAGER') == 3
    assert 'ExperienceRequired = 0 120 240 360' in g.map_ini
    assert 'OCL = SUPERWEAPON_A10ThunderboltMissileStrike1' in g.map_ini
    assert 'OCL = EARTH_BR_HeroClusterStrikePayload' in g.map_ini
    assert 'TriggeredBy = Upgrade_EARTH_BR_HeroAir' in g.map_ini
    assert 'TriggeredBy = Upgrade_EARTH_BR_HeroCapture' in g.map_ini
    for button, power, _ in CAPTURE.values():
        capture = re.search(rf'^CommandButton {button}\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
        assert f'SpecialPower = {power}' in capture
        assert 'Upgrade = Upgrade_EARTH_BR_HeroCapture' in capture
        assert 'NEED_SPECIAL_POWER_SCIENCE' not in capture
    assert g.map_ini.count('Weapon = SECONDARY TunnelDefenderRocketWeapon') == 6
    assert g.map_ini.count('Weapon = TERTIARY EARTH_BR_HeroSniperRifle') == 6
    sniper = re.search(r'^Weapon EARTH_BR_HeroSniperRifle\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
    assert 'PrimaryDamage = 100' in sniper and 'DelayBetweenShots = 3100' in sniper
    assert 'SpecialObject = EARTH_BR_HeroRemoteCharge' in g.map_ini
    assert g.map_ini.count('MaxSpecialObjects = 5') == 3
    main_panel = re.search(r'^CommandSet EARTH_BR_HeroMainUSA\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
    c4_ready = re.search(r'^CommandSet EARTH_BR_HeroC4ReadyUSA\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
    killstreaks = re.search(r'^CommandSet EARTH_BR_HeroKillstreaks0USA\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
    index = 1 if slots == 2 else 2
    air_ready = re.search(rf'^CommandSet EARTH_BR_HeroKillstreaks{index}USA\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
    assert HERO_BUTTONS['Strike'] not in main_panel.split()
    assert HERO_BUTTONS['Strike'] in air_ready.split()
    assert HERO_BUTTONS['Air'] in killstreaks.split() and HERO_BUTTONS['Strike'] not in killstreaks.split()
    assert HERO_BUTTONS['PlaceC4'] not in main_panel.split()
    assert f"11 = {HERO_BUTTONS['Weapons']}" in main_panel
    assert f"12 = {HERO_BUTTONS['Shop']}" in main_panel
    assert all(HERO_BUTTONS[key] in c4_ready.split() for key in ('Weapons', 'Shop', 'Killstreaks', 'PlaceC4'))
    for index, rpg, sniper in ((0, 'RPG', 'Sniper'), (1, 'RPG', 'EquipSniper'),
                               (2, 'EquipRPG', 'Sniper'), (3, 'EquipRPG', 'EquipSniper')):
        weapon_menu = re.search(rf'^CommandSet EARTH_BR_HeroWeapons{index}USA\n(.*?)^End$', g.map_ini, re.M | re.S)[1]
        assert f'2 = {HERO_BUTTONS[rpg]}' in weapon_menu
        assert f'3 = {HERO_BUTTONS[sniper]}' in weapon_menu
    if slots == 2:
        assert 'MaxHealth = 987' in g.map_ini and 'PrimaryDamage = 93' in g.map_ini
        assert re.search(r'^Locomotor EARTH_BR_HeroWalkUSA\n[^\n]+\n  Speed = 27$', g.map_ini, re.M)
        assert 'BuildCost = 1575' in g.map_ini and '$1575' in g.map_strings['EARTH_BR_HeroAir']
        assert HERO_BUTTONS['ClusterBuy'] not in killstreaks.split()
        assert g.br_phases == (180, *PHASES[1:3])
    assert 'Armor = RADIATION 20%' in g.map_ini
    assert re.search(r'^\s*Armor\s*=\s*EARTH_BR_StructureArmor\b', g.map_ini, re.M)
    assert 'DamageType = RADIATION' in g.map_ini
    assert {role for role, _ in g.br_pois} == set(POI)
    fields = {}
    airdrops = {}
    original = MapFile.read(path)
    ids = {i: s for s, i in original.strings}
    for o in original.objects:
        if o.name != '*Waypoints/Waypoint':
            continue
        name = next((v for k, v in o.props if ids[k >> 8] == 'waypointName'), '')
        if name.startswith('EARTH_BR_Field'):
            fields[name] = (o.x, o.y)
        if '_AirDrop' in name:
            airdrops[name] = (o.x, o.y)
    assert PHASES[0] == 300 and len(PHASES) == 8
    scripts_by_name = {s.name: s for s in g.br_scripts()}
    assert timer('EARTH_BR_ZoneWarning', g.br_phases[0] - FIRST_RADIATION_WARNING) in scripts_by_name['EARTH_BR_Setup'].actions
    zone_scripts = {name: s for name, s in scripts_by_name.items() if name.startswith('EARTH_BR_Safe')}
    for path_ in g.br_zone_patterns:
        remaining = set(fields)
        previous = float('inf')
        assert len(path_) == len(g.br_phases)
        for phase, (area, new_fields, (x, y, radius)) in enumerate(path_):
            assert radius < previous
            assert len(g.br_zone_markers[area]) == g.opt['br_zone_beacons']
            assert len(g.br_airdrops[area]) == g.opt['br_drop_count']
            assert all(dist((x, y), airdrops[name]) < radius for name in g.br_airdrops[area])
            activation = zone_scripts[area + '_Activate'].actions
            assert all(action != 'NAMED_DELETE' for action, _ in activation)
            warning = zone_scripts[area + '_Warning'].actions
            assert warning[-1][0] == 'OBJECT_CREATE_RADAR_EVENT'
            if phase == 0:
                assert warning[0] == ('PLAY_SOUND_EFFECT', [('sound', 'AirRaidSiren')])
                assert warning[1] == ('SHOW_MILITARY_CAPTION', [
                    ('text', 'SCRIPT:EARTH_BR_FirstRadiationBriefing'), ('int', 10000)])
                assert warning[2][0] == 'DISPLAY_COUNTDOWN_TIMER'
            else:
                assert warning[0][0] == 'DISPLAY_TEXT'
            for name in g.br_airdrops[area]:
                beacon = zone_scripts[name + '_Beacon']
                assert not beacon.once and ('radar_event', RADAR_BEACON) in beacon.actions[0][1]
                assert not beacon.active and beacon.conditions == [('NAMED_NOT_DESTROYED', [('unit', name)])]
                assert ('ENABLE_SCRIPT', [('script', beacon.name)]) in activation
            assert ('PLAY_SOUND_EFFECT', [('sound', 'PreDirtyNukeDetonation')]) in activation
            for seconds in (2, 4, 6):
                ping = zone_scripts[f'{area}_Ping{seconds}']
                assert ping.actions == [a for a in activation
                                        if a[0] == 'OBJECT_CREATE_RADAR_EVENT' and a[1][0][1] in g.br_zone_markers[area]]
            assert set(new_fields) <= remaining
            remaining.difference_update(new_fields)
            assert all(dist((x, y), fields[name]) < radius for name in remaining)
            previous = radius
        assert remaining
    sides, _, scripts = parse_sides(original)
    assert any(v == 'PlyrInsurgents' for side in sides for _, v in side)
    assert scripts
    copy = root / 'roundtrip.map'
    original.write(copy, compress=True)
    assert copy.read_bytes() == path.read_bytes()
    print(f"{slots} slots: {len(g.br_vehicles)} vehicles, {len(g.br_rebels)} rebels, {g.stats['br_pedestrians']} civilians, "
          f'drops at least {g.br_spawn_min} units apart, map roundtrip OK')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('game', nargs='?', default=find_game_dir(), help='Zero Hour folder containing INIZH.big')
    args = parser.parse_args()
    archive = Path(args.game) / 'INIZH.big'
    if not archive.is_file():
        parser.error('INIZH.big not found')
    with TemporaryDirectory() as temp:
        catalog = Catalog.load([archive])
        for n in (2, 32):
            check(catalog, Path(temp), n)
