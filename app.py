"""Zero Hour Earth - generates real-world Zero Hour skirmish maps from OpenStreetMap + elevation data."""

APP_NAME = 'Zero Hour Earth'
VERSION = '0.53.5'
import math, os, random, re, subprocess, sys, threading, traceback

import catalog, editor, generator, online, preflight, tr
from tr import T
from geo import Proj
from g_invasion import INVASION_NUMBERS, INVASION_ROSTERS

HERE = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(HERE, 'template.map')
# 1 game unit = 0.25 m (calibrated on civilian buildings), 1 tile = 10 units
M_PER_TILE_1TO1 = 2.5
SCALES = {'1:1  (budovy a cesty v skutočnej veľkosti)': 1.0,
          '1:2  (2x väčšia oblasť, budovy zmenšené)': 2.0,
          '1:4  (4x väčšia oblasť, budovy zmenšené)': 4.0}
MIN_TILES, MAX_TILES, WARN_TILES = 120, 2000, 1200      # 1:1 up to 5 x 5 km
EXP_MAX_TILES = 2000          # experimental huge maps
CLASSIC_MAX_TILES = 1000      # classic 32-bit game: max 2.5 x 2.5 km at 1:1
LAYOUT_OPTS = {'Automaticky (rohy, potom strany)': 'auto', 'Do kruhu': 'circle',
               'Dva tímy proti sebe (západ vs. východ)': 'teams',
               'Dva tímy proti sebe (sever vs. juh)': 'teams_ns', 'Ručne na mape (klikaním)': 'manual',
               'Zombie Survival (automaticky)': 'zsurv', 'Invasion (automaticky)': 'invasion'}
RES_OPTS = {'Málo': 'low', 'Normálne': 'normal', 'Veľa': 'high'}
ZOMBIE_PACING = {'Vlny': 'waves', 'Sústavne': 'continuous'}
TERRAIN_OPTS = {'Skutočné sklony (kopce 1:1, celkové stúpanie zmenšené)': 'slopes', 'Výrazný (kopce zvýraznené)': 'strong', 'Skutočná výška (vrcholy stlačené)': 'real',
                'Plne stlačený (celý terén pomerne)': 'fit'}


def tiles_for(km, scale):
    return int(round(km * 1000.0 / (M_PER_TILE_1TO1 * scale)))


def max_km(scale, huge=False):
    return (EXP_MAX_TILES if huge else MAX_TILES) * M_PER_TILE_1TO1 * scale / 1000.0


def safe_name(s):
    s = re.sub(r'[^A-Za-z0-9_]+', '_', s.encode('ascii', 'ignore').decode() or 'Earth').strip('_')
    return (s or 'Earth')[:40]


def map_folder_name(name, clearance=False, zombie=False, zsurv=False, zdef=False, light=False, invasion=False):
    """Folder / file name of the generated map: game modes go into a bracket, like "[ZOMBIE SURVIVAL] Poltar"."""
    tags = [t for t, on in (('CLEARANCE', clearance), ('ZOMBIE', zombie)) if on]
    if zsurv: tags = ['ZOMBIE SURVIVAL']
    if zdef: tags = ['ZOMBIE DEFENSE']
    if invasion: tags = ['INVASION']
    if light: tags.append('LIGHT')
    return f"[{'+'.join(tags)}] {name}" if tags else name


def free_map_name(maps_dir, base, **modes):
    """The first unused name: Poltar_2, Poltar_3, ... (the folder name counts, including the mode bracket)."""
    base = re.sub(r'_\d+$', '', safe_name(base))        # Poltar_2 -> Poltar_3, not Poltar_2_2
    n = 2
    while os.path.exists(os.path.join(maps_dir, map_folder_name(safe_name(f'{base}_{n}'), **modes))):
        n += 1
    return safe_name(f'{base}_{n}')


def run_generation(place, area_km, tiles, game_dir, maps_dir, log=print, center=None, map_name=None,
                   style='auto', terrain_mode='strong', target='gx', gx_dir='', aspect=1.0, **opts):
    big = [os.path.join(game_dir, 'INIZH.big'), os.path.join(game_dir, 'INI.big')]
    if not os.path.isfile(big[0]):
        raise RuntimeError('Nenašiel som INIZH.big. Vyber priečinok, kde je nainštalovaná hra Zero Hour.')
    log('Načítavam budovy z hry ...')
    cat = catalog.Catalog.load(big)
    log(f'  {len(cat.buildings)} budov, {len(cat.road_w)} typov ciest')
    # road limit: GeneralsX = its own Data\INI\GameData.ini (if raised there); classic game = always the stock 4000
    if target == 'gx':
        v = catalog.read_loose_limit(gx_dir) if gx_dir else None
        # nothing is written into the game any more: a changed GameData.ini stops other players from joining
        cat.road_segments = 10 ** 9 if v and v >= catalog.ROAD_LIMITS['MaxRoadSegments'] else (v or 4000)   # raised = no trimming
    else:
        cat.road_segments = 4000
        opts['light'] = True if opts.get('light') is None else opts['light']
    log('  limit ciest: ' + ('neobmedzené' if cat.road_segments >= 10 ** 9 else str(cat.road_segments)))

    m = re.match(r'^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$', place or '')
    if center:
        lat, lon = center; label = 'vybraná oblasť na mape'
    elif m:
        lat, lon, label = float(m.group(1)), float(m.group(2)), place
    else:
        log(f'Hľadám "{place}" ...')
        lat, lon, label = online.geocode(place)
    log(f'  {label}  ({lat:.5f}, {lon:.5f})')

    proj = Proj(lat, lon, area_km * 1000.0, tiles, aspect)
    log(f'Oblasť {area_km:g} × {area_km * aspect:g} km, {proj.mpc:.1f} m na dlaždicu')
    log('Sťahujem OpenStreetMap ...')
    osm = online.fetch_osm(proj.bbox(150), log)
    log(f'  cesty {len(osm.roads)}, budovy {len(osm.buildings)}, voda {len(osm.water)}, lesy {len(osm.forests)}')
    # places with (almost) no buildings in OSM (Iraq, much of Africa / Asia): footprints from satellite images
    s_, w_, n_, e_ = proj.bbox(0)
    inside = sum(1 for _, pts in osm.buildings if pts and s_ <= pts[0][0] <= n_ and w_ <= pts[0][1] <= e_)
    if inside < 30 and opts.get('sat_buildings', True):
        log(f'  OSM má v oblasti len {inside} budov - dopĺňam budovy zo satelitných snímok (Microsoft) ...')
        try:
            import msbuildings
            extra = msbuildings.fetch(proj.bbox(50), log)
            if extra:
                osm.buildings += extra
                log(f'  budovy spolu {len(osm.buildings)}')
        except Exception as ex:
            log(f'  satelitné budovy sa nepodarilo načítať: {ex}')
    # Slovakia: the real height of every building from the national laser scan (ZBGIS), read only over the map
    try:
        import lls
        if lls.in_slovakia(proj.lat0, proj.lon0):
            log('Výšky budov z laserového skenovania (ZBGIS) ...')
            hs = lls.heights([(t, r) for t, r in osm.buildings if t.get('_osm')], proj.bbox(0), log)
            n_h = 0
            for t, r in osm.buildings:
                h = hs.get(t.get('_osm'))
                if h and not t.get('height') and not t.get('building:levels'):
                    t['height'] = str(h); n_h += 1
            log(f'  výška z lasera priradená {n_h} budovám')
    except Exception as ex:
        log(f'  výšky budov z lasera sa nepodarilo načítať: {ex}')
    # ground colours from the satellite image (Sentinel-2 cloudless, EOX) - only the tiles over the map
    try:
        import sat
        log('Satelitná mapa terénu (Sentinel-2) ...')
        opts['sat_img'] = sat.fetch(proj.bbox((generator.BORDER + 2) * proj.mpc), log)
        log('  ' + sat.CREDIT)
    except Exception as ex:
        log(f'  satelitnú mapu sa nepodarilo načítať: {ex}')
    log('Sťahujem terén ...')
    dem = online.DemSampler(proj.bbox((generator.BORDER + 3) * proj.mpc), proj.mpc, log)

    name = safe_name(map_name or (place or 'Earth').split(',')[0])
    # game modes go into the name, like "[zombies] wheel of death": the skirmish list shows the file name
    base_name = name                     # editor choices belong to the place, whatever the game mode
    name = map_folder_name(name, opts.get('mode_clearance'), opts.get('mode_zombie'), opts.get('mode_zsurvival'),
                           opts.get('mode_zdefense'), opts.get('light'), opts.get('mode_invasion'))
    out_dir = os.path.join(maps_dir, name)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, name + '.map')
    seed = int(opts.pop('seed', 1) or 1)
    # choices made in the map editor for this map (buildings, insurgent bases)
    ed = editor.load_edits(base_name)
    if ed['buildings']:
        opts['building_overrides'] = ed['buildings']
        log(f'  úpravy z editora: {len(ed["buildings"])} budov')
    if ed.get('zones') is not None and not ed.get('zones_auto'):
        opts['ins_zones'] = ed['zones']
        log(f'  oblasti povstalcov z editora: {len(ed["zones"])}')
    if ed.get('bases') is not None and not ed.get('bases_auto'):
        opts['ins_bases'] = ed['bases']
        log(f'  základne povstalcov z editora: {len(ed["bases"])}')
    g = generator.generate(osm, dem, proj, tiles, cat, TEMPLATE, out, log=log, style=style, terrain_mode=terrain_mode,
                           seed=seed, **opts)
    log(f'Štýl budov: {g.style}')
    g.preview().save(os.path.join(out_dir, name + '_preview.png'))
    # skirmish-menu thumbnail: <map>.tga, 128x128, 24-bit uncompressed (same as World Builder writes)
    g.preview(clean=True).convert('RGB').resize((128, 128), resample=3).save(
        os.path.join(out_dir, name + '.tga'), compression=None)
    log('  náhľad pre menu hry: ' + name + '.tga')
    strp = os.path.join(out_dir, 'map.str')            # texts of the on-screen counters (zombie rounds)
    if g.game_mode:
        with open(strp, 'w', encoding='latin-1', newline='\r\n') as f:
            for k, v in getattr(g, 'map_strings', generator.Gen.ZOMBIE_STR).items():
                f.write(f'SCRIPT:{k}\n"{v}"\nEND\n\n')
    elif os.path.exists(strp):
        os.remove(strp)
    inip = os.path.join(out_dir, 'map.ini')            # Zombie mode: the shop Humvee
    if g.opt['mode_zombie']:
        with open(inip, 'w', encoding='latin-1', newline='\r\n') as f:
            f.write(generator.Gen.full_zombie_ini(bool(opts.get('mode_zsurvival') or opts.get('mode_zdefense')),
                                                  opts.get('zs_bounty', 10), bool(opts.get('zombie_growing')),
                                                  bool(opts.get('mode_zdefense')),
                                                  lifetime=not (opts.get('mode_clearance') and not (opts.get('mode_zsurvival') or opts.get('mode_zdefense'))),
                                                  central_tunnel=g.central_tunnel,
                                                  continuous=g.opt['zombie_pacing'] == 'continuous'))
    elif os.path.exists(inip):
        os.remove(inip)
    log('Hotovo: ' + out)
    log('Štatistika: ' + ', '.join(f'{k}={v}' for k, v in g.stats.items()))
    run_generation.last_stats = dict(g.stats)
    # data for the map editor
    os.makedirs(editor.EDIT_DIR, exist_ok=True)
    bg = os.path.join(editor.EDIT_DIR, base_name + '_bg.png')
    from PIL import Image
    pv = g.preview(clean=True).convert('RGB')
    if pv.size[0] != pv.size[1]:          # a strip map: the editor works on a square, the map in its south-west corner
        side = max(pv.size); sq = Image.new('RGB', (side, side), (20, 22, 26)); sq.paste(pv, (0, side - pv.size[1])); pv = sq
    pv.save(bg)
    run_generation.last_edit = dict(map_name=base_name, area=max(g.AX, g.AY), bg=bg, buildings=g.bld_info,
                                    bases=getattr(g, 'base_info', []), starts=list(g.starts),
                                    zones=getattr(g, 'zone_info', []),
                                    proj=(lat, lon, area_km * 1000.0, tiles, aspect))
    return out


SETTINGS = os.path.join(os.path.expanduser('~'), '.cncearth', 'settings.json')
ZOMBIE_MAX_TILES = 800      # Zombie mode: at most 800 x 800 tiles (2 x 2 km at 1:1) - hordes need a compact map

# difficulty presets: one click sets the insurgent and zombie options (Vlastná = keep what you set yourself)
STYLE_OPTS = {'Automaticky podľa krajiny': 'auto', 'Európa': 'eu', 'Blízky východ a Afrika': 'me', 'Ázia': 'asia'}
# map detail: one choice instead of a dozen boxes (the boxes stay under Krajina / Mesto -> they show what it set)
DETAIL = {'Plný': dict(tree_density=1.0, max_trees_v2=3900, bushes=True, willows=True, civilians=True, traffic=True,
                       farm_life=True, sounds=True, fences=True, footways=True, details=True, lamps=True, cars=True,
                       civ_amount=1.5),
          'Stredný': dict(tree_density=0.8, max_trees_v2=3000, bushes=True, willows=False, civilians=True, traffic=False,
                          farm_life=False, sounds=True, fences=True, footways=False, details=True, lamps=False, cars=False,
                          civ_amount=1.0),
          'LIGHT': dict(tree_density=0.6, max_trees_v2=2500, bushes=False, willows=False, civilians=True, traffic=False,
                        farm_life=False, sounds=False, fences=False, footways=False, details=False, lamps=False, cars=False,
                        civ_amount=0.5)}
TARGETS = {'GeneralsX': 'gx', 'Klasická (GeneralsOnline)': 'classic'}
# BIG UPDATE: built for GeneralsX only - everything that would make the map poorer is fixed on, not a choice
FORCED = dict(target='GeneralsX', detail='Plný', light=False, huge=True, bushes=True, willows=True, civilians=True,
              traffic=True, farm_life=True, sounds=True, sheds=True, fences=True, footways=True, train=True,
              details=True, lamps=True, cars=True, ai_paths=True, ins_localized=False)
