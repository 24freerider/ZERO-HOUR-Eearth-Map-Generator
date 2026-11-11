"""Constants, model tables and small helpers of the map generator (split out of generator.py)."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides

HS = 0.625      # world units per height step
BORDER = 70
OCC = 2         # occupancy pixels per cell
BASE_H = 5.0    # small reserve below the lowest ground so river and lake beds can be carved (max ~4 units deep)

WHITE_ROAD = 'TwoLaneDarkDotted'
YELLOW_ROAD = 'TwoLane'
ASPHALT_ROADS = (WHITE_ROAD, YELLOW_ROAD)
PAVED_STREETS = ASPHALT_ROADS + ('Cobblestone', 'Sidewalk4')
UNPAVED_STREETS = ('GravelRoad', 'DirtRoad2', 'DirtRoadTracks')
ROAD_TYPE = {'rail': 'TrainTrack', 'rail_side': 'TrainTrack', 'motorway': WHITE_ROAD, 'trunk': WHITE_ROAD,
             'primary': WHITE_ROAD, 'secondary': WHITE_ROAD, 'tertiary': YELLOW_ROAD, 'street_marked': YELLOW_ROAD,
             'unclassified': YELLOW_ROAD, 'residential': YELLOW_ROAD, 'street_unmarked': YELLOW_ROAD,
             'living_street': YELLOW_ROAD, 'service': YELLOW_ROAD, 'track_paved': YELLOW_ROAD,
             'street_concrete': 'Sidewalk4', 'street_cobble': 'Cobblestone', 'track_gravel': 'GravelRoad',
             'track': 'DirtRoad2', 'track_rutted': 'DirtRoadTracks', 'path': 'DirtPath1',
             'pedestrian': 'Sidewalk3', 'path_paved': 'Sidewalk2', 'footway': 'Sidewalk'}
ROAD_W = {'TrainTrack': 35, WHITE_ROAD: 35, YELLOW_ROAD: 35, 'Cobblestone': 30,
          'Sidewalk4': 28, 'DirtRoad2': 33, 'DirtRoadTracks': 52, 'DirtPath1': 10, 'GravelRoad': 33,
          'Sidewalk3': 22, 'Sidewalk2': 16, 'Sidewalk': 8, 'ParkingLines': 35}
ROAD_SURFACE = {'DirtPath1': 'dirt', 'DirtRoadTracks': 'dirt', 'DirtRoad2': 'dirt', 'GravelRoad': 'dirt',
                'TrainTrack': 'dirt', 'Sidewalk': 'cobble', 'Sidewalk2': 'cobble', 'Sidewalk3': 'cobble',
                'Sidewalk4': 'conc', 'Cobblestone': 'cobble', WHITE_ROAD: 'asphalt', YELLOW_ROAD: 'asphalt'}
ROAD_PRIO = ['rail', 'rail_side', 'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential',
             'street_marked', 'street_unmarked', 'living_street', 'street_concrete', 'street_cobble', 'pedestrian',
             'service', 'track_paved', 'track_gravel', 'track', 'track_rutted',
             'path_paved', 'path', 'footway']
# the only models that may be stacked into taller blocks: the square tower block and the long block with balconies
STACKABLE = {'AsiaApartment02', 'AsianApartmentSingle01'}

# blocks of flats (panelaky) and flat-roofed buildings are built from these pieces, laid out over the real OSM
# footprint (L / U / H shapes included). Order = preference; a piece is used if the part of the footprint fits it.
PANEL_TALL = ['CivilianHighrise01', 'AsianApartment01']             # 9+ floors: high-rise blocks side by side
PANEL_LONG = ['AsianApartmentSingle01', 'AsianApartment01']         # long blocks with balconies
PANEL_BLOCK = ['AsianApartment01', 'AsianApartmentUnit']            # ordinary block of flats
PANEL_TOWER = 'AsiaApartment02'                                     # small square block, may be stacked
FLAT_PIECES = ['AsianApartmentUnit']                                # small shops, offices, flat roofs
# big flat-roofed buildings of a city (offices, department stores, old-town blocks): bigger pieces, so a large
# footprint is a few buildings instead of a grid of dozens of small cubes
CITY_LONG = ['SovietRadioBuilding', 'AsianOffice01', 'AsianApartmentUnit']   # long parts
CITY_SQUARE = ['GreekShop', 'AsianOffice01', 'AsianApartmentUnit']          # square-ish parts
CITY_TALL = ['CivilianHighrise02', 'AsianOffice01']                          # 6+ floors
GARAGE_PIECES = ['MogadishuGarage']
# dense big cities (Vienna, Budapest ... building on building): many more different city buildings, by height
DENSE_MID = ['SovietBuilding', 'StanSovietOffice01', 'SovietGovernmentBuildingSmall', 'EuroHighrise2', 'GreekShop',
             'AsianOffice01', 'SovietRadioBuilding', 'AsiaApartment02', 'AsianApartment01', 'AsianApartmentSingle01']
DENSE_TALL = ['EuroHighrise', 'CivilianHighrise01', 'CivilianHighrise02', 'SovietBuilding', 'AsianOffice01']
DENSE_SHARE = 0.28       # ... and at least this share of the map covered by buildings (Vienna ~40 %, Poltar ~10 %)
DENSE_PER_KM2 = 900       # buildings per km2 from which a town counts as a dense city (Poltar and towns stay as before)
FILL_CLASSES = ('apt', 'civic', 'flat', 'garage')
# building types the player can pick in the editor -> (class, forced pieces)
EDIT_KINDS = {'apt_low': ('apt', None), 'apt_high': ('apt', None), 'hall': ('sporthall', None),
              'shed': ('shed', None), 'industrial': ('industrial', None), 'civic': ('civic', None),
              'tower': ('apt', ['AsiaApartment02']), 'long': ('apt', ['AsianApartmentSingle01']),
              'mid': ('apt', ['AsianApartment01']), 'high': ('apt', ['CivilianHighrise01']),
              'flat': ('flat', None), 'house': ('house', None), 'garage': ('garage', None)}
MAX_ROAD_POINTS = 14000
# The game keeps at most 4000 road segments (GameData.ini MaxRoadSegments) and silently skips every road after
# that. Each pair of road points is one segment, every bend adds one per 30 degrees of turn and every junction
# adds one more. Stay safely below with an estimate; roads are written most important first.
ROAD_SEG_BUDGET = 3600
MINOR_ROADS = UNPAVED_STREETS + ('DirtPath1', 'Sidewalk', 'Sidewalk2')
MAX_TREES = 10000
TREE_SPACING = 26.0   # world units between forest trees at 1:1 (~6.5 m)
BROADLEAF = ['TreeOak01', 'TreeOak02', 'TreeOak05', 'TreeOak06', 'TreeOak1', 'TreeBirch01', 'TreeBirch02',
             'TreeBirch03', 'TreeBirch04', 'TreeBirch05', 'TreeMaple03', 'TreeMaple04', 'TreeMaple05',
             'TreeMaple06', 'TreeMaple1', 'TreeMaple2', 'TreeDogwood1', 'TreeDogwood3', 'TreeDogwood4']
CONIFER = ['TreeSpruce', 'TreeSpruce2', 'TreeSpruce03', 'TreeSpruce04', 'TreeSpruce05', 'TreeFir01', 'TreeFir02',
           'TreeFir03', 'TreeFir04', 'TreeFir05', 'TreePine', 'TreePine2', 'TreePine3', 'TreePine4', 'TreePine5']
WILLOWS = ['TreeWillow01', 'TreeWillow02', 'TreeWillow03']
BUSHES = ['Bush01', 'Bush02', 'Bush03', 'Bush04', 'Bush05', 'Bush06', 'Bush07', 'Bush08', 'Bush09', 'Bush10', 'Bush11']
STREET_TREES = ['TreeMaple03', 'TreeMaple04', 'TreeOak1', 'TreeBirch01', 'TreeDogwood1']
MAX_BUILDINGS = 10 ** 9           # no cap: the player wants every building
TREES = ['TreeDogwood1', 'TreeDogwood3', 'TreeDogwood4', 'TreeDogwood8', 'TreeMaple06']

STYLES = {
    # Central / Western Europe: German, Euro, Soviet-block (panelaky), chalets, barns
    'eu': {
        'house': ['GermanTownHouse1', 'GermanTownHouse2', 'FarmHouse', 'GermanPub01',
                  'GermanCotage01', 'Chalet', 'Chalet2', 'Chalet3'],
        'apt': ['AsianApartment01', 'AsianApartmentSingle01', 'AsiaApartment02', 'CivilianHighrise01'],
        'shop': ['SupplyWarehouse', 'WaterfrontWherehouse01', 'IndustrialBuilding02'],     # big halls only
        'pub': ['GermanPub01', 'GermanPub02'],
        'industrial': ['IndustrialBuilding01', 'IndustrialBuilding02', 'ToxinRepository', 'SupplyWarehouse', 'WaterfrontWherehouse01', 'WaterfrontWherehouse02', 'SovietRadioBuilding', 'OilRefinery', 'TechOilRefinery', 'StanOilTank01', 'StanOilTank02', 'ChemicalFactory', 'PharmaceuticalFactory', 'VehicleHanger01', 'StanHanger01', 'CivilianTEROilDepot'],
        'farmhall': ['IndustrialBuilding01', 'WaterfrontWherehouse01', 'WaterfrontWherehouse02', 'BarnCoop', 'SupplyWarehouse'],
        'hospital': ['TechHospital'],
        'sporthall': ['SupplyWarehouse', 'IndustrialBuilding02', 'WaterfrontWherehouse01'],
        'mall': ['AsianMegaHotel01', 'AsianMegaMall01', 'InternationalHotel01', 'StanHotel02'],
        'civic': ['GermanTownHall', 'SovietRadioBuilding'],
        'church': ['GermanTownHall'],      # the game has no European church; town hall with tower is closest
        'farm': ['BarnShed', 'BarnCoop', 'FarmHouse'],
        'shed': ['Outhouse02', 'Shack', 'BarnShed'],
    },
    # Middle East / Central Asia / Africa
    'me': {
        'house': ['Shack', 'MogadishuHouse06', 'MogadishuHouse04', 'MogadishuHouse12', 'MogadishuHouse13',
                  'MogadishuHouse02', 'MogadishuArchedHouse'],
        'apt': ['MogadishuSmallApartment', 'StanApartment02', 'StanApartment01', 'StanApartmentSingle01',
                'MogadishuHighrise02'],
        'shop': ['StanSmallRetail01', 'StanSmallRetail02', 'StanSmallRetail03', 'StanConvenienceStore01',
                 'StanRestaurant01', 'StanHotel01', 'MogadishuShop01'],
        'industrial': ['IndustrialBuilding01', 'IndustrialBuilding02', 'ToxinRepository', 'SupplyWarehouse', 'WaterfrontWherehouse01', 'WaterfrontWherehouse02', 'SovietRadioBuilding', 'OilRefinery', 'TechOilRefinery', 'StanOilTank01', 'StanOilTank02', 'ChemicalFactory', 'PharmaceuticalFactory', 'VehicleHanger01', 'StanHanger01', 'CivilianTEROilDepot'],
        'farmhall': ['IndustrialBuilding01', 'WaterfrontWherehouse01', 'WaterfrontWherehouse02', 'BarnCoop', 'SupplyWarehouse'],
        'hospital': ['TechHospital'],
        'sporthall': ['SupplyWarehouse', 'IndustrialBuilding02', 'WaterfrontWherehouse01'],
        'mall': ['AsianMegaHotel01', 'AsianMegaMall01', 'InternationalHotel01', 'StanHotel02'],
        'civic': ['StanSchoolhouse02', 'StanTownHall02', 'StanTownHall03', 'TechHospital'],
        'church': ['MidEastCathedral02', 'StanTownHall02'],
        'farm': ['BarnShed', 'BarnCoop'],
        'shed': ['Outhouse01', 'Outhouse02', 'GrassHut02', 'MogadishuGarage'],
    },
    # East Asia
    'asia': {
        'house': ['RiverHouse01', 'RiverHouse02', 'AsianApartmentUnit'],
        'apt': ['AsianApartmentUnit', 'AsiaApartment02', 'AsianApartment01', 'AsianApartmentSingle01',
                'AsianApartmentComplex'],
        'shop': ['AsianRetailStore01', 'AsianRetailStore02', 'ChinaRetail01', 'ChinaRetail02', 'AsianElectricRetail'],
        'industrial': ['IndustrialBuilding01', 'IndustrialBuilding02', 'ToxinRepository', 'SupplyWarehouse', 'WaterfrontWherehouse01', 'WaterfrontWherehouse02', 'SovietRadioBuilding', 'OilRefinery', 'TechOilRefinery', 'StanOilTank01', 'StanOilTank02', 'ChemicalFactory', 'PharmaceuticalFactory', 'VehicleHanger01', 'StanHanger01', 'CivilianTEROilDepot'],
        'farmhall': ['IndustrialBuilding01', 'WaterfrontWherehouse01', 'WaterfrontWherehouse02', 'BarnCoop', 'SupplyWarehouse'],
        'hospital': ['TechHospital'],
        'sporthall': ['SupplyWarehouse', 'IndustrialBuilding02', 'WaterfrontWherehouse01'],
        'mall': ['AsianMegaHotel01', 'AsianMegaMall01', 'InternationalHotel01', 'StanHotel02'],
        'civic': ['AsiaTownHall01', 'AsiaGovernmentBuilding', 'TechHospital'],
        'church': ['AsiaTownHall01'],
        'farm': ['BarnShed', 'BarnCoop'],
        'shed': ['Outhouse01', 'Outhouse02', 'QuonsetHut01'],
    },
}
COMMON = {}
STYLE_NAMES = {'eu': 'Európa', 'me': 'Blízky východ a Afrika', 'asia': 'Ázia'}


def region_style(lat, lon):
    """Building set by where the map is: Europe; Middle East, North Africa, Africa and Central Asia; South / East
    Asia. Elsewhere (the Americas, Australia) the European set, until the game's buildings are sorted for them."""
    if (lat >= 42 and -25 <= lon <= 50) or (35 <= lat < 42 and -10 <= lon <= 28):
        return 'eu'                                         # incl. Iberia, Italy, the Balkans, Greece
    if -36 <= lat < 38 and -20 <= lon <= 52:
        return 'me'                                         # Africa, Arabia
    if 12 <= lat < 55 and 26 <= lon < 75:
        return 'me'                                         # Turkey, Levant, Iraq, Iran, Afghanistan, Central Asia
    if -12 <= lat <= 55 and 75 <= lon <= 150:
        return 'asia'                                       # India, China, Korea, Japan, South-East Asia
    return 'eu'

