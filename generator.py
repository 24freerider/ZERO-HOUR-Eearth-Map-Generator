"""Builds a Zero Hour .map from OSM + elevation data.

The generator is one class, Gen, assembled from topic modules (mixins) so that a change touches a small file:
  gconst.py      constants, model tables, small helpers          g_terrain.py    heights, water
  g_roads.py     roads, overpasses, railways                     g_buildings.py  the town
  g_bases.py     starts, bases, AI paths, patrols                g_nature.py     trees, details, life
  g_insurgents.py insurgents                                     g_zini.py       zombie map.ini texts
  g_zmode.py     zombie / clearance / survival / defense         g_output.py     textures, blends, scripts, preview
This file keeps the core helpers, run() (the order of the steps) and generate()."""
import math, random, struct
import numpy as np
from PIL import Image, ImageDraw

import geo
import preflight
from online import poi_kind
from geo import CELL
from mapio import MapFile, HeightMap, BlendTile, MapObject, _wstr, BlendTail, tile_index, Script, write_sides
from gconst import *
from g_terrain import TerrainMixin
from g_roads import RoadsMixin
from g_buildings import BuildingsMixin
from g_bases import BasesMixin
from g_nature import NatureMixin
from g_insurgents import InsurgentsMixin
from g_zini import ZombieIniMixin
from g_zmode import ZombieModesMixin
from g_invasion import InvasionMixin, INVASION_NUMBERS, INVASION_ROSTERS
from g_broyale import BattleRoyaleMixin
from g_brsettings import BR_SETTINGS
from g_output import OutputMixin


