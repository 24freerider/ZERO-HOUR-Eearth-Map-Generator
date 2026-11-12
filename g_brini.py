"""Map-local rules for the Battle Royale infantry and radiation."""
import re

from g_brhero import CAPTURE, HERO, hero_abilities_ini, hero_modules, hero_rifle_ini
from g_brsettings import FIRST_RADIATION_WARNING

RIFLES = {
    'USA': 'AmericaInfantryRanger',
    'China': 'ChinaInfantryRedguard',
    'GLA': 'GLAInfantryRebel',
}
REBEL = 'EARTH_BR_StreetRebel'
FIELD = 'RadiationFieldLarge'
ZONE_MARKER = 'EARTH_BR_RadarMarker'
ELITE_DROP = 'EARTH_BR_EliteDrop'
HUMVEES = ('Humvee1', 'AmericaVehicleHumvee', 'AmericaVehicleMedic')
NUKE_TRUCK = 'ConvoyTruckArmedWithNuke'
VEHICLE_BUY_UPGRADE = 'Upgrade_EARTH_BR_BuyScoutVehicle'
STARTERS = {family: 'EARTH_BR_Starter' + family for family in RIFLES}
POI = {
    'Cluster': ('EnglishTVStatio', 'SuperweaponCarpetBomb', 'SUPERWEAPON_CarpetBomb'),
    'Intel': ('ChineeseSpyPost', None, None),
}


def structure_radiation_ini(catalog, models, radiation_pct):
    # An Armor definition replaces the entire table. Copy the stock coefficients.
    profiles = {}
    structures = []
    armor_line = re.compile(r'(^[ \t]*Armor\s*=\s*)(\w+)', re.M | re.I)

    def radiation_armor(match):
        source = match[2]
        if source not in catalog.armors:
            return match[0]
        name = 'EARTH_BR_' + source
        if name not in profiles:
            coefficients = [line.strip() for line in catalog.armors[source].splitlines()
                            if not re.match(r'^\s*Armor\s*=\s*RADIATION\b', line, re.I)]
            coefficients.append(f'Armor = RADIATION {radiation_pct}%')
            profiles[name] = f'Armor {name}\n' + '\n'.join('  ' + line for line in coefficients) + '\nEnd\n'
        return match[1] + name

    for name, model in sorted(models.items()):
        sets = catalog.structure_armor_sets.get(model)
        if sets:
            original = '\n'.join(line for block in sets for line in block.splitlines())
            patched = armor_line.sub(radiation_armor, original)
            if patched != original:
                structures.append(f'Object {name}\n{patched}\nEnd\n')
    return '\n'.join([*profiles.values(), *structures])


