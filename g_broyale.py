"""Free-for-all Battle Royale: arena layout, civilian units and zone scripts."""
import math

import numpy as np

import geo
from g_brhero import hero_purchase_scripts, hero_strings
from g_brini import POI, REBEL, RIFLES, STARTERS, battle_royale_ini
from g_brscript import (ARENA, CLOCK, DROPS, NEUTRAL, PEOPLE, PREFIX, STORM, WARNING, ZONE,
                        message, radar_ping, spawn, transfer)
from g_brvehicles import CIVIL_VEHICLES, HUMVEES, NUKE_TRUCK, BattleRoyaleVehicleMixin
from g_brzone import FIELD, BattleRoyaleZoneMixin
from g_brsettings import BR_COUNTS, FIRST_RADIATION_WARNING
from g_invasion import counter, expired, relations, set_counter, timer
from gconst import BORDER, CELL, INS_SIDE, INS_TEAM, MAX_PLAYERS, OCC, PAVED_STREETS, UNPAVED_STREETS, ZS_FACTIONS
from mapio import MapObject, Script, ScriptGroup, write_sides


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def wp(i):
    return f'{PREFIX}Drop{i}'


def no_units(player):
    # Count by template, including infantry in captured buildings.
    return [('PLAYER_HAS_OBJECT_COMPARISON',
             [('side', player), ('comparison', 2), ('int', 0), ('type', model)])
            for model in (*STARTERS.values(), *RIFLES.values())]


class BattleRoyaleMixin(BattleRoyaleZoneMixin, BattleRoyaleVehicleMixin):
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
            scripts.extend(hero_purchase_scripts(self.opt, i, player, starter, joined))
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
        scripts.extend(self.br_zone_scripts(pick, phases, warning_lead, first_warning_lead))
        return scripts

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
