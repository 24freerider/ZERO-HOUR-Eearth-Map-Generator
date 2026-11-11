"""The map.ini text of the zombie modes (static data and builders)."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class ZombieIniMixin:
    # ----------------------------------------------------------- game modes (Clearance / Zombie)
    ZOMBIE_SIDE = 'SkirmishGLA'      # the GLA AI of the lobby gets this side's scripts and teams (Player.cpp)

    ZOMBIE_STR = {'EARTH_ZRound': 'Round:', 'EARTH_ZAmount': 'Zombies this round:', 'EARTH_InsLeft': 'Insurgents left:',
                  'EARTH_C_MainIntro': 'CLEARANCE: fight your way north through the town and destroy the insurgents\' main base!',
                  'EARTH_C_MainDown': 'The insurgents\' main base is destroyed - the town is yours!',
                  'EARTH_D_Intro': 'DEFENSE: protect the nuclear silo in the centre! Capture it with infantry - then its nuke is yours.',
                  'EARTH_D_Repaired': 'The silo is yours! Its nuclear missile is ready to use - no power needed. It repairs itself when not under attack.',
                  'EARTH_D_Lost': 'The silo is destroyed - radiation everywhere. Game over.',
                  'EARTH_ZAlive': 'Zombies left:',
                  'EARTH_S_NoGLA': 'No zombie master! Add one AI player with the GLA faction in the lobby - without it no zombies come.',
                  'EARTH_ZCleared': 'Wave cleared! The next one comes in a few seconds.',
                  'EARTH_S_Intro': 'ZOMBIE SURVIVAL: no base - buy soldiers in your Humvee. Money for every zombie killed.',
                  'EARTH_S_Down': 'A shop vehicle is down. Its respawn timer has started.',
                  'EARTH_S_RespawnReady': 'Respawn ready: select Respawn Ambush in the left sidebar and click open ground.',
                  'EARTH_S_RespawnTitle': 'Respawn Ambush',
                  'EARTH_S_RespawnTip': 'Bring your Humvee back immediately at the selected position.',
                  'EARTH_ObjDone': 'Objectives done:',
                  'EARTH_ObjIntro': 'Objectives: free the town hall and the church, destroy the arms depot. They are marked on the map.',
                  'EARTH_Obj_hall_Done': 'Town hall freed! Reward: money and reinforcements.',
                  'EARTH_Obj_church_Done': 'Church freed! The hostages are safe. Reward: money and reinforcements.',
                  'EARTH_Obj_depot_Done': 'Arms depot destroyed! Reward: money and reinforcements.'}

    # map.ini of the Zombie mode: the Humvee becomes a shop - it trains infantry inside itself (TransportContain is the
    # exit of ProductionUpdate), the trained units need no barracks. Same idea as "[zombies] wheel of death".
    ZOMBIE_INI = '''; Zero Hour Earth - Zombie mode
; The Humvee is a shop: it trains infantry inside itself (TransportContain is the exit of ProductionUpdate) and sells
; upgrades on a second page (the GLA worker's page-switch upgrades) - the same idea as "[zombies] wheel of death".
Object AmericaVehicleHumvee
  CommandSet = EARTH_HumveeShopCommandSet
  UpgradeCameo1 = Upgrade_AmericaCompositeArmor
  UpgradeCameo2 = Upgrade_GLAJunkRepair
  UpgradeCameo3 = Upgrade_AmericaTOWMissile
  UpgradeCameo4 = Upgrade_AmericaAdvancedTraining
  UpgradeCameo5 = Upgrade_AmericaRangerFlashBangGrenade
  RemoveModule ModuleTag_05
  RemoveModule ModuleTag_08
  RemoveModule ModuleTag_16
  RemoveModule ModuleTag_CratesChange
  AddModule
   Behavior = TransportContain ModuleTag_EARTH_Shop
    PassengersAllowedToFire = Yes
    Slots = 6
    DamagePercentToUnits = 100%
    AllowInsideKindOf = INFANTRY
    ExitDelay = 250
    NumberOfExitPaths = 3
    GoAggressiveOnExit = Yes
   End
   Behavior = ProductionUpdate ModuleTag_EARTH_ShopQueue
    MaxQueueEntries = 9
   End
   Behavior = CommandSetUpgrade ModuleTag_EARTH_Page2
    TriggeredBy = Upgrade_GLAWorkerFakeCommandSet
    RemovesUpgrades = Upgrade_GLAWorkerRealCommandSet
    CommandSet = EARTH_HumveeUpgradeCommandSet
   End
   Behavior = CommandSetUpgrade ModuleTag_EARTH_Page1
    TriggeredBy = Upgrade_GLAWorkerRealCommandSet
    RemovesUpgrades = Upgrade_GLAWorkerFakeCommandSet Upgrade_GLAWorkerRealCommandSet
    CommandSet = EARTH_HumveeShopCommandSet
   End
   Behavior = MaxHealthUpgrade ModuleTag_EARTH_Armor
    TriggeredBy = Upgrade_AmericaCompositeArmor
    AddMaxHealth = 800.0
    ChangeType = ADD_CURRENT_HEALTH_TOO
   End
   Behavior = AutoHealBehavior ModuleTag_EARTH_Repair
    HealingAmount = 10
    HealingDelay = 1000
    TriggeredBy = Upgrade_GLAJunkRepair
   End
  End
End

CommandSet EARTH_HumveeShopCommandSet
  1 = Command_ConstructAmericaInfantryRanger
  2 = Command_ConstructAmericaInfantryMissileDefender
  3 = Command_ConstructAmericaInfantryPathfinder
  4 = Command_ConstructAmericaInfantryColonelBurton
  5 = Command_ConstructChinaInfantryRedguard
  6 = Command_ConstructChinaInfantryTankHunter
  7 = Command_TransportExit
  8 = Command_TransportExit
  9 = Command_TransportExit
  10 = Command_TransportExit
  11 = Command_ConstructGLAInfantryJarmenKell
  12 = Command_Evacuate
  13 = Command_UpgradeGLAWorkerFakeCommandSet
  14 = Command_Stop
End

CommandSet EARTH_HumveeUpgradeCommandSet
  1 = Command_UpgradeAmericaCompositeArmor
  2 = Command_UpgradeGLAJunkRepair
  3 = Command_UpgradeAmericaTOWMissile
  4 = Command_UpgradeAmericaAdvancedTraining
  5 = Command_UpgradeAmericaRangerFlashBangGrenade
  6 = Command_UpgradeAmericaRangerCaptureBuilding
  7 = Command_TransportExit
  8 = Command_TransportExit
  9 = Command_TransportExit
  10 = Command_TransportExit
  11 = Command_AttackMove
  12 = Command_Evacuate
  13 = Command_UpgradeGLAWorkerRealCommandSet
  14 = Command_Stop
End
''' + ''.join(f'''
Object {u}
  Prerequisites
  End
  AddModule
   Behavior = MaxHealthUpgrade ModuleTag_EARTH_InfArmor
    TriggeredBy = Upgrade_AmericaCompositeArmor
    AddMaxHealth = 60.0
    ChangeType = ADD_CURRENT_HEALTH_TOO
   End
  End
End
''' for u in ('AmericaInfantryRanger', 'AmericaInfantryMissileDefender', 'AmericaInfantryPathfinder',
            'AmericaInfantryColonelBurton', 'ChinaInfantryRedguard', 'ChinaInfantryTankHunter', 'GLAInfantryJarmenKell'))

    @staticmethod
    def survival_ini(bounty=10):
        """map.ini additions of ZombieSurvival (on top of ZOMBIE_INI); the zombie tiers come from zombie_tiers_ini()."""
        t = """
