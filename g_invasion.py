"""Invasion: a shared headquarters, the players' build sites, and escalating faction attacks."""
import math

import numpy as np
from PIL import Image, ImageDraw

import geo
from gconst import BORDER, CELL, MAX_PLAYERS, OCC
from mapio import MapObject, Script, write_sides


DEFENDERS = 'EarthInvasionHQ'
HQ = 'EARTH_I_HQ'
HQ_WP = 'EARTH_I_HQ_WP'
ARENA = 'EARTH_I_Arena'
INVASION_NUMBERS = {
    # default, min, max, slider step
    'invasion_prep': (180, 0, 900, 15),
    'invasion_phase': (120, 15, 600, 15),
    'invasion_interval': (18, 4, 90, 1),
    'invasion_size': (4, 1, 12, 1),
    'invasion_limit': (100, 1, 300, 1),
    'invasion_money': (7500, 0, 30000, 500),
    'invasion_income': (500, 0, 5000, 50),
}

# Infantry, light vehicle, tank, heavy tank, siege, aircraft (empty for GLA).
INVASION_ROSTERS = {
    'USA': ('AmericaInfantryRanger', 'AmericaInfantryMissileDefender', 'AmericaVehicleHumvee',
            'AmericaTankCrusader', 'AmericaTankPaladin', 'AmericaVehicleTomahawk', 'AmericaJetRaptor'),
    'China': ('ChinaInfantryRedguard', 'ChinaInfantryTankHunter', 'ChinaTankGattling',
              'ChinaTankBattleMaster', 'ChinaTankOverlord', 'ChinaVehicleInfernoCannon', 'ChinaJetMIG'),
    'GLA': ('GLAInfantryRebel', 'GLAInfantryTunnelDefender', 'GLAVehicleTechnical',
            'GLATankScorpion', 'GLATankMarauder', 'GLAVehicleRocketBuggy', None),
}


def counter(name, comparison, value):
    return ('COUNTER', [('counter', name), ('comparison', comparison), ('int', value)])


def set_counter(name, value):
    return ('SET_COUNTER', [('counter', name), ('int', value)])


def timer(name, seconds):
    return ('SET_MILLISECOND_TIMER', [('counter', name), ('real', seconds)])


def expired(name):
    return ('TIMER_EXPIRED', [('counter', name)])


def relations(a, b, value):
    return [('PLAYER_RELATES_PLAYER', [('side', src), ('side', dst), ('relation', value)])
            for src, dst in ((a, b), (b, a))]


