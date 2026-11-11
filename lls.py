"""Real building heights in Slovakia from the national airborne laser scan (LLS, UGKK SR, CC BY 4.0).

UGKK publishes the surface model DMP 1.0 (roofs, trees) and the terrain model DMR 5.0 as two huge ZIPs of 1 m
GeoTIFF tiles (ETRS89-TM34). Like the PMTiles reader, only what lies under the map is read with HTTP range
requests: the ZIP directory once (cached), then the few tiles over the map. Roof height = surface - terrain,
taken inside every OSM footprint. Results are cached per area in ~/.cncearth/lls."""
import io, json, math, os, re, struct, zlib, hashlib, urllib.request
import numpy as np

URLS = {'dsm': 'https://opendata.skgeodesy.sk/static/INSPIRE/Elevation/DSM1_0_etrs89-tm34_h.zip',
        'dtm': 'https://opendata.skgeodesy.sk/static/INSPIRE/Elevation/DTM5_0_etrs89-tm34_h.zip'}
CACHE = os.path.join(os.path.expanduser('~'), '.cncearth', 'lls')
UA = 'ZeroHourEarth map generator'
MAX_TILES = 12


def in_slovakia(lat, lon):
    return 47.7 <= lat <= 49.65 and 16.8 <= lon <= 22.6


def utm34(lat, lon):
    """ETRS89 / TM34 (= UTM zone 34N on GRS80): lat, lon in degrees -> easting, northing in metres."""
    a, f = 6378137.0, 1 / 298.257222101
    k0, lon0 = 0.9996, math.radians(21.0)
    e2 = f * (2 - f); ep2 = e2 / (1 - e2)
    p, l = math.radians(lat), math.radians(lon)
    N = a / math.sqrt(1 - e2 * math.sin(p) ** 2)
    T = math.tan(p) ** 2; C = ep2 * math.cos(p) ** 2; A = math.cos(p) * (l - lon0)
    M = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * p
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * p)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * p)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * p))
    x = k0 * N * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A ** 5 / 120) + 500000.0
    y = k0 * (M + N * math.tan(p) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A ** 4 / 24
                                     + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A ** 6 / 720))
    return x, y


class Range:
    """A remote file read with HTTP Range requests (refuses a server that would send the whole 150 GB)."""
    def __init__(self, url):
        self.url, self.bytes = url, 0

    def read(self, off, n):
        req = urllib.request.Request(self.url, headers={'User-Agent': UA, 'Range': f'bytes={off}-{off + n - 1}'})
        try:
            r = urllib.request.urlopen(req, timeout=120)
        except Exception as ex:
            raise RuntimeError(f'{self.url.rsplit("/", 1)[-1]}: {ex}')
        with r:
            if r.status != 206:
                raise RuntimeError(f'server neposiela časti súboru (Range) - HTTP {r.status}')
            size = r.headers.get('Content-Range', '').rsplit('/', 1)[-1]
            self.size = int(size) if size.isdigit() else getattr(self, 'size', None)
            d = r.read()
        self.bytes += len(d)
        return d


