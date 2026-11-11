"""Offline test: synthetic town + river + lake + forest on synthetic hills."""
import math, random, struct, sys, time
import numpy as np
from catalog import Catalog
from online import OSM
from geo import Proj
from mapio import MapFile, _wstr
import generator

import os
import catalog
HERE = os.path.dirname(os.path.abspath(__file__))
# usage: python selftest.py [game folder or path to INIZH.big]
_arg = sys.argv[1] if len(sys.argv) > 1 and __name__ == '__main__' else (os.environ.get('ZH_GAME_DIR') or catalog.find_game_dir() or '')
BIG = _arg if _arg.lower().endswith('.big') else os.path.join(_arg, 'INIZH.big')
if not os.path.isfile(BIG) and os.path.isfile('/mnt/user-data/uploads/INIZH.big'):
    BIG = '/mnt/user-data/uploads/INIZH.big'
TEMPLATE = os.path.join(HERE, 'template.map')
OUT_DIR = os.environ.get('TEMP') or os.environ.get('TMPDIR') or '/tmp'


def check_trigger_format():
    m = MapFile.read(TEMPLATE)
    b = next(c[2] for c in m.chunks if c[0] == 'PolygonTriggers')
    n = struct.unpack_from('<I', b, 0)[0]; p = 4; out = [struct.pack('<I', n)]
    for _ in range(n):
        nm = _wstr(b[p + 2:p + 2 + struct.unpack_from('<H', b, p)[0]].decode()); p += len(nm)
        ly_len = struct.unpack_from('<H', b, p)[0]; ly = b[p:p + 2 + ly_len]; p += 2 + ly_len
        tid, iw, ir, rs, npt = struct.unpack_from('<IBBII', b, p); p += 14
        pts = b[p:p + 12 * npt]; p += 12 * npt
        out.append(nm + ly + struct.pack('<IBBII', tid, iw, ir, rs, npt) + pts)
    ok = b''.join(out) == b and p == len(b)
    print('PolygonTriggers format check:', 'OK' if ok else 'MISMATCH', 'triggers', n)
    return ok