; ---------------- ZombieSurvival
; the command center stays in the pen: it pays BOUNTY % of the cost of every kill (as in [zombies] wheel of death)
Object AmericaCommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object AirF_AmericaCommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object Lazr_AmericaCommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object SupW_AmericaCommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object ChinaCommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object Tank_ChinaCommandCenter
  RemoveModule ModuleTag_26 ; the respawn power comes only from the temporary barracks
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object Nuke_ChinaCommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object Infa_ChinaCommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object GLACommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object Slth_GLACommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object Demo_GLACommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

Object Chem_GLACommandCenter
  AddModule
   Behavior = CashBountyPower ModuleTag_EARTH_Bounty
    SpecialPowerTemplate = SpecialAbilityCashBounty1
    Bounty = BOUNTY%
   End
  End
End

SpecialPower Tank_SuperweaponTankParadrop
  Enum = SPECIAL_TANK_PARADROP
  ReloadTime = 2000
  RequiredScience = SCIENCE_TankParadrop1
  PublicTimer = No
  SharedSyncedTimer = Yes
  RadiusCursorRadius = 35
  ShortcutPower = Yes
  AcademyClassify = ACT_SUPERPOWER
End

ObjectCreationList EARTH_OCL_HumveeAmbush
  CreateObject
    ObjectNames = AmericaVehicleHumvee
    Count = 1
    DiesOnBadLand = Yes
    RequiresLivePlayer = Yes
  End