def transport_ini(catalog, models, opt):
    parts = ['''CommandSet EARTH_BR_Transport
  1 = Command_Evacuate
  13 = Command_AttackMove
  14 = Command_Stop
End

CommandSet EARTH_BR_CivilTransport
  1 = Command_Evacuate
  2 = Command_UpgradeAmericaSupplyLines
  13 = Command_AttackMove
  14 = Command_Stop
End

CommandSet EARTH_BR_NukeTransport
  1 = Command_Evacuate
  2 = Command_DetonateConvoyTruckNuke
  14 = Command_Stop
End

Locomotor EARTH_BR_CivilDrive
  Surfaces = GROUND RUBBLE
  Speed = 65
  SpeedDamaged = 35
  TurnRate = 180
  TurnRateDamaged = 150
  Acceleration = 200
  AccelerationDamaged = 120
  Braking = 200
  MinTurnSpeed = 0
  TurnPivotOffset = -0.33
  ZAxisBehavior = NO_Z_MOTIVE_FORCE
  Appearance = FOUR_WHEELS
  StickToGround = Yes
End
''', f'''Upgrade {VEHICLE_BUY_UPGRADE}
  Type = OBJECT
  BuildTime = 0
  BuildCost = {opt['br_vehicle_price']}
  DisplayName = SCRIPT:EARTH_BR_BuyScoutVehicle
End

CommandButton Command_UpgradeAmericaSupplyLines
  Command = OBJECT_UPGRADE
  Upgrade = {VEHICLE_BUY_UPGRADE}
  Options = NOT_QUEUEABLE
  TextLabel = SCRIPT:EARTH_BR_BuyScoutVehicle
  DescriptLabel = SCRIPT:EARTH_BR_BuyScoutVehicleHelp
  ButtonBorderType = UPGRADE
End
''']
    for model in models:
        modules = catalog.vehicle_modules.get(model, ())
        native_ai = any(behavior == 'TransportAIUpdate' for behavior, _ in modules)
        removals = ''.join(f'  RemoveModule {tag}\n' for behavior, tag in modules if behavior != 'TransportAIUpdate')
        ai = '' if native_ai else '''   Behavior = TransportAIUpdate ModuleTag_EARTH_BR_TransportAI
   End
'''
        # Locomotor belongs to the AI module. Write it after installing the replacement AI.
        locomotor = '' if native_ai else '  Locomotor = SET_NORMAL EARTH_BR_CivilDrive\n'
        armed = ' CAN_ATTACK' if model in (*HUMVEES[:2], NUKE_TRUCK) else ''
        cabin = '''   Behavior = GarrisonContain ModuleTag_Transport01
    ContainMax = 4
    MobileGarrison = Yes
    AllowInsideKindOf = INFANTRY
    AllowNeutralInside = Yes
    AllowEnemiesInside = No
    PassengersAllowedToFire = Yes
    NumberOfExitPaths = 0
   End
'''
        civilian = model not in (*HUMVEES, NUKE_TRUCK)
        purchase = f'''   Behavior = ProductionUpdate ModuleTag_EARTH_BR_VehicleQueue
    MaxQueueEntries = 1
   End
   Behavior = CommandSetUpgrade ModuleTag_EARTH_BR_OwnScoutVehicle
    TriggeredBy = {VEHICLE_BUY_UPGRADE}
    CommandSet = EARTH_BR_Transport
   End
''' if civilian else ''
        command_set = ('EARTH_BR_CivilTransport' if civilian else
                       'EARTH_BR_NukeTransport' if model == NUKE_TRUCK else 'EARTH_BR_Transport')
        vision = f"  ShroudClearingRange = {opt['br_vehicle_scout_range']}\n" if civilian else ''
        nuke_weapon = '''  WeaponSet
   Conditions = None
   Weapon = PRIMARY ConvoyTruckSuicideWeapon
   AutoChooseSources = PRIMARY NONE
  End
''' if model == NUKE_TRUCK else ''
        parts.append(f"""Object {model}
  KindOf = PRELOAD SELECTABLE CAN_CAST_REFLECTIONS VEHICLE TRANSPORT SCORE{armed}
  CommandSet = {command_set}
{vision}{nuke_weapon}{removals}  AddModule
{ai}{cabin}{purchase}  End
{locomotor}End
""")
    return '\n'.join(parts)


