"""Map editor: after a map is generated, the player can click buildings and pick what they are (block of flats
type, floors, shop, house, garage or nothing) and place insurgent bases with their own content. The choices
are saved per map in ~/.cncearth/edits/<map>.json and used every time that map is generated again."""
import json, math, os

from tr import T

EDIT_DIR = os.path.join(os.path.expanduser('~'), '.cncearth', 'edits')

# shown in the editor (Slovak, translated by T) -> generator kind
KINDS = [('Automaticky (podľa OSM)', 'auto'),
         ('Rodinný dom (šikmá strecha)', 'house'),
         ('Bytovka nízka (do 6 poschodí, podľa vchodov)', 'apt_low'),
         ('Bytovka vysoká (8 poschodí)', 'apt_high'),
         ('Hala / telocvičňa (nízka)', 'hall'),
         ('Priemysel / sklad', 'industrial'),
         ('Úrad / škola', 'civic'),
         ('Kôlňa / hospodárska budova', 'shed'),
         ('Panelák štvorcový (AsiaApartment02)', 'tower'),
         ('Panelák dlhý s balkónmi (AsianApartmentSingle01)', 'long'),
         ('Panelák 5-7 poschodí (AsianApartment01)', 'mid'),
         ('Výškový panelák 8+ (CivilianHighrise01)', 'high'),
         ('Obchod / plochá strecha (AsianApartmentUnit)', 'flat'),
         ('Garáže (MogadishuGarage)', 'garage'),
         ('Bez budovy', 'none')]
LEGEND = [('apt', 'Bytovka'), ('house', 'Dom'), ('shed', 'Kôlňa'), ('garage', 'Garáže'), ('flat', 'Plochá / obchod'),
          ('shop', 'Obchod / hala'), ('civic', 'Úrad / škola'), ('industrial', 'Priemysel'), ('sporthall', 'Hala / telocvičňa'),
          ('farm', 'Farma'), ('church', 'Kostol')]
QUICK = [('Automaticky', 'auto'), ('Dom', 'house'), ('Bytovka nízka', 'apt_low'), ('Bytovka vysoká', 'apt_high'),
         ('Hala', 'hall'), ('Priemysel', 'industrial'), ('Garáže', 'garage'), ('Bez budovy', 'none')]
COLORS = {'sporthall': '#30c090', 'garage_one': '#909090', 'farmhall': '#a08040', 'hospital': '#ff80c0', 'mall': '#40e0b0',
          'apt': '#e0a030', 'civic': '#c060e0', 'flat': '#40b0e0', 'garage': '#909090', 'house': '#e06050',
          'shed': '#806050', 'farm': '#a08040', 'industrial': '#7070a0', 'church': '#ffffff', 'shop': '#40e0b0',
          'pub': '#e06050', 'station': '#40b0e0'}
BASE_FIELDS = [('buildings', 'Budovy'), ('defenses', 'Obranné veže'), ('infantry', 'Pešiaci'), ('vehicles', 'Vozidlá')]


def edits_path(map_name):
    return os.path.join(EDIT_DIR, f'{map_name}.json')


def load_edits(map_name):
    try:
        d = json.load(open(edits_path(map_name), encoding='utf-8'))
        d.setdefault('buildings', {}); d.setdefault('bases', None); d.setdefault('zones', None)
        return d
    except Exception:
        return {'buildings': {}, 'bases': None, 'zones': None}


