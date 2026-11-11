"""Minimal PMTiles v3 + Mapbox Vector Tile reader (pure Python, HTTP range requests), enough to fetch building
polygons for one small area out of a huge global file without downloading the whole thing.

PMTiles v3: 127-byte header, a root directory in the first 16 KB, leaf directories, tiles addressed by a
Hilbert-curve tile id. Directories and tiles are usually gzip-compressed. Spec: github.com/protomaps/PMTiles."""
import gzip, math, struct, urllib.request, zlib

UA = 'ZeroHourEarth map generator'


class RangeSource:
    def __init__(self, url, timeout=60):
        self.url, self.timeout, self.bytes = url, timeout, 0

    def read(self, offset, length):
        req = urllib.request.Request(self.url, headers={'User-Agent': UA, 'Range': f'bytes={offset}-{offset + length - 1}'})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = r.read()
            if r.status == 200 and len(data) > length:      # server ignored the range
                data = data[offset:offset + length]
        self.bytes += len(data)
        return data


class FileSource:          # for tests
    def __init__(self, path):
        self.f = open(path, 'rb'); self.bytes = 0

    def read(self, offset, length):
        self.f.seek(offset); d = self.f.read(length); self.bytes += len(d); return d


def _decompress(data, kind):
    if kind in (0, 1): return data
    if kind == 2:
        try: return gzip.decompress(data)
        except OSError: return zlib.decompress(data)
    raise ValueError('nepodporovaná kompresia PMTiles (%d)' % kind)


def _varint(b, i):
    shift = res = 0
    while True:
        c = b[i]; i += 1
        res |= (c & 0x7f) << shift
        if not c & 0x80: return res, i
        shift += 7


def zxy_to_tileid(z, x, y):
    acc = ((1 << (2 * z)) - 1) // 3
    n = 1 << z
    d = 0
    s = n >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:                                  # rotate
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


def parse_dir(b):
    n, i = _varint(b, 0)
    ids, runs, lens, offs = [], [], [], []
    last = 0
    for _ in range(n):
        v, i = _varint(b, i); last += v; ids.append(last)
    for _ in range(n):
        v, i = _varint(b, i); runs.append(v)
    for _ in range(n):
        v, i = _varint(b, i); lens.append(v)
    for k in range(n):
        v, i = _varint(b, i)
        offs.append(offs[k - 1] + lens[k - 1] if (v == 0 and k > 0) else v - 1)
    return list(zip(ids, runs, lens, offs))


class PMTiles:
    def __init__(self, src):
        self.src = src
        h = src.read(0, 16384)
        if h[:7] != b'PMTiles' or h[7] != 3: raise ValueError('nie je PMTiles v3')
        (self.root_off, self.root_len, self.meta_off, self.meta_len, self.leaf_off, self.leaf_len,
         self.data_off, self.data_len) = struct.unpack_from('<8Q', h, 8)
        self.icomp, self.tcomp, self.ttype, self.minz, self.maxz = h[97], h[98], h[99], h[100], h[101]
        root = h[self.root_off:self.root_off + self.root_len] if self.root_off + self.root_len <= len(h) else \
            src.read(self.root_off, self.root_len)
        self.root = parse_dir(_decompress(root, self.icomp))
        self._leaves = {}

    def _find(self, entries, tid):
        lo, hi = 0, len(entries) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if entries[mid][0] <= tid: best = mid; lo = mid + 1
            else: hi = mid - 1
        return entries[best] if best is not None else None

    def tile(self, z, x, y):
        tid = zxy_to_tileid(z, x, y)
        entries = self.root
        for _ in range(4):
            e = self._find(entries, tid)
            if e is None: return None
            t0, run, ln, off = e
            if run == 0:                                       # a leaf directory
                key = (off, ln)
                if key not in self._leaves:
                    self._leaves[key] = parse_dir(_decompress(self.src.read(self.leaf_off + off, ln), self.icomp))
                entries = self._leaves[key]
                continue
            if tid < t0 + run:
                return _decompress(self.src.read(self.data_off + off, ln), self.tcomp)
            return None
        return None


