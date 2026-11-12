"""Battle Royale vehicle roster, placement, ownership scripts and INI rules."""
import math
from itertools import cycle, islice

import numpy as np

from g_brscript import NEUTRAL, PREFIX, transfer
from g_invasion import counter
from gconst import BORDER, CELL, PAVED_STREETS, UNPAVED_STREETS
from mapio import Script, ScriptGroup


HUMVEES = ('Humvee1', 'AmericaVehicleHumvee', 'AmericaVehicleMedic')
NUKE_TRUCK = 'ConvoyTruckArmedWithNuke'
VEHICLE_BUY_UPGRADE = 'Upgrade_EARTH_BR_BuyScoutVehicle'
START_CAR_DISTANCE = 340
# Stock civilian road vehicles, including utility, emergency and farm vehicles.
CIVIL_VEHICLES = (
    'CarSedan01', 'CarCompact', 'CarCamaro', 'CarSport01', 'CarSport02', 'CarSUV01',
    'CarScrap', 'CarLimo1', 'CarLimo2', 'CarLimo3', 'ShiekLimo',
    'CarLuxury01', 'CarLuxury02', 'CarLuxury03', 'CarLuxury04',
    'CarTaxiCab01', 'CarTaxiCab02', 'CarTaxiCab03', 'CarTaxiCab04', 'CarTaxiCab05',
    'CarBug1', 'CarBug2', 'CarBug3', 'CarSwatchEuro', 'CarAsian1', 'CarAsian2',
    'CarEuroVan1', 'CarEuroVan2', 'CarEuroPoliceVan', 'PoliceCar', 'CarAmbulance', 'Firetruck',
    'TourBus', 'DoubleDeckerTourBus', 'BigRig', 'TankerTruck', 'TruckWork', 'TruckWork2',
    'TruckChicken', 'TruckFarmer', 'FarmerTruck', 'FarmerChickenTruck', 'SupplyTruck',
    'Tractor', 'TractorBackhoe', 'TractorCombine', 'ForkliftSmall', 'ForkliftLarge',
    'Scooter', 'Rickshaw',
)



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


class BattleRoyaleVehicleMixin:
    def br_place_vehicles(self):
        self.br_vehicles = []
        civilian = [model for model in CIVIL_VEHICLES if not self.known_objects or model in self.known_objects]
        humvees = [model for model in HUMVEES if not self.known_objects or model in self.known_objects]
        nuke_trucks = self.opt['br_nuke_truck_count'] if not self.known_objects or NUKE_TRUCK in self.known_objects else 0
        self.br_transport_models = (*humvees, *civilian, *((NUKE_TRUCK,) if nuke_trucks else ()))
        pool = self.br_points(18, edge=75, dense=True)
        if not len(pool):
            return
        self.rng.shuffle(civilian)
        civilian_models = iter(islice(cycle(civilian), self.opt['br_vehicle_count']))
        parking = [[self.p.to_units(*ll) for ll in ring] for kind, ring in self.osm.areas if kind == 'parking']
        parking_mask = self.raster_poly(parking)
        preferred = parking_mask[(pool[:, 1] / CELL).astype(int) + BORDER,
                                 (pool[:, 0] / CELL).astype(int) + BORDER]
        roadside = self.br_road_points(pool, PAVED_STREETS + UNPAVED_STREETS)
        street_parking = preferred | roadside
        quality = np.where(preferred, 1.9, np.where(roadside, 1.4, 1.0))
        distances = np.full(len(pool), max(self.AX, self.AY) ** 2)

        def place(model, j):
            x, y = pool[j]
            name = f'{PREFIX}Vehicle{len(self.br_vehicles)}'
            self.add_obj(model, x, y, self.rng.random() * math.tau, 0, name, self.base_props, 'team' + NEUTRAL)
            self.br_vehicles.append((name, model, (x, y)))
            self.occ_poly(self.rect(x, y, 55, 55, 0), True)
            np.minimum(distances, ((pool - pool[j]) ** 2).sum(axis=1), out=distances)

        for drop in self.br_drops:
            from_drop = ((pool - drop) ** 2).sum(axis=1)
            nearby = (from_drop <= START_CAR_DISTANCE ** 2) & (distances >= 55 ** 2)
            if nearby.any():
                model = next(civilian_models, None)
                if model is None:
                    break
                parking_nearby = nearby & street_parking
                viable = parking_nearby if parking_nearby.any() else nearby
                place(model, np.where(viable, from_drop, np.inf).argmin())
        military = [NUKE_TRUCK] * nuke_trucks + list(islice(cycle(humvees), self.opt['br_humvee_count']))
        for model in military:
            viable = distances >= 55 ** 2
            if not viable.any():
                break
            place(model, np.where(viable, distances * quality, -1).argmax())
        for model in civilian_models:
            viable = (distances >= 55 ** 2) & street_parking
            if not viable.any():
                break
            place(model, np.where(viable, distances * quality, -1).argmax())

    def br_transport_scripts(self, name, players):
        scan, purchases = name + '_CheckPurchase', name + '_Purchases'
        neutral = ('NAMED_OWNED_BY_PLAYER', [('unit', name), ('side', NEUTRAL)])
        alive = ('NAMED_NOT_DESTROYED', [('unit', name)])
        empty = ('NAMED_BUILDING_IS_EMPTY', [('unit', name)])
        purchase_checks, keep_car = [], []
        for i, player in enumerate(players):
            keep = f'{name}_Keep{i}'
            buyer_alive = counter(f'{PREFIX}Alive{i}', 2, 1)
            completed = ('PLAYER_BUILT_UPGRADE_FROM_NAMED', [
                ('side', player), ('upgrade', VEHICLE_BUY_UPGRADE), ('unit', name)])
            purchase_checks.append(Script(f'{purchases}{i}', [
                ('DISABLE_SCRIPT', [('script', scan)]),
                ('ENABLE_SCRIPT', [('script', keep)]),
            ], conditions=[buyer_alive, completed]))
            # The first emptying clears GarrisonContain's original team. Future riders
            # record the permanent buyer as the owner and return the car to them on exit.
            keep_car.append(Script(keep, [transfer(name, player)], active=False,
                                   conditions=[alive, empty, neutral, buyer_alive]))
        return [Script(scan, [('CALL_SUBROUTINE', [('subroutine', purchases)])],
                       conditions=[alive], once=False, delay=1),
                ScriptGroup(purchases, purchase_checks), *keep_car]
