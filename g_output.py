"""Textures, blends, script areas and the preview image."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *


class OutputMixin:
    # ----------------------------------------------------------- textures
    def paint(self, classes):
        WX, WY = self.WX, self.WY
        cls = np.zeros((WY, WX), np.int16)
        names = {'grass': 'GrassMediumType1', 'forest': 'GrassMediumType11', 'dirt': 'DirtMediumType1',
                 'sand': 'SandMediumType1', 'rock': 'RocksMediumType1', 'conc': 'ConcreteType1',
                 'field': 'GrassMediumType38a', 'asphalt': 'AsphaltType1', 'cobble': 'CobblestoneType1',
                 'crops': 'SandType1darker', 'park_ns': 'ParkingLotType1c', 'park_ew': 'ParkingLotType1d'}
        idx = {k: next((i for i, c in enumerate(classes) if c[4] == v), 0) for k, v in names.items()}
        P, A = self.p, self.AREA
        cls[:] = idx['grass']
        farm = None
        for kind, ring in self.osm.landuse:
            poly = geo.clip_poly([P.to_units(*pt) for pt in ring], self.AX, self.AY)
            if len(poly) < 3: continue
            m = self.raster_poly([poly])
            if kind == 'farmland':
                farm = m if farm is None else (farm | m)
            if kind in ('farmland', 'meadow'): cls[m] = idx['field']
            elif kind in ('industrial', 'commercial', 'retail'): cls[m] = idx['conc']
        sat_img = self.opt.get('sat_img')
        if sat_img is not None:
            # ground textures from the real colours of the satellite image (Sentinel-2): meadow, field, soil,
            # built-up ground, sand - OSM land use stays underneath where the image says plain meadow
            try:
                import sat as satm
                ii, jj = np.mgrid[:WY, :WX]
                ux = (jj - BORDER + 0.5) * CELL; uy = (ii - BORDER + 0.5) * CELL
                lat, lon = P.from_units(ux, uy)
                rgb = satm.sample(sat_img, np.asarray(lat), np.asarray(lon))
                rgb = np.stack([geo.box_mean(rgb[..., c], 1) for c in range(3)], -1)   # no single-cell speckles
                k = satm.classify(rgb)
                inside = (jj >= BORDER) & (jj < WX - BORDER) & (ii >= BORDER) & (ii < WY - BORDER)
                # grey / brown outside the town is bare or harvested farmland, not concrete: ploughed field there
                town_ = self.raster_poly([pg for pg, _ in self.bld_rects]) if self.bld_rects else np.zeros((WY, WX), bool)
                town_ = geo.box_mean(town_.astype(float), max(2, int(40 / P.mpc))) > 0.02
                crops_ = 'crops' if any(c[4] == 'SandType1darker' for c in classes) else 'dirt'
                k = k.copy()
                k[(k == 4) & ~town_] = 6
                k[(k == 3) & ~town_] = 6
                for code, name in ((1, 'forest'), (2, 'field'), (3, crops_), (4, 'conc'), (5, 'sand'), (6, crops_)):
                    cls[(k == code) & inside] = idx[name]
                self.stats['satellite_ground'] = {n_: int(((k == c_) & inside).sum()) for c_, n_ in
                                                  ((0, 'meadow'), (1, 'dark green'), (2, 'field'), (3, 'soil'), (4, 'built'), (5, 'sand'))}
            except Exception as ex:
                self.stats['satellite_ground'] = f'chyba: {ex}'
        if farm is not None:                 # real fields (OSM farmland): ploughed brown soil, whatever the season photo says
            cls[farm] = idx['crops'] if any(c[4] == 'SandType1darker' for c in classes) else idx['dirt']
            self.stats['farmland_cells'] = int(farm.sum())
        cls[self.forest_mask] = idx['forest']
        gx, gy = np.gradient(self.H, CELL)
        slope = np.hypot(gx, gy)
        cls[slope > 0.45] = idx['rock']
        wd = geo.box_mean(self.water_mask, 1) > 0
        cls[wd] = idx['sand']
        # paint the ground under buildings and roads, so towns and roads show on the minimap (radar)
        img = Image.new('L', (WX, WY), 255); d = ImageDraw.Draw(img)
        for poly, name in self.bld_rects:
            cxp = sum(p[0] for p in poly) / 4; cyp = sum(p[1] for p in poly) / 4
            d.polygon([(self.cx(cxp + (x - cxp) * 1.25), self.cx(cyp + (y - cyp) * 1.25)) for x, y in poly], fill=idx['conc'])
        surface_only = UNPAVED_STREETS + ('DirtPath1', 'TrainTrack')
        all_roads = [(rt, run) for rt, run in self.road_lines if rt not in surface_only] + self.road_tex_only
        for rt, surface in ROAD_SURFACE.items():
            coverage = 0.4 if rt == 'DirtRoadTracks' else 0.75
            wpx = max(1, round(self.road_w[rt] * coverage / CELL))
            for t, run in all_roads:
                if t == rt:
                    d.line([(self.cx(x), self.cx(y)) for x, y in run], fill=idx[surface], width=wpx, joint='curve')
        marked_parking = getattr(self, 'marked_parking', set())
        for lot, (kind, poly) in enumerate(getattr(self, 'area_polys', [])):
            if kind == 'parking':
                surface = 'asphalt'
                if lot not in marked_parking:
                    # Ground-texture markings for lots too small for a ParkingLines strip.
                    r_ = geo.min_rect(poly)
                    th_ = (r_[4] % math.pi) if r_ else 0.0
                    key_ = 'park_ns' if th_ < math.pi / 4 or th_ > 3 * math.pi / 4 else 'park_ew'
                    if any(c[4] == names[key_] for c in classes):
                        surface = key_
                d.polygon([(self.cx(x), self.cx(y)) for x, y in poly], fill=idx[surface])
        for poly in getattr(self, 'pitch_polys', []):
            d.polygon([(self.cx(x), self.cx(y)) for x, y in poly], fill=idx['field'])
        paint = np.asarray(img).astype(np.int16)
        m = (paint != 255) & ~self.water_mask
        cls[m] = paint[m]
        ys, xs = np.mgrid[:WY, :WX]
        tiles = np.zeros((WY, WX), np.int32)
        for ci, c in enumerate(classes):
            m = cls == ci
            if m.any():
                tiles[m] = tile_index(c, xs[m], ys[m])     # World Builder's formula (4 sub-tiles per bitmap tile)
        self.cls, self.tiles = cls, tiles
        self.make_blends(classes)

    TEX_PRIO = {'GrassMediumType1': 0, 'GrassMediumType38a': 1, 'GrassMediumType11': 2, 'DirtMediumType1': 3,
                'SandMediumType1': 4, 'RocksMediumType1': 5, 'CliffMediumType1': 6, 'ConcreteType1': 7,
                'CobblestoneType1': 8, 'Cobble01': 8, 'WoodType1': 8, 'AsphaltType1': 9, 'SandType1darker': 3,
                'ParkingLotType1c': 9, 'ParkingLotType1d': 9}

    def make_blends(self, classes):
        """Soft edges between textures, the way World Builder's auto-blend does it: a cell next to a
        higher-priority texture gets that texture blended over it with an alpha gradient from that side."""
        WX, WY, cls = self.WX, self.WY, self.cls
        prio_of = np.array([self.TEX_PRIO.get(c[4], 0) for c in classes])
        P = prio_of[cls]
        best_p = P.copy(); best_d = np.full((WY, WX), -1, np.int8)
        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1)]
        nb_cls = np.zeros((WY, WX), cls.dtype)
        for di, (dx, dy) in enumerate(dirs):
            sh = np.full((WY, WX), -1, np.int64)
            ys0, ys1 = max(0, -dy), WY - max(0, dy); xs0, xs1 = max(0, -dx), WX - max(0, dx)
            sh[ys0:ys1, xs0:xs1] = cls[ys0 + dy:ys1 + dy, xs0 + dx:xs1 + dx]
            valid = sh >= 0
            pp = np.where(valid, prio_of[np.clip(sh, 0, None)], -1)
            better = pp > best_p                       # orthogonal directions come first and win ties
            best_p = np.where(better, pp, best_p); best_d = np.where(better, di, best_d)
            nb_cls = np.where(better, sh, nb_cls)
        sel = best_d >= 0
        ys, xs = np.nonzero(sel)
        d = best_d[sel]; nc = nb_cls[sel]
        bn = np.zeros(len(ys), np.int64)
        for ci, c in enumerate(classes):
            m = nc == ci
            if m.any(): bn[m] = tile_index(c, xs[m], ys[m])
        dx = np.array([v[0] for v in dirs])[d]; dy = np.array([v[1] for v in dirs])[d]
        kind = np.where(dy == 0, 0, np.where(dx == 0, 1, np.where(dx > 0, 2, 3)))   # horiz, vert, rdiag, ldiag
        inv = np.where(kind == 0, dx < 0, dy < 0).astype(np.int64)
        key = bn * 8 + kind * 2 + inv
        uniq, inverse = np.unique(key, return_inverse=True)
        if len(uniq) > 16000:                          # engine limit 16192 blend tiles: drop diagonals
            keep = kind < 2
            ys, xs, key = ys[keep], xs[keep], key[keep]
            uniq, inverse = np.unique(key, return_inverse=True)
        blend = np.zeros((WY, WX), np.int32)
        blend[ys, xs] = inverse + 1
        self.blend_arr = blend
        self.blend_list = []
        for k in uniq:
            b, kd, iv = int(k // 8), int((k % 8) // 2), int(k % 2)
            self.blend_list.append((b, int(kd == 0), int(kd == 1), int(kd == 2), int(kd == 3), iv, 0, -1))
        self.stats['texture_blends'] = len(self.blend_list)

    # ----------------------------------------------------------- triggers
    def add_area(self, name, cx, cy, r, n=16):
        """A script trigger area: a circle-like polygon kept inside the map."""
        poly = [(min(max(cx + r * math.cos(t), 1), self.AX - 1), min(max(cy + r * math.sin(t), 1), self.AY - 1))
                for t in (2 * math.pi * k / n for k in range(n))]
        self.area_triggers.append((name, poly))

    def build_script_areas(self):
        """InnerPerimeter<N> / OuterPerimeter<N> around every start: the skirmish AI scripts look these areas up
        ('[Skirmish]MyOuterPerimeter', '[Skirmish]EnemyOuterPerimeter') to defend and to attack - without them the AI
        only builds. The insurgents get number 13 and one area per base for their attack waves."""
        if self.opt['mode_clearance'] or self.opt['mode_zombie']:     # the whole map: counting what is left
            if self.opt['mode_zombie'] and self.opt['zombie_pacing'] == 'continuous':
                self.area_triggers.append(('EARTH_WholeMap', [(0, 0), (self.AX, 0), (self.AX, self.AY), (0, self.AY)]))
            else:
                self.add_area('EARTH_WholeMap', self.AX / 2, self.AY / 2, math.hypot(self.AX, self.AY) * 0.55)
        if self.zsurv:                              # the zombie AI's start: who starts here is not a person
            zx, zy = self.starts[-1]                 # in the border: no clamping into the map
            self.area_triggers.append(('EARTH_ZombieHome', [(zx - 500, zy - 300), (zx + 500, zy - 300),
                                                            (zx + 500, zy + 300), (zx - 500, zy + 300)]))
            # the arena = the playable area (the starts are outside it): a person with no unit here is down
            X, Y = self.AX, self.AY
            self.area_triggers.append(('EARTH_Arena', [(1, 1), (X - 1, 1), (X - 1, Y - 1), (1, Y - 1)]))
        if getattr(self, 'main_base', None):       # Clearance: the main base - no insurgent building left = won
            self.add_area('EARTH_InsMain', self.main_base[0], self.main_base[1], self.base_clear * 1.3)
        if getattr(self, 'silo', None):            # Defense: zombies this close go for the silo itself
            self.add_area('EARTH_SiloZone', self.silo[1], self.silo[2], 330)
        for o in getattr(self, 'objectives', []):
            self.add_area(o['area'], o['x'], o['y'], o['r'])
        for q in getattr(self, 'qrf', []):
            self.add_area(q['area'], q['x'], q['y'], q['r'])
        for i, (x, y) in enumerate(self.starts):
            self.add_area(f'InnerPerimeter{i + 1}', x, y, self.base_clear * 0.55)
            self.add_area(f'OuterPerimeter{i + 1}', x, y, self.base_clear * 1.1)
        holds = getattr(self, 'strongholds', [])
        if holds:
            hx, hy = holds[0]
            self.add_area(f'InnerPerimeter{INS_START + 1}', hx, hy, 450)
            self.add_area(f'OuterPerimeter{INS_START + 1}', hx, hy, 900)
        for k, (hx, hy) in enumerate(holds):
            self.add_area(f'EARTH_InsBase{k + 1}', hx, hy, 650)
        self.stats['script_areas'] = len(self.area_triggers)

    def dump_triggers(self):
        out = [struct.pack('<I', len(self.water_polys) + len(self.area_triggers))]
        for i, (poly, z, river, rstart) in enumerate(self.water_polys):
            out.append(_wstr(f'EARTH_Water_{i + 1}') + _wstr(''))
            out.append(struct.pack('<IBBII', i + 1, 1, 1 if river else 0, rstart if river else 0, len(poly)))
            for x, y in poly:
                out.append(struct.pack('<iii', int(x), int(y), int(round(z))))
        n0 = len(self.water_polys)
        for j, (name, poly) in enumerate(self.area_triggers):
            out.append(_wstr(name) + _wstr(''))
            out.append(struct.pack('<IBBII', n0 + j + 1, 0, 0, 0, len(poly)))
            for x, y in poly:
                c_, r_ = min(max(int(self.cx(x)), 0), self.WX - 1), min(max(int(self.cx(y)), 0), self.WY - 1)
                out.append(struct.pack('<iii', int(x), int(y), int(round(float(self.H[r_, c_])))))
        return b''.join(out)

    def _parse_classes(self, tail, hm):
        imp = ((hm.w + 7) // 8) * hm.h
        nb, nbl, ncl, ntc = struct.unpack_from('<IIII', tail, imp)
        q = imp + 16; classes = []; start = q
        for _ in range(ntc):
            first, num, wd, unk = struct.unpack_from('<IIII', tail, q); q += 16
            l = struct.unpack_from('<H', tail, q)[0]; q += 2
            nm = tail[q:q + l].decode(); q += l
            classes.append((first, num, wd, unk, nm))
        return {'classes': classes, 'classes_raw': tail[start:q],
                'head': struct.pack('<IIII', nb, 1, 1, ntc)}

    # ----------------------------------------------------------- preview
    def preview(self, clean=False):
        B = BORDER
        H = self.H
        gx, gy = np.gradient(H)
        shade = np.clip(0.75 + (-gx + gy) * 0.35, 0.3, 1.2)
        pal = {}
        base = np.zeros((self.WY, self.WX, 3), float)
        colors = [(104, 140, 70), (70, 110, 55), (150, 120, 80), (214, 196, 140), (130, 125, 115),
                  (110, 110, 110), (90, 90, 90), (150, 150, 150), (120, 105, 90), (130, 100, 60), (122, 150, 80), (130, 115, 95)]
        for i in range(len(colors)):
            base[self.cls == i] = colors[i % len(colors)]
        img = Image.fromarray(np.clip(base * shade[..., None], 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(img)
        wm = self.water_mask
        arr = np.asarray(img).copy(); arr[wm] = (60, 110, 190)
        img = Image.fromarray(arr); d = ImageDraw.Draw(img)
        road_colours = {'dirt': (150, 120, 80), 'cobble': (118, 107, 99), 'conc': (139, 139, 134), 'asphalt': (60, 60, 60)}
        for rt, run in self.road_lines + getattr(self, 'road_tex_only', []):
            d.line([(self.cx(x), self.cx(y)) for x, y in run], fill=road_colours[ROAD_SURFACE[rt]],
                   width=max(1, round(self.road_w[rt] / CELL)) if clean else 1, joint='curve')
        for poly, name in self.bld_rects:
            d.polygon([(self.cx(x), self.cx(y)) for x, y in poly], fill=(175, 165, 150) if clean else (200, 60, 50))
        if clean:
            return img.crop((BORDER, BORDER, BORDER + self.NX, BORDER + self.NY)).transpose(Image.FLIP_TOP_BOTTOM)
        for a, b in self.bridges:
            d.line([(self.cx(a[0]), self.cx(a[1])), (self.cx(b[0]), self.cx(b[1]))], fill=(255, 200, 0), width=4)
        h = self.base_clear / CELL / 2
        for x, y in () if self.battle_royale else self.starts:
            d.rectangle([self.cx(x) - h, self.cx(y) - h, self.cx(x) + h, self.cx(y) + h], outline=(255, 255, 0), width=2)
        for x, y in self.br_drops if self.battle_royale else self.starts:
            d.ellipse([self.cx(x) - 5, self.cx(y) - 5, self.cx(x) + 5, self.cx(y) + 5], outline=(255, 255, 0), width=2)
        if self.battle_royale:
            for _, _, (x, y) in self.br_vehicles:
                cx, cy = self.cx(x), self.cx(y)
                d.rectangle((cx - 2, cy - 2, cx + 2, cy + 2), fill='#46bde5')
        if self.invasion:
            x, y = map(self.cx, self.invasion_hq)
            d.ellipse((x - 6, y - 6, x + 6, y + 6), fill='#285da0', outline='white')
            for x, y in self.invasion_spawns:
                cx, cy = self.cx(x), self.cx(y)
                d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill='#b01010', outline='white')
        img = img.crop((B, B, B + self.NX, B + self.NY)).transpose(Image.FLIP_TOP_BOTTOM)
        return img
