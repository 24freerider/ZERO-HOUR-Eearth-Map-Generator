"""Map-local Battle Royale hero abilities and shop."""

from itertools import product
from typing import NamedTuple

from g_brscript import PREFIX
from g_invasion import counter, set_counter
from mapio import Script

HERO = 'EARTH_BR_Hero'
DRONE = HERO + 'DroneUnit'
C4_CHARGE = HERO + 'RemoteCharge'
C4_POWER = 'SpecialAbilityColonelBurtonRemoteCharges'
AIR_POWER = HERO + 'PrecisionPower'
CLUSTER_POWER = HERO + 'ClusterStrikePower'


class WeaponSlot(NamedTuple):
    menu_slot: int
    engine_slot: str
    icon: str
    weapon: str


class KillstreakSlot(NamedTuple):
    menu_slot: int
    action: str
    setting: str
    power: str


WEAPON_SLOTS = {
    'RPG': WeaponSlot(2, 'SECONDARY', 'SURPG', 'TunnelDefenderRocketWeapon'),
    'Sniper': WeaponSlot(3, 'TERTIARY', 'SAPathfinder1', HERO + 'SniperRifle'),
}
KILLSTREAK_SLOTS = {
    'Air': KillstreakSlot(1, 'Strike', 'br_enable_air', AIR_POWER),
    'ClusterBuy': KillstreakSlot(2, 'ClusterStrike', 'br_enable_cluster', CLUSTER_POWER),
}

# Key, icon, English title, Slovak title, English help, Slovak help.
PURCHASES = (
    ('Heal', 'SSRepair2', 'Fast healing', 'Rychle liecenie',
     'Regenerate additional health after disengaging.', 'Mimo boja doplna viac zdravia.'),
    ('Armor', 'SSCompositeArmor', 'Body armor', 'Vesta',
     'Increase maximum and current health.', 'Zvysi maximalne aj aktualne zdravie.'),
    ('C4', 'SSRemoteDemo', 'Buy one C4 charge', 'Kupit jednu C4',
     'Buy one charge for remote demolition.', 'Kupi jednu naloz na dialkovy odpal.'),
    ('Speed', 'SUShoes', 'Adrenaline', 'Adrenalin',
     'Run faster, including while wounded.', 'Zrychli pohyb aj pri zraneni.'),
    ('Stealth', 'SSCamoflage', 'Sniper stealth', 'Ostrelovacske maskovanie',
     'Stay invisible while stationary, even when firing.', 'Ked stojis, zostanes neviditelny aj pri strelbe.'),
    ('Damage', 'SSAPShells', 'Better weapon', 'Lepsia zbran',
     'Increase base rifle damage.', 'Zvysi zakladne poskodenie pusky.'),
    ('Capture', 'SSCaptureBuilding', 'Capture buildings', 'Obsadzovanie budov',
     'Unlock the ability to capture neutral tech buildings.', 'Odomkne obsadzovanie neutralnych technickych budov.'),
    ('Air', 'SSA10Attack', 'Buy airstrike', 'Kupit nalet',
     'Buy one A-10 airstrike, then select its target with the strike button.', 'Kup jeden nalet A-10 a potom oznac ciel tlacidlom naletu.'),
    ('ClusterBuy', 'SNCBomber', 'Buy cluster strike', 'Kupit kazetovy nalet',
     'Buy one cluster strike, then choose a target.', 'Kup jeden kazetovy nalet a potom oznac ciel.'),
    ('Drone', 'SAScout', 'Scout drone', 'Prieskumny dron',
     'Launch a drone that follows you and detects stealth. Buy a replacement if destroyed.',
     'Dron ta sleduje a odhaluje maskovanie. Po zniceni sa da kupit nahrada.'),
    ('Shop', 'SSAdvancedTraining', 'Ability Shop', 'Ability Shop', 'Open the upgrade shop.', 'Otvor obchod s vylepseniami.'),
    ('Weapons', 'SSMachineGun', 'WEAPONS', 'WEAPONS', 'Buy or equip weapons.', 'Kup zbran alebo ju vyber.'),
    ('Killstreaks', 'SSA10Attack', 'Killstreaks', 'Killstreaks', 'Buy and target air support.', 'Kup a zaciel letecku podporu.'),
    ('RPG', 'SURPG', 'Buy RPG', 'Kupit RPG', 'Unlock the RPG in this slot.', 'Odomkne RPG v tomto poli.'),
    ('Sniper', 'SAPathfinder1', 'Buy sniper rifle', 'Kupit ostrelovaciu pusku',
     'Unlock a long-range rifle in this slot.', 'Odomkne pusku s dlhym dosahom v tomto poli.'),
    ('Back', 'SSMineBunker', 'Back', 'Spat', 'Return to your abilities.', 'Vrat sa k schopnostiam.'),
)
CAPTURE = {
    'USA': ('Command_AmericaRangerCaptureBuilding', 'SpecialAbilityRangerCaptureBuilding', '20'),
    'China': ('Command_ChinaInfantryRedGuardCaptureBuilding', 'SpecialAbilityRedGuardCaptureBuilding', '18'),
    'GLA': ('Command_GLAInfantryRebelCaptureBuilding', 'SpecialAbilityRebelCaptureBuilding', '19'),
}
RIFLE_SOUND = {'USA': 'RangerWeapon', 'China': 'RedGuardWeapon', 'GLA': 'RebelWeapon'}