End

CommandButton EARTH_Command_RespawnAmbush
  Command = SPECIAL_POWER_FROM_SHORTCUT
  SpecialPower = Tank_SuperweaponTankParadrop
  Options = NEED_SPECIAL_POWER_SCIENCE NEED_TARGET_POS CONTEXTMODE_COMMAND
  Science = SCIENCE_TankParadrop1
  TextLabel = SCRIPT:EARTH_S_RespawnTitle
  DescriptLabel = SCRIPT:EARTH_S_RespawnTip
  ButtonImage = SSGLAAmbush
  RadiusCursorType = AMBUSH
  InvalidCursorName = GenericInvalid
End

; A temporary power source created off-map for the downed player's own team.
Object AmericaBarracks
  KindOf = PRELOAD STRUCTURE SELECTABLE IMMOBILE UNATTACKABLE
  AddModule
   Behavior = OCLSpecialPower ModuleTag_EARTH_Respawn
    SpecialPowerTemplate = Tank_SuperweaponTankParadrop
    OCL = EARTH_OCL_HumveeAmbush
    CreateLocation = CREATE_AT_LOCATION
    OCLAdjustPositionToPassable = Yes
   End
  End
End
"""
        # Replace the stock drop shortcuts or use a free slot in the faction's sidebar.
        for faction, slots in (
            ('USA', dict.fromkeys(('', 'AirF_', 'SupW_', 'Lazr_'), 2)),
            ('China', {'': 9, 'Nuke_': 9, 'Infa_': 9, 'Tank_': 3}),
            ('GLA', dict.fromkeys(('', 'Demo_', 'Slth_', 'Chem_'), 8)),
        ):
            for prefix, slot in slots.items():
                t += f'\nCommandSet {prefix}SpecialPowerShortcut{faction}\n'
                t += f'  {slot} = EARTH_Command_RespawnAmbush\nEnd\n'
        t += ZombieIniMixin.survival_shops_ini()
        return t.replace('BOUNTY', str(int(bounty)))

    MIG_INI = """
; ---------------- "[zombies] wheel of death": scripted MiG crash
Object ChinaJetMIG
  RemoveModule ModuleTag_03
  AddModule
   Behavior = JetSlowDeathBehavior ModuleTag_Fish01
    FXOnGroundDeath = FX_JetOnGroundDeath
    OCLOnGroundDeath = OCL_MIGDeathFinalBlowUp
    DestructionDelay = 99999999
    RollRate = 0.2
    RollRateDelta = 100%
    PitchRate = 0.0
    FallHowFast = 110.0%
    FXInitialDeath = FX_MigDeathInitial
    OCLInitialDeath = OCL_MIGDeathInitial
    DelaySecondaryFromInitialDeath = 500
    FXSecondary = FX_JetDeathSecondary
    OCLSecondary = OCL_MIGDeathSecondary
    FXHitGround = FX_JetDeathHitGround
    OCLHitGround = OCL_MIGDeathHitGround
    DelayFinalBlowUpFromHitGround = 200
    FXFinalBlowUp = FX_JetDeathFinalBlowUp
    OCLFinalBlowUp = {final_ocl}
   End
  End
End
"""

    MIG_ZOMBIES_INI = """
