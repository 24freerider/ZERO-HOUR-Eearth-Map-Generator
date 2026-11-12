"""All internet access: Nominatim, Overpass (OpenStreetMap) and public elevation tiles."""
import hashlib, io, json, math, os, tempfile, time
import numpy as np

UA = {'User-Agent': 'ZeroHourEarth/0.51.2 (hobby Zero Hour map generator)'}
CACHE = os.path.join(os.path.expanduser('~'), '.cncearth', 'cache')
OVERPASS = ['https://overpass-api.de/api/interpreter',
            'https://overpass.kumi.systems/api/interpreter',
            'https://overpass.private.coffee/api/interpreter',
            'https://maps.mail.ru/osm/tools/overpass/api/interpreter']
DEM_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'


def _cache_path(key, ext):
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest() + ext)


def _write_atomic(path, data, mode='wb'):
    """Publish a complete cache entry after writing to a unique temporary file."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode=mode, dir=os.path.dirname(path) or '.',
                                         suffix='.part', delete=False) as temporary:
            temporary_path = temporary.name
            temporary.write(data)
        for attempt in range(5):
            try:
                os.replace(temporary_path, path)
                break
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32) or attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def geocode(query):
    import requests
    r = requests.get('https://nominatim.openstreetmap.org/search',
                     params={'q': query, 'format': 'json', 'limit': 1}, headers=UA, timeout=30)
    r.raise_for_status()
    j = r.json()
    if not j:
        raise RuntimeError(f'Miesto "{query}" som nenašiel.')
    return float(j[0]['lat']), float(j[0]['lon']), j[0].get('display_name', query)


def geocode_many(query, limit=6):
    """Several candidates for a place name: [(lat, lon, display_name)]. Only on a click of the user (no autocomplete)."""
    import requests
    r = requests.get('https://nominatim.openstreetmap.org/search',
                     params={'q': query, 'format': 'json', 'limit': limit, 'dedupe': 1}, headers=UA, timeout=30)
    r.raise_for_status()
    out = [(float(j['lat']), float(j['lon']), j.get('display_name', query)) for j in r.json()]
    if not out:
        raise RuntimeError(f'Miesto "{query}" som nenašiel.')
    return out


# ------------------------------------------------------------------ OSM
class OSM:
    def __init__(self):
        self.roads = []       # (highway, [(lat, lon)])
        self.roundabouts = [] # indices into roads: junction=roundabout ways
        self.bridge_ways = [] # [(lat, lon)] lines of roads tagged bridge=* / layer>0 (the upper road of an overpass)
        self.buildings = []   # (tags, [(lat, lon)])
        self.water = []       # [(lat, lon)] rings
        self.waterways = []   # (kind, [(lat, lon)])
        self.waterway_width = []   # metres from the OSM width tag per waterway, 0 = unknown
        self.forests = []     # rings
        self.landuse = []     # (kind, ring)
        self.fuel = []        # (lat, lon) petrol stations
        self.water_kind = []  # per water ring: lake / pond / river / ...
        self.forest_leaf = [] # leaf type per forest ring: broadleaved / needleleaved / mixed
        self.pitches = []     # (sport, ring)
        self.trees = []       # (lat, lon) single trees
        self.areas = []       # (kind, ring): cemetery / playground / park / parking
        self.points = []      # (kind, lat, lon): bus_stop / playground / church
        self.tree_rows = []   # [(lat, lon)] lines
        self.pois = []        # (kind, lat, lon): pub / shop / civic / church - what is inside a building
        self.barriers = []    # (kind, [(lat, lon)]): fence / chain / wall / stone / brick / concrete / hedge


POI_AMENITY = ('pub|bar|restaurant|cafe|fast_food|biergarten|bank|pharmacy|post_office|school|kindergarten|'
               'townhall|police|fire_station|library|community_centre|doctors|clinic|hospital|theatre|cinema|'
               'marketplace|nightclub')
PUB = {'pub', 'bar', 'restaurant', 'cafe', 'fast_food', 'biergarten', 'nightclub'}
CIVIC = {'school', 'kindergarten', 'townhall', 'police', 'fire_station', 'library', 'community_centre',
         'clinic', 'hospital', 'theatre', 'cinema', 'university', 'college'}


def poi_kind(tags):
    """What a shop / amenity / office tag says the building is used for, or None."""
    a = tags.get('amenity', '')
    if a == 'place_of_worship': return 'church'
    if a in PUB: return 'pub'
    if a in ('hospital', 'clinic') or tags.get('healthcare') in ('hospital', 'clinic', 'centre'): return 'hospital'
    if a in CIVIC: return 'civic'
    if a in ('bank', 'pharmacy', 'post_office', 'marketplace', 'doctors'): return 'shop'
    if tags.get('shop') or tags.get('office') or tags.get('craft'): return 'shop'
    if tags.get('tourism') in ('hotel', 'guest_house', 'motel', 'hostel'): return 'pub'
    return None


def barrier_kind(tags):
    b = tags.get('barrier')
    if b == 'hedge': return 'hedge'
    if b == 'fence':
        ft = tags.get('fence_type', '')
        if ft in ('chain_link', 'metal', 'wire', 'barbed_wire', 'electric', 'mesh', 'railing', 'metal_bars'): return 'chain'
        if ft in ('concrete', 'panel'): return 'concrete'
        return 'fence'
    if b == 'wall':
        wt = (tags.get('wall', '') + ' ' + tags.get('material', '')).strip()
        if 'brick' in wt: return 'brick'
        if 'concrete' in wt or 'noise' in wt: return 'concrete'
        if 'stone' in wt or 'dry_stone' in wt: return 'stone'
        return 'wall'
    return None


ROAD_RE = ('motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|track|path|'
           'pedestrian|footway|cycleway|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link|road|busway')
PAVED = {'asphalt', 'paved', 'concrete', 'concrete:plates', 'concrete:lanes', 'chipseal', 'metal'}
CONCRETE_ROAD = {'concrete', 'concrete:plates', 'concrete:lanes'}
COBBLE = {'sett', 'cobblestone', 'unhewn_cobblestone', 'paving_stones', 'bricks', 'paving_stones:30'}
GRAVEL = {'gravel', 'fine_gravel', 'compacted', 'pebblestone', 'shells'}
DIRT = {'unpaved', 'dirt', 'ground', 'earth', 'grass', 'mud', 'sand', 'grass_paver', 'woodchips'}


def road_kind(hw, tags):
    """Highway class adjusted by the OSM surface: asphalt stays asphalt, gravel/dirt/cobbles get their own look."""
    sf = tags.get('surface', '').split(';')[0].strip()
    if hw.endswith('_link'): hw = hw[:-5]          # slip roads / connectors look like the road they belong to
    if hw == 'road': hw = 'unclassified'
    if hw == 'busway': hw = 'service'
    tt = tags.get('tracktype', '')
    if hw == 'track':
        if sf in CONCRETE_ROAD: return 'street_concrete'
        if sf in PAVED or (not sf and tt == 'grade1'): return 'track_paved'
        if sf in COBBLE: return 'street_cobble'
        if sf in GRAVEL or (not sf and tt == 'grade2'): return 'track_gravel'
        if tt in ('grade4', 'grade5') or sf in ('grass', 'mud'): return 'track_rutted'
        return 'track'
    if hw == 'path':
        if sf in PAVED or sf in COBBLE: return 'path_paved'
        return 'path'
    if hw == 'pedestrian':
        return 'street_cobble' if sf in COBBLE else 'pedestrian'
    if hw in ('footway', 'cycleway'):
        # pavements drawn along a street are already part of the street; crossings are tiny
        if tags.get('footway') in ('sidewalk', 'crossing') or tags.get('cycleway') in ('sidewalk', 'crossing'):
            return None
        if tags.get('tunnel') or tags.get('covered') == 'yes' or tags.get('indoor') == 'yes' or tags.get('layer', '0').startswith('-'):
            return None
        if sf in DIRT or sf in GRAVEL: return 'path'
        return 'footway'                       # paved footpath between blocks, through parks
    if sf in COBBLE: return 'street_cobble'
    if sf in GRAVEL: return 'track_gravel'
    if sf in DIRT: return 'track_rutted' if sf in ('grass', 'mud') else 'track'
    if hw in ('tertiary', 'unclassified', 'residential', 'living_street', 'service'):
        markings = tags.get('lane_markings')
        if markings == 'yes': return 'street_marked'
        if sf in CONCRETE_ROAD: return 'street_concrete'
        if hw == 'tertiary' and (markings == 'no' or tags.get('lanes') == '1'):
            return 'street_unmarked'
    return hw


def _query(s, w, n, e):
    bb = f'{s:.6f},{w:.6f},{n:.6f},{e:.6f}'
    # "out geom(bbox)" clips huge forest/lake relations to our area -> small, fast answers
    return f'''[out:json][timeout:120];
(
 way["highway"~"^({ROAD_RE})$"]({bb});
 way["building"]({bb});
 relation["building"]({bb});
 way["natural"~"^(water|wood)$"]({bb});
 relation["natural"~"^(water|wood)$"]({bb});
 way["waterway"~"^(river|stream|canal|riverbank)$"]({bb});
 relation["waterway"="riverbank"]({bb});
 way["landuse"~"^(forest|farmland|meadow|residential|industrial|commercial|retail|farmyard|garages|allotments)$"]({bb});
 relation["landuse"~"^(forest|farmland|meadow|industrial|farmyard)$"]({bb});
 way["man_made"="works"]({bb});
 way["leisure"~"^(sports_centre|sports_hall|stadium)$"]({bb});
 relation["man_made"="works"]({bb});
 node["amenity"="fuel"]({bb});
 way["leisure"="pitch"]({bb});
 way["railway"="rail"]({bb});
 way["landuse"="cemetery"]({bb});
 way["amenity"~"^(grave_yard|parking)$"]({bb});
 way["leisure"~"^(playground|park|garden)$"]({bb});
 way["landuse"~"^(grass|village_green|recreation_ground)$"]({bb});
 way["natural"="scrub"]({bb});
 node["leisure"="playground"]({bb});
 node["highway"="bus_stop"]({bb});
 nwr["railway"~"^(station|halt)$"]({bb});
 nwr["amenity"~"^(bus_station|marketplace)$"]({bb});
 nwr["place"="square"]({bb});
 way["highway"="pedestrian"]["area"="yes"]({bb});
 node["man_made"="silo"]({bb});
 way["man_made"="silo"]({bb});
 node["amenity"="place_of_worship"]({bb});
 node["shop"]({bb});
 node["amenity"~"^({POI_AMENITY})$"]({bb});
 node["office"]({bb});
 node["tourism"~"^(hotel|guest_house|motel|hostel)$"]({bb});
 node["craft"]({bb});
 way["barrier"~"^(fence|wall|hedge)$"]({bb});
 way["amenity"="place_of_worship"]({bb});
 node["natural"="tree"]({bb});
 way["natural"="tree_row"]({bb});
 way["amenity"="fuel"]({bb});
);
out geom({bb});'''


_SERVER_FILE = os.path.join(CACHE, 'servers.json')


def _server_order():
    """Fastest server from the last run goes first."""
    try:
        best = json.load(open(_SERVER_FILE))
        return [u for u in best if u in OVERPASS] + [u for u in OVERPASS if u not in best]
    except Exception:
        return list(OVERPASS)


def _remember(url, order):
    try:
        os.makedirs(CACHE, exist_ok=True)
        json.dump([url] + [u for u in order if u != url], open(_SERVER_FILE, 'w'))
    except Exception:
        pass


def _fetch_one(url, q, log, stop):
    import requests
    host = url.split('/')[2]
    t0 = time.time()
    try:
        with requests.post(url, data={'data': q}, headers=UA, timeout=(10, 120), stream=True) as r:
            if r.status_code != 200:
                reason = {429: 'preťažený', 504: 'nestihol odpovedať', 400: 'chybná požiadavka'}.get(r.status_code, '')
                return None, f'{host}: HTTP {r.status_code} {reason}'
            buf, got, last = [], 0, 0
            for chunk in r.iter_content(256 * 1024):
                if stop.is_set(): return None, None            # another server already won
                buf.append(chunk); got += len(chunk)
                if got - last > 3 * 1024 * 1024:
                    log(f'    {host}: {got / 1048576:.1f} MB ...'); last = got
            data = json.loads(b''.join(buf))
            if 'remark' in data and not data.get('elements'):
                return None, f'{host}: {data["remark"][:120]}'
            return data, f'{host}: OK, {got / 1048576:.1f} MB za {time.time() - t0:.0f} s'
    except Exception as ex:
        return None, f'{host}: ' + ('neodpovedá' if 'imed out' in str(ex) else str(ex)[:120])


def _post(q, log, parallel=3):
    """Ask several Overpass servers at once and take the first complete answer."""
    import concurrent.futures as cf
    import threading
    errors = []
    for attempt in range(3):
        order = _server_order()
        stop = threading.Event()
        log(f'  Sťahujem z {min(parallel, len(order))} serverov naraz (pokus {attempt + 1}) ...')
        pool = cf.ThreadPoolExecutor(max_workers=len(order))
        futs = {}
        pending = list(order)
        # start the first few, add the next one whenever one fails
        for u in pending[:parallel]:
            futs[pool.submit(_fetch_one, u, q, log, stop)] = u
        pending = pending[parallel:]
        result = None
        while futs:
            done, _ = cf.wait(list(futs), return_when=cf.FIRST_COMPLETED)
            for f in done:
                u = futs.pop(f)
                data, msg = f.result()
                if msg: log('    ' + msg)
                if data is not None and result is None:
                    result = data; stop.set(); _remember(u, order)
                elif data is None:
                    errors.append(msg)
                    if pending and result is None:
                        nu = pending.pop(0); futs[pool.submit(_fetch_one, nu, q, log, stop)] = nu
            if result is not None:
                break
        pool.shutdown(wait=False, cancel_futures=True)
        if result is not None:
            return result
        if attempt < 2:
            log('  Všetky servery sú preťažené, skúšam znova o 15 s ...')
            time.sleep(15)
    raise RuntimeError('OpenStreetMap (Overpass) sa nepodarilo stiahnuť:\n' + '\n'.join(str(e) for e in errors[-4:]))


OSM_VER = 19        # bump when _query() changes (the cache key)


def _osm_key(bbox, max_tile_km=5.5):
    s, w, n, e = bbox
    km = max((n - s) * 110.574, (e - w) * 111.32 * math.cos(math.radians((s + n) / 2)))
    parts = max(1, math.ceil(km / max_tile_km))
    return f'osm{OSM_VER}|{s:.5f},{w:.5f},{n:.5f},{e:.5f}|{parts}', parts


def osm_status(bbox, max_tile_km=5.5):
    """(parts per side, cached): how many download pieces fetch_osm() needs and whether the data is already on disk."""
    key, parts = _osm_key(bbox, max_tile_km)
    return parts, os.path.exists(_cache_path(key, '.json'))


def fetch_osm(bbox, log=print, max_tile_km=5.5):
    s, w, n, e = bbox
    key, parts = _osm_key(bbox, max_tile_km)
    cp = _cache_path(key, '.json')
    if os.path.exists(cp):
        try:
            with open(cp) as cached:
                data = json.load(cached)
            log('  OSM z cache.')
            return parse_osm(data)
        except ValueError:
            log('  cache OSM je poškodená - sťahujem znova')
    elements = {}
    for i in range(parts):
        for j in range(parts):
            sub = (s + (n - s) * i / parts, w + (e - w) * j / parts,
                   s + (n - s) * (i + 1) / parts, w + (e - w) * (j + 1) / parts)
            log(f'  OSM časť {i * parts + j + 1}/{parts * parts}')
            try:
                got = _post(_query(*sub), log)
            except Exception:
                # the servers are down: the same area from an older version's cache is better than nothing
                for ver in range(17, 9, -1):
                    old = _cache_path(f'osm{ver}|{s:.5f},{w:.5f},{n:.5f},{e:.5f}|{parts}', '.json')
                    if os.path.exists(old):
                        log('  servery OSM nefungujú - používam staršie stiahnuté dáta tejto oblasti (bez noviniek)')
                        return parse_osm(json.load(open(old)))
                raise
            for el in got.get('elements', []):
                k = (el['type'], el['id'])
                if k in elements and el['type'] == 'relation':
                    elements[k].setdefault('members', []).extend(el.get('members', []))
                else:
                    elements[k] = el
    data = {'elements': list(elements.values())}
    _write_atomic(cp, json.dumps(data), 'w')
    return parse_osm(data)


PREVIEW_MAX_KM = 8.0       # the quick preview is meant for the area you are about to generate, not for whole regions
PREV_MAJOR = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary'}


def _query_preview(s, w, n, e):
    bb = f'{s:.6f},{w:.6f},{n:.6f},{e:.6f}'
    # roads and water/forest as geometry, buildings only as one point each (a big town has 100k of them)
    return f'''[out:json][timeout:90];
way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"]({bb})->.r;
(way["natural"~"^(water|wood)$"]({bb}); way["landuse"="forest"]({bb}); way["waterway"~"^(river|canal|riverbank)$"]({bb}););
out geom({bb});
.r out geom({bb});
way["building"]({bb});
out center;'''


def fetch_preview(bbox, log=print):
    """Light OSM data for the quick preview in the map picker:
    dict(bbox, roads=[(major?, pts)], water=[pts], forest=[pts], bld=[(lat, lon)]). Cached on disk."""
    s, w, n, e = bbox
    cp = _cache_path(f'prev1|{s:.5f},{w:.5f},{n:.5f},{e:.5f}', '.json')
    if os.path.exists(cp):
        try:
            d = json.load(open(cp))
            return dict(bbox=tuple(d['bbox']), roads=[(c, [tuple(p) for p in pts]) for c, pts in d['roads']],
                        water=[[tuple(p) for p in r] for r in d['water']], forest=[[tuple(p) for p in r] for r in d['forest']],
                        bld=[tuple(p) for p in d['bld']])
        except Exception:
            pass
    data = _post(_query_preview(s, w, n, e), log)
    out = dict(bbox=(s, w, n, e), roads=[], water=[], forest=[], bld=[])
    for el in data.get('elements', []):
        t = el.get('tags') or {}
        if el.get('type') != 'way':
            continue
        if 'building' in t:
            c = el.get('center')
            if c: out['bld'].append((c['lat'], c['lon']))
            continue
        pts = _ring_from(el.get('geometry') or [])
        if len(pts) < 2:
            continue
        if 'highway' in t:
            out['roads'].append((1 if t['highway'].replace('_link', '') in PREV_MAJOR else 0, pts))
        elif t.get('natural') == 'water' or t.get('waterway') in ('river', 'canal', 'riverbank'):
            out['water'].append(pts)
        else:
            out['forest'].append(pts)
    try:
        os.makedirs(CACHE, exist_ok=True)
        json.dump(out, open(cp, 'w'))
    except Exception:
        pass
    return out


def _ring_from(geom):
    # clipped geometry ("out geom(bbox)") can contain null entries for nodes outside the area
    return [(p['lat'], p['lon']) for p in geom if p and 'lat' in p]


def _way_runs(geometry):
    """Keep each visible stretch of a clipped way separate."""
    run = []
    for p in geometry:
        if p and 'lat' in p and 'lon' in p:
            run.append((p['lat'], p['lon']))
        else:
            if len(run) >= 2:
                yield run
            run = []
    if len(run) >= 2:
        yield run


def _merge_rings(ways):
    """Join open way pieces (multipolygon outers) into closed rings."""
    rings, pieces = [], [list(w) for w in ways if len(w) > 1]
    while pieces:
        cur = pieces.pop()
        changed = True
        while changed and cur[0] != cur[-1]:
            changed = False
            for i, p in enumerate(pieces):
                if p[0] == cur[-1]: cur += p[1:]
                elif p[-1] == cur[-1]: cur += p[::-1][1:]
                elif p[-1] == cur[0]: cur = p[:-1] + cur
                elif p[0] == cur[0]: cur = p[::-1][:-1] + cur
                else: continue
                pieces.pop(i); changed = True; break
        if len(cur) >= 4: rings.append(cur)
    return rings


def parse_osm(data):
    o = OSM()
    for el in data['elements']:
        tags = el.get('tags', {})
        public_space = (tags.get('place') == 'square' or
                        tags.get('amenity') == 'marketplace' or
                        (tags.get('highway') == 'pedestrian' and tags.get('area') == 'yes'))
        if public_space:
            if 'lat' in el:
                o.points.append(('square', el['lat'], el['lon']))
            else:
                geometries = ([el.get('geometry', [])] if el['type'] == 'way' else
                              [m.get('geometry', []) for m in el.get('members', []) if m.get('role') == 'outer'])
                for ring in _merge_rings([_ring_from(g) for g in geometries if g]):
                    o.areas.append(('square', ring))
        if (tags.get('amenity') == 'bus_station' or tags.get('building') == 'train_station'
                or el['type'] != 'node' and tags.get('railway') in ('station', 'halt')):
            if 'lat' in el:
                o.points.append(('station', el['lat'], el['lon']))
            elif el.get('geometry'):
                ring = _ring_from(el['geometry'])
                if ring:
                    o.points.append(('station', *(sum(axis) / len(axis) for axis in zip(*ring))))
        if tags.get('amenity') == 'fuel':
            if el['type'] == 'node' and 'lat' in el:
                o.fuel.append((el['lat'], el['lon']))
            elif 'geometry' in el:
                g = _ring_from(el['geometry'])
                if g: o.fuel.append((sum(p[0] for p in g) / len(g), sum(p[1] for p in g) / len(g)))
            if 'building' not in tags:
                continue
        if el['type'] == 'node' and 'lat' in el:
            k = ('bus_stop' if tags.get('highway') == 'bus_stop' else
                 'playground' if tags.get('leisure') == 'playground' else
                 'church' if tags.get('amenity') == 'place_of_worship' else
                 'station' if tags.get('railway') in ('station', 'halt') else
                 'silo' if tags.get('man_made') == 'silo' else None)
            if k:
                o.points.append((k, el['lat'], el['lon'])); continue
        if el['type'] == 'node' and 'lat' in el:
            k = poi_kind(tags)
            if k:
                o.pois.append((k, el['lat'], el['lon'])); continue
        if tags.get('barrier') and 'building' not in tags and 'geometry' in el:
            k = barrier_kind(tags)
            pts = _ring_from(el['geometry'])
            if k and len(pts) >= 2: o.barriers.append((k, pts))
            continue
        if tags.get('man_made') == 'silo' and 'building' not in tags and 'geometry' in el:
            r = _ring_from(el['geometry'])
            if r:
                o.points.append(('silo', sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r)))
            continue
        if tags.get('railway') == 'rail' and 'geometry' in el:
            if tags.get('service') == 'crossover': continue          # short switches between parallel tracks
            side = tags.get('service') in ('yard', 'siding', 'spur')
            o.roads.extend(('rail_side' if side else 'rail', run) for run in _way_runs(el['geometry']))
            continue
        area_kind = ('cemetery' if tags.get('landuse') == 'cemetery' or tags.get('amenity') == 'grave_yard' else
                     'playground' if tags.get('leisure') == 'playground' else
                     'park' if tags.get('leisure') == 'park' else
                     'green' if tags.get('leisure') == 'garden' or tags.get('natural') == 'scrub' or
                                tags.get('landuse') in ('grass', 'village_green', 'recreation_ground') else
                     'parking' if tags.get('amenity') == 'parking' and 'building' not in tags else None)
        if area_kind and 'geometry' in el:
            r = _ring_from(el['geometry'])
            if len(r) >= 4: o.areas.append((area_kind, r))
            continue
        if tags.get('natural') == 'tree' and el['type'] == 'node' and 'lat' in el:
            o.trees.append((el['lat'], el['lon'])); continue
        if tags.get('natural') == 'tree_row' and 'geometry' in el:
            o.tree_rows.append(_ring_from(el['geometry'])); continue
        if tags.get('leisure') == 'pitch' and 'geometry' in el:
            r = _ring_from(el['geometry'])
            sp = tags.get('sport', '')
            if 'soccer' in sp or 'football' in sp or sp == 'multi' or (not sp and tags.get('surface') == 'artificial_turf'):
                sp = 'soccer'                      # 'soccer;basketball', multi-use or artificial turf pitches
            if len(r) >= 4: o.pitches.append((sp, r))
            continue
        if el['type'] == 'way' and 'geometry' in el:
            pts = _ring_from(el['geometry'])
            if len(pts) < 2: continue
            if 'highway' in tags:
                hw = tags['highway']
                # parking aisles and drive-throughs only clutter the map; alleys and longer driveways are real
                # (often asphalt) lanes, e.g. the access roads between blocks of flats
                sv = tags.get('service')
                driveway = hw == 'service' and sv == 'driveway'
                if hw == 'service' and sv in ('parking_aisle', 'drive-through', 'emergency_access'):
                    continue
                if tags.get('area') == 'yes':
                    continue
                hw = road_kind(hw, tags)
                if hw is None: continue
                lay = str(tags.get('layer', '0')).split(';')[0].strip()
                for run in _way_runs(el['geometry']):
                    if driveway and sum(math.hypot((a[0] - b[0]) * 110574, (a[1] - b[1]) * 74000)
                                                for a, b in zip(run, run[1:])) < 30:
                        continue
                    o.roads.append((hw, run))
                    if (tags.get('bridge') not in (None, '', 'no')) or (lay.lstrip('+').isdigit() and int(lay) > 0):
                        o.bridge_ways.append(run)
                    if tags.get('junction') in ('roundabout', 'circular'):
                        o.roundabouts.append(len(o.roads) - 1)
            elif 'building' in tags:
                if len(pts) >= 4: o.buildings.append((dict(tags, _osm=f"w{el['id']}"), pts))
            elif tags.get('waterway') == 'riverbank':          # old tagging of a river area
                if len(pts) >= 4:
                    o.water.append(pts); o.water_kind.append('riverbank')
            elif 'waterway' in tags:
                o.waterways.append((tags['waterway'], pts))
                try: o.waterway_width.append(float(str(tags.get('width', '0')).replace(',', '.').split()[0]))
                except ValueError: o.waterway_width.append(0.0)
            elif tags.get('natural') == 'water':
                if len(pts) >= 4:
                    o.water.append(pts); o.water_kind.append(tags.get('water', 'lake'))
            elif tags.get('natural') == 'wood' or tags.get('landuse') == 'forest':
                if len(pts) >= 4:
                    o.forests.append(pts); o.forest_leaf.append(tags.get('leaf_type', 'mixed'))
            elif 'landuse' in tags and len(pts) >= 4:
                o.landuse.append((tags['landuse'], pts))
            elif tags.get('man_made') == 'works' and len(pts) >= 4:      # a factory site (glassworks ...)
                o.landuse.append(('industrial', pts))
            elif tags.get('leisure') in ('sports_centre', 'sports_hall', 'stadium') and 'building' not in tags and len(pts) >= 4:
                o.landuse.append(('sports', pts))                         # a sports ground: its big building = hall
        elif el['type'] == 'relation':
            outers = [_ring_from(m['geometry']) for m in el.get('members', [])
                      if m.get('role') == 'outer' and 'geometry' in m]
            if 'building' in tags:
                # big buildings with courtyards / several wings (schools, factories) are multipolygons
                inners = [r for r in _merge_rings([_ring_from(m['geometry']) for m in el.get('members', [])
                                                   if m.get('role') == 'inner' and 'geometry' in m]) if len(r) >= 4]
                for ring in _merge_rings(outers):
                    if ring[0] != ring[-1]: ring = ring + [ring[0]]
                    if len(ring) >= 4:
                        o.buildings.append((dict(tags, _osm=f"r{el['id']}", _holes=inners), ring))
                continue
            for ring in _merge_rings(outers):
                if tags.get('natural') == 'water' or tags.get('waterway') == 'riverbank':
                    o.water.append(ring)
                    o.water_kind.append('riverbank' if tags.get('waterway') == 'riverbank' else tags.get('water', 'lake'))
                elif tags.get('natural') == 'wood' or tags.get('landuse') == 'forest':
                    o.forests.append(ring); o.forest_leaf.append(tags.get('leaf_type', 'mixed'))
                elif tags.get('landuse') in ('farmland', 'meadow', 'industrial', 'farmyard'): o.landuse.append((tags['landuse'], ring))
                elif tags.get('man_made') == 'works': o.landuse.append(('industrial', ring))
    return o


# ------------------------------------------------------------------ DEM
class DemSampler:
    """Mapzen/AWS 'terrarium' tiles stitched into one mosaic and sampled bilinearly."""

    def __init__(self, bbox, mpc, log=print):
        from PIL import Image
        s, w, n, e = bbox
        lat_c = (s + n) / 2
        z = int(math.ceil(math.log2(156543.03 * math.cos(math.radians(lat_c)) / max(mpc, 1.0))))
        self.z = max(7, min(15, z + 1))          # one level finer than the cell size: smoother, truer slopes
        self.px = lambda lon: (lon + 180.0) / 360.0 * (2 ** self.z) * 256
        self.py = lambda lat: (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * (2 ** self.z) * 256
        x0, x1 = int(self.px(w) // 256) - 1, int(self.px(e) // 256) + 1
        y0, y1 = int(self.py(n) // 256) - 1, int(self.py(s) // 256) + 1
        nx, ny = x1 - x0 + 1, y1 - y0 + 1
        log(f'  DEM zoom {self.z}, {nx * ny} dlaždíc')
        mosaic = np.zeros((ny * 256, nx * 256), np.float32)
        import requests
        import concurrent.futures as cf

        def get_tile(xy):
            x, y = xy
            cp = _cache_path(f'dem|{self.z}|{x}|{y}', '.png')
            if os.path.exists(cp):
                return xy, open(cp, 'rb').read()
            for attempt in range(4):
                try:
                    r = requests.get(DEM_URL.format(z=self.z, x=x, y=y), headers=UA, timeout=30)
                    if r.status_code == 200:
                        _write_atomic(cp, r.content)
                        return xy, r.content
                except Exception:
                    pass
                time.sleep(1 + attempt)
            raise RuntimeError(f'Nepodarilo sa stiahnuť výškovú dlaždicu {self.z}/{x}/{y}')

        jobs = [(x0 + tx, y0 + ty) for ty in range(ny) for tx in range(nx)]
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for (x, y), raw in ex.map(get_tile, jobs):
                tx, ty = x - x0, y - y0
                a = np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'), np.float32)
                mosaic[ty * 256:(ty + 1) * 256, tx * 256:(tx + 1) * 256] = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
        self.m, self.ox, self.oy = mosaic, x0 * 256, y0 * 256

    def __call__(self, lat, lon):
        lat = np.asarray(lat, float); lon = np.asarray(lon, float)
        fx = (lon + 180.0) / 360.0 * (2 ** self.z) * 256 - self.ox
        lr = np.radians(lat)
        fy = (1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / math.pi) / 2 * (2 ** self.z) * 256 - self.oy
        fx = np.clip(fx, 0, self.m.shape[1] - 1.001); fy = np.clip(fy, 0, self.m.shape[0] - 1.001)
        x0, y0 = fx.astype(int), fy.astype(int); tx, ty = fx - x0, fy - y0
        m = self.m
        return (m[y0, x0] * (1 - tx) * (1 - ty) + m[y0, x0 + 1] * tx * (1 - ty) +
                m[y0 + 1, x0] * (1 - tx) * ty + m[y0 + 1, x0 + 1] * tx * ty)
