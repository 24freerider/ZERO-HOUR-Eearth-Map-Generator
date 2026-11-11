"""Pre-generation helpers: where the player starts will be and simple sanity checks of the chosen settings.

layout_rel() is the single source of the start layouts: generator.Gen.layout_points() calls it, and the map picker
uses the same function to draw the starts BEFORE the map is generated. Pure Python (math only), no tkinter.
"""
import math


def layout_rel(n, layout, AX, AY, base_clear):
    """Relative (0..1) start positions for n players, the layout name and the map size in game units."""
    m = min(0.25, max(0.12, (base_clear * 0.75) / min(AX, AY)))
    my = min(0.25, max(0.03, (base_clear * 0.75) / AY))     # margin from the south / north edge
    if layout == 'mode':          # classic Zombie: leave room for the central tunnel
        return [(max(m, 0.5 - (base_clear / 2 + 400) / AX), 0.5), (0.06, 0.06)]
    if layout == 'clear':
        # Clearance: the people side by side along the southern edge, the town ahead of them; the insurgents'
        # main base stands in the north; the AI slot (dormant / zombie master) in the north-west corner
        h = n - 1
        pts = [(m + (1 - 2 * m) * (i + 0.5) / h, my) for i in range(h)] if h > 1 else [(0.5, my)]
        return pts + [(0.06, 0.97)]
    if layout == 'auto' and AY >= AX * 1.5:
        # a north-south strip: half the players in the south, half in the north, facing each other
        a = (n + 1) // 2; b = n - a
        south = [(m + (1 - 2 * m) * (i + 0.5) / a, my) for i in range(a)]
        north = [(m + (1 - 2 * m) * (i + 0.5) / max(1, b), 1 - my) for i in range(b)]
        out = []
        for i in range(max(a, b)):
            if i < a: out.append(south[i])
            if i < b: out.append(north[i])
        return out
    if layout == 'zsurv':         # ZombieSurvival: people around the centre, the zombie AI last, in a corner
        h = n - 1
        if h == 1: pts = [(0.5, 0.5)]
        else: pts = [(0.5 + 0.08 * math.cos(math.tau * i / h), 0.5 + 0.08 * math.sin(math.tau * i / h)) for i in range(h)]
        return pts + [(0.06, 0.06)]
    if layout == 'circle':
        r = 0.5 - m
        return [(0.5 + r * math.cos(math.tau * i / n + math.pi / 4 + math.pi),
                 0.5 + r * math.sin(math.tau * i / n + math.pi / 4 + math.pi)) for i in range(n)]
    if layout == 'teams_ns':
        # Rotate the west/east pattern so odd-numbered starts are north.
        return [(y, 1 - x) for x, y in layout_rel(n, 'teams', AY, AX, base_clear)]
    if layout == 'teams':
        # two teams facing each other: west side vs east side
        a = (n + 1) // 2; b = n - a
        fit = max(1, int((1 - 2 * m) * AY / (base_clear * 1.05)))   # bases per column

        def side(k, x, inward):
            cols = 1 if k <= fit else 2
            per = math.ceil(k / cols)
            out = []
            for c in range(cols):
                xx = x + inward * c * (base_clear * 1.1 / AX)
                cnt = min(per, k - c * per)
                out += [(xx, m + (1 - 2 * m) * (i + 0.5) / cnt) for i in range(cnt)]
            return out
        west, east = side(a, m, 1), side(b, 1 - m, -1)
        out = []
        for i in range(max(a, b)):          # alternate so Player 1,3,5.. = west, 2,4,6.. = east
            if i < a: out.append(west[i])
            if i < b: out.append(east[i])
        return out
    corners = [(m, m), (1 - m, 1 - m), (1 - m, m), (m, 1 - m)]
    edges = [(0.5, m), (0.5, 1 - m), (m, 0.5), (1 - m, 0.5)]
    if n == 2: return [corners[0], corners[1]]
    if n == 3: return [(m, m), (1 - m, m), (0.5, 1 - m)]
    if n <= 8: return (corners + edges)[:n]
    if n <= 12:      # 9 - 12 players: corners + edges + an inner ring
        inner = [(0.33, 0.33), (0.67, 0.67), (0.67, 0.33), (0.33, 0.67)]
        return (corners + edges + inner)[:n]
    # 13 - 32 players (GeneralsX): an outer ring along the edges and an inner ring round the town
    n_out = math.ceil(n * 0.6); n_in = n - n_out
    r_out, r_in = 0.5 - m, max(0.18, 0.5 - m - 0.22)
    pts = [(0.5 + r_out * math.cos(math.tau * i / n_out + 0.3), 0.5 + r_out * math.sin(math.tau * i / n_out + 0.3))
           for i in range(n_out)]
    pts += [(0.5 + r_in * math.cos(math.tau * i / max(1, n_in)), 0.5 + r_in * math.sin(math.tau * i / max(1, n_in)))
            for i in range(n_in)]
    return pts