ObjectCreationList EARTH_OCL_MigZombies
  CreateObject
    ObjectNames = CINE_GLAInfantryTerrorist
    IgnorePrimaryObstacle = Yes
    Disposition = SEND_IT_OUT
    DispositionIntensity = 4
    Count = 3
    RequiresLivePlayer = Yes
  End
End
"""

    DEFENSE_INI = """
; ---------------- ZombieSurvival Defense: the silo can be captured (it stays yours and its nuke needs no power),
; it slowly repairs itself when nobody hits it, and leaves radiation when it falls
Object ChinaNuclearMissileLauncher
  KindOf = PRELOAD STRUCTURE SELECTABLE IMMOBILE SCORE CAPTURABLE FS_SUPERWEAPON
  EnergyProduction = 0                ; captured silo uses no power - no 'low power' for the new owner
  AddModule
   Behavior = CreateObjectDie ModuleTag_EARTH_Radiation
    CreationList = OCL_RadiationFieldLarge
   End
   Behavior = AutoHealBehavior ModuleTag_EARTH_SelfRepair
    StartsActive = Yes
    HealingAmount = 10
    HealingDelay = 1000
    StartHealingDelay = 8000
   End
  End
End
"""

    @staticmethod
    def full_zombie_ini(survival=False, bounty=10, growing=False, defense=False, lifetime=True, central_tunnel=False,
                        continuous=False):
        """The whole map.ini of the zombie modes."""
        t = ZombieIniMixin.ZOMBIE_INI
        if central_tunnel:
            t += '\nObject GLASneakAttackTunnelNetwork\n  KindOf = PRELOAD STRUCTURE IMMOBILE UNATTACKABLE\nEnd\n'
        if defense: t += ZombieIniMixin.DEFENSE_INI
        if survival:
            t = t.replace('CommandSet = EARTH_HumveeShopCommandSet', 'CommandSet = EARTH_HumveeSurvivalSet')   # both pages
            t += ZombieIniMixin.survival_ini(bounty)
            # Continuous spawns must all go through the population gate.
            final_ocl = 'OCL_MIGDeathFinalBlowUp' if continuous else 'EARTH_OCL_MigZombies'
            t += ZombieIniMixin.MIG_INI.format(final_ocl=final_ocl)
            if not continuous:
                t += ZombieIniMixin.MIG_ZOMBIES_INI
        if survival or growing:
            t += ZombieIniMixin.zombie_tiers_ini(costs=survival, defense=defense, lifetime=lifetime)   # costs: the bounty
        return t

    @staticmethod
    def survival_shops_ini():
        """ZombieSurvival: no dozer / workers at the start, and the Troop Crawler and Battle Bus become shops like the
        Humvee. Every shop sells the same specialists (Pathfinder, Jarmen Kell, Mini-Gunner, Col. Burton) plus the
        faction's basic and anti-tank soldier; page 2 = the faction's upgrades."""
        t = "\n; ---------------- ZombieSurvival: no dozer or workers at the start (the command center stays off the map)\n"
        for f in ('FactionAmerica', 'FactionAmericaAirForceGeneral', 'FactionAmericaSuperWeaponGeneral',
                  'FactionAmericaLaserGeneral', 'FactionChina', 'FactionChinaTankGeneral', 'FactionChinaNukeGeneral',
                  'FactionChinaInfantryGeneral', 'FactionGLA', 'FactionGLAStealthGeneral', 'FactionGLADemolitionGeneral',
                  'FactionGLAToxinGeneral'):
            t += f"PlayerTemplate {f}\n  StartingUnit0 = Bush01\nEnd\n"
        common = ['Command_ConstructAmericaInfantryPathfinder', 'Command_ConstructGLAInfantryJarmenKell',
                  'Infa_Command_ConstructChinaInfantryMiniGunner', 'Command_ConstructAmericaInfantryColonelBurton']
        shops = {
            'ChinaVehicleTroopCrawler': ('Crawler', ['ModuleTag_06', 'ModuleTag_12'],
                                         ['Command_ConstructChinaInfantryRedguard', 'Command_ConstructChinaInfantryTankHunter'],
                                         ['Command_UpgradeAmericaCompositeArmor', 'Command_UpgradeGLAJunkRepair',
                                          'Command_UpgradeChinaNationalism', 'Command_UpgradeChinaFanaticism',
                                          'Command_UpgradeChinaRedguardCaptureBuilding']),
            'GLAVehicleBattleBus': ('Bus', ['ModuleTag_10', 'ModuleTag_25'],
                                    ['Command_ConstructGLAInfantryRebel', 'Command_ConstructGLAInfantryRPGTrooper'],
                                    ['Command_UpgradeAmericaCompositeArmor', 'Command_UpgradeGLAJunkRepair',
                                     'Command_UpgradeGLAAPBullets', 'Command_UpgradeGLACamoNetting',
                                     'Command_UpgradeGLARebelCaptureBuilding'])}
        for obj, (tag, remove, basic, ups) in shops.items():
            t += f"""