# Map-local overrides inherit the stock buttons' already-loaded images.
HERO_BUTTONS = {
    'Heal': 'Command_EmergencyRepair',
    'Armor': 'Command_UpgradeAmericaCompositeArmor',
    'C4': 'Command_ConstructGLADemoTrap',
    'Speed': 'Command_UpgradeGLAWorkerShoes',
    'Stealth': 'Command_UpgradeGLACamouflage',
    'Damage': 'Command_UpgradeGLAAPBullets',
    'Capture': 'Command_UpgradeAmericaRangerCaptureBuilding',
    'Air': 'AirF_Command_A10ThunderboltMissileStrike',
    'Strike': 'Command_A10ThunderboltMissileStrike',
    'ClusterBuy': 'Command_PurchaseScienceCarpetBomb',
    'ClusterStrike': 'Command_ChinaCarpetBomb',
    'Killstreaks': 'Command_PurchaseScienceA10ThunderboltMissileStrike1',
    'Drone': 'Command_ConstructAmericaVehicleScoutDrone',
    'Shop': 'Command_UpgradeAmericaAdvancedTraining',
    'Weapons': 'Command_AmericaRangerSwitchToMachineGun',
    'Back': 'Command_UpgradeChinaMines',
    'PlaceC4': 'Command_EARTH_BR_PlaceC4',
    'DetonateC4': 'Command_EARTH_BR_DetonateC4',
    'Rifle': 'Command_ConstructAmericaInfantryColonelBurton',
    'RPG': 'Command_ConstructGLAInfantryRPGTrooper',
    'EquipRPG': 'GC_Slth_Command_ConstructGLAInfantryRPGTrooper',
    'Sniper': 'Command_ConstructAmericaInfantryPathfinder',
    'EquipSniper': 'AirF_Command_ConstructAmericaInfantryPathfinder',
}


def menu_states(keys):
    for index, ownership in enumerate(product((False, True), repeat=len(keys))):
        yield index, [key for key, owned in zip(keys, ownership) if owned], [
            key for key, owned in zip(keys, ownership) if not owned]


def hero_menu_keys(opt):
    return {
        'Weapons': [key for key in WEAPON_SLOTS if opt['br_enable_' + key.lower()]],
        'Killstreaks': [key for key, spec in KILLSTREAK_SLOTS.items() if opt[spec.setting]],
    }


def hero_alternate_weapons_ini(opt):
    # Only an explicit equip command may lock a purchased weapon.
    return ''.join(f'   Weapon = {spec.engine_slot} {spec.weapon}\n'
                   f'   AutoChooseSources = {spec.engine_slot} NONE\n'
                   for key, spec in WEAPON_SLOTS.items() if opt['br_enable_' + key.lower()])


