"""Player starts, bases, resources, AI paths, patrols, spawn points."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class BasesMixin:
    # ----------------------------------------------------------- bases and resources
    def pick_spots(self):
        B = BORDER
        gx, gy = np.gradient(self.H, CELL)
        slope = np.hypot(gx, gy)
        self.slope = slope
        rb = max(4, int(self.base_clear / 2 / CELL))
        water_near = geo.box_mean(self.water_mask, rb) > 0.0
        occ_c = self.occ.reshape(self.WY, OCC, self.WX, OCC).mean((1, 3))
        bad = geo.box_mean(slope, rb) * 60 + water_near * 50 + geo.box_mean(occ_c, rb) * 30
        self.bad = bad

        def best(ax, ay, win):
            cxm, cym = int(ax * self.NX) + B, int(ay * self.NY) + B
            w = int(win * min(self.NX, self.NY))
            # the search window is clipped to the map: a start near a corner (game modes put the dormant AI at 6 %)
            # would otherwise give a negative slice start -> an empty window
            y0, y1 = max(0, cym - w), min(bad.shape[0], cym + w + 1)
            x0, x1 = max(0, cxm - w), min(bad.shape[1], cxm + w + 1)
            sub = bad[y0:y1, x0:x1]
            if sub.size == 0:
                return min(max(ax * self.AX, 0), self.AX), min(max(ay * self.AY, 0), self.AY)
            iy, ix = np.unravel_index(np.argmin(sub), sub.shape)
            x, y = (x0 + ix - B + 0.5) * CELL, (y0 + iy - B + 0.5) * CELL
            mg = min(self.base_clear * 0.6, min(self.AX, self.AY) * 0.2)
            return min(max(x, mg), self.AX - mg), min(max(y, mg), self.AY - mg)
        self.best = best

    def layout_points(self):
        """Relative (0..1) start positions for the chosen player count and layout (see preflight.layout_rel)."""
        return preflight.layout_rel(self.players, self.layout, self.AX, self.AY, self.base_clear)

    def build_pad_points(self, points):
        """Fit dry, separated build pads near the requested world positions."""
        size = self.base_clear
        pad = math.ceil(size / (2 * CELL))
        core = min(pad, max(5, int(AI_CORE_R / CELL)))
        water = geo.box_mean(self.water_mask, pad)
        wet_core = geo.box_mean(self.water_mask, core)
        roads = self.occ.reshape(self.WY, OCC, self.WX, OCC).mean((1, 3))
        blocked = geo.box_mean(roads, core)
        relief = geo.box_mean(self.slope, core)
        half = size / 2
        crowded_auto = not self.game_mode and self.layout != 'manual' and self.players > 12
        radius = max(45, pad * (3 if crowded_auto else 2))
        starts = []
        for i, (x, y) in enumerate(points):
            rx, ry = preflight.manual_point(x / self.AX, y / self.AY, self.AX, self.AY, size)
            x, y = rx * self.AX, ry * self.AY
            cx, cy = int(self.cx(x)), int(self.cx(y))
            lo_x, hi_x = BORDER + pad, BORDER + self.NX - pad
            lo_y, hi_y = BORDER + pad, BORDER + self.NY - pad
            if self.layout == 'teams':
                if i % 2: lo_x = max(lo_x, BORDER + self.NX // 2 + 1)
                else: hi_x = min(hi_x, BORDER + (self.NX - 1) // 2)
            if self.layout == 'teams_ns':
                if i % 2: hi_y = min(hi_y, BORDER + (self.NY - 1) // 2)
                else: lo_y = max(lo_y, BORDER + self.NY // 2 + 1)
            cx, cy = min(max(cx, lo_x), hi_x), min(max(cy, lo_y), hi_y)
            x0, x1 = max(lo_x, cx - radius), min(hi_x, cx + radius) + 1
            y0, y1 = max(lo_y, cy - radius), min(hi_y, cy + radius) + 1
            if not self.game_mode and self.water_mask[y0:y1, x0:x1].all():
                x0, x1, y0, y1 = lo_x, hi_x + 1, lo_y, hi_y + 1
            yy, xx = np.ogrid[y0:y1, x0:x1]
            region = np.s_[y0:y1, x0:x1]
            wx, wy = (xx - BORDER) * CELL, (yy - BORDER) * CELL
            cost = (np.hypot(wx - x, wy - y) / (radius * CELL)
                    + 2 * blocked[region] + 0.5 * relief[region]
                    + 8 * water[region] + 18 * (wet_core[region] > 0)
                    + 300 * self.water_mask[region])
            if not self.game_mode:
                cost += 4 * (water[region] > 0)
            for sx, sy in starts:
                dx, dy = np.abs(wx - sx), np.abs(wy - sy)
                cost += 100 * ((dx < size) & (dy < size))
                # When the squares cannot all fit, keep the command centers apart first.
                cost += 1000 * ((dx < size * 0.75) & (dy < size * 0.75))
            ix = min(max(cx - x0, 0), x1 - x0 - 1)
            iy = min(max(cy - y0, 0), y1 - y0 - 1)
            best_y, best_x = np.unravel_index(np.argmin(cost), cost.shape)
            if cost[iy, ix] > cost[best_y, best_x] + 0.12 or abs(wx[0, ix] - x) > CELL or abs(wy[iy, 0] - y) > CELL:
                x, y = (x0 + best_x - BORDER) * CELL, (y0 + best_y - BORDER) * CELL
                self.log(f'  štart {i + 1}: presunutý na vhodnejšiu plochu')
            starts.append((min(max(x, half), self.AX - half), min(max(y, half), self.AY - half)))
        return starts

    def flatten_build_pad(self, x, y, length=None, width=None, angle=0, elevation=None, slot=None):
        """Level a build pad or supply approach while retaining the riverbed."""
        hl = (self.base_clear if length is None else length) / 2
        hw = (self.base_clear if width is None else width) / 2
        blend = max(65, min(hl, hw) * 0.4)
        c, s = math.cos(angle), math.sin(angle)
        cx, cy = self.cx(x), self.cx(y)
        rx = math.ceil((abs(c) * hl + abs(s) * hw + blend * 2) / CELL) + 1
        ry = math.ceil((abs(s) * hl + abs(c) * hw + blend * 2) / CELL) + 1
        x0, x1 = max(BORDER, int(cx) - rx), min(BORDER + self.NX, int(cx) + rx + 1)
        y0, y1 = max(BORDER, int(cy) - ry), min(BORDER + self.NY, int(cy) + ry + 1)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        dx, dy = (xx + 0.5 - cx) * CELL, (yy + 0.5 - cy) * CELL
        d = np.maximum(np.abs(dx * c + dy * s) - hl, np.abs(dy * c - dx * s) - hw)
        ground = self.H[y0:y1, x0:x1]
        dry = ~self.water_mask[y0:y1, x0:x1]
        if slot is not None:
            owners = self.skirmish_level[y0:y1, x0:x1]
            dry &= (owners == 0) | (owners == slot)
        inner = dry & (d <= 0)
        if inner.any():
            z = float(np.median(ground[inner])) if elevation is None else elevation
            weight = np.clip(1 - d / blend, 0, 1) * dry
            ground[:] += (z - ground) * weight
            if slot is not None:
                owners[inner] = slot

    def place_bases(self):
        self.starts = []
        if self.layout in ('zsurv', 'invasion', 'battle_royale'):
            self.place_hidden_starts()
            return
        if self.layout == 'manual' and len(self.manual_starts) >= 2:
            points = [self.p.to_units(lat, lon) for lat, lon in self.manual_starts[:MAX_PLAYERS - int(self.multi)]]
            self.starts = self.build_pad_points(points)
            if self.multi and len(self.starts) < MAX_PLAYERS:         # the AI's start: the corner farthest from the people
                corners = [(0.06, 0.06), (0.94, 0.06), (0.06, 0.94), (0.94, 0.94)]
                ax, ay = max(corners, key=lambda c: min(math.hypot(c[0] * self.AX - x, c[1] * self.AY - y)
                                                         for x, y in self.starts))
                self.starts.append(self.best(ax, ay, 0.06))
        elif not self.game_mode:
            self.starts = self.build_pad_points([(rx * self.AX, ry * self.AY) for rx, ry in self.layout_points()])
        else:
            win = 0.10 if self.players <= 4 else (0.06 if self.players <= 8 else (0.04 if self.players <= 12 else 0.015))
            yy, xx = np.ogrid[:self.WY, :self.WX]
            for ax, ay in self.layout_points():
                x, y = self.best(ax, ay, win)
                if any(math.hypot(x - sx, y - sy) < self.base_clear * 0.6 for sx, sy in self.starts):
                    x, y = self._dry(ax * self.AX, ay * self.AY)     # no free good spot left: the plain point
                self.starts.append((x, y))
                # keep the next bases out of this one: a base square must not overlap another
                r = self.base_clear * 1.05 / CELL
                self.bad[(np.abs(xx - self.cx(x)) < r) & (np.abs(yy - self.cx(y)) < r)] += 1e6
        if not self.game_mode:
            self.skirmish_level = np.zeros_like(self.water_mask, dtype=np.uint8)
        for slot, (x, y) in enumerate(self.starts, 1):
            if self.layout == 'manual' or not self.game_mode:
                self.flatten_build_pad(x, y, slot=slot if not self.game_mode else None)
            else:
                self.flatten(x, y, self.base_clear * 0.55)
        self.stats['players'] = len(self.starts)
        self.write_start_waypoints()

    def write_start_waypoints(self):
        for i, (x, y) in enumerate(self.starts):
            nm = f'Player_{i + 1}_Start'
            props = []
            for key, v in self.wp_props:
                n = self.sid[key >> 8]
                if n in ('uniqueID', 'objectName', 'waypointName'): v = nm
                elif n == 'waypointID': v = i + 1
                props.append((key, v))
            self.objs.append(MapObject(3, x, y, 0.0, 0.0, 0, '*Waypoints/Waypoint', props))

    def place_hidden_starts(self):
        """Keep the command centres in the border while the players start in the arena."""
        self.starts = preflight.hidden_starts(self.AX, self.AY, self.humans, BORDER * CELL,
                                              reserve_ai=not self.battle_royale)
        self.stats['players'] = len(self.starts)
        self.write_start_waypoints()

    def build_ai_paths(self):
        """Center / Flank / Backdoor waypoint paths towards every player's base (used by the skirmish AI
        to attack), the same scheme World Builder maps and MapToPlay use: label = <Kind><target player>."""
        self.links = []
        if not self.opt['ai_paths'] or len(self.starts) < 2:
            self.stats['ai_paths'] = 0; self.next_wp = len(self.starts); return
        wid = len(self.starts)
        AX, AY = self.AX, self.AY
        C = (AX / 2, AY / 2)
        npaths = 0
        lerp = lambda a, b, t: (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

        def routes(S, T, end):
            dx, dy = T[0] - S[0], T[1] - S[1]
            L = math.hypot(dx, dy) or 1.0
            px, py = -dy / L, dx / L                       # perpendicular
            side = lambda t, k: (lerp(S, T, t)[0] + px * L * k, lerp(S, T, t)[1] + py * L * k)
            mid = lerp(S, T, 0.5)
            via = lerp(mid, C, 0.5)                        # the centre route bends towards the map centre
            return {'Center': [S, lerp(S, via, 0.6), via, lerp(via, end, 0.5), end],
                    'Flank': [S, side(0.3, 0.25), side(0.6, 0.3), side(0.85, 0.15), end],
                    'Backdoor': [S, side(0.3, -0.25), side(0.6, -0.3), side(0.85, -0.15), end]}

        def chain(label, pts):
            nonlocal wid
            prev = None
            for k, (x, y) in enumerate(pts):
                x, y = self._dry(min(max(x, 20), AX - 20), min(max(y, 20), AY - 20))
                wid += 1
                nm = f'EARTH_{label}_{wid}'
                props = []
                for key, v in self.path_wp_props:
                    n = self.sid[key >> 8]
                    if n in ('uniqueID', 'objectName', 'waypointName'): v = nm
                    elif n == 'waypointID': v = wid
                    elif n == 'waypointPathLabel1': v = label
                    elif n == 'waypointPathBiDirectional': v = 0
                    props.append((key, v))
                self.objs.append(MapObject(3, float(x), float(y), 0.0, 0.0, 0, '*Waypoints/Waypoint', props))
                if prev is not None: self.links.append((prev, wid))
                prev = wid

        # the skirmish AI looks up '<Kind><enemy start + 1>' and joins the path at its nearest waypoint, so every
        # enemy base gets a path from every other base (not only from the farthest one)
        many = len(self.starts) > 6
        for ti, T in enumerate(self.starts):
            far = max((s for j, s in enumerate(self.starts) if j != ti), key=lambda s: math.hypot(s[0] - T[0], s[1] - T[1]))
            for si, S in enumerate(self.starts):
                if si == ti: continue
                dx, dy = T[0] - S[0], T[1] - S[1]
                L = math.hypot(dx, dy) or 1.0
                end = (T[0] - dx / L * self.base_clear * 0.35, T[1] - dy / L * self.base_clear * 0.35)
                for kind, pts in routes(S, T, end).items():
                    if many and kind != 'Center' and S != far: continue
                    chain(f'{kind}{ti + 1}', pts); npaths += 1
        # the insurgents are a real enemy too: when an AI picks them, it looks for 'Center13' etc.
        targets = list(getattr(self, 'strongholds', []))
        if not targets and self.ins_houses:
            hx = sorted(h[0] for h in self.ins_houses); hy = sorted(h[1] for h in self.ins_houses)
            targets = [(hx[len(hx) // 2], hy[len(hy) // 2])]
        if targets and (self.opt['insurgents'] or self.opt['fortress'] or int(self.opt['outposts']) > 0 or self.opt['ins_bases']):
            for S in self.starts:
                T = min(targets, key=lambda t: math.hypot(t[0] - S[0], t[1] - S[1]))
                for kind, pts in routes(S, T, T).items():
                    chain(f'{kind}{INS_START + 1}', pts); npaths += 1
        self.stats['ai_paths'] = npaths
        self.next_wp = wid

    def build_patrols(self):
        """Insurgent technicals driving round town streets: a closed waypoint loop (out along the streets and
        back) per car; the start-up script sends each car onto its loop (NAMED_FOLLOW_WAYPOINTS) and, as the path
        is closed, the engine keeps driving it for ever."""
        self.patrols = []
        n_car = int(self.opt['patrols']) if (not self.known_objects or 'GLAVehicleTechnical' in self.known_objects) else 0
        n_foot = max(0, int(self.opt.get('foot_patrols', 0) or 0)) if self.opt['insurgents'] else 0
        n = n_car + (n_foot + 5) // 6                 # foot soldiers march in squads of 6 (3 x 2) on their own loops
        if n <= 0:
            self.stats['patrols'] = 0; return
        R = self.rng
        # street graph of the town roads
        key = lambda p: (round(p[0]), round(p[1]))
        adj, pos = {}, {}
        for rt, run in self.road_lines:
            if rt not in PAVED_STREETS: continue
            for a_, b_ in zip(run[:-1], run[1:]):
                ka, kb = key(a_), key(b_)
                if ka == kb: continue
                pos[ka], pos[kb] = a_, b_
                adj.setdefault(ka, set()).add(kb); adj.setdefault(kb, set()).add(ka)
        built = self.raster_poly([p for p, _ in self.bld_rects]) if self.bld_rects else None
        if not adj or built is None:
            self.stats['patrols'] = 0; return
        town = geo.box_mean(built, max(2, int(40 / self.p.mpc))) > 0.08
        def in_town(k):
            x, y = pos[k]; c_, r_ = int(self.cx(x)), int(self.cx(y))
            return 0 <= c_ < self.WX and 0 <= r_ < self.WY and town[r_, c_] and self.in_zone(x, y)
        nodes = [k for k in adj if in_town(k)
                 and all(math.hypot(pos[k][0] - sx, pos[k][1] - sy) > self.base_clear * 1.3 for sx, sy in self.starts)]
        if not nodes:
            self.stats['patrols'] = 0; return
        wid = getattr(self, 'next_wp', len(self.starts))
        made, foot = 0, 0
        for i in range(n * 3):
            if made >= n: break
            start = R.choice(nodes)
            route, prev, cur, length = [start], None, start, 0.0
            for _ in range(40):                       # random walk along the streets, no immediate U-turns
                nxt = [k for k in adj[cur] if k != prev and in_town(k)]
                if not nxt: break
                k = R.choice(nxt)
                length += math.hypot(pos[k][0] - pos[cur][0], pos[k][1] - pos[cur][1])
                route.append(k); prev, cur = cur, k
                if length > 2400: break
            if length < 300: continue
            pts = [pos[k] for k in route]
            pts = geo.rdp(pts, 6.0)
            loop = pts + pts[-2:0:-1]                 # there and back again -> a closed circuit
            label = f'EARTH_Patrol_{i + 1}'
            first = None; prevw = None
            for j, (x, y) in enumerate(loop):
                wid += 1
                props = []
                for k_, v in self.path_wp_props:
                    nm = self.sid[k_ >> 8]
                    if nm in ('uniqueID', 'objectName', 'waypointName'): v = f'{label}_{j + 1}'
                    elif nm == 'waypointID': v = wid
                    elif nm == 'waypointPathLabel1': v = label
                    elif nm == 'waypointPathBiDirectional': v = 0
                    props.append((k_, v))
                self.objs.append(MapObject(3, float(x), float(y), 0.0, 0.0, 0, '*Waypoints/Waypoint', props))
                if prevw is not None: self.links.append((prevw, wid))
                if first is None: first = wid
                prevw = wid
            self.links.append((prevw, first))        # close the loop
            if made < n_car:
                car = f'EARTH_PatrolCar_{i + 1}'
                x0, y0 = loop[0]; x1, y1 = loop[1]
                self.objs.append(MapObject(3, float(x0), float(y0), 0.0, math.atan2(y1 - y0, x1 - x0) % math.tau, 0,
                                           'GLAVehicleTechnical', self.mkprops(car, self.base_props, INS_TEAM)))
                self.patrols.append((car, label))
            else:      # a squad on foot: 3 rows of 2, side by side, all start together on the same route -> they
                       # march as one column instead of spreading over the loop
                x0, y0 = loop[0]; x1, y1 = loop[1]
                d_ = math.hypot(x1 - x0, y1 - y0) or 1.0
                ux, uy = (x1 - x0) / d_, (y1 - y0) / d_          # marching direction
                vx, vy = -uy, ux                                 # across
                for j in range(min(6, n_foot - foot)):
                    row, col = j // 2, j % 2
                    px_ = x0 - ux * row * 16 + vx * (col - 0.5) * 14
                    py_ = y0 - uy * row * 16 + vy * (col - 0.5) * 14
                    reb = f'EARTH_PatrolRebel_{foot + 1}'
                    self.objs.append(MapObject(3, float(px_), float(py_), 0.0, math.atan2(uy, ux) % math.tau, 0,
                                               'GLAInfantryRebel', self.mkprops(reb, self.base_props, INS_TEAM)))
                    self.patrols.append((reb, label))
                    foot += 1
            made += 1
        self.next_wp = wid
        self.stats['patrols'] = min(made, n_car)
        self.stats['foot_patrols'] = foot

    def build_spawn_points(self):
        """A waypoint in front of every insurgent War Factory / Barracks: new units appear there."""
        self.spawns = []
        wid = getattr(self, 'next_wp', len(self.starts))
        extra_wp = list(getattr(self, 'nests', [])) + [(o['wp'], o['x'], o['y']) for o in getattr(self, 'objectives', [])] \
            + list(getattr(self, 'zs_wps', [])) + ([self.silo] if getattr(self, 'silo', None) else [])
        if getattr(self, 'humvee_wp', None): extra_wp.append(self.humvee_wp)
        if getattr(self, 'main_base', None): extra_wp.append(('EARTH_InsMainWP',) + tuple(self.main_base))
        extra_wp += list(getattr(self, 'start_humvee_wps', []))
        if self.opt['mode_zombie']:
            extra_wp.append(('EARTH_ZMarkWP', -150.0, -600.0))   # black border
        for k, (name, x, y) in enumerate(extra_wp):
            wid += 1
            props = []
            for key_, v in self.wp_props:
                nm = self.sid[key_ >> 8]
                if nm in ('uniqueID', 'objectName', 'waypointName'): v = name
                elif nm == 'waypointID': v = wid
                elif nm.startswith('waypointPathLabel'): v = ''
                props.append((key_, v))
            self.objs.append(MapObject(3, float(x), float(y), 0.0, 0.0, 0, '*Waypoints/Waypoint', props))
        self.next_wp = wid
        if int(self.opt['ins_prod_interval']) <= 0: return
        for k, (uid, unit, x, y, base) in enumerate(getattr(self, 'producers', [])):
            if not (self.known_objects and unit not in self.known_objects):
                x, y = self._dry(x, y, 40)
                wid += 1
                name = f'EARTH_Spawn_{k + 1}'
                props = []
                for key_, v in self.wp_props:
                    nm = self.sid[key_ >> 8]
                    if nm in ('uniqueID', 'objectName', 'waypointName'): v = name
                    elif nm == 'waypointID': v = wid
                    elif nm.startswith('waypointPathLabel'): v = ''
                    props.append((key_, v))
                self.objs.append(MapObject(3, float(x), float(y), 0.0, 0.0, 0, '*Waypoints/Waypoint', props))
                self.spawns.append((uid, unit, name, base))
        self.next_wp = wid
        self.stats['ins_factories'] = len(self.spawns)

    def dump_links(self):
        links = getattr(self, 'links', [])
        return struct.pack('<I', len(links)) + b''.join(struct.pack('<II', a, b) for a, b in links)

    def starting_supply_spot(self, x, y, dock, wet_route, wet_service, slot):
        """Find a dock with dry ground for a supply center on the side facing the base."""
        facing = math.atan2(self.AY / 2 - y, self.AX / 2 - x)
        # Keep the depot outside the stock opening build positions when there is room.
        preferred = min(670, max(550, self.base_clear * 0.4))
        radii = sorted(range(250, 761, 30), key=lambda r: abs(r - preferred))
        headings = [0] + [j for i in range(1, 12) for j in (i, -i)] + [12]
        fallback = None
        for radius in radii:
            steps = np.linspace(0, radius, math.ceil(radius / CELL) + 1)
            for turn in headings:
                angle = facing + turn * math.tau / 24
                ux, uy = math.cos(angle), math.sin(angle)
                px, py = x + radius * ux, y + radius * uy
                if not self.inside(px, py, max(90, math.hypot(*dock[:2]) / 2 + 10)):
                    continue
                if self.layout == 'teams' and (px < self.AX / 2) != (x < self.AX / 2):
                    continue
                if self.layout == 'teams_ns' and (py < self.AY / 2) != (y < self.AY / 2):
                    continue
                if any((x, y) != (sx, sy) and math.hypot(px - sx, py - sy) < radius
                       for sx, sy in self.starts):
                    continue
                poly = self.rect(px, py, dock[0], dock[1], angle)
                if self.occ_test(poly) > 0:
                    continue
                cols = (self.cx(x) + steps * ux / CELL).astype(int)
                rows = (self.cx(y) + steps * uy / CELL).astype(int)
                owners = self.skirmish_level[rows, cols]
                if wet_route[rows, cols].any() or ((owners != 0) & (owners != slot)).any():
                    continue
                spot = (px, py, angle, poly)
                service_x, service_y = px - 125 * ux, py - 125 * uy
                if not wet_service[int(self.cx(service_y)), int(self.cx(service_x))]:
                    return spot
                if fallback is None:
                    fallback = spot
        return fallback

    def place_resources(self):
        cnt = 0
        if getattr(self, 'zsurv', False):            # Zombie Survival / Defense: no supplies, no oil - bounty only
            self.stats['supply_docks'] = 0; return
        primary_ranges = []

        def free_near(x, y, a, b, rmin=60, rmax=260, extra_supply=False):
            for rad in range(int(rmin), int(rmax), 20):
                for k in range(16):
                    t = self.rng.random() * math.tau
                    px, py = x + rad * math.cos(t), y + rad * math.sin(t)
                    if not self.inside(px, py, max(a, b) / 2 + 15): continue
                    if self.layout == 'manual' and any(math.hypot(px - sx, py - sy) < min(220, self.base_clear * 0.4)
                                                        for sx, sy in self.starts): continue
                    if extra_supply and any(math.hypot(px - sx, py - sy) < distance + 30
                                            for sx, sy, distance in primary_ranges): continue
                    poly = self.rect(px, py, a, b, t)
                    if self.occ_test(poly) == 0:
                        return px, py, t, poly
            return None
        def put(name, x, y, ang, poly, uid):
            self.occ_poly(poly, True)
            self.add_obj(name, x, y, ang, 0, uid, self.base_props)
        dock = self.cat.buildings.get('SupplyDock', (100, 122, 13))
        pile = self.cat.buildings.get('SupplyPileSmall', (32, 32, 5))
        derrick = self.cat.buildings.get('TechOilDerrick', (46, 42, 30))
        start_count = len(self.starts)
        if not self.game_mode:
            wet_route = geo.box_mean(self.water_mask, 2) > 0
            wet_service = geo.box_mean(self.water_mask, 8) > 0
            for i, (x, y) in enumerate(self.starts):
                spot = self.starting_supply_spot(x, y, dock, wet_route, wet_service, i + 1)
                if spot is None:
                    self.log(f'  štart {i + 1}: nenašiel sa dostupný Supply Dock na rovnakej strane vody')
                    continue
                px, py, angle, poly = spot
                dist = math.hypot(px - x, py - y)
                primary_ranges.append((x, y, dist))
                ux, uy = math.cos(angle), math.sin(angle)
                level = self.H[int(self.cx(y)), int(self.cx(x))]
                # A flat lane for the dozer plus room for the warehouse and a supply center.
                lx, ly = (x + px) / 2, (y + py) / 2
                dx, dy = px - 65 * ux, py - 65 * uy
                self.flatten_build_pad(lx, ly, dist + 40, 100, angle, level, slot=i + 1)
                self.flatten_build_pad(dx, dy, 360, 270, angle, level, slot=i + 1)
                put('SupplyDock', px, py, angle, poly, f'EARTH_Resource_{i + 1}_1')
                pile_x, pile_y = px + 115 * ux, py + 115 * uy
                pile_poly = self.rect(pile_x, pile_y, pile[0], pile[1], angle)
                if self.inside(pile_x, pile_y, max(pile[:2]) / 2 + 10) and self.occ_test(pile_poly) == 0:
                    put('SupplyPileSmall', pile_x, pile_y, angle, pile_poly, f'EARTH_Resource_{i + 1}_2')
                for zone in (self.rect(lx, ly, dist + 40, 100, angle), self.rect(dx, dy, 360, 270, angle)):
                    self.occ_poly(zone, True)
                cnt += 1
            self.stats['starting_supplies'] = cnt
            for x, y in self.starts:
                self.occ_poly(self.rect(x, y, self.base_clear, self.base_clear, 0), True)
        spots = list(self.starts) if self.game_mode else []
        if self.resources == 'high':
            spots += self.starts          # second dock at every base
        extra = {'low': [], 'normal': [(0.5, 0.15), (0.85, 0.5), (0.5, 0.85), (0.15, 0.5)],
                 'high': [(0.5, 0.15), (0.85, 0.5), (0.5, 0.85), (0.15, 0.5), (0.3, 0.3), (0.7, 0.7),
                          (0.3, 0.7), (0.7, 0.3)]}[self.resources]
        nb = len(spots)
        for ax, ay in extra:
            spots.append(self.best(ax, ay, 0.07))
        for k, (x, y) in enumerate(spots):
            i = k if self.game_mode else k + start_count
            r = (free_near(x, y, dock[0], dock[1], 320, 900, extra_supply=True) if k < nb else
                 free_near(x, y, dock[0], dock[1], extra_supply=True))
            if not r: continue
            px, py, t, poly = r
            self.flatten(px, py, 70)
            put('SupplyDock', px, py, t, poly, f'EARTH_Resource_{i + 1}_1')
            r2 = free_near(px, py, pile[0], pile[1], 70, 160)
            if r2: put('SupplyPileSmall', r2[0], r2[1], r2[2], r2[3], f'EARTH_Resource_{i + 1}_2')
            cnt += 1
        oil = {'low': [(0.5, 0.5)], 'normal': [(0.5, 0.5), (0.3, 0.5), (0.7, 0.5)],
               'high': [(0.5, 0.5), (0.3, 0.5), (0.7, 0.5), (0.5, 0.3), (0.5, 0.7)]}[self.resources]
        if self.opt['oil_count'] is not None:
            ring = [(0.5, 0.5), (0.3, 0.5), (0.7, 0.5), (0.5, 0.3), (0.5, 0.7), (0.3, 0.3), (0.7, 0.7), (0.3, 0.7), (0.7, 0.3)]
            oil = ring[:max(0, min(9, int(self.opt['oil_count'])))]
        for i, (ax, ay) in enumerate(oil):
            x, y = self.best(ax, ay, 0.05)
            r = free_near(x, y, derrick[0], derrick[1], 20, 200)
            if r:
                self.flatten(r[0], r[1], 50)
                put('TechOilDerrick', r[0], r[1], r[2], r[3], f'EARTH_Oil_{i + 1}')
        self.stats['supply_docks'] = cnt
