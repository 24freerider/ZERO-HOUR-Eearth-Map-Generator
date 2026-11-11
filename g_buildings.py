"""The town: houses, blocks of flats, garages, special buildings, fences, fuel, silos, pitches."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class BuildingsMixin:
    # ----------------------------------------------------------- buildings
    def rank_models(self, klass, L, Wd, hm=0.0):
        """Models of this class that fit the footprint, best first; near-equal ones shuffled for variety."""
        names = STYLES[self.style]
        pool = [n for n in names.get(klass, names['house']) if n in self.cat.buildings]
        if klass == 'apt' and self.apt_model == 'panel_mix':
            # the blocks from the player's screenshots: square tower block + long block with balconies
            long_block = L / max(Wd, 1e-3) >= 2.0 and L * Wd > 6000
            pick = ('StanSovietOffice01', 'StanSovietOffice02') if long_block else ('EuroHighrise2',)
            pool = [n for n in pick if n in self.cat.buildings]
        elif klass == 'apt' and self.apt_model != 'auto' and self.apt_model in self.cat.buildings:
            pool = [self.apt_model]
        scored = []
        for n in pool:
            xe, ye, mh = self.cat.buildings[n]
            a, b = max(xe, ye), min(xe, ye)
            if L / Wd >= 2.2 and L > 1.25 * a:
                # long block built from sections: the depth must match (deeper than reality looks worse),
                # the length comes from the section count
                sc = (2.0 * math.log(b / Wd) if b > Wd else 1.2 * math.log(Wd / b)) + 0.1 * abs(math.log(a / b))
            else:
                sc = abs(math.log(a * b / (L * Wd))) + 0.6 * abs(math.log((a / b) / (L / Wd)))
            if a > L * 1.35: sc += 0.6          # clearly longer than the real house -> penalise
            if hm and mh and klass in ('apt', 'civic', 'shop'):
                # height: the model, possibly stacked up to 3x, should reach the real height
                target = hm * self.p.upm
                fl = max(1, min(3 if self.stack_tall and n in STACKABLE else 1, round(target / mh)))
                sc += 0.6 * abs(math.log(target / (mh * fl)))
            scored.append((sc + self.rng.random() * 0.25, n))
        scored.sort()
        return [n for _, n in scored]

    # ----------------------------------------------------------- footprint fill (panelaky, schools, shops)
    @staticmethod
    def _largest_rect(m):
        """Largest all-True axis-aligned rectangle in a bool matrix: (area, row0, col0, rows, cols)."""
        h = np.zeros(m.shape[1], int)
        best = (0, 0, 0, 0, 0)
        for r in range(m.shape[0]):
            h = np.where(m[r], h + 1, 0)
            st = []
            for c in range(len(h) + 1):
                cur = h[c] if c < len(h) else 0
                start = c
                while st and st[-1][1] >= cur:
                    s0, hh = st.pop()
                    a = hh * (c - s0)
                    if a > best[0]: best = (a, r - hh + 1, s0, hh, c - s0)
                    start = s0
                st.append((start, cur))
        return best

    def footprint_rects(self, pts, holes, th, min_side):
        """Split a footprint (any shape: L, U, H, courtyards) into a few rectangles in its own orientation.
        Returns [(cx, cy, a, b, ang)] with a = length along ang."""
        c, sn = math.cos(th), math.sin(th)
        ox = sum(p[0] for p in pts) / len(pts); oy = sum(p[1] for p in pts) / len(pts)
        loc = lambda x, y: ((x - ox) * c + (y - oy) * sn, -(x - ox) * sn + (y - oy) * c)
        lp = [loc(*p) for p in pts]
        xs, ys = [p[0] for p in lp], [p[1] for p in lp]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        res = max(2.0, min(min_side / 4.0, span / 80.0))
        x0, y0 = min(xs), min(ys)
        W_, H_ = int((max(xs) - x0) / res) + 2, int((max(ys) - y0) / res) + 2
        img = Image.new('L', (W_, H_), 0); d = ImageDraw.Draw(img)
        d.polygon([((x - x0) / res, (y - y0) / res) for x, y in lp], fill=255)
        for h in holes:
            if len(h) >= 3:
                d.polygon([((x - x0) / res, (y - y0) / res) for x, y in (loc(*p) for p in h)], fill=0)
        m = np.asarray(img) > 0
        total = m.sum()
        if not total: return []
        out, covered = [], 0
        min_cells = max(2, int(min_side * 0.55 / res))
        while len(out) < 10 and covered < total * 0.93:
            a, r0, c0, nr, nc = self._largest_rect(m)
            if nr < min_cells or nc < min_cells: break
            m[r0:r0 + nr, c0:c0 + nc] = False
            covered += a
            lx, ly = x0 + (c0 + nc / 2) * res, y0 + (r0 + nr / 2) * res
            wx, wy = ox + lx * c - ly * sn, oy + lx * sn + ly * c
            a_len, b_len = nc * res, nr * res
            ang = th if a_len >= b_len else th + math.pi / 2
            out.append((wx, wy, max(a_len, b_len), min(a_len, b_len), ang))
        return out

    def _piece_options(self, klass, hm, square, a, b, area_m2=0.0):
        """Pieces for one part of a footprint: by real height (floors) and shape (square vs. long)."""
        if klass == 'flat':
            if area_m2 < 250: return FLAT_PIECES
            if hm >= 6 * 3.0 + 1: return CITY_TALL
            return CITY_SQUARE if square else CITY_LONG
        if klass == 'garage':
            return GARAGE_PIECES
        floors = int(round((hm - 1) / 3)) if hm else 4
        squarish = a / max(b, 1e-3) < 1.8
        if floors >= 7:                                       # high-rise: a square tower = ONE CivilianHighrise02,
            return ['CivilianHighrise02'] if squarish else ['CivilianHighrise01', 'CivilianHighrise02']   # long = 01
        if getattr(self, 'dense', False) and b > 24 * self.p.upm:    # big-city solid block: big pieces, not a grid
            return CITY_SQUARE if square else CITY_LONG
        # lower blocks (up to 6 floors): one entrance = AsiaApartment02, two entrances = AsianApartmentSingle01,
        # long blocks are built mainly from the two-entrance piece
        if a >= 30 * self.p.upm and not square:            # a long block: the long two-entrance piece
            return ['AsianApartmentSingle01', PANEL_TOWER]
        return [PANEL_TOWER, 'AsianApartmentSingle01']      # a short block: the one-entrance piece

    def _garage_rows(self, blds):
        """Single garages mapped one by one (3 x 6 m each) -> rows: neighbours of the same direction are joined into
        one footprint, so the garages stand neatly side by side like in reality instead of a scatter of boxes."""
        P = self.p
        gar, rest = [], []
        for tags, ring in blds:
            if tags.get('building') in ('garage', 'garages') and len(ring) >= 4:
                r = geo.min_rect([P.to_units(*pt) for pt in ring[:-1]])
                if r: gar.append((tags, ring, r)); continue
            rest.append((tags, ring))
        par = list(range(len(gar)))
        def f(i):
            while par[i] != i: par[i] = par[par[i]]; i = par[i]
            return i
        for i in range(len(gar)):
            for j in range(i + 1, len(gar)):
                (x1, y1, L1, W1, t1), (x2, y2, L2, W2, t2) = gar[i][2], gar[j][2]
                d = abs((t1 - t2 + math.pi / 2) % math.pi - math.pi / 2)
                if d > 0.3: continue
                # neighbours in ONE row stand side by side: offset along the short side (the garage width),
                # hardly any along the long side (the depth) - rows behind / across a driveway stay separate
                dx, dy = x2 - x1, y2 - y1
                along_long = abs(dx * math.cos(t1) + dy * math.sin(t1))
                along_short = abs(-dx * math.sin(t1) + dy * math.cos(t1))
                if along_long < 0.35 * max(L1, L2) and along_short < 1.6 * max(W1, W2) + 4:
                    par[f(i)] = f(j)
        groups = {}
        for i in range(len(gar)): groups.setdefault(f(i), []).append(i)
        out = list(rest)
        for g in groups.values():
            if len(g) == 1:
                out.append(gar[g[0]][:2]); continue
            pts = [P.to_units(*pt) for i in g for pt in gar[i][1][:-1]]
            cx, cy, L, W, th = geo.min_rect(pts)
            c, sn = math.cos(th), math.sin(th)
            corners = [(cx + c * u - sn * v, cy + sn * u + c * v) for u, v in ((-L / 2, -W / 2), (L / 2, -W / 2), (L / 2, W / 2), (-L / 2, W / 2))]
            ring = [tuple(map(float, P.from_units(x, y))) for x, y in corners]
            out.append((dict(gar[g[0]][0], building='garages'), ring + [ring[0]]))
        self.stats['garage_rows'] = sum(1 for g in groups.values() if len(g) > 1)
        return out

    def _with_stack(self, fl):
        """floors of stacked pieces + the extra layers the player added to this building in the editor (stack)"""
        return min(6, fl + int(getattr(self, '_cur_stack', 0) or 0))

    def _apt_row(self, pts, hm, placed):
        """A block of flats as ONE continuous building: one model repeated in a straight row, touching, along the
        whole footprint (a stepped OSM outline is taken as its bounding rectangle).
          7+ floors: squarish -> ONE CivilianHighrise02, long -> CivilianHighrise01 side by side
          up to 6 floors: entrances ~ length / 12.5 m; odd -> that many AsiaApartment02 (one entrance each),
          even -> half as many AsianApartmentSingle01 (two entrances each)."""
        r = geo.min_rect(pts)
        if not r: return False
        cx, cy, L, W, th = r
        fill = abs(geo.poly_area(pts)) / max(1.0, L * W) if hasattr(geo, 'poly_area') else 1.0
        if fill < 0.6: return False                      # L / U shapes: the old piece-by-piece filling
        floors = int(round((hm - 1) / 3)) if hm else 5
        have = lambda n: n in self.cat.buildings
        if floors >= 7 and L / max(W, 1e-3) < 1.8 and have('CivilianHighrise02'):
            name, n = 'CivilianHighrise02', 1
        else:                                            # low, or a long tall block = the same pieces stacked
            if not (have(PANEL_TOWER) and have('AsianApartmentSingle01')): return False
            ent = max(1, round(L / (12.5 * self.p.upm)))
            name, n = (PANEL_TOWER, ent) if ent % 2 else ('AsianApartmentSingle01', ent // 2)
        xe, ye, mh = self.cat.buildings[name]
        ml = max(xe, ye)
        mang = th - (0 if xe >= ye else math.pi / 2)
        ux, uy = math.cos(th), math.sin(th)
        pieces = []
        for i in range(n):
            oa = (i - (n - 1) / 2) * ml * 0.9                # a little overlap: the models have empty margins, so
                                                             # touching footprints still showed a gap in the game
            pieces.append((cx + ux * oa, cy + uy * oa))
        ok = [p for p in pieces if self.inside(p[0], p[1], 5) and
              self.occ_test(self.rect(p[0], p[1], xe * 0.6, ye * 0.6, mang)) <= 0.45]
        if len(ok) < len(pieces): return False          # all or nothing: never a building with holes
        self._apt_row_n = 0
        ent_n = n * (2 if name == 'AsianApartmentSingle01' else 1) if name != 'CivilianHighrise02' else 1
        self.apt_rows.append((cx, cy, n * ml * 0.9 + ml * 0.1, max(min(xe, ye), W), th, ent_n))
        for x, y in pieces:
            poly = self.rect(x, y, xe, ye, mang)
            self.occ_poly(poly, True)
            uid = f'EARTH_Building_{placed + self._apt_row_n + 1}'
            self.bld_rects.append((poly, name)); self.bld_klass.append('apt')
            self.add_obj(name, x, y, mang, 0, uid, self.base_props)
            self.bld_objs.append((uid, name, x, y, poly, 'apt'))
            self._apt_row_n += 1
            if hm and name in STACKABLE and mh > 15:
                fl = self._with_stack(max(1, min(3, round(hm * self.p.upm / mh))))
                for f in range(1, fl):
                    self.objs.append(MapObject(3, float(x), float(y), float(mh * f * 0.97), float(mang % (2 * math.pi)),
                                               0, name, self.mkprops(f'{uid}_F{f + 1}', self.base_props)))
                    self._stacked += 1
        return True

    def _clear_houses(self, poly):
        """Remove the town's houses whose centre lies in poly (an insurgent base or its door lane takes the spot)."""
        gone = set()
        keep = []
        for b in self.bld_objs:
            if geo.point_in_poly(b[2], b[3], poly): gone.add(b[0])
            else: keep.append(b)
        if not gone: return 0
        self.bld_objs = keep
        polys = {id(b[4]) for b in keep}
        self.bld_rects = [(pg, n) for pg, n in self.bld_rects if id(pg) in polys or not geo.point_in_poly(
            sum(p[0] for p in pg) / len(pg), sum(p[1] for p in pg) / len(pg), poly)]
        def uid_of(o):
            for k, v in o.props:
                if isinstance(v, str) and v.startswith('EARTH_Building_'): return v.split('_F')[0]
            return None
        self.objs = [o for o in self.objs if uid_of(o) not in gone]
        self.stats['houses_cleared_for_bases'] = self.stats.get('houses_cleared_for_bases', 0) + len(gone)
        return len(gone)

    def _vary(self, opts, hm, placed):
        """Dense city: the usual pieces plus more city buildings of a similar height, in an order of its own for
        every building - so a street is not one model repeated."""
        if not getattr(self, 'dense', False) or opts in (GARAGE_PIECES, FLAT_PIECES):
            return opts
        pool = list(dict.fromkeys(list(opts) + (DENSE_TALL if hm and hm >= 6 * 3.0 + 1 else DENSE_MID)))
        if hm:                                   # never much taller than the real building
            pool = [n for n in pool if n in opts or self.cat.buildings.get(n, (0, 0, 999))[2] <= hm * self.p.upm * 1.35]
        random.Random(placed * 7919 + int(self.opt.get('seed', 1))).shuffle(pool)
        return pool

    def _fill_building(self, klass, pts, holes, th, hm, area_m2, placed, forced=None):
        """Lay model pieces over the real footprint; pieces of one building may overlap each other."""
        have = lambda n: n in self.cat.buildings
        smallest = min((min(self.cat.buildings[n][:2]) for n in FLAT_PIECES + PANEL_BLOCK if have(n)), default=30)
        if klass == 'apt' and not forced and self._apt_row(pts, hm, placed): return self._apt_row_n
        rects = self.footprint_rects(pts, holes, th, smallest)
        if not rects: return 0
        pieces = []
        for ri, (rx, ry, a, b, ang) in enumerate(rects):
            square = a / max(b, 1e-3) < 1.45
            opts = forced or self._vary(self._piece_options(klass, hm, square, a, b, area_m2), hm, placed)
            for name in opts:
                if not have(name): continue
                xe, ye, mh = self.cat.buildings[name]
                ml, ms = max(xe, ye), min(xe, ye)
                if not forced and (a < ml * 0.6 or b < ms * 0.55): continue   # this part is too small for the piece
                if forced and ri > 0 and (a < ml * 0.4 or b < ms * 0.4): continue
                k = max(1, math.ceil(a / ml - 0.3))                   # pieces along the length (overlap > gaps)
                rows = max(1, math.ceil(b / ms - 0.35))               # and across a deep part
                if (name == PANEL_TOWER and square and a < ml * 1.75) or \
                        (name == 'CivilianHighrise02' and a / max(b, 1e-3) < 1.8 and a < ml * 2.2):
                    k = rows = 1                                      # a square block / tower stays ONE building
                if name in GARAGE_PIECES:                            # garages: side by side, touching, no overlap
                    k, rows = max(1, round(a / ml)), max(1, round(b / ms))
                if k * rows > 40: continue
                ux, uy, vx, vy = math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang)
                stp_a = (a - ml) / (k - 1) if k > 1 else 0
                stp_b = (b - ms) / (rows - 1) if rows > 1 else 0
                if name in GARAGE_PIECES:
                    stp_a, stp_b = ml * 0.99, ms * 0.99
                    a, b = ml + stp_a * (k - 1), ms + stp_b * (rows - 1)   # the row is centred on the footprint
                mang = ang - (0 if xe >= ye else math.pi / 2)
                for i in range(k):
                    for j in range(rows):
                        oa = -(a - ml) / 2 + i * stp_a if k > 1 else 0.0
                        ob = -(b - ms) / 2 + j * stp_b if rows > 1 else 0.0
                        pieces.append((name, rx + ux * oa + vx * ob, ry + uy * oa + vy * ob, mang, xe, ye, mh))
                break
        if not pieces: return 0
        # test against roads / other buildings before any piece of this building is marked
        gz = klass == 'garage'      # a garage model is deeper than a real garage: it may reach over the driveway
        ok = [pc for pc in pieces if self.inside(pc[1], pc[2], 5) and
              self.occ_test(self.rect(pc[1], pc[2], pc[4] * (0.6 if gz else 0.85), pc[5] * (0.6 if gz else 0.85), pc[3]))
              <= (0.5 if gz else 0.2)]
        if len(ok) < max(1, len(pieces) * 0.5): return 0
        n = 0
        for name, x, y, mang, xe, ye, mh in ok:
            poly = self.rect(x, y, xe, ye, mang)
            self.occ_poly(poly, True)
            uid = f'EARTH_Building_{placed + n + 1}'
            self.bld_rects.append((poly, name)); self.bld_klass.append(klass)
            self.add_obj(name, x, y, mang, 0, uid, self.base_props)
            self.bld_objs.append((uid, name, x, y, poly, klass))
            n += 1
            if self.stack_tall and hm and name in STACKABLE and mh > 15:
                floors = self._with_stack(max(1, min(3, round(hm * self.p.upm / mh))))
                for f in range(1, floors):
                    self.objs.append(MapObject(3, float(x), float(y), float(mh * f * 0.97), float(mang % (2 * math.pi)),
                                               0, name, self.mkprops(f'{uid}_F{f + 1}', self.base_props)))
                    self._stacked += 1
        return n

    def _fits(self, name, L, Wd):
        """A model may be at most ~2x the real footprint (long blocks built from sections are always fine)."""
        xe, ye, _ = self.cat.buildings[name]
        a, b = max(xe, ye), min(xe, ye)
        if L > 1.25 * a and L / max(Wd, 1e-3) >= 2.2:
            return b <= Wd * 1.8
        return a * b <= 2.0 * L * Wd and a <= 1.6 * L

    # ----------------------------------------------------------- fences, walls, hedges
    def place_fences(self):
        P, A, R = self.p, self.AREA, self.rng
        cap = int(self.opt['max_fences'])
        n, by = 0, {}
        lines = sorted(self.osm.barriers, key=lambda b: b[0] == 'hedge')   # built fences first, hedges last
        for kind, line in lines:
            if n >= cap: break
            opts = [(m, ln) for m, ln in FENCES.get(kind, []) if not self.known_objects or m in self.known_objects]
            if not opts: continue
            model, ln = R.choice(opts)
            pts = [P.to_units(*pt) for pt in line]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                seg = math.hypot(x1 - x0, y1 - y0)
                if seg < ln * 0.6: continue
                k = max(1, round(seg / (ln * 0.97)))
                ang = math.atan2(y1 - y0, x1 - x0)
                for i in range(k):
                    if n >= cap: break
                    t = (i + 0.5) / k
                    x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                    if not self.inside(x, y, 20): continue
                    poly = self.rect(x, y, ln * 0.8, 5, ang)
                    if self.occ_test(poly) > 0.3: continue      # road, building, water or a base
                    self.occ_poly(self.rect(x, y, ln, 6, ang), True)
                    self.add_obj(model, x, y, ang, 0, f'EARTH_Fence_{n + 1}', self.base_props)
                    n += 1
                    by[kind] = by.get(kind, 0) + 1
        self.stats['fences'] = n
        self.stats['fences_by_kind'] = by

    def place_fuel(self):
        """Petrol stations from OSM: GasStation model + a short asphalt access road to the nearest road."""
        name = next((n for n in ('GasStation', 'GasStation03', 'AmericanGasStation') if n in self.cat.buildings), None)
        n = 0
        if not name:
            self.stats['gas_stations'] = 0; return
        xe, ye, _ = self.cat.buildings[name]
        for lat, lon in self.osm.fuel:
            x, y = self.p.to_units(lat, lon)
            if not self.inside(x, y, max(xe, ye)): continue
            nr = self._nearest_road(x, y) or self._nearest_road(x, y, tuple(ROAD_W))
            ang = nr[3] if nr else 0.0
            nx, ny = -math.sin(ang), math.cos(ang)
            # keep the station beside the road, never on it
            if nr and nr[0] < 400:
                d, px, py, _, rt = nr
                nx, ny = (x - px, y - py) if d > 1 else (-math.sin(ang), math.cos(ang))
                L = math.hypot(nx, ny) or 1; nx, ny = nx / L, ny / L
                want = self.road_w[rt] / 2 + min(xe, ye) / 2 + 12
                if d < want:
                    x, y = px + nx * want, py + ny * want
            poly = None
            for push in range(0, 120, 10):
                cx_, cy_ = (x + nx * push, y + ny * push) if nr else (x, y)
                cand = self.rect(cx_, cy_, xe, ye, ang)
                if self.occ_test(cand) <= 0.05:
                    poly, x, y = cand, cx_, cy_; break
            if poly is None: continue
            self.occ_poly(poly, True)
            self.bld_rects.append((poly, name)); self.bld_klass.append('fuel')
            n += 1
            self.add_obj(name, x, y, ang, 0, f'EARTH_GasStation_{n}', self.base_props)
        self.stats['gas_stations'] = n

    def build_buildings(self):
        P, s_boost = self.p, None
        # virtual size boost so houses stay readable at game scale
        # 1 unit = 0.25 m at 1:1 -> no boost; smaller scales get a mild boost so houses stay visible
        s = min(2.5, max(1.0, math.sqrt(self.p.mpc / 2.5)))
        self.stats['building_boost'] = round(s, 2)
        km2 = max(0.05, (self.AX / P.upm) * (self.AY / P.upm) / 1e6)
        built = 0.0
        for _t, ring in self.osm.buildings:
            r_ = geo.min_rect([P.to_units(*pt) for pt in ring[:-1]]) if len(ring) >= 4 else None
            if r_: built += r_[2] * r_[3]
        share = built / max(1.0, self.AX * self.AY)
        self.dense = len(self.osm.buildings) / km2 >= DENSE_PER_KM2 and share >= DENSE_SHARE
        if self.dense: self.stats['dense_city'] = f'{share * 100:.0f} % zastavané - viac druhov budov'
        cand = []
        # what is around / inside each building: shops, pubs, offices (OSM points) and the land use
        pois = [(k, *P.to_units(la, lo)) for k, la, lo in self.osm.pois]
        pgrid = {}
        for i, (k, x, y) in enumerate(pois):
            pgrid.setdefault((int(x // 200), int(y // 200)), []).append(i)
        lu_masks = {}
        for kind, ring in self.osm.landuse:
            if kind in ('industrial', 'farmyard', 'garages', 'allotments', 'retail', 'commercial', 'sports'):
                poly = geo.clip_poly([P.to_units(*pt) for pt in ring], self.AX, self.AY)
                if len(poly) >= 3:
                    m = self.raster_poly([poly])
                    lu_masks[kind] = lu_masks[kind] | m if kind in lu_masks else m

        def poi_in(cx, cy, L, Wd, th):
            c, sn = math.cos(th), math.sin(th)
            for gx in (int(cx // 200) - 1, int(cx // 200), int(cx // 200) + 1):
                for gy in (int(cy // 200) - 1, int(cy // 200), int(cy // 200) + 1):
                    for i in pgrid.get((gx, gy), ()):
                        k, x, y = pois[i]
                        dx, dy = x - cx, y - cy
                        if abs(dx * c + dy * sn) <= L / 2 + 8 and abs(-dx * sn + dy * c) <= Wd / 2 + 8:
                            return k
            return None

        def landuse_at(x, y):
            i, j = int(self.cx(y)), int(self.cx(x))
            for kind, m in lu_masks.items():
                if 0 <= i < m.shape[0] and 0 <= j < m.shape[1] and m[i, j]:
                    return kind
            return None

        why = {}
        for tags, ring in self._garage_rows(self.osm.buildings):
            if tags.get('building') == 'silo' or tags.get('man_made') == 'silo':
                continue                                  # silos: OilRefinery complexes, see place_silos
            pts = [P.to_units(*pt) for pt in ring[:-1]]
            holes = [[P.to_units(*pt) for pt in h[:-1]] for h in tags.get('_holes', ())]
            oid = tags.get('_osm', '')
            ov = (self.opt['building_overrides'] or {}).get(oid)
            if ov and ov.get('kind') == 'none':
                continue                                  # removed in the editor
            r = geo.min_rect(pts)
            if not r: continue
            cx, cy, L, Wd, th = r
            if not self.inside(cx, cy, 5): continue
            L, Wd = L * s, Wd * s
            if L * Wd < 60: continue
            kl = classify(tags)
            hm = real_height_m(tags)
            # blocks of flats are often mapped as plain building=yes: big, deep footprints or 4+ storeys -> flats
            area_m2 = (L / s) * (Wd / s) / (self.p.upm ** 2)
            depth_m = (Wd / s) / self.p.upm
            hall_ok = False
            if kl == 'house':
                b = tags.get('building', 'yes')
                big = (area_m2 > 260 and depth_m > 9) or hm >= 4 * 3.0 + 1
                # use from OSM: the building's own shop/amenity tags, or a shop/pub/office point inside it
                pk = poi_kind(tags) or poi_in(cx, cy, L / s, Wd / s, th)
                lu = landuse_at(cx, cy) if b == 'yes' else None
                if lu == 'sports' and area_m2 > 300:
                    kl = 'sporthall'; why['sporthall'] = why.get('sporthall', 0) + 1
                elif pk == 'hospital':
                    kl = 'hospital'; why['hospital'] = why.get('hospital', 0) + 1
                elif lu == 'industrial' or (lu in ('farmyard', 'garages', 'allotments') and hm < 4 * 3.0 + 1):
                    kl = {'industrial': 'industrial' if area_m2 > 120 else 'shed',
                          'farmyard': ('farmhall' if area_m2 > 300 else 'farm') if area_m2 > 70 else 'shed'}.get(lu, 'shed')
                    why['landuse_' + lu] = why.get('landuse_' + lu, 0) + 1
                elif big:
                    # a block of flats stays one even with a grocery or pub on the ground floor
                    kl = 'apt'
                    if pk in ('civic', 'church', 'hospital'):
                        kl = pk
                    elif lu in ('retail', 'commercial') and hm < 4 * 3.0 + 1:
                        kl = 'shop'; hall_ok = True  # shopping centre / supermarket hall
                    if kl != 'apt': why[kl] = why.get(kl, 0) + 1
                elif pk:
                    kl = pk; why[pk] = why.get(pk, 0) + 1
            roof = str(tags.get('roof:shape', '')).lower()
            # shops, offices, flat roofs: plain flat-roofed buildings, not half-timbered pubs
            # a hall only for a real supermarket / shopping centre; offices and city shops are city buildings
            if getattr(self, 'dense', False) and (tags.get('shop') == 'mall' or (kl == 'shop' and area_m2 >= 4000)):
                kl = 'mall'                                  # big city shopping centre: a mega mall / hotel
            elif kl == 'shop' and not (area_m2 >= 900 and (hall_ok or tags.get('building') in ('retail', 'supermarket'))):
                kl = 'flat'
            elif kl == 'pub' and roof == 'flat':
                kl = 'flat'
            if kl == 'farm' and area_m2 > 300:
                kl = 'farmhall'                       # cowsheds, barns, stores of a farm: big halls
            if kl == 'civic' and (tags.get('amenity') in ('hospital', 'clinic') or tags.get('building') == 'hospital'
                                  or tags.get('healthcare') in ('hospital', 'clinic', 'centre')):
                kl = 'hospital'
            elif kl == 'civic' and area_m2 < 250:
                kl = 'flat'
            elif kl == 'house' and roof == 'flat' and area_m2 > 100:
                kl = 'flat'
            if tags.get('building') == 'train_station' or tags.get('railway') == 'station':
                kl = 'flat'
            if tags.get('building') in ('garage', 'garages') or (kl == 'shed' and landuse_at(cx, cy) == 'garages'):
                kl = 'garage'
            forced = None
            if ov:
                if ov.get('kind') in EDIT_KINDS:
                    kl, forced = EDIT_KINDS[ov['kind']]
                    if ov['kind'] == 'apt_low' and not ov.get('levels'): hm = 5 * 3.0 + 1.0
                    if ov['kind'] == 'apt_high' and not ov.get('levels'): hm = 8 * 3.0 + 1.0
                if ov.get('levels'):
                    hm = int(ov['levels']) * 3.0 + 1.0
            if not hm and kl == 'apt':
                # no floor count in OSM: a short block of one section (a 'bodovka', up to ~22 m) is a tower of
                # 8 floors, longer blocks are the usual lower ones (Poltar: Sklarska Z/H/G/F = towers)
                Lm, Wm = (L / s) / self.p.upm, (Wd / s) / self.p.upm
                hm = (8 * 3.0 + 1.0) if (Lm <= 22 and Wm >= 9) else self.apt_levels * 3.0 + 1.0
            if s != 1.0:
                pts = [(cx + (x - cx) * s, cy + (y - cy) * s) for x, y in pts]
                holes = [[(cx + (x - cx) * s, cy + (y - cy) * s) for x, y in h] for h in holes]
            cand.append((L * Wd, cx, cy, L, Wd, th, kl, hm, pts, holes, area_m2, oid, forced))
        cand.sort(reverse=True)
        self.stats['building_use_from_osm'] = why
        placed = 0
        stacked = 0
        by_class = {}
        self._stacked = 0
        self._cur_stack = 0
        self._hospitals = []
        self.apt_rows = []
        for area, cx, cy, L, Wd, th, klass, hm, pts, holes, area_m2, oid, forced in cand:
            if placed >= MAX_BUILDINGS: break
            info = dict(id=oid, pts=[(round(x, 1), round(y, 1)) for x, y in pts], kind=klass, models=[],
                        floors=int(round((hm - 1) / 3)) if hm else 0, edited=bool(forced))
            info['_n0'], info['_hm'] = len(self.bld_objs), hm
            self._cur_stack = int(((self.opt['building_overrides'] or {}).get(oid) or {}).get('stack', 0) or 0)
            info['stack'] = self._cur_stack
            self.bld_info.append(info)
            if klass in FILL_CLASSES:
                n0 = len(self.bld_objs)
                n = self._fill_building(klass, pts, holes, th, hm, area_m2, placed, forced)
                if n:
                    info['models'] = sorted({b[1] for b in self.bld_objs[n0:]})
                    placed += n; by_class[klass] = by_class.get(klass, 0) + 1
                    continue
                if klass == 'garage' and 'MogadishuGarage' in self.cat.buildings:
                    klass = 'garage_one'                             # too small for a row: one garage, not a shack
                else:
                    klass = 'house'                                  # nothing fitted: an ordinary building
            # special buildings must roughly fit their footprint, otherwise use the ordinary fallback
            for k_try in [klass] + FALLBACK.get(klass, []):
                models = [n for n in self.rank_models(k_try, L, Wd, hm)[:4] if self._fits(n, L, Wd)] \
                    if k_try in FALLBACK else self.rank_models(k_try, L, Wd, hm)[:4]
                if models: break
            klass = k_try
            if klass == 'garage_one': models = ['MogadishuGarage']
            if klass == 'hospital':
                if any(math.hypot(cx - hx, cy - hy) < 600 for hx, hy in self._hospitals): klass, models = 'civic', \
                    [n for n in self.rank_models('civic', L, Wd, hm)[:4] if self._fits(n, L, Wd)] or self.rank_models('house', L, Wd, hm)[:4]
                else: self._hospitals.append((cx, cy))
            if klass == 'house' and area < 700: klass = 'shed'; models = self.rank_models('shed', L, Wd, hm)[:4]
            if klass == 'shed' and not self.opt['sheds']: continue
            if self.opt['building_density'] < 1.0 and klass in ('house', 'shed', 'farm') and \
                    self.rng.random() > self.opt['building_density']: continue
            n_before = placed
            for name in models:
                xe, ye, _ = self.cat.buildings[name]
                long_is_x = xe >= ye
                mlong = max(xe, ye)
                k = 1
                if L > 1.25 * mlong and klass not in ('hospital', 'garage_one', 'sporthall'):
                    # long block (e.g. an 8-entrance panelak): identical sections joined end to end, no gaps
                    k = min(10, max(2, round(L / (mlong * 0.98))))
                ang = th - (0 if long_is_x else math.pi / 2)
                step = mlong * 0.98 if k > 1 else 0.0     # sections touch end to end, no gap and no overlap
                spots = []
                for i in range(k):
                    off = (i - (k - 1) / 2) * step
                    x, y = cx + off * math.cos(th), cy + off * math.sin(th)
                    if k > 1:   # shrink the test box along the block so neighbouring sections never collide
                        sx_, sy_ = (step * 0.9, ye * 0.9) if long_is_x else (xe * 0.9, step * 0.9)
                        poly = self.rect(x, y, sx_, sy_, ang)
                    else:
                        poly = self.rect(x, y, xe * 0.9, ye * 0.9, ang)
                    if klass == 'garage_one':      # one garage: may reach over its driveway like the rows do
                        test = self.rect(x, y, xe * 0.6, ye * 0.6, ang)
                        if self.inside(x, y, 5) and self.occ_test(test) <= 0.5: spots.append((x, y, poly))
                        continue
                    if self.inside(x, y, 5) and self.occ_test(poly) <= 0.15:
                        spots.append((x, y, poly))
                if not spots or (k > 1 and len(spots) < k * 0.75): continue
                for x, y, poly in spots:
                    self.occ_poly(poly, True)
                    self.bld_rects.append((poly, name))
                    self.bld_klass.append(klass)
                    self.add_obj(name, x, y, ang, 0, f'EARTH_Building_{placed + 1}', self.base_props)
                    self.bld_objs.append((f'EARTH_Building_{placed + 1}', name, x, y, poly, klass))
                    placed += 1
                    # tall building: the same model stacked on top (z is height above the ground)
                    mh = self.cat.buildings[name][2]
                    if self.stack_tall and hm and name in STACKABLE and mh > 15:
                        floors = self._with_stack(max(1, min(3, round(hm * self.p.upm / mh))))
                        for f in range(1, floors):
                            self.objs.append(MapObject(3, float(x), float(y), float(mh * f * 0.97),
                                                       float(ang % (2 * math.pi)), 0, name,
                                                       self.mkprops(f'EARTH_Building_{placed}_F{f + 1}', self.base_props)))
                            stacked += 1
                break
            if placed > n_before:
                by_class[klass] = by_class.get(klass, 0) + 1
                info['models'] = [self.bld_objs[-1][1]]; info['kind'] = klass
        self._cur_stack = 0
        # for the editor's 2D preview: the model outlines, the floors they show in the game, the roof, the fit
        ends = [i['_n0'] for i in self.bld_info[1:]] + [len(self.bld_objs)]
        for info, end in zip(self.bld_info, ends):
            objs = self.bld_objs[info.pop('_n0'):end]; hm_ = info.pop('_hm') or 0
            info['polys'] = [[(round(x, 1), round(y, 1)) for x, y in o[4]] for o in objs]
            top = 0.0
            for o in objs:
                mh = self.cat.buildings.get(o[1], (0, 0, 0))[2]
                k = max(1, min(3, round(hm_ * self.p.upm / mh))) if (self.stack_tall and hm_ and o[1] in STACKABLE and mh > 15) else 1
                if k > 1 or (self.stack_tall and hm_ and o[1] in STACKABLE and mh > 15): k = min(6, k + info.get('stack', 0))
                top = max(top, mh * k)
            info['game_floors'] = int(round(top / self.p.upm / 3.0)) if top else 0
            info['roof'] = 'pitched' if info['kind'] in ('house', 'pub', 'farm', 'shed', 'church') else 'flat'
            fa = abs(geo.poly_area(info['pts'])) or 1.0
            info['fit'] = round(sum(abs(geo.poly_area(pg)) for pg in info['polys']) / fa, 2) if objs else 0.0
        self.stats['buildings'] = placed
        self.stats['buildings_by_use'] = by_class
        self.stats['stacked_floors'] = stacked + self._stacked
        self.stats['buildings_candidates'] = len(cand)

    def place_silos(self):
        """Farm cooperative silos (OSM building=silo / man_made=silo): one OilRefinery per group of silos."""
        model = 'OilRefinery'
        self.stats['silos'] = 0
        if model not in self.cat.buildings: return
        P = self.p
        spots = [P.to_units(la, lo) for k, la, lo in self.osm.points if k == 'silo']
        for tags, ring in self.osm.buildings:
            if tags.get('building') == 'silo' or tags.get('man_made') == 'silo':
                spots.append(P.to_units(sum(p[0] for p in ring[:-1]) / (len(ring) - 1),
                                        sum(p[1] for p in ring[:-1]) / (len(ring) - 1)))
        groups = []
        near = 60 * self.p.upm                            # silos closer than ~60 m form one group
        for x, y in spots:
            for g in groups:
                if math.hypot(g[0] / g[2] - x, g[1] / g[2] - y) < near:
                    g[0] += x; g[1] += y; g[2] += 1; break
            else:
                groups.append([x, y, 1])
        xe, ye, _ = self.cat.buildings[model]
        n = 0
        for gx, gy, k in groups:
            x, y = gx / k, gy / k
            if not self.inside(x, y, max(xe, ye)): continue
            for ang in (0.0, math.pi / 2, math.pi / 4):
                poly = self.rect(x, y, xe, ye, ang)
                if self.occ_test(self.rect(x, y, xe * 0.85, ye * 0.85, ang)) <= 0.25:
                    self.occ_poly(poly, True)
                    self.bld_rects.append((poly, model)); self.bld_klass.append('farm')
                    self.add_obj(model, x, y, ang, 0, f'EARTH_Silo_{n + 1}', self.base_props)
                    n += 1
                    break
        self.stats['silos'] = n

    def place_pitches(self):
        """Football pitches: two goals, a stand on one long side, field texture."""
        P, A = self.p, self.AREA
        goal = 'SoccerStadiumGoal'; stand = 'SoccerStadiumSideBleachers'
        self.pitch_polys = []
        n = 0
        for sport, ring in self.osm.pitches:
            if sport not in ('soccer', 'football', ''): continue
            pts = [P.to_units(*pt) for pt in ring[:-1]]
            r = geo.min_rect(pts)
            if not r: continue
            cx, cy, L, Wd, th = r
            if not self.inside(cx, cy, 20) or L < 60: continue
            if sport == '' and L < 150: continue          # tiny unnamed pitch: probably not football
            poly = self.rect(cx, cy, L, Wd, th)
            self.pitch_polys.append(poly)
            ux, uy = math.cos(th), math.sin(th)
            if goal in self.known_objects:
                for sgn in (1, -1):
                    gx, gy = cx + sgn * ux * (L / 2 - 6), cy + sgn * uy * (L / 2 - 6)
                    # the goal is 8 m wide along its local y and opens along local +x: turn it so the width lies
                    # across the pitch and both goals face each other on the centre line
                    self.add_obj(goal, gx, gy, th + (math.pi if sgn > 0 else 0.0), 0,
                                 f'EARTH_Goal_{n}_{sgn}', self.base_props)
            if False and stand in self.cat.buildings and L > 200:      # no stadium stands - they do not fit a town pitch
                xe, ye, _ = self.cat.buildings[stand]
                vx, vy = -uy, ux
                off = Wd / 2 + min(xe, ye) / 2 + 6
                sx, sy = cx + vx * off, cy + vy * off
                sp = self.rect(sx, sy, xe, ye, th + (0 if xe >= ye else math.pi / 2))
                if self.inside(sx, sy, 20) and self.occ_test(sp) < 0.05:
                    self.occ_poly(sp, True); self.bld_rects.append((sp, stand)); self.bld_klass.append('stand')
                    self.add_obj(stand, sx, sy, th + (0 if xe >= ye else math.pi / 2), 0, f'EARTH_Stand_{n}', self.base_props)
            self.occ_poly(poly, True)
            n += 1
        self.stats['football_pitches'] = n
