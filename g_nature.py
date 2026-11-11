"""Trees, details (graveyards, playgrounds, lamps ...) and life (civilians, cars, animals)."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class NatureMixin:
    # ----------------------------------------------------------- trees
    def _tree_ok(self, x, y):
        if not self.inside(x, y, 5): return False
        lm = getattr(self, 'lot_mask', None)
        if lm is not None:
            c_, r_ = int(self.cx(x)), int(self.cx(y))
            if 0 <= r_ < lm.shape[0] and 0 <= c_ < lm.shape[1] and lm[r_, c_]: return False   # no tree on a car park
        px, py = int(self.cx(x) * OCC), int(self.cx(y) * OCC)
        if self.occ[py, px]:
            # a forest around a start stays; only the core where the first buildings go is kept free
            bo = getattr(self, 'base_only', None)
            if bo is None or not bo[py, px]: return False
            if any(math.hypot(x - sx, y - sy) < max(AI_CORE_R, self.base_clear * 0.35) for sx, sy in self.starts): return False
        cx, cy = int(self.cx(x)), int(self.cx(y))
        return not self.water_mask[cy, cx]

    def _put_tree(self, name, x, y):
        self.ntrees += 1
        self.add_obj(name, x, y, self.rng.random() * math.tau, 0, f'EARTH_Tree_{self.ntrees}', self.base_props)
        # trees block buildings placed later
        px, py = int(self.cx(x) * OCC), int(self.cx(y) * OCC)
        self.occ[max(py - 1, 0):py + 2, max(px - 1, 0):px + 2] = True

    def place_trees(self):
        """Trees. The engine draws at most 4000 trees (W3DTreeBuffer::MAX_TREES in EA's source) and silently drops
        the rest, so the budget is spent where it shows: OSM single trees / tree rows / willows first, then forest
        trees spread over ALL forests with extra weight on forest edges, in shuffled order."""
        P, A, R = self.p, self.AREA, self.rng
        self.ntrees = 0
        have = lambda names: [n for n in names if n in self.known_objects] or names[:1]
        season = self.opt['season']
        if season == 'autumn':
            BL = ['TreeOakFall1', 'TreeOakFall2', 'TreeOakFall3', 'TreeOakFall4', 'TreeOakFall5', 'TreeOakFall6',
                  'TreeOakFall7', 'TreeOakFall8', 'TreeMaple05', 'TreeMaple06', 'TreeBirch03', 'TreeBirch04']
            CF = CONIFER
        elif season == 'winter':
            BL = ['TreeOak01Snow', 'TreeOak02Snow', 'TreeMaple03Snow', 'TreeMaple04Snow', 'TreeDogwood1Snow']
            CF = ['TreeSpruceSnow', 'TreeSpruce03Snow', 'TreeSpruce04Snow', 'TreeSpruce05Snow', 'TreeFir01snow',
                  'TreeFir06Snow', 'TreeFir07Snow', 'TreeFir08Snow', 'TreePineSnow', 'TreePine4snow', 'TreePine5snow']
        else:
            BL, CF = BROADLEAF, CONIFER
        broad, conif, willow, bush = have(BL), have(CF), have(WILLOWS), have(BUSHES)
        if season == 'winter': bush = have(['Bush12Snow', 'Bush13Snow', 'Bush14Snow'])
        if not self.opt['bushes']: bush = None
        budget = max(0, int(self.opt['max_trees']))

        # ---- 1) the trees that matter most: OSM single trees, tree rows, willows by the water
        extras = []
        for lat, lon in self.osm.trees:
            x, y = P.to_units(lat, lon)
            if self._tree_ok(x, y): extras.append((R.choice(STREET_TREES), x, y))
        for ring in self.osm.tree_rows:
            line = [P.to_units(*pt) for pt in ring]
            for run in geo.clip_polyline(line, self.AX, self.AY):
                for a_, b_ in zip(run[:-1], run[1:]):
                    seg = math.hypot(b_[0] - a_[0], b_[1] - a_[1]); t = 0.0
                    while t < seg:
                        x, y = a_[0] + (b_[0] - a_[0]) * t / seg, a_[1] + (b_[1] - a_[1]) * t / seg
                        if self._tree_ok(x, y): extras.append((R.choice(STREET_TREES), x, y))
                        t += 40
        # town greenery: parks get trees, lawns / gardens / scrub get a few trees and bushes (OSM areas)
        ng = 0
        for kind, ring in getattr(self.osm, 'areas', []):
            if kind not in ('park', 'green'): continue
            poly = geo.clip_poly([P.to_units(*pt) for pt in ring], self.AX, self.AY)
            if len(poly) < 3: continue
            xs_, ys_ = [p_[0] for p_ in poly], [p_[1] for p_ in poly]
            area_u = abs(geo.poly_area(poly))
            step = 48.0 if kind == 'park' else 64.0          # ~12 m / 16 m between trees
            nmax = int(area_u / (step * step))
            for _ in range(min(400, nmax * 2)):
                x = min(xs_) + R.random() * (max(xs_) - min(xs_)); y = min(ys_) + R.random() * (max(ys_) - min(ys_))
                if not geo.point_in_poly(x, y, poly): continue
                if self._tree_ok(x, y):
                    use_bush = bush and (R.random() < (0.3 if kind == 'park' else 0.55))
                    extras.append((R.choice(bush) if use_bush else R.choice(STREET_TREES), x, y)); ng += 1
                    if len(extras) > budget // 2: break
        # lawns between the blocks of flats and street trees (Slovak housing estates are green)
        for cx_, cy_, Lr, Wr, thr, ne_ in getattr(self, 'apt_rows', []):
            ux_, uy_, vx_, vy_ = math.cos(thr), math.sin(thr), -math.sin(thr), math.cos(thr)
            for side_ in (-1, 1):
                k_ = -Lr / 2
                while k_ <= Lr / 2:
                    if R.random() < 0.6:
                        off = Wr / 2 + (8 + R.random() * 10) * P.upm
                        x, y = cx_ + ux_ * k_ + vx_ * side_ * off, cy_ + uy_ * k_ + vy_ * side_ * off
                        if self._tree_ok(x, y):
                            extras.append((R.choice(bush) if bush and R.random() < 0.25 else R.choice(STREET_TREES), x, y)); ng += 1
                    k_ += 16 * P.upm
        for rt_, run_ in self.road_lines:
            if rt_ not in ASPHALT_ROADS: continue
            for a_, b_ in zip(run_[:-1], run_[1:]):
                seg = math.hypot(b_[0] - a_[0], b_[1] - a_[1])
                if seg < 1: continue
                nx_, ny_ = -(b_[1] - a_[1]) / seg, (b_[0] - a_[0]) / seg
                t_ = R.random() * 20 * P.upm
                while t_ < seg:
                    if R.random() < 0.3:
                        sd = 1 if R.random() < 0.5 else -1
                        off = self.road_w[rt_] / 2 + 5 * P.upm
                        x = a_[0] + (b_[0] - a_[0]) * t_ / seg + nx_ * sd * off
                        y = a_[1] + (b_[1] - a_[1]) * t_ / seg + ny_ * sd * off
                        if self._tree_ok(x, y): extras.append((R.choice(STREET_TREES), x, y)); ng += 1
                    t_ += 22 * P.upm
        self.stats['town_greenery'] = ng
        if self.opt['willows'] and self.water_mask.any():
            bank = (geo.box_mean(self.water_mask, 3) > 0) & ~self.water_mask
            ys_, xs_ = np.nonzero(bank)
            idx = list(range(len(xs_))); R.shuffle(idx)
            for i in idx[:min(len(idx) // 12, max(50, budget // 12))]:
                x = (xs_[i] - BORDER + R.random()) * CELL; y = (ys_[i] - BORDER + R.random()) * CELL
                if self._tree_ok(x, y):
                    extras.append((R.choice(willow) if (R.random() < 0.6 or not bush) else R.choice(bush), x, y))
        R.shuffle(extras)
        extras = extras[:budget // 2]               # never let extras eat the forests

        # ---- 2) dense forest candidates over every forest, then a weighted pick (edges count 4x)
        polys, leaf = [], []
        for ring, lt in zip(self.osm.forests, self.osm.forest_leaf or ['mixed'] * len(self.osm.forests)):
            poly = geo.clip_poly([P.to_units(*pt) for pt in ring], self.AX, self.AY)
            if len(poly) >= 3:
                polys.append(geo.rdp_ring(poly, 4.0)); leaf.append(lt)
        self.forest_mask = self.raster_poly(polys)
        sp = TREE_SPACING / max(float(self.opt['tree_density']), 0.1) * min(1.0, 2.5 / self.p.mpc) ** 0.5
        area = float(self.forest_mask.sum()) * CELL * CELL
        sp = max(sp, math.sqrt(area / 400000.0) if area else 0)      # safety: at most ~400k candidates
        edge = self.forest_mask & ~(geo.box_mean(self.forest_mask, max(2, int(40 / self.p.mpc))) > 0.99)
        cand, wts = [], []
        # groves: inside a forest the trees gather in dense clumps (smooth random field ** 3) instead of a thin even
        # spread, so the limited number of trees reads as thick forest; the dark forest floor fills the gaps
        gn = max(4, int(max(self.AX, self.AY) / 260))
        gfield = np.random.default_rng(R.randrange(1 << 30)).random((gn + 2, gn + 2))

        def grove(x, y):
            fx, fy = x / max(self.AX, self.AY) * gn, y / max(self.AX, self.AY) * gn
            ix, iy = int(fx), int(fy); tx, ty = fx - ix, fy - iy
            v = (gfield[iy, ix] * (1 - tx) * (1 - ty) + gfield[iy, ix + 1] * tx * (1 - ty) +
                 gfield[iy + 1, ix] * (1 - tx) * ty + gfield[iy + 1, ix + 1] * tx * ty)
            return 0.05 + 3.0 * v ** 3
        for poly, lt in zip(polys, leaf):
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            m = self.raster_poly([poly])
            mix = 0.9 if lt == 'needleleaved' else (0.1 if lt == 'broadleaved' else 0.45)
            y = max(min(ys), 0)
            while y < min(max(ys), self.AY):
                x = max(min(xs), 0)
                while x < min(max(xs), self.AX):
                    jx, jy = x + (R.random() - 0.5) * sp * 0.8, y + (R.random() - 0.5) * sp * 0.8
                    cx, cy = int(self.cx(jx)), int(self.cx(jy))
                    if 0 <= cx < self.WX and 0 <= cy < self.WY and m[cy, cx] and self._tree_ok(jx, jy):
                        on_edge = bool(edge[cy, cx])
                        if bush and on_edge and R.random() < 0.3: name = R.choice(bush)
                        else: name = R.choice(conif if R.random() < mix else broad)
                        cand.append((name, jx, jy)); wts.append(4.0 if on_edge else grove(jx, jy))
                    x += sp
                y += sp
        n_forest = max(0, min(len(cand), budget - len(extras)))
        if n_forest < len(cand):
            w = np.array(wts); w = w / w.sum()
            pick = np.random.default_rng(R.randrange(1 << 30)).choice(len(cand), size=n_forest, replace=False, p=w)
            forest = [cand[i] for i in pick]
        else:
            forest = cand
        R.shuffle(forest)
        for name, x, y in extras + forest:
            self._put_tree(name, x, y)
        self.stats['trees'] = self.ntrees
        self.stats['forest_tree_slots'] = len(cand)

    def _prop(self, name, x, y, ang, size=12.0, force=False):
        """Place a small prop if the spot is free. Returns True on success."""
        if self.known_objects and name not in self.known_objects: return False
        if not self.inside(x, y, 4): return False
        poly = self.rect(x, y, size, size, ang)
        if not force and self.occ_test(poly) > 0.05: return False
        cx, cy = int(self.cx(x)), int(self.cx(y))
        if self.water_mask[cy, cx]: return False
        self.occ_poly(poly, True)
        self.ndetail += 1
        self.add_obj(name, x, y, ang, 0, f'EARTH_Detail_{self.ndetail}', self.base_props)
        return True

    def place_nature_details(self):
        """Scatter small clusters of native game props on dry banks and in woodland."""
        density = float(self.opt['nature_details'])
        if density <= 0:
            return
        if self.opt['light']:
            density = min(density, 0.35)
        R = self.rng
        winter = self.opt['season'] == 'winter'
        rocks = [('RockClusterSmallSnow01', 22), ('RockClusterMediumSnow01', 32)] if winter else [
            ('Rocks1Brown', 16), ('Rocks2Brown', 18), ('RocksG03', 18), ('RocksG09', 18),
            ('RockClusterSmall01', 24), ('RockClusterMedium01', 34)]
        stumps = [('TreeOakStump', 16), ('TreePineStump', 16), ('TreeMapleStump', 16)]
        bushes = [(n, 14) for n in (['Bush12Snow', 'Bush13Snow'] if winter else BUSHES[:5])]
        banks = (geo.box_mean(self.water_mask, 5) > 0) & ~self.water_mask
        self.ndetail = getattr(self, 'ndetail', 0)
        before = self.ndetail
        for mask, palette, spacing, limit in (
            (banks, rocks + (bushes if self.opt['willows'] else []), 24, 520),
            (self.forest_mask & ~banks & ~self.water_mask, rocks + stumps + (bushes if self.opt['bushes'] else []), 44, 650),
            ((self.slope > 0.20) & (self.slope < 0.55) & ~self.forest_mask & ~banks & ~self.water_mask, rocks, 88, 180),
        ):
            palette = [(name, size) for name, size in palette if name in self.known_objects]
            if not palette:
                continue
            cells = np.flatnonzero(mask[BORDER:BORDER + self.NY, BORDER:BORDER + self.NX])
            count = min(limit, math.ceil(density * len(cells) / spacing))
            for cell in R.sample(range(len(cells)), min(len(cells), count * 3)):
                if count <= 0:
                    break
                row, col = divmod(int(cells[cell]), self.NX)
                x, y = (col + R.random()) * CELL, (row + R.random()) * CELL
                name, size = R.choice(palette)
                if not self._prop(name, x, y, R.random() * math.tau, size):
                    continue
                count -= 1
                if count > 0 and R.random() < 0.45:
                    t = R.random() * math.tau
                    name, size = R.choice(palette)
                    if self._prop(name, x + 36 * math.cos(t), y + 36 * math.sin(t), t, size):
                        count -= 1
        self.stats['nature_details'] = self.ndetail - before

    def place_parking(self, poly, rect, lot_index, cnt):
        """Lay out straight ParkingLines strips and park cars in the same bays."""
        cx, cy, length, width, angle = rect
        ux, uy = math.cos(angle), math.sin(angle)
        R = self.rng
        cars = [name for name in ('CarSedan01', 'CarCompact', 'CarEuroVan1', 'CarBug1', 'CarLuxury01', 'CarSUV01',
                                  'CarSwatchEuro', 'CarSport01')
                if not self.known_objects or name in self.known_objects]

        def point(along, across):
            return cx + ux * along - uy * across, cy + uy * along + ux * across

        def park(along, across, face):
            if not self.opt['cars'] or not cars or R.random() > 0.8:
                return
            x, y = point(along, across)
            if geo.point_in_poly(x, y, poly) and self._prop(R.choice(cars), x, y, face, 9):
                cnt['parked_cars'] = cnt.get('parked_cars', 0) + 1

        # The straight road atlas repeats every four road widths and has eight bays.
        stripe_width = self.road_w['ParkingLines']
        bay, depth = stripe_width / 2, stripe_width * 0.95
        row_step = depth + max(15, 4.5 * self.p.upm)
        rows = 1 + int((width - depth - 4) // row_step)
        bays = int((length - 6) // bay)
        initial_budget = self.parking_budget
        if self.parking_budget and rows > 0 and bays >= 2:
            first = -bay * bays / 2
            for r in range(rows):
                across = (r - (rows - 1) / 2) * row_step
                face = angle + (-1 if r % 2 else 1) * math.pi / 2
                row_start = None
                for j in range(bays + 1):
                    x, y = point(first + (j + 0.5) * bay, across)
                    footprint = self.rect(x, y, bay, depth, angle)
                    if (j < bays and geo.point_in_poly(x, y, poly)
                            and all(geo.point_in_poly(ax, ay, poly) for ax, ay in footprint)
                            and self.occ_test(footprint) == 0):
                        if row_start is None:
                            row_start = j
                        continue
                    if row_start is not None and j - row_start >= 2:
                        a, b = point(first + row_start * bay, across), point(first + j * bay, across)
                        self.parking_rows.append((b, a) if r % 2 else (a, b))
                        self.parking_budget -= 1
                        for k in range(row_start, j):
                            park(first + (k + 0.5) * bay, across, face)
                    row_start = None
                    if not self.parking_budget:
                        break
                if not self.parking_budget:
                    break
        if self.parking_budget < initial_budget:
            self.marked_parking.add(lot_index)
        elif self.opt['cars']:
            # Small lots and exhausted road budgets still use the parking ground texture.
            bay, depth = max(12, 2.5 * self.p.upm), max(16, 5.0 * self.p.upm)
            rows = max(1, int(width / (depth * 1.6)))
            for r in range(rows):
                across = (r - (rows - 1) / 2) * (width - depth) / max(1, rows - 1)
                for j in range(1, int(length / bay)):
                    park(-length / 2 + j * bay, across, angle + (-1 if r % 2 else 1) * math.pi / 2)

    def place_details(self):
        """Cemeteries, playgrounds, parks, parking, bus stops, church statues and street lamps."""
        P, A, R = self.p, self.AREA, self.rng
        self.ndetail = getattr(self, "ndetail", 0)
        self.area_polys = []
        self.parking_rows = []
        self.marked_parking = set()
        unused = int(self.stats['road_segment_limit'] * 0.9) - self.stats['road_segments_est']
        unused -= 3 * self.stats.get('entrance_paths', 0)
        self.parking_budget = max(0, unused // 3) if not self.cat.road_w or 'ParkingLines' in self.cat.road_w else 0
        cnt = {}
        for kind, ring in self.osm.areas:
            poly = geo.clip_poly([P.to_units(*pt) for pt in ring], self.AX, self.AY)
            if len(poly) < 3: continue
            r = geo.min_rect(poly)
            if not r: continue
            cx, cy, L, Wd, th = r
            self.area_polys.append((kind, poly))
            if kind == 'cemetery':
                m = self.raster_poly([poly])
                # rows of tombstones aligned with the cemetery, a crypt in the middle
                self._prop('Crypt01', cx, cy, th, 40)
                ux, uy, vx, vy = math.cos(th), math.sin(th), -math.sin(th), math.cos(th)
                i = -L / 2 + 14
                while i < L / 2 - 10:
                    j = -Wd / 2 + 14
                    while j < Wd / 2 - 10:
                        x, y = cx + ux * i + vx * j, cy + uy * i + vy * j
                        c_, r_ = int(self.cx(x)), int(self.cx(y))
                        if 0 <= c_ < self.WX and 0 <= r_ < self.WY and m[r_, c_]:
                            if self._prop(R.choice(['Tombstones01', 'Tombs']), x, y, th, 14):
                                cnt['tombstones'] = cnt.get('tombstones', 0) + 1
                        j += 30
                    i += 36
            elif kind == 'playground':
                self._prop('PlaygroundSet', cx, cy, th, 24)
                self._prop('Swing', cx + math.cos(th) * 30, cy + math.sin(th) * 30, th, 16)
                cnt['playgrounds'] = cnt.get('playgrounds', 0) + 1
            elif kind == 'park':
                if L * Wd > 30000: self._prop(R.choice(['Fountain1', 'Fountain2']), cx, cy, 0, 30)
                for k in range(4):
                    t = th + k * math.pi / 2
                    self._prop('ParkBench', cx + math.cos(t) * 45, cy + math.sin(t) * 45, t + math.pi / 2, 10)
                cnt['parks'] = cnt.get('parks', 0) + 1
            elif kind == 'parking':
                self.place_parking(poly, r, len(self.area_polys) - 1, cnt)
        self.stats['parking_marking_rows'] = len(self.parking_rows)
        for kind, lat, lon in self.osm.points:
            x, y = P.to_units(lat, lon)
            if kind == 'bus_stop':
                nr = self._nearest_road(x, y)
                if nr and nr[0] < 120:
                    d, px, py, a, rt = nr
                    nx, ny = (x - px) / (d or 1), (y - py) / (d or 1)
                    off = self.road_w[rt] / 2 + 8
                    if self._prop(R.choice(['BusStop', 'BusStop02']), px + nx * off, py + ny * off, a, 14):
                        cnt['bus_stops'] = cnt.get('bus_stops', 0) + 1
            elif kind == 'playground':
                self._prop('PlaygroundSet', x, y, R.random() * math.tau, 24)
        # churches: an angel statue in front of every church building
        for poly, name in self.bld_rects:
            if name in STYLES[self.style]['church'] and getattr(self, '_church_names', None) is None:
                pass
        for (poly, name), klass in zip(self.bld_rects, getattr(self, 'bld_klass', [])):
            if klass == 'church':
                cxp = sum(p[0] for p in poly) / 4; cyp = sum(p[1] for p in poly) / 4
                ax, ay = poly[1][0] - poly[0][0], poly[1][1] - poly[0][1]
                L = math.hypot(ax, ay) or 1
                self._prop('StatueAngel', cxp + ay / L * 60, cyp - ax / L * 60, math.atan2(ay, ax), 12)
                cnt['churches'] = cnt.get('churches', 0) + 1
        # street lamps along asphalt roads, only in built-up areas
        if not self.opt['lamps']:
            self.stats.update(cnt); return
        built = self.raster_poly([p for p, _ in self.bld_rects]) if self.bld_rects else np.zeros((self.WY, self.WX), bool)
        built = geo.box_mean(built, max(2, int(35 / self.p.mpc))) > 0
        lamp = 'OldStreetLamppost' if 'OldStreetLamppost' in self.known_objects or not self.known_objects else 'StreetLamp'
        nl = 0
        for rt, run in self.road_lines:
            if rt not in PAVED_STREETS + ('Sidewalk3',): continue
            side = 1
            off = self.road_w[rt] / 2 + 5
            for a_, b_ in zip(run[:-1], run[1:]):
                seg = math.hypot(b_[0] - a_[0], b_[1] - a_[1])
                if seg < 1: continue
                ux, uy = (b_[0] - a_[0]) / seg, (b_[1] - a_[1]) / seg
                t = 40.0
                while t < seg and nl < 1500:
                    x, y = a_[0] + ux * t, a_[1] + uy * t
                    c_, r_ = int(self.cx(x)), int(self.cx(y))
                    if 0 <= c_ < self.WX and 0 <= r_ < self.WY and built[r_, c_]:
                        if self._prop(lamp, x - uy * off * side, y + ux * off * side, math.atan2(uy, ux), 6):
                            nl += 1
                        side = -side
                    t += 140.0
        cnt['street_lamps'] = nl
        self.stats.update(cnt)

    # ----------------------------------------------------------- living world
    def place_life(self):
        """Civilians in town, parked cars along streets, farm machines and animals in fields,
        ambient sound emitters (birds in woods, town noise, rivers, church bell, owls at night)."""
        R, A = self.rng, self.AREA
        CIV = 'teamPlyrCivilian'
        known = lambda n: (not self.known_objects) or n in self.known_objects
        cnt = {}
        built = self.raster_poly([p for p, _ in self.bld_rects]) if self.bld_rects else np.zeros((self.WY, self.WX), bool)
        town = geo.box_mean(built, max(2, int(40 / self.p.mpc))) > 0.08 if self.bld_rects else built
        protected = getattr(self, 'skirmish_level', None)

        def put(name, x, y, ang, size, owner=None, force=False):
            if not known(name) or not self.inside(x, y, 8): return False
            if protected is not None and protected[int(self.cx(y)), int(self.cx(x))]: return False
            poly = self.rect(x, y, size, size, ang)
            if not force and self.occ_test(poly) > 0.05: return False
            if self._wet(x, y): return False
            self.occ_poly(poly, True)
            self.nlife = getattr(self, 'nlife', 0) + 1
            self.add_obj(name, x, y, ang, 0, f'EARTH_Life_{self.nlife}', self.base_props, owner)
            return True

        # parked cars along town streets (on the kerb, parallel to the road)
        if self.opt['traffic']:
            cars = ['CarSedan01', 'CarCompact', 'CarEuroVan1', 'CarEuroVan2', 'CarBug1', 'CarBug2', 'CarBug3',
                    'CarSwatchEuro', 'CarLuxury01', 'CarSport01', 'CarSUV01', 'TruckWork', 'CarEuroPoliceVan']
            n = 0
            for rt, run in self.road_lines:
                if rt not in PAVED_STREETS + ('Sidewalk3', 'Sidewalk2', 'Sidewalk'): continue
                off = self.road_w[rt] / 2 - 6
                for a_, b_ in zip(run[:-1], run[1:]):
                    seg = math.hypot(b_[0] - a_[0], b_[1] - a_[1])
                    if seg < 30: continue
                    ux, uy = (b_[0] - a_[0]) / seg, (b_[1] - a_[1]) / seg
                    t = 20.0
                    while t < seg - 20 and n < 300:
                        x, y = a_[0] + ux * t, a_[1] + uy * t
                        c_, r_ = int(self.cx(x)), int(self.cx(y))
                        if 0 <= c_ < self.WX and 0 <= r_ < self.WY and town[r_, c_] and R.random() < 0.18:
                            side = 1 if R.random() < 0.5 else -1
                            ang = math.atan2(uy, ux) + (0 if side > 0 else math.pi)
                            # cars sit on the road edge, so they are allowed on road occupancy
                            if put(R.choice(cars), x - uy * off * side, y + ux * off * side, ang, 14, CIV, force=True):
                                n += 1
                        t += 45.0
            cnt['parked_cars'] = n
        # civilians: pedestrians near houses in town
        if self.opt['civilians']:
            people = ['GenericMale01', 'GenericMale02', 'GenericFemale01', 'GenericFemale02', 'AmericanFarmer01']
            amount = max(0.0, float(self.opt['civ_amount']))
            cap = int(500 * amount) + 20
            n = 0
            # pedestrians on the pavements of town streets
            for rt, run in self.road_lines:
                if rt not in PAVED_STREETS + ('Sidewalk3', 'Sidewalk2', 'Sidewalk') or n >= cap * 0.5: continue
                off = self.road_w[rt] / 2 + 4
                for a_, b_ in zip(run[:-1], run[1:]):
                    seg = math.hypot(b_[0] - a_[0], b_[1] - a_[1])
                    if seg < 20: continue
                    ux, uy = (b_[0] - a_[0]) / seg, (b_[1] - a_[1]) / seg
                    t = R.random() * 60
                    while t < seg and n < cap * 0.5:
                        x, y = a_[0] + ux * t, a_[1] + uy * t
                        c_, r_ = int(self.cx(x)), int(self.cx(y))
                        if 0 <= c_ < self.WX and 0 <= r_ < self.WY and town[r_, c_] and R.random() < 0.10 * amount:
                            sd = 1 if R.random() < 0.5 else -1
                            if put(R.choice(people), x - uy * off * sd, y + ux * off * sd, math.atan2(uy, ux), 5, CIV):
                                n += 1
                        t += 60
            # people around the houses
            for poly, name in self.bld_rects:
                if n >= cap: break
                if R.random() > min(0.9, 0.12 * amount): continue
                cxp = sum(p[0] for p in poly) / 4; cyp = sum(p[1] for p in poly) / 4
                r = max(math.hypot(poly[0][0] - cxp, poly[0][1] - cyp), 10) + 10
                t = R.random() * math.tau
                if put(R.choice(people), cxp + math.cos(t) * r, cyp + math.sin(t) * r, R.random() * math.tau, 6, CIV):
                    n += 1
            cnt['civilians'] = n
        # farm life on fields and meadows
        if self.opt['farm_life']:
            n = 0
            for kind, ring in self.osm.landuse:
                if kind not in ('farmland', 'meadow') or n >= 120: continue
                poly = geo.clip_poly([self.p.to_units(*pt) for pt in ring], self.AX, self.AY)
                r = geo.min_rect(poly) if len(poly) >= 3 else None
                if not r: continue
                cx, cy, L, Wd, th = r
                if L * Wd < 40000: continue
                if kind == 'farmland':
                    choices = ['Tractor', 'TractorCombine', 'FarmerTruck', 'HorseDrawnCart', 'Haystack', 'Haystack']
                else:
                    choices = ['Ox', 'Ox', 'Ox', 'Haystack', 'HorseDrawnCart']
                for k in range(min(4, int(L * Wd / 60000) + 1)):
                    x = cx + (R.random() - 0.5) * L * 0.6; y = cy + (R.random() - 0.5) * Wd * 0.6
                    nm = R.choice(choices)
                    if put(nm, x, y, th + R.random() * 0.4, 20, None if nm == 'Haystack' else CIV): n += 1
            cnt['farm_objects'] = n
        # ambient sound emitters
        if self.opt['sounds']:
            night = int(self.opt['time_of_day']) == 4
            winter = self.opt['season'] == 'winter'
            n = 0
            def snd(name, x, y):
                nonlocal n
                if n < 80 and known(name) and self.inside(x, y, 5):
                    n += 1
                    self.add_obj(name, x, y, 0, 0, f'EARTH_Sound_{n}', self.base_props)
            fm = getattr(self, 'forest_mask', None)
            step = max(60, int(800 / CELL))              # one emitter per ~800 units of forest / town grid
            for gy in range(BORDER, self.WY - BORDER, step):
                for gx in range(BORDER, self.WX - BORDER, step):
                    x, y = (gx - BORDER + step / 2) * CELL, (gy - BORDER + step / 2) * CELL
                    c_, r_ = min(int(self.cx(x)), self.WX - 1), min(int(self.cx(y)), self.WY - 1)
                    if town[r_, c_]:
                        snd('Amb_TemperateTownDayAmbientLoop' if not night else 'Amb_DesertVillageNightDog', x, y)
                    elif fm is not None and fm[r_, c_]:
                        snd('Amb_WinterNatureWindLoop' if winter else ('Amb_TemperateNightOwl' if night else 'Amb_TemperateForestBirdsLoop'), x, y)
                    elif R.random() < 0.3:
                        snd('Amb_WinterNatureCrows' if winter else 'Amb_TemperateMeadowBirdsLoop', x, y)
            for poly, z, river, rs in self.water_polys[::4]:
                cxp = sum(p[0] for p in poly) / len(poly); cyp = sum(p[1] for p in poly) / len(poly)
                snd('Amb_WaterRiverLoop' if river else 'Amb_WaterLakeAmbientLoop', cxp, cyp)
            for kind, poly in getattr(self, 'area_polys', []):
                if kind == 'cemetery':
                    snd('Amb_UrbanCemetaryBell', sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
                elif kind == 'park':
                    snd('Amb_UrbanParkBirds', sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
            cnt['ambient_sounds'] = n
        self.stats.update(cnt)
