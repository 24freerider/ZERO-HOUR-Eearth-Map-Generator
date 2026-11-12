"""Free-for-all Battle Royale: arena layout, civilian units and zone scripts."""
import math
from itertools import cycle, islice

import numpy as np

import geo
from g_brhero import AIR_POWER, C4_CHARGE, CLUSTER_POWER, HERO, hero_strings
from g_brini import (ELITE_DROP, FIELD, HUMVEES, NUKE_TRUCK, POI, REBEL, RIFLES, STARTERS,
                     VEHICLE_BUY_UPGRADE, ZONE_MARKER, battle_royale_ini)
from g_brsettings import BR_COUNTS, FIRST_RADIATION_WARNING
from g_invasion import counter, expired, relations, set_counter, timer
from gconst import BORDER, CELL, INS_SIDE, INS_TEAM, MAX_PLAYERS, OCC, PAVED_STREETS, UNPAVED_STREETS, ZS_FACTIONS
from mapio import MapObject, Script, ScriptGroup, write_sides


PREFIX = 'EARTH_BR_'
NEUTRAL = 'EarthBRNeutral'
STORM = 'EarthBRStorm'
DROPS = 'EarthBRAirDrops'
ARENA = PREFIX + 'Arena'
ZONE = PREFIX + 'Zone'
CLOCK = PREFIX + 'Clock'
WARNING = PREFIX + 'ZoneWarning'
RADAR_BEACON = 5
ZONE_PING_TIMERS = {f'{PREFIX}ZonePing{seconds}': seconds for seconds in (2, 4, 6)}
PEOPLE = 'EarthBRCivilians'
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


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def wp(i):
    return f'{PREFIX}Drop{i}'


def no_units(player):
    # Count by template, including infantry in captured buildings.
    return [('PLAYER_HAS_OBJECT_COMPARISON',
             [('side', player), ('comparison', 2), ('int', 0), ('type', model)])
            for model in (*STARTERS.values(), *RIFLES.values())]


def spawn(model, team, waypoint, name=None):
    args = [('type', model), ('team', team), ('waypoint', waypoint)]
    if name is not None:
        return ('CREATE_NAMED_ON_TEAM_AT_WAYPOINT', [('unit', name)] + args)
    return ('CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT', args)


def transfer(name, player):
    return ('NAMED_TRANSFER_OWNERSHIP_PLAYER', [('unit', name), ('side', player)])


def message(key):
    return ('DISPLAY_TEXT', [('text', f'SCRIPT:{PREFIX}{key}')])


def radar_ping(name):
    return ('OBJECT_CREATE_RADAR_EVENT', [('unit', name), ('radar_event', RADAR_BEACON)])


def spend_upgrade(unit, purchase):
    return ('NAMED_RECEIVE_UPGRADE', [('unit', unit), ('upgrade', f'Upgrade_{HERO}{purchase}Spent')])