# a special building that does not fit its footprint falls back to an ordinary one (never a giant model)
FALLBACK = {'pub': ['house'], 'shop': ['house'], 'civic': ['house'], 'church': ['house'],
            'industrial': ['farm', 'shed'], 'farm': ['shed'], 'hospital': ['civic', 'house'], 'farmhall': ['farm', 'shed'],
            'sporthall': ['farmhall', 'shed'], 'mall': ['shop', 'house']}

# fences / walls / hedges: model names and the length of one piece in world units (1 unit = 0.25 m at 1:1)
FENCES = {'fence': [('PicketFence01', 32), ('PicketFence02', 30)],
          'chain': [('ChainLinkFence01', 28)],
          'wall': [('BrickWall01', 30), ('StoneWall02', 30), ('StoneWall03', 30)],
          'brick': [('BrickWall01', 30), ('BrickWall02', 26), ('BrickWall03', 26)],
          'stone': [('StoneWall02', 30), ('StoneWall03', 30), ('StoneWall04', 30), ('StoneWall05', 30)],
          'concrete': [('ConcreteWall01', 30)],
          'hedge': [('Hedge1', 18)]}
BRIDGE = {'TrainTrack': 'Industrial', WHITE_ROAD: 'EuropeanBridgeAsphalt', YELLOW_ROAD: 'EuropeanBridgeTwoLane',
          'Sidewalk4': 'ConcreteOneLane', 'Cobblestone': 'EuropeanBridge',
          'DirtRoad2': 'WoodenSectional', 'DirtRoadTracks': 'WoodenSectional', 'DirtPath1': 'WoodenSectional', 'GravelRoad': 'WoodenSectional',
          'Sidewalk3': 'WoodenSectional', 'Sidewalk2': 'WoodenSectional', 'Sidewalk': 'WoodenSectional'}