Object {obj}
  CommandSet = EARTH_{tag}ShopCommandSet
""" + ''.join(f"  RemoveModule {r}\n" for r in remove) + f"""  AddModule
   Behavior = TransportContain ModuleTag_EARTH_Shop
    PassengersAllowedToFire = Yes
    Slots = 6
    DamagePercentToUnits = 100%
    AllowInsideKindOf = INFANTRY
    ExitDelay = 250
    NumberOfExitPaths = 3
    GoAggressiveOnExit = Yes
   End
   Behavior = ProductionUpdate ModuleTag_EARTH_ShopQueue
    MaxQueueEntries = 9
   End
   Behavior = CommandSetUpgrade ModuleTag_EARTH_Page2
    TriggeredBy = Upgrade_GLAWorkerFakeCommandSet
    RemovesUpgrades = Upgrade_GLAWorkerRealCommandSet
    CommandSet = EARTH_{tag}UpgradeCommandSet
   End
   Behavior = CommandSetUpgrade ModuleTag_EARTH_Page1
    TriggeredBy = Upgrade_GLAWorkerRealCommandSet
    RemovesUpgrades = Upgrade_GLAWorkerFakeCommandSet Upgrade_GLAWorkerRealCommandSet
    CommandSet = EARTH_{tag}ShopCommandSet
   End
   Behavior = MaxHealthUpgrade ModuleTag_EARTH_Armor
    TriggeredBy = Upgrade_AmericaCompositeArmor
    AddMaxHealth = 800.0
    ChangeType = ADD_CURRENT_HEALTH_TOO
   End
   Behavior = AutoHealBehavior ModuleTag_EARTH_Repair
    HealingAmount = 10
    HealingDelay = 1000
    TriggeredBy = Upgrade_GLAJunkRepair
   End
  End
End

CommandSet EARTH_{tag}ShopCommandSet
  1 = {basic[0]}
  2 = {basic[1]}
  3 = {common[0]}
  4 = {common[1]}
  5 = {common[2]}
  6 = {common[3]}
  7 = Command_TransportExit
  8 = Command_TransportExit
  9 = Command_TransportExit
  10 = Command_TransportExit
  11 = Command_AttackMove
  12 = Command_Evacuate
  13 = Command_UpgradeGLAWorkerFakeCommandSet
  14 = Command_Stop
End

CommandSet EARTH_{tag}UpgradeCommandSet
""" + ''.join(f"  {k + 1} = {u}\n" for k, u in enumerate(ups)) + """  7 = Command_TransportExit
  8 = Command_TransportExit
  9 = Command_TransportExit
  10 = Command_TransportExit
  11 = Command_AttackMove
  12 = Command_Evacuate
  13 = Command_UpgradeGLAWorkerRealCommandSet
  14 = Command_Stop
End
"""
        # the Humvee shop: same line-up (replaces the command set of ZOMBIE_INI in full_zombie_ini)
        t += """
CommandSet EARTH_HumveeSurvivalSet
  1 = Command_ConstructAmericaInfantryRanger
  2 = Command_ConstructAmericaInfantryMissileDefender
""" + ''.join(f"  {k + 3} = {c}\n" for k, c in enumerate(common)) + """  7 = Command_TransportExit
  8 = Command_TransportExit
  9 = Command_TransportExit
  10 = Command_TransportExit
  11 = Command_AttackMove
  12 = Command_Evacuate
  13 = Command_UpgradeGLAWorkerFakeCommandSet
  14 = Command_Stop