def synthetic_osm(lat0, lon0, size_m):
    o = OSM()
    kx = 111320.0 * math.cos(math.radians(lat0)); ky = 110574.0
    ll = lambda x, y: (lat0 + (y - size_m / 2) / ky, lon0 + (x - size_m / 2) / kx)
    rng = random.Random(3)
    # street grid with a main road
    for i in range(0, int(size_m), 180):
        o.roads.append(('residential', [ll(i, 400), ll(i, size_m - 400)]))
        o.roads.append(('residential', [ll(400, i), ll(size_m - 400, i)]))
    o.roads.append(('primary', [ll(0, size_m / 2), ll(size_m * .4, size_m / 2 + 100), ll(size_m, size_m / 2 - 150)]))
    o.roads.append(('track', [ll(100, 100), ll(900, 400), ll(1400, 300)]))
    # buildings along streets
    for gx in range(400, int(size_m) - 400, 180):
        for gy in range(400, int(size_m) - 400, 180):
            for k in range(6):
                bx = gx + 15 + rng.random() * 120; by = gy + 15 + rng.random() * 120
                w, h = 8 + rng.random() * 8, 9 + rng.random() * 10
                a = math.radians(rng.choice([0, 0, 90, 17]))
                pts = []
                for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h), (-w, -h)):
                    pts.append(ll(bx + dx * math.cos(a) - dy * math.sin(a), by + dx * math.sin(a) + dy * math.cos(a)))
                o.buildings.append(({'building': rng.choice(['yes', 'house', 'yes', 'garage', 'apartments'])}, pts))
                if k == 0 and rng.random() < 0.5:   # shops / pubs / offices inside some houses
                    o.pois.append((rng.choice(['pub', 'shop', 'civic']), *ll(bx, by)))
            # garden fences and hedges along the block edges
            o.barriers.append((rng.choice(['fence', 'fence', 'hedge', 'wall', 'chain']),
                               [ll(gx + 12, gy + 12), ll(gx + 168, gy + 12), ll(gx + 168, gy + 168)]))
    # a farmyard with plain building=yes barns and an industrial estate
    for x0, kind in ((600, 'farmyard'), (3300, 'industrial')):
        o.landuse.append((kind, [ll(x0, 3300), ll(x0 + 300, 3300), ll(x0 + 300, 3500), ll(x0, 3500), ll(x0, 3300)]))
        for i in range(3):
            bx = x0 + 40 + i * 90
            o.buildings.append(({'building': 'yes'}, [ll(bx, 3350), ll(bx + 40, 3350), ll(bx + 40, 3375), ll(bx, 3375), ll(bx, 3350)]))
    o.buildings.append(({'building': 'industrial'}, [ll(2800, 2800), ll(2950, 2800), ll(2950, 2880), ll(2800, 2880), ll(2800, 2800)]))
    o.buildings.append(({'building': 'church'}, [ll(1900, 1900), ll(1930, 1900), ll(1930, 1950), ll(1900, 1950), ll(1900, 1900)]))
    # lake & river & forest
    o.water.append([ll(3000 + 220 * math.cos(t), 700 + 150 * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 24)])
    o.waterways.append(('river', [ll(0, 2900), ll(600, 3000), ll(1300, 2800), ll(2000, 3100), ll(2600, 3000), ll(size_m, 3300)]))
    o.waterways.append(('stream', [ll(1500, 0), ll(1550, 800), ll(1480, 1500)]))
    o.forests.append([ll(100, 3200), ll(1400, 3500), ll(1500, 3900), ll(300, 3900), ll(100, 3200)])
    o.forests.append([ll(2400, 1700), ll(3800, 1500), ll(3900, 2500), ll(2500, 2600), ll(2400, 1700)])
    o.landuse.append(('farmland', [ll(2000, 3300), ll(3000, 3300), ll(3000, 3900), ll(2000, 3900), ll(2000, 3300)]))
    return o


def synthetic_dem(lat, lon):
    x = (lon - 19.80) * 111320 * math.cos(math.radians(48.43)); y = (lat - 48.43) * 110574
    return 380 + 60 * np.sin(x / 900) * np.cos(y / 1100) + 0.03 * x + 25 * np.exp(-((x - 1200) ** 2 + (y + 1000) ** 2) / 600 ** 2)


if __name__ == '__main__':
    t0 = time.time()
    if not os.path.isfile(BIG):
        sys.exit('INIZH.big not found. Usage: python selftest.py "C:\\path\\to\\Zero Hour"')
    ok = check_trigger_format()
    cat = Catalog.load([BIG])
    print('catalog buildings', len(cat.buildings), 'roads', len(cat.road_w))
    lat0, lon0, size, tiles = 48.43, 19.80, 4000, 400
    proj = Proj(lat0, lon0, size, tiles)
    osm = synthetic_osm(lat0, lon0, size)
    g = generator.generate(osm, synthetic_dem, proj, tiles, cat, TEMPLATE, os.path.join(OUT_DIR, 'test_gen.map'), seed=1)
    print(g.stats, 'time', round(time.time() - t0, 1))
    g.preview().resize((800, 800)).save(os.path.join(OUT_DIR, 'preview.png'))
    m2 = MapFile.read(os.path.join(OUT_DIR, 'test_gen.map'))
    import collections
    print('reread: heightmap', m2.height.w, 'objects', len(m2.objects), collections.Counter(o.name for o in m2.objects).most_common(14))
    m2.write(os.path.join(OUT_DIR, 'test_gen2.map'), compress=open(os.path.join(OUT_DIR, 'test_gen.map'), 'rb').read(4) == b'EAR\x00')
    print('roundtrip of generated map identical:', open(os.path.join(OUT_DIR, 'test_gen.map'), 'rb').read() == open(os.path.join(OUT_DIR, 'test_gen2.map'), 'rb').read())