def battle_royale_ini(radius, catalog, structure_models, transport_models, opt):
    parts = ['; Zero Hour Earth - Battle Royale (map-local rules)\n', hero_abilities_ini(opt),
             transport_ini(catalog, transport_models, opt)]
    xp = opt['br_drop_xp']
    # Separate starter templates give only the heroes the larger health pool.
    for family, tag in {'USA': '03', 'China': '02', 'GLA': '02'}.items():
        starter = STARTERS[family]
        hp = opt['br_hp_' + family.lower()]
        # Keep inherited weapon and stealth upgrades from overriding the hero shop.
        stock_tags = {'USA': ('10',), 'China': (), 'GLA': ('07', '09', '10')}[family]
        removals = ''.join(f'  RemoveModule ModuleTag_{stock_tag}\n' for stock_tag in (*stock_tags, CAPTURE[family][2]))
        # Only the explicit equip buttons can lock a purchased weapon.
        alternate_weapons = ''
        if opt['br_enable_rpg']:
            alternate_weapons += ('   Weapon = SECONDARY TunnelDefenderRocketWeapon\n'
                                  '   AutoChooseSources = SECONDARY NONE\n')
        if opt['br_enable_sniper']:
            alternate_weapons += (f'   Weapon = TERTIARY {HERO}SniperRifle\n'
                                  '   AutoChooseSources = TERTIARY NONE\n')
        weapons = ''.join(f"""  WeaponSet
   Conditions = {condition}
   Weapon = PRIMARY {HERO}Rifle{family}
{alternate_weapons}   WeaponLockSharedAcrossSets = Yes
  End
""" for condition in ('None', 'PLAYER_UPGRADE'))
        lock = '''   Behavior = LockWeaponCreate ModuleTag_EARTH_BR_DefaultRifle
    SlotToLock = PRIMARY
   End
''' if family != 'USA' else ''
        parts.append(f"""{hero_rifle_ini(family, opt)}ObjectReskin {starter} {RIFLES[family]}
End

Object {starter}
  CommandSet = {HERO}Main{family}
  KindOf = +HERO +SALVAGER
  IsTrainable = Yes
  ExperienceRequired = 0 {xp} {2 * xp} {3 * xp}
  Locomotor = SET_NORMAL {HERO}Walk{family}
  Locomotor = SET_NORMAL_UPGRADED {HERO}Run
{weapons}  RemoveModule ModuleTag_{tag}
{removals}  AddModule
   Body = ActiveBody ModuleTag_EARTH_BR_StarterBody
    MaxHealth = {hp}
    InitialHealth = {hp}
   End
{lock}{hero_modules(family, opt)}  End
End
""")
    # Use the stock rebel's health, rifle and idle target acquisition.
    parts.append(f'''ObjectReskin {REBEL} GLAInfantryRebel
End

Object {REBEL}
  VisionRange = {opt['br_rebel_range']}
  ShroudClearingRange = {opt['br_rebel_range']}
  IsTrainable = No
  WeaponSet
   Conditions = None
   Weapon = PRIMARY GLARebelMachineGun
  End
End
''')
    parts.append(f'''Object {ELITE_DROP}
  DisplayName = OBJECT:Crate
  KindOf = PARACHUTABLE SELECTABLE ALWAYS_VISIBLE
  RadarPriority = UNIT
  Geometry = BOX
  GeometryMajorRadius = 15
  GeometryMinorRadius = 15
  GeometryHeight = 10
  GeometryIsSmall = Yes
  AddModule
   Draw = W3DModelDraw ModuleTag_EARTH_BR_EliteModel
    DefaultConditionState
     Model = PMJnkCrate
    End
   End
   Draw = W3DModelDraw ModuleTag_EARTH_BR_EliteBeacon
    DefaultConditionState
     Model = SCMBeacon
    End
   End
   Behavior = SalvageCrateCollide ModuleTag_EARTH_BR_ElitePickup
    RequiredKindOf = HERO
    ForbiddenKindOf = PROJECTILE AIRCRAFT
    WeaponChance = 0%
    LevelChance = 100%
    MoneyChance = 0%
    MinMoney = 0
    MaxMoney = 0
    ExecuteFX = FX_CratePickup
   End
  End
End
''')
    pedestrian_rewards = {'GenericMale01': opt['br_civilian_reward'], 'GenericFemale01': opt['br_civilian_reward'],
                          'AmericanFarmer01': opt['br_farmer_reward']}
    for reward in sorted(set(pedestrian_rewards.values()) - {0}):
        crate = f'EARTH_BR_PedestrianCash{reward}'
        parts.append(f"""Object {crate}
  KindOf = CRATE
  Geometry = CYLINDER
  GeometryMajorRadius = 35
  GeometryHeight = 12
  GeometryIsSmall = Yes
  AddModule
   Draw = W3DModelDraw ModuleTag_EARTH_BR_CashModel
    DefaultConditionState
     Model = PMUNCrate03
    End
   End
   Behavior = MoneyCrateCollide ModuleTag_EARTH_BR_Cash
    ForbiddenKindOf = PROJECTILE AIRCRAFT
    ForbidOwnerPlayer = Yes
    MoneyProvided = {reward}
   End
   Behavior = LifetimeUpdate ModuleTag_EARTH_BR_CrateLife
    MinLifetime = 30000
    MaxLifetime = 30000
   End
   Behavior = DestroyDie ModuleTag_EARTH_BR_CrateExpire
   End
  End
End

ObjectCreationList {crate}Drop
  CreateObject
    ObjectNames = {crate}
    Count = 1
    Disposition = LIKE_EXISTING
    IgnorePrimaryObstacle = Yes
  End
End
""")
    for model, reward in pedestrian_rewards.items():
        drop = f"""
  AddModule
   Behavior = CreateObjectDie ModuleTag_EARTH_BR_PedestrianDrop
    CreationList = EARTH_BR_PedestrianCash{reward}Drop
    DeathTypes = ALL
   End
  End""" if reward else ''
        parts.append(f"""Object {model}
  CrushableLevel = 0{drop}
End
""")
    for role, (model, power, ocl) in POI.items():
        name = 'EARTH_BR_' + role
        # Copy the actual garrison building with its door and firing bones.
        special = ''
        button = ''
        slot = ''
        if power:
            parts.append(f'SpecialPower {power}\n  ShortcutPower = No\n  PublicTimer = No\n  SharedSyncedTimer = No\nEnd\n')
            special = f"""   Behavior = OCLSpecialPower ModuleTag_EARTH_BR_POIPower
    SpecialPowerTemplate = {power}
    OCL = {ocl}
    CreateLocation = CREATE_AT_EDGE_NEAR_SOURCE
    StartsPaused = Yes
   End
"""
            button = f"""CommandButton Command_CarpetBomb
  Command = SPECIAL_POWER
  SpecialPower = {power}
  Options = NEED_TARGET_POS CONTEXTMODE_COMMAND
  TextLabel = SCRIPT:{name}Title
  DescriptLabel = SCRIPT:{name}Help
  ButtonBorderType = ACTION
End
"""
            slot = '  1 = Command_CarpetBomb\n'
        parts.append(f"""ObjectReskin {name} {model}
End

Object {name}
  DisplayName = SCRIPT:{name}Title
  CommandSet = {name}
  RemoveModule ModuleTag_03
  AddModule
   Behavior = GarrisonContain ModuleTag_EARTH_BR_POISeats
    ContainMax = 1
    EnterSound = GarrisonEnter
    ExitSound = GarrisonExit
   End
{special}  End
End

{button}
CommandSet {name}
{slot}  7 = Command_StructureExit
  12 = Command_Evacuate
End
""")
    parts.append(f'''Object {ZONE_MARKER}
  KindOf = INERT IMMOBILE UNATTACKABLE NO_COLLIDE
  ArmorSet
   Conditions = None
   Armor = InvulnerableArmor
  End
  AddModule
   Body = ActiveBody ModuleTag_EARTH_BR_MarkerBody
    MaxHealth = 1
    InitialHealth = 1
   End
   Behavior = LifetimeUpdate ModuleTag_EARTH_BR_MarkerLife
    MinLifetime = {1000 * (max(FIRST_RADIATION_WARNING, opt['br_zone_warning']) + 1)}
    MaxLifetime = {1000 * (max(FIRST_RADIATION_WARNING, opt['br_zone_warning']) + 1)}
   End
   Behavior = DestroyDie ModuleTag_EARTH_BR_MarkerDie
   End
  End
End

Object {FIELD}
  KindOf = IMMOBILE INERT NO_COLLIDE UNATTACKABLE
  SoundAmbient = NoSound
  RemoveModule ModuleTag_02
  RemoveModule ModuleTag_04
  RemoveModule ModuleTag_05
  AddModule
   Body = InactiveBody ModuleTag_EARTH_BR_FieldBody
   End
  End
End

Weapon LargeRadiationFieldWeapon
  PrimaryDamage = {opt['br_zone_damage']}
  PrimaryDamageRadius = {radius}
  DamageType = RADIATION
  RadiusDamageAffects = ALLIES ENEMIES NEUTRALS
  DelayBetweenShots = {opt['br_zone_tick']}
End
''')
    parts.append(structure_radiation_ini(catalog, structure_models, opt['br_zone_building_pct']))
    # Keep the engine's original command center as a hidden victory anchor.
    # The same StartingUnit0 substitution is used by Zombie Survival.
    from gconst import ZS_FACTIONS
    for power in ('SuperweaponA10ThunderboltMissileStrike', 'AirF_SuperweaponA10ThunderboltMissileStrike'):
        parts.append(f'SpecialPower {power}\n  ShortcutPower = No\nEnd\n')
    parts.append('CommandSet EARTH_BR_HiddenBase\nEnd\n')
    variants = {'USA': ('', 'AirF_', 'SupW_', 'Lazr_'),
                'China': ('', 'Tank_', 'Nuke_', 'Infa_'),
                'GLA': ('', 'Slth_', 'Demo_', 'Chem_')}
    for family, (factions, _, _) in ZS_FACTIONS.items():
        for faction, variant in zip(factions, variants[family]):
            center = variant + ('America' if family == 'USA' else family) + 'CommandCenter'
            sidebar = ''
            original = variant + 'SpecialPowerShortcutUSA'
            if family == 'USA' and original in catalog.usa_shortcuts:
                command_set = 'EARTH_BR_' + original
                buttons = ''.join(f'  {slot} = {button}\n' for slot, button in catalog.usa_shortcuts[original]
                                  if 'A10ThunderboltMissileStrike' not in button)
                parts.append(f'CommandSet {command_set}\n{buttons}End\n')
                sidebar = f'  SpecialPowerShortcutCommandSet = {command_set}\n'
            parts.append(f'PlayerTemplate Faction{faction}\n  StartingUnit0 = Bush01\n{sidebar}'
                         '  PurchaseScienceCommandSetRank1 = EARTH_BR_HiddenBase\n'
                         '  PurchaseScienceCommandSetRank3 = EARTH_BR_HiddenBase\n'
                         '  PurchaseScienceCommandSetRank8 = EARTH_BR_HiddenBase\nEnd\n')
            parts.append(f'Object {center}\n  CommandSet = EARTH_BR_HiddenBase\n'
                         f'  VisionRange = 0\n  ShroudClearingRange = 0\nEnd\n')
    return '\n'.join(parts)