# ---------------------------------------------------------------- Mapbox Vector Tile (protobuf) decoding
def _fields(b):
    i, n = 0, len(b)
    while i < n:
        k, i = _varint(b, i)
        f, wt = k >> 3, k & 7
        if wt == 0:
            v, i = _varint(b, i); yield f, v
        elif wt == 2:
            ln, i = _varint(b, i); yield f, b[i:i + ln]; i += ln
        elif wt == 1: yield f, b[i:i + 8]; i += 8
        elif wt == 5: yield f, b[i:i + 4]; i += 4
        else: raise ValueError('bad protobuf')


def _packed(b):
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i); out.append(v)
    return out


def mvt_polygons(data):
    """-> [(extent, [ring [(x, y)...]])] exterior rings of every polygon feature in every layer."""
    out = []
    for f, layer in _fields(data):
        if f != 3: continue
        extent, feats = 4096, []
        for lf, v in _fields(layer):
            if lf == 5: extent = v
            elif lf == 2: feats.append(v)
        for ft in feats:
            gtype, geom = 0, b''
            for ff, v in _fields(ft):
                if ff == 3: gtype = v
                elif ff == 4: geom = v
            if gtype != 3: continue
            cmds = _packed(geom)
            x = y = 0; i = 0; ring = []; rings = []
            while i < len(cmds):
                c = cmds[i]; i += 1
                cid, cnt = c & 7, c >> 3
                if cid in (1, 2):
                    for _ in range(cnt):
                        dx, dy = cmds[i], cmds[i + 1]; i += 2
                        x += (dx >> 1) ^ -(dx & 1); y += (dy >> 1) ^ -(dy & 1)
                        if cid == 1:
                            if len(ring) >= 3: rings.append(ring)
                            ring = [(x, y)]
                        else:
                            ring.append((x, y))
                elif cid == 7:
                    if len(ring) >= 3: rings.append(ring)
                    ring = []
            if len(ring) >= 3: rings.append(ring)
            for r in rings:
                a = sum(r[k][0] * r[(k + 1) % len(r)][1] - r[(k + 1) % len(r)][0] * r[k][1] for k in range(len(r)))
                if a > 0: out.append((extent, r))          # exterior ring (holes have the other sign)
    return out


def tile_range(s, w, n, e, z):
    def xy(lat, lon):
        lat = max(-85.0511, min(85.0511, lat)); k = 1 << z
        x = int((lon + 180) / 360 * k)
        y = int((1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * k)
        return min(max(x, 0), k - 1), min(max(y, 0), k - 1)
    x0, y0 = xy(n, w); x1, y1 = xy(s, e)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def to_latlon(z, tx, ty, extent, px, py):
    k = 1 << z
    lon = (tx + px / extent) / k * 360 - 180
    m = math.pi * (1 - 2 * (ty + py / extent) / k)
    return math.degrees(math.atan(math.sinh(m))), lon


def buildings(src, bbox, log=print, max_tiles=40):
    """Building rings [(lat, lon)] inside bbox (s, w, n, e) from a PMTiles source of footprints."""
    pm = PMTiles(src)
    s, w, n, e = bbox
    z = pm.maxz
    tiles = tile_range(s, w, n, e, z)
    while len(tiles) > max_tiles and z > pm.minz:
        z -= 1; tiles = tile_range(s, w, n, e, z)
    out = []
    for tx, ty in tiles:
        data = pm.tile(z, tx, ty)
        if not data: continue
        for extent, ring in mvt_polygons(data):
            pts = [to_latlon(z, tx, ty, extent, x, y) for x, y in ring]
            la, lo = pts[0]
            if s <= la <= n and w <= lo <= e:
                out.append(pts + [pts[0]])
    log(f'  satelitné budovy: {len(out)} budov z {len(tiles)} dlaždíc (z{z}), stiahnuté {src.bytes / 1048576:.1f} MB')
    return out