End
"""
        for u in ('GLAInfantryRebel', 'GLAInfantryTunnelDefender', 'Infa_ChinaInfantryMiniGunner'):
            t += f"""
Object {u}
  Prerequisites
  End
  AddModule
   Behavior = MaxHealthUpgrade ModuleTag_EARTH_InfArmor
    TriggeredBy = Upgrade_AmericaCompositeArmor
    AddMaxHealth = 60.0
    ChangeType = ADD_CURRENT_HEALTH_TOO
   End
  End
End
"""
        return t

    @staticmethod
    def zombie_tiers_ini(costs=True, defense=False, lifetime=True):
        """Growing zombies = the zombie types of "[zombies] wheel of death" v5-6 by DrGoldFish (see ZS_TIERS), copied
        with their speeds, health, the burning CINE zombie, the 3000 HP boss with a knife and the zombie upgrades."""
        t = """
; zombies as in "[zombies] wheel of death" v5-6 by DrGoldFish
Weapon EARTH_ZombieKnife
  PrimaryDamage = 500.0
  PrimaryDamageRadius = 0.0
  AttackRange = 2.0
  DamageType = EXPLOSION
  DeathType = NORMAL
  WeaponSpeed = 999999.0
  ProjectileObject = NONE
  FireFX = NONE
  FireSound = HeroUSAKnifeAttack
  DelayBetweenShots = 0
  ClipSize = 1
  ClipReloadTime = 500
  PreAttackType = PER_ATTACK
End

Weapon EARTH_ZombieShockwave
  PrimaryDamage = 10
  PrimaryDamageRadius = 50.0
  AttackRange = 30.0
  ShockWaveAmount = 250
  ShockWaveRadius = 70
  ShockWaveTaperOff = 0.6
  DamageType = MELEE
  DeathType = NORMAL
  WeaponSpeed = 99999.0
  ProjectileObject = NONE
  RadiusDamageAffects = ALLIES ENEMIES NEUTRALS
  DelayBetweenShots = 99999
  ClipSize = 2
  ClipReloadTime = 2500
  AutoReloadsClip = No
End

Armor EARTH_ZombieBossArmor
  Armor = CRUSH 200%
  Armor = ARMOR_PIERCING 10%
  Armor = SNIPER 100%
  Armor = EXPLOSION 0%
  Armor = FLAME 25%
  Armor = LASER 25%
  Armor = POISON 0%
  Armor = RADIATION 0%
  Armor = MICROWAVE 0%
  Armor = PARTICLE_BEAM 150%
  Armor = HAZARD_CLEANUP 0%
  Armor = KILL_PILOT 0%
  Armor = SURRENDER 100%
  Armor = SUBDUAL_MISSILE 0%
  Armor = SUBDUAL_VEHICLE 0%
  Armor = SUBDUAL_BUILDING 0%
End
"""
        locos = {}
        for row in ZS_TIERS:
            for lc in (row[2], row[3]):
                if lc and lc not in locos:
                    locos[lc] = f'EARTH_ZLoco{lc[0]}_{lc[1]}'
                    t += f"""
Locomotor {locos[lc]}
  Surfaces = GROUND RUBBLE
  Speed = {lc[0]}
  SpeedDamaged = {lc[1]}
  TurnRate = 500
  TurnRateDamaged = 500
  Acceleration = 150
  AccelerationDamaged = 75
  Braking = 150
  MinTurnSpeed = 0
  ZAxisBehavior = NO_Z_MOTIVE_FORCE
  Appearance = TWO_LEGS
  StickToGround = Yes
  GroupMovementPriority = MOVES_FRONT