SHAPES = {'Štvorec': 1.0, 'Obdĺžnik 1:2 (sever - juh)': 2.0, 'Obdĺžnik 1:3 (sever - juh)': 3.0,
          'Obdĺžnik 1:4 (sever - juh)': 4.0,
          'Obdĺžnik 2:1 (západ - východ)': 1 / 2, 'Obdĺžnik 3:1 (západ - východ)': 1 / 3,
          'Obdĺžnik 4:1 (západ - východ)': 1 / 4}
PRESETS = {'Vlastná': None,
           'Ľahká': dict(ins_difficulty=0, ins_cap_v2=800, patrols=3, outposts=1, ins_prod_interval=240,
                         ins_wave_size=8, ins_oil=6, ins_every=4, zombie_size=8, zombie_growth=4, zombie_break=25, zombie_start=15,
                         zs_start_money=3000, zs_respawn=45),
           'Stredná': dict(ins_difficulty=1, ins_cap_v2=2000, patrols=6, outposts=3, ins_prod_interval=120,
                           ins_wave_size=5, ins_oil=4, ins_every=3, zombie_size=12, zombie_growth=6, zombie_break=15, zombie_start=10,
                           zs_start_money=2000, zs_respawn=60),
           'Peklo': dict(ins_difficulty=2, ins_cap_v2=6000, patrols=15, outposts=6, ins_prod_interval=60,
                         ins_wave_size=4, ins_oil=2, ins_every=1, zombie_size=20, zombie_growth=10, zombie_break=10, zombie_start=5,
                         zs_start_money=1000, zs_respawn=90)}
INS_LEVELS = {'Ľahká (rebeli)': 1, 'Stredná (rebeli, RPG, teroristi)': 2, 'Ťažká (+ únoscovia áut)': 3}
APT_MODELS = {'Paneláky z obrázka: vežové + s balkónmi (mix)': 'panel_mix',
              'Hranatá vežová bytovka (EuroHighrise2)': 'EuroHighrise2',
              'Hranatý panelák (SovietBuilding)': 'SovietBuilding',
              'Panelák s balkónmi (StanSovietOffice01)': 'StanSovietOffice01',
              'Dlhý zdobený blok (SovietGovernmentBuildingSmall)': 'SovietGovernmentBuildingSmall',
              'Vysoký dom (EuroHighrise)': 'EuroHighrise',
              'Automaticky (mix podľa veľkosti)': 'auto'}
FORT_FACTIONS = {'GLA': 'GLA', 'Čína': 'China', 'USA': 'America'}
FORT_LEVELS = {'Slabá (6 veží, 6 jednotiek)': 1, 'Stredná (10 veží, 12 jednotiek)': 2, 'Silná (16 veží, 20 jednotiek)': 3}
SEASON_OPTS = {'Leto': 'summer', 'Jeseň (farebné listy)': 'autumn', 'Zima (zasnežené stromy)': 'winter'}
TIME_OPTS = {'Ráno': 1, 'Poludnie': 2, 'Večer': 3, 'Noc': 4}

def _originals():
    g = globals()
    return {k: dict(g[k]) for k in ('SCALES', 'LAYOUT_OPTS', 'RES_OPTS', 'TERRAIN_OPTS', 'APT_MODELS', 'FORT_FACTIONS',
                                    'FORT_LEVELS', 'SEASON_OPTS', 'TIME_OPTS', 'INS_LEVELS', 'SHAPES', 'STYLE_OPTS', 'ZOMBIE_PACING')}


_ORIG = None


# colours: dark military look, orange accent like the Generals menus
BG, PANEL, FIELD, FG, DIM, ACC, ACC2 = '#121519', '#1a1f24', '#2a3037', '#e8e8e8', '#98a2ac', '#f0a020', '#ffbf40'
CARD, BORDER = '#222830', '#2f363f'