FLAG_ROAD_A, FLAG_ROAD_B, FLAG_BRIDGE_A, FLAG_BRIDGE_B = 0x02, 0x04, 0x10, 0x20
# Insurgents: skirmish deletes every map side except FactionCivilian ones (SidesList::prepareForMP_or_Skirmish),
# so the enemy is a civilian-faction side; a start-up script makes it and all skirmish players (player0..11) enemies.
INS_SIDE, INS_TEAM = 'PlyrInsurgents', 'teamPlyrInsurgents'
MAX_PLAYERS = 32     # GeneralsX 0.9.70+: 32 players (the classic game shows 8)
INS_START = 32       # the insurgents' own start number, past every player's (AI path labels <Kind>33)
AI_BUILD_R = 1600.0      # no insurgents this close to a start (AI build area)
AI_CORE_R = 330.0        # tree-free core of a start base (command center + first AI buildings)
FORT_ALLEY = 2.0         # insurgent base buildings stand wall to wall (doors face out, nothing blocks them)
# ZombieSurvival tiers: (object, scale, speed, hp, cost -> bounty 10 % = $ per kill). Existing terrorist variants
# are re-tuned in map.ini, so no new objects are needed.
ZOMBIE_EARLY_ROUNDS = 5
ZOMBIE_DEFAULT_LIVE_LIMIT = 180
ZOMBIE_MAX_LIVE_LIMIT = 3000
# ZombieSurvival shop vehicle per faction family (as in "[zombies] wheel of death": all are placed, a script keeps one)
ZS_FACTIONS = {'USA': (['America', 'AmericaAirForceGeneral', 'AmericaSuperWeaponGeneral', 'AmericaLaserGeneral'],
                       'AmericaVehicleHumvee', 'Humvee'),
               'China': (['China', 'ChinaTankGeneral', 'ChinaNukeGeneral', 'ChinaInfantryGeneral'],
                         'ChinaVehicleTroopCrawler', 'Crawler'),
               'GLA': (['GLA', 'GLAStealthGeneral', 'GLADemolitionGeneral', 'GLAToxinGeneral'],
                       'GLAVehicleBattleBus', 'Bus')}   # the first rounds come from one close point north of the fight
