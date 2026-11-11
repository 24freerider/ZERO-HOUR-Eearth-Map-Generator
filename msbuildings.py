"""Building footprints from satellite images (Microsoft Global ML Building Footprints) for places where
OpenStreetMap has (almost) no buildings - Iraq, much of Africa and Asia.

The data is split into Bing-map tiles at zoom 9 (about 70 x 70 km). dataset-links.csv lists every tile:
Location, QuadKey, Size, UploadDate, Url. Each tile is a .csv.gz with one GeoJSON feature per line
({"type": "Feature", "geometry": {"type": "Polygon", ...}, "properties": {"height": .., "confidence": ..}}).
Tiles are cached in ~/.cncearth/msbuildings so a place is downloaded only once."""
import gzip, io, json, math, os, time, urllib.request

LINKS = 'https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv'
CACHE = os.path.join(os.path.expanduser('~'), '.cncearth', 'msbuildings')
UA = {'User-Agent': 'ZeroHourEarth map generator'}
MAX_MB = 100            # Microsoft fallback: never download more than this for one map
# first choice: one global PMTiles file (Google + Microsoft + OSM, combined by VIDA) - only the few tiles over the
# map are read with HTTP range requests, typically 1 - 10 MB
PMTILES_URLS = ('https://data.source.coop/vida/google-microsoft-osm-open-buildings/pmtiles/goog_msft_osm.pmtiles',
                'https://beta.source.coop/vida/google-microsoft-osm-open-buildings/pmtiles/goog_msft_osm.pmtiles')


def _get(url, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)


def quadkey(lat, lon, z=9):
    lat = max(-85.05112878, min(85.05112878, lat))
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    s = math.sin(math.radians(lat))
    y = int((0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n)
    x, y = min(max(x, 0), n - 1), min(max(y, 0), n - 1)
    q = ''
    for i in range(z, 0, -1):
        d, m = 0, 1 << (i - 1)
        if x & m: d += 1
        if y & m: d += 2
        q += str(d)
    return q


def quadkeys(s, w, n, e, z=9):
    keys = set()
    steps = 6
    for i in range(steps + 1):
        for j in range(steps + 1):
            keys.add(quadkey(s + (n - s) * i / steps, w + (e - w) * j / steps, z))
    return sorted(keys)


def _size_mb(txt):
    try:
        t = txt.strip().upper()
        for u, f in (('GB', 1024), ('MB', 1), ('KB', 1 / 1024), ('B', 1 / 1048576)):
            if t.endswith(u): return float(t[:-len(u)]) * f
        return float(t) / 1048576
    except Exception:
        return 0.0


def _links(log):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, 'dataset-links.csv')
    if not os.path.isfile(p) or time.time() - os.path.getmtime(p) > 60 * 86400:
        log('  sťahujem zoznam satelitných dlaždíc budov (Microsoft) ...')
        data = _get(LINKS, 120).read()
        with open(p + '.part', 'wb') as f: f.write(data)
        os.replace(p + '.part', p)
    rows = []
    with open(p, encoding='utf-8', errors='replace') as f:
        head = f.readline().strip().split(',')
        ix = {h: i for i, h in enumerate(head)}
        for line in f:
            c = line.rstrip('\n').split(',')
            if len(c) < len(head): continue
            rows.append((c[ix['QuadKey']], c[ix['Url']], _size_mb(c[ix.get('Size', 2)]), c[ix['Location']]))
    return rows


def _tile_file(url, log):
    name = url.rstrip('/').split('/')[-1]
    sub = url.rstrip('/').split('/')[-3:-1]
    p = os.path.join(CACHE, '_'.join(sub + [name]))
    if not os.path.isfile(p):
        log(f'  sťahujem {name} ...')
        with _get(url, 300) as r, open(p + '.part', 'wb') as f:
            while True:
                b = r.read(1 << 20)
                if not b: break
                f.write(b)
        os.replace(p + '.part', p)
    return p


def fetch(bbox, log=print):
    """bbox = (south, west, north, east). Returns [(tags, [(lat, lon), ...])] like OSM.buildings.
    Only the map's area: cached per area in ~/.cncearth/msbuildings, so a place is fetched once."""
    import hashlib
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, 'area_' + hashlib.sha1(('%.5f,%.5f,%.5f,%.5f' % tuple(bbox)).encode()).hexdigest() + '.json')
    if os.path.isfile(cp):
        try:
            out = json.load(open(cp))
            log(f'  satelitné budovy z cache: {len(out)}')
            return [(t, [tuple(p) for p in pts]) for t, pts in out]
        except Exception:
            pass
    out = None
    try:
        import pmtiles
        for url in PMTILES_URLS:
            try:
                log('  satelitné budovy (Google + Microsoft): čítam len dlaždice nad mapou ...')
                rings = pmtiles.buildings(pmtiles.RangeSource(url), bbox, log)
                out = [({'building': 'yes', 'source': 'satellite'}, r) for r in rings]
                break
            except Exception as ex:
                log(f'  {url.split("/")[2]}: {ex}')
    except Exception as ex:
        log(f'  PMTiles: {ex}')
    if out is None:
        out = _fetch_ms(bbox, log)
    try:
        json.dump(out, open(cp, 'w'))
    except Exception:
        pass
    return out


def _fetch_ms(bbox, log=print):
    """Fallback: Microsoft's tiles (a whole ~70 km tile is downloaded, filtered, then deleted)."""
    s, w, n, e = bbox
    want = set(quadkeys(s, w, n, e))
    rows = [r for r in _links(log) if r[0] in want]
    if not rows:
        log('  satelitné budovy: pre toto miesto nie sú dáta'); return []
    total = sum(r[2] for r in rows)
    if total > MAX_MB:
        log(f'  satelitné budovy: dáta sú priveľké ({total:.0f} MB) - preskakujem'); return []
    log(f'  satelitné budovy: {len(rows)} dlaždíc, {total:.1f} MB ({", ".join(sorted({r[3] for r in rows}))})')
    out = []
    for qk, url, mb, loc in rows:
        try:
            path = _tile_file(url, log)
        except Exception as ex:
            log(f'  satelitné budovy: {qk} sa nepodarilo stiahnuť ({ex})'); continue
        with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                if line[0] == '"':                      # a csv cell with doubled quotes
                    line = line[1:-1].replace('""', '"')
                try:
                    ft = json.loads(line)
                except Exception:
                    continue
                g = ft.get('geometry') or {}
                if g.get('type') != 'Polygon' or not g.get('coordinates'): continue
                ring = g['coordinates'][0]
                lon0, lat0 = ring[0][0], ring[0][1]
                if not (s <= lat0 <= n and w <= lon0 <= e): continue
                tags = {'building': 'yes', 'source': 'microsoft'}
                h = (ft.get('properties') or {}).get('height', -1)
                try:
                    if h and float(h) > 0: tags['height'] = f'{float(h):.1f}'
                except Exception:
                    pass
                out.append((tags, [(p[1], p[0]) for p in ring]))
        try: os.remove(path)          # the big tile is not kept - only the area (see fetch)
        except OSError: pass
    log(f'  satelitné budovy: {len(out)} budov v oblasti')
    return out
