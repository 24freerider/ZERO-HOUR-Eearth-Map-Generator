"""Ground colours from a satellite image: Sentinel-2 cloudless by EOX (s2maps.eu, CC BY-NC-SA 4.0,
contains modified Copernicus Sentinel data). Only the web-map tiles over the map are downloaded (a few
hundred kB) and cached in ~/.cncearth/sat. The generator turns the colour of every cell into a ground texture:
green meadow, yellow field, brown soil, grey built-up ground, sand."""
import io, math, os, urllib.request
import numpy as np

URL = 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg'
CACHE = os.path.join(os.path.expanduser('~'), '.cncearth', 'sat')
UA = 'ZeroHourEarth map generator'
CREDIT = 'Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020)'


def _xy(lat, lon, z):
    n = 1 << z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(max(-85.05, min(85.05, lat))))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def _tile(z, x, y, log):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f'{z}_{x}_{y}.jpg')
    if not os.path.isfile(p):
        req = urllib.request.Request(URL.format(z=z, x=x, y=y), headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with open(p + '.part', 'wb') as f: f.write(data)
        os.replace(p + '.part', p)
    from PIL import Image
    return np.asarray(Image.open(p).convert('RGB'))


def fetch(bbox, log=print, z=15, max_tiles=144):
    """bbox (s, w, n, e) -> (rgb array, z, x0, y0) - the mosaic and its top-left tile, or None."""
    s, w, n, e = bbox
    while z > 10:
        x0, y1 = _xy(s, w, z); x1, y0 = _xy(n, e, z)
        tx0, tx1, ty0, ty1 = int(x0), int(x1), int(y0), int(y1)
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= max_tiles: break
        z -= 1
    rows = []
    got = 0
    for ty in range(ty0, ty1 + 1):
        row = []
        for tx in range(tx0, tx1 + 1):
            try:
                row.append(_tile(z, tx, ty, log)); got += 1
            except Exception as ex:
                if got == 0 and tx == tx0 and ty == ty0:
                    raise RuntimeError(f'satelitná mapa nedostupná: {ex}')
                row.append(np.zeros((256, 256, 3), np.uint8))
        rows.append(np.concatenate(row, axis=1))
    img = np.concatenate(rows, axis=0)
    log(f'  satelitná mapa (Sentinel-2): {got} dlaždíc (z{z})')
    return img, z, tx0, ty0


def sample(sat, lat, lon):
    """RGB of the satellite mosaic at lat / lon arrays (nearest pixel)."""
    img, z, tx0, ty0 = sat
    n = 1 << z
    lat = np.clip(lat, -85.05, 85.05)
    x = (lon + 180.0) / 360.0 * n
    sl = np.sin(np.radians(lat))
    y = (0.5 - np.log((1 + sl) / (1 - sl)) / (4 * np.pi)) * n
    px = np.clip(((x - tx0) * 256).astype(int), 0, img.shape[1] - 1)
    py = np.clip(((y - ty0) * 256).astype(int), 0, img.shape[0] - 1)
    return img[py, px].astype(np.float32)


def classify(rgb):
    """RGB (..., 3) -> ground kind codes: 0 meadow, 1 dark green, 2 field (yellow), 3 soil, 4 built-up, 5 sand."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    br = (r + g + b) / 3
    exg = g - (r + b) / 2                       # excess green: vegetation
    sat_ = rgb.max(-1) - rgb.min(-1)
    out = np.zeros(r.shape, np.int8)
    out[(exg > 8) & (br < 55)] = 1                  # only really dark woods / hedges (the photos are dark)
    yellow = (r >= g * 0.9) & (r > b * 1.5) & (g < r * 1.08) & (br > 105)     # ripe / dry field
    soil = (r > g) & (g >= b * 0.95) & (br <= 110) & (exg < 4)
    grey = (sat_ < 22) & (br > 95) & (br < 200)
    sand = (br >= 200) & (sat_ < 60)
    out[yellow] = 2; out[soil] = 3; out[grey] = 4; out[sand] = 5
    return out