class Gen(TerrainMixin, RoadsMixin, BuildingsMixin, BasesMixin, NatureMixin, InsurgentsMixin,
          ZombieIniMixin, ZombieModesMixin, InvasionMixin, BattleRoyaleMixin, OutputMixin):
    def __init__(self, osm, elev_fn, proj, tiles, catalog, template_path, style=None, seed=1, log=print,
                 terrain_mode='strong', players=4, layout='auto', manual_starts=None, resources='normal',
                 stack_tall=True, apt_levels=4, **kw):
        self.osm, self.elev_fn, self.p, self.N = osm, elev_fn, proj, tiles
        # the map may be a north-south strip (Oblasť -> Tvar): NX x NY playable cells, WX x WY with the border
        self.NX, self.NY = int(tiles), int(getattr(proj, 'tiles_y', tiles) or tiles)
        for k in ('fuel', 'forest_leaf', 'pitches', 'trees', 'tree_rows', 'areas', 'points', 'water_kind',
                  'pois', 'barriers', 'waterway_width'):
            if not hasattr(osm, k): setattr(osm, k, [])
        self.cat, self.tpl_path, self.log = catalog, template_path, log
        self.WX, self.WY = self.NX + 2 * BORDER, self.NY + 2 * BORDER
        self.rng = random.Random(seed)
        self.AX, self.AY = self.NX * CELL, self.NY * CELL
        self.AREA = self.AX           # east-west size (kept for the editor); use AX / AY in new code
        self.terrain_mode = terrain_mode
        self.players = max(2, min(MAX_PLAYERS, int(players)))
        self.layout, self.manual_starts = (layout if layout not in ('zsurv', 'invasion', 'battle_royale') else 'auto'), manual_starts or []
        if self.layout == 'manual' and len(self.manual_starts) >= 2:
            self.players = min(MAX_PLAYERS, len(self.manual_starts))
        self.multi = False         # a game mode with several people (see below)
        self.resources, self.stack_tall = resources, stack_tall
        self.apt_levels = int(apt_levels)
        self.apt_model = kw.pop('apt_model', 'panel_mix') or 'panel_mix'
        o = dict(terrain_strength=3.0, smooth=1, base_size=900, oil_count=None, season='summer', time_of_day=2,
                 tree_density=1.0, max_trees=10000, bushes=True, willows=True, building_density=1.0, sheds=True,
                 lamps=True, details=True, nature_details=1.0, cars=True, ai_paths=True,
                 civilians=True, traffic=True, farm_life=True, sounds=True,
                 fortress=False, fortress_player=0, fortress_faction='GLA', fortress_level=2,
                 insurgents=False, ins_every=3, ins_max=4, ins_difficulty=2, outposts=0, civ_amount=1.5,
                 ins_cap=4000, patrols=6, foot_patrols=20, fences=True, max_fences=3000, train=True,
                 building_overrides=None, ins_bases=None, ins_zones=None, ins_prod_interval=120, footways=True,
                 ins_wave_size=5, ins_localized=False, ins_oil=4, mode_clearance=False, mode_zombie=False,
                 zombie_nests=6, zombie_start=10, zombie_interval=45, zombie_size=12, black_map=False, zombie_break=15,
                 ins_qrf=True, missions=False, mission_reward=5000, mode_zsurvival=False, zs_start_money=2000, zd_start_money=5000,
                 zs_respawn=60, zs_bounty=10, zombie_growing=False, zombie_growth=None, mode_zdefense=False,
                 zombie_rate=2, zombie_pacing='waves', zombie_live_limit=ZOMBIE_DEFAULT_LIVE_LIMIT,
                 mode_invasion=False, invasion_faction='China', invasion_air=True,
                 mode_battle_royale=False, compact=True, light=False)
        o.update({key: bounds[0] for key, bounds in INVASION_NUMBERS.items()})
        o.update({key: spec[0] for key, spec in BR_SETTINGS.items()})
        o.update({k: v for k, v in kw.items() if v is not None})
        if o['light']:
            # LIGHT = for the classic 32-bit game (GeneralsOnline / Steam): far fewer objects and units, roads kept
            # inside the stock limit; GeneralsX copes with the full map, the classic game runs out of memory / speed
            o.update(max_trees=min(int(o['max_trees']), 2500), tree_density=min(float(o['tree_density']), 0.6),
                     bushes=False, willows=False, fences=False, lamps=False, details=False, cars=False,
                     traffic=False, farm_life=False, sounds=False, footways=False,
                     civ_amount=min(float(o['civ_amount']), 0.5),
                     ins_cap=min(int(o['ins_cap']), 1200), ins_max=min(int(o['ins_max']), 2),
                     patrols=min(int(o['patrols']), 3), foot_patrols=min(int(o['foot_patrols']), 50))
        # ZombieSurvival: no bases, one shop vehicle per player, growing zombies and targeted respawn.
        # It runs on the Zombie machinery (GLA AI = zombie master); the town has no insurgents at all.
        # ZombieSurvival Defense = ZombieSurvival + a nuclear silo in the centre that must not fall
        self.battle_royale = bool(o['mode_battle_royale'])
        if self.battle_royale:
            for key, (default, minimum, maximum, _, _) in BR_SETTINGS.items():
                if isinstance(default, bool):
                    o[key] = str(o[key]).lower() in ('true', '1', 'yes')
                else:
                    o[key] = min(maximum, max(minimum, int(o[key])))
            o.update(mode_invasion=False, mode_clearance=False, mode_zombie=False, mode_zsurvival=False,
                     mode_zdefense=False, insurgents=False, fortress=False, outposts=0, patrols=0,
                     foot_patrols=0, ins_qrf=False, ins_bases=None, ai_paths=False, train=False,
                     cars=False, traffic=False, civilians=False, farm_life=False, black_map=True)
        self.invasion = bool(o['mode_invasion'])
        if self.invasion:
            o.update(mode_clearance=False, mode_zombie=False, mode_zsurvival=False, mode_zdefense=False,
                     insurgents=False, fortress=False, outposts=0, patrols=0, foot_patrols=0,
                     ins_qrf=False, ins_bases=None, ai_paths=False)
            if o['invasion_faction'] not in INVASION_ROSTERS:
                raise ValueError(f"Unknown Invasion faction: {o['invasion_faction']}")
            for key, (_, low, high, _) in INVASION_NUMBERS.items():
                o[key] = min(high, max(low, int(o[key])))
        self.zdef = bool(o['mode_zdefense'])
        if self.zdef: o['mode_zsurvival'] = True
        self.zsurv = bool(o['mode_zsurvival'])
        if self.zsurv:
            o.update(mode_zombie=True, mode_clearance=False, insurgents=False, fortress=False, outposts=0,
                     patrols=0, foot_patrols=0, ins_qrf=False, ins_bases=None)
        # game modes: you start in the middle of the town, one dormant AI slot keeps the game running
        self.game_mode = bool(o['mode_clearance'] or o['mode_zombie'] or self.invasion or self.battle_royale)
        if self.battle_royale:
            self.players = max(2, min(MAX_PLAYERS, int(players)))
            self.humans, self.layout = self.players, 'battle_royale'
        elif self.invasion:
            self.players = max(2, min(MAX_PLAYERS, int(players)))
            self.humans = self.players - 1
            self.layout = 'invasion'
        elif self.zsurv:
            self.humans = max(1, min(MAX_PLAYERS - 1, int(players) - 1))   # 'players' = people + the zombie AI
            self.players, self.layout = self.humans + 1, 'zsurv'
        elif self.game_mode:
            if o['mode_clearance']: o['insurgents'] = True
            n_people = len(self.manual_starts) if (self.layout == 'manual' and len(self.manual_starts) >= 2) else int(players)
            n_people = min(n_people, MAX_PLAYERS - 1)      # + 1 start for the AI
            if n_people >= 2:
                # several people: the chosen layout and count stay; the insurgents need no start (their own side);
                # classic Zombie adds one more start for the zombie AI (the lobby's GLA AI)
                self.multi, self.n_people = True, n_people
                self.players = n_people + 1      # + the zombie AI (Zombie) or the dormant AI (Clearance)
                if o['mode_clearance'] and self.layout == 'auto':
                    self.layout = 'clear'        # Clearance: the people in the south, the town ahead of them
            else:          # one person: + a dormant AI slot, so the game does not end at once
                self.players = 2
                self.layout = 'clear' if o['mode_clearance'] else 'mode'
        self.opt = o
        self.central_tunnel = bool(o['mode_zombie'] and not o['mode_clearance'] and not self.zsurv)
        self.base_clear = preflight.base_width(o['base_size'], self.AX, self.AY, self.players, self.layout,
                                               skirmish=not self.game_mode)
        if style in (None, 'auto'):
            style = region_style(self.p.lat0, self.p.lon0)
            log(f'  štýl budov podľa polohy: {STYLE_NAMES.get(style, style)}')
        self.style = style
        self.objs = []
        self.bridges = []
        self.bld_rects = []
        self.bld_klass = []
        self.bld_objs = []     # (uid, model, x, y, poly, klass) of ground-floor buildings
        self.ins_units = []    # (unit uid, building uid) for the garrison script
        self.ins_houses = []   # (x, y) of garrisoned houses
        self.bld_info = []     # per OSM building, for the editor: dict(id, pts, kind, models, floors)
        self.area_triggers = []  # (name, polygon) script areas: skirmish perimeters, insurgent bases
        self.road_w = dict(ROAD_W); self.road_w.update({k: v for k, v in catalog.road_w.items() if k in ROAD_W})
        self.stats = {}
        self.known_objects = getattr(catalog, 'objects', set()) or set()

    # ----------------------------------------------------------- raster helpers
    def cx(self, u): return u / CELL + BORDER

    def raster_poly(self, polys):
        img = Image.new('L', (self.WX, self.WY), 0); d = ImageDraw.Draw(img)
        for poly in polys:
            if len(poly) >= 3:
                d.polygon([(self.cx(x), self.cx(y)) for x, y in poly], fill=255)
        return np.asarray(img) > 0

    def occ_poly(self, poly, value=True):
        S = OCC
        pts = [(self.cx(x) * S, self.cx(y) * S) for x, y in poly]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, y0 = max(int(min(xs)) - 1, 0), max(int(min(ys)) - 1, 0)
        x1, y1 = min(int(max(xs)) + 2, self.occ.shape[1]), min(int(max(ys)) + 2, self.occ.shape[0])
        if x1 <= x0 or y1 <= y0: return 1.0
        img = Image.new('L', (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(img).polygon([(x - x0, y - y0) for x, y in pts], fill=255)
        m = np.asarray(img) > 0
        sub = self.occ[y0:y1, x0:x1]
        n = m.sum()
        frac = (m & sub).sum() / n if n else 1.0
        if value is not None: sub[m] = value if frac is not None else sub[m]
        return frac

    def occ_test(self, poly, grid=None):
        S = OCC
        pts = [(self.cx(x) * S, self.cx(y) * S) for x, y in poly]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, y0 = max(int(min(xs)) - 1, 0), max(int(min(ys)) - 1, 0)
        x1, y1 = min(int(max(xs)) + 2, self.occ.shape[1]), min(int(max(ys)) + 2, self.occ.shape[0])
        if x1 <= x0 or y1 <= y0: return 1.0
        img = Image.new('L', (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(img).polygon([(x - x0, y - y0) for x, y in pts], fill=255)
        m = np.asarray(img) > 0
        n = m.sum()
        g = self.occ if grid is None else grid
        return (m & g[y0:y1, x0:x1]).sum() / n if n else 1.0

    @staticmethod
    def rect(cx, cy, a, b, ang):
        c, s = math.cos(ang), math.sin(ang)
        return [(cx + dx * c - dy * s, cy + dx * s + dy * c) for dx, dy in
                ((-a / 2, -b / 2), (a / 2, -b / 2), (a / 2, b / 2), (-a / 2, b / 2))]

    def inside(self, x, y, m=0.0):
        return m <= x <= self.AX - m and m <= y <= self.AY - m

    def pick_model(self, klass, L, Wd):
        r = self.rank_models(klass, L, Wd)
        return r[0] if r else None

    def mkprops(self, uid, template_props, owner=None):
        out = []
        for key, v in template_props:
            nm = self.sid[key >> 8]
            if nm in ('uniqueID', 'objectName'): v = uid
            elif nm == 'originalOwner' and owner: v = owner
            out.append((key, v))
        return out

    def add_obj(self, name, x, y, ang, flags, uid, base, owner=None):
        self.objs.append(MapObject(3, float(x), float(y), 0.0, float(ang % (2 * math.pi)), flags, name,
                                   self.mkprops(uid, base, owner)))

    def _nearest_road(self, x, y, types=PAVED_STREETS):
        best = None
        for rt, run in self.road_lines:
            if rt not in types: continue
            for a, b in zip(run[:-1], run[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                L2 = dx * dx + dy * dy or 1e-9
                t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / L2))
                px, py = a[0] + t * dx, a[1] + t * dy
                d = math.hypot(x - px, y - py)
                if best is None or d < best[0]:
                    best = (d, px, py, math.atan2(dy, dx), rt)
        return best

    def flatten(self, x, y, r):
        cx, cy = self.cx(x), self.cx(y)
        yy, xx = np.ogrid[:self.WY, :self.WX]
        d = np.hypot(xx + 0.5 - cx, yy + 0.5 - cy) * CELL
        m = d < r
        if not m.any(): return
        z = float(self.H[m].mean())
        wgt = np.clip((r * 1.5 - d) / (r * 0.5), 0, 1)
        if hasattr(self, 'skirmish_level'):
            wgt[self.skirmish_level != 0] = 0
        self.H = np.where(wgt > 0, self.H * (1 - wgt) + z * wgt, self.H)

    def _dry(self, x, y, mg=80.0):
        """Clamp into the map and step off water so a waypoint is reachable."""
        x = min(max(x, mg), self.AX - mg); y = min(max(y, mg), self.AY - mg)
        if not self._wet(x, y): return x, y
        for r in range(20, 900, 20):
            for k in range(16):
                t = math.tau * k / 16
                px, py = x + r * math.cos(t), y + r * math.sin(t)
                if mg <= px <= self.AX - mg and mg <= py <= self.AY - mg and not self._wet(px, py):
                    return px, py
        return x, y

    # ----------------------------------------------------------- road objects
    def _wet(self, x, y):
        cx, cy = int(self.cx(x)), int(self.cx(y))
        return 0 <= cx < self.WX and 0 <= cy < self.WY and self.water_mask[cy, cx]

    # ----------------------------------------------------------- main
    def run(self):
        log = self.log
        tpl = MapFile.read(self.tpl_path)
        self.sid = {i: n for n, i in tpl.strings}
        by = {}
        for o in tpl.objects: by.setdefault(o.name, o)
        self.base_props = by['TreeDogwood1'].props
        self.road_props = by['TwoLane'].props
        self.wp_props = next(o.props for o in tpl.objects if o.name == '*Waypoints/Waypoint')
        self.path_wp_props = next((o.props for o in tpl.objects if o.name == '*Waypoints/Waypoint'
                                   and any(self.sid[k >> 8] == 'waypointPathLabel1' for k, _ in o.props)), self.wp_props)
        WX, WY = self.WX, self.WY
        self._occ_img = Image.new('L', (WX * OCC, WY * OCC), 0)
        log('Terén ...'); self.build_terrain()
        log('Voda ...'); self.build_water()
        log('Cesty ...')
        self.occ = np.zeros((WY * OCC, WX * OCC), bool)
        self.build_roads()
        self.over_spans = {}
        try: self.build_overpasses()
        except Exception as ex: log(f'  nadjazdy sa nepodarilo vytvoriť: {ex}')
        self.occ |= np.asarray(self._occ_img) > 0
        wm = geo.box_mean(self.water_mask, 1) > 0
        self.occ |= np.kron(wm, np.ones((OCC, OCC), bool))
        log('Základne a zdroje ...')
        self.pick_spots()
        self.place_bases()
        if self.central_tunnel:
            self.place_zombie_tunnel()
        if not (self.invasion or self.battle_royale):
            self.place_resources()
        occ_before = self.occ.copy()
        for x, y in () if self.invasion or self.battle_royale else self.starts:
            self.occ_poly(self.rect(x, y, self.base_clear, self.base_clear, 0), True)
        if self.zsurv:             # the vehicles' ring in the town centre (Defense: round the silo): a free square,
            ns = min(MAX_PLAYERS, max(8, self.humans + 1))   # so no Humvee spawns inside a house (dense cities)
            rr = max(300 if self.zdef else 140, ns * 60 / math.tau)
            cx_, cy_ = self.AX / 2, self.AY / 2
            if self.zdef:                                        # and the silo's own footprint
                a_, b_ = self.cat.buildings.get('ChinaNuclearMissileLauncher', (140, 140, 40))[:2]
                self.occ_poly(self.rect(cx_, cy_, a_ * 1.3, b_ * 1.3, 0), True)
            self.zs_plaza = True
        self.base_only = self.occ & ~occ_before      # blocked only because it is a player base (trees may grow there)
        if self.layout == 'manual' or not self.game_mode:
            self.base_only[:] = False              # the full build pad is available to the skirmish AI
        log('Benzínky ...'); self.place_fuel()
        log('Ihriská ...'); self.place_pitches()
        log('Silá ...'); self.place_silos()
        self.occ_static = self.occ.copy()      # roads, water, player bases, pitches - without the town's houses
        log('Budovy ...'); self.build_buildings()
        if self.invasion:
            self.place_invasion_hq()
        # the real town first: insurgent bases come after it and may stand among / over the houses
        if self.opt['fortress']:
            log('Nepriateľská pevnosť ...'); self.place_fortress()
        if self.opt['mode_clearance'] and not self.zsurv:
            log('Hlavná základňa povstalcov (sever) ...'); self.place_main_base()
        if int(self.opt['outposts']) > 0 or self.opt['ins_bases'] is not None:
            log('Základne povstalcov ...'); self.place_outposts()

        if self.opt['insurgents'] or self.opt['fortress'] or int(self.opt['outposts']) > 0 or self.opt['ins_bases'] is not None:
            self.place_ins_oil()
        if self.opt['insurgents']:
            log('Stanovištia povstalcov ...'); self.place_checkpoints()
        if self.opt['insurgents']:
            log('Povstalci v domoch ...'); self.place_insurgents()
        if self.opt['insurgents'] and self.opt['ins_qrf']:
            log('Zásahové jednotky povstalcov ...'); self.place_qrf()
        # objectives (town hall / church / arms depot) are off since v0.38.1 - Clearance = just the counter
        if self.opt['mode_clearance'] and self.opt['missions']:
            log('Úlohy (Clearance) ...'); self.place_objectives()
        if self.opt['mode_zombie']:
            if self.zdef:
                log('Atómové silo ...'); self.place_silo()
            log('Zombie hniezda ...'); self.place_nests()
            self.zombie_teams_list = [f'EARTH_Zombies{k + 1}' for k in range(len(self.nests))] + ['EARTH_ZombiesBig']
            if self.zsurv: self.zombie_teams_list.append('EARTH_ZMigs')     # the MiGs: never hunted
        if self.opt['footways']:
            log('Chodníky ku vchodom ...'); self.place_entrance_paths()
        if self.opt['fences'] and self.osm.barriers:
            log('Ploty ...'); self.place_fences()
        if self.opt['details']:
            log('Detaily (cintoríny, ihriská, zastávky, lampy) ...'); self.place_details()
        else:
            self.area_polys = []
        if self.battle_royale:
            # Reserve drops and civilian units before adding trees and other scenery.
            self.next_wp = len(self.starts)
            self.br_layout()
            br_wp_end = self.next_wp
        log('Stromy ...'); self.place_trees()
        self.place_nature_details()
        if any(self.opt[k] for k in ('civilians', 'traffic', 'farm_life', 'sounds')):
            log('Oživenie (civilisti, autá, zvieratá, zvuky) ...'); self.place_life()
        self.emit_roads()
        self.links = []
        if self.battle_royale:
            self.next_wp = br_wp_end
        else:
            self.build_ai_paths()
            self.build_script_areas()
        self.build_patrols()
        self.build_railroad()
        self.build_spawn_points()
        self.build_air_paths()
        if self.invasion:
            self.build_invasion_waypoints()
        # flatten under buildings
        for poly, name in self.bld_rects:
            xs = [self.cx(x) for x, y in poly]; ys = [self.cx(y) for x, y in poly]
            x0, x1 = max(int(min(xs)), 0), min(int(max(xs)) + 2, self.WX)
            y0, y1 = max(int(min(ys)), 0), min(int(max(ys)) + 2, self.WY)
            if x1 > x0 and y1 > y0:
                sub = self.H[y0:y1, x0:x1]
                if hasattr(self, 'skirmish_level'):
                    unreserved = self.skirmish_level[y0:y1, x0:x1] == 0
                    sub[unreserved] = sub.mean()
                else:
                    sub[:] = sub.mean()
        log('Textúry ...')
        t = tpl.blend
        parse = self._parse_classes(t.tail, tpl.height)
        classes = list(parse['classes'])
        # extra ground textures beyond the template: brown fields (SandType1darker, TXSand01c) and car parks with
        # bay lines (ParkingLotType1c = lines north-south, 1d = east-west); all 128 px = 2 x 2 tiles
        for extra in ('SandType1darker', 'ParkingLotType1c', 'ParkingLotType1d'):
            if not any(c[4] == extra for c in classes):
                nxt = max(c[0] + c[1] for c in classes)
                classes.append((nxt, 4, 2, 0, extra))
        self.paint(classes)
        # ---- assemble
        Hb = np.clip(np.round(self.H / HS), 0, 255).astype(np.uint8)
        m = tpl
        m.height = HeightMap(WX, WY, BORDER, [(self.NX, self.NY)], Hb.tobytes())
        bt = BlendTail.parse(t.tail, t.w, t.h)          # template dimensions
        bt.classes = [tuple(c) for c in classes]
        bt.num_bitmap_tiles = max(c[0] + c[1] for c in classes)
        bt.cliff_state = bytes(((WX + 7) // 8) * WY)
        bt.blends, bt.cliffs = self.blend_list, []
        m.blend = BlendTile(WX, WY, self.tiles.ravel().tolist(), self.blend_arr.ravel().tolist(), [0] * (WX * WY),
                            [0] * (WX * WY), bt.dump())
        m.objects = self.objs
        if self.battle_royale:
            self.br_write_sides(m)
        elif self.invasion:
            self.write_invasion_sides(m)
        elif self.opt['insurgents'] or self.opt['fortress'] or int(self.opt['outposts']) > 0 or self.opt['ins_bases'] \
                or getattr(self, 'patrols', []) or self.game_mode or self.opt['black_map']:
            write_sides(m, [dict(name=INS_SIDE, display='Insurgents', faction='FactionCivilian',
                                 enemies=' '.join(f'player{i}' for i in range(MAX_PLAYERS)), color=0xFFB01010,
                                 start_index=INS_START,
                                 teams=([f'teamInsWave{b + 1}' for b in range(len(getattr(self, 'strongholds', [])))]
                                        if int(self.opt['ins_wave_size']) > 0 else [])
                                       + [q['team'] for q in getattr(self, 'qrf', [])])],
                        {INS_SIDE: self.insurgent_scripts(),
                         **({self.ZOMBIE_SIDE: self.zombie_scripts()} if self.opt['mode_zombie'] else {})},
                        extra_teams=[(t, self.ZOMBIE_SIDE) for t in getattr(self, 'zombie_teams_list', [])])
        for c in m.chunks:
            if c[0] == 'PolygonTriggers': c[2] = self.dump_triggers()
            if c[0] == 'WaypointsList': c[2] = self.dump_links()
            if c[0] == 'GlobalLighting':
                c[2] = struct.pack('<I', int(self.opt['time_of_day'])) + c[2][4:]
        self.map = m
        self.stats['objects'] = len(self.objs)
        return m


def generate(osm, elev_fn, proj, tiles, catalog, template_path, out_path, style=None, seed=1, log=print,
             terrain_mode='strong', **opts):
    g = Gen(osm, elev_fn, proj, tiles, catalog, template_path, style, seed, log, terrain_mode, **opts)
    m = g.run()
    m.write(out_path, compress=bool(g.opt.get('compact')))
    return g
