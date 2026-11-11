"""Terrain and water: heights, lakes, rivers."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class TerrainMixin:
    # ----------------------------------------------------------- terrain
    def build_terrain(self):
        B = BORDER
        ux, uy = np.meshgrid((np.arange(self.WX) - B + 0.5) * CELL, (np.arange(self.WY) - B + 0.5) * CELL)
        lat, lon = self.p.from_units(ux, uy)
        E = np.maximum(np.asarray(self.elev_fn(lat, lon), float), -1.0)
        E = geo.blur(E, int(self.opt['smooth'])) if int(self.opt['smooth']) > 0 else E
        lo_, hi_ = float(np.percentile(E, 0.5)), float(np.percentile(E, 99.5))
        rel_m = max(hi_ - lo_, 0.5)
        rel_u = rel_m * self.p.upm                 # real relief in game units
        RANGE = 255 * HS - BASE_H - 1              # all 0..255 height steps of the map format, minus the water reserve
        x = (E - lo_) * self.p.upm                 # true-scale height above the lowest point
        if self.terrain_mode == 'slopes':
            # "real slopes": local landforms (hills, banks, valleys) at true 1:1 height, only the slow
            # map-wide rise is compressed so everything fits the 8-bit height range of the map format
            r = max(4, int(900.0 / self.p.mpc))                     # ~900 m window: hills smaller than this stay 1:1
            # odd reflection at the map edges keeps a steady slope steady (no fake bumps at the border)
            Ep = np.pad(E, 2 * r, mode='reflect', reflect_type='odd')
            low = geo.box_mean(geo.box_mean(Ep, r), r)[2 * r:-2 * r, 2 * r:-2 * r]
            det = (E - low) * self.p.upm
            d0, d1 = float(det.min()), float(det.max())             # keep hill tops, they are tiny areas
            dspan = max(d1 - d0, 1e-3)
            sd = min(1.0, RANGE * 0.9 / dspan)                     # local detail stays 1:1 unless it alone is too high
            lowu = (low - low.min()) * self.p.upm
            lspan = max(float(lowu.max()), 1e-3)
            dpart = (det - d0) * sd
            # the largest share of the regional rise that still fits all 0..255 steps (binary search)
            lo_k, hi_k = 0.0, 1.0
            for _ in range(18):
                mid = (lo_k + hi_k) / 2
                if float((dpart + lowu * mid).max()) <= RANGE: lo_k = mid
                else: hi_k = mid
            k = lo_k
            h = dpart + lowu * k
            self.H = BASE_H + np.clip(h, 0, RANGE)
            self.ex = sd
            self.stats.update(local_vertical_scale=round(sd, 2), regional_vertical_scale=round(k, 2))
        elif self.terrain_mode == 'real':
            # true vertical scale, peaks softly squeezed into the limit
            self.H = BASE_H + RANGE * np.tanh(np.maximum(x, 0) / RANGE)
            self.ex = 1.0
        else:
            # use the full range; 'strong' also boosts gentle terrain up to 3x
            self.ex = min(RANGE / rel_u, float(self.opt['terrain_strength']) if self.terrain_mode == 'strong' else 1.0)
            self.H = BASE_H + np.clip(x * self.ex, 0, RANGE)
        self.stats.update(relief_m=round(rel_m, 1), vertical_scale=round(self.ex, 2),
                          game_relief_m=round(float(self.H.max() - self.H.min()) / self.p.upm, 1), mpc=round(self.p.mpc, 2))

    # ----------------------------------------------------------- water
    def build_water(self):
        """Lakes: one flat water polygon at shore level, shallow bed.
        Rivers: written like World Builder / MapToPlay rivers (isRiver polygon = left bank + right bank),
        split into short pieces wherever the ground drops, so the water follows the valley without canyons."""
        WX, WY, P = self.WX, self.WY, self.p
        self.water_mask = np.zeros((WY, WX), bool)
        self.water_polys = []   # (poly, z, is_river, river_start)
        H0 = self.H.copy()      # terrain before any carving

        def hat(x, y):
            cx_, cy_ = min(max(int(self.cx(x)), 0), WX - 1), min(max(int(self.cx(y)), 0), WY - 1)
            return float(H0[cy_, cx_])

        def carve(mask, z, depth=3.0):
            # smooth bowl: deepest in the middle, rising gently to just above the water at the bank,
            # so the shoreline follows the terrain instead of square cell steps
            ys_, xs_ = np.nonzero(mask)
            if len(ys_) == 0: return
            y0, y1 = max(ys_.min() - 4, 0), min(ys_.max() + 5, WY); x0, x1 = max(xs_.min() - 4, 0), min(xs_.max() + 5, WX)
            sub = mask[y0:y1, x0:x1].astype(float)
            s = geo.blur(sub, 2)                               # 0 outside .. 1 deep inside
            target = z + 1.2 - (depth + 1.2) * np.clip(s * 1.6, 0, 1)
            near = s > 0.02
            Hs = self.H[y0:y1, x0:x1]
            Hs[near] = np.minimum(Hs[near], target[near])

        river_area_kinds = ('river', 'stream', 'canal', 'ditch', 'drain', 'riverbank')
        kinds = self.osm.water_kind if len(self.osm.water_kind) == len(self.osm.water) else ['lake'] * len(self.osm.water)
        has_river_area = False
        for ring, kind in zip(self.osm.water, kinds):
            if kind in river_area_kinds:
                has_river_area = True        # the river is drawn from its centre line instead (sloped water)
                continue
            poly = geo.clip_poly([P.to_units(*pt) for pt in ring], self.AX, self.AY)
            poly = geo.rdp_ring(poly, 6.0)
            if len(poly) < 3: continue
            m = self.raster_poly([poly])
            if m.sum() < 4: continue
            shore = (geo.box_mean(m, 1) > 0) & ~m
            z = float(np.percentile(H0[shore], 15)) - 0.5 if shore.any() else float(np.median(H0[m]))
            carve(m, z, 3.0)
            self.water_mask |= m
            self.water_polys.append((poly, z, False, 0))

        widths = {'river': 56.0 if has_river_area else 44.0, 'canal': 32.0, 'stream': 18.0}
        # the real banks: edges of the OSM river areas (riverbank polygons, also when cut by the map edge)
        banks = []
        for ring, kind in zip(self.osm.water, kinds):
            if kind in river_area_kinds:
                u = [P.to_units(*pt) for pt in ring]
                banks += [(a, b) for a, b in zip(u, u[1:])]
        BG = 200.0
        bgrid = {}
        for i, (a, b) in enumerate(banks):
            for gx in range(int(min(a[0], b[0]) // BG), int(max(a[0], b[0]) // BG) + 1):
                for gy in range(int(min(a[1], b[1]) // BG), int(max(a[1], b[1]) // BG) + 1):
                    bgrid.setdefault((gx, gy), []).append(i)
        max_half = 700.0 * P.upm                          # a river is at most ~1.4 km wide

        def bank_dist(px, py, nx, ny):
            """Distance along the ray (px,py)+t*(nx,ny) to the nearest bank edge, or None."""
            best, step = None, BG * 0.9
            t = 0.0
            seen = set()
            while t < max_half and best is None:
                x, y = px + nx * t, py + ny * t
                for gx in (int(x // BG) - 1, int(x // BG), int(x // BG) + 1):
                    for gy in (int(y // BG) - 1, int(y // BG), int(y // BG) + 1):
                        for i in bgrid.get((gx, gy), ()):
                            if i in seen: continue
                            seen.add(i)
                            (ax, ay), (bx, by) = banks[i]
                            ex, ey = bx - ax, by - ay
                            den = nx * ey - ny * ex
                            if abs(den) < 1e-9: continue
                            tt = ((ax - px) * ey - (ay - py) * ex) / den
                            uu = ((ax - px) * ny - (ay - py) * nx) / den
                            if 0 <= uu <= 1 and 0 < tt < max_half and (best is None or tt < best): best = tt
                t += step
            return best

        self.stats['river_banks'] = len(banks)
        wtags = self.osm.waterway_width if len(self.osm.waterway_width) == len(self.osm.waterways) else [0.0] * len(self.osm.waterways)
        for (kind, ring), wtag in zip(self.osm.waterways, wtags):
            wdt = max(widths.get(kind, 18.0), wtag * P.upm) if wtag else widths.get(kind, 18.0)
            line = geo.rdp([P.to_units(*pt) for pt in ring], 4.0)
            for run in geo.clip_polyline(line, self.AX, self.AY):
                # densify every ~12 units and read the ground along the river
                pts = [run[0]]
                for p_, q_ in zip(run[:-1], run[1:]):
                    L = math.hypot(q_[0] - p_[0], q_[1] - p_[1]); k = max(1, int(L / 12))
                    pts += [(p_[0] + (q_[0] - p_[0]) * t / k, p_[1] + (q_[1] - p_[1]) * t / k) for t in range(1, k + 1)]
                if len(pts) < 2: continue
                h = np.array([hat(x, y) for x, y in pts])
                # half widths left / right of the centre line, measured to the real banks where they exist
                half = np.full((len(pts), 2), wdt / 2)
                if banks and kind in ('river', 'canal'):
                    for i, (x, y) in enumerate(pts):
                        a_, b_ = pts[max(i - 1, 0)], pts[min(i + 1, len(pts) - 1)]
                        dx, dy = b_[0] - a_[0], b_[1] - a_[1]; L = math.hypot(dx, dy) or 1.0
                        nx, ny = -dy / L, dx / L
                        dl, dr = bank_dist(x, y, nx, ny), bank_dist(x, y, -nx, -ny)
                        if dl is not None and dr is not None and dl + dr > wdt * 0.8:
                            half[i] = (dl, dr)
                    k_ = 9                                  # smooth out islands, piers and odd bank pieces
                    pad = np.pad(half, ((k_, k_), (0, 0)), mode='edge')
                    half = np.array([np.median(pad[i:i + 2 * k_ + 1], axis=0) for i in range(len(pts))])
                    self.stats['river_width_max_m'] = max(self.stats.get('river_width_max_m', 0),
                                                          round(float(half.sum(1).max()) / P.upm))
                hw_of = {pts[i]: half[i] for i in range(len(pts))}
                # cut into pieces: a new piece whenever the ground changes by more than ~1 m (4 units)
                s = 0
                while s < len(pts) - 1:
                    e = s + 1
                    lo = hi = h[s]
                    while e < len(pts) - 1 and e - s < 60:
                        lo2, hi2 = min(lo, h[e + 1]), max(hi, h[e + 1])
                        if hi2 - lo2 > 4.0: break
                        lo, hi, e = lo2, hi2, e + 1
                    piece = pts[s:min(e + 2, len(pts))]          # overlap one step with the next piece
                    if len(piece) >= 2:
                        cl = geo.rdp(piece, 2.0)
                        if len(cl) < 2: cl = [piece[0], piece[-1]]
                        strip = self._river_strip(cl, [hw_of.get(p_, (wdt / 2, wdt / 2)) for p_ in cl])
                        strip = [(min(max(x, 0), self.AX), min(max(y, 0), self.AY)) for x, y in strip]
                        n_left = len(cl)
                        z = float(min(h[s:e + 1])) + 1.0
                        m = self.raster_poly([strip])
                        if m.any():
                            carve(m, z, 3.0)
                            self.water_mask |= m
                            self.water_polys.append((strip, z, True, n_left - 1))
                    s = e
        self.stats['water_polygons'] = len(self.water_polys)

    @staticmethod
    def _river_strip(cl, half):
        """Left bank forward + right bank back, each point at its own distance (left, right) from the centre line."""
        left, right = [], []
        for i, p in enumerate(cl):
            a, b = cl[max(i - 1, 0)], cl[min(i + 1, len(cl) - 1)]
            dx, dy = b[0] - a[0], b[1] - a[1]; L = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / L, dx / L
            dl, dr = half[i]
            left.append((p[0] + nx * dl, p[1] + ny * dl))
            right.append((p[0] - nx * dr, p[1] - ny * dr))
        return left + right[::-1]