def start_plan(players, layout, km, tiles, aspect, base_size, clearance=False, zombie=False, zsurv=False,
               zdef=False, manual_points=(), invasion=False):
    """Where the generator will put the starts (before terrain nudges them a little).

    Mirrors the player-count / layout logic at the top of generator.Gen.__init__ and place_bases().
    Returns a dict:
      points   [(rx, ry)] relative to the playable area (0..1; may lie outside = the black border)
      n        number of starts
      layout   the layout actually used
      base_m   side of one base square in metres (0 when the mode has no bases)
      dim_m    (east-west, north-south) size of the area in metres
      outside  True when the starts are outside the playable area
    """
    import generator
    CELL, BORDER, MAXP = 10.0, generator.BORDER, generator.MAX_PLAYERS
    players = int(players)
    if layout in ('zsurv', 'invasion') and not (zsurv or zdef or invasion): layout = 'auto'
    zs = bool(zsurv or zdef)
    if invasion:
        n, lay = max(2, min(MAXP, players)), 'invasion'
    elif zs:
        humans = max(1, min(MAXP - 1, players - 1))
        n, lay = humans + 1, 'zsurv'
    elif clearance or zombie:
        n_people = len(manual_points) if layout == 'manual' else players
        if layout == 'manual':
            n_people = max(2, n_people)
        n_people = min(n_people, MAXP - 1)
        if n_people >= 2:
            n = n_people + 1
            lay = 'clear' if (clearance and layout == 'auto') else layout
        else:
            n, lay = 2, ('clear' if clearance else 'mode')
    else:
        n = max(2, min(MAXP, len(manual_points) if layout == 'manual' else players))
        lay = layout
    NX, NY = int(tiles), int(round(tiles * aspect))
    AX, AY = NX * CELL, NY * CELL
    skirmish = not (clearance or zombie or zs or invasion)
    base_clear = base_width(base_size, AX, AY, n, lay, skirmish)
    mpc = km * 1000.0 / tiles                      # metres per cell
    dim_m = (km * 1000.0, km * 1000.0 * NY / max(1, NX))
    if lay in ('zsurv', 'invasion'):
        pts = hidden_starts(AX, AY, n - 1, BORDER * CELL)
        return dict(points=[(x / AX, y / AY) for x, y in pts], n=n, layout=lay, base_m=0.0, dim_m=dim_m, outside=True)
    if lay == 'manual':
        rel = [manual_point(x, y, AX, AY, base_clear) for x, y in manual_points[:MAXP - int(clearance or zombie)]]
    else:
        rel = layout_rel(n, lay, AX, AY, base_clear)
    return dict(points=rel, n=len(rel), layout=lay, base_m=base_clear * mpc / CELL, dim_m=dim_m,
                outside=False, skirmish=skirmish)


def hidden_starts(width, height, humans, border):
    """Hidden command centers above the playable map, with an AI start below it."""
    step = min(420.0, (width + border) / humans)
    starts = [(width / 2 + (k - (humans - 1) / 2) * step, height + border * (0.35 if k % 2 else 0.6))
              for k in range(humans)]
    starts.append((width / 2, -border * 0.5))
    return starts


def base_width(requested, width, height, players, layout, skirmish=False):
    size = float(requested)
    if skirmish:
        # Stock skirmish plans put early buildings several hundred units from the command center.
        fit = min(0.78 * min(width, height), 0.9 * math.sqrt(width * height / players))
        if layout == 'teams':
            fit = min(fit, 0.47 * width)
        elif layout == 'teams_ns':
            fit = min(fit, 0.47 * height)
        size = min(max(size, 1040.0), fit)
    if players > 12:
        size = min(size, 0.8 * math.sqrt(width * height) / math.sqrt(players))
    if layout == 'manual':
        size = min(size, 0.9 * min(width, height))
    return size


def manual_point(rx, ry, width, height, size):
    mx, my = size / (2 * width), size / (2 * height)
    return min(max(rx, mx), 1 - mx), min(max(ry, my), 1 - my)


def base_report(plan):
    """(coverage, too_close): the share of the map the base squares take, and how many pairs of bases are so close
    that the generator would have to move one of them."""
    if not plan or plan['base_m'] <= 0:
        return 0.0, 0
    dx, dy = plan['dim_m']
    b = plan['base_m']
    cover = len(plan['points']) * b * b / max(1.0, dx * dy)
    pts = [(x * dx, y * dy) for x, y in plan['points']]
    over = 0
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dx, dy = abs(pts[i][0] - pts[j][0]), abs(pts[i][1] - pts[j][1])
            close = max(dx, dy) < b if plan.get('skirmish') or plan['layout'] == 'manual' else math.hypot(dx, dy) < b * 0.6
            if close:
                over += 1
    return cover, over


def preview_stats(prev, rect):
    """What the quick OSM preview says about a rectangle (south, west, north, east), or None when the rectangle
    is not fully inside the previewed area."""
    if not prev:
        return None
    ps, pw, pn, pe = prev['bbox']
    s, w, n, e = rect
    if s < ps or w < pw or n > pn or e > pe:
        return None
    inside = lambda la, lo: s <= la <= n and w <= lo <= e
    bld = sum(1 for la, lo in prev['bld'] if inside(la, lo))
    seg = 0
    for _cls, pts in prev['roads']:
        seg += sum(1 for a, b in zip(pts, pts[1:]) if inside(*a) or inside(*b))
    return dict(buildings=bld, road_segments=seg)