# Zombie tiers - the zombies of "[zombies] wheel of death" (DrGoldFish): they see the whole map, climb cliffs like
# Col. Burton, only blow up when they reach you (not when shot), and get faster / tougher by type; the boss kills with
# a knife in one hit. Our cost decides the bounty (10 % = $20 ... $200). Lifetime is longer than in wheel of death,
# because our maps are bigger.
# (object, scale, locomotor (speed, damaged), upgraded locomotor or None, hp or None = game default, cost, dynamite,
#  knife, extra) - values of wheel of death v5-6; extra: 'cine' = burning zombie with a shockwave, 'boss' = hazmat armour
ZS_TIERS = [('Demo_GLAInfantryTerrorist', 1.0, (25, 20), (32, 20), None, 200, False, False, ''),
            ('GLAInfantryTerrorist', 1.1, (32, 20), (36, 25), None, 600, True, False, ''),
            ('Slth_GLAInfantryTerrorist', 1.15, (36, 25), (42, 32), None, 1000, True, False, ''),
            ('Chem_GLAInfantryTerrorist', 1.3, (40, 80), None, 200, 1500, True, False, ''),
            ('CINE_GLAInfantryTerrorist', 1.2, (32, 20), (36, 25), 300, 1500, True, False, 'cine'),
            ('GC_Slth_GLAInfantryTerrorist', 1.15, (87, 60), None, None, 1800, False, False, ''),
            ('GC_Chem_GLAInfantryTerrorist', 1.5, (42, 32), None, 3000, 3500, False, True, 'boss')]
