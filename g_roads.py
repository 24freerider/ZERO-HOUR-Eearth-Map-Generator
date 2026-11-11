"""Roads, overpasses, railways, air paths, footpaths to entrances."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class RoadsMixin:
    # ----------------------------------------------------------- roads (occupancy + objects)
    def smooth_roundabouts(self):
        """OSM roundabouts (junction=roundabout, often split into several ways) -> one even circle: the same radius
        all round, short equal arcs, never smaller than the road needs; every road entering it ends exactly on the
        circle, so the game joins it. Without this the ring is a few long straight pieces - a star of planks."""
        roads = list(self.osm.roads)
        idx = [i for i in getattr(self.osm, 'roundabouts', []) if i < len(roads) and roads[i][0] in ROAD_TYPE]
        if not idx: return roads
        key = lambda pt: (round(pt[0], 7), round(pt[1], 7))
        lat0 = self.p.lat0
        kx, ky = 111320.0 * math.cos(math.radians(lat0)), 110574.0
        groups = []                         # ways of one roundabout touch each other (shared nodes)
        for i in idx:
            ks = {key(p) for p in roads[i][1]}
            hit = [g for g in groups if g['keys'] & ks]
            g = {'ways': [i], 'keys': set(ks)}
            for h in hit:
                g['ways'] += h['ways']; g['keys'] |= h['keys']; groups.remove(h)
            groups.append(g)
        drop, add, n_done = set(), [], 0
        for g in groups:
            pts = [p for i in g['ways'] for p in roads[i][1]]
            clat = sum(p[0] for p in pts) / len(pts); clon = sum(p[1] for p in pts) / len(pts)
            d = [math.hypot((p[1] - clon) * kx, (p[0] - clat) * ky) for p in pts]
            r = sum(d) / len(d)
            if r < 3 or max(d) > r * 1.8: continue       # not a ring (a half roundabout / a strange shape)
            hw = roads[g['ways'][0]][0]
            w_m = self.road_w.get(ROAD_TYPE[hw], 40) / self.p.upm
            r = max(r, w_m * 2.0)                         # big enough for the road to curve round (an island inside)
            ang = lambda la, lo: math.atan2((la - clat) * ky, (lo - clon) * kx)
            on = lambda a: (clat + math.sin(a) * r / ky, clon + math.cos(a) * r / kx)
            # roads entering the ring: their ring node moves onto the circle (same point -> a game junction)
            snaps = {}
            for j, (h2, ring) in enumerate(roads):
                if j in g['ways'] or h2 not in ROAD_TYPE: continue
                new = list(ring)
                for k, p in enumerate(ring):
                    if key(p) in g['keys']:
                        a = ang(*p); q = on(a); snaps[a] = q; new[k] = q
                if new != list(ring): roads[j] = (h2, new)
            n = max(10, min(28, int(round(2 * math.pi * r / 9.0))))
            angles = [-math.pi + 2 * math.pi * t / n for t in range(n)]
            for a in snaps:                               # an entry replaces the nearest even point
                k = min(range(len(angles)), key=lambda t: abs(math.atan2(math.sin(angles[t] - a), math.cos(angles[t] - a))))
                angles[k] = a
            angles.sort()
            circle = [snaps.get(a) or on(a) for a in angles]
            circle.append(circle[0])
            drop |= set(g['ways']); add.append((hw, circle)); n_done += 1
        self.stats['roundabouts'] = n_done
        return [r_ for j, r_ in enumerate(roads) if j not in drop] + add

    def build_roads(self):
        """OSM ways -> game road segments.
        * junction nodes (shared by 2+ ways) are always kept, so the game can join roads into T / X crossings
        * a way that runs on top of an already placed road (dual carriageway, duplicates) is dropped
        """
        P, A = self.p, self.AREA
        roads = sorted(self.smooth_roundabouts(), key=lambda r: (ROAD_PRIO.index(r[0]) if r[0] in ROAD_PRIO else 99,
                                                                 -geo.polyline_len(r[1]) if r[0] in ('rail', 'rail_side') else 0))
        # railways like the real thing: the longest main line first and straight, then the side tracks that leave
        # it; a track lying on one already laid (the 2nd track of a double line, the parallel tracks of a yard,
        # short crossovers) is dropped - the game draws them as one messy tangle
        rail_img = Image.new('L', (self.WX * 2, self.WY * 2), 0); rd = ImageDraw.Draw(rail_img)
        rail_drop = 0
        # car parks: the service lanes OSM draws across them are no roads - the lot is one asphalt area with bays
        P_ = self.p
        lots = [geo.clip_poly([P_.to_units(*pt) for pt in ring], self.AX, self.AY)
                for kind, ring in getattr(self.osm, 'areas', []) if kind == 'parking']
        lots = [pg for pg in lots if len(pg) >= 3]
        lot_mask = self.raster_poly(lots) if lots else None
        self.lot_mask = lot_mask
        lot_drop = 0
        key = lambda pt: (round(pt[0], 7), round(pt[1], 7))
        uses = {}
        for hw, ring in roads:
            if hw not in ROAD_TYPE: continue
            for i, pt in enumerate(ring):
                k = key(pt)
                uses[k] = uses.get(k, 0) + (2 if i in (0, len(ring) - 1) else 1)
        junction = {k for k, n in uses.items() if n >= 2}

        def simplify(ring, eps=4.0):
            pts = [P.to_units(*pt) for pt in ring]
            keep = [key(pt) in junction for pt in ring]
            out, start = [], 0
            for i in range(1, len(pts)):
                if keep[i] or i == len(pts) - 1:
                    seg = geo.rdp(pts[start:i + 1], eps)
                    out += seg if not out else seg[1:]
                    start = i
            # drop points closer than 4 units, but never a junction or an end point
            jset = {pts[i] for i in range(len(pts)) if keep[i]} | {pts[0], pts[-1]}
            res = [out[0]]
            for pt in out[1:]:
                if pt in jset or math.hypot(pt[0] - res[-1][0], pt[1] - res[-1][1]) > 4:
                    res.append(pt)
            return res

        S = 2                                            # duplicate test resolution (pixels per cell)
        dup_img = Image.new('L', (self.WX * S, self.WY * S), 0); dd = ImageDraw.Draw(dup_img)
        dup = None
        self.road_lines = []   # (type, pts)
        self.road_tex_only = []   # streets over the game's road limit: painted on the ground, no road object
        total, dropped, capped, segs = 0, 0, 0, 0
        limit = int(getattr(self.cat, 'road_segments', 4000) or 4000)
        if self.opt['light']: limit = min(limit, 4000)      # the classic game has the stock limit
        budget = int(limit * 0.9)
        for hw, ring in roads:
            rt = ROAD_TYPE.get(hw)
            if not rt: continue
            if self.cat.road_w and rt not in self.cat.road_w:
                rt = {'Sidewalk4': WHITE_ROAD, 'DirtRoadTracks': 'DirtRoad2'}.get(rt, rt)
            if rt == 'Sidewalk' and not self.opt['footways']: continue
            line = simplify(ring, 10.0 if rt in MINOR_ROADS else 5.0)
            for run in geo.clip_polyline(line, self.AX, self.AY):
                if len(run) < 2: continue
                if lot_mask is not None and (hw in ('service', 'track_paved') or rt in ('Sidewalk', 'Sidewalk2')):
                    n_in = n_all = 0
                    for a_, b_ in zip(run[:-1], run[1:]):
                        k_ = max(1, int(math.hypot(b_[0] - a_[0], b_[1] - a_[1]) / 10))
                        for t in range(k_ + 1):
                            c_ = int(self.cx(a_[0] + (b_[0] - a_[0]) * t / k_)); r_ = int(self.cx(a_[1] + (b_[1] - a_[1]) * t / k_))
                            if 0 <= r_ < lot_mask.shape[0] and 0 <= c_ < lot_mask.shape[1]:
                                n_all += 1; n_in += bool(lot_mask[r_, c_])
                    if n_all and n_in / n_all > 0.5:
                        lot_drop += 1; continue
                cost = len(run) - 1 + 2                   # segments + its two ends (junction pieces)
                for a_, b_, c_ in zip(run, run[1:], run[2:]):
                    t = abs(math.atan2(c_[1] - b_[1], c_[0] - b_[0]) - math.atan2(b_[1] - a_[1], b_[0] - a_[0]))
                    t = min(t, math.tau - t)
                    if t > 0.05: cost += math.ceil(t / (math.pi / 6))
                if segs + cost > budget:
                    capped += 1
                    if rt != 'TrainTrack': self.road_tex_only.append((rt, run))
                    continue
                # is this way mostly lying on top of roads we already have?
                dup = np.asarray(dup_img)
                L = geo.polyline_len(run)
                if rt == 'TrainTrack':
                    ra = np.asarray(rail_img); n_in = n_all = 0
                    for a_, b_ in zip(run[:-1], run[1:]):
                        seg = math.hypot(b_[0] - a_[0], b_[1] - a_[1]); k = max(2, int(seg / 8))
                        for t in range(k + 1):
                            x = a_[0] + (b_[0] - a_[0]) * t / k; y = a_[1] + (b_[1] - a_[1]) * t / k
                            px, py = int(self.cx(x) * 2), int(self.cx(y) * 2)
                            if 0 <= px < ra.shape[1] and 0 <= py < ra.shape[0]:
                                n_all += 1; n_in += ra[py, px] > 0
                    share = n_in / max(1, n_all)
                    if (hw == 'rail_side' and (L < 70 * self.p.upm or share > 0.35)) or (hw == 'rail' and share > 0.6) \
                            or (L < 40 * self.p.upm and share > 0.2):
                        rail_drop += 1; continue
                    rd.line([(self.cx(x) * 2, self.cx(y) * 2) for x, y in run], fill=255,
                            width=max(2, int(self.road_w[rt] * 1.6 / CELL * 2)))
                if L > 0 and rt != 'TrainTrack':
                    n_in = n_all = 0
                    for a_, b_ in zip(run[:-1], run[1:]):
                        seg = math.hypot(b_[0] - a_[0], b_[1] - a_[1]); k = max(1, int(seg / 5))
                        for t in range(1, k):
                            x = a_[0] + (b_[0] - a_[0]) * t / k; y = a_[1] + (b_[1] - a_[1]) * t / k
                            px, py = int(self.cx(x) * S), int(self.cx(y) * S)
                            if 0 <= px < dup.shape[1] and 0 <= py < dup.shape[0]:
                                n_all += 1; n_in += dup[py, px] > 0
                    if n_all > 3 and n_in / n_all > (0.3 if rt == 'Sidewalk' else 0.85):
                        dropped += 1; continue
                total += 2 * (len(run) - 1)
                segs += cost
                self.road_lines.append((rt, run))
                if rt != 'TrainTrack':
                    dd.line([(self.cx(x) * S, self.cx(y) * S) for x, y in run], fill=255,
                            width=max(2, int(self.road_w[rt] * 0.9 / CELL * S)))
        d = ImageDraw.Draw(self._occ_img)
        for rt, run in self.road_lines + self.road_tex_only:
            if rt == 'Sidewalk': continue            # footpaths may run right along the houses
            w = (self.road_w[rt] + 2) / CELL * OCC
            d.line([(self.cx(x) * OCC, self.cx(y) * OCC) for x, y in run], fill=255, width=max(int(w), 2))
        self.stats['road_points'] = total
        if lot_drop: self.stats['parking_lanes_dropped'] = lot_drop
        if rail_drop: self.stats['rail_tracks_merged'] = rail_drop
        self.stats['duplicate_roads_removed'] = dropped
        self.stats['road_segments_est'] = segs
        self.stats['road_segment_limit'] = limit
        if capped:
            self.stats['roads_over_limit'] = capped
            self.log(f'  POZOR: {capped} ciest sa nezmestilo do limitu hry ({limit} úsekov ciest), sú len namaľované '
                     f'na zemi (najmenej dôležité: chodníky, poľné cesty, vedľajšie ulice). '
                     f'Pomôže tlačidlo "Zvýšiť limit ciest v hre" (karta Export).')

    def place_entrance_paths(self):
        """A footpath from every entrance of a block of flats to the street, like in reality: the entrances are
        spread along the side facing the nearest road, each path runs straight to that road."""
        n_p = 0
        for cx, cy, L, W, th, ne in getattr(self, 'apt_rows', []):
            nr = self._nearest_road(cx, cy, PAVED_STREETS + ('Sidewalk2', 'Sidewalk3'))
            if not nr or nr[0] > 90 * self.p.upm: continue
            ux, uy, vx, vy = math.cos(th), math.sin(th), -math.sin(th), math.cos(th)
            side = 1 if (nr[1] - cx) * vx + (nr[2] - cy) * vy > 0 else -1     # the side that faces the street
            for k in range(ne):
                oa = (k + 0.5) * L / ne - L / 2
                dx, dy = cx + ux * oa + vx * side * (W / 2 + 3), cy + uy * oa + vy * side * (W / 2 + 3)
                r = self._nearest_road(dx, dy, PAVED_STREETS + ('Sidewalk2', 'Sidewalk3', 'Sidewalk'))
                if not r or r[0] > 70 * self.p.upm or r[0] < 6: continue
                ex, ey = r[1], r[2]
                # no path through another building
                bad = 0
                for t in (0.3, 0.5, 0.7, 0.9):
                    if self.occ_test(self.rect(dx + (ex - dx) * t, dy + (ey - dy) * t, 4, 4, 0)) > 0.5: bad += 1
                if bad > 1: continue
                self.road_lines.append(('Sidewalk', [(dx, dy), (ex, ey)])); n_p += 1
        self.stats['entrance_paths'] = n_p

    def build_railroad(self):
        """A running train: the locomotive follows a chain of waypoints laid along the longest railway line
        (out to the map edge and back, so the track is a closed loop and the train keeps running). Waypoints
        whose name ends in 'Station' make it stop at the real stations; 'Tunnel' keeps it level over bridges."""
        self.stats['train'] = 0
        eng = 'TrainEngine'
        if not self.opt['train'] or (self.known_objects and eng not in self.known_objects):
            return
        P, A = self.p, self.AREA
        lines = [[P.to_units(*pt) for pt in ring] for hw, ring in self.osm.roads if hw == 'rail' and len(ring) >= 2]
        if not lines: return
        # OSM splits a railway into many ways: join them end to end into long lines
        key = lambda p: (round(p[0] / 2), round(p[1] / 2))
        merged = True
        while merged:
            merged = False
            for i in range(len(lines)):
                for j in range(len(lines)):
                    if i == j: continue
                    a, b = lines[i], lines[j]
                    if key(a[-1]) == key(b[0]): lines[i] = a + b[1:]
                    elif key(a[-1]) == key(b[-1]): lines[i] = a + b[::-1][1:]
                    elif key(a[0]) == key(b[-1]): lines[i] = b + a[1:]
                    elif key(a[0]) == key(b[0]): lines[i] = b[::-1] + a[1:]
                    else: continue
                    lines.pop(j); merged = True; break
                if merged: break
        runs = [r for ln in lines for r in geo.clip_polyline(ln, self.AX, self.AY) if len(r) >= 2]
        if not runs: return
        best = max(runs, key=geo.polyline_len)
        if geo.polyline_len(best) < 1500: return
        best = geo.rdp(best, 3.0)
        pts = [best[0]]
        for (x0, y0), (x1, y1) in zip(best, best[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            k = max(1, int(seg / 150))
            for t in range(1, k + 1):
                pts.append((x0 + (x1 - x0) * t / k, y0 + (y1 - y0) * t / k))
        n = len(pts)
        wet = [self._wet(x, y) for x, y in pts]
        # a segment that crosses water (river under a bridge) counts as wet at both its ends
        wseg = [any(self._wet(pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t / 12, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t / 12)
                    for t in range(1, MAX_PLAYERS)) for i in range(n - 1)]
        for i, w in enumerate(wseg):
            if w: wet[i] = wet[i + 1] = True
        stations = set()
        for kind, la, lo in self.osm.points:
            if kind != 'station': continue
            sx, sy = P.to_units(la, lo)
            i = min(range(n), key=lambda i: math.hypot(pts[i][0] - sx, pts[i][1] - sy))
            if math.hypot(pts[i][0] - sx, pts[i][1] - sy) < 400 and 0 < i < n - 1: stations.add(i)
        seq = [(i, 'f') for i in range(n)] + [(i, 'b') for i in range(n - 2, 0, -1)]
        wid = getattr(self, 'next_wp', len(self.starts))
        first = prev = None
        for k, (i, d) in enumerate(seq):
            nxt = i + 1 if d == 'f' else i - 1
            tun = wet[i] or (0 <= nxt < n and wet[nxt])
            name = f'Railroad_{k + 1}' + ('Station' if i in stations else 'Tunnel' if tun else '')
            wid += 1
            props = []
            for key_, v in self.wp_props:
                nm = self.sid[key_ >> 8]
                if nm in ('uniqueID', 'objectName', 'waypointName'): v = name
                elif nm == 'waypointID': v = wid
                elif nm.startswith('waypointPathLabel'): v = ''
                props.append((key_, v))
            x, y = pts[i]
            self.objs.append(MapObject(3, float(x), float(y), 0.0, 0.0, 0, '*Waypoints/Waypoint', props))
            if prev is not None: self.links.append((prev, wid))
            if first is None: first = wid
            prev = wid
        self.links.append((prev, first))                # closed loop: the train never leaves the map
        self.next_wp = wid
        (x0, y0), (x1, y1) = pts[0], pts[1]
        self.objs.append(MapObject(3, float(x0), float(y0), 0.0, math.atan2(y1 - y0, x1 - x0) % math.tau, 0, eng,
                                   self.mkprops('EARTH_Train', self.base_props)))
        self.stats['train'] = 1
        self.stats['train_track_m'] = round(geo.polyline_len(best) / self.p.upm)
        self.stats['train_stations'] = len(stations)

    def build_air_paths(self):
        """ZombieSurvival / Defense: 4 MiG routes across the town (wheel of death AirSpawn<k> / AirPath<k>): from one
        edge over the centre to the opposite one."""
        self.air_paths = []
        if not self.zsurv: return
        X, Y = self.AX, self.AY
        wid = getattr(self, 'next_wp', len(self.starts))
        C = (X / 2, Y / 2)
        routes = [((60, Y * 0.3), C, (X - 60, Y * 0.7)), ((X - 60, Y * 0.25), C, (60, Y * 0.75)),
                  ((X * 0.3, Y - 60), C, (X * 0.7, 60)), ((X * 0.75, 60), C, (X * 0.25, Y - 60))]
        for k, pts in enumerate(routes, 1):
            label = f'EARTH_AirPath{k}'
            prev = None
            for j, (x, y) in enumerate(pts):
                wid += 1
                nm = f'EARTH_AirSpawn{k}' if j == 0 else f'EARTH_Air{k}_{j}'
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
            self.air_paths.append(label)
        self.next_wp = wid

    def _split_on_water(self, run, step=5.0, pad=4, max_span=4400.0):   # Danube bridges are ~0.5 km long
        """Densify a road and cut it into ('road', pts) / ('bridge', a, b) pieces at water crossings."""
        pts = [run[0]]
        orig = {0}                                   # indices of the original vertices
        for a, b in zip(run[:-1], run[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1]); k = max(1, int(L / step))
            for i in range(1, k + 1):
                pts.append((a[0] + (b[0] - a[0]) * i / k, a[1] + (b[1] - a[1]) * i / k))
            orig.add(len(pts) - 1)
        n = len(pts)
        wet = [self._wet(x, y) for x, y in pts]
        if not any(wet):
            return [('road', run)]
        runs, i = [], 0
        while i < n:
            if wet[i]:
                j = i
                while j < n and wet[j]: j += 1
                runs.append((i, j)); i = j
            else:
                i += 1
        pieces, start = [], 0

        def land(a, b):
            if b - a >= 1:
                pieces.append(('road', [pts[i] for i in range(a, b + 1) if i in (a, b) or i in orig]))

        def reach_dry(p0, p1, limit=400.0):
            # walk from p0 away from p1 until dry ground (+ a small landing), None if no bank in reach
            dx, dy = p0[0] - p1[0], p0[1] - p1[1]; L = math.hypot(dx, dy) or 1.0
            dx, dy = dx / L, dy / L
            t = 0.0
            while t < limit:
                x, y = p0[0] + dx * t, p0[1] + dy * t
                if not self.inside(x, y): return None
                if not self._wet(x, y):
                    return (x + dx * 20, y + dy * 20)
                t += 5.0
            return None

        for i, j in runs:
            if i == 0 or j >= n:
                # the way starts and/or ends over water - typical for OSM bridge ways
                a = pts[max(i - 1 - pad, start)] if i > 0 else reach_dry(pts[0], pts[min(1, n - 1)])
                b = pts[min(j + pad, n - 1)] if j < n else reach_dry(pts[-1], pts[max(n - 2, 0)])
                if a is not None and b is not None and math.hypot(b[0] - a[0], b[1] - a[1]) <= max_span:
                    if i > 0: land(start, max(i - 1 - pad, start))
                    pieces.append(('bridge', a, b))
                elif i > 0:
                    land(start, i - 1)
                start = n if j >= n else min(j + pad, n - 1)
                if j >= n: break
                continue
            s0, e0 = max(i - 1 - pad, start), min(j + pad, n - 1)
            span = math.hypot(pts[e0][0] - pts[s0][0], pts[e0][1] - pts[s0][1])
            if span <= max_span and not wet[s0] and not wet[e0]:
                land(start, s0)
                pieces.append(('bridge', pts[s0], pts[e0]))
                start = e0
            else:                           # lake / too long: road just stops at the shore
                land(start, i - 1); start = j
        if start < n - 1: land(start, n - 1)
        return pieces

    # ----------------------------------------------------------- overpasses
    OVER_CLEAR = 28.0      # game units between the lower road and the bridge deck

    OVER_SLOPE = 0.07      # slope of the embankment up to the bridge

    def build_overpasses(self):
        """Two roads that cross without a shared junction (OSM: one is a bridge) -> a real overpass.
        The ground round the upper road is raised to deck height (an embankment with gentle ramps), except a corridor
        along the lower road that stays at road level; emit_roads() then makes the upper road a bridge across it."""
        self.over_spans = {}
        VEH = PAVED_STREETS + UNPAVED_STREETS
        RANK = {rt: i for i, rt in enumerate(('TrainTrack',) + VEH)}
        lines = self.road_lines
        bw = [w for w in getattr(self.osm, 'bridge_ways', []) if len(w) >= 2]
        if not bw or not lines: return
        WX, WY, P = self.WX, self.WY, self.p
        bimg = Image.new('L', (WX, WY), 0); bd = ImageDraw.Draw(bimg)
        for w in bw:
            bd.line([(self.cx(x), self.cx(y)) for x, y in (P.to_units(*pt) for pt in w)], fill=255, width=2)
        bm = np.asarray(bimg) > 0

        def flagged(x, y):
            c, r = int(self.cx(x)), int(self.cx(y))
            return 0 <= r < WY and 0 <= c < WX and bool(bm[max(r - 1, 0):r + 2, max(c - 1, 0):c + 2].any())

        run_flag = {}

        def is_bridge_run(li):
            """a road is the bridge itself when (almost) all of it lies on an OSM way tagged bridge / layer>0"""
            if li not in run_flag:
                n_in = n_all = 0
                run = lines[li][1]
                for a_, b_ in zip(run[:-1], run[1:]):
                    L_ = math.hypot(b_[0] - a_[0], b_[1] - a_[1]); k = max(1, int(L_ / 8))
                    for t_ in range(k + 1):
                        n_all += 1; n_in += flagged(a_[0] + (b_[0] - a_[0]) * t_ / k, a_[1] + (b_[1] - a_[1]) * t_ / k)
                run_flag[li] = n_all > 0 and n_in / n_all >= 0.6
            return run_flag[li]

        G = 120.0
        grid = {}
        for li, (rt, run) in enumerate(lines):
            if rt not in VEH and rt != 'TrainTrack': continue          # a railway can only pass under a bridge
            for si in range(len(run) - 1):
                a, b = run[si], run[si + 1]
                for gx in range(int(min(a[0], b[0]) // G), int(max(a[0], b[0]) // G) + 1):
                    for gy in range(int(min(a[1], b[1]) // G), int(max(a[1], b[1]) // G) + 1):
                        grid.setdefault((gx, gy), []).append((li, si))
        cross, seen = [], set()
        for cell in grid.values():
            for ai in range(len(cell)):
                for bi in range(ai + 1, len(cell)):
                    (li, si), (lj, sj) = cell[ai], cell[bi]
                    if li == lj or (li, si, lj, sj) in seen: continue
                    seen.add((li, si, lj, sj))
                    p, p2 = lines[li][1][si], lines[li][1][si + 1]
                    q, q2 = lines[lj][1][sj], lines[lj][1][sj + 1]
                    rx, ry, sx, sy = p2[0] - p[0], p2[1] - p[1], q2[0] - q[0], q2[1] - q[1]
                    den = rx * sy - ry * sx
                    lr, ls = math.hypot(rx, ry), math.hypot(sx, sy)
                    if lr < 1e-6 or ls < 1e-6 or abs(den) < 0.42 * lr * ls: continue      # parallel / shallow angle
                    t = ((q[0] - p[0]) * sy - (q[1] - p[1]) * sx) / den
                    u = ((q[0] - p[0]) * ry - (q[1] - p[1]) * rx) / den
                    if not (-1e-6 <= t <= 1 + 1e-6 and -1e-6 <= u <= 1 + 1e-6): continue
                    X = (p[0] + t * rx, p[1] + t * ry)
                    near_v = lambda a_, b_: min(math.hypot(X[0] - a_[0], X[1] - a_[1]), math.hypot(X[0] - b_[0], X[1] - b_[1])) < 3.0
                    if near_v(p, p2) and near_v(q, q2): continue                    # a real junction (shared node)
                    if self._wet(*X): continue                                       # river bridges are made elsewhere
                    fi, fj = is_bridge_run(li), is_bridge_run(lj)
                    if not (fi or fj): continue                                      # no bridge tag: leave it as it was
                    if fi != fj: up = li if fi else lj
                    else: up = li if RANK[lines[li][0]] >= RANK[lines[lj][0]] else lj
                    lo = lj if up == li else li
                    if lines[up][0] == 'TrainTrack': continue
                    s_ang = abs(den) / (lr * ls)
                    cross.append((up, lo, X, s_ang, (si if up == li else sj), (t if up == li else u)))
        if not cross: return

        # arc length of every crossing along its upper road, grouped per upper road
        def cum_len(run):
            c = [0.0]
            for a, b in zip(run[:-1], run[1:]): c.append(c[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
            return c
        by_up = {}
        for up, lo, X, sa, si, t in cross:
            run = lines[up][1]; cl = cum_len(run)
            seg = cl[si + 1] - cl[si]
            s = cl[si] + t * seg
            ext = (self.road_w[lines[lo][0]] / 2 + 14.0) / max(sa, 0.42)
            by_up.setdefault(up, []).append((s, min(ext, 400.0), lo, X))
        gap = max(50.0, 20.0 * P.upm)
        yy, xx = np.mgrid[:WY, :WX]
        px_u = (xx + 0.5 - BORDER) * CELL; py_u = (yy + 0.5 - BORDER) * CELL
        n_over = 0
        Hmax = 255 * HS - 1.0
        # roads meeting end to end (shared end point) continue the embankment along the approach
        ends = {}
        for li, (rt, run) in enumerate(lines):
            if rt in VEH:
                ends.setdefault((round(run[0][0] / 3), round(run[0][1] / 3)), []).append((li, 0))
                ends.setdefault((round(run[-1][0] / 3), round(run[-1][1] / 3)), []).append((li, 1))

        def extend(li, want, front):
            """points continuing road li beyond its start (front) or end, about `want` units long"""
            out, used, cur, got = [], {li}, li, 0.0
            pt = lines[li][1][0] if front else lines[li][1][-1]
            prev = lines[li][1][1] if front else lines[li][1][-2]
            for _ in range(8):
                if got >= want: break
                best = None
                for lj, e in ends.get((round(pt[0] / 3), round(pt[1] / 3)), []):
                    if lj in used: continue
                    r2 = lines[lj][1] if e == 0 else lines[lj][1][::-1]
                    d1 = (pt[0] - prev[0], pt[1] - prev[1]); d2 = (r2[1][0] - r2[0][0], r2[1][1] - r2[0][1])
                    n1, n2 = math.hypot(*d1) or 1.0, math.hypot(*d2) or 1.0
                    cs = (d1[0] * d2[0] + d1[1] * d2[1]) / (n1 * n2)
                    if cs > 0.5 and (best is None or cs > best[0]): best = (cs, lj, r2)
                if best is None: break
                _, lj, r2 = best
                used.add(lj)
                for a_ in r2[1:]:
                    out.append(a_); got += math.hypot(a_[0] - pt[0], a_[1] - pt[1]); pt = a_
                prev = r2[-2] if len(r2) > 1 else prev
                if got >= want: break
            return out

        for up, items in by_up.items():
            items.sort(key=lambda it: it[0])
            groups, cur = [], None
            for s, ext, lo, X in items:
                if cur and s - ext <= cur['s1'] + gap:
                    cur['s1'] = max(cur['s1'], s + ext); cur['lows'].add(lo); cur['X'].append(X)
                else:
                    cur = {'s0': s - ext, 's1': s + ext, 'lows': {lo}, 'X': [X]}; groups.append(cur)
            run = lines[up][1]; rt_up = lines[up][0]; L = cum_len(run)[-1]
            for g in groups:
                s0, s1 = max(0.0, g['s0'] - 6.0), min(L, g['s1'] + 6.0)
                if s1 - s0 < 60.0:
                    mid = (s0 + s1) / 2; s0, s1 = max(0.0, mid - 30.0), min(L, mid + 30.0)
                if s1 - s0 < 30.0 or s1 - s0 > 700.0: continue
                self.over_spans.setdefault(up, []).append((s0, s1))
                # the chain: approach before + the road + approach after
                Lr = self.OVER_CLEAR / self.OVER_SLOPE
                pre = extend(up, Lr, True)[::-1]; post = extend(up, Lr, False)
                chain = pre + list(run) + post
                if not pre:
                    a_, b_ = run[0], run[1]; d_ = math.hypot(b_[0] - a_[0], b_[1] - a_[1]) or 1.0
                    chain = [(a_[0] - (b_[0] - a_[0]) / d_ * Lr, a_[1] - (b_[1] - a_[1]) / d_ * Lr)] + chain
                if not post:
                    a_, b_ = run[-1], run[-2]; d_ = math.hypot(b_[0] - a_[0], b_[1] - a_[1]) or 1.0
                    chain = chain + [(a_[0] - (b_[0] - a_[0]) / d_ * Lr, a_[1] - (b_[1] - a_[1]) / d_ * Lr)]
                cc = cum_len(chain)
                # offset of the original road inside the chain
                k0 = next((i for i, pt in enumerate(chain) if abs(pt[0] - run[0][0]) < 1e-6 and abs(pt[1] - run[0][1]) < 1e-6), 0)
                off = cc[k0]
                c0, c1 = off + s0, off + s1
                xs_ = [pt[0] for pt in chain]; ys_ = [pt[1] for pt in chain]
                hw = self.road_w[rt_up] / 2 + 10.0; fall = 115.0
                bx0, bx1 = int(self.cx(min(xs_) - hw - fall)) - 1, int(self.cx(max(xs_) + hw + fall)) + 2
                by0, by1 = int(self.cx(min(ys_) - hw - fall)) - 1, int(self.cx(max(ys_) + hw + fall)) + 2
                bx0, by0, bx1, by1 = max(bx0, 0), max(by0, 0), min(bx1, WX), min(by1, WY)
                if bx1 - bx0 < 2 or by1 - by0 < 2: continue
                gx_, gy_ = px_u[by0:by1, bx0:bx1], py_u[by0:by1, bx0:bx1]
                dmin = np.full(gx_.shape, 1e18); sc = np.zeros(gx_.shape)
                for i in range(len(chain) - 1):
                    a_, b_ = chain[i], chain[i + 1]
                    dx_, dy_ = b_[0] - a_[0], b_[1] - a_[1]; l2 = dx_ * dx_ + dy_ * dy_
                    if l2 < 1e-9: continue
                    tt = np.clip(((gx_ - a_[0]) * dx_ + (gy_ - a_[1]) * dy_) / l2, 0, 1)
                    dd = np.hypot(gx_ - (a_[0] + tt * dx_), gy_ - (a_[1] + tt * dy_))
                    better = dd < dmin
                    dmin = np.where(better, dd, dmin); sc = np.where(better, cc[i] + tt * math.sqrt(l2), sc)
                # deck height: the road level under the bridge + clearance
                base = float(np.mean([self.H[min(max(int(self.cx(y)), 0), WY - 1), min(max(int(self.cx(x)), 0), WX - 1)] for x, y in g['X']]))
                D = min(base + self.OVER_CLEAR, Hmax)
                out_ = np.maximum(0.0, np.maximum(c0 - sc, sc - c1))
                T = D - self.OVER_SLOPE * out_
                wc = np.clip(1.0 - np.maximum(0.0, dmin - hw) / fall, 0.0, 1.0)
                Hs = self.H[by0:by1, bx0:bx1]
                raise_ = np.maximum(0.0, (T - Hs)) * wc
                # the corridor of the lower road(s) stays at road level
                cimg = Image.new('L', (bx1 - bx0, by1 - by0), 0); cd = ImageDraw.Draw(cimg)
                for lo in g['lows']:
                    wpx = max(2, int((self.road_w[lines[lo][0]] + 18) / CELL))
                    cd.line([(self.cx(x) - bx0, self.cx(y) - by0) for x, y in lines[lo][1]], fill=255, width=wpx)
                cm = np.clip(geo.box_mean(np.asarray(cimg) > 0, 6) * 2.5, 0.0, 1.0)
                # Confine the lower corridor to the crossing so it cannot carve a long shelf.
                dist = np.full(gx_.shape, np.inf)
                for x, y in g['X']:
                    np.minimum(dist, np.hypot(gx_ - x, gy_ - y), out=dist)
                cm *= np.clip((230.0 - dist) / 135.0, 0.0, 1.0)
                raise_ = raise_ * (1.0 - cm)
                raise_[self.water_mask[by0:by1, bx0:bx1]] = 0.0
                self.H[by0:by1, bx0:bx1] = np.minimum(Hs + raise_, Hmax)
                n_over += 1
        self.stats['overpasses'] = n_over

    def _cut_spans(self, run, spans):
        """Cut a road at the overpass spans (arc length intervals): ('road', pts) and ('bridge', a, b) pieces."""
        cl = [0.0]
        for a, b in zip(run[:-1], run[1:]): cl.append(cl[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        L = cl[-1]

        def at(s):
            s = min(max(s, 0.0), L)
            for i in range(len(run) - 1):
                if s <= cl[i + 1] or i == len(run) - 2:
                    seg = (cl[i + 1] - cl[i]) or 1.0; t = (s - cl[i]) / seg
                    return (run[i][0] + (run[i + 1][0] - run[i][0]) * t, run[i][1] + (run[i + 1][1] - run[i][1]) * t)

        def sub(a, b):
            pts = [at(a)] + [run[i] for i in range(len(run)) if a + 1e-6 < cl[i] < b - 1e-6] + [at(b)]
            return pts

        pieces, pos = [], 0.0
        for s0, s1 in sorted(spans):
            if s0 < pos: s0 = pos
            if s1 - s0 < 5: continue
            if s0 - pos > 3: pieces.append(('road', sub(pos, s0)))
            pieces.append(('bridge', at(s0), at(s1)))
            pos = s1
        if L - pos > 3: pieces.append(('road', sub(pos, L)))
        return pieces

    def emit_roads(self):
        n, nb = 0, 0
        for rt, run in self.road_lines:
            n += 1
            base_p = self._cut_spans(run, self.over_spans[n - 1]) if self.over_spans.get(n - 1) else [('road', run)]
            pieces = []
            for pc0 in base_p:
                pieces += self._split_on_water(pc0[1]) if (pc0[0] == 'road' and self.water_mask.any()) else [pc0]
            for pi, pc in enumerate(pieces):
                if pc[0] == 'road':
                    pts = pc[1]
                    for j, (a, b) in enumerate(zip(pts[:-1], pts[1:])):
                        self.add_obj(rt, a[0], a[1], 0, FLAG_ROAD_A, f'EARTH_Road_{n}_{pi}_{j}_A', self.road_props)
                        self.add_obj(rt, b[0], b[1], 0, FLAG_ROAD_B, f'EARTH_Road_{n}_{pi}_{j}_B', self.road_props)
                else:
                    _, a, b = pc
                    # The two mapped carriageways can have separate decks.
                    near = lambda p, q: math.hypot(p[0] - q[0], p[1] - q[1]) < min(60, self.road_w[rt] * 0.7)
                    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                    if any((near(a, c) and near(b, d)) or (near(a, d) and near(b, c)) or
                           near(mid, ((c[0] + d[0]) / 2, (c[1] + d[1]) / 2)) for c, d in self.bridges):
                        continue
                    bt = BRIDGE.get(rt, 'EuropeanBridgeAsphalt')
                    nb += 1
                    self.add_obj(bt, a[0], a[1], 0, FLAG_BRIDGE_A, f'EARTH_Bridge_{nb}_A', self.road_props)
                    self.add_obj(bt, b[0], b[1], 0, FLAG_BRIDGE_B, f'EARTH_Bridge_{nb}_B', self.road_props)
                    self.bridges.append((a, b))
        for i, (a, b) in enumerate(getattr(self, 'parking_rows', []), 1):
            self.add_obj('ParkingLines', *a, 0, FLAG_ROAD_A, f'EARTH_Parking_{i}_A', self.road_props)
            self.add_obj('ParkingLines', *b, 0, FLAG_ROAD_B, f'EARTH_Parking_{i}_B', self.road_props)
        self.stats['bridges'] = nb
