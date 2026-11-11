"""Insurgents: houses, scripts, QRF, objectives, checkpoints, bases, fortress, zones."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class InsurgentsMixin:
    def place_checkpoints(self):
        """Insurgent checkpoints on the town's streets: sandbags on both kerbs, a barricade half across the road
        (one lane stays open), a Stinger site beside it, crates / tyres and a few guards."""
        if not (self.opt['insurgents'] and not self.zsurv): return
        R, P = self.rng, self.p
        self.ndetail = getattr(self, 'ndetail', 0)
        pts = []
        for rt, run in self.road_lines:
            if rt not in ASPHALT_ROADS: continue
            for a_, b_ in zip(run[:-1], run[1:]):
                L = math.hypot(b_[0] - a_[0], b_[1] - a_[1])
                if L < 60: continue
                pts.append(((a_[0] + b_[0]) / 2, (a_[1] + b_[1]) / 2, math.atan2(b_[1] - a_[1], b_[0] - a_[0]), rt))
        R.shuffle(pts)
        want = max(3, min(12, int(self.AX * self.AY / (2200 * 2200))))
        keep_out = max(self.base_clear * 1.5 + 300, AI_BUILD_R)
        done = []
        for x, y, a, rt in pts:
            if len(done) >= want: break
            if not self.in_zone(x, y) or self._wet(x, y): continue
            if any(math.hypot(x - sx, y - sy) < keep_out for sx, sy in self.starts): continue
            if any(math.hypot(x - dx, y - dy) < 900 for dx, dy in done): continue
            w = self.road_w[rt]
            nx, ny = -math.sin(a), math.cos(a)
            side = 1 if R.random() < 0.5 else -1
            made = 0
            for k in (-1, 1):                                     # sandbags along both kerbs (model long side = its y)
                made += self._prop('SandBagWall01', x + nx * k * (w / 2 + 8), y + ny * k * (w / 2 + 8),
                                   a + math.pi / 2, 12, force=True)
            for q_ in (0.15, 0.4):                                # sandbags across one half of the road
                made += self._prop('SandBagWall01', x + nx * side * w * q_, y + ny * side * w * q_, a, 10, force=True)
            for prop in ('TirePile', 'BigWoodenBarrels', 'SandbagPile01'):
                t_ = R.random() * math.tau
                self._prop(prop, x + nx * side * (w / 2 + 30) + math.cos(t_) * 14, y + ny * side * (w / 2 + 30) + math.sin(t_) * 14,
                           R.random() * math.tau, 10)
            # a Stinger site beside the road
            sx_, sy_ = x - nx * side * (w / 2 + 55), y - ny * side * (w / 2 + 55)
            sz = self.cat.buildings.get('GLAStingerSite', (60, 60, 20))
            if (not self.known_objects or 'GLAStingerSite' in self.known_objects) and self.inside(sx_, sy_, 30) \
                    and self.occ_test(self.rect(sx_, sy_, sz[0], sz[1], a)) <= 0.02:
                self.occ_poly(self.rect(sx_, sy_, sz[0], sz[1], a), True)
                self.add_obj('GLAStingerSite', sx_, sy_, a, 0, f'EARTH_Check_{len(done) + 1}_Stinger', self.base_props, INS_TEAM)
            for g_ in range(3):                                    # guards at the barricade
                gx_, gy_ = x + nx * side * (w / 2 + 20) + math.cos(a) * (g_ - 1) * 14, y + ny * side * (w / 2 + 20) + math.sin(a) * (g_ - 1) * 14
                unit = 'GLAInfantryTunnelDefender' if g_ == 1 else 'GLAInfantryRebel'
                if self.known_objects and unit not in self.known_objects: unit = 'GLAInfantryRebel'
                self.objs.append(MapObject(3, float(gx_), float(gy_), 0.0, (a + math.pi / 2 * side) % math.tau, 0, unit,
                                           self.mkprops(f'EARTH_Check_{len(done) + 1}_G{g_}', self.base_props, INS_TEAM)))
            done.append((x, y))
        self.stats['checkpoints'] = len(done)

    # ----------------------------------------------------------- town clearing: insurgents in houses
    def place_insurgents(self):
        """Every N-th garrisonable house outside the player bases gets 1-4 insurgents next to its door;
        the start-up script sends each of them into that house (NAMED_GARRISON_SPECIFIC_BUILDING)."""
        R = self.rng
        every = max(1, int(self.opt['ins_every'])); cap = max(1, min(4, int(self.opt['ins_max'])))
        lvl = max(1, min(3, int(self.opt['ins_difficulty'])))
        mix = [(n, w) for n, w in INS_MIX[lvl] if not self.known_objects or n in self.known_objects]
        if not mix:
            return
        names, weights = zip(*mix)
        # the skirmish AI refuses to build where an enemy object stands (AIPlayer: NO_ENEMY_OBJECT_OVERLAP), and its
        # build list reaches far from the command center: no insurgent house near any start, whatever the base size
        keep_out = max(self.base_clear * 1.5 + 300, AI_BUILD_R)
        cands = [b for b in self.bld_objs
                 if self.cat.garrison.get(b[1], 0) >= 1 and b[5] not in ('shed',)
                 and all(math.hypot(b[2] - sx, b[3] - sy) > keep_out for sx, sy in self.starts)
                 and self.in_zone(b[2], b[3])]
        R.shuffle(cands)
        chosen = cands[::every]
        n_units = 0
        MAX_INS = max(0, int(self.opt['ins_cap']))   # chosen houses are shuffled, so a cap is spread evenly
        for uid, model, x, y, poly, klass in chosen:
            if n_units >= MAX_INS: break
            room = self.cat.garrison.get(model, 10)
            k = R.choices([1, 2, 3, 4], weights=[30, 35, 25, 10])[0]
            k = max(1, min(k, cap, room))
            # stand around the house, just outside its walls
            r = max(math.hypot(p[0] - x, p[1] - y) for p in poly) + 10
            for i in range(k):
                t = R.random() * math.tau
                ux, uy = x + math.cos(t) * r, y + math.sin(t) * r
                if not self.inside(ux, uy, 10) or self._wet(ux, uy):
                    ux, uy = x, y + r
                n_units += 1
                uname = f'EARTH_Ins_{n_units}'
                self.objs.append(MapObject(3, float(ux), float(uy), 0.0, float(t + math.pi), 0,
                                           R.choices(names, weights=weights)[0],
                                           self.mkprops(uname, self.base_props, INS_TEAM)))
                self.ins_units.append((uname, uid))
            self.ins_houses.append((x, y))
        self.stats['insurgent_houses'] = len({b for _, b in self.ins_units})
        self.stats['insurgents'] = n_units

    def insurgent_scripts(self):
        """Start-up scripts of the insurgent side: hostility both ways with every skirmish slot, then garrison."""
        rel = []
        for i in range(MAX_PLAYERS):
            r = 0 if (i == 0 or not self.game_mode or self.multi) else 1   # one-person game modes: only player0
            rel.append(('PLAYER_RELATES_PLAYER', [('side', INS_SIDE), ('side', f'player{i}'), ('relation', r)]))
            rel.append(('PLAYER_RELATES_PLAYER', [('side', f'player{i}'), ('side', INS_SIDE), ('relation', r)]))
        scripts = [Script('EARTH_Insurgents_Hostile', rel)]
        if self.opt['black_map']:
            # black map: everything that no unit of yours looks at becomes shroud (PartitionManager::shroudMapForPlayer,
            # empty side = every human player); the border outside the playable area is black too
            scripts.append(Script('EARTH_BlackMap', [('MAP_SHROUD_ALL', [('side', '')]), ('ENABLE_BORDER_SHROUD', [])]))
        if self.opt['mode_zombie'] and not self.zsurv:
            # Clearance + Zombie: the zombie AI (owner of the marker / zombie-only units) and the insurgents are on
            # the same side - they never fight each other, both go for the people
            for i in range(MAX_PLAYERS):
                P = f'player{i}'
                groups = [[('NAMED_OWNED_BY_PLAYER', [('unit', 'EARTH_ZMark'), ('side', P)])]]
                groups += [[('PLAYER_HAS_OBJECT_COMPARISON', [('side', P), ('comparison', 3), ('int', 1), ('type', t)])]
                           for t in self.ZOMBIE_ONLY]
                scripts.append(Script(f'EARTH_ZInsFriends{i}',
                                      [('PLAYER_RELATES_PLAYER', [('side', INS_SIDE), ('side', P), ('relation', 2)]),
                                       ('PLAYER_RELATES_PLAYER', [('side', P), ('side', INS_SIDE), ('relation', 2)])],
                                      any_of=groups))
        if self.game_mode:
            scripts += self.mode_scripts()
        # every War Factory / Barracks builds one unit every N seconds for as long as it stands
        wave = int(self.opt['ins_wave_size'])
        for uid, unit, wp, base in getattr(self, 'spawns', []):
            team = f'teamInsWave{base + 1}' if wave > 0 else INS_TEAM
            scripts.append(Script(f'EARTH_Produce_{uid}', [('CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT',
                                                            [('type', unit), ('team', team), ('waypoint', wp)])],
                                  conditions=[('NAMED_NOT_DESTROYED', [('unit', uid)])],
                                  delay=int(self.opt['ins_prod_interval']), once=False))
        # attack waves: as soon as N tanks stand at a base, its whole wave team (tanks + infantry) hunts the players
        tank = self.FORT.get(self.opt['fortress_faction'], self.FORT['GLA'])['tank']
        for base in sorted({b for *_, b in getattr(self, 'spawns', [])}) if wave > 0 else []:
            scripts.append(Script(f'EARTH_Wave_{base + 1}', [('TEAM_HUNT', [('team', f'teamInsWave{base + 1}')])],
                                  conditions=[('PLAYER_HAS_COMPARISON_UNIT_TYPE_IN_TRIGGER_AREA',
                                               [('side', INS_SIDE), ('comparison', 3), ('int', wave), ('type', tank),
                                                ('area', f'EARTH_InsBase{base + 1}')])],
                                  delay=10, once=False))
        # reinforcements: as soon as a unit of yours enters a district, its quick reaction force hunts you
        for q in getattr(self, 'qrf', []):
            for i in range(12 if (not self.game_mode or self.multi) else 1):
                scripts.append(Script(f"EARTH_QRF_{q['team']}_p{i}", [('TEAM_HUNT', [('team', q['team'])])],
                                      conditions=[('PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA',
                                                   [('side', f'player{i}'), ('comparison', 3), ('int', 1),
                                                    ('kindof', 'CAN_ATTACK'), ('area', q['area'])])],
                                      delay=3))
        pat = [('NAMED_FOLLOW_WAYPOINTS', [('unit', c), ('path', l)]) for c, l in getattr(self, 'patrols', [])]
        if pat:
            scripts.append(Script('EARTH_Insurgents_Patrols', pat))
        acts = [('NAMED_GARRISON_SPECIFIC_BUILDING', [('unit', u), ('unit', b)]) for u, b in self.ins_units]
        for i in range(0, len(acts), 60):
            scripts.append(Script(f'EARTH_Insurgents_Garrison_{i // 60 + 1}', acts[i:i + 60]))
        return scripts

    # ----------------------------------------------------------- insurgent reinforcements, missions
    def place_qrf(self):
        """Quick reaction forces: the town is cut into districts; each district with insurgents gets a small team
        (technicals + RPG + rebels) that hunts whoever enters the district or its neighbours."""
        self.qrf = []
        lvl = max(1, min(3, int(self.opt['ins_difficulty'])))
        n = max(2, min(5, int(max(self.AX, self.AY) / 1800)))
        size = max(self.AX, self.AY) / n
        units = {1: ['GLAVehicleTechnical', 'GLAInfantryRebel', 'GLAInfantryRebel'],
                 2: ['GLAVehicleTechnical', 'GLAInfantryTunnelDefender', 'GLAInfantryRebel', 'GLAInfantryRebel'],
                 3: ['GLAVehicleTechnical', 'GLAVehicleTechnical', 'GLAInfantryTunnelDefender', 'GLAInfantryTunnelDefender',
                     'GLAInfantryRebel', 'GLAInfantryRebel']}[lvl]
        known = lambda nm: (not self.known_objects) or nm in self.known_objects
        units = [u for u in units if known(u)]
        houses = getattr(self, 'ins_houses', [])
        k = 0
        for gy in range(n):
            for gx in range(n):
                cx_, cy_ = (gx + 0.5) * size, (gy + 0.5) * size
                inside = [h for h in houses if abs(h[0] - cx_) < size / 2 and abs(h[1] - cy_) < size / 2]
                if len(inside) < 3: continue
                if any(math.hypot(cx_ - sx, cy_ - sy) < AI_BUILD_R for sx, sy in self.starts): continue
                hx = sum(h[0] for h in inside) / len(inside); hy = sum(h[1] for h in inside) / len(inside)
                k += 1
                team = f'teamInsQRF{k}'
                made = 0
                for u in units:
                    for tries in range(30):
                        t = self.rng.random() * math.tau; r = 40 + self.rng.random() * 160
                        x, y = hx + math.cos(t) * r, hy + math.sin(t) * r
                        if not self.inside(x, y, 30) or self._wet(x, y): continue
                        poly = self.rect(x, y, 30, 30, t)
                        if self.occ_test(poly) > 0.05: continue
                        self.occ_poly(poly, True)
                        self.add_obj(u, x, y, t, 0, f'EARTH_QRF{k}_{made + 1}', self.base_props, team)
                        made += 1
                        break
                if made:
                    self.qrf.append(dict(team=team, area=f'EARTH_QRFArea{k}', x=hx, y=hy, r=size * 0.85))
        self.stats['ins_reaction_forces'] = len(self.qrf)

    def place_objectives(self):
        """Clearance missions: free the town hall (largest civic building), free the church, destroy the arms depot.
        Each one is guarded; done -> money and a few reinforcements."""
        self.objectives = []
        known = lambda nm: (not self.known_objects) or nm in self.known_objects
        guards = [u for u in ('GLAInfantryRebel', 'GLAInfantryTunnelDefender', 'GLAInfantryRebel', 'GLAInfantryRebel')
                  if known(u)]
        far = lambda x, y: all(math.hypot(x - sx, y - sy) > AI_BUILD_R for sx, sy in self.starts)
        area_of = lambda poly: abs(sum(poly[i][0] * poly[i - 1][1] - poly[i - 1][0] * poly[i][1] for i in range(len(poly)))) / 2
        b = getattr(self, 'bld_objs', [])
        civic = sorted([o for o in b if o[5] == 'civic' and far(o[2], o[3])], key=lambda o: -area_of(o[4]))
        church = [o for o in b if o[5] == 'church' and far(o[2], o[3])]
        if not civic:
            civic = sorted([o for o in b if o[5] == 'apt' and far(o[2], o[3])], key=lambda o: -area_of(o[4]))
        n_g = 0

        def guard(x, y, r, building=None):
            nonlocal n_g
            for u in guards:
                for tries in range(20):
                    t = self.rng.random() * math.tau
                    ux, uy = x + math.cos(t) * r, y + math.sin(t) * r
                    if self.inside(ux, uy, 20) and not self._wet(ux, uy): break
                n_g += 1
                nm = f'EARTH_ObjGuard_{n_g}'
                self.add_obj(u, ux, uy, t + math.pi, 0, nm, self.base_props, INS_TEAM)
                if building and self.cat.garrison.get(building[1], 0) >= 1 and 'Infantry' in u:
                    self.ins_units.append((nm, building[0]))

        rew = {'hall': ['AmericaInfantryRanger'] * 3 + ['AmericaVehicleHumvee'],
               'church': ['AmericaInfantryRanger'] * 2 + ['AmericaInfantryMissileDefender'] * 2,
               'depot': ['AmericaTankCrusader', 'AmericaTankCrusader']}
        rew = {k: [u for u in v if known(u)] for k, v in rew.items()}
        for key, lst in (('hall', civic), ('church', church)):
            if not lst: continue
            o = lst[0]
            r = max(math.hypot(p[0] - o[2], p[1] - o[3]) for p in o[4]) + 20
            guard(o[2], o[3], r, o)
            self.objectives.append(dict(key=key, kind='clear', x=o[2], y=o[3], r=r + 140, uid=o[0],
                                        wp=f'EARTH_Obj_{key}', area=f'EARTH_ObjArea_{key}', reward_units=rew[key]))
        depot = 'GLABlackMarket' if known('GLABlackMarket') else 'GLASupplyStash'
        if known(depot):
            x, y = self.far_from_starts(self.base_clear * 3.0)
            sz = self.cat.buildings.get(depot, (80, 80, 20))
            self.occ_poly(self.rect(x, y, sz[0], sz[1], 0), True)
            self.add_obj(depot, x, y, 0.0, 0, 'EARTH_Obj_DepotBuilding', self.base_props, INS_TEAM)
            guard(x, y, max(sz) * 0.7 + 30)
            self.objectives.append(dict(key='depot', kind='destroy', x=x, y=y, r=max(sz) + 150,
                                        uid='EARTH_Obj_DepotBuilding', wp='EARTH_Obj_depot', area='EARTH_ObjArea_depot',
                                        reward_units=rew['depot']))
        self.stats['objectives'] = len(self.objectives)
        names = {'hall': 'free the town hall', 'church': 'free the church', 'depot': 'destroy the arms depot'}
        goals = [names[o['key']] for o in self.objectives]
        if goals:
            txt = ', '.join(goals[:-1]) + (' and ' if len(goals) > 1 else '') + goals[-1]
            self.map_strings = dict(self.ZOMBIE_STR, EARTH_ObjIntro=f'Objectives: {txt}. They are marked on the map.')

    # ----------------------------------------------------------- enemy fortress (experimental)
    FORT = {
        'GLA': dict(core=['GLAArmsDealer', 'GLABarracks'], tank='GLATankScorpion', inf='GLAInfantryRebel',
                    extra=['GLASupplyStash', 'GLAPalace', 'GLABlackMarket'],
                    defense=['GLAStingerSite', 'GLATunnelNetwork', 'GLAStingerSite', 'GLADemoTrap'],
                    units=['GLATankScorpion', 'GLAVehicleTechnical', 'GLAVehicleQuadCannon', 'GLATankMarauder',
                           'GLAVehicleRocketBuggy', 'GLAInfantryRebel', 'GLAInfantryRebel']),
        'China': dict(core=['ChinaWarFactory', 'ChinaBarracks'], tank='ChinaTankBattleMaster', inf='ChinaInfantryRedguard',
                      extra=['ChinaSupplyCenter', 'ChinaPowerPlant', 'ChinaPowerPlant'],
                      defense=['ChinaGattlingCannon', 'ChinaBunker', 'ChinaGattlingCannon'],
                      units=['ChinaTankBattleMaster', 'ChinaTankBattleMaster', 'ChinaTankOverlord', 'ChinaTankGattling',
                             'ChinaVehicleTroopCrawler', 'ChinaInfantryRedguard', 'ChinaInfantryRedguard']),
        'America': dict(core=['AmericaWarFactory', 'AmericaBarracks'], tank='AmericaTankCrusader',
                        inf='AmericaInfantryRanger', extra=['AmericaSupplyCenter', 'AmericaPowerPlant', 'AmericaPowerPlant'],
                        defense=['AmericaPatriotBattery', 'AmericaFireBase', 'AmericaPatriotBattery'],
                        units=['AmericaTankCrusader', 'AmericaTankCrusader', 'AmericaTankPaladin', 'AmericaVehicleHumvee',
                               'AmericaInfantryRanger', 'AmericaInfantryRanger', 'AmericaInfantryMissileDefender']),
    }

    def build_stronghold(self, sx, sy, lvl, size, tag, counts=None):
        """Insurgent base: core buildings in a ring (centre left free), defence towers facing the map centre,
        a garrison of units in front. size=1 full fortress, ~0.5 small outpost."""
        owner = INS_TEAM
        fac = self.FORT.get(self.opt['fortress_faction'], self.FORT['GLA'])
        lvl = max(1, min(3, int(lvl)))
        self.flatten(sx, sy, 450 * max(size, 0.7))
        known = lambda n: (not self.known_objects) or n in self.known_objects
        placed = {'buildings': 0, 'defenses': 0, 'units': 0}
        kinds = {'infantry': 0, 'vehicles': 0}
        cxm, cym = self.AX / 2, self.AY / 2
        face = math.atan2(cym - sy, cxm - sx) if math.hypot(cxm - sx, cym - sy) > 50 else self.rng.random() * math.tau
        self.nfort = getattr(self, 'nfort', 0)

        static = getattr(self, 'occ_static', None)

        def fits(name, x, y, ang, strict, pad=1.1):
            if not known(name) or not self.inside(x, y, 30) or self._wet(x, y): return False
            sz = self.cat.buildings.get(name, (40, 40, 20))
            poly = self.rect(x, y, sz[0] * pad, sz[1] * pad, ang)
            # first a free spot; else one that only overlaps the town's houses (roads, water, bases stay free)
            if strict or static is None:
                return self.occ_test(poly) <= 0.02
            return self.occ_test(poly, static) <= 0.02

        def build(name, x, y, ang, kind, strict=True, spawn=None, force=False, pad=1.1):
            if not force and not fits(name, x, y, ang, strict, pad): return False
            if not known(name): return False
            uid = f'EARTH_{tag}_{kind}_{self.nfort + 1}'
            sz = self.cat.buildings.get(name, (40, 40, 20))
            poly = self.rect(x, y, sz[0], sz[1], ang)

            self.occ_poly(poly, True)
            self.nfort += 1
            self.add_obj(name, x, y, ang, 0, uid, self.base_props, owner)
            placed[kind] += 1
            if kind == 'buildings' and name in fac['core'][:2]:   # War Factory / Barracks keep producing
                unit = fac['tank'] if name == fac['core'][0] else fac['inf']
                if spawn is None:
                    dx, dy = sx - x, sy - y; d = math.hypot(dx, dy) or 1.0
                    off = max(sz[0], sz[1]) * 0.55 + 40
                    spawn = (x + dx / d * off, y + dy / d * off)
                self.producers.append((uid, unit, spawn[0], spawn[1], len(getattr(self, 'strongholds', []))))
            return True

        # ---- one dense block: buildings wall to wall in rows, facing the map centre
        ncore = 2                                       # War Factory + Barracks
        if counts: ncore = max(0, min(4, int(counts.get('buildings', ncore))))
        names = (fac['core'] * 2)[:ncore]
        if tag == 'Fort' and not counts: names += fac.get('extra', [])[:lvl]
        names = [n for n in names if known(n)]
        if not hasattr(self, 'producers'): self.producers = []
        # front row: War Factory + Barracks side by side, doors (model +x) to the map centre, a tank alley between
        # them; back row: the other buildings back to back with it, doors facing away - so no door is blocked
        gap = FORT_ALLEY
        rows = [[n for n in names if n in fac['core']], [n for n in names if n not in fac['core']]]
        if not rows[0]: rows = [rows[1], []]
        lay, rots = [], []
        for r, row_ in enumerate(rows):
            if not row_: continue
            sz = [tuple(self.cat.buildings.get(n, (160, 160, 80))[:2]) for n in row_]
            width = sum(b for a, b in sz) + gap * (len(row_) - 1)
            x_ = -width / 2
            for n, (a, b) in zip(row_, sz):
                oy = a / 2 + 1 if r == 0 else -a / 2 - 1
                # back row faces away (doors outward); the Palace the other way round, front to the courtyard
                lay.append((n, x_ + b / 2, oy, b, a)); rots.append(face if (r == 0 or n == 'GLAPalace') else face + math.pi)
                x_ += b + gap
        if lay:
            x0 = min(ox - a / 2 for _, ox, oy, a, b in lay); x1 = max(ox + a / 2 for _, ox, oy, a, b in lay)
            y0 = min(oy - b / 2 for _, ox, oy, a, b in lay); y1 = max(oy + b / 2 for _, ox, oy, a, b in lay)
            bw, bh = x1 - x0, y1 - y0
            lay = [(n, ox - (x0 + x1) / 2, oy - (y0 + y1) / 2, a, b) for n, ox, oy, a, b in lay]
        else:
            bw = bh = 0.0
        ang = face - math.pi / 2                        # local +y = towards the map centre
        ca, sa = math.cos(ang), math.sin(ang)
        to_w = lambda ox, oy, cx_, cy_: (cx_ + ox * ca - oy * sa, cy_ + ox * sa + oy * ca)
        spawn_of = lambda ox, oy, b, cx_, cy_: to_w(ox, oy + b / 2 + 70, cx_, cy_)   # in front of the door
        placed_block = False
        offsets = [(0.0, 0.0)] + [(r * math.cos(t), r * math.sin(t)) for r in range(60, 1700, 60)
                                  for t in (k * math.pi / 8 for k in range(16))]

        def lanes_free(cx_, cy_):
            # the doors of the War Factory / Barracks must open onto free ground (the units have to get out)
            for k, (n, ox, oy, a, b) in enumerate(lay):
                if n not in fac['core'][:2]: continue
                lx, ly = to_w(ox, oy + b / 2 + 70, cx_, cy_)
                if self.occ_test(self.rect(lx, ly, a, 110, face)) > 0.05: return False
            return True
        for strict in (True,):           # free ground only: the base borders on the houses, never stands in them
            for dx, dy in offsets:
                cx_, cy_ = sx + dx, sy + dy
                if all(fits(n, *to_w(ox, oy, cx_, cy_), rots[k], strict, pad=1.0)
                       for k, (n, ox, oy, a, b) in enumerate(lay)) and lanes_free(cx_, cy_):
                    for k, (n, ox, oy, a, b) in enumerate(lay):
                        build(n, *to_w(ox, oy, cx_, cy_), rots[k], 'buildings', spawn=spawn_of(ox, oy, b, cx_, cy_),
                              force=True)
                    sx, sy = cx_, cy_
                    placed_block = True
                    break
            if placed_block: break
        if not placed_block:                            # no room for the whole block: each building on its own free spot
            for k, (n, ox, oy, a, b) in enumerate(lay):
                for dx, dy in offsets:
                    x_, y_ = to_w(ox, oy, sx + dx, sy + dy)
                    if build(n, x_, y_, rots[k], 'buildings', True, spawn=spawn_of(ox, oy, b, sx + dx, sy + dy)): break
        if tag == 'Main' and placed['buildings'] == 0:  # the Clearance goal must exist: build it anyway
            for k, (n, ox, oy, a, b) in enumerate(lay):
                build(n, *to_w(ox, oy, sx, sy), rots[k], 'buildings', spawn=spawn_of(ox, oy, b, sx, sy), force=True)
        # ---- defences glued to the corners of the buildings: outer corners of the block first
        nd = int({1: 6, 2: 10, 3: 16}[lvl] * (1 if size >= 1 else 0.5))
        if counts: nd = max(0, int(counts.get('defenses', nd)))
        # every building corner on the outside of the block; a tower hugs the wall so the alleys stay open
        corners = []
        for n, ox, oy, a, b in sorted(lay, key=lambda t: -math.hypot(t[1], t[2])):
            for sxn, syn in ((-1, 1), (1, 1), (1, -1), (-1, -1)):
                ex = abs(ox + sxn * a / 2) >= bw / 2 - 3
                ey = abs(oy + syn * b / 2) >= bh / 2 - 3
                if not (ex or ey): continue                 # corner inside the block (an alley crossing)
                p = (ox + sxn * (a / 2 + 22 if ex else a / 2 - 20), oy + syn * (b / 2 + 22 if ey else b / 2 - 20))
                if all(math.hypot(p[0] - q[0], p[1] - q[1]) > 40 for q in corners): corners.append(p)
        corners.sort(key=lambda p: -(abs(p[0]) / max(bw, 1) + abs(p[1]) / max(bh, 1)))   # block corners first
        ring = list(corners)
        while corners and len(corners) < nd:            # more towers than corners: a second ring further out
            ring = [(x_ * (1 + 50 / max(math.hypot(x_, y_), 1)), y_ * (1 + 50 / max(math.hypot(x_, y_), 1))) for x_, y_ in ring]
            corners += ring
        for i in range(min(nd, len(corners))):
            ox, oy = corners[i]
            name = fac['defense'][i % len(fac['defense'])]
            done = False
            for strict, pad_ in ((True, 0.75), (False, 0.75), (False, 0.4)):
                for push in (0, 15, 30, 50):
                    k_ = 1 + push / max(math.hypot(ox, oy), 1)
                    if build(name, *to_w(ox * k_, oy * k_, sx, sy), ang, 'defenses', strict, pad=pad_):
                        done = True; break
                if done: break
        rd = max(bw, bh) / 2 + 60
        nu = int({1: 6, 2: 12, 3: 20}[lvl] * (1 if size >= 1 else 0.5))
        unit_list = [fac['units'][i % len(fac['units'])] for i in range(nu)]
        if counts:
            inf = [u for u in fac['units'] if 'Infantry' in u] or fac['units']
            veh = [u for u in fac['units'] if 'Infantry' not in u] or fac['units']
            unit_list = [inf[i % len(inf)] for i in range(max(0, int(counts.get('infantry', 0))))] + \
                        [veh[i % len(veh)] for i in range(max(0, int(counts.get('vehicles', 0))))]
        for name in unit_list:
            for tries in range(24):
                t = face + (self.rng.random() - 0.5) * (1.4 if size >= 1 else 6.0)
                r = rd + 20 + self.rng.random() * 100
                x, y = sx + math.cos(t) * r, sy + math.sin(t) * r
                if not known(name) or not self.inside(x, y, 30) or self._wet(x, y): continue
                poly = self.rect(x, y, 30, 30, t)
                if self.occ_test(poly) > (0.02 if tries < 16 else 0.5): continue
                self.occ_poly(poly, True)
                self.nfort += 1
                self.add_obj(name, x, y, face, 0, f'EARTH_{tag}_unit_{self.nfort}', self.base_props, owner)
                placed['units'] += 1
                kinds['infantry' if 'Infantry' in name else 'vehicles'] += 1
                break
        self.occ_poly(self.rect(sx, sy, max(bw, bh) + 300, max(bw, bh) + 300, ang), True)
        self.strongholds = getattr(self, 'strongholds', []) + [(sx, sy)]
        if not hasattr(self, 'base_info'): self.base_info = []
        lat, lon = self.p.from_units(sx, sy)
        self.base_info.append(dict(lat=float(lat), lon=float(lon), x=sx, y=sy, fortress=(tag == 'Fort'),
                                   buildings=placed['buildings'], defenses=placed['defenses'], **kinds))
        return placed

    def place_ins_oil(self):
        """Oil derricks (TechOilDerrick, they earn money for whoever captures them) where the insurgents are:
        around their bases, or in their area."""
        n = 0 if self.zsurv else int(self.opt['ins_oil'])
        name = 'TechOilDerrick'
        self.stats['oil_derricks'] = 0
        if n <= 0 or (self.known_objects and name not in self.known_objects): return
        centres = list(getattr(self, 'strongholds', [])) or [(z[0], z[1]) for z in self.insurgent_zones()]
        if not centres: return
        xe, ye, _ = self.cat.buildings.get(name, (90, 90, 40))
        static = getattr(self, 'occ_static', None)
        R, made = self.rng, 0
        for k in range(n):
            cx_, cy_ = centres[k % len(centres)]
            for strict in (True, False):
                ok = False
                for tries in range(40):
                    t = R.random() * math.tau
                    r = 350 + tries * 15 + R.random() * 150
                    x, y = cx_ + math.cos(t) * r, cy_ + math.sin(t) * r
                    if not self.inside(x, y, 60) or self._wet(x, y) or not self.in_zone(x, y, 200): continue
                    poly = self.rect(x, y, xe * 1.1, ye * 1.1, t)
                    if self.occ_test(poly, None if strict or static is None else static) > 0.02: continue
                    self.occ_poly(poly, True)
                    self.add_obj(name, x, y, t, 0, f'EARTH_Oil_{made + 1}', self.base_props)
                    made += 1; ok = True
                    break
                if ok: break
        self.stats['oil_derricks'] = made

    def insurgent_zones(self):
        """Where the insurgents are: circles (x, y, r) in map units. Drawn in the editor, otherwise one compact
        area around the centre of the biggest town."""
        if getattr(self, '_zones', None) is not None:
            return self._zones
        P = self.p
        zones, auto = [], False
        if self.opt['ins_zones'] is not None:
            for z in self.opt['ins_zones']:
                x, y = P.to_units(z['lat'], z['lon'])
                zones.append((x, y, float(z['radius_m']) * P.upm))
        elif not self.opt['ins_localized']:
            pass                                          # the whole town, like before
        else:
            auto = True
            WX, WY = self.WX, self.WY
            dens = np.zeros((WY, WX))
            for tags, ring in self.osm.buildings:
                la = sum(p[0] for p in ring) / len(ring); lo = sum(p[1] for p in ring) / len(ring)
                x, y = P.to_units(la, lo)
                c_, r_ = int(self.cx(x)), int(self.cx(y))
                if 0 <= c_ < WX and 0 <= r_ < WY: dens[r_, c_] += 1
            r_m = min(max(250.0, P.area * 0.12), 600.0)          # compact: a district, not the whole map
            dens = geo.box_mean(dens, max(2, int(r_m * 0.6 / P.mpc)))
            yy, xx = np.ogrid[:WY, :WX]
            for sx, sy in self.starts:                           # never right next to a player base
                dens[(xx - self.cx(sx)) ** 2 + (yy - self.cx(sy)) ** 2 < ((self.base_clear * 1.5 + r_m * P.upm * 0.6) / CELL) ** 2] *= 0.1
            if dens.max() > 0:
                iy, ix = np.unravel_index(int(np.argmax(dens)), dens.shape)
                zones.append(((ix - BORDER + 0.5) * CELL, (iy - BORDER + 0.5) * CELL, r_m * P.upm))
        self._zones = zones
        self.zone_info = []
        for x, y, r in zones:
            la, lo = P.from_units(x, y)
            self.zone_info.append(dict(lat=float(la), lon=float(lo), radius_m=r / P.upm, x=x, y=y, r=r, auto=auto))
        return zones

    def in_zone(self, x, y, pad=0.0):
        zones = self.insurgent_zones()
        return not zones or any(math.hypot(x - zx, y - zy) <= zr + pad for zx, zy, zr in zones)

    def place_outposts(self):
        """Small insurgent bases spread over the map: in towns (where OSM has many buildings) and in the countryside."""
        bases = self.opt['ins_bases']
        if bases is not None:                         # placed by hand in the editor (an empty list = none)
            made = 0
            for i, b in enumerate(bases):
                x, y = self.p.to_units(b['lat'], b['lon'])
                if not self.inside(x, y, 60): continue
                tot = sum(int(b.get(k, 0)) for k in ('buildings', 'defenses', 'infantry', 'vehicles'))
                size = 1.0 if tot > 14 else 0.6
                p = self.build_stronghold(x, y, int(self.opt['fortress_level']), size, f'Base{i + 1}', counts=b)
                if sum(p.values()): made += 1
            self.stats['outposts'] = made
            return
        n = int(self.opt['outposts'])
        if n <= 0: return
        WX, WY, R = self.WX, self.WY, self.rng
        dens = np.zeros((WY, WX))
        for tags, ring in self.osm.buildings:
            la = sum(p[0] for p in ring) / len(ring); lo = sum(p[1] for p in ring) / len(ring)
            x, y = self.p.to_units(la, lo)
            cx_, cy_ = int(self.cx(x)), int(self.cx(y))
            if 0 <= cx_ < WX and 0 <= cy_ < WY: dens[cy_, cx_] += 1
        local = geo.box_mean(dens, max(2, int(40 / self.p.mpc)))      # buildings right here
        dens = geo.box_mean(dens, max(4, int(250 / self.p.mpc)))      # town nearby
        dens = dens / (dens.max() or 1)
        local = local / (local.max() or 1)
        # next to the town, on open ground: never on top of the blocks of flats
        score = dens - local * 4.0 + np.random.default_rng(R.randrange(1 << 30)).random((WY, WX)) * 0.6 - self.bad * 0.02
        score[self.water_mask] = -1e9
        m = BORDER + int(self.base_clear * 0.8 / CELL)
        score[:m, :] = score[-m:, :] = score[:, :m] = score[:, -m:] = -1e9
        yy, xx = np.ogrid[:WY, :WX]
        zones = self.insurgent_zones()
        if zones:                                     # inside the insurgent area (or just at its edge)
            inz = np.zeros((WY, WX), bool)
            for zx, zy, zr in zones:
                inz |= (xx - self.cx(zx)) ** 2 + (yy - self.cx(zy)) ** 2 < ((zr * 1.15) / CELL) ** 2
            score[~inz] = -1e9
        def block(x, y, rad):
            score[(xx - self.cx(x)) ** 2 + (yy - self.cx(y)) ** 2 < (rad / CELL) ** 2] = -1e9
        for sx, sy in self.starts: block(sx, sy, self.base_clear * 2.2)
        for sx, sy in getattr(self, 'strongholds', []): block(sx, sy, self.base_clear * 1.8)
        made = 0
        for i in range(n):
            iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
            if score[iy, ix] < -1e8: break
            x, y = (ix - BORDER + 0.5) * CELL, (iy - BORDER + 0.5) * CELL
            p = self.build_stronghold(x, y, int(self.opt['fortress_level']), 0.5, f'Outpost{i + 1}')
            block(x, y, self.base_clear * 1.2 if zones else max(self.base_clear * 1.8, math.sqrt(self.AX * self.AY) / (n + 1) * 0.6))
            if sum(p.values()): made += 1
        self.stats['outposts'] = made

    def place_main_base(self):
        """Clearance: the insurgents' main base at the northern edge, in the middle - the goal: destroy it."""
        my = min(0.25, max(0.03, (self.base_clear * 0.9) / self.AY))
        x, y = self.best(0.5, 1 - my, 0.08)
        placed = self.build_stronghold(x, y, max(2, int(self.opt['fortress_level'])), 1.0, 'Main')
        self.occ_poly(self.rect(x, y, self.base_clear * 1.4, self.base_clear * 1.4, 0), True)
        self.main_base = (x, y)
        self.stats['main_base'] = f"{placed['buildings']} budov, {placed['defenses']} veží, {placed['units']} jednotiek"

    def far_from_starts(self, min_dist, zone=None):
        """Flat, dry spot as far from every player start as possible (prefers the map centre on ties)."""
        yy, xx = np.mgrid[:self.WY, :self.WX]
        ux, uy = (xx - BORDER + 0.5) * CELL, (yy - BORDER + 0.5) * CELL
        d = np.full((self.WY, self.WX), 1e9)
        for sx, sy in self.starts:
            d = np.minimum(d, np.hypot(ux - sx, uy - sy))
        for sx, sy in getattr(self, 'strongholds', []):
            d = np.minimum(d, np.hypot(ux - sx, uy - sy) * 1.5)
        m = BORDER + int(self.base_clear * 0.8 / CELL)
        score = np.minimum(d, min_dist * 1.5) - self.bad * 40 - np.hypot(ux - self.AX / 2, uy - self.AY / 2) * 0.05
        score[self.water_mask] = -1e9
        if zone is not None:                          # stay in the insurgent area, prefer its quieter edge
            zx, zy, zr = zone
            dz = np.hypot(ux - zx, uy - zy)
            score = score - np.abs(dz - zr * 0.7) * 0.5
            score[dz > zr * 1.1] = -1e9
        score[:m, :] = score[-m:, :] = score[:, :m] = score[:, -m:] = -1e9
        iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
        return (ix - BORDER + 0.5) * CELL, (iy - BORDER + 0.5) * CELL

    def place_fortress(self):
        """A pre-built, defended base at one start position, owned by that skirmish slot ('teamplayerN').
        The player in that lobby slot must use the same start position. Experimental."""
        if not self.opt['fortress'] or not self.starts: 
            return
        k = 0
        zones = self.insurgent_zones()
        sx, sy = self.far_from_starts(self.base_clear * 3.0, zones[0] if zones else None)
        placed = self.build_stronghold(sx, sy, int(self.opt['fortress_level']), 1.0, 'Fort')
        self.occ_poly(self.rect(sx, sy, self.base_clear * 1.6, self.base_clear * 1.6, 0), True)
        self.stats['fortress'] = f"ďaleko od hráčov ({self.opt['fortress_faction']}): {placed['buildings']} budov, " \
                                 f"{placed['defenses']} obranných veží, {placed['units']} jednotiek"