# zombie-side upgrades as in wheel of death: the first Chem zombie brings composite armour (+30 HP for the small ones),
# the first fast GC zombie brings nuclear engines (the upgraded, faster locomotors)
ZS_GRANTS = {'Chem_GLAInfantryTerrorist': 'Upgrade_AmericaCompositeArmor',
             'GC_Slth_GLAInfantryTerrorist': 'Upgrade_ChinaNuclearTanks'}
# round mix: (first round, last round, % per tier) - normal zombies stay the majority even late
ZS_MIX = [(1, 3, (100, 0, 0, 0, 0, 0, 0)), (4, 6, (70, 30, 0, 0, 0, 0, 0)), (7, 9, (50, 30, 20, 0, 0, 0, 0)),
          (10, 12, (40, 25, 20, 10, 5, 0, 0)), (13, 15, (35, 25, 15, 12, 8, 5, 0)),
          (16, 999, (30, 22, 15, 13, 10, 7, 3))]
INS_MIX = {1: [('GLAInfantryRebel', 85), ('GLAInfantryTunnelDefender', 15)],
           2: [('GLAInfantryRebel', 60), ('GLAInfantryTunnelDefender', 30), ('GLAInfantryTerrorist', 10)],
           3: [('GLAInfantryRebel', 45), ('GLAInfantryTunnelDefender', 35), ('GLAInfantryTerrorist', 10),
               ('GLAInfantryHijacker', 10)]}
