"""Reads object footprints and road widths straight from the game's INI archives."""
import os, re, struct, sys


def read_big(path):
    d = open(path, 'rb').read()
    if d[:4] not in (b'BIGF', b'BIG4'):
        raise ValueError('not a .big archive: ' + path)
    n = struct.unpack_from('>I', d, 8)[0]
    p, files = 16, {}
    for _ in range(n):
        off, sz = struct.unpack_from('>II', d, p); p += 8
        e = d.index(b'\0', p)
        files[d[p:e].decode('latin1').replace('/', '\\').lower()] = (off, sz)
        p = e + 1
    return d, files


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class Catalog:
    def __init__(self):
        self.buildings = {}   # name -> (x_extent, y_extent, height)  (full sizes, world units)
        self.road_w = {}      # road type -> width
        self.objects = set()  # every Object / ObjectReskin name the game knows
        self.garrison = {}    # building name -> how many soldiers fit inside (GarrisonContain)
        self.road_segments = 4000   # GameData.ini MaxRoadSegments the game will really use

    @staticmethod
    def load(big_paths):
        cat = Catalog()
        for bp in big_paths:
            if not os.path.isfile(bp):
                continue
            d, files = read_big(bp)
            for fn in ('data\\ini\\object\\civilianbuilding.ini', 'data\\ini\\object\\techbuildings.ini',
                       'data\\ini\\object\\factionbuilding.ini'):
                if fn not in files: continue
                off, sz = files[fn]
                t = d[off:off + sz].decode('latin1')
                for m in re.finditer(r'^Object\s+(\S+)(.*?)^End\s*$', t, re.S | re.M):
                    body = m.group(2)
                    g = lambda k: (re.search(r'^\s*' + k + r'\s*=\s*([^\s;]+)', body, re.M) or [None, None])[1]
                    if g('Geometry') != 'BOX': continue
                    a, b, h = _num(g('GeometryMajorRadius')), _num(g('GeometryMinorRadius')), _num(g('GeometryHeight'))
                    if a and b and a > 0 and b > 0:
                        cat.buildings.setdefault(m.group(1), (2 * a, 2 * b, h or 20.0))
            for fn, (off, sz) in files.items():
                if fn.endswith('.ini') and '\\object\\' in fn:
                    t = d[off:off + sz].decode('latin1')
                    cat.objects.update(re.findall(r'^(?:Object|ObjectReskin)\s+(\S+)', t, re.M))
                    for m in re.finditer(r'^Object\s+(\S+)(.*?)^End\s*$', t, re.S | re.M):
                        g = re.search(r'GarrisonContain.*?ContainMax\s*=\s*(\d+)', m.group(2), re.S)
                        if g: cat.garrison.setdefault(m.group(1), int(g.group(1)))
            if 'data\\ini\\gamedata.ini' in files and not getattr(cat, '_rs_set', False):
                off, sz = files['data\\ini\\gamedata.ini']
                v = re.search(r'^\s*MaxRoadSegments\s*=\s*(\d+)', d[off:off + sz].decode('latin1'), re.M)
                if v: cat.road_segments = int(v.group(1)); cat._rs_set = True
            if 'data\\ini\\roads.ini' in files:
                off, sz = files['data\\ini\\roads.ini']
                t = d[off:off + sz].decode('latin1')
                for m in re.finditer(r'^Road\s+(\S+)(.*?)^End', t, re.S | re.M):
                    w = re.search(r'RoadWidth\s*=\s*([\d.]+)', m.group(2))
                    if w: cat.road_w.setdefault(m.group(1), float(w.group(1)))
        # a loose Data\INI\GameData.ini in the game folder wins over the archives (see game_limits)
        for bp in big_paths:
            v = read_loose_limit(os.path.dirname(bp))
            if v: cat.road_segments = v; break
        return cat


# ---------------- raising the game's road limit (loose Data\INI\GameData.ini, like a mod) ----------------
MARK = '; Zero Hour Earth: raised road limits'
ROAD_LIMITS = {'MaxRoadSegments': 200000, 'MaxRoadVertex': 65531, 'MaxRoadIndex': 65531}   # GeneralsX: segments practically unlimited; vertex/index buffers are 16-bit, the game clamps them to 65531


def loose_gamedata(game_dir):
    return os.path.join(game_dir, 'Data', 'INI', 'GameData.ini')