def hero_strings(sk, opt):
    strings = {}
    for key, _, en, slovak, en_help, sk_help in PURCHASES:
        price = opt.get('br_price_' + key.lower(), 0)
        title = slovak if sk else en
        strings[f'{HERO}{key}'] = f'${price} - {title}' if price else title
        strings[f'{HERO}{key}Help'] = sk_help if sk else en_help
    strings.update({
        HERO + 'PlaceC4': 'Polozit C4' if sk else 'Place C4',
        HERO + 'PlaceC4Help': 'Pripoji kupenu C4 na budovu alebo vozidlo.' if sk else 'Attach a purchased C4 charge to a building or vehicle.',
        HERO + 'DetonateC4': 'Odpalit C4' if sk else 'Detonate C4',
        HERO + 'DetonateC4Help': 'Odpali vsetky polozene naloze.' if sk else 'Detonate all planted charges.',
        HERO + 'Rifle': 'Puska' if sk else 'Rifle',
        HERO + 'EquipRPG': 'Pouzit RPG' if sk else 'Equip RPG',
        HERO + 'EquipSniper': 'Pouzit ostrelovaciu pusku' if sk else 'Equip sniper rifle',
        HERO + 'Strike': 'Letecky nalet' if sk else 'Airstrike',
        HERO + 'ClusterStrike': 'Kazetovy nalet' if sk else 'Cluster strike',
        HERO + 'ClusterStrikeHelp': 'Oznac miesto kazetoveho naletu.' if sk else 'Choose the target for your purchased cluster strike.',
        HERO + 'StrikeHelp': 'Oznac miesto naletu A-10.' if sk else 'Choose the target for your purchased A-10 strike.',
    })
    return strings