def gui():
    import json
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import mappicker

    if sys.platform == 'win32':
        # sharp on 4K / high-DPI screens: without this Windows renders the window at 96 DPI and blows it up (blurry)
        try:
            import ctypes
            try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception: ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    root = tk.Tk()
    try:
        DPI = float(root.winfo_fpixels('1i'))
    except Exception:
        DPI = 96.0
    SC = max(1.0, DPI / 96.0)
    try: root.tk.call('tk', 'scaling', DPI / 72.0)
    except Exception: pass
    px = lambda n: int(round(n * SC))
    root.title(f'{APP_NAME} v{VERSION}' + T(' - generátor reálnych máp pre C&C Generals: Zero Hour'))
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    W_, H_ = min(px(1500), sw - 32), min(px(940), sh - 90)
    root.geometry(f'{W_}x{H_}+{max(0, (sw - W_) // 2)}+10')
    root.minsize(min(px(480), W_), min(px(305), H_))
    root.configure(bg=BG)
    try: root.iconbitmap(os.path.join(HERE, 'icon.ico'))
    except Exception: pass
    try:
        from PIL import Image, ImageTk
        ic = Image.open(os.path.join(HERE, 'icon.png')).convert('RGBA')
        root._icons = [ImageTk.PhotoImage(ic.resize((n, n), Image.LANCZOS)) for n in (16, 32, 48, 64, 128)]
        root.iconphoto(True, *root._icons)
    except Exception:
        pass
    st = ttk.Style(root)
    try: st.theme_use('clam')
    except Exception: pass
    st.configure('.', background=BG, foreground=FG, fieldbackground=FIELD, bordercolor='#333a42',
                 font=('Segoe UI', 10))
    st.configure('TFrame', background=BG)
    st.configure('Panel.TFrame', background=PANEL)
    st.configure('TLabel', background=PANEL, foreground=FG)
    st.configure('Top.TLabel', background=BG, foreground=FG)
    st.configure('Title.TLabel', background=BG, foreground=ACC, font=('Segoe UI', 20, 'bold'))
    st.configure('Sub.TLabel', background=BG, foreground=DIM, font=('Segoe UI', 9))
    st.configure('Dim.TLabel', background=PANEL, foreground=DIM, font=('Segoe UI', 9))
    st.configure('Info.TLabel', background=BG, foreground=ACC2, font=('Segoe UI', 10, 'bold'))
    st.configure('H.TLabel', background=PANEL, foreground=ACC, font=('Segoe UI', 11, 'bold'))
    st.configure('TCheckbutton', background=PANEL, foreground=FG)
    st.map('TCheckbutton', background=[('active', PANEL)], indicatorcolor=[('selected', ACC), ('!selected', FIELD)])
    st.configure('TButton', background=FIELD, foreground=FG, padding=6, borderwidth=0)
    st.map('TButton', background=[('active', '#3a424b'), ('disabled', '#22272c')])
    st.configure('Go.TButton', background=ACC, foreground='#111', font=('Segoe UI', 14, 'bold'), padding=12)
    st.map('Go.TButton', background=[('active', ACC2), ('disabled', '#6b5a2e')])
    st.configure('TNotebook', background=BG, borderwidth=0)
    st.configure('TNotebook.Tab', background=FIELD, foreground=DIM, padding=(9, 6), font=('Segoe UI', 9, 'bold'))
    st.map('TNotebook.Tab', background=[('selected', PANEL)], foreground=[('selected', ACC)])
    st.configure('TCombobox', fieldbackground=FIELD, background=FIELD, foreground=FG, arrowcolor=ACC)
    st.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', FG)])
    root.option_add('*TCombobox*Listbox.background', FIELD)
    root.option_add('*TCombobox*Listbox.foreground', FG)
    root.option_add('*TCombobox*Listbox.selectBackground', ACC)
    st.configure('TSpinbox', fieldbackground=FIELD, foreground=FG, arrowcolor=ACC)
    st.configure('TEntry', fieldbackground=FIELD, foreground=FG)
    st.configure('Horizontal.TProgressbar', background=ACC, troughcolor=FIELD, borderwidth=0, thickness=6)
    st.configure('Card.TFrame', background=CARD)
    st.configure('Card.TLabel', background=CARD, foreground=FG)
    st.configure('CardDim.TLabel', background=CARD, foreground=DIM, font=('Segoe UI', 9))
    st.configure('Card.TCheckbutton', background=CARD, foreground=FG)
    st.map('Card.TCheckbutton', background=[('active', CARD)], indicatorcolor=[('selected', ACC), ('!selected', FIELD)])
    st.configure('PageTitle.TLabel', background=PANEL, foreground=FG, font=('Segoe UI', 15, 'bold'))
    st.configure('Summary.TLabel', background=BG, foreground=FG, font=('Segoe UI', 10, 'bold'))
    st.configure('Status.TLabel', background=BG, foreground=DIM, font=('Segoe UI', 9))

    saved = {}
    try:
        saved = json.load(open(SETTINGS, encoding='utf-8'))
    except Exception:
        pass
    tr.LANG = saved.get('lang', 'en')
    global _ORIG
    if _ORIG is None: _ORIG = _originals()
    global SCALES, LAYOUT_OPTS, RES_OPTS, TERRAIN_OPTS, APT_MODELS, FORT_FACTIONS, FORT_LEVELS, SEASON_OPTS, TIME_OPTS, INS_LEVELS
    global SHAPES, STYLE_OPTS
    tdict = lambda name: {T(k): v for k, v in _ORIG[name].items()}
    SCALES, LAYOUT_OPTS, RES_OPTS, TERRAIN_OPTS = tdict('SCALES'), tdict('LAYOUT_OPTS'), tdict('RES_OPTS'), tdict('TERRAIN_OPTS')
    APT_MODELS, FORT_FACTIONS, FORT_LEVELS = tdict('APT_MODELS'), tdict('FORT_FACTIONS'), tdict('FORT_LEVELS')
    SEASON_OPTS, TIME_OPTS, INS_LEVELS = tdict('SEASON_OPTS'), tdict('TIME_OPTS'), tdict('INS_LEVELS')
    SHAPES, STYLE_OPTS = tdict('SHAPES'), tdict('STYLE_OPTS')
    V = {}

    def var(key, kind, default):
        val = FORCED[key] if key in FORCED else saved.get(key, default)
        v = {'s': tk.StringVar, 'i': tk.IntVar, 'd': tk.DoubleVar, 'b': tk.BooleanVar}[kind](value=val)
        v._forced = key in FORCED
        V[key] = (v, default); return v

    # ---------------- header
    head = ttk.Frame(root); head.pack(fill='x', padx=px(16), pady=(px(10), 0))
    # logo: globe + chrome ZERO HOUR + orange EARTH (logo.png, drawn by tools/make_logo.py), scaled to the screen DPI
    lh = px(66)
    logo = tk.Canvas(head, bg=BG, highlightthickness=0, bd=0, height=lh + px(4), width=px(620))
    try:
        from PIL import Image, ImageTk
        im_ = Image.open(os.path.join(HERE, 'logo.png')).convert('RGBA')
        im_ = im_.resize((int(im_.size[0] * lh / im_.size[1]), lh), Image.LANCZOS)
        logo._img = ImageTk.PhotoImage(im_)
        logo.create_image(0, px(2), image=logo._img, anchor='nw')
        logo.create_text(im_.size[0] + px(14), lh - px(8), anchor='sw', font=('Segoe UI', 9), fill=DIM, text=f'v{VERSION}')
        logo.config(width=im_.size[0] + px(70))
    except Exception:
        logo.create_text(0, px(30), text='ZERO HOUR EARTH', anchor='w', font=('Segoe UI Black', 24), fill=ACC)
    sub_ = ttk.Label(head, text=T('reálne mapy z OpenStreetMap pre C&C Generals: Zero Hour'), style='Sub.TLabel')
    sb = ttk.Frame(head); sb.pack(side='right')
    langs = {'English': 'en', 'Slovenčina': 'sk'}
    langv = tk.StringVar(value=next(k for k, v in langs.items() if v == tr.LANG))
    lang_box = ttk.Combobox(sb, textvariable=langv, values=list(langs), state='readonly', width=11)
    lang_box.pack(side='right', padx=(10, 0))
    restart = {'flag': False}
    def lang_changed(*_):
        saved['lang'] = langs.get(langv.get(), 'en')
        save_settings()
        restart['flag'] = True
        root.destroy()                       # gui() is started again in the new language
    lang_box.bind('<<ComboboxSelected>>', lang_changed)
    place = ttk.Entry(sb, width=22); place.insert(0, saved.get('place', 'Poltár')); place.pack(side='left', padx=6, ipady=3)
    search_btn = ttk.Button(sb, text=T('Nájsť na mape')); search_btn.pack(side='left')
    fav_btn = ttk.Button(sb, text='★', width=3); fav_btn.pack(side='left', padx=(4, 0))
    logo.pack(side='left', fill='x', expand=True)
    sub_.place(in_=logo, x=int(logo.cget('width')), rely=0.65)

    # accent line under the header: bright orange fading into the background
    line = tk.Canvas(root, bg=BG, highlightthickness=0, bd=0, height=max(2, px(2)))
    line.pack(fill='x', padx=px(16), pady=(px(6), px(4)))

    def paint_line(e=None):
        line.delete('all'); w = max(1, line.winfo_width()); n = 60
        for i in range(n):
            t = i / n
            r_, g_, b_ = (int(a * (1 - t) + b * t) for a, b in zip((240, 160, 32), (18, 21, 25)))
            line.create_rectangle(w * i / n, 0, w * (i + 1) / n + 1, px(2), outline='', fill=f'#{r_:02x}{g_:02x}{b_:02x}')
    line.bind('<Configure>', paint_line)
    logf = ttk.Frame(root); logf.pack(side='bottom', fill='x', padx=16, pady=(4, 10))
    body = ttk.Frame(root); body.pack(fill='both', expand=True, padx=16, pady=6)
    split = ttk.Panedwindow(body, orient='horizontal')
    split.pack(fill='both', expand=True)
    st.configure('Sash', sashthickness=px(9), background=FIELD)
    left, right = ttk.Frame(split), ttk.Frame(split)
    split.add(left, weight=1)
    split.add(right, weight=0)
    panel_width = var('panel_width', 'i', 680)

    def fit_panel(*_):
        width = split.winfo_width()
        if width <= 1:
            return
        right_width = min(max(px(285), px(panel_width.get())), width - px(120))
        split.sashpos(0, width - right_width - px(9))

    def remember_panel(_event):
        width = round((split.winfo_width() - split.sashpos(0) - px(9)) / SC)
        panel_width.set(min(1200, max(285, width)))

    panel_width.trace_add('write', fit_panel)
    split.bind('<Configure>', fit_panel)
    split.bind('<ButtonRelease-1>', remember_panel, add='+')
    root.after_idle(fit_panel)

    # ---------------- map
    km = var('km', 'd', 1.5)
    scale = var('scale', 's', list(SCALES)[0])
    shape = var('shape', 's', list(SHAPES)[0])
    aspect_of = lambda: SHAPES.get(shape.get(), next((v for k_, v in SHAPES.items() if T(k_) == shape.get()), 1.0))
    info = tk.StringVar()
    state = {'lat': saved.get('lat', 48.4316), 'lon': saved.get('lon', 19.7930)}
    sel_hooks = []                  # called whenever the selected area moves / changes size (pre-check, autosave)

    def sel_changed(lat=None, lon=None, k=None):
        if lat is not None: state['lat'], state['lon'] = lat, lon
        sc = SCALES.get(scale.get(), 1.0)
        try: k = float(km.get())
        except Exception: return
        asp = aspect_of()
        mk = max_km(sc, bool(V['huge'][0].get()) if 'huge' in V else False)
        huge_on = bool(V['huge'][0].get()) if 'huge' in V else False
        if not huge_on and any(k_ in V and V[k_][0].get() for k_ in ('mode_zombie', 'mode_zsurvival', 'mode_zdefense')):
            mk = min(mk, ZOMBIE_MAX_TILES * M_PER_TILE_1TO1 * sc / 1000)   # zombie hordes: smaller maps only
        tv = V['target'][0].get() if 'target' in V else 'GeneralsX'
        if tv not in ('GeneralsX', T('GeneralsX')):
            mk = min(mk, CLASSIC_MAX_TILES * M_PER_TILE_1TO1 * sc / 1000)   # the 32-bit classic game: smaller maps
        mk /= max(1.0, asp)                              # limit the longest side in either orientation
        if k > mk:
            km.set(mk); return
        t = tiles_for(k, sc)
        warn = '   ⚠ veľká mapa' if max(t, round(t * asp)) > WARN_TILES else \
               ('   ⚠ príliš malá' if min(t, round(t * asp)) < MIN_TILES else '')
        info.set(T(f"{k:g} × {k * asp:g} km   •   mapa {t} × {round(t * asp)} dlaždíc   •   1 dlaždica = {M_PER_TILE_1TO1 * sc:g} m   •   "
                 f"stred {state['lat']:.4f}, {state['lon']:.4f}{warn}"))
        for h in sel_hooks:
            try: h()
            except Exception: pass

    picker = mappicker.MapPicker(left, km, on_change=sel_changed, lat=state['lat'], lon=state['lon'], zoom=14,
                                 width=px(900), height=px(600))
    picker.pack(fill='both', expand=True)
    ttk.Label(left, textvariable=info, style='Info.TLabel').pack(anchor='w', pady=(6, 0))
    ttk.Label(left, text=T('ľavým ťahaj = posun   •   koliesko = zoom   •   klik = presuň výber   •   pravým ťahaj = nakresli oblasť'
                         '   •   v režime štartov: klik = hráč, pravý klik = zmaž'), style='Sub.TLabel').pack(anchor='w')

    # ---------------- pages: menu on the left, one page at a time, every page scrolls
    gobar = ttk.Frame(right); gobar.pack(side='bottom', fill='x')
    nav = tk.Frame(right, bg=BG); nav.pack(side='left', fill='y', padx=(0, 10))
    page_name = tk.StringVar()
    page_menu = ttk.Combobox(right, state='readonly', textvariable=page_name)
    content = ttk.Frame(right); content.pack(side='left', fill='both', expand=True)
    page_names = {}

    def arrange_nav(e):
        if e.width < px(610) or e.height < px(560):
            nav.pack_forget()
            if not page_menu.winfo_manager():
                page_menu.pack(side='top', fill='x', before=content, pady=(0, 4))
        else:
            page_menu.pack_forget()
            if not nav.winfo_manager():
                nav.pack(side='left', fill='y', padx=(0, 10), before=content)

    right.bind('<Configure>', arrange_nav)
    page_menu.bind('<<ComboboxSelected>>', lambda e: show_page(page_names[page_name.get()]))
    pages, nav_btns, scrollers = [], {}, []
    cur_page = {'f': None}

    class Tip:
        """Small hover tooltip (the ⓘ marks)."""
        def __init__(self, w, text):
            self.w, self.text, self.tw = w, text, None
            w.bind('<Enter>', self.show); w.bind('<Leave>', self.hide)

        def show(self, e=None):
            if self.tw: return
            x, y = self.w.winfo_rootx() + 18, self.w.winfo_rooty() + 18
            self.tw = tk.Toplevel(self.w); self.tw.wm_overrideredirect(True); self.tw.wm_geometry(f'+{x}+{y}')
            tk.Label(self.tw, text=self.text, bg='#2b3138', fg=FG, justify='left', wraplength=px(360), padx=px(10), pady=px(8),
                     font=('Segoe UI', 9), relief='flat', highlightthickness=1, highlightbackground=ACC).pack()

        def hide(self, e=None):
            if self.tw: self.tw.destroy(); self.tw = None

    def show_page(f):
        if cur_page['f'] is not None: cur_page['f']._outer.pack_forget()
        f._outer.pack(fill='both', expand=True); cur_page['f'] = f
        for p, b in nav_btns.items():
            on = p is f
            if on: page_name.set(p._title)
            b.config(bg=PANEL if on else BG, fg=ACC if on else DIM)
            b._bar.config(bg=ACC if on else BG)

    def _wheel(e):
        f = cur_page['f']
        for outer, cv in scrollers:
            if f is not None and outer is f._outer:
                cv.yview_scroll(int(-e.delta / 120) if e.delta else (-1 if getattr(e, 'num', 0) == 4 else 1), 'units')
    root.bind_all('<MouseWheel>', lambda e: _wheel(e) if str(e.widget).startswith(str(right)) else None, add='+')

    def tab(title, icon=''):
        outer = ttk.Frame(content, style='Panel.TFrame')
        cv = tk.Canvas(outer, bg=PANEL, highlightthickness=0, bd=0)
        sbar = ttk.Scrollbar(outer, orient='vertical', command=cv.yview)
        hbar = ttk.Scrollbar(outer, orient='horizontal', command=cv.xview)
        cv.configure(yscrollcommand=sbar.set, xscrollcommand=hbar.set)
        hbar.pack(side='bottom', fill='x')
        sbar.pack(side='right', fill='y'); cv.pack(side='left', fill='both', expand=True)
        f = ttk.Frame(cv, style='Panel.TFrame', padding=(16, 12))
        win = cv.create_window(0, 0, window=f, anchor='nw')
        def fit_page(_event=None):
            width = max(cv.winfo_width(), f.winfo_reqwidth())
            cv.itemconfigure(win, width=width)
            cv.configure(scrollregion=cv.bbox('all'))

        f.bind('<Configure>', fit_page)
        cv.bind('<Configure>', fit_page)
        scrollers.append((outer, cv))
        f.columnconfigure(0, weight=1); f._row = 0; f._outer = outer
        ttk.Label(f, text=f'{icon}  {T(title)}', style='PageTitle.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 8))
        f._row = 1
        f._canvas = cv
        f._title = f'{icon}  {T(title)}'
        page_names[f._title] = f
        page_menu.configure(values=tuple(page_names))
        # menu entry
        row_ = tk.Frame(nav, bg=BG); row_.pack(fill='x', pady=1)
        bar_ = tk.Frame(row_, bg=BG, width=4); bar_.pack(side='left', fill='y')
        b = tk.Label(row_, text=f' {icon}  {T(title)}', bg=BG, fg=DIM, anchor='w', padx=8, pady=9, cursor='hand2',
                     font=('Segoe UI', 10, 'bold'), width=13)
        b.pack(side='left', fill='x')
        b._bar = bar_
        b.bind('<Button-1>', lambda e, f=f: show_page(f))
        nav_btns[f] = b; pages.append(f)
        return f

    def section(page, title, advanced=False, open_=False):
        """A card on a page. advanced=True: folded under 'Pokročilé ▸' until clicked."""
        card = tk.Frame(page, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        card.grid(row=page._row, column=0, sticky='we', pady=(0, 10)); page._row += 1
        card.columnconfigure(0, weight=1)
        hdr = tk.Frame(card, bg=CARD); hdr.grid(row=0, column=0, sticky='we', padx=12, pady=(8, 4))
        body_ = ttk.Frame(card, style='Card.TFrame', padding=(12, 0, 12, 10))
        body_.columnconfigure(1, weight=1); body_._row = 0
        body_._canvas = page._canvas
        if not advanced:
            tk.Frame(hdr, bg=ACC, width=max(3, px(3)), height=px(14)).pack(side='left', padx=(0, px(8)))
            tk.Label(hdr, text=T(title).upper(), bg=CARD, fg=FG, font=('Segoe UI', 9, 'bold')).pack(side='left')
            body_.grid(row=1, column=0, sticky='we')
        else:
            lab = tk.Label(hdr, text='', bg=CARD, fg=DIM, font=('Segoe UI', 9, 'bold'), cursor='hand2')
            lab.pack(side='left')
            st_ = {'open': open_}

            def paint():
                lab.config(text=('▾  ' if st_['open'] else '▸  ') + T(title).upper())
                if st_['open']: body_.grid(row=1, column=0, sticky='we')
                else: body_.grid_remove()

            def toggle(e=None):
                st_['open'] = not st_['open']; paint()
            lab.bind('<Button-1>', toggle); paint()
        return body_

    def info_mark(parent, text):
        m = tk.Label(parent, text='ⓘ', bg=CARD, fg=ACC2, cursor='question_arrow', font=('Segoe UI', 10))
        Tip(m, T(text)); return m

    def row(f, label, widget, hint=None):
        lf = ttk.Frame(f, style='Card.TFrame')
        ttk.Label(lf, text=T(label), style='Card.TLabel').pack(side='left')
        if hint: info_mark(lf, hint).pack(side='left', padx=(5, 0))
        lf.grid(row=f._row, column=0, sticky='w', pady=5, padx=(0, 12))
        widget.grid(row=f._row, column=1, columnspan=2, sticky='we', pady=5); f._row += 1
        return lf, widget

    def note(f, text):
        label = ttk.Label(f, text=T(text), style='CardDim.TLabel', wraplength=px(470), justify='left')
        label.grid(row=f._row, column=0, columnspan=3, sticky='w', pady=(2, 4))
        f._row += 1
        f._canvas.bind('<Configure>', lambda e: label.configure(wraplength=max(px(165), min(px(470), e.width - px(60)))),
                       add='+')

    def combo(f, v, values):
        c = ttk.Combobox(f, textvariable=v, values=values, state='readonly')
        cur = v.get()
        if cur not in values:
            back = {en: sk for sk, en in tr.EN.items()}
            for alt in (T(cur), back.get(cur), T(back.get(cur, ''))):
                if alt in values:
                    v.set(alt); break
            else:
                v.set(values[0])
        return c

    numeric_commits = []

    def number_field(f, v, a, b, increment, width=7, display_scale=1):
        # Keep partially typed text separate from the validated setting.
        text = tk.StringVar(value=f'{v.get() * display_scale:g}')
        entry = ttk.Spinbox(f, from_=a * display_scale, to=b * display_scale, increment=increment * display_scale,
                            width=width, textvariable=text)
        is_int = isinstance(v, tk.IntVar)

        def sync(*_):
            text.set(f'{v.get() * display_scale:g}')

        def commit(_event=None):
            try:
                value = float(text.get().strip().replace(',', '.')) / display_scale
                if not math.isfinite(value) or (is_int and not value.is_integer()):
                    raise ValueError
                value = min(b, max(a, value))
                value = int(value) if is_int else value
                if value != v.get():
                    v.set(value)
            except ValueError:
                pass
            sync()

        v.trace_add('write', sync)
        entry.configure(command=commit)
        entry.bind('<Return>', commit)
        entry.bind('<FocusOut>', commit)
        numeric_commits.append(commit)
        return entry

    def slider(f, v, a, b, res):
        fr = ttk.Frame(f, style='Card.TFrame')
        number_field(fr, v, a, b, res).pack(side='right', padx=(5, 0))
        tk.Scale(fr, variable=v, from_=a, to=b, resolution=1 if isinstance(v, tk.IntVar) else -1,
                 digits=5, showvalue=False, orient='horizontal', bg=CARD, fg=ACC2,
                 troughcolor='#3c4651', highlightthickness=0, activebackground=ACC2, sliderrelief='flat',
                 sliderlength=px(16), width=px(12), bd=0, length=px(100)).pack(side='left', fill='x', expand=True)
        return fr

    def check(f, text, v, hint=None):
        if getattr(v, '_forced', False): return       # always on - no switch to make the map poorer
        cf = ttk.Frame(f, style='Card.TFrame')
        ttk.Checkbutton(cf, text=T(text), variable=v, style='Card.TCheckbutton').pack(side='left')
        if hint: info_mark(cf, hint).pack(side='left', padx=(5, 0))
        cf.grid(row=f._row, column=0, columnspan=3, sticky='w', pady=3); f._row += 1

    def button(f, text, cmd):
        ttk.Button(f, text=T(text), command=cmd).grid(row=f._row, column=0, columnspan=3, sticky='w', pady=6); f._row += 1

    # --- Herné módy
    t0 = tab('Herné módy', '★')
    m_clear = var('mode_clearance', 'b', False)
    m_zomb = var('mode_zombie', 'b', False)
    m_black = var('black_map', 'b', True)

    def mode_card(parent, v, icon, title, desc):
        fr = tk.Frame(parent, bg=CARD, highlightthickness=2, highlightbackground=BORDER, cursor='hand2')
        fr.grid(row=parent._row, column=0, sticky='we', pady=(0, 8)); parent._row += 1
        fr.columnconfigure(1, weight=1)
        ic = tk.Label(fr, text=icon, bg=CARD, fg=FG, font=('Segoe UI Symbol', 24)); ic.grid(row=0, column=0, rowspan=2, padx=12, pady=10)
        tl = tk.Label(fr, text=T(title), bg=CARD, fg=FG, font=('Segoe UI', 12, 'bold'), anchor='w')
        tl.grid(row=0, column=1, sticky='we', pady=(10, 0))
        dl = tk.Label(fr, text=T(desc), bg=CARD, fg=DIM, font=('Segoe UI', 9), anchor='w', justify='left', wraplength=px(360))
        dl.grid(row=1, column=1, sticky='we', pady=(0, 10))
        sw = tk.Label(fr, text='', bg=CARD, font=('Segoe UI', 9, 'bold'), width=5, pady=3)
        sw.grid(row=0, column=2, rowspan=2, padx=12)

        def paint(*_):
            on = bool(v.get())
            fr.config(highlightbackground=ACC if on else BORDER)
            tl.config(fg=ACC if on else FG)
            sw.config(text=T('ZAP') if on else T('VYP'), bg=ACC if on else FIELD, fg='#111' if on else DIM)

        def toggle(e=None): v.set(not bool(v.get()))
        for w in (fr, ic, tl, dl, sw): w.bind('<Button-1>', toggle)
        v.trace_add('write', paint); paint()
        return fr, dl

    def segmented(parent, var_, options, on_pick=None, grid=None):
        """A row of buttons, one of them lit (like the difficulty)."""
        f_ = tk.Frame(parent, bg=CARD)
        btns = {}

        def paint_(*_):
            for k_, b_ in btns.items():
                on = var_.get() in (k_, T(k_))
                b_.config(bg=ACC if on else FIELD, fg='#111' if on else FG)
        for k_ in options:
            b_ = tk.Label(f_, text=T(k_), bg=FIELD, fg=FG, padx=12, pady=5, cursor='hand2', font=('Segoe UI', 9, 'bold'))
            b_.pack(side='left', padx=(0, 4))
            b_.bind('<Button-1>', lambda e, k_=k_: (var_.set(T(k_)), on_pick and on_pick(k_)))
            btns[k_] = b_
        var_.trace_add('write', paint_); paint_()
        return f_

    target = var('target', 's', 'GeneralsX')
    detail = var('detail', 's', 'Plný')
    light = var('light', 'b', False)
    spin_refs = {}

    def target_key():
        return next((k for k in TARGETS if T(k) == target.get() or k == target.get()), 'GeneralsX')

    def detail_key():
        return next((k for k in DETAIL if T(k) == detail.get() or k == detail.get()), 'Plný')

    def apply_detail(k=None):
        k = k or detail_key()
        for key, val in DETAIL[k].items():
            if key in V: V[key][0].set(val)
        light.set(k == 'LIGHT')

    target_hooks = []                    # filled later (folders / road limit on the Export page, area size)

    def apply_target(k=None):
        k = k or target_key()
        for h in target_hooks:
            try: h(TARGETS[k])
            except Exception: pass
        sp = spin_refs.get('players')
        if TARGETS[k] == 'classic':
            detail.set(T('LIGHT')); apply_detail('LIGHT')
            if 'players' in V and int(V['players'][0].get() or 1) > 8: V['players'][0].set(8)
            if sp: sp.config(to=8)
        else:
            if detail_key() == 'LIGHT':
                detail.set(T('Plný')); apply_detail('Plný')
            if sp: sp.config(to=32)
    target_hooks.append(lambda tg: sel_changed())   # re-check the area size for the new target
    mode_card(t0, m_clear, '⌖', 'Clearance', 'Vyčisti mesto od povstalcov v budovách. Počítadlo ukazuje, koľko ich ešte '
                                             'ostáva; keď padne posledný, vyhrali ste.')
    m_zs = var('mode_zsurvival', 'b', False)
    m_zd = var('mode_zdefense', 'b', False)
    m_invasion = var('mode_invasion', 'b', False)
    # one Zombie card, the variant below it (the three switches underneath stay as they were for the generator)
    ZKINDS = {'Klasický': 'Kolá hord terroristov z okrajov mapy, ďalšia vlna až keď zničíš všetkých. Máš základňu '
                          'a k tomu Humvee obchod s vojakmi. Dá sa spojiť s Clearance.',
              'Survival': 'Bez základne: každý má len vozidlo-obchod (Humvee / Crawler / Bus). Peniaze za každého '
                          'zabitého zombíka, zombíci s vlnami rastú, po zničení vozidla Respawn Ambush.',
              'Defense': 'Ako Survival, ale v strede stojí atómové silo - keď ho zničia, prehrali ste. Zombíci idú naň, '
                         'hráča do ~100 m napadnú. Pešiak ho môže obsadiť a jeho atómovka je potom tvoja.'}
    z_on = tk.BooleanVar(value=bool(m_zomb.get() or m_zs.get() or m_zd.get()))
    z_kind = tk.StringVar(value=T('Defense' if m_zd.get() else 'Survival' if m_zs.get() else 'Klasický'))
    zfr, zdl = mode_card(t0, z_on, '☣', 'Zombie', ZKINDS['Klasický'])
    zsel = segmented(zfr, z_kind, list(ZKINDS))
    zsel.grid(row=2, column=1, sticky='w', pady=(0, 6))
    note_z = tk.Label(zfr, text=T('V lobby pridaj 1 AI hráča s frakciou GLA - je to pán zombíkov.'), bg=CARD, fg=ACC2,
                      font=('Segoe UI', 9), anchor='w')
    note_z.grid(row=3, column=1, sticky='w', pady=(0, 10))
    _zsync = {'busy': False}

    def zkind_key():
        return next((k for k in ZKINDS if T(k) == z_kind.get() or k == z_kind.get()), 'Klasický')

    def z_sync(*_):
        if _zsync['busy']: return
        _zsync['busy'] = True
        k = zkind_key(); on = bool(z_on.get())
        if on and m_invasion.get(): m_invasion.set(False)
        zdl.config(text=T(ZKINDS[k]))
        m_zomb.set(on and k == 'Klasický'); m_zs.set(on and k == 'Survival'); m_zd.set(on and k == 'Defense')
        if on and k != 'Klasický' and m_clear.get(): m_clear.set(False)     # Survival / Defense stand alone
        _zsync['busy'] = False
    z_on.trace_add('write', z_sync); z_kind.trace_add('write', z_sync)

    def clear_on(*_):
        if m_clear.get() and m_invasion.get():
            m_invasion.set(False)
        if m_clear.get() and z_on.get() and zkind_key() != 'Klasický':
            z_kind.set(T('Klasický'))
    m_clear.trace_add('write', clear_on)

    def zs_layout(*_):
        if 'layout' not in V: return
        lv = V['layout'][0]
        desired = 'invasion' if m_invasion.get() else 'zsurv' if m_zs.get() or m_zd.get() else 'auto'
        if desired != 'auto' or LAYOUT_OPTS.get(lv.get()) in ('zsurv', 'invasion'):
            name = next(k for k, value in LAYOUT_OPTS.items() if value == desired)
            if lv.get() != name: lv.set(name)
    m_zs.trace_add('write', zs_layout)
    m_zd.trace_add('write', zs_layout)
    mode_card(t0, m_invasion, '⚑', 'Invasion', 'Chráňte spoločné veliteľstvo v strede. Po príprave začnú sústavné útoky vybranej frakcie.')
    inv_panel = section(t0, 'Nastavenia Invasion')
    inv_faction = var('invasion_faction', 's', 'China')
    row(inv_panel, 'Útočiaca frakcia', combo(inv_panel, inv_faction, list(INVASION_ROSTERS)))
    inv_air = var('invasion_air', 'b', True)
    air_widgets = row(inv_panel, 'Letecké nájazdy', ttk.Checkbutton(inv_panel, variable=inv_air),
                      'Stíhačky USA a China prídu od úrovne 5. GLA útočí pozemnou technikou.')
    inv_labels = {
        'invasion_prep': 'Čas na stavbu (s)',
        'invasion_phase': 'Ďalšia úroveň po (s)',
        'invasion_interval': 'Prvý interval útokov (s)',
        'invasion_size': 'Počiatočná veľkosť skupiny',
        'invasion_limit': 'Maximum útočníkov naraz',
        'invasion_money': 'Štartovacie peniaze na hráča',
        'invasion_income': 'Príjem na hráča za minútu',
    }
    inv_vars = {}
    for key, (default, minimum, maximum, step) in INVASION_NUMBERS.items():
        inv_vars[key] = v = var(key, 'i', default)
        row(inv_panel, inv_labels[key], slider(inv_panel, v, minimum, maximum, step))
    note(inv_panel, 'Na karte Hráči zadaj počet obrancov + 1. V lobby daj ľudí do jedného tímu a obyčajnú GLA AI na posledný štart do iného tímu.')

    def invasion_sync(*_):
        if m_invasion.get():
            if z_on.get(): z_on.set(False)
            if m_clear.get(): m_clear.set(False)
            inv_panel.master.grid()
        else:
            inv_panel.master.grid_remove()
        zs_layout()

    def invasion_air_sync(*_):
        for widget in air_widgets:
            widget.grid_remove() if inv_faction.get() == 'GLA' else widget.grid()

    m_invasion.trace_add('write', invasion_sync)
    inv_faction.trace_add('write', invasion_air_sync)
    if m_invasion.get(): invasion_sync()
    else:
        z_sync()
        invasion_sync()
    invasion_air_sync()
    mode_card(t0, m_black, '◐', 'Čierna mapa', 'Mapa je čierna a odkrýva sa až tam, kam prídeš. Platí pre každú mapu.')

    s = section(t0, 'Obtiažnosť')
    preset = var('preset', 's', 'Vlastná')

    def preset_key():
        return next((k for k in PRESETS if T(k) == preset.get() or k == preset.get()), 'Vlastná')

    def paint_seg():
        for k_, b_ in seg.items():
            on = k_ == preset_key()
            b_.config(bg=ACC if on else FIELD, fg='#111' if on else FG)

    def apply_preset(*_):
        p = PRESETS.get(preset_key())
        paint_seg()
        if not p: return
        for k, v in p.items():
            if k == 'ins_difficulty' and k in V:
                V[k][0].set(list(INS_LEVELS)[v])
            elif k in V:
                V[k][0].set(v)
    segf = tk.Frame(s, bg=CARD); segf.grid(row=s._row, column=0, columnspan=3, sticky='w', pady=(2, 4)); s._row += 1
    seg = {}
    for k in PRESETS:
        b_ = tk.Label(segf, text=T(k), bg=FIELD, fg=FG, padx=14, pady=6, cursor='hand2', font=('Segoe UI', 10, 'bold'))
        b_.pack(side='left', padx=(0, 4))
        b_.bind('<Button-1>', lambda e, k=k: (preset.set(T(k)), apply_preset()))
        seg[k] = b_
    paint_seg()
    note(s, 'Ľahká / Stredná / Peklo nastaví naraz zombíkov, ZombieSurvival aj povstalcov - hodnoty uvidíš nižšie '
            'a na karte Nepriateľ. Vlastná = nechá, čo si nastavíš sám.')
    s = section(t0, 'Zombíci')
    pacing_options = {T(k): v for k, v in ZOMBIE_PACING.items()}
    z_pacing = var('zombie_pacing', 's', T('Vlny'))
    row(s, 'Príchod zombíkov', combo(s, z_pacing, list(pacing_options)),
        'Sústavne: bez pauzy na vyčistenie mapy, tempo a náročnosť postupne rastú.')
    z_limit = var('zombie_live_limit', 'i', generator.ZOMBIE_DEFAULT_LIVE_LIMIT)
    limit_row = row(s, 'Najviac živých zombíkov',
                    number_field(s, z_limit, 1, generator.ZOMBIE_MAX_LIVE_LIMIT, 1),
                    'Pri dosiahnutí limitu sa ďalší zombíci objavia až po uvoľnení miesta. Vyšší limit môže spomaliť hru.')
    z_size = var('zombie_size', 'i', 12)
    row(s, 'Počiatočný počet', slider(s, z_size, 1, 80, 1))
    z_grow = var('zombie_growth', 'i', 6)
    row(s, 'Prírastok obtiažnosti', slider(s, z_grow, 0, 40, 1),
        'Pri vlnách pridáva zombíkov do každej vlny. Pri sústavnom príchode zrýchľuje tempo.')
    z_start = var('zombie_start', 'i', 10)
    row(s, 'Začiatok po (s)', slider(s, z_start, 0, 300, 10))
    z_break = var('zombie_break', 'i', 15)
    pause_row = row(s, 'Pauza medzi vlnami (s)', slider(s, z_break, 5, 120, 5))

    def show_pacing_settings(*_):
        continuous = pacing_options.get(z_pacing.get()) == 'continuous'
        for widget in limit_row if continuous else pause_row:
            widget.grid()
        for widget in pause_row if continuous else limit_row:
            widget.grid_remove()

    z_pacing.trace_add('write', show_pacing_settings)
    show_pacing_settings()
    z_growing = var('zombie_growing', 'b', False)
    check(s, 'Rastúci zombíci aj v klasickom Zombie', z_growing,
          'ako v ZombieSurvival: s vlnami väčší, rýchlejší a s viac HP, normálni ostávajú väčšinou (v ZombieSurvival vždy)')
    s = section(t0, 'ZombieSurvival')
    zs_money = var('zs_start_money', 'i', 2000)
    row(s, 'Štartovacie peniaze (Survival)', slider(s, zs_money, 0, 20000, 500))
    zd_money = var('zd_start_money', 'i', 5000)
    row(s, 'Štartovacie peniaze (Defense)', slider(s, zd_money, 0, 50000, 500),
        'v Defense treba hneď brániť silo - viac peňazí na začiatok')
    zs_resp = var('zs_respawn', 'i', 60)
    row(s, 'Respawn po (s)', slider(s, zs_resp, 10, 300, 5),
        'Čas po strate vozidla, kým sa sprístupní Respawn Ambush. Klikni na voľné miesto v aréne.')
    zs_bounty = var('zs_bounty', 'i', 10)
    row(s, 'Peniaze za zabitie (% ceny)', slider(s, zs_bounty, 0, 50, 1), '10 % = normálny zombík $20, boss $200')
    s = section(t0, 'Povstalci')
    note(s, 'Nastavenia povstalcov (počet, výroba, vlny, hliadky, pevnosť) sú na karte Nepriateľ.')
    button(s, 'Upraviť povstalcov →', lambda: show_page(t7))
    s = section(t0, 'Ako hrať')
    note(s, '1. Clearance: pridaj do lobby 1 AI (hocijakú) až za ľudí - ostane neutrálna, len drží hru.\n'
            '2. Zombie (Klasický, Survival, Defense): pridaj 1 AI za GLA - je to pán zombíkov. Štart je jedno.\n'
            '3. Survival / Defense: počet hráčov na karte Hráči = ľudia + 1 (zombie AI); nedá sa spojiť s Clearance.')

    # --- Oblasť
    t1 = tab('Oblasť', '◎')
    s = section(t1, 'Výber a mierka')
    row(s, 'Veľkosť oblasti (km)', number_field(s, km, 0.5, 20, 0.25))
    row(s, 'Mierka', combo(s, scale, list(SCALES)), '1:1 = budovy a cesty v skutočnej veľkosti (max 5 km)')
    row(s, 'Tvar', combo(s, shape, list(SHAPES)),
        'Pomer je šírka ku výške. Veľkosť v km udáva šírku mapy zo západu na východ.')
    def shape_changed(*_):
        picker.aspect = aspect_of(); picker.redraw(); sel_changed()
    shape.trace_add('write', shape_changed)
    picker.aspect = aspect_of()
    mname = var('name', 's', 'Poltar')          # the field itself is on the Export page
    button(s, 'Priblížiť na výber', picker.fit_selection)

    # quick OSM preview: roads, water, forest and building density drawn over the map (no terrain, no generation)
    prev_state = {'busy': False}
    pvf = ttk.Frame(s, style='Card.TFrame')
    prev_btn = ttk.Button(pvf, text=T('Rýchly náhľad OSM'))
    prev_btn.pack(side='left')
    prev_hide = ttk.Button(pvf, text=T('Skryť náhľad'))
    prev_hide.pack(side='left', padx=(6, 0))
    pvf.grid(row=s._row, column=0, columnspan=3, sticky='w', pady=6); s._row += 1
    note(s, 'Rýchly náhľad stiahne z OpenStreetMap len cesty, vodu, lesy a polohu budov a nakreslí ich na mapu vľavo - '
            'hneď vidíš, či je v oblasti dosť mesta.')

    def run_preview():
        if prev_state['busy']: return
        k, asp = picker.km(), aspect_of()
        if max(k, k * asp) > online.PREVIEW_MAX_KM:
            return messagebox.showwarning(T('Náhľad OSM'), T('Rýchly náhľad je pre oblasť do ') + f'{online.PREVIEW_MAX_KM:g} km' +
                                          T(' na dlhšej strane. Zmenši oblasť.'))
        (s_, w_), (n_, e_) = mappicker.square_corners(*picker.sel, k, asp)
        mx_, my_ = (e_ - w_) * 0.1, (n_ - s_) * 0.1           # a margin, so a small move of the square stays covered
        bb = (s_ - my_, w_ - mx_, n_ + my_, e_ + mx_)
        prev_state['busy'] = True; prev_btn.config(state='disabled'); status.set(T('Sťahujem náhľad OSM ...'))

        def job():
            try:
                data = online.fetch_preview(bb, log)
                def ok():
                    picker.set_osm(data); status.set(T('Náhľad OSM je hotový.')); schedule_refresh()
                root.after(0, ok)
            except Exception as ex:
                root.after(0, lambda: (status.set(T('Chyba - pozri log.')), messagebox.showerror(T('Náhľad OSM'), T(str(ex)))))
            finally:
                prev_state['busy'] = False
                root.after(0, lambda: prev_btn.config(state='normal'))
        threading.Thread(target=job, daemon=True).start()
    prev_btn.config(command=run_preview)
    prev_hide.config(command=lambda: (picker.set_osm(None), schedule_refresh()))
    note(s, 'Terén je vždy skutočný z výškových dát (kopce 1:1) - bez nastavení.')
    huge = var('huge', 'b', True)

    # --- Hráči
    t2 = tab('Hráči', '⚑')
    s = section(t2, 'Hráči a základne')
    players = var('players', 'i', 4)
    spin_refs['players'] = number_field(s, players, 1, 32, 1)
    row(s, 'Počet hráčov', spin_refs['players'],
        'až 32 hráčov (pri 13+ odporúčam väčšiu oblasť); 1 = Clearance / Zombie pre jedného hráča')
    layout = var('layout', 's', list(LAYOUT_OPTS)[0])
    zs_layout()
    layout_combo = combo(s, layout, list(LAYOUT_OPTS))
    row(s, 'Rozmiestnenie', layout_combo)
    button(s, 'Vymazať ručné štarty', lambda: picker.clear_starts())
    base = var('base_size_v2', 'i', 600)
    row(s, 'Priestor pre základňu', slider(s, base, 200, 1600, 50),
        'Štvorec okolo štartu (400 jednotiek ≈ 100 m). V skirmishi sa veľkosť prispôsobí priestoru potrebnému pre AI. '
        'Nevhodný štart sa môže posunúť na blízku suchú plochu.')
    s = section(t2, 'Zdroje')
    res = var('resources', 's', 'Normálne')
    row(s, 'Supply Docks', combo(s, res, list(RES_OPTS)))
    oil = var('oil_count', 'i', 3)
    row(s, 'Ropné veže', slider(s, oil, 0, 9, 1))
    var('ai_paths', 'b', True)

    # --- Krajina
    t3 = tab('Krajina', '♣')
    s = section(t3, 'Atmosféra')
    season = var('season', 's', list(SEASON_OPTS)[0])
    row(s, 'Ročné obdobie', combo(s, season, list(SEASON_OPTS)))
    tod = var('time_of_day', 's', 'Poludnie')
    row(s, 'Denná doba', combo(s, tod, list(TIME_OPTS)), 'osvetlenie mapy v hre')
    s = section(t3, 'Lesy a stromy')
    td = var('tree_density', 'd', 1.0)
    row(s, 'Hustota lesa', slider(s, td, 0.3, 4.0, 0.1), '1.0 = strom každých ~6,5 m')
    check(s, 'Kríky na okrajoch lesa', var('bushes', 'b', True))
    check(s, 'Vŕby a kríky popri vode', var('willows', 'b', True))
    s = section(t3, 'Prírodné detaily')
    nature_details = var('nature_details', 'd', 1.0)
    row(s, 'Hustota prírodných detailov', slider(s, nature_details, 0, 2, 0.1),
        'Skaly, pne a kríky v lesoch, pri vode a na svahoch. 0 = vypnuté.')
    s = section(t3, 'Život')
    note(s, 'Civilisti, autá, traktory, zvuky, kríky, ploty, chodníky, lampy a vlak sú vždy zapnuté - plná mapa.')
    check(s, 'Civilisti v obciach', var('civilians', 'b', True))
    check(s, 'Zaparkované autá pri cestách', var('traffic', 'b', True))
    check(s, 'Traktory, vozy, voly a seno na poliach', var('farm_life', 'b', True))
    check(s, 'Zvuky prostredia (vtáky, mesto, rieka, zvon, sova v noci)', var('sounds', 'b', True))
    s = section(t3, 'Pokročilé', advanced=True)
    mt = var('max_trees_v2', 'i', 3900)
    row(s, 'Max. počet stromov', slider(s, mt, 500, 300000, 100),
        'hra vykreslí najviac 4000 stromov (limit enginu), ostatné zahodí - nad 4000 má zmysel len s enginom bez tohto limitu')
    civ_amt = var('civ_amount', 'd', 1.5)
    row(s, 'Množstvo civilistov', slider(s, civ_amt, 0.2, 4.0, 0.1), 'na chodníkoch a pri domoch; 1.0 = málo, 4.0 = rušné mesto')

    # --- Mesto
    t4 = tab('Mesto', '▦')
    s = section(t4, 'Budovy')
    bstyle = var('building_style', 's', list(STYLE_OPTS)[0])
    row(s, 'Štýl budov', combo(s, bstyle, list(STYLE_OPTS)),
        'Automaticky: Európa, Blízky východ a Afrika (hlinené domy, mešity, bazáre) alebo Ázia podľa polohy mapy. '
        'Amerika a Austrália zatiaľ európske.')
    bd = var('building_density', 'd', 1.0)
    check(s, 'Kôlne a garáže', var('sheds', 'b', True))
    check(s, 'Ploty, múry a živé ploty z OSM', var('fences', 'b', True))
    check(s, 'Chodníky medzi panelákmi a cez parky', var('footways', 'b', True))
    check(s, 'Jazdiaci vlak na železnici', var('train', 'b', True),
          'vlak jazdí po najdlhšej trati na mape tam a späť a zastavuje na staniciach z OSM')
    check(s, 'Cintoríny, ihriská, parky, zastávky', var('details', 'b', True))
    check(s, 'Pouličné lampy', var('lamps', 'b', True))
    check(s, 'Autá na parkoviskách', var('cars', 'b', True))
    s = section(t4, 'Pokročilé', advanced=True)
    aptl = var('apt_levels', 'i', 5)
    row(s, 'Hustota drobnej zástavby (%)', number_field(s, bd, 0, 1, 0.01, display_scale=100),
        'Podiel rodinných domov, kôlní a hospodárskych budov. Predvolená hodnota je 100 %.')
    row(s, 'Bytovky bez údaja – poschodí', number_field(s, aptl, 2, 13, 1),
        'Náhradný počet poschodí pre bytovky bez údaja o výške. Rozpoznané vežové paneláky majú vlastný odhad.')
    stack = var('stack', 'b', True)
    check(s, 'Skladanie výškových budov', stack, 'Pri vhodných modeloch skladá diely na výšku podľa odhadovaného počtu poschodí.')

    # --- Nepriateľ
    t7 = tab('Nepriateľ', '⚔')
    s = section(t7, 'Povstalci v meste')
    ins = var('insurgents', 'b', True)
    check(s, 'Povstalci obsadia domy v obciach', ins,
          'Samostatná nepriateľská strana mapy: skript ich na začiatku nastaví ako nepriateľov všetkých hráčov a pošle do domov.')
    ins_lvl = var('ins_difficulty', 's', list(INS_LEVELS)[1])
    row(s, 'Obtiažnosť', combo(s, ins_lvl, list(INS_LEVELS)))
    ins_cap = var('ins_cap_v2', 'i', 4000)
    if int(ins_cap.get() or 0) > 5000: ins_cap.set(5000)
    row(s, 'Max. počet povstalcov', slider(s, ins_cap, 0, 5000, 100), 'veľa vojakov môže hru spomaliť')
    qrf = var('ins_qrf', 'b', True)
    check(s, 'Povstalci posielajú posily (zásahové jednotky v každej štvrti)', qrf,
          'keď vojdeš do štvrte, jej zásahová jednotka (technicals, RPG, rebeli) vyrazí na teba')
    loc = var('ins_localized', 'b', False)
    check(s, 'Povstalci na jednom mieste (inak po celom meste)', loc,
          'Miesto si nakreslíš v editore po vygenerovaní (karta Náhľad), inak program zvolí centrum mesta.')
    s = section(t7, 'Základne, výroba a útoky')
    outp = var('outposts', 'i', 3)
    row(s, 'Menšie základne povstalcov', slider(s, outp, 0, 15, 1), 'v oblasti povstalcov: War Factory + kasárne s obranou')
    prod = var('ins_prod_interval', 'i', 120)
    row(s, 'Výroba: 1 jednotka za (s)', slider(s, prod, 0, 600, 30),
        'War Factory vyrába tanky, kasárne pešiakov; 0 = bez výroby. 120 s = spolu 1 jednotka za minútu na základňu')
    wave = var('ins_wave_size', 'i', 5)
    row(s, 'Útočná vlna: tankov naraz', slider(s, wave, 0, 20, 1),
        'keď je pri základni toľko tankov, celá skupina (tanky + pešiaci) vyrazí na hráčov; 0 = len bránia základňu')
    patrols = var('patrols', 'i', 6)
    row(s, 'Hliadky technicals', slider(s, patrols, 0, 60, 1), 'autá s guľometom jazdia dookola po uliciach')
    foot = var('foot_patrols', 'i', 20)
    row(s, 'Pešie hliadky rebelov', slider(s, foot, 0, 200, 5),
        'rebeli pochodujú po uliciach v čatách po 6 (3 rady po 2), až 200')
    ins_oil = var('ins_oil', 'i', 4)
    row(s, 'Ropné veže pri povstalcoch', slider(s, ins_oil, 0, 12, 1), 'TechOilDerrick: kto ju obsadí, zarába peniaze')
    s = section(t7, 'Pevnosť povstalcov')
    fort = var('fortress', 'b', False)
    check(s, 'Postaviť pevnosť povstalcov', fort,
          'Hustý blok budov s uličkami pre tanky a obranou na rohoch, čo najďalej od všetkých hráčov.')
    ffac = var('fortress_faction', 's', 'GLA')
    row(s, 'Frakcia', combo(s, ffac, list(FORT_FACTIONS)))
    flvl = var('fortress_level', 's', list(FORT_LEVELS)[1])
    row(s, 'Opevnenie', combo(s, flvl, list(FORT_LEVELS)))
    s = section(t7, 'Pokročilé', advanced=True)
    ins_every = var('ins_every', 'i', 3)
    row(s, 'Obsadený každý N-tý dom', slider(s, ins_every, 1, 10, 1), '3 = každý tretí dom, 1 = všetky domy')
    ins_max = var('ins_max', 'i', 4)
    row(s, 'Max. vojakov v dome', slider(s, ins_max, 1, 4, 1), 'v dome bude náhodne 1 až toto číslo (väčšinou 1 - 3)')

    # --- Export
    t5 = tab('Export', '⬇')
    s0 = section(t5, 'Mapa')
    row(s0, 'Názov mapy', ttk.Entry(s0, textvariable=mname),
        'meno v zozname máp v hre; herné módy sa pridajú do hranatej zátvorky')

    # ---- saved profiles: the whole setup (incl. the place on the map and manual starts) under a name; built-in templates
    PROFILES = os.path.join(os.path.expanduser('~'), '.cncearth', 'profiles.json')
    PROFILE_SKIP = {'game_dir', 'maps_dir', 'maps_dir_gx', 'gx_dir', 'panel_width'}          # folders belong to the computer, not the setup
    OPT_MAP = {'scale': 'SCALES', 'shape': 'SHAPES', 'layout': 'LAYOUT_OPTS', 'resources': 'RES_OPTS', 'season': 'SEASON_OPTS',
               'time_of_day': 'TIME_OPTS', 'building_style': 'STYLE_OPTS', 'ins_difficulty': 'INS_LEVELS',
               'fortress_faction': 'FORT_FACTIONS', 'fortress_level': 'FORT_LEVELS', 'zombie_pacing': 'ZOMBIE_PACING'}
    MODE_KEYS = ('mode_zombie', 'mode_zsurvival', 'mode_zdefense', 'mode_invasion')
    # templates change only what they name; everything else (place, size, folders ...) stays as it is
    TEMPLATES = {'Šablóna: bežný skirmish': dict(mode_clearance=False, mode_zombie=False, mode_zsurvival=False,
                                                 mode_zdefense=False, players=4, layout=list(_ORIG['LAYOUT_OPTS'])[0],
                                                 insurgents=False, black_map=False),
                 'Šablóna: Clearance (jeden hráč)': dict(mode_clearance=True, mode_zombie=False, mode_zsurvival=False,
                                                         mode_zdefense=False, players=1, insurgents=True, black_map=True,
                                                         preset='Stredná'),
                 'Šablóna: Zombie Survival (2 ľudia)': dict(mode_clearance=False, mode_zombie=False, mode_zsurvival=True,
                                                            mode_zdefense=False, players=3, black_map=True, preset='Stredná'),
                 'Šablóna: Zombie Defense (2 ľudia)': dict(mode_clearance=False, mode_zombie=False, mode_zsurvival=False,
                                                           mode_zdefense=True, players=3, black_map=True, preset='Stredná'),
                 'Šablóna: Invasion (2 ľudia)': dict(mode_clearance=False, mode_zombie=False, mode_zsurvival=False,
                                                    mode_zdefense=False, mode_invasion=True, players=3)}

    def read_profiles():
        try: return json.load(open(PROFILES, encoding='utf-8'))
        except Exception: return {}

    def canon(key, val):
        """A displayed option text -> its Slovak key (so a profile works in either language)."""
        src = _ORIG.get(OPT_MAP[key]) if key in OPT_MAP else (PRESETS if key == 'preset' else None)
        if src is None: return val
        return next((k_ for k_ in src if k_ == val or T(k_) == val), val)

    def put(key, val):
        if key not in V or key in FORCED or key in PROFILE_SKIP or key in MODE_KEYS: return
        if key in OPT_MAP or key == 'preset': val = T(val)
        try: V[key][0].set(val)
        except Exception: pass

    def apply_profile(d, run_preset=False):
        if run_preset and d.get('preset'):
            put('preset', d['preset']); apply_preset()           # a template: the preset sets the numbers first
        for key, val in d.items():
            if key in ('lat', 'lon', 'place', 'starts'): continue
            if key == 'preset' and run_preset: continue
            put(key, val)
        zs_ = {k_: bool(d.get(k_, V[k_][0].get())) for k_ in MODE_KEYS[:-1]}
        m_invasion.set(False)
        z_kind.set(T('Defense' if zs_['mode_zdefense'] else 'Survival' if zs_['mode_zsurvival'] else 'Klasický'))
        z_on.set(any(zs_.values()))
        if 'mode_clearance' in d: m_clear.set(bool(d['mode_clearance']))
        m_invasion.set(bool(d.get('mode_invasion', False)))
        if 'place' in d: place.delete(0, 'end'); place.insert(0, d['place'])
        if 'starts' in d: picker.starts = [tuple(p) for p in d['starts']]
        if 'lat' in d and 'lon' in d:
            picker.center_on(d['lat'], d['lon']); picker.fit_selection()
        paint_seg(); refresh(); update_summary(); schedule_refresh()

    prof_name = tk.StringVar()
    s_pf = section(t5, 'Profily nastavení')
    pcombo = ttk.Combobox(s_pf, textvariable=prof_name)
    row(s_pf, 'Profil', pcombo, 'Vyber uložený profil alebo šablónu. Nový profil uložíš tak, že napíšeš názov a klikneš na Uložiť.')

    def fill_profiles():
        pcombo.config(values=[T(k_) for k_ in TEMPLATES] + sorted(read_profiles()))
    fill_profiles()

    def current_profile():
        d = {}
        for key, (v, _d) in V.items():
            if key in PROFILE_SKIP or key in FORCED: continue
            try: val = v.get()
            except Exception: continue
            d[key] = canon(key, val) if (key in OPT_MAP or key == 'preset') else val
        d.update(place=place.get(), lat=picker.sel[0], lon=picker.sel[1], starts=[list(p) for p in picker.starts])
        return d

    def profile_save():
        nm_ = prof_name.get().strip()
        if not nm_ or nm_ in [T(k_) for k_ in TEMPLATES]:
            return messagebox.showinfo(T('Profily'), T('Napíš do poľa Profil názov nového profilu (šablóny sa neprepisujú).'))
        allp = read_profiles()
        if nm_ in allp and not messagebox.askyesno(T('Profily'), T('Profil už existuje. Prepísať?')):
            return
        allp[nm_] = current_profile()
        try:
            os.makedirs(os.path.dirname(PROFILES), exist_ok=True)
            json.dump(allp, open(PROFILES, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except Exception as ex:
            return messagebox.showerror(T('Profily'), str(ex))
        fill_profiles(); status.set(T('Profil uložený: ') + nm_)

    def profile_load():
        nm_ = prof_name.get().strip()
        tpl = next((k_ for k_ in TEMPLATES if T(k_) == nm_ or k_ == nm_), None)
        if tpl: return apply_profile(TEMPLATES[tpl], run_preset=True)
        d = read_profiles().get(nm_)
        if d is None:
            return messagebox.showinfo(T('Profily'), T('Taký profil nie je. Vyber ho zo zoznamu.'))
        apply_profile(d); status.set(T('Profil načítaný: ') + nm_)

    def profile_delete():
        nm_ = prof_name.get().strip()
        allp = read_profiles()
        if nm_ not in allp:
            return messagebox.showinfo(T('Profily'), T('Zmazať sa dajú len vlastné profily (nie šablóny).'))
        if not messagebox.askyesno(T('Profily'), T('Zmazať profil') + f' "{nm_}"?'):
            return
        allp.pop(nm_)
        try: json.dump(allp, open(PROFILES, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except Exception as ex: return messagebox.showerror(T('Profily'), str(ex))
        prof_name.set(''); fill_profiles()
    pbf = ttk.Frame(s_pf, style='Card.TFrame')
    for txt_, cmd_ in (('Načítať', profile_load), ('Uložiť', profile_save), ('Zmazať', profile_delete)):
        ttk.Button(pbf, text=T(txt_), command=cmd_).pack(side='left', padx=(0, 6))
    pbf.grid(row=s_pf._row, column=0, columnspan=3, sticky='w', pady=6); s_pf._row += 1
    note(s_pf, 'Profil uloží všetko: režim, hráčov, obtiažnosť, krajinu, povstalcov, aj miesto na mape a ručné štarty. '
               'Šablóny menia len režim a hráčov.')
    s = section(t5, 'Priečinky')
    gdir = var('game_dir', 's', catalog.find_game_dir())
    mdir = var('maps_dir', 's', catalog.default_maps_dir('classic'))
    mdir_gx = var('maps_dir_gx', 's', catalog.default_maps_dir('gx'))
    gxdir = var('gx_dir', 's', catalog.default_gx_dir())
    if not os.path.isfile(os.path.join(gxdir.get() or '', 'generalszh.exe')) and catalog.default_gx_dir():
        gxdir.set(catalog.default_gx_dir())        # an empty / wrong saved folder -> the real GeneralsX install
    for lbl, v in (('Hra (INIZH.big)', gdir), ('GeneralsX', gxdir), ('Mapy GeneralsX', mdir_gx)):
        fr = ttk.Frame(s, style='Card.TFrame')
        ttk.Entry(fr, textvariable=v).pack(side='left', fill='x', expand=True)
        ttk.Button(fr, text='…', width=3, command=lambda v=v: v.set(filedialog.askdirectory() or v.get())).pack(side='left', padx=(4, 0))
        row(s, lbl, fr)
    s = section(t5, 'Ostatné')
    seed = var('seed', 'i', 1)
    seed_fr = ttk.Frame(s, style='Card.TFrame')
    number_field(seed_fr, seed, 1, 99999, 1).pack(side='left')
    ttk.Button(seed_fr, text='⚄  ' + T('Náhodné'), command=lambda: seed.set(random.randint(1, 99999))).pack(side='left', padx=(8, 0))
    row(s, 'Náhodné semienko', seed_fr, 'iné číslo = iné rozmiestnenie stromov a variácie domov')
    compact = var('compact', 'b', True)
    check(s, 'Kompaktná mapa (komprimovaná)', compact,
          'mapa je asi 10x menšia (napr. 5 MB → 0,5 MB), nič z nej nechýba - hra ju rozbalí sama ako oficiálne mapy; '
          'menšia mapa sa rýchlejšie pošle spoluhráčom v lobby')
    openf = var('open_folder', 'b', True)
    check(s, 'Po dokončení otvoriť priečinok mapy', openf)

    def reset():
        for k, (v, d) in V.items():
            if k not in ('game_dir', 'maps_dir', 'maps_dir_gx', 'name'): v.set(d)
        refresh()
    button(s, 'Obnoviť predvolené nastavenia', reset)
    s = section(t5, 'Limit ciest v hre')
    lim_lbl = ttk.Label(s, text='', style='CardDim.TLabel', wraplength=px(470), justify='left')
    lim_lbl.grid(row=s._row, column=0, columnspan=3, sticky='w'); s._row += 1

    def show_limit(*_):
        v = catalog.read_loose_limit(gxdir.get()) if gxdir.get() else None
        if TARGETS[target_key()] == 'classic':
            return lim_lbl.config(text=T('Klasická hra: vždy 4000 úsekov ciest (do hry na Steame sa nič nezapisuje).'))
        if v and v >= catalog.ROAD_LIMITS['MaxRoadSegments']:
            return lim_lbl.config(text=T('GeneralsX: cesty neobmedzené (nastavené automaticky).'))
        lim_lbl.config(text=T('Teraz: ') + f'{v or 4000} ' + T('úsekov ciest') +
                       (T(' (zvýšené)') if v and v > 4000 else T(' (pôvodný limit hry - pri veľkých mestách chýbajú ulice)')))
    gxdir.trace_add('write', show_limit)

    def raise_limit():
        try:
            if not os.path.isfile(os.path.join(gxdir.get(), 'generalszh.exe')):
                raise RuntimeError(T('V priečinku GeneralsX (Export -> Priečinky) nie je generalszh.exe. Limit ciest sa '
                                     'zapisuje len do GeneralsX, nikdy do hry na Steame (GeneralsOnline by ťa nepustil).'))
            n = catalog.raise_road_limit(gxdir.get(), source_dir=gdir.get())
            messagebox.showinfo(T('Limit ciest'), T('Limit ciest v GeneralsX je zvýšený na ') + str(n) +
                                T('.\n\nPlatí po reštarte GeneralsX. Spoluhráči ho potrebujú tiež (rovnaké dáta), '
                                  'inak sa nepripoja.'))
        except Exception as ex:
            messagebox.showerror(T('Limit ciest'), T(str(ex)))
        show_limit()

    def restore_limit():
        try:
            r = catalog.restore_road_limit(gxdir.get())
            messagebox.showinfo(T('Limit ciest'), T({'restored': 'Pôvodný GameData.ini je obnovený.', 'removed': 'Limit ciest je vrátený na pôvodný.',
                                                     'nothing': 'Nebolo čo vracať.'}[r]))
        except Exception as ex:
            messagebox.showerror(T('Limit ciest'), T(str(ex)))
        show_limit()
    def auto_gx(tg):
        """Target switched: GeneralsX -> find its folder and raise its road limit quietly; classic -> nothing is
        written anywhere (GeneralsOnline must stay untouched), the generator keeps the stock 4000."""
        if tg != 'gx': show_limit(); return
        if not os.path.isfile(os.path.join(gxdir.get() or '', 'generalszh.exe')) and catalog.default_gx_dir():
            gxdir.set(catalog.default_gx_dir())
        if os.path.isfile(os.path.join(gxdir.get() or '', 'generalszh.exe')):
            pass              # the road limit is never raised by itself (multiplayer needs the original game data)
        show_limit()
    target_hooks.append(auto_gx)
    root.after(200, lambda: auto_gx(TARGETS[target_key()]))      # also at start
    show_limit()

    # --- Náhľad
    t6 = tab('Náhľad', '▣')
    s = section(t6, 'Posledná mapa')
    prev_lbl = ttk.Label(s, text=T('Náhľad sa zobrazí po vygenerovaní mapy.'), style='CardDim.TLabel')
    prev_lbl.grid(row=0, column=0, sticky='nw')
    stats_lbl = ttk.Label(s, text='', style='CardDim.TLabel', wraplength=px(440), justify='left')
    stats_lbl.grid(row=1, column=0, sticky='nw', pady=8)
    edit_btn = ttk.Button(s, text=T('UPRAVIŤ BUDOVY A ZÁKLADNE POVSTALCOV'), state='disabled')
    edit_btn.grid(row=2, column=0, sticky='we', pady=6)
    ttk.Label(s, text=T('V editore klikneš na budovu a určíš, aký panelák alebo dom to má byť a koľko má poschodí. '
                        'Môžeš aj rozmiestniť základne povstalcov a určiť, čo v nich bude. Potom pregeneruješ mapu.'),
              style='CardDim.TLabel', wraplength=px(440), justify='left').grid(row=3, column=0, sticky='nw')
    last_job = {}
    show_page(t0)

    # ---------------- generate
    # summary of what will be made, right above the button
    summary = tk.StringVar()
    pre = tk.Frame(gobar, bg=BG); pre.pack(fill='x', pady=(8, 0))          # the pre-check lines (filled by update_precheck)
    summary_label = ttk.Label(gobar, textvariable=summary, style='Summary.TLabel', justify='left')
    summary_label.pack(anchor='w', pady=(8, 0))
    def wrap_summary(e):
        summary_label.configure(wraplength=max(1, e.width - px(12)))
        for label in pre.winfo_children():
            label.configure(wraplength=max(1, e.width - px(12)))
    gobar.bind('<Configure>', wrap_summary)
    btn = ttk.Button(gobar, text=T('VYGENEROVAŤ MAPU'), style='Go.TButton')
    btn.pack(fill='x', pady=(6, 4))
    bar = ttk.Progressbar(gobar, mode='indeterminate'); bar.pack(fill='x')
    status = tk.StringVar(value=T('Pripravené.'))
    stf = ttk.Frame(gobar); stf.pack(fill='x', pady=(4, 0))
    status_label = ttk.Label(stf, textvariable=status, style='Status.TLabel')
    pre_btn = ttk.Button(stf, text=T('Kontrola mapy'),
                         command=lambda: messagebox.showinfo(T('Kontrola mapy'), '\n\n'.join(txt for _, txt in precheck_items())))
    log_btn = tk.Label(stf, text=T('Log ▸'), bg=BG, fg=ACC2, cursor='hand2', font=('Segoe UI', 9, 'bold'))
    log_btn.pack(side='right')
    status_label.pack(side='left', fill='x', expand=True)

    def arrange_footer(e):
        if e.height < px(350):
            pre.pack_forget()
            summary_label.pack_forget()
            if not pre_btn.winfo_manager():
                pre_btn.pack(side='right', padx=(0, px(6)), before=status_label)
        else:
            pre_btn.pack_forget()
            if not pre.winfo_manager():
                pre.pack(fill='x', pady=(8, 0), before=btn)
                summary_label.pack(anchor='w', pady=(8, 0), before=btn)
    right.bind('<Configure>', arrange_footer, add='+')

    box = tk.Text(logf, height=7, state='disabled', wrap='word', bg='#0d0f12', fg='#b8c2cc', insertbackground=FG,
                  relief='flat', font=('Consolas', 9), padx=8, pady=6)
    log_open = {'v': bool(saved.get('log_open', False))}

    def paint_log():
        if log_open['v']: box.pack(fill='x')
        else: box.pack_forget()
        log_btn.config(text=T('Log ▾') if log_open['v'] else T('Log ▸'))

    def toggle_log(e=None):
        log_open['v'] = not log_open['v']; saved['log_open'] = log_open['v']; paint_log()
    log_btn.bind('<Button-1>', toggle_log); paint_log()

    def update_summary(*_):
        try:
            k = picker.km()
        except Exception:
            k = km.get()
        modes_ = [n for n, v in (('Clearance', m_clear), ('Zombie', m_zomb), ('ZombieSurvival', m_zs),
                                  ('ZombieSurvival Defense', m_zd), ('Invasion', m_invasion)) if v.get()]
        if m_invasion.get():
            pl = f"{max(1, int(players.get()) - 1)} {T('obrancovia + GLA AI')}"
        elif m_zs.get() or m_zd.get():
            pl = f"{max(1, int(players.get()) - 1)} {T('ľudia + zombie AI')}"
        else:
            pl = T('ty v strede + 1 AI') if modes_ else f"{players.get()} {T('hráči')}"
        nm_ = map_folder_name(safe_name(mname.get() or 'Earth'), **mode_flags())
        parts = [nm_, f'{float(k):g} × {float(k) * aspect_of():g} km', pl,
                 ' + '.join(modes_) if modes_ else T('bežný skirmish')]
        if m_black.get(): parts.append(T('čierna mapa'))
        if ins.get() and not m_invasion.get(): parts.append(T('povstalci'))
        pk = preset_key()
        if pk != 'Vlastná': parts.append(T(pk))
        parts.append('GeneralsX · ' + T('plný detail') + ' · ' + T('cesty neobmedzené'))
        got = [T(n) for k_, n in (('footways', 'chodníky'), ('fences', 'ploty'), ('lamps', 'lampy'), ('cars', 'autá'),
                                  ('civilians', 'civilisti')) if k_ in V and V[k_][0].get()]
        parts.append(', '.join(got) if got else T('bez detailov'))
        summary.set('▶  ' + '   •   '.join(parts))
    # ---------------- pre-check: what the chosen settings mean, before the (long) generation starts
    PRE_COL = {'ok': '#7fc97f', 'info': DIM, 'warn': ACC2, 'err': '#ff6b5e'}
    PRE_ICON = {'ok': '✔', 'info': 'ℹ', 'warn': '⚠', 'err': '✖'}
    last_plan = {'p': None}

    def maps_dir_now():
        return mdir_gx.get() if TARGETS[target_key()] == 'gx' else mdir.get()

    def mode_flags():
        return dict(clearance=bool(m_clear.get()), zombie=bool(m_zomb.get()), zsurv=bool(m_zs.get()),
                    zdef=bool(m_zd.get()), light=bool(light.get()), invasion=bool(m_invasion.get()))

    for v in (m_clear, m_zomb, m_zs, m_zd, m_invasion, m_black, players, km, shape, mname, ins, preset, target, detail, gxdir,
              *(V[k_][0] for k_ in ('footways', 'fences', 'lamps', 'cars', 'civilians') if k_ in V)):
        v.trace_add('write', update_summary)
    update_summary()

    def update_plan():
        """Draw start markers and base boundaries before terrain-based adjustments."""
        try:
            k, asp = picker.km(), aspect_of()
            south, west, north, east = selection_bounds()
            points = [((lon - west) / (east - west), (lat - south) / (north - south))
                      for lat, lon in selected_starts()]
            plan = preflight.start_plan(int(players.get()), LAYOUT_OPTS.get(layout.get(), 'auto'), k,
                                        tiles_for(k, SCALES.get(scale.get(), 1.0)), asp, int(base.get()),
                                        manual_points=points, **{k_: v_ for k_, v_ in mode_flags().items() if k_ != 'light'})
        except Exception:
            plan = None
        last_plan['p'] = plan
        picker.plan = plan
        picker.redraw()

    def selection_bounds():
        (south, west), (north, east) = mappicker.square_corners(*picker.sel, picker.km(), aspect_of())
        return south, west, north, east

    def selected_starts():
        south, west, north, east = selection_bounds()
        return [(lat, lon) for lat, lon in picker.starts if south <= lat <= north and west <= lon <= east]

    def precheck_items():
        items = []
        try:
            k, asp, sc = picker.km(), aspect_of(), SCALES.get(scale.get(), 1.0)
            t = tiles_for(k, sc); lat, lon = picker.sel
        except Exception:
            return items
        if not os.path.isfile(os.path.join(gdir.get() or '', 'INIZH.big')):
            items.append(('err', T('Nenašiel som INIZH.big - nastav priečinok hry na karte Export.')))
        try:
            parts, cached = online.osm_status(Proj(lat, lon, k * 1000.0, t, asp).bbox(150))
            items.append(('ok', T('OSM dáta tejto oblasti sú v cache - generovanie bude rýchlejšie.')) if cached else
                         ('info', T('OSM sa stiahne z internetu (dielov: {}).').format(parts * parts)))
        except Exception:
            pass
        (s_, w_), (n_, e_) = mappicker.square_corners(lat, lon, k, asp)
        stt = preflight.preview_stats(picker.osm, (s_, w_, n_, e_))
        if stt:
            dens = stt['buildings'] / max(0.01, k * k * asp)
            if stt['buildings'] == 0 and stt['road_segments'] == 0:
                items.append(('warn', T('Podľa náhľadu je oblasť prázdna (žiadne budovy ani cesty v OSM).')))
            elif stt['buildings'] < 30:
                items.append(('warn', T('V oblasti je len {} budov - doplnia sa zo satelitných snímok (Microsoft).').format(stt['buildings'])))
            else:
                items.append(('info', T('V oblasti je {} budov ({} na km²).').format(stt['buildings'], f'{dens:.0f}')))
            try:
                raised = (catalog.read_loose_limit(gxdir.get()) or 0) >= catalog.ROAD_LIMITS['MaxRoadSegments'] \
                    if gxdir.get() else False
            except Exception:
                raised = False
            if TARGETS[target_key()] == 'classic' or not raised:
                if stt['road_segments'] >= 2800:
                    items.append(('warn', T('Ulíc je veľa (aspoň {} úsekov) - hra má limit 4000 a menšie ulice môžu chýbať.').format(stt['road_segments'])))
        elif picker.osm is not None:
            items.append(('info', T('Náhľad OSM je pre inú oblasť - spusti ho znova (karta Oblasť).')))
        else:
            items.append(('info', T('Tip: „Rýchly náhľad OSM“ (karta Oblasť) ukáže, čo v oblasti je.')))
        plan = last_plan['p']
        if plan and plan['base_m'] > 0:
            cover, over = preflight.base_report(plan)
            if over:
                items.append(('warn', T('Základne sú príliš blízko pri sebe (párov: {}) - zmenši Priestor pre základňu alebo zväčši oblasť.').format(over)))
            elif cover > 0.35:
                items.append(('warn', T('Základne zaberú ~{} % mapy - zmenši Priestor pre základňu alebo zväčši oblasť.').format(f'{cover * 100:.0f}')))
            else:
                items.append(('ok', T('{} štartov, základňa ~{} m, zaberajú ~{} % mapy.').format(plan['n'], f"{plan['base_m']:.0f}", f'{cover * 100:.0f}')))
        folder = os.path.join(maps_dir_now() or '', map_folder_name(safe_name(mname.get().strip() or 'Earth'), **mode_flags()))
        if maps_dir_now() and os.path.isdir(folder):
            items.append(('warn', T('Mapa s týmto názvom už existuje - pri generovaní sa opýtam, či ju prepísať.')))
        order = {'err': 0, 'warn': 1, 'info': 2, 'ok': 3}
        return sorted(items, key=lambda it: order[it[0]])[:(2 if right.winfo_height() < px(580) else 4)]

    def update_precheck():
        for w_ in pre.winfo_children(): w_.destroy()
        for lvl, txt in precheck_items():
            tk.Label(pre, text=f'{PRE_ICON[lvl]}  {txt}', bg=BG, fg=PRE_COL[lvl], font=('Segoe UI', 9), anchor='w',
                     justify='left', wraplength=max(px(230), right.winfo_width() - px(12))).pack(fill='x')

    refresh_job = {'id': None}

    def schedule_refresh(*_):
        """Plan + pre-check, once after a burst of changes (dragging the square or a slider fires many)."""
        if refresh_job['id']: root.after_cancel(refresh_job['id'])
        refresh_job['id'] = root.after(150, lambda: (update_plan(), update_precheck()))
    for v in (layout, players, scale, km, shape, base, m_clear, m_zomb, m_zs, m_zd, m_invasion, mname, gdir, gxdir, mdir, mdir_gx, target):
        v.trace_add('write', schedule_refresh)
    sel_hooks.append(schedule_refresh)
    picker.on_starts = lambda _s: schedule_refresh()
    root.after(400, schedule_refresh)

    def refresh(*_):
        lay = LAYOUT_OPTS.get(layout.get(), 'auto')
        automatic = m_invasion.get() or m_zs.get() or m_zd.get()
        layout_combo.configure(state='disabled' if automatic else 'readonly',
                               values=list(LAYOUT_OPTS) if automatic else [k for k, v in LAYOUT_OPTS.items()
                                                                         if v not in ('zsurv', 'invasion')])
        picker.start_mode = lay == 'manual' and not automatic
        try: picker.max_starts = int(players.get())
        except Exception: pass
        picker.starts = picker.starts[:picker.max_starts]
        sel_changed(); picker.redraw()
    for v in (layout, players, scale, km, huge, m_zomb, m_zs, m_zd, m_invasion):
        v.trace_add('write', refresh)
    if saved.get('starts'):
        picker.starts = [tuple(p) for p in saved['starts']]
    picker.sel = (state['lat'], state['lon'])
    refresh()

    def log(msg):
        msg = T(msg)
        def w():
            box.config(state='normal'); box.insert('end', msg + '\n'); box.see('end'); box.config(state='disabled')
            line = msg.strip().splitlines()[0] if msg.strip() else ''
            if line and not line.startswith(' '): status.set(line[:110])
        root.after(0, w)

    def do_search(event=None):
        q = place.get().strip()
        if not q: return
        m = re.match(r'^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$', q)
        if m:
            picker.center_on(float(m.group(1)), float(m.group(2))); picker.fit_selection(); return
        search_btn.config(state='disabled')
        def job():
            try:
                found = online.geocode_many(q)
                def ok():
                    if len(found) == 1:
                        goto_place(found[0][0], found[0][1], found[0][2], q)
                    else:                       # several candidates: the user picks (a menu under the search box)
                        menu = tk.Menu(root, tearoff=0)
                        for lat, lon, label in found:
                            menu.add_command(label=short_label(label, 90),
                                             command=lambda a=lat, b=lon, c=label: goto_place(a, b, c, q))
                        menu.tk_popup(search_btn.winfo_rootx(), search_btn.winfo_rooty() + search_btn.winfo_height())
                root.after(0, ok)
            except Exception as ex:
                root.after(0, lambda: messagebox.showerror(T('Hľadanie'), T(str(ex))))
            finally:
                root.after(0, lambda: search_btn.config(state='normal'))
        threading.Thread(target=job, daemon=True).start()
    search_btn.config(command=do_search); place.bind('<Return>', do_search)

    # ---- recent and favourite places (★ menu next to the search box); kept in settings.json
    def short_label(label, n=60):
        label = ' '.join(str(label).split())
        return label if len(label) <= n else label[:n - 1] + '…'

    def remember_place(label, lat, lon):
        rec = [r for r in saved.get('recent', []) if abs(r[1] - lat) > 1e-3 or abs(r[2] - lon) > 1e-3]
        saved['recent'] = ([[short_label(label), lat, lon]] + rec)[:8]
        schedule_save()

    def goto_place(lat, lon, label, query=None):
        picker.center_on(lat, lon); picker.fit_selection()
        if query:
            mname.set(safe_name(query.split(',')[0])); log(T('Nájdené: ') + label)
        remember_place(label.split(',')[0] if label else 'place', lat, lon)

    def fav_menu(e=None):
        menu = tk.Menu(root, tearoff=0)
        favs = saved.get('favs', [])
        cur = (mname.get().strip() or place.get().strip() or 'place')

        def add_fav():
            lat, lon = picker.sel
            saved['favs'] = [f for f in favs if abs(f[1] - lat) > 1e-3 or abs(f[2] - lon) > 1e-3] + [[short_label(cur), lat, lon]]
            schedule_save()

        def del_fav(f):
            saved['favs'] = [x for x in favs if x is not f]; schedule_save()
        menu.add_command(label=T('★ Pridať toto miesto do obľúbených') + f'  ({short_label(cur, 30)})', command=add_fav)
        if favs:
            menu.add_separator()
            menu.add_command(label=T('Obľúbené:'), state='disabled')
            for f in favs:
                menu.add_command(label='   ' + f[0], command=lambda f=f: goto_place(f[1], f[2], f[0], f[0]))
            rm = tk.Menu(menu, tearoff=0)
            for f in favs:
                rm.add_command(label=f[0], command=lambda f=f: del_fav(f))
            menu.add_cascade(label=T('Odstrániť z obľúbených'), menu=rm)
        rec = saved.get('recent', [])
        if rec:
            menu.add_separator()
            menu.add_command(label=T('Naposledy použité:'), state='disabled')
            for r in rec:
                menu.add_command(label='   ' + r[0], command=lambda r=r: goto_place(r[1], r[2], r[0], r[0]))
            menu.add_command(label=T('Vymazať históriu'), command=lambda: (saved.__setitem__('recent', []), schedule_save()))
        menu.tk_popup(fav_btn.winfo_rootx(), fav_btn.winfo_rooty() + fav_btn.winfo_height())
    fav_btn.config(command=fav_menu)

    def save_settings():
        d = {}
        for k, (v, _) in V.items():
            try: d[k] = v.get()
            except Exception: pass                      # e.g. a half-typed number in a spin box: skip it this time
        d.update(place=place.get(), lat=picker.sel[0], lon=picker.sel[1], starts=picker.starts,
                 lang=saved.get('lang', tr.LANG), recent=saved.get('recent', []), favs=saved.get('favs', []),
                 log_open=log_open['v'])
        try:
            os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
            json.dump(d, open(SETTINGS, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except Exception:
            pass
    root.protocol('WM_DELETE_WINDOW', lambda: (save_settings(), root.destroy()))

    # autosave: a moment after the last change, so a crash or a killed window loses nothing
    save_job = {'id': None}

    def schedule_save(*_):
        if save_job['id']: root.after_cancel(save_job['id'])
        save_job['id'] = root.after(1500, save_settings)
    for _v, _d in V.values():
        _v.trace_add('write', schedule_save)
    place.bind('<KeyRelease>', schedule_save)
    sel_hooks.append(schedule_save)
    picker.on_starts = lambda _s: (schedule_refresh(), schedule_save())

    def show_preview(out):
        try:
            from PIL import Image, ImageTk
            p = os.path.splitext(out)[0] + '_preview.png'
            im = Image.open(p); im.thumbnail((px(440), px(440)))
            prev_lbl._img = ImageTk.PhotoImage(im)
            prev_lbl.config(image=prev_lbl._img, text='')
            s_ = getattr(run_generation, 'last_stats', {})
            names = {'patrols': 'hliadok', 'outposts': 'základní povstalcov', 'insurgents': 'povstalcov', 'insurgent_houses': 'obsadených domov', 'civilians': 'civilistov', 'parked_cars': 'áut', 'farm_objects': 'na poliach',
                     'ambient_sounds': 'zvukov', 'buildings': 'budov', 'stacked_floors': 'poschodí navyše', 'trees': 'stromov', 'bridges': 'mostov', 'overpasses': 'nadjazdov',
                     'players': 'hráčov', 'supply_docks': 'Supply Docks', 'ai_paths': 'AI trás', 'gas_stations': 'benzínok',
                     'football_pitches': 'ihrísk', 'churches': 'kostolov', 'street_lamps': 'lámp', 'tombstones': 'náhrobkov',
                     'game_relief_m': 'm prevýšenie v hre'}
            txt = '   •   '.join(f'{s_[k]} {v}' for k, v in names.items() if k in s_)
            if 'starting_supplies' in s_:
                txt += '\n\n' + T('Štarty s dostupnými zásobami: {}/{}').format(s_['starting_supplies'], s_['players'])
            if 'fortress' in s_: txt += '\n\nPevnosť: ' + str(s_['fortress'])
            stats_lbl.config(text=T(txt))
            if getattr(run_generation, 'last_edit', None): edit_btn.config(state='normal')
            show_page(t6)
        except Exception as ex:
            log(T('Náhľad sa nepodarilo zobraziť: ') + str(ex))

    def open_map_editor():
        data = getattr(run_generation, 'last_edit', None)
        if not data or not last_job: return
        def again():
            btn.config(state='disabled'); bar.start(12)
            threading.Thread(target=work, args=last_job['job'], daemon=True).start()
        try:
            editor.open_editor(root, data, again, (BG, PANEL, FG, ACC2))
        except Exception as ex:
            log(T('Editor sa nepodarilo otvoriť: ') + str(ex)); log(traceback.format_exc())
    edit_btn.config(command=open_map_editor)


    def work(args, kwargs):
        last_job['job'] = (args, dict(kwargs))
        try:
            out = run_generation(*args, **kwargs)
            def done():
                show_preview(out)
                if openf.get():
                    try:
                        if sys.platform == 'win32': os.startfile(os.path.dirname(out))
                        else: subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', os.path.dirname(out)])
                    except Exception: pass
                messagebox.showinfo(T('Hotovo'), T('Mapa je vytvorená:\n') + out + T('\n\nReštartuj hru a nájdeš ju medzi skirmish mapami.'))
            root.after(0, done)
            root.after(0, lambda: status.set(T('Hotovo: ') + os.path.basename(out)))
        except Exception as ex:
            root.after(0, lambda: status.set(T('Chyba - pozri log.')))
            log('CHYBA: ' + str(ex)); log(traceback.format_exc())
            root.after(0, lambda: messagebox.showerror(T('Chyba'), T(str(ex))))
        finally:
            root.after(0, lambda: (bar.stop(), btn.config(state='normal')))

    def start():
        for commit in numeric_commits:
            commit()
        sc = SCALES.get(scale.get(), 1.0)
        k = picker.km(); t = tiles_for(k, sc)
        kwargs_shape = {'aspect': aspect_of()}
        short = min(t, round(t * aspect_of()))
        if short < MIN_TILES:
            needed = MIN_TILES * M_PER_TILE_1TO1 * sc / (1000 * min(1.0, aspect_of()))
            return messagebox.showwarning(T('Malá oblasť'), T(f'Oblasť je príliš malá ({short} dlaždíc). Zväčši ju aspoň na {needed:g} km.'))
        if (m_zomb.get() or m_zs.get() or m_zd.get()) and max(t, round(t * aspect_of())) > ZOMBIE_MAX_TILES and not huge.get():
            return messagebox.showwarning(T('Zombie'), T('Zombie mód je len pre menšie mapy - zmenši oblasť na najviac ') +
                                          f'{ZOMBIE_MAX_TILES * M_PER_TILE_1TO1 * sc / 1000:g} km.')
        lay = LAYOUT_OPTS.get(layout.get(), 'auto')
        if m_invasion.get(): lay = 'invasion'
        elif m_zs.get() or m_zd.get(): lay = 'zsurv'
        elif lay in ('zsurv', 'invasion'): lay = 'auto'
        npl = int(players.get())
        inside = []
        if lay == 'manual':
            inside = selected_starts()
            if len(inside) < 2:
                return messagebox.showwarning(T('Štarty'), T('Pri ručnom rozmiestnení klikni na mapu aspoň 2 štarty vo vnútri červeného rámčeka.'))
            npl = len(inside)
        # a map with this name is already there: overwrite it, or make a new one next to it
        nm_base, md_ = safe_name(mname.get().strip() or 'Earth'), maps_dir_now()
        folder = os.path.join(md_, map_folder_name(nm_base, **mode_flags()))
        if os.path.isdir(folder):
            new_nm = free_map_name(md_, nm_base, **mode_flags())
            ans = messagebox.askyesnocancel(
                T('Mapa už existuje'),
                T('Mapa už existuje:') + '\n' + folder + '\n\n' + T('Áno = prepísať') + '\n' +
                T('Nie = vytvoriť novú mapu s názvom') + f' "{new_nm}"\n' + T('Zrušiť = nič nerobiť') + '\n\n' +
                T('(Úpravy z editora patria pôvodnému názvu.)'))
            if ans is None: return
            if ans is False: mname.set(new_nm)
        remember_place(place.get().strip() or mname.get().strip() or 'place', *picker.sel)
        save_settings()
        btn.config(state='disabled'); bar.start(12)
        box.config(state='normal'); box.delete('1.0', 'end'); box.config(state='disabled')
        kwargs = dict(log=log, center=picker.sel, **kwargs_shape, map_name=mname.get().strip() or 'Earth',
                      style=STYLE_OPTS.get(bstyle.get(), next((v for k, v in STYLE_OPTS.items() if T(k) == bstyle.get()), 'auto')),
                      target=TARGETS[target_key()], gx_dir=gxdir.get(),
                      terrain_mode='slopes',
                      players=npl, layout=lay, manual_starts=inside, resources=RES_OPTS.get(res.get(), 'normal'),
                      stack_tall=bool(stack.get()), apt_levels=int(aptl.get()),
                      terrain_strength=3.0, smooth=1, base_size=int(base.get()),
                      oil_count=int(oil.get()), season=SEASON_OPTS.get(season.get(), 'summer'),
                      time_of_day=TIME_OPTS.get(tod.get(), 2), tree_density=float(td.get()), max_trees=int(mt.get()),
                      bushes=bool(V['bushes'][0].get()), willows=bool(V['willows'][0].get()),
                      nature_details=float(nature_details.get()),
                      building_density=float(bd.get()), sheds=bool(V['sheds'][0].get()),
                      fences=bool(V['fences'][0].get()),
                      footways=bool(V['footways'][0].get()),
                      train=bool(V['train'][0].get()),
                      lamps=bool(V['lamps'][0].get()), details=bool(V['details'][0].get()),
                      cars=bool(V['cars'][0].get()), ai_paths=bool(V['ai_paths'][0].get()), seed=int(seed.get()),
                      apt_model='panel_mix',
                      civilians=bool(V['civilians'][0].get()), traffic=bool(V['traffic'][0].get()),
                      farm_life=bool(V['farm_life'][0].get()), sounds=bool(V['sounds'][0].get()),
                      fortress=bool(fort.get()), fortress_player=0,
                      insurgents=bool(ins.get()), ins_every=int(ins_every.get()), ins_max=int(ins_max.get()),
                      ins_difficulty=INS_LEVELS.get(ins_lvl.get(), 2), outposts=int(outp.get()), ins_prod_interval=int(prod.get()), ins_wave_size=int(wave.get()),
                      ins_localized=bool(loc.get()), ins_oil=int(ins_oil.get()),
                      ins_cap=int(ins_cap.get() or 0), patrols=int(patrols.get()), foot_patrols=int(foot.get()),
                      civ_amount=float(civ_amt.get()),
                      fortress_faction=FORT_FACTIONS.get(ffac.get(), 'GLA'),
                      fortress_level=FORT_LEVELS.get(flvl.get(), 2),
                      mode_clearance=bool(m_clear.get()), mode_zombie=bool(m_zomb.get()), black_map=bool(m_black.get()),
                      mode_zsurvival=bool(m_zs.get()), mode_zdefense=bool(m_zd.get()),
                      mode_invasion=bool(m_invasion.get()), invasion_faction=inv_faction.get(),
                      invasion_air=bool(inv_air.get()), language=tr.LANG,
                      **{key: int(value.get()) for key, value in inv_vars.items()},
                      ins_qrf=bool(qrf.get()),
                      zombie_size=int(z_size.get()), zombie_growth=int(z_grow.get()), zombie_start=int(z_start.get()),
                      zombie_break=int(z_break.get()), zombie_growing=bool(z_growing.get()),
                      zombie_pacing=pacing_options[z_pacing.get()], zombie_live_limit=int(z_limit.get()),
                      zs_start_money=int(zs_money.get()), zd_start_money=int(zd_money.get()), zs_respawn=int(zs_resp.get()), zs_bounty=int(zs_bounty.get()),
                      compact=bool(compact.get()), light=bool(light.get()))
        threading.Thread(target=work, args=((None, k, t, gdir.get(), mdir_gx.get() if TARGETS[target_key()] == 'gx' else mdir.get()), kwargs), daemon=True).start()
    btn.config(command=start)
    root.after(300, picker.fit_selection)
    root.mainloop()
    return 'restart' if restart['flag'] else None


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--cli':
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument('--cli', action='store_true')
        ap.add_argument('place'); ap.add_argument('--km', type=float, default=5.0)
        ap.add_argument('--scale', type=float, default=1.0)
        ap.add_argument('--game', default=catalog.find_game_dir()); ap.add_argument('--out', default=catalog.default_maps_dir())
        ap.add_argument('--invasion', choices=INVASION_ROSTERS, metavar='FACTION', help='Invasion with China, GLA or USA')
        ap.add_argument('--players', type=int, default=4, help='number of player slots')
        a = ap.parse_args()
        run_generation(a.place, a.km, tiles_for(a.km, a.scale), a.game, a.out, players=a.players,
                       mode_invasion=bool(a.invasion), invasion_faction=a.invasion)
    else:
        while gui() == 'restart':
            pass