BASE_CLEAR = 900     # world units: free, flat square for each player's base (AI needs room to build)


PITCHED_ROOFS = {'gabled', 'hipped', 'half_hipped', 'gambrel', 'mansard', 'saltbox', 'pyramidal', 'side_hipped',
                 'round', 'onion', 'dome', 'skillion', 'gabled_row'}


def real_height_m(tags):
    h = str(tags.get('height', '')).replace('m', '').strip()
    try:
        return float(h)
    except ValueError:
        pass
    lv = str(tags.get('building:levels', '')).strip()
    try:
        return float(lv) * 3.0 + 1.0
    except ValueError:
        return 0.0


def classify(tags):
    b = tags.get('building', 'yes')
    if tags.get('amenity') == 'place_of_worship': return 'church'
    if b in ('apartments', 'dormitory', 'residential') and int(tags.get('building:levels', '3') if str(tags.get('building:levels', '3')).isdigit() else 3) >= 3:
        return 'apt'
    if b in ('apartments', 'dormitory'): return 'apt'
    if b in ('retail', 'commercial', 'office', 'supermarket', 'kiosk', 'hotel', 'restaurant'): return 'shop'
    if b in ('industrial', 'warehouse', 'factory', 'manufacture', 'hangar'): return 'industrial'
    if b == 'hospital' or tags.get('amenity') in ('hospital', 'clinic'): return 'hospital'
    if b in ('sports_hall', 'sports_centre', 'stadium', 'gymnasium', 'riding_hall') or \
            tags.get('leisure') in ('sports_centre', 'sports_hall', 'ice_rink', 'swimming_pool') and b != 'yes' or \
            tags.get('leisure') in ('sports_centre', 'sports_hall'): return 'sporthall'
    if b in ('school', 'kindergarten', 'public', 'civic', 'government', 'university', 'college', 'train_station'): return 'civic'
    if b in ('church', 'cathedral', 'chapel', 'mosque', 'temple', 'synagogue'): return 'church'
    if b in ('barn', 'farm', 'farm_auxiliary', 'cowshed', 'stable', 'greenhouse', 'silo', 'sty'): return 'farm'
    if b in ('shed', 'garage', 'garages', 'carport', 'hut', 'roof', 'cabin', 'outbuilding', 'service', 'container'): return 'shed'
    return 'house'
