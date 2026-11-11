"""Zombie / Clearance / Survival / Defense: silo, nests, scripts."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class ZombieModesMixin:
    def place_silo(self):
        """ZombieSurvival Defense: a Chinese nuclear missile silo as close to the town centre as there is room."""
        name = 'ChinaNuclearMissileLauncher'
        a, b = self.cat.buildings.get(name, (140, 140, 40))[:2]
        cx_, cy_ = self.AX / 2, self.AY / 2
        spot = None if not getattr(self, 'zs_plaza', False) or self._wet(cx_, cy_) else (cx_, cy_)   # the kept-free square
        for r in (range(0, 1400, 40) if spot is None else ()):
            for k in range(max(1, int(r / 30))):
                t = math.tau * k / max(1, int(r / 30))
                x, y = cx_ + math.cos(t) * r, cy_ + math.sin(t) * r
                if self._wet(x, y): continue
                if self.occ_test(self.rect(x, y, a * 1.3, b * 1.3, 0)) <= 0.03:
                    spot = (x, y); break
            if spot: break
        if spot is None:
            spot = self._dry(cx_, cy_, 100)
        x, y = spot
        self.occ_poly(self.rect(x, y, a * 1.2, b * 1.2, 0), True)
        self.add_obj(name, x, y, 0.0, 0, 'EARTH_Silo', self.base_props, INS_TEAM)
        self.silo = ('EARTH_SiloWP', x, y)
        self.stats['silo'] = (round(x), round(y))

    def place_zombie_tunnel(self):
        """Reserve a tunnel and a clear exit as near the centre as the terrain permits."""
        cx, cy = self.AX / 2, self.AY / 2
        model = 'GLASneakAttackTunnelNetwork'
        if self.known_objects and model not in self.known_objects:
            model = 'TrainTunnel'
        fallback = None
        best = None
        for r in range(0, int(min(self.AX, self.AY) * 0.45), 40):
            for k in range(max(1, math.ceil(math.tau * r / 60))):
                t = k * math.tau / max(1, math.ceil(math.tau * r / 60))
                x, y = cx + r * math.cos(t), cy + r * math.sin(t)
                if not self.inside(x, y, 140) or self._wet(x, y):
                    continue
                for angle in (0, math.pi / 2, math.pi, math.pi * 1.5):
                    dx, dy = math.cos(angle), math.sin(angle)
                    sx, sy = x + 65 * dx, y + 65 * dy
                    if self._wet(sx, sy):
                        continue
                    # The exit can open onto a road. Only the tunnel model needs empty ground.
                    if self.occ_test(self.rect(x, y, 54, 54, angle)) > 0:
                        continue
                    area = self.rect(x + 24 * dx, y + 24 * dy, 134, 78, angle)
                    if fallback is None:
                        fallback = (x, y, angle, sx, sy, area)
                    if any(abs(x - bx) < self.base_clear / 2 + 105 and abs(y - by) < self.base_clear / 2 + 105
                           for bx, by in self.starts):
                        continue
                    best = (x, y, angle, sx, sy, area)
                    break
                if best:
                    break
            if best:
                break
        spot = best or fallback
        if spot:
            x, y, angle, sx, sy, area = spot
            self.occ_poly(area, True)
            self.flatten(x, y, 75)
            if not self.known_objects or model in self.known_objects:
                self.add_obj(model, x, y, angle, 0, 'EARTH_ZombieTunnel', self.base_props)
        else:
            sx, sy = self._dry(cx, cy, 80)
            self.log('  pri strede sa nenašlo voľné miesto na zombie tunel')
        self.tunnel_exit = ('EARTH_ZTunnelExit', sx, sy)

    def place_nests(self):
        """Zombie spawn points. First rounds: 5 points spread along the top edge and 5 along the bottom edge; later
        (and always in Defense): a ring round the town - one point after another at random."""
        self.nests = [self.tunnel_exit] if self.central_tunnel else []
        n = max(4, int(self.opt['zombie_nests']) + 2)
        AX, AY, mg = self.AX, self.AY, 220.0
        # Clearance + Zombie: the zombies come from the north, from the insurgents' main base, through the town
        north = bool(self.opt['mode_clearance']) and not self.zsurv
        fx, fy = (AX / 2, AY / 2) if (self.zsurv or self.multi) else self.starts[0]
        early = []
        if north:
            for k in range(5):
                x = mg + (AX - 2 * mg) * (k + 0.5) / 5
                early.append(self._dry(x, AY - mg, 40))
                early.append(self._dry(x + (AX - 2 * mg) * 0.1, AY - mg * 2.5, 40))
        elif not self.zdef and not self.central_tunnel:   # from all four edges
            for k in range(4):
                f_ = (k + 0.5) / 4
                early.append(self._dry(mg + (AX - 2 * mg) * f_, AY - mg, 40))      # north
                early.append(self._dry(AX - mg, mg + (AY - 2 * mg) * f_, 40))      # east
                early.append(self._dry(mg + (AX - 2 * mg) * (1 - f_), mg, 40))     # south
                early.append(self._dry(mg, mg + (AY - 2 * mg) * (1 - f_), 40))     # west
        for k, (x, y) in enumerate(early):
            self.nests.append((f'EARTH_ZEdge{k + 1}', x, y))
        self.n_early = len(early)
        if north:                         # the later rounds: the whole northern quarter of the map
            for k in range(n):
                for tries in range(40):
                    x = mg + (AX - 2 * mg) * self.rng.random()
                    y = AY - mg - (AY * 0.25) * self.rng.random()
                    x, y = self._dry(x, y, 40)
                    if self._wet(x, y): continue
                    self.nests.append((f'EARTH_ZSpawn{k + 1}', x, y)); break
        rx, ry = min(0.38 * AX, 2600.0), min(0.38 * AY, 2600.0)       # a ring (an ellipse on a strip map)
        for k in (range(n) if not north and not self.central_tunnel else ()):
            t = math.tau * k / n + math.pi / n
            for tries in range(40):
                tt = t + (self.rng.random() - 0.5) * (0.3 if tries < 20 else 1.2)
                q = 1 - 0.3 * self.rng.random() * (tries >= 20)
                x = min(max(fx + math.cos(tt) * rx * q, mg), AX - mg); y = min(max(fy + math.sin(tt) * ry * q, mg), AY - mg)
                x, y = self._dry(x, y, 40)
                if self._wet(x, y): continue
                self.nests.append((f'EARTH_ZSpawn{k + 1}', x, y)); break
        self.stats['zombie_spawns'] = len(self.nests)
        # your shop on wheels (Humvee) appears next to start 1
        sx, sy = self.starts[0]
        self.humvee_wp = ('EARTH_HumveeSpawn',) + tuple(self._dry(sx + 160, sy + 60, 40))
        if self.multi:
            self.start_humvee_wps = [(f'EARTH_HumveeStart{k + 1}',) + tuple(self._dry(x + 160, y + 60, 40))
                                     for k, (x, y) in enumerate(self.starts)]
        if self.zsurv:
            # vehicles around the town centre (Defense: around the silo)
            cx_, cy_ = (self.silo[1], self.silo[2]) if getattr(self, 'silo', None) else (self.AX / 2, self.AY / 2)
            self.zs_wps = []
            # slots player0..: any of them can be a person (lobby slots may leave gaps - at least 8 as before)
            ns = self.zs_slots = min(MAX_PLAYERS, max(8, self.humans + 1))
            # free spots in the streets / squares near the centre (the town's buildings stay); every slot gets one,
            # several slots share a spot when there are few (a few metres apart), no big ring
            spots, r0 = [], (300 if self.zdef else 60)
            for r in range(int(r0), 1600, 30):
                for k in range(max(1, int(r / 25))):
                    t = math.tau * k / max(1, int(r / 25)) + r * 0.37
                    x, y = cx_ + math.cos(t) * r, cy_ + math.sin(t) * r
                    if not self.inside(x, y, 60) or self._wet(x, y): continue
                    if self.occ_test(self.rect(x, y, 60, 60, 0)) > 0.02: continue      # a building / tree is there
                    if any(math.hypot(x - a, y - b) < 110 for a, b in spots): continue
                    spots.append((x, y))
                    if len(spots) >= min(ns, 6): break
                if len(spots) >= min(ns, 6): break
            if not spots: spots = [self._dry(cx_, cy_, 40)]
            for i in range(ns):
                bx, by = spots[i % len(spots)]
                j = i // len(spots)                               # the 2nd, 3rd .. vehicle on the same spot: beside it
                ox, oy = [(0, 0), (55, 0), (-55, 0), (0, 55), (0, -55), (55, 55), (-55, -55), (55, -55), (-55, 55)][j % 9]
                x, y = self._dry(bx + ox, by + oy, 40)
                self.zs_wps.append((f'EARTH_HumveeSpawn{i}', x, y))
            # the Humvees are placed in the map for player0..7 (as in "[zombies] wheel of death"); a script deletes the
            # ones of empty slots and of the zombie AI
            for i in range(ns):
                _, x, y = self.zs_wps[i]
                for fam, (_, obj, tag) in ZS_FACTIONS.items():
                    self.add_obj(obj, x, y, math.atan2(cy_ - y, cx_ - x), 0, f'EARTH_{tag}{i}', self.base_props,
                                 f'teamplayer{i}')
            # Place one off-map waypoint per seat for the temporary respawn power source.
            rows = math.ceil(ns / 2)
            dy = min(150.0, (self.AY + 400) / max(1, rows))
            for i in range(ns):        # two columns in the left border
                x, y = -250.0 - (i % 2) * 220.0, 150.0 + (i // 2) * dy
                self.zs_wps.append((f'EARTH_RespWP{i}', x, y))

    def mode_scripts(self):
        """Clearance scripts (insurgent side). Skirmish ends with victory as soon as one alliance is left and the
        insurgents (a civilian side) do not count: with Clearance alone one AI slot is kept dormant (neutral, no money);
        when all insurgents are gone everybody becomes allies -> the game's own victory screen."""
        if self.zsurv:
            return self.survival_scripts()
        sc = []
        if not self.opt['mode_zombie']:          # with zombies the GLA AI is the zombie master - keep it hostile
            # the dormant AI = the lobby slots after the people (one person: slots 1..11; N people: N..11 - add the
            # AI after the people in the lobby): neutral to the people and without money, so the game neither ends
            # at once (allied people alone = victory) nor gets attacked
            np_ = self.n_people if self.multi else 1
            calm = []
            for h in range(np_):
                for j in range(np_, MAX_PLAYERS):
                    for a, b in ((h, j), (j, h)):
                        calm.append(('PLAYER_RELATES_PLAYER', [('side', f'player{a}'), ('side', f'player{b}'), ('relation', 1)]))
            sc.append(Script('EARTH_Mode_DormantAI', calm))
            sc.append(Script('EARTH_Mode_AINoMoney', [('PLAYER_SET_MONEY', [('side', f'player{i}'), ('int', 0)])
                                                      for i in range(np_, MAX_PLAYERS)], delay=20, once=False))
        if self.opt['mode_zombie'] and self.multi:
            # a shop Humvee next to every start, for whoever starts there (START_POSITION_IS); the zombie AI loses
            # its own (zombie_owner_scripts)
            for i in range(min(MAX_PLAYERS, max(8, self.players))):
                for k, (wp, _, _) in enumerate(getattr(self, 'start_humvee_wps', [])):
                    sc.append(Script(f'EARTH_Z_Humvee{i}_S{k + 1}', [('CREATE_NAMED_ON_TEAM_AT_WAYPOINT',
                                     [('unit', f'EARTH_ZHumvee{i}'), ('type', 'AmericaVehicleHumvee'),
                                      ('team', f'teamplayer{i}'), ('waypoint', wp)])],
                                     conditions=[('START_POSITION_IS', [('side', f'player{i}'), ('int', k + 1)])]))
        if self.opt['mode_zombie'] and self.multi:
            sc += self.zombie_owner_scripts(lambda i: [('NAMED_DELETE', [('unit', f'EARTH_ZHumvee{i}')])])
        elif self.opt['mode_zombie'] and getattr(self, 'humvee_wp', None):
            # the shop Humvee of "[zombies] wheel of death" (map.ini makes it a transport that trains infantry)
            sc.append(Script('EARTH_Z_Humvee', [('CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT',
                                                 [('type', 'AmericaVehicleHumvee'), ('team', 'teamplayer0'),
                                                  ('waypoint', self.humvee_wp[0])])]))
        if self.opt['mode_clearance']:
            # on-screen counter of insurgents that can still fight (units, garrisons, defences). Scripts cannot add
            # numbers up, so the count is shown in steps: exact up to 20, then by 5 / 25 / 100.
            th = list(range(0, 21)) + list(range(25, 101, 5)) + list(range(125, 1001, 25)) + list(range(1100, 10001, 100))
            area = ('area', 'EARTH_WholeMap')
            obj = getattr(self, 'objectives', [])
            if obj:
                intro = [('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_ObjIntro')]),
                         ('SET_COUNTER', [('counter', 'EARTH_ObjDone'), ('int', 0)]),
                         ('DISPLAY_COUNTER', [('counter', 'EARTH_ObjDone'), ('text', 'SCRIPT:EARTH_ObjDone')])]
                intro += [('MAP_REVEAL_AT_WAYPOINT', [('waypoint', o['wp']), ('real', 120),
                                                      ('side', '' if self.multi else 'player0')]) for o in obj]
                sc.append(Script('EARTH_Obj_Intro', intro))
                reward = int(self.opt['mission_reward'])
                for o in obj:
                    people = list(range(self.n_people)) if self.multi else [0]
                    done = [('DISPLAY_TEXT', [('text', f"SCRIPT:EARTH_Obj_{o['key']}_Done")])]
                    done += [('PLAYER_GIVE_MONEY', [('side', f'player{i}'), ('int', reward)]) for i in people]
                    done += [('INCREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_ObjDone')])]
                    if not self.multi:      # several people: money for everybody, no units for one of them
                        done += [('CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT', [('type', u), ('team', 'teamplayer0'),
                                                                         ('waypoint', o['wp'])]) for u in o['reward_units']]
                    if o['kind'] == 'destroy':
                        groups = [[('NAMED_DESTROYED', [('unit', o['uid'])])]]
                    else:      # freed = no insurgent left there and a unit of a person (any person) inside
                        groups = [[('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                                    [('side', INS_SIDE), ('comparison', 1), ('int', 0), ('kindof', 'CAN_ATTACK'), ('area', o['area'])]),
                                   ('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                                    [('side', f'player{i}'), ('comparison', 3), ('int', 1), ('kindof', 'CAN_ATTACK'), ('area', o['area'])])]
                                  for i in people]
                    sc.append(Script(f"EARTH_Obj_{o['key']}", done, any_of=groups, delay=2))
            sc.append(Script('EARTH_C_Counter', [('SET_COUNTER', [('counter', 'EARTH_InsLeft'), ('int', 0)]),
                                                 ('DISPLAY_COUNTER', [('counter', 'EARTH_InsLeft'),
                                                                      ('text', 'SCRIPT:EARTH_InsLeft')])]))
            for k, t in enumerate(th):
                cond = [('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                         [('side', INS_SIDE), ('comparison', 1), ('int', t), ('kindof', 'CAN_ATTACK'), area])]
                if k > 0:
                    cond.append(('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                                 [('side', INS_SIDE), ('comparison', 4), ('int', th[k - 1]), ('kindof', 'CAN_ATTACK'), area]))
                sc.append(Script(f'EARTH_C_Left{t}', [('SET_COUNTER', [('counter', 'EARTH_InsLeft'), ('int', t)])],
                                 conditions=cond, delay=2, once=False))
            win = []
            pairs = [(a, b) for a in range(MAX_PLAYERS) for b in range(MAX_PLAYERS) if a != b] if self.multi else \
                [p for i in range(1, MAX_PLAYERS) for p in ((0, i), (i, 0))]
            for a, b in pairs:
                win.append(('PLAYER_RELATES_PLAYER', [('side', f'player{a}'), ('side', f'player{b}'), ('relation', 2)]))
            groups = [[('PLAYER_ALL_DESTROYED', [('side', INS_SIDE)])]]
            if getattr(self, 'main_base', None):
                # the main base in the north is the goal: no insurgent building / tower left there = won
                groups.append([('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                                [('side', INS_SIDE), ('comparison', 1), ('int', 0), ('kindof', 'STRUCTURE'),
                                 ('area', 'EARTH_InsMain')])])
                sc.append(Script('EARTH_C_MainIntro', [
                    ('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_C_MainIntro')]),
                    ('MAP_REVEAL_AT_WAYPOINT', [('waypoint', 'EARTH_InsMainWP'), ('real', 160),
                                                ('side', '' if self.multi else 'player0')])]))
                win = [('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_C_MainDown')])] + win
            sc.append(Script('EARTH_Mode_Victory', win, any_of=groups, delay=5))
        return sc

    def zombie_scripts(self):
        """Zombie master scripts on the SkirmishGLA side (the lobby's GLA AI runs them), on the principle of
        '[zombies] wheel of death': rounds on a timer, a counter of zombies to spawn, a counter that cycles every frame
        over the spawn points so they come from all edges, TEAM_HUNT every few seconds, counters shown on screen."""
        Z = self.ZOMBIE_SIDE
        sp = getattr(self, 'nests', [])
        n = len(sp)
        if not n: return []
        grace = max(0, int(self.opt['zombie_start']))
        continuous = self.opt['zombie_pacing'] == 'continuous'
        limit = min(ZOMBIE_MAX_LIVE_LIMIT, max(1, int(self.opt['zombie_live_limit'])))
        every = max(20, int(self.opt['zombie_interval']))
        size = max(1, int(self.opt['zombie_size']))
        grow = int(self.opt['zombie_growth']) if self.opt['zombie_growth'] is not None else max(1, size // 2)
        R = 40
        area = ('area', 'EARTH_WholeMap')
        live_space = [('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                       [('side', Z), ('comparison', 0), ('int', limit), ('kindof', 'CAN_ATTACK'), area])] if continuous else []

        def level_range(first, last):
            cond = [('COUNTER', [('counter', 'EARTH_ZRound'), ('comparison', 3), ('int', first)])]
            if last is not None:
                cond.append(('COUNTER', [('counter', 'EARTH_ZRound'), ('comparison', 1), ('int', last)]))
            return cond

        teams = [f'EARTH_Zombies{k + 1}' for k in range(n)] + ['EARTH_ZombiesBig']
        sc = [Script('EARTH_Z_Setup', [('SET_COUNTER', [('counter', 'EARTH_ZRound'), ('int', 0)]),
                                       ('SET_COUNTER', [('counter', 'EARTH_ZLeft'), ('int', 0)]),
                                       ('SET_COUNTER', [('counter', 'EARTH_ZAmount'), ('int', 0)]),
                                       ('SET_MILLISECOND_TIMER', [('counter', 'EARTH_ZNext'), ('real', grace + 1)]),
                                       ('DISPLAY_COUNTER', [('counter', 'EARTH_ZRound'), ('text', 'SCRIPT:EARTH_ZRound')])]),
              Script('EARTH_Z_NoMoney', [('PLAYER_SET_MONEY', [('side', Z), ('int', 0)])], delay=15, once=False)]
        if not continuous or self.zsurv or self.multi:
            sc.append(Script('EARTH_Z_Mark', [('CREATE_NAMED_ON_TEAM_AT_WAYPOINT', [('unit', 'EARTH_ZMark'),
                             ('type', 'CINE_GLAInfantryTerrorist'), ('team', teams[0]), ('waypoint', sp[0][0])])]))
        if self.zsurv and getattr(self, 'air_paths', None):
            # "[zombies] wheel of death" DyingMigs: from round 6 MiGs fly over the town and are shot down after
            # 6 - 11 s. In Waves the wreck also spills burning zombies.
            na = len(self.air_paths)
            sc.append(Script('EARTH_Z_MigStart', [('SET_RANDOM_MSEC_TIMER', [('counter', 'EARTH_ZMigT'), ('real', 8), ('real', 16)])],
                             conditions=[('COUNTER', [('counter', 'EARTH_ZRound'), ('comparison', 3), ('int', 6)])]))
            for k in range(1, na + 1):
                sc.append(Script(f'EARTH_Z_Mig{k}', [
                    ('CREATE_NAMED_ON_TEAM_AT_WAYPOINT', [('unit', f'EARTH_Mig{k}'), ('type', 'ChinaJetMIG'),
                                                          ('team', 'EARTH_ZMigs'), ('waypoint', f'EARTH_AirSpawn{k}')]),
                    ('NAMED_FOLLOW_WAYPOINTS', [('unit', f'EARTH_Mig{k}'), ('path', f'EARTH_AirPath{k}')]),
                    ('SET_RANDOM_MSEC_TIMER', [('counter', f'EARTH_ZMigDie{k}'), ('real', 6), ('real', 11)]),
                    ('SET_RANDOM_MSEC_TIMER', [('counter', 'EARTH_ZMigT'), ('real', 8), ('real', 16)]),
                    ('INCREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_ZMigK')])],
                    conditions=[('COUNTER', [('counter', 'EARTH_ZRound'), ('comparison', 3), ('int', 6)]),
                                ('TIMER_EXPIRED', [('counter', 'EARTH_ZMigT')]),
                                ('COUNTER', [('counter', 'EARTH_ZMigK'), ('comparison', 2), ('int', k - 1)]),
                                ('PLAYER_HAS_OBJECT_COMPARISON', [('side', Z), ('comparison', 0), ('int', 40),
                                                                  ('type', 'CINE_GLAInfantryTerrorist')])] + live_space,
                    once=False))
                sc.append(Script(f'EARTH_Z_MigDie{k}', [('NAMED_KILL', [('unit', f'EARTH_Mig{k}')])],
                                 conditions=[('TIMER_EXPIRED', [('counter', f'EARTH_ZMigDie{k}')])], delay=1, once=False))
            sc.append(Script('EARTH_Z_MigWrap', [('SET_COUNTER', [('counter', 'EARTH_ZMigK'), ('int', 0)])],
                             conditions=[('COUNTER', [('counter', 'EARTH_ZMigK'), ('comparison', 3), ('int', na)])],
                             once=False))
        if continuous:
            self.map_strings = dict(getattr(self, 'map_strings', self.ZOMBIE_STR),
                                    EARTH_ZRound='Pressure phase:', EARTH_ZAlive='Zombies active:',
                                    EARTH_ZLimit='Zombie limit:')
            sc[0].actions += [
                ('SET_COUNTER', [('counter', 'EARTH_ZLimit'), ('int', limit)]),
                ('DISPLAY_COUNTER', [('counter', 'EARTH_ZLimit'), ('text', 'SCRIPT:EARTH_ZLimit')])]
            sc.append(Script('EARTH_Z_Advance', [
                ('INCREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_ZRound')]),
                ('SET_MILLISECOND_TIMER', [('counter', 'EARTH_ZNext'), ('real', every)])],
                conditions=[('TIMER_EXPIRED', [('counter', 'EARTH_ZNext')])], once=False))
            # The original boss schedule ends at phase 40. Regular zombies keep spawning.
            for phase in range(5, R + 1, 5):
                sc.append(Script(f'EARTH_Z_Boss{phase}', [
                    ('SET_COUNTER', [('counter', 'EARTH_ZBig'), ('int', min(3, phase // 5))])],
                    conditions=[('COUNTER', [('counter', 'EARTH_ZRound'), ('comparison', 2), ('int', phase)])]))
        else:
            sc[0].actions.append(('DISPLAY_COUNTER', [('counter', 'EARTH_ZAmount'), ('text', 'SCRIPT:EARTH_ZAmount')]))
            for k in range(1, R + 1):
                amount = size + (k - 1) * grow + (k // 5) * size
                big = k // 5 if k % 5 == 0 else 0
                acts = [('INCREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_ZRound')]),
                        ('INCREMENT_COUNTER', [('int', amount), ('counter', 'EARTH_ZLeft')]),
                        ('SET_COUNTER', [('counter', 'EARTH_ZAmount'), ('int', amount)]),
                        # The long timer is a fallback when a zombie cannot be reached.
                        ('SET_MILLISECOND_TIMER', [('counter', 'EARTH_ZNext'),
                                                   ('real', 36000 if (self.opt['mode_clearance'] and not self.zsurv) else 300)]),
                        ('SET_COUNTER', [('counter', 'EARTH_ZWait'), ('int', 1)])]
                if big:
                    acts.append(('INCREMENT_COUNTER', [('int', big), ('counter', 'EARTH_ZBig')]))
                cond = [('TIMER_EXPIRED', [('counter', 'EARTH_ZNext')]),
                        ('COUNTER', [('counter', 'EARTH_ZRound'), ('comparison', 2 if k < R else 3), ('int', k - 1)])]
                sc.append(Script(f'EARTH_Z_Round{k}', acts, conditions=cond, once=(k < R)))
            # The next wave waits until every zombie has spawned and been killed.
            sc.append(Script('EARTH_Z_Cleared', [
            ('SET_COUNTER', [('counter', 'EARTH_ZWait'), ('int', 0)]),
            ('SET_MILLISECOND_TIMER', [('counter', 'EARTH_ZNext'), ('real', max(5, int(self.opt['zombie_break'])))]),
            ('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_ZCleared')])],
            conditions=[('COUNTER', [('counter', 'EARTH_ZWait'), ('comparison', 2), ('int', 1)]),
                        ('COUNTER', [('counter', 'EARTH_ZLeft'), ('comparison', 1), ('int', 0)]),
                        ('COUNTER', [('counter', 'EARTH_ZBig'), ('comparison', 1), ('int', 0)]),
                        ('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                         [('side', Z), ('comparison', 1), ('int', 0), ('kindof', 'CAN_ATTACK'), area])],
            delay=2, once=False))
        # zombies trickle in: a gate opens every 30/rate frames and lets ONE zombie out; the point is picked by a
        # counter that runs every frame, so it changes from zombie to zombie (random sides, all through the wave)
        ne = getattr(self, 'n_early', 0)
        nr = n - ne
        EARLY = ZOMBIE_EARLY_ROUNDS if ne else 0
        gap = max(1, int(round(30 / max(0.2, float(self.opt['zombie_rate'])))))
        sc.append(Script('EARTH_Z_Gate', [('INCREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_ZGate')])], once=False))
        for cname, cnt in (('EARTH_ZSpreadE', ne), ('EARTH_ZSpread', nr)):
            if cnt <= 0: continue
            sc.append(Script(f'{cname}_Step', [('INCREMENT_COUNTER', [('int', 1), ('counter', cname)])], once=False))
            sc.append(Script(f'{cname}_Wrap', [('SET_COUNTER', [('counter', cname), ('int', 0)])],
                             conditions=[('COUNTER', [('counter', cname), ('comparison', 3), ('int', cnt)])], once=False))
        tiers = self.zsurv or bool(self.opt['zombie_growing'])
        if tiers:
            # a pseudo-random number 0..99 that jumps every frame picks the zombie tier by the round's mix
            sc.append(Script('EARTH_Z_Pick', [('INCREMENT_COUNTER', [('int', 37), ('counter', 'EARTH_ZPick')])], once=False))
            sc.append(Script('EARTH_Z_PickWrap', [('DECREMENT_COUNTER', [('int', 100), ('counter', 'EARTH_ZPick')])],
                             conditions=[('COUNTER', [('counter', 'EARTH_ZPick'), ('comparison', 3), ('int', 100)])],
                             once=False))
        mixes = ZS_MIX if tiers else [(1, 2, (100, 0)), (3, None, (0, 100))]
        if continuous and tiers:
            first, _, weights = ZS_MIX[-1]
            mixes = ZS_MIX[:-1] + [(first, None, weights)]
        units = [t[0] for t in ZS_TIERS] if tiers else ['GLAInfantryTerrorist', 'Chem_GLAInfantryTerrorist']
        gate = [('COUNTER', [('counter', 'EARTH_ZGate'), ('comparison', 3), ('int', gap)])]
        regular = [('COUNTER', [('counter', 'EARTH_ZLeft'), ('comparison', 4), ('int', 0)])]
        spend = [('DECREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_ZLeft')])]
        close_gate = [('SET_COUNTER', [('counter', 'EARTH_ZGate'), ('int', 0)])]
        if continuous:
            max_rate = min(3.0, max(0.5, float(self.opt['zombie_rate'])))
            gate = [('COUNTER', [('counter', 'EARTH_ZCanSpawn'), ('comparison', 2), ('int', 1)])]
            regular = [('COUNTER', [('counter', 'EARTH_ZBig'), ('comparison', 1), ('int', 0)])]
            spend = []
            close_gate += [('SET_COUNTER', [('counter', name), ('int', 0)])
                           for name in ('EARTH_ZOpen', 'EARTH_ZCanSpawn')]
            for lo, hi in ((1, 2), (3, 5), (6, 9), (10, 15), (16, None)):
                rate = min(max_rate, max(0.2, (size + (lo - 1) * (grow + size / 10)) / every))
                sc.append(Script(f'EARTH_Z_Pace{lo}', [('SET_COUNTER', [('counter', 'EARTH_ZOpen'), ('int', 1)])],
                                 conditions=level_range(lo, hi) + [
                                     ('COUNTER', [('counter', 'EARTH_ZGate'), ('comparison', 3), ('int', math.ceil(30 / rate))])],
                                 once=False))
            # Check the arena once for all spawn points and close the gate after the first spawn.
            sc.append(Script('EARTH_Z_Population', [
                ('SET_COUNTER', [('counter', 'EARTH_ZCanSpawn'), ('int', 1)])],
                conditions=[('COUNTER', [('counter', 'EARTH_ZOpen'), ('comparison', 2), ('int', 1)])] + live_space,
                else_actions=[('SET_COUNTER', [('counter', 'EARTH_ZCanSpawn'), ('int', 0)])], once=False))
        for k, (wp, x, y) in enumerate(sp):
            if k < ne: phase, where = (1, EARLY if nr else None), [('COUNTER', [('counter', 'EARTH_ZSpreadE'), ('comparison', 2), ('int', k)])]
            else: phase, where = (EARLY + 1, None), [('COUNTER', [('counter', 'EARTH_ZSpread'), ('comparison', 2), ('int', k - ne)])]
            for lo, hi, mix in mixes:
                lo2 = max(lo, phase[0])
                hi2 = min((end for end in (hi, phase[1]) if end is not None), default=None)
                if hi2 is not None and lo2 > hi2: continue
                a = 0
                for t, pct in enumerate(mix):
                    if pct <= 0: continue
                    cond = regular + gate + where + level_range(lo2, hi2)
                    if pct < 100:
                        cond += [('COUNTER', [('counter', 'EARTH_ZPick'), ('comparison', 3), ('int', a)]),
                                 ('COUNTER', [('counter', 'EARTH_ZPick'), ('comparison', 0), ('int', a + pct)])]
                    sc.append(Script(f'EARTH_Z_Spawn{k}_R{lo2}_T{t + 1}', spend + close_gate + [
                        ('CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT', [('type', units[t]), ('team', teams[k]), ('waypoint', wp)])],
                        conditions=cond, once=False))
                    a += pct
            sc.append(Script(f'EARTH_Z_Big{k}', [
                ('DECREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_ZBig')])] + close_gate + [
                ('CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT', [('type', 'GLAVehicleBombTruck'), ('team', teams[-1]), ('waypoint', wp)])],
                conditions=[('COUNTER', [('counter', 'EARTH_ZBig'), ('comparison', 4), ('int', 0)])]
                + gate + level_range(*phase) + where,
                once=False))
        # "Zombies left": the living zombies counted on the whole map (exact up to 30, then in steps)
        th = list(range(0, 31)) + list(range(35, 101, 5)) + list(range(110, 301, 10)) + list(range(325, 1001, 25)) \
            + list(range(1100, 3001, 100))
        if continuous:
            th = [t for t in th if t < limit] + [limit]
        sc.append(Script('EARTH_Z_LeftShow', [('SET_COUNTER', [('counter', 'EARTH_ZAlive'), ('int', 0)]),
                                              ('DISPLAY_COUNTER', [('counter', 'EARTH_ZAlive'), ('text', 'SCRIPT:EARTH_ZAlive')])]))
        for k_, t_ in enumerate(th):
            cond = [('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA', [('side', Z), ('comparison', 1), ('int', t_),
                                                                          ('kindof', 'CAN_ATTACK'), area])]
            if k_:
                cond.append(('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA', [('side', Z), ('comparison', 4),
                                                                                  ('int', th[k_ - 1]), ('kindof', 'CAN_ATTACK'), area]))
            sc.append(Script(f'EARTH_Z_Left{t_}', [('SET_COUNTER', [('counter', 'EARTH_ZAlive'), ('int', t_)])],
                             conditions=cond, delay=1, once=False))
        if self.zdef:
            # Defense: the zombies attack-move to the silo - on the way they jump on any person within their sight
            # (VisionRange 300 = about 100 m for an AI unit); close to the silo they go for the silo itself
            for t in teams:
                sc.append(Script(f'EARTH_Z_Go_{t}', [('TEAM_ATTACK_NAMED', [('team', t), ('unit', 'EARTH_Silo')])],
                                 conditions=[('TEAM_INSIDE_AREA_PARTIALLY', [('team', t), ('area', 'EARTH_SiloZone'),
                                                                             ('surfaces', 3)])],
                                 else_actions=[('TEAM_USE_COMMANDBUTTON_ABILITY_AT_WAYPOINT',
                                                [('team', t), ('button', 'Command_AttackMove'), ('waypoint', 'EARTH_SiloWP')])],
                                 delay=4, once=False))
        else:
            sc.append(Script('EARTH_Z_Hunt', [('TEAM_HUNT', [('team', t)]) for t in teams], delay=3, once=False))
        self.zombie_teams = teams
        return sc

    def defense_scripts(self, human):
        """Defense: the silo belongs to the insurgent side (unused in this mode): neutral to the people (they do not
        shoot it, and can capture it), enemy of the zombie AI. A capture = repair: the silo is replaced by a new one.
        Silo destroyed = radiation and game over."""
        sc = []
        for i in range(MAX_PLAYERS):
            P = f'player{i}'
            calm = [('PLAYER_RELATES_PLAYER', [('side', INS_SIDE), ('side', P), ('relation', 1)]),
                    ('PLAYER_RELATES_PLAYER', [('side', P), ('side', INS_SIDE), ('relation', 1)])]
            hate = [('PLAYER_RELATES_PLAYER', [('side', INS_SIDE), ('side', P), ('relation', 0)]),
                    ('PLAYER_RELATES_PLAYER', [('side', P), ('side', INS_SIDE), ('relation', 0)])]
            sc.append(Script(f'EARTH_D_Rel{i}', calm, conditions=human(i)))
            sc.append(Script(f'EARTH_D_Hate{i}', hate,
                             conditions=[('COUNTER', [('counter', f'EARTH_IsZ{i}'), ('comparison', 2), ('int', 1)])]))
            # captured = the silo stays the person's: its nuke is theirs (no power needed); lost only when destroyed
            sc.append(Script(f'EARTH_D_Captured{i}', [('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_D_Repaired')])],
                             conditions=[('NAMED_OWNED_BY_PLAYER', [('unit', 'EARTH_Silo'), ('side', P)])]))
        sc.append(Script('EARTH_D_Intro', [('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_D_Intro')]),
                                           ('MAP_REVEAL_AT_WAYPOINT', [('waypoint', 'EARTH_SiloWP'), ('real', 300), ('side', '')])]))
        for i in range(MAX_PLAYERS):
            P = f'player{i}'
            sc.append(Script(f'EARTH_D_Lost{i}', [('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_D_Lost')]),
                                                  ('PLAYER_KILL', [('side', P)])],
                             conditions=[('NAMED_DESTROYED', [('unit', 'EARTH_Silo')])] + human(i), delay=1))
        return sc

    ZOMBIE_ONLY = ('CINE_GLAInfantryTerrorist', 'GC_Slth_GLAInfantryTerrorist', 'GC_Chem_GLAInfantryTerrorist')

    def zombie_owner_scripts(self, found):
        """Who is the zombie AI (people's side). The zombie side itself creates a marker zombie EARTH_ZMark at frame
        0 (zombie_scripts - the same action, team and kind of waypoint as every zombie spawn); its owner, or the owner
        of any zombie-only unit (CINE / GC terrorists nobody can build), is the zombie AI -> EARTH_IsZ<i> = 1,
        EARTH_ZFound = 1, the marker goes. found(i) = more actions for that player (its cars). Nothing else waits
        for this any more: cars and money go by faction, as in "[zombies] wheel of death"."""
        sc = []
        for i in range(MAX_PLAYERS):
            P = f'player{i}'
            groups = [[('NAMED_OWNED_BY_PLAYER', [('unit', 'EARTH_ZMark'), ('side', P)])]]
            groups += [[('PLAYER_HAS_OBJECT_COMPARISON', [('side', P), ('comparison', 3), ('int', 1), ('type', t)])]
                       for t in self.ZOMBIE_ONLY]
            sc.append(Script(f'EARTH_ZIs{i}', [('SET_COUNTER', [('counter', f'EARTH_IsZ{i}'), ('int', 1)]),
                                               ('SET_COUNTER', [('counter', 'EARTH_ZFound'), ('int', 1)]),
                                               ('NAMED_DELETE', [('unit', 'EARTH_ZMark')])] + found(i),
                             any_of=groups))
        return sc

    def survival_scripts(self):
        """Give a downed player one targeted respawn after the configured delay."""
        sc = []
        arena = ('area', 'EARTH_Arena')
        resp = max(10, int(self.opt['zs_respawn']))
        # the zombie AI: the owner of the marker / zombie-only units (zombie_owner_scripts); its cars go away
        cars = lambda i: [('NAMED_DELETE', [('unit', f'EARTH_{t}{i}')]) for _, _, t in ZS_FACTIONS.values()] \
            if i < self.zs_slots else []
        sc += self.zombie_owner_scripts(cars)
        sc.append(Script('EARTH_S_NoGLA', [('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_S_NoGLA')])],
                         conditions=[('TIMER_EXPIRED', [('counter', 'EARTH_S_Ready')]),
                                     ('COUNTER', [('counter', 'EARTH_ZFound'), ('comparison', 2), ('int', 0)])]))
        # a person = not the zombie AI, once the first 25 s are over (by then the zombie AI has shown itself)
        human = lambda i: [('TIMER_EXPIRED', [('counter', 'EARTH_S_Ready')]),
                           ('COUNTER', [('counter', f'EARTH_S_Seat{i}'), ('comparison', 2), ('int', 1)]),
                           ('COUNTER', [('counter', f'EARTH_IsZ{i}'), ('comparison', 2), ('int', 0)])]
        self._human = human
        sc.append(Script('EARTH_S_Ready', [('SET_MILLISECOND_TIMER', [('counter', 'EARTH_S_Ready'), ('real', 25)]),
                                           ('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_S_Intro')])]))
        all_factions = [nm for names, _, _ in ZS_FACTIONS.values() for nm in names]
        for i in range(MAX_PLAYERS):
            P = f'player{i}'
            seat = [[('SKIRMISH_PLAYER_FACTION', [('side', P), ('faction', nm)])] for nm in all_factions]
            # Empty lobby seats lose their cars, as in wheel of death.
            acts = [('SET_COUNTER', [('counter', f'EARTH_S_Seat{i}'), ('int', 1)]),
                    ('PLAYER_GRANT_SCIENCE', [('side', P), ('science', 'SCIENCE_CashBounty1')]),
                    ('PLAYER_GRANT_SCIENCE', [('side', P), ('science', 'SCIENCE_TankParadrop1')]),
                    ('PLAYER_SET_MONEY', [('side', P), ('int', int(self.opt['zd_start_money' if self.zdef else 'zs_start_money']))])]
            sc.append(Script(f'EARTH_S_Start{i}', acts, any_of=seat, else_actions=cars(i)))
            if i < self.zs_slots:
                # the faction's own car stays, the other two go ("0$-Player<i>-USA" etc.)
                for fam, (names, _, tag) in ZS_FACTIONS.items():
                    others = [('NAMED_DELETE', [('unit', f'EARTH_{t}{i}')]) for f2, (_, _, t) in ZS_FACTIONS.items() if f2 != fam]
                    sc.append(Script(f'EARTH_S_Keep{fam}{i}', others,
                                     any_of=[[('SKIRMISH_PLAYER_FACTION', [('side', P), ('faction', nm)])] for nm in names]))
        # camera: the starts are outside the playable area (black) - everybody's camera goes to the vehicles in the
        # town (Defense: the silo), the same for all, as "[zombies] wheel of death" does (RESET_CAMERA 'Middle').
        # (v0.37.1 moved each camera to its own vehicle via "<Local Player>" - the host's game froze with it.)
        cam = 'EARTH_SiloWP' if getattr(self, 'silo', None) else 'EARTH_HumveeSpawn0'
        sc.append(Script('EARTH_S_Camera', [('RESET_CAMERA', [('waypoint', cam), ('real', 0.0), ('real', 0.0), ('real', 0.0)])]))
        if not self.zdef:
            # Solo players can respawn. Co-op Survival still ends when the whole team is out of the arena.
            sc.append(Script('EARTH_S_AliveReset', [('SET_COUNTER', [('counter', name), ('int', 0)])
                                                   for name in ('EARTH_S_Alive', 'EARTH_S_People')], once=False))
            for i in range(self.zs_slots):
                sc.append(Script(f'EARTH_S_People{i}', [('INCREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_S_People')])],
                                 conditions=human(i), once=False))
                sc.append(Script(f'EARTH_S_Alive{i}', [('INCREMENT_COUNTER', [('int', 1), ('counter', 'EARTH_S_Alive')])],
                                 conditions=human(i) + [('SKIRMISH_PLAYER_HAS_UNITS_IN_AREA', [('side', f'player{i}'), arena])],
                                 once=False))
        shops = [obj for _, obj, _ in ZS_FACTIONS.values()]
        for i in range(self.zs_slots):
            P = f'player{i}'
            st = f'EARTH_S_State{i}'
            relay, timer = f'EARTH_Resp{i}', f'EARTH_S_Resp{i}'
            # Track the shop vehicle. Surviving infantry or a captured silo do not block its replacement.
            shop_count = lambda comparison, count, obj: ('PLAYER_HAS_COMPARISON_UNIT_TYPE_IN_TRIGGER_AREA',
                                                          [('side', P), ('comparison', comparison), ('int', count),
                                                           ('type', obj), arena])
            no_shop = [shop_count(2, 0, obj) for obj in shops]
            sc.append(Script(f'EARTH_S_Down{i}', [
                ('SET_COUNTER', [('counter', st), ('int', 1)]),
                ('SET_MILLISECOND_TIMER', [('counter', timer), ('real', resp)]),
                ('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_S_Down')])],
                conditions=human(i) + [('COUNTER', [('counter', st), ('comparison', 2), ('int', 0)])] + no_shop,
                delay=1, once=False))
            ready = human(i) + [('COUNTER', [('counter', st), ('comparison', 2), ('int', 1)]),
                                ('TIMER_EXPIRED', [('counter', timer)])] + no_shop
            eligibility = [ready] if self.zdef else [
                ready + [('COUNTER', [('counter', 'EARTH_S_People'), ('comparison', 2), ('int', 1)])],
                ready + [('COUNTER', [('counter', 'EARTH_S_Alive'), ('comparison', 3), ('int', 1)])]]
            sc.append(Script(f'EARTH_S_RespawnReady{i}', [
                ('CREATE_NAMED_ON_TEAM_AT_WAYPOINT', [('unit', relay), ('type', 'AmericaBarracks'),
                                                      ('team', f'teamplayer{i}'), ('waypoint', f'EARTH_RespWP{i}')]),
                ('SET_COUNTER', [('counter', st), ('int', 2)]),
                ('DISPLAY_TEXT', [('text', 'SCRIPT:EARTH_S_RespawnReady')])],
                any_of=eligibility, once=False))
            # Consume the power on a successful respawn. An unusable target can be retried after its short reload.
            pending = [('COUNTER', [('counter', st), ('comparison', 3), ('int', 1)])]
            sc.append(Script(f'EARTH_S_Returned{i}', [
                ('NAMED_DELETE', [('unit', relay)]),
                ('SET_COUNTER', [('counter', st), ('int', 0)])],
                any_of=[pending + [shop_count(3, 1, obj)] for obj in shops], once=False))
        if self.zdef:
            sc += self.defense_scripts(human)
        for i in range(self.zs_slots):
            if self.zdef: break                # Defense: you only lose with the silo
            P = f'player{i}'
            sc.append(Script(f'EARTH_S_GameOver{i}', [('PLAYER_KILL', [('side', P)])],
                             conditions=[('COUNTER', [('counter', 'EARTH_S_People'), ('comparison', 3), ('int', 2)]),
                                         ('COUNTER', [('counter', 'EARTH_S_Alive'), ('comparison', 1), ('int', 0)])] + human(i),
                             delay=1, once=False))
        return sc