def _zip_index(src, key, log):
    """[(name, local_header_offset, compressed_size, method)] from the ZIP's central directory (ZIP64 aware)."""
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, key + '_index.json')
    if os.path.isfile(cp):
        return json.load(open(cp))
    log('  laser (ZBGIS): čítam obsah balíka ' + key + ' (raz) ...')
    src.read(0, 1)
    size = src.size
    tail = src.read(max(0, size - 65558), min(size, 65558))
    i = tail.rfind(b'PK\x05\x06')
    if i < 0: raise RuntimeError('nie je ZIP')
    cd_n, cd_size, cd_off = struct.unpack_from('<HII', tail, i + 10)
    j = tail.rfind(b'PK\x06\x06')                          # ZIP64 end of central directory
    if j >= 0:
        cd_n, = struct.unpack_from('<Q', tail, j + 32)
        cd_size, cd_off = struct.unpack_from('<QQ', tail, j + 40)
    cd = src.read(cd_off, cd_size)
    out, p = [], 0
    while p + 46 <= len(cd) and cd[p:p + 4] == b'PK\x01\x02':
        method, = struct.unpack_from('<H', cd, p + 10)
        csize, usize = struct.unpack_from('<II', cd, p + 20)
        nl, el, cl = struct.unpack_from('<HHH', cd, p + 28)
        off, = struct.unpack_from('<I', cd, p + 42)
        name = cd[p + 46:p + 46 + nl].decode('utf-8', 'replace')
        ex = cd[p + 46 + nl:p + 46 + nl + el]
        q = 0
        while q + 4 <= len(ex):                            # ZIP64 extra field: the real sizes / offset
            hid, hl = struct.unpack_from('<HH', ex, q)
            if hid == 1:
                vals = list(struct.unpack_from('<' + 'Q' * (hl // 8), ex, q + 4)); k = 0
                if usize == 0xFFFFFFFF: usize = vals[k]; k += 1
                if csize == 0xFFFFFFFF: csize = vals[k]; k += 1
                if off == 0xFFFFFFFF: off = vals[k]
            q += 4 + hl
        if name.lower().endswith(('.tif', '.tiff')):
            out.append([name, off, csize, method])
        p += 46 + nl + el + cl
    json.dump(out, open(cp, 'w'))
    return out


def _member(src, ent):
    name, off, csize, method = ent
    h = src.read(off, 30)
    nl, el = struct.unpack_from('<HH', h, 26)
    data = src.read(off + 30 + nl + el, csize)
    if method == 8: data = zlib.decompress(data, -15)
    elif method != 0: raise RuntimeError('nepodporovaná kompresia ZIP')
    return data


def _tif(data):
    """GeoTIFF -> (array, x0, y0, px, py): the top-left corner and the pixel size in metres."""
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    sc = im.tag_v2.get(33550); tp = im.tag_v2.get(33922)
    if not sc or not tp: raise RuntimeError('GeoTIFF bez súradníc')
    arr = np.asarray(im, dtype=np.float32)
    return arr, float(tp[3]) - float(tp[0]) * sc[0], float(tp[4]) + float(tp[1]) * sc[1], float(sc[0]), float(sc[1])


def _tile_xy(name):
    """The tile's corner from its file name: two numbers that look like a TM34 easting / northing (m or km)."""
    nums = [int(n) for n in re.findall(r'\d+', os.path.basename(name))]
    for i in range(len(nums)):
        for j in range(len(nums)):
            if i == j: continue
            for s in (1, 10, 100, 1000):
                e, n = nums[i] * s, nums[j] * s
                if 150000 <= e <= 900000 and 5000000 <= n <= 5600000:
                    return e, n
    return None


def _grids(kind, e0, n0, e1, n1, log):
    src = Range(URLS[kind])
    idx = _zip_index(src, kind, log)
    pos = [(ent, _tile_xy(ent[0])) for ent in idx]
    pos = [(ent, xy) for ent, xy in pos if xy]
    if not pos: raise RuntimeError('nepoznám rozdelenie dlaždíc v balíku ' + kind)
    xs = sorted({xy[0] for _, xy in pos}); ys = sorted({xy[1] for _, xy in pos})
    step = min([b - a for a, b in zip(xs, xs[1:]) if b > a] + [b - a for a, b in zip(ys, ys[1:]) if b > a] + [5000])
    want = [ent for ent, (x, y) in pos if x - step <= e1 and x + 2 * step >= e0 and y - step <= n1 and y + 2 * step >= n0]
    want = sorted(want, key=lambda ent: math.hypot(_tile_xy(ent[0])[0] - (e0 + e1) / 2, _tile_xy(ent[0])[1] - (n0 + n1) / 2))
    grids = []
    for ent in want[:MAX_TILES * 2]:
        arr, x0, y0, px, py = _tif(_member(src, ent))
        x1, y1 = x0 + arr.shape[1] * px, y0 - arr.shape[0] * py
        if x1 >= e0 and x0 <= e1 and y0 >= n0 and y1 <= n1:
            grids.append((arr, x0, y0, px, py))
        if len(grids) >= MAX_TILES: break
    log(f'  laser (ZBGIS) {kind.upper()}: {len(grids)} dlaždíc, stiahnuté {src.bytes / 1048576:.1f} MB')
    return grids


def _sample(grids, e, n):
    for arr, x0, y0, px, py in grids:
        c, r = int((e - x0) / px), int((y0 - n) / py)
        if 0 <= r < arr.shape[0] and 0 <= c < arr.shape[1]:
            v = float(arr[r, c])
            return v if -100 < v < 3000 else None
    return None


def heights(buildings, bbox, log=print):
    """{osm id: roof height in m} for the buildings (OSM [(tags, ring)]) inside bbox (s, w, n, e)."""
    s, w, n, e = bbox
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, 'area_' + hashlib.sha1(('%.5f,%.5f,%.5f,%.5f' % tuple(bbox)).encode()).hexdigest() + '.json')
    if os.path.isfile(cp):
        try:
            out = json.load(open(cp)); log(f'  výšky budov (laser) z cache: {len(out)}'); return out
        except Exception:
            pass
    pts = [utm34(la, lo) for la, lo in ((s, w), (s, e), (n, w), (n, e))]
    e0, e1 = min(p[0] for p in pts), max(p[0] for p in pts)
    n0, n1 = min(p[1] for p in pts), max(p[1] for p in pts)
    dsm = _grids('dsm', e0, n0, e1, n1, log)
    dtm = _grids('dtm', e0, n0, e1, n1, log)
    out = {}
    for tags, ring in buildings:
        oid = tags.get('_osm')
        if not oid or len(ring) < 4: continue
        poly = [utm34(la, lo) for la, lo in ring]
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
        st = max(1.0, math.sqrt((bx1 - bx0) * (by1 - by0) / 300))
        vals = []
        y = by0 + st / 2
        while y < by1:
            x = bx0 + st / 2
            while x < bx1:
                inside = False
                for k in range(len(poly) - 1):
                    (xa, ya), (xb, yb) = poly[k], poly[k + 1]
                    if (ya > y) != (yb > y) and x < (xb - xa) * (y - ya) / (yb - ya) + xa:
                        inside = not inside
                if inside:
                    a_, b_ = _sample(dsm, x, y), _sample(dtm, x, y)
                    if a_ is not None and b_ is not None: vals.append(a_ - b_)
                x += st
            y += st
        if len(vals) >= 4:
            h = float(np.percentile(vals, 80))           # the roof, not the eaves or a tree beside the wall
            if 2.0 <= h <= 200: out[oid] = round(h, 1)
    log(f'  výšky budov (laser ZBGIS): {len(out)} budov')
    try: json.dump(out, open(cp, 'w'))
    except Exception: pass
    return out