End
"""
        vision = 300 if defense else 5000
        for name, scale, lc, up, hp, cost, dyn, knife, extra in ZS_TIERS:
            o = f"\nObject {name}\n" + (f"  BuildCost = {cost}\n" if costs else "")
            o += f"  Scale = {scale}\n  VisionRange = {vision}\n  ShroudClearingRange = {max(vision, 600)}\n"
            o += "  KindOf = PRELOAD SELECTABLE CAN_ATTACK CAN_CAST_REFLECTIONS INFANTRY SALVAGER SCORE\n"
            o += "  Prerequisites\n  End\n"
            o += f"  Locomotor = SET_NORMAL {locos[lc]} ColonelBurtonCliffLocomotor\n"
            if up:
                o += f"  Locomotor = SET_NORMAL_UPGRADED {locos[up]} ColonelBurtonCliffLocomotor\n"
            if dyn or extra == 'boss':
                o += "  RemoveModule ModuleTag_Death04\n"
            if extra == 'boss':
                o += "  RemoveModule ModuleTag_Death05\n"
                o += "  ArmorSet\n    Conditions = None\n    Armor = EARTH_ZombieBossArmor\n    DamageFX = InfantryDamageFX\n  End\n"
            if knife:
                o += "  WeaponSet\n    Conditions = None\n    Weapon = PRIMARY EARTH_ZombieKnife\n  End\n"
            if extra == 'cine':
                o += "  RemoveModule ModuleTag_01\n"
            if name.startswith('GC_Slth'):
                o += "  RemoveModule ModuleTag_15\n"      # as in wheel of death (no stealth)
            add = ""
            if hp:
                o += "  RemoveModule ModuleTag_02\n"
                add += f"   Body = ActiveBody ModuleTag_EARTH_ZBody\n    MaxHealth = {hp}.0\n    InitialHealth = {hp}.0\n   End\n"
            if extra == 'cine':
                add += """   Draw = W3DModelDraw ModuleTag_EARTH_Burn
    DefaultConditionState
      Model = CIBURN_SKN
      Animation = CIBURN_SKL.CIBURN_RNA
      AnimationMode = LOOP
      Flags = RANDOMSTART
      ParticleSysBone = Bone_FX1 FireInfantrySmall
    End
    ConditionState = DYING
      Animation = CIBURN_SKL.CIBURN_FLA
      AnimationMode = ONCE
      Flags = START_FRAME_FIRST
      ParticleSysBone = Bone_FX1 FireInfantrySmallSub1
      ParticleSysBone = Bone_FX2 FireInfantrySmallSub2
      ParticleSysBone = Bone_FX9 FireInfantrySmallSub3
      ParticleSysBone = Bone_FX3 FireInfantrySmallSub4
      ParticleSysBone = Bone_FX5 FireInfantrySmallSub5
      ParticleSysBone = Bone_FX6 FireInfantrySmallSub6
      ParticleSysBone = Bone_FX5 FireInfantrySmallSub7
      ParticleSysBone = Bone_FX8 FireInfantrySmallSub8
      ParticleSysBone = Bone_FX7 FireInfantrySmallSub9
      ParticleSysBone = Bone_FX8 FireInfantrySmallSub10
      AnimationSpeedFactorRange = 0.85 1.15
    End
   End
   Behavior = FireWeaponWhenDeadBehavior ModuleTag_EARTH_Shock
    DeathWeapon = EARTH_ZombieShockwave
    StartsActive = Yes
    DeathTypes = ALL
   End
"""
            if dyn:
                add += """   Behavior = FireWeaponWhenDeadBehavior ModuleTag_EARTH_Boom
    DeathWeapon = SuicideDynamitePack
    StartsActive = Yes
    DeathTypes = NONE +SUICIDED
   End
"""
            if up:
                add += """   Behavior = MaxHealthUpgrade ModuleTag_EARTH_Armour
    TriggeredBy = Upgrade_AmericaCompositeArmor
    AddMaxHealth = 30.0
    ChangeType = ADD_CURRENT_HEALTH_TOO
   End
   Behavior = LocomotorSetUpgrade ModuleTag_EARTH_Engine
    TriggeredBy = Upgrade_ChinaNuclearTanks
   End
"""
            if name in ZS_GRANTS:
                add += f"""   Behavior = GrantUpgradeCreate ModuleTag_EARTH_Grant
    UpgradeToGrant = {ZS_GRANTS[name]}
   End
"""
            if lifetime:          # Clearance + Zombie: no time limit - a zombie lives until it is killed
                add += """   Behavior = LifetimeUpdate ModuleTag_EARTH_Life
    MinLifetime = 240000
    MaxLifetime = 300000
   End
"""
            t += o + "  AddModule\n" + add + "  End\nEnd\n"
        return t