def read_loose_limit(game_dir):
    p = loose_gamedata(game_dir)
    try:
        v = re.search(r'^\s*MaxRoadSegments\s*=\s*(\d+)', open(p, encoding='latin1').read(), re.M)
        return int(v.group(1)) if v else None
    except OSError:
        return None


def default_gx_dir():
    """GeneralsX installs into %LOCALAPPDATA%\\Programs\\GeneralsX."""
    base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    for p in (os.path.join(base, 'Programs', 'GeneralsX'), os.path.join(base, 'GeneralsX'),
              os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'GeneralsX')):
        if os.path.isfile(os.path.join(p, 'generalszh.exe')):
            return p
    p = os.path.join(base, 'Programs', 'GeneralsX')
    return p if os.path.isdir(p) else ''


def raise_road_limit(game_dir, source_dir=None):
    """Write Data\\INI\\GameData.ini = the game's own GameData.ini with bigger road buffers. An existing loose file
    (from a mod) is edited in place after a backup. Returns the new MaxRoadSegments."""
    p = loose_gamedata(game_dir)
    if os.path.isfile(p):
        text = open(p, encoding='latin1').read()
        if MARK not in text and not os.path.isfile(p + '.zhearth-backup'):
            open(p + '.zhearth-backup', 'w', encoding='latin1').write(text)
    else:
        text = None
        for big in ('INIZH.big', 'INI.big'):
            bp = os.path.join(source_dir or game_dir, big)   # GeneralsX has no INIZH.big: read the game's
            if os.path.isfile(bp):
                d, files = read_big(bp)
                if 'data\\ini\\gamedata.ini' in files:
                    off, sz = files['data\\ini\\gamedata.ini']
                    text = d[off:off + sz].decode('latin1'); break
        if text is None:
            raise RuntimeError('GameData.ini not found in INIZH.big')
    for k, v in ROAD_LIMITS.items():
        text, n = re.subn(r'^(\s*' + k + r'\s*=\s*)\d+', lambda m_: m_.group(1) + str(v), text, count=1, flags=re.M)
        if not n: raise RuntimeError(f'{k} not found in GameData.ini')
    if MARK not in text: text = MARK + '\r\n' + text
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w', encoding='latin1', newline='').write(text)
    return ROAD_LIMITS['MaxRoadSegments']


def restore_road_limit(game_dir):
    p = loose_gamedata(game_dir)
    if os.path.isfile(p + '.zhearth-backup'):
        os.replace(p + '.zhearth-backup', p); return 'restored'
    if os.path.isfile(p) and MARK in open(p, encoding='latin1').read():
        os.remove(p); return 'removed'
    return 'nothing'


def find_game_dir():
    """Best-effort autodetect of the Zero Hour install folder."""
    cands = []
    if sys.platform.startswith('win'):
        try:
            import winreg
            for root in (winreg.HKEY_LOCAL_MACHINE,):
                for sub in (r'SOFTWARE\WOW6432Node\Electronic Arts\EA Games\Command and Conquer Generals Zero Hour',
                            r'SOFTWARE\Electronic Arts\EA Games\Command and Conquer Generals Zero Hour'):
                    try:
                        k = winreg.OpenKey(root, sub)
                        cands.append(winreg.QueryValueEx(k, 'InstallPath')[0])
                    except OSError:
                        pass
        except ImportError:
            pass
        for pf in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), 'C:\\', 'D:\\'):
            if pf:
                for sub in ('EA Games\\Command & Conquer Generals Zero Hour',
                            'Steam\\steamapps\\common\\Command & Conquer Generals - Zero Hour',
                            'Command and Conquer Generals Zero Hour'):
                    cands.append(os.path.join(pf, sub))
    for c in cands:
        if c and os.path.isfile(os.path.join(c, 'INIZH.big')):
            return c
    return ''


def default_maps_dir(target='classic'):
    """Where the game looks for user maps: GeneralsX -> Documents\\GeneralsX\\GeneralsZH\\Maps,
    the classic game (Steam / GeneralsOnline) -> Documents\\Command and Conquer Generals Zero Hour Data\\Maps."""
    docs = os.path.join(os.path.expanduser('~'), 'Documents')
    if target == 'gx':
        return os.path.join(docs, 'GeneralsX', 'GeneralsZH', 'Maps')
    return os.path.join(docs, 'Command and Conquer Generals Zero Hour Data', 'Maps')
