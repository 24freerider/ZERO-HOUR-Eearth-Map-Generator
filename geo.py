"""Projection + small 2D geometry helpers (no external deps except numpy)."""
import math
import numpy as np

CELL = 10.0  # world units per map cell


class Proj:
    """Local equirectangular projection. Game units: x east, y north,
    origin = south-west corner of the playable area."""

    def __init__(self, lat0, lon0, area_m, tiles, aspect=1.0):
        """area_m / tiles = the east-west size; aspect = north-south / east-west (1 = square, 4 = 1:4 strip)."""
        self.lat0, self.lon0, self.area = lat0, lon0, float(area_m)
        self.aspect = float(aspect)
        self.area_y = self.area * self.aspect
        self.tiles_x = int(tiles)
        self.tiles_y = int(round(tiles * self.aspect))
        self.ky = 110574.0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.mpc = self.area / tiles          # metres per cell
        self.upm = CELL / self.mpc            # game units per metre

    def to_units(self, lat, lon):
        xm = (lon - self.lon0) * self.kx + self.area / 2
        ym = (lat - self.lat0) * self.ky + self.area_y / 2
        return xm * self.upm, ym * self.upm

    def from_units(self, ux, uy):
        xm = np.asarray(ux) / self.upm - self.area / 2
        ym = np.asarray(uy) / self.upm - self.area_y / 2
        return self.lat0 + ym / self.ky, self.lon0 + xm / self.kx

    def bbox(self, margin_m=0):
        s, w = self.from_units((-margin_m) * self.upm, (-margin_m) * self.upm)
        n, e = self.from_units((self.area + margin_m) * self.upm, (self.area_y + margin_m) * self.upm)
        return float(s), float(w), float(n), float(e)


def rdp(pts, eps):
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]; bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        best, bi = -1, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / L
            if d > best:
                best, bi = d, i
        if best > eps:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def clip_seg(p, q, xmax, ymax):
    """Liang-Barsky against [0,xmax]x[0,ymax]."""
    x0, y0 = p; dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0), (dx, xmax - x0), (-dy, y0), (dy, ymax - y0)):
        if pp == 0:
            if qq < 0:
                return None
        else:
            r = qq / pp
            if pp < 0:
                if r > t1: return None
                t0 = max(t0, r)
            else:
                if r < t0: return None
                t1 = min(t1, r)
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def clip_polyline(pts, xmax, ymax):
    runs, cur = [], []
    for a, b in zip(pts[:-1], pts[1:]):
        c = clip_seg(a, b, xmax, ymax)
        if c is None:
            if len(cur) > 1: runs.append(cur)
            cur = []
            continue
        s, e = c
        if cur and math.hypot(cur[-1][0] - s[0], cur[-1][1] - s[1]) < 1e-6:
            cur.append(e)
        else:
            if len(cur) > 1: runs.append(cur)
            cur = [s, e]
    if len(cur) > 1: runs.append(cur)
    return runs


def clip_poly(poly, xmax, ymax):
    """Sutherland-Hodgman against the rectangle."""
    def clip(pts, inside, inter):
        out = []
        for i, cur in enumerate(pts):
            prev = pts[i - 1]
            if inside(cur):
                if not inside(prev): out.append(inter(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(inter(prev, cur))
        return out
    def ix(x):
        return lambda p, q: (x, p[1] + (q[1] - p[1]) * (x - p[0]) / ((q[0] - p[0]) or 1e-9))
    def iy(y):
        return lambda p, q: (p[0] + (q[0] - p[0]) * (y - p[1]) / ((q[1] - p[1]) or 1e-9), y)
    pts = list(poly)
    for ins, it in ((lambda p: p[0] >= 0, ix(0)), (lambda p: p[0] <= xmax, ix(xmax)),
                    (lambda p: p[1] >= 0, iy(0)), (lambda p: p[1] <= ymax, iy(ymax))):
        if not pts: return []
        pts = clip(pts, ins, it)
    return pts


def hull(points):
    pts = sorted(set(points))
    if len(pts) < 3: return pts
    def cross(o, a, b): return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0: up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def min_rect(points):
    """Minimum-area bounding rectangle -> (cx, cy, L, W, theta) with L >= W,
    theta = direction of the long axis (radians)."""
    h = hull(points)
    if len(h) < 3: return None
    best = None
    for i in range(len(h)):
        x0, y0 = h[i]; x1, y1 = h[(i + 1) % len(h)]
        a = math.atan2(y1 - y0, x1 - x0)
        c, s = math.cos(-a), math.sin(-a)
        xs = [px * c - py * s for px, py in h]
        ys = [px * s + py * c for px, py in h]
        w, hh = max(xs) - min(xs), max(ys) - min(ys)
        if best is None or w * hh < best[0]:
            best = (w * hh, a, (max(xs)+min(xs))/2, (max(ys)+min(ys))/2, w, hh)
    _, a, rx, ry, w, hh = best
    c, s = math.cos(a), math.sin(a)
    cx, cy = rx * c - ry * s, rx * s + ry * c
    if w >= hh: return cx, cy, w, hh, a % math.pi
    return cx, cy, hh, w, (a + math.pi / 2) % math.pi


def polyline_len(pts):
    return sum(math.hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(pts[:-1], pts[1:]))


def offset_strip(pts, hw):
    """Polygon around a polyline with half-width hw."""
    n = len(pts)
    norms = []
    for a, b in zip(pts[:-1], pts[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]; L = math.hypot(dx, dy) or 1e-9
        norms.append((-dy / L, dx / L))
    left, right = [], []
    for i, p in enumerate(pts):
        n0 = norms[max(i - 1, 0)]; n1 = norms[min(i, len(norms) - 1)]
        nx, ny = n0[0] + n1[0], n0[1] + n1[1]
        L = math.hypot(nx, ny)
        if L < 1e-6: nx, ny = n1
        else: nx, ny = nx / L, ny / L
        dot = max(nx * n1[0] + ny * n1[1], 0.5)
        k = hw / dot
        left.append((p[0] + nx * k, p[1] + ny * k))
        right.append((p[0] - nx * k, p[1] - ny * k))
    return left + right[::-1]


def blur(a, passes=1):
    k = np.array([1, 4, 6, 4, 1], float) / 16.0
    for _ in range(passes):
        p = np.pad(a, ((0, 0), (2, 2)), mode='edge')
        a = sum(k[i] * p[:, i:i + a.shape[1]] for i in range(5))
        p = np.pad(a, ((2, 2), (0, 0)), mode='edge')
        a = sum(k[i] * p[i:i + a.shape[0], :] for i in range(5))
    return a


def box_mean(a, r):
    """Mean over (2r+1)^2 window using cumulative sums."""
    p = np.pad(a.astype(float), r, mode='edge')
    c = p.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    n = 2 * r + 1
    s = c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]
    return s / (n * n)


def rdp_ring(poly, eps):
    """Simplify a closed ring (handles a repeated closing point)."""
    pts = list(poly)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 5:
        return pts
    j = max(range(len(pts)), key=lambda i: math.hypot(pts[i][0] - pts[0][0], pts[i][1] - pts[0][1]))
    a = rdp(pts[:j + 1], eps)
    b = rdp(pts[j:] + [pts[0]], eps)
    return a[:-1] + b[:-1]


def poly_area(pts):
    """Signed shoelace area of a polygon (open or closed ring)."""
    n = len(pts)
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))


def point_in_poly(x, y, pts):
    inside, j = False, len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]; xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside
