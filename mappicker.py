"""Interactive OpenStreetMap area picker for tkinter (no extra dependencies).

Mouse:
  left drag        - move the map
  wheel            - zoom
  left click       - move the selected square here
  right drag       - draw a new selection (or Shift + left drag)
"""
import io, math, os, queue, threading
from tr import T

TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
UA = {'User-Agent': 'ZeroHourEarth/0.16.4 (hobby Zero Hour map generator)'}
TILE_CACHE = os.path.join(os.path.expanduser('~'), '.cncearth', 'tiles')
TS = 256


# ---------------- pure math (tested without a display) ----------------
def ll_to_px(lat, lon, z):
    n = TS * 2 ** z
    lat = max(min(lat, 85.0), -85.0)
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
    return x, y


def px_to_ll(x, y, z):
    n = TS * 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def square_corners(lat, lon, km, aspect=1.0):
    """South-west and north-east corner of a km (east-west) x km*aspect (north-south) rectangle centred on lat/lon."""
    dlat = km * aspect * 1000 / 2 / 110574.0
    dlon = km * 1000 / 2 / (111320.0 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon), (lat + dlat, lon + dlon)


def km_between(lat1, lon1, lat2, lon2):
    dy = (lat2 - lat1) * 110574.0
    dx = (lon2 - lon1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return abs(dx) / 1000, abs(dy) / 1000


# ---------------- widget ----------------
class MapPicker:
    def __init__(self, parent, km_var, on_change=None, lat=48.43, lon=19.80, zoom=12, width=640, height=420):
        import tkinter as tk
        self.tk = tk
        self.cv = tk.Canvas(parent, width=width, height=height, bg='#d8d8d0', highlightthickness=1,
                            highlightbackground='#888', cursor='fleur')
        self.km_var, self.on_change = km_var, on_change
        self.aspect = 1.0          # north-south / east-west (Oblasť -> Tvar)
        self.z = zoom
        self.cx, self.cy = ll_to_px(lat, lon, zoom)      # world px at canvas centre
        self.sel = (lat, lon)                            # selection centre
        self.images = {}                                 # (z,x,y) -> PhotoImage
        self.pending = set()
        self.q = queue.Queue()
        self.done = queue.Queue()
        self._drag = None
        self._draw_start = None
        self._moved = False
        self.start_mode = False     # True: clicks place player starts instead of moving the selection
        self.starts = []            # [(lat, lon)]
        self.max_starts = 32
        self.on_starts = None
        self.plan = None            # preflight.start_plan(): where the automatic layout will put the starts
        self.osm = None             # quick OSM preview: dict(bbox, roads, water, forest, bld) from online.fetch_preview
        self._osm_cache = None      # (zoom, projected data): the preview in world pixels of the current zoom
        self._panning = False       # the preview is not drawn while the map is dragged (keeps panning smooth)
        for _ in range(4):
            threading.Thread(target=self._worker, daemon=True).start()
        c = self.cv
        c.bind('<Configure>', lambda e: self.redraw())
        c.bind('<ButtonPress-1>', self._press)
        c.bind('<B1-Motion>', self._motion)
        c.bind('<ButtonRelease-1>', self._release)
        c.bind('<Shift-ButtonPress-1>', self._dstart)
        c.bind('<Shift-B1-Motion>', self._dmove)
        c.bind('<Shift-ButtonRelease-1>', self._dend)
        c.bind('<ButtonPress-3>', self._dstart)
        c.bind('<B3-Motion>', self._dmove)
        c.bind('<ButtonRelease-3>', self._dend)
        c.bind('<MouseWheel>', lambda e: self.zoom_at(1 if e.delta > 0 else -1, e.x, e.y))
        c.bind('<Button-4>', lambda e: self.zoom_at(1, e.x, e.y))
        c.bind('<Button-5>', lambda e: self.zoom_at(-1, e.x, e.y))
        try:
            km_var.trace_add('write', lambda *a: self.redraw())
        except Exception:
            pass
        self.cv.after(80, self._poll)

    # geometry helpers
    def grid(self, **kw): self.cv.grid(**kw)
    def pack(self, **kw): self.cv.pack(**kw)

    def _size(self):
        return max(self.cv.winfo_width(), 2), max(self.cv.winfo_height(), 2)

    def to_canvas(self, lat, lon):
        w, h = self._size()
        x, y = ll_to_px(lat, lon, self.z)
        return x - self.cx + w / 2, y - self.cy + h / 2

    def to_ll(self, sx, sy):
        w, h = self._size()
        return px_to_ll(sx - w / 2 + self.cx, sy - h / 2 + self.cy, self.z)

    def km(self):
        try:
            return max(0.5, min(30.0, float(self.km_var.get())))
        except Exception:
            return 5.0

    # public
    def center_on(self, lat, lon, zoom=None, select=True):
        if zoom is not None: self.z = max(3, min(18, zoom))
        self.cx, self.cy = ll_to_px(lat, lon, self.z)
        if select: self.set_selection(lat, lon)
        else: self.redraw()

    def set_selection(self, lat, lon):
        self.sel = (lat, lon)
        self.redraw()
        if self.on_change: self.on_change(lat, lon, self.km())

    def zoom_at(self, d, sx, sy):
        nz = max(3, min(18, self.z + d))
        if nz == self.z: return
        lat, lon = self.to_ll(sx, sy)
        self.z = nz
        w, h = self._size()
        x, y = ll_to_px(lat, lon, nz)
        self.cx, self.cy = x - (sx - w / 2), y - (sy - h / 2)
        self.redraw()

    def fit_selection(self):
        w, h = self._size()
        km = self.km()
        for z in range(18, 2, -1):
            (s, wl), (n, e) = square_corners(*self.sel, km, self.aspect)
            x0, y0 = ll_to_px(n, wl, z); x1, y1 = ll_to_px(s, e, z)
            if x1 - x0 < w * 0.8 and y1 - y0 < h * 0.8:
                self.center_on(*self.sel, zoom=z, select=False); return

    # mouse
    def _press(self, e):
        self._drag = (e.x, e.y, self.cx, self.cy); self._moved = False

    def _motion(self, e):
        if not self._drag: return
        x0, y0, cx, cy = self._drag
        if abs(e.x - x0) + abs(e.y - y0) > 4: self._moved = True
        self.cx, self.cy = cx - (e.x - x0), cy - (e.y - y0)
        self._panning = self.osm is not None and self._moved
        self.redraw()

    def _release(self, e):
        if self._panning:
            self._panning = False; self.redraw()
        if self._drag and not self._moved:
            if self.start_mode:
                if len(self.starts) < self.max_starts:
                    self.starts.append(self.to_ll(e.x, e.y)); self._starts_changed()
            else:
                self.set_selection(*self.to_ll(e.x, e.y))
        self._drag = None

    def _starts_changed(self):
        self.redraw()
        if self.on_starts: self.on_starts(self.starts)

    def clear_starts(self):
        self.starts = []; self._starts_changed()

    def _remove_nearest_start(self, sx, sy):
        if not self.starts: return
        d = [((self.to_canvas(*s)[0] - sx) ** 2 + (self.to_canvas(*s)[1] - sy) ** 2, i) for i, s in enumerate(self.starts)]
        dist, i = min(d)
        if dist < 40 ** 2:
            self.starts.pop(i); self._starts_changed()

    def _dstart(self, e):
        self._drag = None
        self._draw_start = (e.x, e.y)

    def _dmove(self, e):
        if not self._draw_start: return
        self.cv.delete('rubber')
        x0, y0 = self._draw_start
        self.cv.create_rectangle(x0, y0, e.x, e.y, outline='#e01010', dash=(4, 3), width=2, tags='rubber')

    def _dend(self, e):
        if not self._draw_start: return
        x0, y0 = self._draw_start
        self._draw_start = None
        self.cv.delete('rubber')
        if abs(e.x - x0) < 6 or abs(e.y - y0) < 6:
            if self.start_mode: self._remove_nearest_start(e.x, e.y)
            else: self.set_selection(*self.to_ll(e.x, e.y))
            return
        la0, lo0 = self.to_ll(x0, y0); la1, lo1 = self.to_ll(e.x, e.y)
        kx, ky = km_between(la0, lo0, la1, lo1)
        side = round(max(kx, ky / max(self.aspect, 1e-6)), 1)
        self.km_var.set(max(0.5, min(30.0, side)))
        self.set_selection((la0 + la1) / 2, (lo0 + lo1) / 2)

    # tiles
    def _worker(self):
        import requests
        while True:
            key = self.q.get()
            z, x, y = key
            path = os.path.join(TILE_CACHE, str(z), str(x), f'{y}.png')
            data = None
            try:
                if os.path.exists(path):
                    data = open(path, 'rb').read()
                else:
                    r = requests.get(TILE_URL.format(z=z, x=x, y=y), headers=UA, timeout=20)
                    if r.status_code == 200:
                        data = r.content
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        open(path, 'wb').write(data)
            except Exception:
                data = None
            self.done.put((key, data))

    def _poll(self):
        got = False
        while True:
            try:
                key, data = self.done.get_nowait()
            except queue.Empty:
                break
            self.pending.discard(key)
            if data:
                try:
                    from PIL import Image, ImageTk
                    self.images[key] = ImageTk.PhotoImage(Image.open(io.BytesIO(data)).convert('RGB'))
                    got = True
                except Exception:
                    pass
        if got: self.redraw()
        if len(self.images) > 600:
            for k in list(self.images)[:200]:
                if k[0] != self.z: self.images.pop(k, None)
        self.cv.after(80, self._poll)

    # ---- quick OSM preview (roads, water, forest, building density) drawn over the map
    def set_osm(self, data):
        self.osm, self._osm_cache = data, None
        self.redraw()

    def _projected_osm(self):
        """The preview in world pixels of the current zoom (computed once per zoom level)."""
        if self._osm_cache and self._osm_cache[0] == self.z:
            return self._osm_cache[1]
        z, d = self.z, self.osm

        def proj(pts):
            xy = [ll_to_px(la, lo, z) for la, lo in pts]
            xs = [p[0] for p in xy]; ys = [p[1] for p in xy]
            return (min(xs), min(ys), max(xs), max(ys), [v for p in xy for v in p])
        out = dict(roads=[(cls, proj(p)) for cls, p in d['roads']], water=[proj(p) for p in d['water']],
                   forest=[proj(p) for p in d['forest']], bld=[ll_to_px(la, lo, z) for la, lo in d['bld']])
        self._osm_cache = (z, out)
        return out

    def _draw_osm(self, w, h, left, top):
        c, p = self.cv, self._projected_osm()
        vis = lambda b: b[2] >= left and b[0] <= left + w and b[3] >= top and b[1] <= top + h
        shift = lambda flat: [v - (left if i % 2 == 0 else top) for i, v in enumerate(flat)]
        closed = lambda b: len(b[4]) >= 8 and b[4][:2] == b[4][-2:]       # a ring, not an open line (river)
        for b in p['forest']:
            if vis(b) and closed(b):
                c.create_polygon(shift(b[4]), fill='#2e8b3d', outline='', stipple='gray50')
        for b in p['water']:
            if not vis(b): continue
            if closed(b):
                c.create_polygon(shift(b[4]), fill='#1f6fd0', outline='#1f6fd0', stipple='gray50')
            else:
                c.create_line(shift(b[4]), fill='#1f6fd0', width=2)
        for cls, b in p['roads']:
            if vis(b):
                c.create_line(shift(b[4]), fill='#c2410c' if cls else '#e08a3c', width=3 if cls else 1)
        # buildings: one small square per ~6 px cell that has at least one (a density picture, cheap for 100k)
        cell = 6
        seen = set()
        for x, y in p['bld']:
            sx, sy = x - left, y - top
            if 0 <= sx < w and 0 <= sy < h:
                seen.add((int(sx // cell), int(sy // cell)))
        for gx, gy in seen:
            c.create_rectangle(gx * cell + 1, gy * cell + 1, gx * cell + cell - 1, gy * cell + cell - 1,
                               fill='#7a2d9a', outline='')
        n = len(self.osm['bld'])
        c.create_rectangle(4, self._size()[1] - 24, 330, self._size()[1] - 4, fill='#202020', outline='')
        c.create_text(10, self._size()[1] - 14, anchor='w', fill='#ffd000', font=('Segoe UI', 9, 'bold'),
                      text=T('Náhľad OSM') + f': {n} ' + T('budov') + f', {len(self.osm["roads"])} ' + T('ciest'))

    def _draw_plan(self, s, wl, nn, e):
        """Squares of the bases before terrain adjustments."""
        c, plan = self.cv, self.plan
        colors = ['#ff3030', '#3070ff', '#30c030', '#d0a000', '#ff8c00', '#a040ff', '#00a0a0', '#ff60c0',
                  '#606060', '#8b5a2b', '#808000', '#000080']
        half = plan['base_m'] / 2.0
        dlat = half / 110574.0
        for i, (rx, ry) in enumerate(plan['points']):
            lat, lon = s + (nn - s) * ry, wl + (e - wl) * rx
            col = colors[i % len(colors)]
            sx, sy = self.to_canvas(lat, lon)
            if half > 0:
                dlon = half / (111320.0 * math.cos(math.radians(self.sel[0])))
                ax, ay = self.to_canvas(lat + dlat, lon - dlon); bx, by = self.to_canvas(lat - dlat, lon + dlon)
                c.create_rectangle(ax, ay, bx, by, outline=col, width=2, dash=(5, 3))
            if plan['layout'] != 'manual':
                c.create_oval(sx - 8, sy - 8, sx + 8, sy + 8, fill=col, outline='white', width=1)
                c.create_text(sx, sy, text=str(i + 1), fill='white', font=('Segoe UI', 8, 'bold'))
        if plan['outside']:
            c.create_rectangle(4, 4, 470, 26, fill='#202020', outline='')
            invasion = plan['layout'] == 'invasion'
            if invasion:
                x, y = self.to_canvas((s + nn) / 2, (wl + e) / 2)
                c.create_oval(x - 12, y - 12, x + 12, y + 12, fill='#285da0', outline='#fff', width=2)
                c.create_text(x, y, text='HQ', fill='#fff', font=('Segoe UI', 8, 'bold'))
            c.create_text(10, 15, anchor='w', fill='#ffd000', font=('Segoe UI', 9, 'bold'),
                          text=T('Invasion: obrancovia začínajú pri spoločnom veliteľstve' if invasion
                                 else 'ZombieSurvival: štarty sú v čiernom okraji mimo mapy'))

    def redraw(self):
        c = self.cv
        c.delete('all')
        w, h = self._size()
        left, top = self.cx - w / 2, self.cy - h / 2
        n = 2 ** self.z
        tx0, ty0 = int(left // TS), int(top // TS)
        tx1, ty1 = int((left + w) // TS), int((top + h) // TS)
        for ty in range(ty0, ty1 + 1):
            if ty < 0 or ty >= n: continue
            for tx in range(tx0, tx1 + 1):
                key = (self.z, tx % n, ty)
                sx, sy = tx * TS - left, ty * TS - top
                img = self.images.get(key)
                if img is not None:
                    c.create_image(sx, sy, image=img, anchor='nw')
                else:
                    c.create_rectangle(sx, sy, sx + TS, sy + TS, outline='#c8c8c0', fill='#e4e4dc')
                    if key not in self.pending:
                        self.pending.add(key); self.q.put(key)
        if self.osm is not None and not self._panning:
            self._draw_osm(w, h, left, top)
        # selection square
        (s, wl), (nn, e) = square_corners(*self.sel, self.km(), self.aspect)
        x0, y0 = self.to_canvas(nn, wl); x1, y1 = self.to_canvas(s, e)
        c.create_rectangle(x0, y0, x1, y1, outline='#e01010', width=3)
        c.create_rectangle(x0, y0, x1, y1, outline='', fill='#e01010', stipple='gray12')
        if self.plan and self.start_mode == (self.plan['layout'] == 'manual'):
            self._draw_plan(s, wl, nn, e)
        mx, my = self.to_canvas(*self.sel)
        c.create_line(mx - 8, my, mx + 8, my, fill='#e01010', width=2)
        c.create_line(mx, my - 8, mx, my + 8, fill='#e01010', width=2)
        c.create_text(x0 + 4, y0 - 2, anchor='sw', text=f'{self.km():g} x {self.km() * self.aspect:g} km',
                      fill='#a00000', font=('Segoe UI', 10, 'bold'))
        colors = ['#ff3030', '#3070ff', '#30c030', '#ffd000', '#ff8c00', '#a040ff', '#00d0d0', '#ff60c0',
                  '#ffffff', '#8b5a2b', '#808000', '#000080']
        for i, (la, lo) in enumerate(self.starts if self.start_mode else []):
            sx, sy = self.to_canvas(la, lo)
            col = colors[i % len(colors)]
            c.create_oval(sx - 13, sy - 13, sx + 13, sy + 13, fill=col, outline='black' if col == '#ffffff' else 'white', width=2)
            c.create_text(sx, sy, text=str(i + 1), fill='black' if col == '#ffffff' else 'white', font=('Segoe UI', 11, 'bold'))
        if self.start_mode:
            c.create_rectangle(4, 4, 380, 26, fill='#202020', outline='')
            c.create_text(10, 15, anchor='w', fill='#ffd000', font=('Segoe UI', 9, 'bold'),
                          text=T(f'REŽIM ŠTARTOV: klik = pridaj hráča ({len(self.starts)}/{self.max_starts}), pravý = zmaž'))
        c.create_text(w - 4, h - 2, anchor='se', text='© OpenStreetMap contributors',
                      fill='#333', font=('Segoe UI', 8))