class InvasionMixin:
    def invasion_open(self, size):
        occupied = self.occ.reshape(self.WY, OCC, self.WX, OCC).any(axis=(1, 3))
        radius = math.ceil(size / (2 * CELL))
        free = geo.box_mean(occupied, radius) == 0
        pad = BORDER + radius + 1
        return free[pad:self.WY - pad, pad:self.WX - pad], pad

    def invasion_plot(self, target, sizes, max_distance=None, reachable=None):
        for size in sizes:
            free, pad = self.invasion_open(size)
            if reachable is not None:
                inset = pad - BORDER
                free &= reachable[inset:self.NY - inset, inset:self.NX - inset]
            rows, cols = np.nonzero(free)
            if not len(rows):
                continue
            xs, ys = (cols + pad - BORDER) * CELL, (rows + pad - BORDER) * CELL
            distance = (xs - target[0]) ** 2 + (ys - target[1]) ** 2
            i = distance.argmin()
            if max_distance is not None and distance[i] > max_distance ** 2:
                continue
            return float(xs[i]), float(ys[i]), size
        return None

    def invasion_land(self):
        """Dry terrain connected to the headquarters, including existing bridges."""
        land = Image.fromarray((~self.water_mask).astype(np.uint8))
        draw = ImageDraw.Draw(land)
        for a, b in self.bridges:
            draw.line([tuple(map(self.cx, a)), tuple(map(self.cx, b))], fill=1, width=3)
        land = land.crop((BORDER, BORDER, BORDER + self.NX, BORDER + self.NY))
        ImageDraw.floodfill(land, tuple(int(u / CELL) for u in self.invasion_hq), 2)
        return np.asarray(land) == 2

    def place_invasion_hq(self):
        """Reserve construction space on vacant ground after the original town is placed."""
        model = 'AmericaCommandCenter'
        length, width = self.cat.buildings.get(model, (168, 168, 40))[:2]
        footprint = max(length, width) + 22
        plot = self.invasion_plot((self.AX / 2, self.AY / 2), (footprint,))
        if plot is None:
            raise ValueError('Invasion: nenašlo sa suché voľné miesto pre veliteľstvo. Posuň alebo zväčši oblasť.')
        x, y, _ = plot
        self.invasion_hq = (x, y)
        self.flatten_build_pad(x, y, footprint, footprint)
        self.occ_poly(self.rect(x, y, footprint, footprint, 0), True)
        self.add_obj(model, x, y, 0, 0, HQ, self.base_props, 'team' + DEFENDERS)
        self.invasion_reachable = self.invasion_land()

        self.invasion_builders = []
        for slot in range(self.humans):
            angle = math.tau * slot / self.humans
            target = (x + 260 * math.cos(angle), y + 260 * math.sin(angle))
            plot = self.invasion_plot(target, (220, 130, 70), 650, self.invasion_reachable)
            if plot is None:
                plot = self.invasion_plot(target, (70,), reachable=self.invasion_reachable)
            if plot is None:
                raise ValueError('Invasion: nenašlo sa voľné miesto pre každého staviteľa. Zväčši oblasť alebo zníž počet hráčov.')
            px, py, size = plot
            self.flatten_build_pad(px, py, size, size)
            self.occ_poly(self.rect(px, py, size, size, 0), True)
            self.invasion_builders.append((px, py))
        self.stats['invasion_builders'] = len(self.invasion_builders)

        docks = 0
        if self.resources != 'low':
            a, b = self.cat.buildings.get('SupplyDock', (100, 122, 13))[:2]
            size = max(a, b) + 60
            for k in range(4 if self.resources == 'high' else 2):
                angle = math.pi * (k / 2 + 0.25) if self.resources == 'high' else math.pi * (k + 0.25)
                target = (x + 450 * math.cos(angle), y + 450 * math.sin(angle))
                plot = self.invasion_plot(target, (size,), 450, self.invasion_reachable)
                if plot is None:
                    continue
                px, py, _ = plot
                self.flatten_build_pad(px, py, size, size)
                self.occ_poly(self.rect(px, py, size, size, 0), True)
                self.add_obj('SupplyDock', px, py, 0, 0, f'EARTH_I_Supply{k}', self.base_props)
                docks += 1
        self.stats['supply_docks'] = docks

    def build_invasion_waypoints(self):
        def waypoint(name, x, y):
            self.next_wp += 1
            props = [(k, name if self.sid[k >> 8] in ('uniqueID', 'objectName', 'waypointName')
                      else self.next_wp if self.sid[k >> 8] == 'waypointID' else v) for k, v in self.wp_props]
            self.objs.append(MapObject(3, x, y, 0.0, 0.0, 0, '*Waypoints/Waypoint', props))

        waypoint(HQ_WP, *self.invasion_hq)
        for slot, pos in enumerate(self.invasion_builders, 1):
            # The engine places the faction's native starting unit at Rally and its command center at Start.
            waypoint(f'Player_{slot}_Rally', *pos)

        # Prefer dry entries near the playable border, with some lateral variation per map seed.
        free, pad = self.invasion_open(160)
        inset = pad - BORDER
        rows, cols = np.nonzero(free & self.invasion_reachable[inset:self.NY - inset, inset:self.NX - inset])
        xs, ys = (cols + inset) * CELL, (rows + inset) * CELL
        self.invasion_spawns = []
        hx, hy = self.invasion_hq
        near_hq = (xs - hx) ** 2 + (ys - hy) ** 2
        margin = min(480, 0.28 * min(self.AX, self.AY))
        west, east, north, south = (self.rng.uniform(0.3, 0.7) for _ in range(4))
        targets = ((0, self.AY * west, xs <= self.AX / 2, 4, 1),
                   (self.AX, self.AY * east, xs >= self.AX / 2, 4, 1),
                   (self.AX * north, self.AY, ys >= self.AY / 2, 1, 4),
                   (self.AX * south, 0, ys <= self.AY / 2, 1, 4))
        for tx, ty, side, weight_x, weight_y in targets:
            viable = side & (near_hq >= margin ** 2)
            for px, py in self.invasion_spawns:
                viable &= (xs - px) ** 2 + (ys - py) ** 2 >= margin ** 2
            if viable.any():
                score = np.where(viable, weight_x * (xs - tx) ** 2 + weight_y * (ys - ty) ** 2, np.inf)
                k = score.argmin()
                self.invasion_spawns.append((float(xs[k]), float(ys[k])))
        if not self.invasion_spawns:
            raise ValueError('Invasion: pre útočníkov sa nenašlo voľné miesto na rovnakej strane vody ako základňa.')
        for k, (x, y) in enumerate(self.invasion_spawns):
            waypoint(f'EARTH_I_Entry{k}', x, y)
            for j in range(12):
                waypoint(f'EARTH_I_Entry{k}_{j}', x + (j % 4 - 1.5) * 36, y + (j // 4 - 1) * 36)
        self.stats['invasion_entries'] = len(self.invasion_spawns)
        border = BORDER * CELL
        self.area_triggers.append((ARENA, [(-border, -border), (self.AX + border, -border),
                                           (self.AX + border, self.AY + border), (-border, self.AY + border)]))
        self.stats['script_areas'] = len(self.area_triggers)

    def write_invasion_sides(self, m):
        entries = [f'EARTH_I_Team{k}' for k in range(len(self.invasion_spawns))]
        faction = self.opt['invasion_faction']
        air = INVASION_ROSTERS[faction][-1] if self.opt['invasion_air'] else None
        attack_teams = entries + (['EARTH_I_Air'] if air else [])
        sides = [dict(name=DEFENDERS, display='Invasion HQ', faction='FactionCivilian', color=0xFF356ECB)]
        driver = self.ZOMBIE_SIDE
        write_sides(m, sides, {driver: self.invasion_scripts(entries, air)},
                    extra_teams=[(team, driver) for team in attack_teams])

    def invasion_scripts(self, entries, air):
        o = self.opt
        faction = o['invasion_faction']
        driver = self.ZOMBIE_SIDE
        armed = ('START_POSITION_IS', [('side', driver), ('int', self.players)])
        lost = [('NAMED_DESTROYED', [('unit', HQ)])]
        ended = 'EARTH_I_Ended'
        alive = [armed, counter(ended, 2, 0)]
        prep, level, phase = 'EARTH_I_Prep', 'EARTH_I_Level', 'EARTH_I_Phase'
        next_ground, next_air, route = 'EARTH_I_Ground', 'EARTH_I_AirT', 'EARTH_I_Route'
        language = o.get('language', 'sk')
        sk = language == 'sk'
        self.map_strings = {
            'EARTH_I_Prepare': 'Priprava obrany:' if sk else 'Build time:',
            'EARTH_I_Pressure': 'Uroven invazie:' if sk else 'Invasion level:',
            'EARTH_I_Intro': 'INVASION: Bran spolocne velitelstvo.' if sk else 'INVASION: Defend the shared headquarters.',
            'EARTH_I_Begins': f'Zacina utok frakcie {faction}!' if sk else f'The {faction} invasion begins!',
            'EARTH_I_Lost': 'Spolocne velitelstvo bolo znicene!' if sk else 'The shared headquarters has fallen!',
        }
        no_money = ('PLAYER_SET_MONEY', [('side', driver), ('int', 0)])
        setup = relations(driver, DEFENDERS, 0) + [no_money, set_counter(level, 0), set_counter(route, -1),
                 set_counter(ended, 0), timer(prep, o['invasion_prep'] + 1), ('ENABLE_BORDER_SHROUD', []),
                 ('RESET_CAMERA', [('waypoint', HQ_WP), ('real', 0), ('real', 0), ('real', 0)]),
                 ('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_I_Intro')])]
        if o['black_map']:
            setup += [('MAP_SHROUD_ALL', [('side', '')])]
        setup += [('MAP_REVEAL_AT_WAYPOINT', [('waypoint', HQ_WP), ('real', 420), ('side', '')]),
                  ('DISPLAY_COUNTDOWN_TIMER', [('counter', prep), ('text', 'SCRIPT:EARTH_I_Prepare')])]
        scripts = [Script('EARTH_I_Setup', setup, conditions=[armed]),
                   Script('EARTH_I_NoMoney', [no_money], conditions=[armed], delay=15, once=False),
                   Script('EARTH_I_Lost', [set_counter(ended, 1),
                          ('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_I_Lost')]), ('DEFEAT', [])],
                          conditions=[armed] + lost)]
        for i in range(MAX_PLAYERS):
            player = f'player{i}'
            defender = f'EARTH_I_Defender{i}'
            scripts.append(Script(f'EARTH_I_Join{i}', relations(driver, player, 0) +
                                  relations(DEFENDERS, player, 2) + [set_counter(defender, 1),
                                  ('PLAYER_SET_MONEY', [('side', player), ('int', o['invasion_money'])])],
                                  any_of=[[armed, ('START_POSITION_IS', [('side', player), ('int', k)])]
                                          for k in range(1, self.players)]))
            if o['invasion_income']:
                scripts.append(Script(f'EARTH_I_Income{i}', [
                    ('PLAYER_GIVE_MONEY', [('side', player), ('int', o['invasion_income'])])],
                    conditions=alive + [counter(defender, 2, 1), counter(level, 3, 1)], delay=60, once=False))
        scripts += [Script('EARTH_I_Begin', [('HIDE_COUNTDOWN_TIMER', [('counter', prep)]),
                           ('DISPLAY_COUNTER', [('counter', level), ('text', 'SCRIPT:EARTH_I_Pressure')]),
                           ('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_I_Begins')]), set_counter(level, 1),
                           timer(phase, o['invasion_phase']), timer(next_ground, 0.1)],
                           conditions=alive + [expired(prep)]),
                    Script('EARTH_I_Advance', [('INCREMENT_COUNTER', [('int', 1), ('counter', level)]),
                           timer(phase, o['invasion_phase'])], conditions=alive + [counter(level, 3, 1), expired(phase)],
                           once=False)]

        limit = o['invasion_limit']
        spawn_lock = 'EARTH_I_SpawnLock'
        scripts.append(Script('EARTH_I_UnlockSpawn', [set_counter(spawn_lock, 0)], once=False))
        def capacity(count):
            return ('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                    [('side', driver), ('comparison', 0), ('int', limit - count + 1),
                     ('kindof', 'CAN_ATTACK'), ('area', ARENA)])

        def spawn(unit, team, waypoint):
            return ('CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT',
                    [('type', unit), ('team', team), ('waypoint', waypoint)])

        def attack(team):
            return ('TEAM_ATTACK_NAMED', [('team', team), ('unit', HQ)])

        def random_timer(name, minimum, maximum):
            return ('SET_RANDOM_MSEC_TIMER', [('counter', name), ('real', minimum), ('real', maximum)])

        # Independent timers choose an entry and whether it sends a larger group.
        # Keep the result while the attacker cap is full so polling cannot bias the draw.
        draws = [(j, surge, f'EARTH_I_Draw{j}_{surge}')
                 for j in range(len(entries)) for surge in (0, 1)]
        scripts.append(Script('EARTH_I_Draw', [set_counter(route, 0)] +
                              [random_timer(name, 0.45 if surge else 0.15, 1.55 if surge else 1.25)
                               for _, surge, name in draws],
                              conditions=alive + [counter(level, 3, 1), expired(next_ground), counter(route, 2, -1)],
                              once=False))
        for j, surge, name in draws:
            scripts.append(Script(f'{name}_Pick', [set_counter(route, 1 + j * 2 + surge)],
                                  conditions=alive + [counter(route, 2, 0), expired(name)], once=False))

        if air:
            air_order = 'EARTH_I_AirOrderT'
            ground_since_air = 'EARTH_I_GroundSinceAir'
            scripts.append(Script('EARTH_I_AirReady', [timer(next_air, 5), set_counter(ground_since_air, 2)],
                                  conditions=[counter(level, 3, 5)]))
            scripts.append(Script('EARTH_I_AirRaid', [spawn(air, 'EARTH_I_Air', 'EARTH_I_Entry0'),
                                  timer(air_order, 1), timer(next_air, max(12, o['invasion_interval'] * 2)),
                                  set_counter(spawn_lock, 1), set_counter(ground_since_air, 0)],
                                  conditions=alive + [counter(level, 3, 5), expired(next_air),
                                  counter(ground_since_air, 3, 2), counter(spawn_lock, 2, 0), capacity(1),
                                  ('PLAYER_HAS_OBJECT_COMPARISON', [('side', driver), ('comparison', 0),
                                   ('int', min(4, max(1, limit // 10))), ('type', air)])], once=False))
            # Scripted jets enable flight in their first AI update, after they are created.
            scripts.append(Script('EARTH_I_AirOrder', [attack('EARTH_I_Air'), timer(air_order, 86400)],
                                  conditions=alive + [expired(air_order)], once=False))

        rifle, rocket, light, tank, heavy, siege, _ = INVASION_ROSTERS[faction]
        bands = ((1, (rifle, rocket, rifle), 0, 1.00),
                 (2, (light, rifle, rocket), 0, 0.90),
                 (3, (tank, light, rocket, rifle), 1, 0.80),
                 (4, (heavy, tank, rocket, light), 1, 0.68),
                 (5, (siege, heavy, tank, light, rocket), 2, 0.58),
                 (8, (siege, heavy, tank, heavy, rocket), 3, 0.47),
                 (14, (heavy, siege, heavy, tank, light), 4, 0.34),
                 (24, (siege, heavy, tank, heavy, light), 6, 0.24),
                 (40, (heavy, siege, heavy, siege, tank), 7, 0.18),
                 (64, (siege, heavy, siege, heavy, tank), 8, 0.13),
                 (96, (heavy, siege, heavy, siege, heavy), 9, 0.10))
        for k, (first, units, extra, pace) in enumerate(bands):
            when = alive + [counter(level, 3, first)]
            if k + 1 < len(bands):
                when += [counter(level, 0, bands[k + 1][0])]
            interval = max(2, o['invasion_interval'] * pace)
            for j, surge, _ in draws:
                team = entries[j]
                size = min(limit, 12 if surge else 10, o['invasion_size'] + extra + 3 * surge)
                acts = [spawn(units[(n + j) % len(units)], team, f'EARTH_I_Entry{j}_{n}') for n in range(size)]
                acts += [('TEAM_HUNT', [('team', team)]),
                         random_timer(next_ground, interval * (0.9 if surge else 0.55), interval * 1.45),
                         set_counter(route, -1), set_counter(spawn_lock, 1)]
                if air and first >= 5:
                    acts.append(('INCREMENT_COUNTER', [('int', 1), ('counter', ground_since_air)]))
                scripts.append(Script(f'EARTH_I_Attack{first}_{j}_{surge}', acts,
                                      conditions=when + [counter(route, 2, 1 + j * 2 + surge),
                                                         counter(spawn_lock, 2, 0), capacity(size)],
                                      once=False))
        return scripts