class BattleRoyaleMixin:
    def br_waypoint(self, name, x, y):
        self.next_wp += 1
        values = {'uniqueID': name, 'objectName': name, 'waypointName': name, 'waypointID': self.next_wp}
        props = [(k, values.get(self.sid[k >> 8], v)) for k, v in self.wp_props]
        self.objs.append(MapObject(3, float(x), float(y), 0.0, 0.0, 0, '*Waypoints/Waypoint', props))

    def br_points(self, clearance, edge=55, dense=False):
        # Use the same occupied grid as buildings: no footprints, roads, or water at the insertion point.
        blocked = self.occ.reshape(self.WY, OCC, self.WX, OCC).any(axis=(1, 3))
        free = geo.box_mean(blocked, math.ceil(clearance / CELL)) == 0
        slope = np.maximum(np.abs(self.H - np.roll(self.H, 1, axis=0)),
                           np.abs(self.H - np.roll(self.H, 1, axis=1)))
        free &= geo.box_mean(slope > 5.5, 2) == 0
        inset = BORDER + math.ceil(edge / CELL)
        span = max(self.NX, self.NY)
        stride = math.ceil(span / 420) if dense else max(2, math.ceil(span / 280))
        y, x = np.nonzero(free[inset:-inset:stride, inset:-inset:stride])
        return (np.column_stack((x, y)) * stride + inset - BORDER).astype(float) * CELL

    def br_layout(self):
        required = set(RIFLES.values()) | {
            FIELD, 'TechOilDerrick', 'TechHospital', 'GLADemoTrap', 'AmericaVehicleScoutDrone',
        }
        missing = required - self.known_objects if self.known_objects else set()
        if missing:
            raise ValueError('Battle Royale: missing stock Zero Hour objects: ' + ', '.join(sorted(missing)))
        self.area_triggers.append((ARENA, rect(0, 0, self.AX, self.AY)))
        ground = self.br_points(18)
        # Keep a hard, scale-aware minimum; reject a map if it cannot meet the published threshold.
        minimum = max(210, min(600, int(0.54 * math.sqrt(self.AX * self.AY / self.players))))
        self.br_enemy_min = max(180, min(330, int(minimum * 0.64)))
        self.br_spawn_min = minimum
        if len(ground) < self.players:
            raise ValueError('Battle Royale: not enough dry open ground. Select a larger area or fewer players.')

        # Start closer to the centre without reducing the minimum distance between players.
        street = self.br_road_points(ground)
        eligible = ground[street]
        if len(eligible) < self.players * 10:
            eligible = ground
        midpoint = np.array((self.AX, self.AY)) / 2
        central = (np.abs((ground - midpoint) / midpoint) <= .75).all(axis=1)
        best = []
        for candidates in (ground[street & central], ground[central], eligible, ground):
            if len(candidates) < self.players:
                continue
            edge_distance = ((candidates - midpoint) / midpoint) ** 2
            centre_weight = 1 / (1 + 3 * edge_distance.sum(axis=1))
            for _ in range(5):
                trial = []
                d2 = np.full(len(candidates), np.inf)
                noise = np.array([self.rng.uniform(0.82, 1.18) for _ in candidates])
                index = self.rng.choices(range(len(candidates)), weights=centre_weight)[0]
                while len(trial) < self.players and d2[index] >= minimum ** 2:
                    trial.append(tuple(candidates[index]))
                    d2 = np.minimum(d2, ((candidates - candidates[index]) ** 2).sum(axis=1))
                    scores = np.where(d2 >= minimum ** 2, d2 * noise * centre_weight, -1)
                    index = scores.argmax()
                if len(trial) > len(best):
                    best = trial
                if len(best) == self.players:
                    break
            if len(best) == self.players:
                break
        if len(best) != self.players:
            raise ValueError(f'Battle Royale: cannot fit {self.players} drops {minimum} game units apart. '
                             'Select a larger area or fewer players.')
        self.rng.shuffle(best)
        self.br_drops = best
        for i, (x, y) in enumerate(best):
            self.br_waypoint(wp(i), x, y)
            self.occ_poly(self.rect(x, y, 30, 30, 0), True)

        self.br_place_pois()
        self.br_place_vehicles()
        self.br_place_rebels()
        self.br_place_pedestrians()
        self.br_place_tech()
        self.br_phases = tuple(self.opt[f'br_zone_phase{i}'] for i in range(1, self.opt['br_zone_phases'] + 1))
        self.br_zones(ground)
        self.br_offsets = self.rng.sample(range(self.players), min(self.players, len(self.br_zone_patterns)))
        self.stats.update(br_drops=len(best), br_min_spacing=minimum, br_enemy_clearance=self.br_enemy_min,
                          br_rebels=len(self.br_rebels),
                          br_civil_vehicles=sum(model in CIVIL_VEHICLES for _, model, _ in self.br_vehicles),
                          br_humvees=sum(model in HUMVEES for _, model, _ in self.br_vehicles),
                          br_nuke_trucks=sum(model == NUKE_TRUCK for _, model, _ in self.br_vehicles),
                          br_zone_stages=len(self.br_phases), script_areas=len(self.area_triggers))
        for option, stat in (('br_civilian_count', 'br_pedestrians'), ('br_rebel_count', 'br_rebels'),
                             ('br_vehicle_count', 'br_civil_vehicles')):
            target, actual = self.opt[option], self.stats[stat]
            if actual < target:
                label = BR_COUNTS[option][2]
                self.log(f'Battle Royale: {label} {actual}/{target}')

    def br_road_points(self, ground, roads=PAVED_STREETS):
        # Stamp a broad street-side search corridor. The actual pavement and buildings are already excluded.
        corridors = []
        for road, path in self.road_lines:
            if road not in roads:
                continue
            width = self.road_w.get(road, 40) + 210
            for a, b in zip(path, path[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                corridors.append(self.rect((a[0] + b[0]) / 2, (a[1] + b[1]) / 2,
                                           math.hypot(dx, dy) + 80, width, math.atan2(dy, dx)))
        if not corridors:
            return np.zeros(len(ground), dtype=bool)
        mask = self.raster_poly(corridors)
        return mask[(ground[:, 1] / CELL).astype(int) + BORDER, (ground[:, 0] / CELL).astype(int) + BORDER]

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

    def br_place_rebels(self):
        self.br_rebels = []
        target = self.opt['br_rebel_count']
        if not target:
            return
        pool = self.br_points(9, dense=True)
        if not len(pool):
            return
        clear = self.br_enemy_min ** 2
        ddrop = np.full(len(pool), np.inf)
        for p in self.br_drops:
            ddrop = np.minimum(ddrop, ((pool - p) ** 2).sum(axis=1))
        # The fresh occupancy mask already excludes the vehicle footprints.
        pool = pool[ddrop >= clear]
        if not len(pool):
            return
        street = self.br_road_points(pool)
        quality = np.where(street, 1.0, .33)
        d2 = np.full(len(pool), np.inf)
        # Cover each opening area first, then spread stationary sentries along the streets.
        for i in range(target):
            viable = d2 >= 28 ** 2
            if not viable.any():
                break
            if i < len(self.br_drops):
                distance = ((pool - self.br_drops[i]) ** 2).sum(axis=1)
                j = np.where(viable, distance / quality, np.inf).argmin()
            else:
                j = np.where(viable, d2 * quality, -1).argmax()
            x, y = pool[j]
            name = f'{PREFIX}Rebel{i}'
            self.add_obj(REBEL, x, y, 0, 0, name, self.base_props, INS_TEAM)
            self.br_rebels.append((name, (x, y)))
            self.occ_poly(self.rect(x, y, 30, 30, 0), True)
            d2 = np.minimum(d2, ((pool - pool[j]) ** 2).sum(axis=1))

    def br_place_pedestrians(self):
        target = self.opt['br_civilian_count']
        self.stats['br_pedestrians'] = 0
        self.stats['br_farmers'] = 0
        if not target:
            return
        pool = self.br_points(9, dense=True)
        if not len(pool):
            return
        street = self.br_road_points(pool)
        x = (pool[:, 0] / CELL).astype(int) + BORDER
        y = (pool[:, 1] / CELL).astype(int) + BORDER
        fields = [[self.p.to_units(*point) for point in ring]
                  for kind, ring in self.osm.landuse if kind in ('farmland', 'farmyard')]
        farm = self.raster_poly(fields)[y, x]
        gathering = [[self.p.to_units(*point) for point in ring]
                     for kind, ring in self.osm.areas if kind in ('square', 'park', 'playground')]
        for kind, lat, lon in self.osm.points:
            if kind in ('station', 'square', 'bus_stop', 'church'):
                size = 230 if kind in ('station', 'square') else 120
                gathering.append(self.rect(*self.p.to_units(lat, lon), size, size, 0))
        busy = self.raster_poly(gathering)[y, x] & ~farm
        selected = []
        used = np.zeros(len(pool), dtype=bool)

        def take(mask, limit):
            candidates = np.flatnonzero(mask & ~used).tolist()
            chosen = self.rng.sample(candidates, min(limit, len(candidates), target - len(selected)))
            used[chosen] = True
            selected.extend(chosen)
            return len(chosen)

        farmers = take(farm, max(1, target // 20))
        take(busy, math.ceil(target * .6))
        take(street & ~farm, target)
        take(~farm, target)
        for i, j in enumerate(selected):
            model = 'AmericanFarmer01' if i < farmers else ('GenericMale01', 'GenericFemale01')[i % 2]
            x, y = pool[j]
            self.add_obj(model, x, y, self.rng.random() * math.tau, 0, f'{PREFIX}Pedestrian{i}',
                         self.base_props, 'team' + PEOPLE)
        self.stats['br_pedestrians'] = len(selected)
        self.stats['br_farmers'] = farmers

    def br_place_pois(self):
        self.br_pois = []
        pool = self.br_points(45, edge=75)
        for role, fractions in (('Cluster', (.73, .42)), ('Intel', (.50, .65))):
            if not len(pool):
                pool = self.br_points(35, edge=60)
            if not len(pool):
                break
            target = (self.AX * fractions[0], self.AY * fractions[1])
            j = ((pool - target) ** 2).sum(axis=1).argmin()
            x, y = pool[j]
            name = PREFIX + role
            self.add_obj(name, x, y, 0, 0, name, self.base_props, 'team' + NEUTRAL)
            self.occ_poly(self.rect(x, y, 84, 72, 0), True)
            self.br_pois.append((role, name))
            pool = pool[((pool - (x, y)) ** 2).sum(axis=1) >= 230 ** 2]
        self.stats['br_pois'] = len(self.br_pois)

    def br_place_tech(self):
        pool = self.br_points(43, edge=85)
        tech = ('TechOilDerrick', 'TechHospital')
        self.br_tech = []
        for i in range(max(4, min(12, self.players // 2))):
            model = tech[i % len(tech)]
            if not len(pool) or (self.known_objects and model not in self.known_objects):
                continue
            x, y = pool[self.rng.randrange(len(pool))]
            self.add_obj(model, x, y, 0, 0, f'{PREFIX}Tech{i}', self.base_props, 'team' + NEUTRAL)
            self.br_tech.append((x, y))
            self.occ_poly(self.rect(x, y, 96, 96, 0), True)
            pool = pool[((pool - (x, y)) ** 2).sum(axis=1) >= 260 ** 2]

    def br_zones(self, ground):
        # The radii cover every field tile from its centre to its furthest corner.
        pitch = max(85.0, math.sqrt(self.AX * self.AY / 440), max(self.AX, self.AY) / 46)
        nx, ny = max(4, round(self.AX / pitch)), max(4, round(self.AY / pitch))
        dx, dy = self.AX / nx, self.AY / ny
        half_diagonal = math.hypot(dx, dy) / 2
        self.br_field_radius = math.ceil(half_diagonal * 1.07) + 2
        center = np.array((self.AX / 2, self.AY / 2))
        interior = ground[(np.abs((ground - center) / center) <= .42).all(axis=1)]
        if not len(interior):
            raise ValueError('Battle Royale: no dry open ground in the central area for a final safe zone.')
        # Pick distinct central grid cells that contain open ground.
        cells = sorted({(int(x / dx), int(y / dy)) for x, y in interior})
        choices = self.rng.sample(cells, min(self.opt['br_zone_choices'], len(cells)))
        self.br_zone_patterns = []
        self.br_zone_markers = {}
        drop_ground = self.br_points(5, dense=True)
        self.br_airdrops = {ARENA: self.br_airdrop_sites(ARENA, drop_ground if len(drop_ground) else ground)}
        self.br_waypoint(PREFIX + 'View', *center)
        fields = {}
        for iy in range(ny):
            for ix in range(nx):
                name = f'{PREFIX}Field{ix}_{iy}'
                fields[name] = ((ix + .5) * dx, (iy + .5) * dy)
                self.br_waypoint(name, *fields[name])
        final = 1.95 * half_diagonal * self.opt['br_zone_final_size'] / 100
        first = max(.48 * min(self.AX, self.AY), 1.65 * final)
        beacons = self.opt['br_zone_beacons']
        drop_count = self.opt['br_drop_count']
        for k, (ix, iy) in enumerate(choices):
            x, y = (ix + .5) * dx, (iy + .5) * dy
            previous = set(fields)
            pattern = []
            for i in range(len(self.br_phases)):
                radius = first * (final / first) ** (i / (len(self.br_phases) - 1))
                name = f'{PREFIX}Safe{k}_{i + 1}'
                self.br_waypoint(name + '_Warning', x, y)
                rim = [(x + radius * math.cos(math.tau * j / 120),
                        y + radius * math.sin(math.tau * j / 120)) for j in range(120)]
                self.area_triggers.append((name, geo.clip_poly(rim[::4], self.AX, self.AY)))
                visible = [point for point in rim if self.inside(*point, 1)]
                markers = []
                if visible:
                    for j in range(beacons):
                        marker = f'{name}_Beacon{j}'
                        self.br_waypoint(marker, *visible[j * len(visible) // beacons])
                        markers.append(marker)
                self.br_zone_markers[name] = markers
                # A tile that reaches beyond the circle must radiate, even if its centre is inside.
                newly_unsafe = sorted(field for field in previous
                                      if math.dist((x, y), fields[field]) + half_diagonal >= radius)
                previous.difference_update(newly_unsafe)
                distance = ((drop_ground - (x, y)) ** 2).sum(axis=1)
                candidates = drop_ground[distance < (radius - 12) ** 2]
                if len(candidates) < drop_count:
                    inside = ground[((ground - (x, y)) ** 2).sum(axis=1) < (radius - 5) ** 2]
                    candidates = np.unique(np.concatenate((candidates, inside)), axis=0)
                clear = np.ones(len(candidates), dtype=bool)
                for field, point in fields.items():
                    if field not in previous and math.dist((x, y), point) < radius + self.br_field_radius + 8:
                        clear &= ((candidates - point) ** 2).sum(axis=1) > (self.br_field_radius + 8) ** 2
                if clear.sum() >= drop_count:
                    candidates = candidates[clear]
                self.br_airdrops[name] = self.br_airdrop_sites(name, candidates)
                pattern.append((name, newly_unsafe, (x, y, radius)))
            self.br_zone_patterns.append(pattern)

    def br_airdrop_sites(self, area, candidates):
        sites = []
        if len(candidates) and self.opt['br_drop_count']:
            # Keep crate locations separated even in the final circle.
            distances = np.full(len(candidates), np.inf)
            idx = self.rng.randrange(len(candidates))
            for i in range(min(self.opt['br_drop_count'], len(candidates))):
                name = f'{area}_AirDrop{i}'
                x, y = candidates[idx]
                self.br_waypoint(name, x, y)
                sites.append(name)
                np.minimum(distances, ((candidates - (x, y)) ** 2).sum(axis=1), out=distances)
                idx = distances.argmax()
        return sites

    def br_airdrop_actions(self, area):
        actions = []
        if self.br_airdrops[area]:
            actions += [('PLAY_SOUND_EFFECT', [('sound', 'ScriptJetFlyBy1')]), message('AirDrop')]
        for name in self.br_airdrops[area]:
            actions += [spawn(ELITE_DROP, 'team' + DROPS, name, name), radar_ping(name),
                        ('ENABLE_SCRIPT', [('script', name + '_Beacon')])]
        return actions

    def br_airdrop_beacons(self, area):
        return [Script(name + '_Beacon', [radar_ping(name)],
                       conditions=[('NAMED_NOT_DESTROYED', [('unit', name)])], once=False, delay=4, active=False,
                       else_actions=[('DISABLE_SCRIPT', [('script', name + '_Beacon')])])
            for name in self.br_airdrops[area]]

    def br_write_sides(self, m):
        sides = [
            dict(name=INS_SIDE, display='Street Rebels', faction='FactionCivilian', color=0xFFB01010,
                 enemies=' '.join(f'player{i}' for i in range(MAX_PLAYERS))),
            dict(name=NEUTRAL, display='Battle Royale Neutral', faction='FactionCivilian'),
            dict(name=STORM, display='Radiation', faction='FactionCivilian'),
            dict(name=PEOPLE, display='Townspeople', faction='FactionCivilian'),
            dict(name=DROPS, display='Elite Airdrops', faction='FactionCivilian', color=0xFFEAC748),
        ]
        # The neutral map sides run in PvP without a dedicated GLA lobby slot. SkirmishGLA and
        # its counterparts use a small combat routine if the lobby fills seats with stock AI.
        bot_scripts = {}
        for side in ('SkirmishGLA', 'SkirmishAmerica', 'SkirmishChina'):
            bot_scripts[side] = [Script(PREFIX + 'BotHunt', [('PLAYER_HUNT', [('side', side)])],
                                        conditions=[counter(ZONE, 3, 0), expired(PREFIX + 'BotGo')],
                                        delay=9, once=False)]
        write_sides(m, sides, {INS_SIDE: self.br_scripts(), **bot_scripts})
        structure_models = {obj.name: obj.name for obj in m.objects
                            if obj.name in self.cat.structure_armor_sets}
        structure_models.update({name: POI[role][0] for role, name in self.br_pois})
        self.map_ini = battle_royale_ini(self.br_field_radius, self.cat, structure_models, self.br_transport_models, self.opt)

    def br_scripts(self):
        sk = self.opt.get('language', 'sk') == 'sk'
        phases = self.br_phases
        pattern_count = len(self.br_zone_patterns)
        warning_lead = self.opt['br_zone_warning']
        first_warning_lead = min(FIRST_RADIATION_WARNING, phases[0])
        strings = {
            'Intro': ('BATTLE ROYALE: Posledny zivy vyhrava. Najdi vojaka na radare.' if sk else
                      'BATTLE ROYALE: Last player alive wins. Find your rifleman on the radar.'),
            'Next': 'Dalsia zona:' if sk else 'Next zone:',
            'Stage': 'Zona:' if sk else 'Zone:',
            'Closing': 'Radiacia sa siri. Vojdi do bezpecnej zony!' if sk else
                       'Radiation is spreading. Move inside the safe zone!',
            'Final': 'Posledna bezpecna zona!' if sk else 'Final safe zone!',
            'AirDrop': 'Elitne debny su v zone. Zober debnu hrdinom.' if sk else
                       'Elite crates are in the zone. Walk over a crate with your hero to collect it.',
            'ZoneWarning': 'Blizi sa posun zony!' if sk else 'The zone is about to move!',
            'ZoneWarningTimer': 'Posun zony o:' if sk else 'Zone moves in:',
            'FirstRadiationTimer': 'Prva radiacia o:' if sk else 'First radiation in:',
            'FirstRadiationBriefing': (
                f'POPLACH! Radiacia pride o {first_warning_lead} s. Presun sa k majaku v bezpecnej zone.' if sk else
                f'RADIATION ALERT! Fallout in {first_warning_lead} s. Move to the safe-zone beacon.'),
            'ClusterTitle': 'Cluster Bombstrike',
            'IntelTitle': 'Prieskum celej mapy' if sk else 'Full Map Recon',
            'ClusterHelp': 'Pouzitelne z obsadenej budovy.' if sk else 'Available while this building is occupied.',
            'IntelHelp': 'Pri prvom vstupe odkryje mapu a oznaci tvoju polohu.' if sk else 'First entry reveals the map and broadcasts your location.',
            'IntelUsed': 'Prieskum aktivovany. Poloha je oznacena na radare.' if sk else 'Recon activated. Its location is marked on radar.',
            'BuyScoutVehicle': f"${self.opt['br_vehicle_price']} - " + ('Kupit auto' if sk else 'Buy scout car'),
            'BuyScoutVehicleHelp': ('Auto ti zostane po vystupeni. Mozes ho pouzivat na prieskum.' if sk else
                                    'Keep the vehicle after exiting and drive it without a passenger to scout.'),
        }
        self.map_strings = {PREFIX + k: v for k, v in strings.items()} | hero_strings(sk, self.opt)
        pick = PREFIX + 'Pattern'
        ready = PREFIX + 'Ready'
        grace = PREFIX + 'EliminationGrace'
        scripts = [Script(PREFIX + 'Setup', [
            set_counter(ZONE, 0), set_counter(pick, -1), timer(CLOCK, phases[0]),
            timer(WARNING, phases[0] - first_warning_lead), ('PLAYER_SET_RANKLEVELLIMIT', [('int', 1)]),
            timer(ready, 0.3), timer(grace, 15), timer(PREFIX + 'BotGo', 6),
            timer(PREFIX + 'FirstAirDrop', self.opt['br_drop_time']),
            ('ENABLE_BORDER_SHROUD', []), ('MAP_SHROUD_ALL', [('side', '')]),
            ('RADAR_FORCE_ENABLE', []),
            ('RESET_CAMERA', [('waypoint', PREFIX + 'View'), ('real', 0), ('real', 0), ('real', 0)]),
            message('Intro'), ('DISPLAY_COUNTER', [('counter', ZONE), ('text', 'SCRIPT:' + PREFIX + 'Stage')]),
            ('DISPLAY_COUNTDOWN_TIMER', [('counter', CLOCK), ('text', 'SCRIPT:' + PREFIX + 'FirstRadiationTimer')]),
        ] + [('SET_RANDOM_MSEC_TIMER', [('counter', f'{pick}{k}'), ('real', .2), ('real', 1.5)])
             for k in range(pattern_count)])]
        for k in range(pattern_count):
            scripts.append(Script(f'{pick}{k}', [set_counter(pick, k)], conditions=[
                counter(pick, 2, -1), expired(f'{pick}{k}')]))
        players = [f'player{i}' for i in range(MAX_PLAYERS)]
        offsets = [self.br_offsets[k % len(self.br_offsets)] for k in range(pattern_count)]
        for i, player in enumerate(players):
            joined = f'{PREFIX}Alive{i}'
            faction_counter = f'{PREFIX}Faction{i}'
            for family_index, family in enumerate(RIFLES, 1):
                scripts.append(Script(f'{faction_counter}_{family}', [set_counter(faction_counter, family_index)],
                                      any_of=[[('SKIRMISH_PLAYER_FACTION', [('side', player), ('faction', faction)])]
                                              for faction in ZS_FACTIONS[family][0]]))
            # There is no engine scripting indirection for a player or a waypoint. Resolve the
            # actual start index so sparse lobbies and randomized lobby positions work the same.
            for j in range(len(self.br_drops)):
                for family_index, family in enumerate(RIFLES, 1):
                    # A match RNG draw rotates the assignment, never the sites; spacing is invariant.
                    groups = [[counter(joined, 2, 0), counter(faction_counter, 2, family_index),
                               expired(ready), counter(pick, 2, k),
                               ('START_POSITION_IS', [('side', player), ('int', (j + offset) % self.players + 1)])]
                              for k, offset in enumerate(offsets)]
                    starter = f'{PREFIX}First{i}'
                    actions = [
                        spawn(STARTERS[family], 'team' + NEUTRAL, wp(j), starter),
                        set_counter(f'{PREFIX}Pending{i}', 1),
                    ]
                    groups = [[counter(f'{PREFIX}Pending{i}', 2, 0)] + group for group in groups]
                    scripts.append(Script(f'{PREFIX}Insert{i}_{j}_{family}', actions, any_of=groups))
            # Keep the native command center hidden until this player is eliminated.
            starter = f'{PREFIX}First{i}'
            scripts.append(Script(f'{PREFIX}Deploy{i}', [
                transfer(starter, player),
                ('PLAYER_SET_MONEY', [('side', player), ('int', self.opt['br_start_money'])]),
            ] + relations(player, INS_SIDE, 0) + relations(player, NEUTRAL, 1),
                conditions=[counter(f'{PREFIX}Pending{i}', 2, 1),
                            ('NAMED_NOT_DESTROYED', [('unit', starter)])]))
            scripts.append(Script(f'{PREFIX}Confirm{i}', [set_counter(joined, 1)], conditions=[
                counter(f'{PREFIX}Pending{i}', 2, 1),
                ('NAMED_OWNED_BY_PLAYER', [('unit', starter), ('side', player)]),
            ]))
            for key, setting, power in (('Air', 'br_enable_air', AIR_POWER),
                                        ('ClusterBuy', 'br_enable_cluster', CLUSTER_POWER)):
                if self.opt[setting]:
                    args = [('unit', starter), ('power', power)]
                    scripts.append(Script(f'{PREFIX}Spend{key}{i}', [
                        ('NAMED_SET_SPECIAL_POWER_COUNTDOWN', args + [('int', 0)]),
                        ('NAMED_STOP_SPECIAL_POWER_COUNTDOWN', args),
                        spend_upgrade(starter, key),
                    ], conditions=[('PLAYER_TRIGGERED_SPECIAL_POWER_FROM_NAMED',
                                    [('side', player), ('power', power), ('unit', starter)])], once=False))
            if self.opt['br_enable_c4']:
                armed = f'{PREFIX}C4Armed{i}'
                # Spending follows a successful placement. Detonation and lost targets only lower the count.
                for count in range(self.opt['br_c4_limit'] + 1):
                    charge_count = ('PLAYER_HAS_OBJECT_COMPARISON', [
                        ('side', player), ('comparison', 2), ('int', count), ('type', C4_CHARGE)])
                    if count:
                        scripts.append(Script(f'{armed}_Place{count}',
                                              [spend_upgrade(starter, 'C4'), set_counter(armed, count)],
                                              conditions=[counter(joined, 2, 1), counter(armed, 2, count - 1), charge_count],
                                              once=False))
                    if count < self.opt['br_c4_limit']:
                        scripts.append(Script(f'{armed}_Clear{count}', [set_counter(armed, count)],
                                              conditions=[counter(joined, 2, 1), counter(armed, 4, count), charge_count],
                                              once=False))
            # Captured oil/hospitals are useful, but cannot keep a competitor alive after their army is gone.
            scripts.append(Script(f'{PREFIX}Out{i}', [
                set_counter(joined, 2), ('PLAYER_KILL', [('side', player)]),
            ], conditions=[expired(grace), counter(joined, 2, 1),
                           ('NAMED_DESTROYED', [('unit', starter)])] + no_units(player), delay=2))
        # The mode is FFA even if the lobby accidentally assigned allies.
        ffa = [a for i, p in enumerate(players) for q in players[i + 1:] for a in relations(p, q, 0)]
        scripts.insert(1, Script(PREFIX + 'FreeForAll', ffa + [a for p in players for a in relations(p, INS_SIDE, 0)]))
        scripts.append(Script(PREFIX + 'CivilianWalk', [('TEAM_WANDER_IN_PLACE', [('team', 'team' + PEOPLE)])],
                              conditions=[expired(PREFIX + 'BotGo')]))
        for name, model, _ in self.br_vehicles:
            if model in CIVIL_VEHICLES:
                scripts.extend(self.br_transport_scripts(name, players))
        opening_drop = [counter(ZONE, 2, 0), expired(PREFIX + 'FirstAirDrop')]
        scripts.append(Script(ARENA + '_AirDrop', self.br_airdrop_actions(ARENA), conditions=opening_drop))
        scripts.extend(self.br_airdrop_beacons(ARENA))
        reward = self.opt['br_rebel_reward']
        for name, _ in self.br_rebels if reward else ():
            payout = name + '_Payout'
            scripts.append(Script(name + '_OnDeath', [('CALL_SUBROUTINE', [('subroutine', payout)])],
                                  conditions=[('NAMED_DYING', [('unit', name)])]))
            scripts.append(ScriptGroup(payout, [
                Script(payout + str(i), [('PLAYER_GIVE_MONEY', [('side', player), ('int', reward)])],
                       conditions=[counter(f'{PREFIX}Alive{i}', 2, 1),
                                   ('NAMED_ATTACKED_BY_PLAYER', [('unit', name), ('side', player)])])
                for i, player in enumerate(players)
            ]))
        scripts.extend(self.br_poi_scripts(players))
        for k, pattern in enumerate(self.br_zone_patterns):
            for i, (area, fields, _) in enumerate(pattern):
                warning = area + '_Warning'
                warning_conditions = [counter(pick, 2, k), counter(ZONE, 2, i), expired(WARNING)]
                warning_ping = radar_ping(warning)
                if i == 0 or warning_lead:
                    if i == 0:
                        notice = [('PLAY_SOUND_EFFECT', [('sound', 'AirRaidSiren')]),
                                  ('SHOW_MILITARY_CAPTION', [('text', 'SCRIPT:' + PREFIX + 'FirstRadiationBriefing'),
                                                            ('int', min(10, first_warning_lead) * 1000)])]
                        timer_label = 'FirstRadiationTimer'
                    else:
                        notice = [message('ZoneWarning')]
                        timer_label = 'ZoneWarningTimer'
                    scripts.append(Script(warning, [
                        *notice,
                        ('DISPLAY_COUNTDOWN_TIMER', [('counter', CLOCK), ('text', 'SCRIPT:' + PREFIX + timer_label)]),
                        spawn(ZONE_MARKER, 'team' + STORM, warning, warning),
                        warning_ping,
                    ], conditions=warning_conditions))
                    scripts.append(Script(warning + '_Beacon', [warning_ping], once=False, delay=3,
                                          conditions=warning_conditions + [('NAMED_NOT_DESTROYED', [('unit', warning)])]))
                actions = [set_counter(ZONE, i + 1), message('Closing' if i + 1 < len(phases) else 'Final'),
                           ('PLAY_SOUND_EFFECT', [('sound', 'PreDirtyNukeDetonation')])]
                if i + 1 < len(phases):
                    actions += [timer(CLOCK, phases[i + 1]), timer(WARNING, max(0, phases[i + 1] - warning_lead)),
                                ('DISPLAY_COUNTDOWN_TIMER', [('counter', CLOCK), ('text', 'SCRIPT:' + PREFIX + 'Next')])]
                else:
                    actions.append(('HIDE_COUNTDOWN_TIMER', [('counter', CLOCK)]))
                actions += [spawn(FIELD, 'team' + STORM, field) for field in fields]
                actions += self.br_airdrop_actions(area)
                markers = self.br_zone_markers[area]
                pings = [radar_ping(marker) for marker in markers]
                actions += [spawn(ZONE_MARKER, 'team' + STORM, marker, marker) for marker in markers]
                actions += pings
                actions += [timer(name, seconds) for name, seconds in ZONE_PING_TIMERS.items()]
                scripts.append(Script(f'{area}_Activate', actions,
                                      conditions=[counter(pick, 2, k), counter(ZONE, 2, i), expired(CLOCK)]))
                scripts.extend(self.br_airdrop_beacons(area))
                for name, seconds in ZONE_PING_TIMERS.items():
                    scripts.append(Script(f'{area}_Ping{seconds}', pings,
                                          conditions=[counter(pick, 2, k), counter(ZONE, 2, i + 1), expired(name)]))
        return scripts

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

    def br_poi_scripts(self, players):
        scripts = []
        for role, name in self.br_pois:
            _, power, _ = POI[role]
            active, used = name + '_Active', name + '_Used'

            def owned(player):
                return ('NAMED_OWNED_BY_PLAYER', [('unit', name), ('side', player)])

            if role == 'Intel':
                # A garrison switches to the occupant's team until it is emptied.
                for i, player in enumerate(players):
                    scripts.append(Script(name + f'_Reveal{i}', [
                        set_counter(used, 1),
                        ('MAP_REVEAL_ALL', [('side', player)]),
                        radar_ping(name),
                        message('IntelUsed'),
                    ], conditions=[counter(used, 2, 0), owned(player)]))
                continue

            args = [('unit', name), ('power', power)]
            empty = ('NAMED_HAS_FREE_CONTAINER_SLOTS', [('unit', name)])
            start = ('NAMED_START_SPECIAL_POWER_COUNTDOWN', args)
            stop = ('NAMED_STOP_SPECIAL_POWER_COUNTDOWN', args)
            scripts.append(Script(name + '_Enable', [start, set_counter(active, 1)],
                                  any_of=[[counter(active, 2, 0), owned(p)] for p in players], once=False))
            scripts.append(Script(name + '_Prime', [('NAMED_SET_SPECIAL_POWER_COUNTDOWN', args + [('int', 0)])],
                                  conditions=[counter(active, 2, 1)]))
            scripts.append(Script(name + '_Disable', [stop, set_counter(active, 0)],
                                  conditions=[counter(active, 2, 1), empty], once=False))
        return scripts