def save_edits(map_name, d):
    os.makedirs(EDIT_DIR, exist_ok=True)
    json.dump(d, open(edits_path(map_name), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


# ---------------- pure helpers (tested without a display)
def point_in_poly(x, y, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]; xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def find_building(buildings, x, y, tol=0.0):
    """Building under the map point (units); the smallest one wins when footprints overlap."""
    best = None
    for i, b in enumerate(buildings):
        pts = b['pts']
        if len(pts) < 3: continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        if not (min(xs) - tol <= x <= max(xs) + tol and min(ys) - tol <= y <= max(ys) + tol): continue
        if point_in_poly(x, y, pts) or tol > 0:
            a = (max(xs) - min(xs)) * (max(ys) - min(ys))
            if best is None or a < best[0]: best = (a, i)
    return None if best is None else best[1]


class View:
    """Map units <-> canvas pixels with zoom and pan (game y axis points up)."""
    def __init__(self, area, size):
        self.area, self.size = float(area), float(size)
        self.scale = self.size / self.area
        self.ox = self.oy = 0.0              # map units at the canvas left / bottom edge

    def to_px(self, x, y):
        return (x - self.ox) * self.scale, self.size - (y - self.oy) * self.scale

    def to_units(self, px, py):
        return px / self.scale + self.ox, (self.size - py) / self.scale + self.oy

    def zoom(self, f, px, py):
        ux, uy = self.to_units(px, py)
        self.scale = max(self.size / self.area, min(self.scale * f, 2.0))
        self.ox, self.oy = ux - px / self.scale, uy - (self.size - py) / self.scale

    def pan(self, dpx, dpy):
        self.ox -= dpx / self.scale; self.oy += dpy / self.scale


# ---------------- window
def open_editor(root, data, on_regenerate, palette):
    """data: dict(map_name, area, bg (png path), buildings [..], bases [..], starts [(x, y)], proj (lat0, lon0, area_m, tiles))."""
    import tkinter as tk
    from tkinter import ttk
    from geo import Proj
    BG, PANEL, FG, ACC = palette
    proj = Proj(*data['proj'])
    name = data['map_name']
    edits = load_edits(name)
    if edits.get('bases') is None:            # start from where the generator put them
        edits['bases'] = [dict(lat=b['lat'], lon=b['lon'], **{k: int(b.get(k, 0)) for k, _ in BASE_FIELDS})
                          for b in data.get('bases', []) if not b.get('fortress')]
        edits['bases_auto'] = True
    if edits.get('zones') is None:            # the insurgent area the program chose
        edits['zones'] = [dict(lat=z['lat'], lon=z['lon'], radius_m=z['radius_m']) for z in data.get('zones', [])]
        edits['zones_auto'] = True
    blds = data['buildings']

    win = tk.Toplevel(root)
    win.title(T('Editor mapy') + ' - ' + name)
    win.configure(bg=BG)
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    size = max(500, min(sh - 160, sw - 520))
    win.geometry(f'{size + 470}x{size + 40}+20+20')
    cv = tk.Canvas(win, width=size, height=size, bg='#202428', highlightthickness=0, cursor='crosshair')
    cv.grid(row=0, column=0, rowspan=2, padx=(8, 4), pady=8, sticky='nw')
    side = ttk.Frame(win, padding=8)
    side.grid(row=0, column=1, sticky='nsew')
    win.columnconfigure(1, weight=1)
    view = View(data['area'], size)
    state = {'sel': None, 'multi': set(), 'rect': None, 'base': None, 'mode': 'bld', 'drag': None, 'img': None, 'moved': False, 'loading': False}

    bg_img = None
    try:
        from PIL import Image, ImageTk
        bg_img = Image.open(data['bg']).convert('RGB')
    except Exception:
        pass

    # ---- side panel
    mode = tk.StringVar(value='bld')
    ttk.Label(side, text=T('Režim'), style='H.TLabel').pack(anchor='w')
    ttk.Radiobutton(side, text=T('Budovy: klik = jedna, ťahaj = označ viac, Ctrl+klik = pridaj / uber'), variable=mode,
                    value='bld').pack(anchor='w')
    ttk.Radiobutton(side, text=T('Základne povstalcov: klik = pridať, pravý klik = zmazať'), variable=mode,
                    value='base').pack(anchor='w')
    ttk.Radiobutton(side, text=T('Oblasť povstalcov: klikni do stredu a ťahaj = polomer, pravý klik = zmazať'),
                    variable=mode, value='zone').pack(anchor='w')
    ttk.Label(side, text=T('Povstalci v domoch, hliadky aj ich základne budú len v nakreslených kruhoch.'),
              style='Dim.TLabel', wraplength=440).pack(anchor='w')
    ttk.Label(side, text=T('Koliesko = zoom, pravé tlačidlo + ťahanie = posun mapy'), style='Dim.TLabel',
              wraplength=440).pack(anchor='w', pady=(2, 8))

    bfr = ttk.Frame(side); bfr.pack(fill='x', pady=4)
    ttk.Label(bfr, text=T('Vybraná budova'), style='H.TLabel').grid(row=0, column=0, columnspan=2, sticky='w')
    info = ttk.Label(bfr, text=T('nič nie je vybrané'), style='Dim.TLabel', wraplength=440, justify='left')
    info.grid(row=1, column=0, columnspan=2, sticky='w')
    kind_labels = [T(lbl) for lbl, _ in KINDS]
    kind = tk.StringVar(value=kind_labels[0])
    ttk.Label(bfr, text=T('Typ')).grid(row=2, column=0, sticky='w', pady=3)
    kcb = ttk.Combobox(bfr, textvariable=kind, values=kind_labels, state='readonly', width=44)
    kcb.grid(row=2, column=1, sticky='w')
    floors = tk.StringVar(value='0')
    ttk.Label(bfr, text=T('Poschodia (0 = podľa OSM)')).grid(row=3, column=0, sticky='w', pady=3)
    ttk.Spinbox(bfr, from_=0, to=30, textvariable=floors, width=6).grid(row=3, column=1, sticky='w')

    stack = tk.StringVar(value='0')
    ttk.Label(bfr, text=T('Stack: poschodia navyše (0-6)')).grid(row=4, column=0, sticky='w', pady=3)
    ttk.Spinbox(bfr, from_=0, to=6, textvariable=stack, width=6).grid(row=4, column=1, sticky='w')
    sf = ttk.Frame(bfr); sf.grid(row=5, column=0, columnspan=2, sticky='w', pady=(2, 2))
    ttk.Label(sf, text=T('Rýchly stack (rovnaká budova znova navrch):'), style='Dim.TLabel').grid(row=0, column=0, columnspan=4, sticky='w')
    for si_, (sl_, sv_) in enumerate((('Stack +1', 1), ('Stack +2', 2), ('Stack +3', 3), ('Stack = 0', 0))):
        ttk.Button(sf, text=T(sl_), width=14, command=lambda v=sv_: stack.set(str(v))).grid(row=1, column=si_, padx=1, pady=1)
    qf = ttk.Frame(bfr); qf.grid(row=6, column=0, columnspan=2, sticky='w', pady=(6, 2))
    ttk.Label(qf, text=T('Rýchlo zmeniť označené na:'), style='Dim.TLabel').grid(row=0, column=0, columnspan=4, sticky='w')
    def quick(v):
        kind.set(next(T(l) for l, k in KINDS if k == v))
    for qi, (ql, qv) in enumerate(QUICK):
        ttk.Button(qf, text=T(ql), width=14, command=lambda v=qv: quick(v)).grid(row=1 + qi // 4, column=qi % 4, padx=1, pady=1)
    lg = tk.Frame(side, bg=PANEL); lg.pack(fill='x', pady=(6, 0))
    for li, (lk, ll) in enumerate(LEGEND):
        tk.Label(lg, text='  ', bg=COLORS.get(lk, '#888')).grid(row=li // 3, column=(li % 3) * 2, padx=(0, 3), pady=1)
        tk.Label(lg, text=T(ll), bg=PANEL, fg=FG, font=('Segoe UI', 8)).grid(row=li // 3, column=(li % 3) * 2 + 1, sticky='w', padx=(0, 8))
    hover = ttk.Label(side, text=T('Prejdi myšou nad budovu: ukáže sa model a poschodia.'), style='Dim.TLabel', wraplength=440)
    hover.pack(anchor='w', pady=(4, 0))
    basefr = ttk.Frame(side); basefr.pack(fill='x', pady=(12, 4))
    ttk.Label(basefr, text=T('Vybraná základňa povstalcov'), style='H.TLabel').grid(row=0, column=0, columnspan=2, sticky='w')
    base_info = ttk.Label(basefr, text=T('klikni na mapu v režime základní'), style='Dim.TLabel')
    base_info.grid(row=1, column=0, columnspan=2, sticky='w')
    bvars = {}
    for r, (k, lbl) in enumerate(BASE_FIELDS):
        bvars[k] = tk.StringVar(value='0')
        ttk.Label(basefr, text=T(lbl)).grid(row=2 + r, column=0, sticky='w', pady=2)
        ttk.Spinbox(basefr, from_=0, to=60, textvariable=bvars[k], width=6).grid(row=2 + r, column=1, sticky='w')

    summary = ttk.Label(side, text='', style='Dim.TLabel', wraplength=440, justify='left')
    summary.pack(anchor='w', pady=8)

    def update_summary():
        nb = len(edits['buildings']); nbase = len(edits['bases']); nz = len(edits['zones'])
        auto = '  (' + T('ako ich rozmiestnil program') + ')'
        summary.config(text=T('Upravené budovy') + f': {nb}\n' + T('Základne povstalcov') + f': {nbase}'
                       + (auto if edits.get('bases_auto') else '') + '\n' + T('Oblasti povstalcov') + f': {nz}'
                       + (auto if edits.get('zones_auto') else ''))

    # ---- drawing
    def draw():
        cv.delete('all')
        if bg_img is not None:
            from PIL import ImageTk
            N = bg_img.size[0]
            # crop the visible part of the preview (preview pixel = map area / N units, y flipped)
            k = N / view.area
            x0, x1 = view.ox * k, (view.ox + size / view.scale) * k
            y1, y0 = N - view.oy * k, N - (view.oy + size / view.scale) * k
            crop = bg_img.crop((int(x0), int(y0), max(int(x0) + 1, int(x1)), max(int(y0) + 1, int(y1))))
            state['img'] = ImageTk.PhotoImage(crop.resize((size, size)))
            cv.create_image(0, 0, image=state['img'], anchor='nw')
        x_lo, y_lo = view.to_units(0, size); x_hi, y_hi = view.to_units(size, 0)
        for i, b in enumerate(blds):
            pts = b['pts']
            if not pts: continue
            if max(p[0] for p in pts) < x_lo or min(p[0] for p in pts) > x_hi or \
               max(p[1] for p in pts) < y_lo or min(p[1] for p in pts) > y_hi: continue
            ov = edits['buildings'].get(b['id'])
            col = COLORS.get(b['kind'], '#e06050')
            if ov and ov.get('kind') == 'none': col = '#ff2020'
            on = i == state['sel'] or i in state['multi']
            if view.scale * 40 > 6:                    # zoomed in: the game models as they stand (2D preview)
                for pg in b.get('polys', []):
                    cv.create_polygon([c for p in pg for c in view.to_px(*p)], outline='#40e0ff', fill='', dash=(3, 2))
            width = 3 if on else (2 if ov else 1)
            outline = '#ffffff' if on else ('#00ff60' if ov else col)
            fillc = '#802020' if (ov and ov.get('kind') == 'none') else col
            cv.create_polygon([c for p in pts for c in view.to_px(*p)], outline=outline, fill=fillc,
                              stipple='gray50' if not on else 'gray75', width=width)
        for z in edits['zones']:
            zx, zy = proj.to_units(z['lat'], z['lon'])
            px, py = view.to_px(zx, zy); r = z['radius_m'] * proj.upm * view.scale
            cv.create_oval(px - r, py - r, px + r, py + r, outline='#ff3030', width=3, dash=(6, 4))
            cv.create_text(px, py - r - 8, text=T('Povstalci'), fill='#ff6060', font=('Segoe UI', 9, 'bold'))
        if state.get('rect'):
            (ax, ay), (bx, by) = state['rect']
            cv.create_rectangle(ax, ay, bx, by, outline='#ffff00', dash=(5, 3), width=2)
        if state.get('zdraw'):
            (px, py), r = state['zdraw'], state.get('zr', 0)
            cv.create_oval(px - r, py - r, px + r, py + r, outline='#ffff00', width=2)
        for i, (sx, sy) in enumerate(data.get('starts', [])):
            px, py = view.to_px(sx, sy)
            cv.create_oval(px - 10, py - 10, px + 10, py + 10, fill='#3070ff', outline='white', width=2)
            cv.create_text(px, py, text=str(i + 1), fill='white', font=('Segoe UI', 9, 'bold'))
        for i, b in enumerate(edits['bases']):
            px, py = view.to_px(*proj.to_units(b['lat'], b['lon']))
            sel = i == state['base']
            cv.create_rectangle(px - 11, py - 11, px + 11, py + 11, fill='#c01010', outline='#ffff00' if sel else 'white', width=3 if sel else 2)
            cv.create_text(px, py, text='P', fill='white', font=('Segoe UI', 9, 'bold'))
        for b in data.get('bases', []):
            if b.get('fortress'):
                px, py = view.to_px(b['x'], b['y'])
                cv.create_rectangle(px - 13, py - 13, px + 13, py + 13, outline='#ff8000', width=3)
                cv.create_text(px, py, text='F', fill='#ff8000', font=('Segoe UI', 10, 'bold'))
        update_summary()

    # ---- building selection / editing
    def select_building(i):
        state['sel'] = i
        if i is None:
            info.config(text=T('nič nie je vybrané')); draw(); return
        b = blds[i]
        ov = edits['buildings'].get(b['id'], {})
        fit = b.get('fit', 0)
        fit_t = T('sedí') if 0.75 <= fit <= 1.25 else (T('pretŕča') if fit > 1.25 else T('menší ako pôdorys'))
        info.config(text=f"OSM {b['id'] or '?'}   •   " + T('teraz') + f": {', '.join(b['models']) or '-'}\n"
                         + T('v realite') + f" {b['floors']} " + T('posch.') + '   •   ' + T('v hre') +
                         f" ~{b.get('game_floors', 0)} " + T('posch.') + '   •   ' + T('strecha') + ': ' +
                         T('šikmá' if b.get('roof') == 'pitched' else 'rovná') + '\n' + T('pôdorys') +
                         f': {fit * 100:.0f} % ({fit_t})' + (f"\n{len(state['multi'])} " + T('označených budov')
                                                             if len(state['multi']) > 1 else ''))
        k = ov.get('kind', 'auto')
        state['loading'] = True
        kind.set(next((T(l) for l, v in KINDS if v == k), kind_labels[0]))
        floors.set(str(ov.get('levels', 0)))
        stack.set(str(ov.get('stack', 0)))
        state['loading'] = False
        draw()

    def store_building(*_):
        i = state['sel']
        if state['loading'] or i is None: return
        k = next((v for l, v in KINDS if T(l) == kind.get()), 'auto')
        try: lv = int(floors.get())
        except ValueError: lv = 0
        try: st = max(0, min(6, int(stack.get())))
        except ValueError: st = 0
        for j in (state['multi'] | {i}):                # every marked building at once
            if not blds[j]['id']: continue
            if k == 'auto' and lv <= 0 and st <= 0:
                edits['buildings'].pop(blds[j]['id'], None)
            else:
                d = {'kind': k}
                if lv > 0: d['levels'] = lv
                if st > 0: d['stack'] = st
                edits['buildings'][blds[j]['id']] = d
        draw()
    kind.trace_add('write', store_building)
    floors.trace_add('write', store_building)
    stack.trace_add('write', store_building)

    # ---- bases
    def select_base(i):
        state['base'] = i
        if i is None:
            base_info.config(text=T('klikni na mapu v režime základní')); draw(); return
        b = edits['bases'][i]
        base_info.config(text=T('Základňa') + f' {i + 1}')
        state['loading'] = True
        for k, _ in BASE_FIELDS: bvars[k].set(str(b.get(k, 0)))
        state['loading'] = False
        draw()

    def store_base(*_):
        i = state['base']
        if state['loading'] or i is None or i >= len(edits['bases']): return
        for k, _ in BASE_FIELDS:
            try: edits['bases'][i][k] = max(0, int(bvars[k].get()))
            except ValueError: pass
        edits['bases_auto'] = False
    for v in bvars.values(): v.trace_add('write', store_base)

    def nearest_base(px, py):
        best = None
        for i, b in enumerate(edits['bases']):
            bx, by = view.to_px(*proj.to_units(b['lat'], b['lon']))
            d = math.hypot(bx - px, by - py)
            if d < 18 and (best is None or d < best[0]): best = (d, i)
        return None if best is None else best[1]

    # ---- mouse
    def zone_at(px, py):
        best = None
        for i, z in enumerate(edits['zones']):
            zx, zy = view.to_px(*proj.to_units(z['lat'], z['lon']))
            d = math.hypot(zx - px, zy - py)
            if d <= z['radius_m'] * proj.upm * view.scale + 6 and (best is None or d < best[0]): best = (d, i)
        return None if best is None else best[1]

    def zmove(e):
        if mode.get() == 'bld' and state.get('rect'):
            state['rect'] = (state['rect'][0], (e.x, e.y)); draw(); return
        if mode.get() != 'zone' or not state.get('zdraw'): return
        x0, y0 = state['zdraw']; state['zr'] = math.hypot(e.x - x0, e.y - y0); draw()

    def zrelease(e):
        if mode.get() == 'bld' and state.get('rect'):
            (ax, ay), (bx, by) = state['rect']; state['rect'] = None
            if abs(bx - ax) + abs(by - ay) > 8:          # a drawn rectangle: mark every building inside it
                x0, y0 = view.to_units(min(ax, bx), max(ay, by)); x1, y1 = view.to_units(max(ax, bx), min(ay, by))
                hit = {i for i, b in enumerate(blds) if b['pts'] and
                       x0 <= sum(p[0] for p in b['pts']) / len(b['pts']) <= x1 and
                       y0 <= sum(p[1] for p in b['pts']) / len(b['pts']) <= y1}
                state['multi'] = (state['multi'] | hit) if (e.state & 4) else hit
                select_building(min(state['multi']) if state['multi'] else None)
            return
        if mode.get() != 'zone' or not state.get('zdraw'): return
        (x0, y0), r = state['zdraw'], state.get('zr', 0)
        state['zdraw'] = None; state['zr'] = 0
        if r >= 8:
            lat, lon = proj.from_units(*view.to_units(x0, y0))
            if edits.get('zones_auto'): edits['zones'] = []      # the first drawn circle replaces the automatic one
            edits['zones'].append(dict(lat=float(lat), lon=float(lon), radius_m=float(r / view.scale / proj.upm)))
            edits['zones_auto'] = False
        draw()

    def click(e):
        if mode.get() == 'zone':
            state['zdraw'] = (e.x, e.y); state['zr'] = 0; return
        if mode.get() == 'base':
            i = nearest_base(e.x, e.y)
            if i is None:
                ux, uy = view.to_units(e.x, e.y)
                lat, lon = proj.from_units(ux, uy)
                edits['bases'].append(dict(lat=float(lat), lon=float(lon), buildings=2, defenses=4, infantry=6, vehicles=3))
                edits['bases_auto'] = False
                i = len(edits['bases']) - 1
            select_base(i)
        else:
            ux, uy = view.to_units(e.x, e.y)
            i = find_building(blds, ux, uy)
            if i is None: i = find_building(blds, ux, uy, tol=6 / view.scale)
            state['rect'] = ((e.x, e.y), (e.x, e.y))
            if e.state & 4:                              # Ctrl: add / remove one building
                if i is not None: state['multi'] ^= {i}
                select_building(i if i in state['multi'] else (min(state['multi']) if state['multi'] else None))
            else:
                state['multi'] = {i} if i is not None else set()
                select_building(i)

    def rpress(e):
        state['drag'] = (e.x, e.y); state['moved'] = False

    def rmove(e):
        if not state['drag']: return
        x0, y0 = state['drag']
        if abs(e.x - x0) + abs(e.y - y0) > 3: state['moved'] = True
        view.pan(e.x - x0, e.y - y0); state['drag'] = (e.x, e.y); draw()

    def rrelease(e):
        if not state['moved'] and mode.get() == 'zone':
            i = zone_at(e.x, e.y)
            if i is not None:
                edits['zones'].pop(i); edits['zones_auto'] = False; draw()
        if not state['moved'] and mode.get() == 'base':
            i = nearest_base(e.x, e.y)
            if i is not None:
                edits['bases'].pop(i); edits['bases_auto'] = False; select_base(None)
        state['drag'] = None

    def motion(e):
        ux, uy = view.to_units(e.x, e.y)
        i = find_building(blds, ux, uy)
        if i is None: return
        b = blds[i]
        hover.config(text=f"{', '.join(b['models']) or T('bez modelu')}   •   " + T('v realite') + f" {b['floors']} " +
                          T('posch.') + '   •   ' + T('v hre') + f" ~{b.get('game_floors', 0)} " + T('posch.') + '   •   ' +
                          T('šikmá' if b.get('roof') == 'pitched' else 'rovná') + ' ' + T('strecha'))
    cv.bind('<Motion>', motion)
    cv.bind('<ButtonPress-1>', click)
    cv.bind('<B1-Motion>', zmove); cv.bind('<ButtonRelease-1>', zrelease)
    cv.bind('<ButtonPress-3>', rpress); cv.bind('<B3-Motion>', rmove); cv.bind('<ButtonRelease-3>', rrelease)
    cv.bind('<MouseWheel>', lambda e: (view.zoom(1.25 if e.delta > 0 else 0.8, e.x, e.y), draw()))
    cv.bind('<Button-4>', lambda e: (view.zoom(1.25, e.x, e.y), draw()))
    cv.bind('<Button-5>', lambda e: (view.zoom(0.8, e.x, e.y), draw()))

    # ---- buttons
    def save():
        save_edits(name, edits)

    def regenerate():
        save(); win.destroy(); on_regenerate()

    def reset():
        edits['buildings'] = {}; edits['bases'] = None; edits['zones'] = None
        save_edits(name, {'buildings': {}, 'bases': None, 'zones': None})
        win.destroy()

    bb = ttk.Frame(side); bb.pack(fill='x', pady=(10, 0))
    ttk.Button(bb, text=T('PREGENEROVAŤ S ÚPRAVAMI'), style='Go.TButton', command=regenerate).pack(fill='x', pady=3)
    ttk.Button(bb, text=T('Uložiť úpravy'), command=save).pack(fill='x', pady=3)
    ttk.Button(bb, text=T('Zmazať všetky úpravy tejto mapy'), command=reset).pack(fill='x', pady=3)
    ttk.Label(side, text=T('Úpravy sa ukladajú k názvu mapy a použijú sa pri každom ďalšom generovaní tej istej mapy.'),
              style='Dim.TLabel', wraplength=440).pack(anchor='w', pady=6)
    win.protocol('WM_DELETE_WINDOW', lambda: (save(), win.destroy()))
    draw()
    return win