def hero_abilities_ini(opt):
    parts = []
    for key, image, *_ in PURCHASES:
        price = opt.get('br_price_' + key.lower(), 0)
        name = HERO + key
        parts.append(f"""Upgrade Upgrade_{name}
  Type = OBJECT
  BuildTime = {0 if key in ('Shop', 'Weapons', 'Killstreaks', 'Back') else 1}
  BuildCost = {price}
  DisplayName = SCRIPT:{name}
  ButtonImage = {image}
End

CommandButton {HERO_BUTTONS[key]}
  Command = OBJECT_UPGRADE
  Upgrade = Upgrade_{name}
  Options = NOT_QUEUEABLE
  Science = None
  ButtonImage = {image}
  TextLabel = SCRIPT:{name}
  DescriptLabel = SCRIPT:{name}Help
  ButtonBorderType = UPGRADE
End
""")
    for key, power, enum, icon, radius in (
        ('Strike', AIR_POWER, 'SPECIAL_A10_THUNDERBOLT_STRIKE', 'SSA10Attack', opt['br_air_radius']),
        ('ClusterStrike', CLUSTER_POWER, 'SPECIAL_CARPET_BOMB', 'SNCBomber', 2 * opt['br_cluster_radius']),
    ):
        name = HERO + key
        parts.append(f"""SpecialPower {power}
  Enum = {enum}
  ReloadTime = 3000
  PublicTimer = No
  SharedSyncedTimer = No
  ShortcutPower = No
  RadiusCursorRadius = {radius}
End

CommandButton {HERO_BUTTONS[key]}
  Command = SPECIAL_POWER
  SpecialPower = {power}
  Options = NEED_TARGET_POS CONTEXTMODE_COMMAND
  Science = None
  ButtonImage = {icon}
  TextLabel = SCRIPT:{name}
  DescriptLabel = SCRIPT:{name}Help
  ButtonBorderType = ACTION
  RadiusCursorType = A10STRIKE
End
""")
    for button, power, _ in CAPTURE.values():
        parts.append(f"""CommandButton {button}
  Command = SPECIAL_POWER
  SpecialPower = {power}
  Upgrade = Upgrade_{HERO}Capture
  Options = OK_FOR_MULTI_SELECT NEED_TARGET_ENEMY_OBJECT NEED_TARGET_NEUTRAL_OBJECT NEED_UPGRADE
  Science = None
End
""")
    equip_buttons = [('Rifle', 'PRIMARY', 'SABurton')]
    equip_buttons.extend(('Equip' + key, spec.engine_slot, spec.icon) for key, spec in WEAPON_SLOTS.items())
    for key, slot, icon in equip_buttons:
        parts.append(f"""CommandButton {HERO_BUTTONS[key]}
  Command = SWITCH_WEAPON
  WeaponSlot = {slot}
  Options = CHECK_LIKE OK_FOR_MULTI_SELECT
  ButtonImage = {icon}
  TextLabel = SCRIPT:{HERO}{key}
  ButtonBorderType = ACTION
End
""")
    # Leave room for the stock veterancy damage bonus at low custom starting HP.
    lowest_hp = min(opt['br_hp_' + family.lower()] for family in CAPTURE)
    sniper_damage = min(opt['br_sniper_damage'], (lowest_hp - 1) * 2 // 3)
    parts.append(f"""Upgrade Upgrade_{HERO}C4Spent
  Type = OBJECT
End

Upgrade Upgrade_{HERO}AirSpent
  Type = OBJECT
End

Upgrade Upgrade_{HERO}ClusterBuySpent
  Type = OBJECT
End

Weapon BurtonC4ChargeWeapon
  PrimaryDamage = {opt['br_c4_damage']}
  PrimaryDamageRadius = {opt['br_c4_radius']}
  SecondaryDamage = {opt['br_c4_splash_damage']}
  SecondaryDamageRadius = {opt['br_c4_splash_radius']}
End

Weapon A10ThunderboltMissileWeapon
  PrimaryDamage = {opt['br_air_damage']}
  PrimaryDamageRadius = {opt['br_air_radius']}
End

Weapon TunnelDefenderRocketWeapon
  PrimaryDamage = {opt['br_rpg_damage']}
  AttackRange = {opt['br_rpg_range']}
  DelayBetweenShots = {opt['br_rpg_interval']}
End

Weapon {HERO}SniperRifle
  PrimaryDamage = {sniper_damage}
  AttackRange = {opt['br_sniper_range']}
  DamageType = SMALL_ARMS
  DeathType = NORMAL
  WeaponSpeed = 999999
  FireFX = WeaponFX_GenericMachineGunFire
  FireSound = PathfinderWeapon
  DelayBetweenShots = {opt['br_sniper_interval']}
  ClipSize = 0
  AutoReloadsClip = Yes
End

ObjectReskin {C4_CHARGE} RemoteC4Charge
End

CommandButton {HERO_BUTTONS['PlaceC4']}
  Command = SPECIAL_POWER
  SpecialPower = {C4_POWER}
  Upgrade = Upgrade_{HERO}C4
  Options = NEED_UPGRADE NEED_TARGET_ENEMY_OBJECT NEED_TARGET_NEUTRAL_OBJECT NEED_TARGET_ALLY_OBJECT CONTEXTMODE_COMMAND
  ButtonImage = SSRemoteDemo
  CursorName = PlaceRemoteCharge
  InvalidCursorName = PlaceChargeInvalid
  TextLabel = SCRIPT:{HERO}PlaceC4
  DescriptLabel = SCRIPT:{HERO}PlaceC4Help
  ButtonBorderType = ACTION
End

CommandButton {HERO_BUTTONS['DetonateC4']}
  Command = SPECIAL_POWER
  SpecialPower = {C4_POWER}
  ButtonImage = SSDetonate
  TextLabel = SCRIPT:{HERO}DetonateC4
  DescriptLabel = SCRIPT:{HERO}DetonateC4Help
  ButtonBorderType = ACTION
End
""")
    speeds = {family: opt['br_move_' + family.lower()] for family in CAPTURE}
    for name, speed, wounded_ratio in (
        ('WalkUSA', speeds['USA'], .5), ('WalkChina', speeds['China'], .6), ('WalkGLA', speeds['GLA'], .5),
        ('Run', opt['br_speed_upgraded'], .64),
    ):
        parts.append(f"""Locomotor {HERO}{name}
  Surfaces = GROUND RUBBLE
  Speed = {speed}
  SpeedDamaged = {max(1, round(speed * wounded_ratio))}
  TurnRate = 500
  TurnRateDamaged = 500
  Acceleration = 125
  AccelerationDamaged = 75
  Braking = 150
  Appearance = TWO_LEGS
  ZAxisBehavior = NO_Z_MOTIVE_FORCE
  StickToGround = Yes
  GroupMovementPriority = MOVES_FRONT
End
""")
    parts.append(f"""
ObjectReskin {DRONE} AmericaVehicleScoutDrone
End

Object {DRONE}
  VisionRange = {opt['br_drone_detect']}
  ShroudClearingRange = {opt['br_drone_vision']}
  RemoveModule ModuleTag_04
  RemoveModule ModuleTag_12
  AddModule
   Behavior = StealthDetectorUpdate ModuleTag_EARTH_BR_Detect
    DetectionRate = 500
    DetectionRange = {opt['br_drone_detect']}
   End
   Behavior = UpgradeDie ModuleTag_EARTH_BR_DroneDie
    DeathTypes = ALL
    UpgradeToRemove = Upgrade_{HERO}Drone
   End
  End
End

ObjectCreationList {HERO}LaunchDrone
  CreateObject
    ObjectNames = {DRONE}
    Offset = X:-8 Y:0 Z:10
    IgnorePrimaryObstacle = Yes
    Disposition = LIKE_EXISTING
    RequiresLivePlayer = Yes
    Count = 1
  End
End

CommandSet {HERO}Store
  1 = {HERO_BUTTONS['Heal']}
  2 = {HERO_BUTTONS['Armor']}
  4 = {HERO_BUTTONS['Speed']}
  5 = {HERO_BUTTONS['Stealth']}
  6 = {HERO_BUTTONS['Damage']}
  7 = {HERO_BUTTONS['Capture']}
  12 = {HERO_BUTTONS['Back']}
  14 = Command_Stop
End
""")
    for family, (capture, _, _) in CAPTURE.items():
        common = {5: capture, 8: 'Command_AttackMove', 13: 'Command_Guard', 14: 'Command_Stop'}
        if opt['br_enable_c4']:
            common[10] = HERO_BUTTONS['DetonateC4']
        if opt['br_enable_drone']:
            common[4] = HERO_BUTTONS['Drone']
        keys = hero_menu_keys(opt)
        main = common | {11: HERO_BUTTONS['Weapons'], 12: HERO_BUTTONS['Shop']}
        if keys['Killstreaks']:
            main[3] = HERO_BUTTONS['Killstreaks']
        if opt['br_enable_c4']:
            main[1] = HERO_BUTTONS['C4']
        panels = {'Main': main, 'C4Ready': main | {1: HERO_BUTTONS['PlaceC4']}}
        menu_controls = {8: 'Command_AttackMove', 12: HERO_BUTTONS['Back'], 13: 'Command_Guard', 14: 'Command_Stop'}
        for index, owned, locked in menu_states(keys['Weapons']):
            slots = menu_controls | {1: HERO_BUTTONS['Rifle']}
            slots.update({WEAPON_SLOTS[key].menu_slot: HERO_BUTTONS[key] for key in locked})
            slots.update({WEAPON_SLOTS[key].menu_slot: HERO_BUTTONS['Equip' + key] for key in owned})
            panels[f'Weapons{index}'] = slots
        for index, owned, locked in menu_states(keys['Killstreaks']):
            slots = menu_controls.copy()
            slots.update({KILLSTREAK_SLOTS[key].menu_slot: HERO_BUTTONS[key] for key in locked})
            slots.update({KILLSTREAK_SLOTS[key].menu_slot: HERO_BUTTONS[KILLSTREAK_SLOTS[key].action] for key in owned})
            panels[f'Killstreaks{index}'] = slots
        for panel, slots in panels.items():
            buttons = ''.join(f'  {slot} = {button}\n' for slot, button in sorted(slots.items()))
            parts.append(f'CommandSet {HERO}{panel}{family}\n{buttons}End\n')
    return '\n'.join([*parts, hero_strike_ini(opt)])


def hero_strike_ini(opt):
    parts = [f"""Object {HERO}StrikeMarker
  KindOf = IMMOBILE NO_COLLIDE UNATTACKABLE ALWAYS_VISIBLE
  AddModule
   Draw = W3DModelDraw ModuleTag_EARTH_BR_Marker
    DefaultConditionState
     Model = SCMBeacon
    End
   End
   Body = ActiveBody ModuleTag_EARTH_BR_MarkerBody
    MaxHealth = 1
    InitialHealth = 1
   End
   Behavior = LifetimeUpdate ModuleTag_EARTH_BR_MarkerTimer
    MinLifetime = 6500
    MaxLifetime = 6500
   End
   Behavior = DestroyDie ModuleTag_EARTH_BR_MarkerDelete
   End
  End
End
"""]
    name = HERO + 'ClusterStrike'
    offsets = ((0, 0), (-45, -30), (-30, 45), (45, 30), (30, -45))
    parts.append(f"""Weapon {name}Damage
  PrimaryDamage = {opt['br_cluster_damage']}
  PrimaryDamageRadius = {opt['br_cluster_radius']}
  DamageType = EXPLOSION
  DeathType = EXPLODED
  RadiusDamageAffects = ENEMIES NEUTRALS
  DelayBetweenShots = 5000
  FireFX = FX_CarpetBomb
End
""")
    for i in range(len(offsets)):
        impact = f'{name}Impact{i}'
        delay = opt['br_cluster_delay'] + 180 * i
        parts.append(f"""Object {impact}
  KindOf = INERT IMMOBILE NO_COLLIDE UNATTACKABLE
  AddModule
   Body = ActiveBody ModuleTag_EARTH_BR_ImpactBody
    MaxHealth = 1
    InitialHealth = 1
   End
   Behavior = FireWeaponUpdate ModuleTag_EARTH_BR_StrikeDamage
    Weapon = {name}Damage
    InitialDelay = {delay}
   End
   Behavior = LifetimeUpdate ModuleTag_EARTH_BR_ImpactTimer
    MinLifetime = {delay + 250}
    MaxLifetime = {delay + 250}
   End
   Behavior = DestroyDie ModuleTag_EARTH_BR_ImpactDelete
   End
  End
End
""")
    payload = ''.join(f"""  CreateObject
    ObjectNames = {name}Impact{i}
    Offset = X:{x} Y:{y} Z:0
    IgnorePrimaryObstacle = Yes
    Disposition = LIKE_EXISTING
    Count = 1
  End
""" for i, (x, y) in enumerate(offsets))
    parts.append(f"""ObjectCreationList {name}Payload
  CreateObject
    ObjectNames = {HERO}StrikeMarker
    IgnorePrimaryObstacle = Yes
    Disposition = LIKE_EXISTING
    Count = 1
  End
{payload}End
""")
    return '\n'.join(parts)


def hero_rifle_ini(family, opt):
    damage = opt['br_rifle_' + family.lower()]
    delay = opt['br_redguard_interval' if family == 'China' else 'br_rifle_interval']
    return f"""Weapon {HERO}Rifle{family}
  PrimaryDamage = {damage}
  AttackRange = {opt['br_rifle_range']}
  DamageType = SMALL_ARMS
  DeathType = NORMAL
  WeaponSpeed = 999999
  FireFX = WeaponFX_GenericMachineGunFire
  VeterancyFireFX = HEROIC WeaponFX_GenericMachineGunFireWithRedTracers
  FireSound = {RIFLE_SOUND[family]}
  DelayBetweenShots = {delay}
  ClipSize = 0
  AutoReloadsClip = Yes
  WeaponBonus = PLAYER_UPGRADE DAMAGE {100 + opt['br_damage_bonus']}%
End
"""


def hero_modules(family, opt):
    menus = []
    for menu, keys in hero_menu_keys(opt).items():
        for index, owned, locked in menu_states(keys):
            triggers = ' '.join(f'Upgrade_{HERO}{key}' for key in [menu, *owned])
            conflicts = '    ConflictsWith = ' + ' '.join(f'Upgrade_{HERO}{key}' for key in locked) + '\n' if locked else ''
            reset = ['Back', *(key + 'Spent' for key in owned if menu == 'Killstreaks')]
            removals = ' '.join(f'Upgrade_{HERO}{key}' for key in reset)
            menus.append(f"""   Behavior = CommandSetUpgrade ModuleTag_EARTH_BR_{menu}{index}
    TriggeredBy = {triggers}
    RequiresAllTriggers = Yes
{conflicts}    RemovesUpgrades = {removals}
    CommandSet = {HERO}{menu}{index}{family}
   End
""")
    main_command_sets = f"""    CommandSet = {HERO}Main{family}
    CommandSetAlt = {HERO}C4Ready{family}
    TriggerAlt = Upgrade_{HERO}C4
"""
    open_menus = ' '.join(f'Upgrade_{HERO}{key}' for key in ('Shop', 'Weapons', 'Killstreaks'))
    paid_panels = f"""   Behavior = CommandSetUpgrade ModuleTag_EARTH_BR_LoadC4
    TriggeredBy = Upgrade_{HERO}C4
    RemovesUpgrades = Upgrade_{HERO}C4Spent
    CommandSet = {HERO}C4Ready{family}
   End
""" + ''.join(f"""   Behavior = CommandSetUpgrade ModuleTag_EARTH_BR_Spend{key}
    TriggeredBy = Upgrade_{HERO}{key}Spent
    RemovesUpgrades = Upgrade_{HERO}{key} Upgrade_{HERO}{key}Spent {open_menus}
{main_command_sets}   End
""" for key in ('C4', *KILLSTREAK_SLOTS))
    return f"""   Behavior = AutoHealBehavior ModuleTag_EARTH_BR_Regen
    StartsActive = Yes
    StartHealingDelay = {opt['br_heal_delay']}
    HealingDelay = {opt['br_heal_tick']}
    HealingAmount = {opt['br_heal_amount']}
   End
   Behavior = AutoHealBehavior ModuleTag_EARTH_BR_FastHeal
    TriggeredBy = Upgrade_{HERO}Heal
    StartHealingDelay = {opt['br_heal_delay']}
    HealingDelay = {opt['br_heal_tick']}
    HealingAmount = {opt['br_fast_heal']}
   End
   Behavior = MaxHealthUpgrade ModuleTag_EARTH_BR_HeroArmor
    TriggeredBy = Upgrade_{HERO}Armor
    AddMaxHealth = {opt['br_armor_hp']}
    ChangeType = ADD_CURRENT_HEALTH_TOO
   End
   Behavior = LocomotorSetUpgrade ModuleTag_EARTH_BR_HeroSpeed
    TriggeredBy = Upgrade_{HERO}Speed
   End
   Behavior = StealthUpdate ModuleTag_EARTH_BR_HeroStealth
    InnateStealth = No
    StealthDelay = {opt['br_stealth_delay']}
    MoveThresholdSpeed = 3
    StealthForbiddenConditions = MOVING
   End
   Behavior = StealthUpgrade ModuleTag_EARTH_BR_HeroCamouflage
    TriggeredBy = Upgrade_{HERO}Stealth
   End
   Behavior = UnpauseSpecialPowerUpgrade ModuleTag_EARTH_BR_UnlockCapture
    SpecialPowerTemplate = {CAPTURE[family][1]}
    TriggeredBy = Upgrade_{HERO}Capture
   End
   Behavior = WeaponBonusUpgrade ModuleTag_EARTH_BR_HeroDamage
    TriggeredBy = Upgrade_{HERO}Damage
   End
   Behavior = CommandSetUpgrade ModuleTag_EARTH_BR_OpenStore
    TriggeredBy = Upgrade_{HERO}Shop
    RemovesUpgrades = Upgrade_{HERO}Back
    CommandSet = {HERO}Store
   End
{''.join(menus)}   Behavior = CommandSetUpgrade ModuleTag_EARTH_BR_CloseStore
    TriggeredBy = Upgrade_{HERO}Back
    RemovesUpgrades = {open_menus} Upgrade_{HERO}Back
{main_command_sets}   End
   Behavior = ProductionUpdate ModuleTag_EARTH_BR_HeroQueue
    MaxQueueEntries = 1
   End
   Behavior = ObjectCreationUpgrade ModuleTag_EARTH_BR_LaunchDrone
    TriggeredBy = Upgrade_{HERO}Drone
    UpgradeObject = {HERO}LaunchDrone
   End
   Behavior = SpecialAbility ModuleTag_EARTH_BR_C4
    SpecialPowerTemplate = {C4_POWER}
    UpdateModuleStartsAttack = Yes
   End
   Behavior = SpecialAbilityUpdate ModuleTag_EARTH_BR_RemoteC4
    SpecialPowerTemplate = {C4_POWER}
    StartAbilityRange = 0
    SpecialObject = {C4_CHARGE}
    MaxSpecialObjects = {opt['br_c4_limit']}
    SpecialObjectsPersistent = Yes
    SpecialObjectsPersistWhenOwnerDies = No
    AlwaysValidateSpecialObjects = Yes
    UniqueSpecialObjectTargets = No
    UnpackTime = {opt['br_c4_plant']}
    SkipPackingWithNoTarget = Yes
   End
{paid_panels}   Behavior = OCLSpecialPower ModuleTag_EARTH_BR_FlyA10
    SpecialPowerTemplate = {AIR_POWER}
    OCL = SUPERWEAPON_A10ThunderboltMissileStrike1
    CreateLocation = CREATE_AT_EDGE_NEAR_TARGET
    StartsPaused = Yes
   End
   Behavior = UnpauseSpecialPowerUpgrade ModuleTag_EARTH_BR_ArmA10
    SpecialPowerTemplate = {AIR_POWER}
    TriggeredBy = Upgrade_{HERO}Air
   End
   Behavior = OCLSpecialPower ModuleTag_EARTH_BR_CallCluster
    SpecialPowerTemplate = {HERO}ClusterStrikePower
    OCL = {HERO}ClusterStrikePayload
    CreateLocation = CREATE_AT_LOCATION
    StartsPaused = Yes
   End
   Behavior = UnpauseSpecialPowerUpgrade ModuleTag_EARTH_BR_ArmCluster
    SpecialPowerTemplate = {HERO}ClusterStrikePower
    TriggeredBy = Upgrade_{HERO}ClusterBuy
   End
"""


def spend_upgrade(unit, purchase):
    return ('NAMED_RECEIVE_UPGRADE', [('unit', unit), ('upgrade', f'Upgrade_{HERO}{purchase}Spent')])


def hero_purchase_scripts(opt, i, player, starter, joined):
    scripts = []
    for key, spec in KILLSTREAK_SLOTS.items():
        if opt[spec.setting]:
            args = [('unit', starter), ('power', spec.power)]
            scripts.append(Script(f'{PREFIX}Spend{key}{i}', [
                ('NAMED_SET_SPECIAL_POWER_COUNTDOWN', args + [('int', 0)]),
                ('NAMED_STOP_SPECIAL_POWER_COUNTDOWN', args),
                spend_upgrade(starter, key),
            ], conditions=[('PLAYER_TRIGGERED_SPECIAL_POWER_FROM_NAMED',
                            [('side', player), ('power', spec.power), ('unit', starter)])], once=False))
    if opt['br_enable_c4']:
        armed = f'{PREFIX}C4Armed{i}'
        # Spending follows a successful placement. Detonation and lost targets only lower the count.
        for count in range(opt['br_c4_limit'] + 1):
            charge_count = ('PLAYER_HAS_OBJECT_COMPARISON', [
                ('side', player), ('comparison', 2), ('int', count), ('type', C4_CHARGE)])
            if count:
                scripts.append(Script(f'{armed}_Place{count}',
                                      [spend_upgrade(starter, 'C4'), set_counter(armed, count)],
                                      conditions=[counter(joined, 2, 1), counter(armed, 2, count - 1), charge_count],
                                      once=False))
            if count < opt['br_c4_limit']:
                scripts.append(Script(f'{armed}_Clear{count}', [set_counter(armed, count)],
                                      conditions=[counter(joined, 2, 1), counter(armed, 4, count), charge_count],
                                      once=False))
    return scripts
