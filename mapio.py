"""Reader/writer for C&C Generals / Zero Hour .map files (uncompressed 'CkMp').

Structured: HeightMapData, BlendTileData (arrays + raw tail), ObjectsList.
Every other chunk is kept as raw bytes, so a read->write round trip is byte-exact.
"""
import struct

# ---------- small helpers ----------
def _rstr(d, p):
    n = struct.unpack_from('<H', d, p)[0]
    return d[p + 2:p + 2 + n].decode('latin1'), p + 2 + n

def _wstr(s):
    b = s.encode('latin1')
    return struct.pack('<H', len(b)) + b

# ---------- property dictionaries (type in low byte, name id above) ----------
def read_dict(d, p):
    n = struct.unpack_from('<H', d, p)[0]; p += 2
    items = []
    for _ in range(n):
        key = struct.unpack_from('<I', d, p)[0]; p += 4
        t = key & 0xFF
        if t == 0:
            v = d[p]; p += 1
        elif t == 1:
            v = struct.unpack_from('<i', d, p)[0]; p += 4
        elif t == 2:
            v = struct.unpack_from('<f', d, p)[0]; p += 4
        elif t == 3:
            v, p = _rstr(d, p)
        elif t == 4:
            ln = struct.unpack_from('<H', d, p)[0]
            v = d[p + 2:p + 2 + 2 * ln]; p += 2 + 2 * ln   # keep raw utf16
        else:
            raise ValueError(f'unknown dict type {t}')
        items.append((key, v))
    return items, p

def write_dict(items):
    out = [struct.pack('<H', len(items))]
    for key, v in items:
        out.append(struct.pack('<I', key))
        t = key & 0xFF
        if t == 0: out.append(bytes([v]))
        elif t == 1: out.append(struct.pack('<i', v))
        elif t == 2: out.append(struct.pack('<f', v))
        elif t == 3: out.append(_wstr(v))
        elif t == 4: out.append(struct.pack('<H', len(v) // 2) + v)
    return b''.join(out)

# ---------- chunk payloads ----------
class HeightMap:
    def __init__(self, w, h, border, borders, data):
        self.w, self.h, self.border, self.borders, self.data = w, h, border, borders, bytearray(data)
    @staticmethod
    def parse(b):
        w, h, border = struct.unpack_from('<III', b, 0); p = 12
        nb = struct.unpack_from('<I', b, p)[0]; p += 4
        borders = [struct.unpack_from('<II', b, p + 8 * i) for i in range(nb)]; p += 8 * nb
        n = struct.unpack_from('<I', b, p)[0]; p += 4
        return HeightMap(w, h, border, borders, b[p:p + n])
    def dump(self):
        out = struct.pack('<III', self.w, self.h, self.border)
        out += struct.pack('<I', len(self.borders))
        for bw, bh in self.borders: out += struct.pack('<II', bw, bh)
        out += struct.pack('<I', len(self.data)) + bytes(self.data)
        return out

class BlendTile:
    """Per-cell texture arrays; everything after them kept raw (passability,
    texture classes, blend/cliff info tables)."""
    def __init__(self, w, h, tiles, blend, extra, cliff, tail):
        self.w, self.h = w, h
        self.tiles, self.blend, self.extra, self.cliff, self.tail = tiles, blend, extra, cliff, tail
    @staticmethod
    def parse(b, w, h):
        n = w * h
        assert struct.unpack_from('<I', b, 0)[0] == n
        p = 4; arrs = []
        for _ in range(4):
            arrs.append(list(struct.unpack_from(f'<{n}h', b, p))); p += 2 * n
        return BlendTile(w, h, *arrs, b[p:])
    def dump(self):
        n = self.w * self.h
        out = struct.pack('<I', n)
        for a in (self.tiles, self.blend, self.extra, self.cliff):
            out += struct.pack(f'<{n}h', *a)
        return out + self.tail

class MapObject:
    def __init__(self, ver, x, y, z, angle, flags, name, props):
        self.ver, self.x, self.y, self.z, self.angle = ver, x, y, z, angle
        self.flags, self.name, self.props = flags, name, props

# ---------- whole file ----------
class MapFile:
    def __init__(self):
        self.strings = []      # [(name, id)] in file order
        self.chunks = []       # [name, version, payload(bytes)]
        self.height = None
        self.blend = None
        self.objects = []      # [MapObject]
        self._obj_ver = 3

    def sid(self, name):
        for n, i in self.strings:
            if n == name: return i
        i = max([i for _, i in self.strings], default=0) + 1
        self.strings.append((name, i)); return i

    @staticmethod
    def read(path):
        d = open(path, 'rb').read()
        if d[:4] == b'EAR\x00':              # a compressed map (official maps, or ours with "Kompaktná mapa")
            import refpack
            d = refpack.decompress_map(d)
        assert d[:4] == b'CkMp', 'not a CkMp map'
        m = MapFile()
        n = struct.unpack_from('<I', d, 4)[0]; p = 8
        for _ in range(n):
            ln = d[p]; p += 1
            nm = d[p:p + ln].decode('latin1'); p += ln
            i = struct.unpack_from('<I', d, p)[0]; p += 4
            m.strings.append((nm, i))
        names = {i: n_ for n_, i in m.strings}
        while p < len(d):
            cid, ver, size = struct.unpack_from('<IHI', d, p); p += 10
            m.chunks.append([names[cid], ver, d[p:p + size]]); p += size
        for c in m.chunks:
            if c[0] == 'HeightMapData': m.height = HeightMap.parse(c[2])
        for c in m.chunks:
            if c[0] == 'BlendTileData': m.blend = BlendTile.parse(c[2], m.height.w, m.height.h)
            if c[0] == 'ObjectsList': m._parse_objects(c, names)
        return m

    def _parse_objects(self, c, names):
        b = c[2]; p = 0; self.objects = []
        while p < len(b):
            cid, ver, size = struct.unpack_from('<IHI', b, p); p += 10
            assert names[cid] == 'Object'
            x, y, z, ang = struct.unpack_from('<ffff', b, p)
            flags = struct.unpack_from('<I', b, p + 16)[0]
            name, q = _rstr(b, p + 20)
            props, q = read_dict(b, q)
            assert q == p + size, 'object size mismatch'
            self.objects.append(MapObject(ver, x, y, z, ang, flags, name, props))
            p += size

    def _dump_objects(self):
        oid = dict(self.strings)  # name->id lookup below
        ids = {n: i for n, i in self.strings}
        out = []
        for o in self.objects:
            body = struct.pack('<ffffI', o.x, o.y, o.z, o.angle, o.flags) + _wstr(o.name) + write_dict(o.props)
            out.append(struct.pack('<IHI', ids['Object'], o.ver, len(body)) + body)
        return b''.join(out)

    def write(self, path, compress=False):
        ids = {n: i for n, i in self.strings}
        out = [b'CkMp', struct.pack('<I', len(self.strings))]
        for n, i in self.strings:
            nb = n.encode('latin1'); out.append(bytes([len(nb)]) + nb + struct.pack('<I', i))
        for name, ver, payload in self.chunks:
            if name == 'HeightMapData' and self.height: payload = self.height.dump()
            elif name == 'BlendTileData' and self.blend: payload = self.blend.dump()
            elif name == 'ObjectsList': payload = self._dump_objects()
            out.append(struct.pack('<IHI', ids[name], ver, len(payload)) + payload)
        data = b''.join(out)
        if compress:                         # RefPack "EAR" - Zero Hour loads it like the official maps
            import refpack
            data = refpack.compress_map(data)
        open(path, 'wb').write(data)

if __name__ == '__main__':
    import sys
    src = sys.argv[1]; dst = sys.argv[2]
    m = MapFile.read(src); m.write(dst)
    a, b = open(src, 'rb').read(), open(dst, 'rb').read()
    print('size', len(a), len(b), 'IDENTICAL' if a == b else 'DIFFERENT')
    print('heightmap', m.height.w, m.height.h, 'objects', len(m.objects))


# ======================================================================================
# BlendTileData tail (everything after the 4 per-cell arrays), format from EA's WorldHeightMap.cpp (v8)
# ======================================================================================
FLAG_VAL = 0x7ADA0000


class BlendTail:
    def __init__(self):
        self.cliff_state = b''      # (w+7)//8 * h bytes
        self.num_bitmap_tiles = 0
        self.classes = []           # (first, num, width, legacy, name)
        self.num_edge_tiles = 0
        self.edge_classes = []      # (first, num, width, name)
        self.blends = []            # (blendNdx, horiz, vert, rdiag, ldiag, inverted, longdiag, edgeClass)
        self.cliffs = []            # raw 4 + 32 + 2 bytes each

    @staticmethod
    def parse(tail, w, h):
        t = BlendTail(); p = 0
        n = ((w + 7) // 8) * h
        t.cliff_state = tail[:n]; p = n
        t.num_bitmap_tiles, nblend, ncliff, ncls = struct.unpack_from('<IIII', tail, p); p += 16
        for _ in range(ncls):
            first, num, wd, leg = struct.unpack_from('<IIII', tail, p); p += 16
            l = struct.unpack_from('<H', tail, p)[0]; name = tail[p + 2:p + 2 + l].decode('latin1'); p += 2 + l
            t.classes.append((first, num, wd, leg, name))
        t.num_edge_tiles, nedge = struct.unpack_from('<II', tail, p); p += 8
        for _ in range(nedge):
            first, num, wd = struct.unpack_from('<III', tail, p); p += 12
            l = struct.unpack_from('<H', tail, p)[0]; name = tail[p + 2:p + 2 + l].decode('latin1'); p += 2 + l
            t.edge_classes.append((first, num, wd, name))
        for _ in range(1, nblend):
            b = struct.unpack_from('<iBBBBBBi', tail, p); p += 4 + 6 + 4
            flag = struct.unpack_from('<I', tail, p)[0]; p += 4
            assert flag == FLAG_VAL, 'bad blend flag'
            t.blends.append(b)
        for _ in range(1, ncliff):
            t.cliffs.append(tail[p:p + 38]); p += 38
        assert p == len(tail), f'blend tail size mismatch {p} != {len(tail)}'
        return t

    def dump(self):
        out = [self.cliff_state,
               struct.pack('<IIII', self.num_bitmap_tiles, len(self.blends) + 1, len(self.cliffs) + 1, len(self.classes))]
        for first, num, wd, leg, name in self.classes:
            out.append(struct.pack('<IIII', first, num, wd, leg) + _wstr(name))
        out.append(struct.pack('<II', self.num_edge_tiles, len(self.edge_classes)))
        for first, num, wd, name in self.edge_classes:
            out.append(struct.pack('<III', first, num, wd) + _wstr(name))
        for b in self.blends:
            out.append(struct.pack('<iBBBBBBi', *b) + struct.pack('<I', FLAG_VAL))
        out += self.cliffs
        return b''.join(out)


def tile_index(cls, x, y):
    """World Builder's tile index for texture class cls=(first,num,width,...) at cell x,y (numpy ok)."""
    first, num, wd = cls[0], cls[1], cls[2]
    t = first + ((x // 2) % wd) + wd * ((y // 2) % wd)
    return (t << 2) + 2 * (y & 1) + (x & 1)


# ======================================================================================
# SidesList with extra players and per-player scripts (formats from EA's SidesList.cpp / Scripts.cpp)
# ======================================================================================
def _chunk(m, name, ver, payload):
    return struct.pack('<IHI', m.sid(name), ver, len(payload)) + payload


def _key(m, name, t):
    return (m.sid(name) << 8) | t


def parse_sides(m):
    b = next(c for c in m.chunks if c[0] == 'SidesList')[2]
    p = 0
    n = struct.unpack_from('<I', b, p)[0]; p += 4
    sides = []
    for _ in range(n):
        d, p = read_dict(b, p)
        nbl = struct.unpack_from('<I', b, p)[0]; p += 4
        assert nbl == 0, 'build lists not supported'
        sides.append(d)
    nt = struct.unpack_from('<I', b, p)[0]; p += 4
    teams = []
    for _ in range(nt):
        d, p = read_dict(b, p); teams.append(d)
    return sides, teams, b[p:]


class Script:
    """One script: list of (actionName, actionEnum, [params]) run once when the game starts.
    params: ('side'|'unit'|'relation', value)."""
    def __init__(self, name, actions, conditions=None, delay=0, once=True, else_actions=None, any_of=None, active=True):
        """conditions: [(conditionName, [params])], all must hold; delay: re-check every N seconds;
        once=False: the script stays active and runs again whenever its conditions hold."""
        self.name, self.actions = name, actions
        self.conditions, self.delay, self.once = conditions or [], int(delay), once
        self.else_actions = else_actions or []      # run when the conditions do NOT hold (ScriptActionFalse)
        self.any_of = any_of or []                  # [[conds], [conds]]: OR of AND-groups (several OrCondition chunks)
        self.active = active


class ScriptGroup:
    # World Builder subroutine groups run only when explicitly called.
    def __init__(self, name, scripts):
        self.name, self.scripts = name, scripts


PARAM_TYPE = {'script': 2, 'object_list': 47, 'subroutine': 13, 'radar_event': 29, 'side': 11, 'sound': 12, 'unit': 14, 'relation': 19, 'int': 0, 'path': 24, 'type': 15, 'team': 3, 'waypoint': 7,
              'comparison': 6, 'area': 9, 'counter': 4, 'real': 1, 'text': 25, 'kindof': 27, 'power': 30, 'science': 31, 'upgrade': 32, 'faction': 46, 'surfaces': 36, 'button': 41}
ACTION_ENUM = {'NO_OP': 5, 'PLAY_SOUND_EFFECT': 7, 'ENABLE_SCRIPT': 8, 'DISABLE_SCRIPT': 9, 'OBJECTLIST_ADDOBJECTTYPE': 287, 'CALL_SUBROUTINE': 10, 'NAMED_FOLLOW_WAYPOINTS': 56, 'PLAYER_RELATES_PLAYER': 86, 'NAMED_GARRISON_SPECIFIC_BUILDING': 107,
               'PLAYER_TRANSFER_OWNERSHIP_PLAYER': 84, 'TEAM_WANDER_IN_PLACE': 233, 'PLAYER_HUNT': 96,
               'RADAR_FORCE_ENABLE': 208, 'OBJECT_CREATE_RADAR_EVENT': 213, 'MAP_REVEAL_ALL': 102,
               'NAMED_STOP_SPECIAL_POWER_COUNTDOWN': 164, 'NAMED_START_SPECIAL_POWER_COUNTDOWN': 165,
               'NAMED_SET_SPECIAL_POWER_COUNTDOWN': 166,
               'PLAYER_GARRISON_ALL_BUILDINGS': 110, 'CREATE_UNNAMED_ON_TEAM_AT_WAYPOINT': 41, 'TEAM_GUARD': 58,
               'TEAM_HUNT': 60, 'SET_TIMER': 6, 'PLAYER_SET_MONEY': 154, 'SET_COUNTER': 2, 'INCREMENT_COUNTER': 15,
               'DEFEAT': 4,
               'DECREMENT_COUNTER': 16, 'SET_MILLISECOND_TIMER': 20, 'DISPLAY_COUNTER': 236, 'MAP_SHROUD_ALL': 146,
               'ENABLE_BORDER_SHROUD': 318, 'DISPLAY_TEXT': 76, 'SHOW_MILITARY_CAPTION': 139, 'DISPLAY_COUNTDOWN_TIMER': 160, 'HIDE_COUNTDOWN_TIMER': 161,
               'MAP_REVEAL_AT_WAYPOINT': 90, 'PLAYER_GIVE_MONEY': 155, 'TEAM_DELETE': 72, 'PLAYER_KILL': 75,
               'NAMED_TRANSFER_OWNERSHIP_PLAYER': 85, 'PLAYER_SET_RANKLEVELLIMIT': 275, 'PLAYER_GRANT_SCIENCE': 276, 'NAMED_DELETE': 71, 'NAMED_RECEIVE_UPGRADE': 252,
               'CREATE_NAMED_ON_TEAM_AT_WAYPOINT': 40, 'TEAM_ATTACK_NAMED': 50, 'TEAM_USE_COMMANDBUTTON_ABILITY_AT_WAYPOINT': 239, 'MOVE_CAMERA_TO': 14, 'RESET_CAMERA': 19, 'NAMED_KILL': 73, 'SET_RANDOM_MSEC_TIMER': 148}
COND_ENUM = {'BUILDING_ENTERED_BY_PLAYER': 50, 'TYPE_SIGHTED': 76, 'NAMED_BUILDING_IS_EMPTY': 77, 'NAMED_DYING': 55, 'NAMED_ATTACKED_BY_PLAYER': 21, 'NAMED_HAS_FREE_CONTAINER_SLOTS': 108, 'PLAYER_TRIGGERED_SPECIAL_POWER_FROM_NAMED': 63, 'CONDITION_TRUE': 3, 'NAMED_NOT_DESTROYED': 16, 'PLAYER_HAS_COMPARISON_UNIT_TYPE_IN_TRIGGER_AREA': 73,
             'TIMER_EXPIRED': 4, 'PLAYER_ALL_DESTROYED': 5, 'COUNTER': 1, 'PLAYER_BUILT_UPGRADE_FROM_NAMED': 69,
             'PLAYER_HAS_COMPARISON_UNIT_KIND_IN_TRIGGER_AREA': 74, 'NAMED_DESTROYED': 15,
             'PLAYER_HAS_OBJECT_COMPARISON': 57, 'SKIRMISH_PLAYER_IS_OUTSIDE_AREA': 98, 'PLAYER_TRIGGERED_SPECIAL_POWER': 60,
             'SKIRMISH_PLAYER_HAS_UNITS_IN_AREA': 96, 'SKIRMISH_PLAYER_FACTION': 86, 'NAMED_OWNED_BY_PLAYER': 30, 'TEAM_INSIDE_AREA_PARTIALLY': 7, 'START_POSITION_IS': 107}
KINDOF = {'CAN_ATTACK': 3, 'STRUCTURE': 7, 'INFANTRY': 8, 'VEHICLE': 9}
GREATER_EQUAL = 3


def _script_bytes(m, s):
    def param(kind, v):
        t = PARAM_TYPE[kind]
        if kind in ('relation', 'int', 'comparison', 'surfaces', 'radar_event'):
            return struct.pack('<Iif', t, int(v), 0.0) + _wstr('')
        if kind == 'kindof':                 # int = KindOf bit, string = its name (as World Builder writes it)
            return struct.pack('<Iif', t, KINDOF[v], 0.0) + _wstr(v)
        if kind == 'real':
            return struct.pack('<Iif', t, 0, float(v)) + _wstr('')
        return struct.pack('<Iif', t, 0, 0.0) + _wstr(str(v))
    def group(conds):
        return _chunk(m, 'OrCondition', 1, b''.join(
            _chunk(m, 'Condition', 4, struct.pack('<I', COND_ENUM[cn]) + struct.pack('<I', _key(m, cn, 3)) +
                   struct.pack('<I', len(ps)) + b''.join(param(k, v) for k, v in ps)) for cn, ps in conds))
    groups = getattr(s, 'any_of', None) or [getattr(s, 'conditions', None) or [('CONDITION_TRUE', [])]]
    body = [_wstr(s.name), _wstr(''), _wstr(''), _wstr(''),
            bytes([int(getattr(s, 'active', True)), int(getattr(s, 'once', True)), 1, 1, 1, 0]), struct.pack('<i', int(getattr(s, 'delay', 0)))]
    body += [group(g) for g in groups]
    for aname, params in s.actions:
        pay = struct.pack('<I', ACTION_ENUM[aname]) + struct.pack('<I', _key(m, aname, 3)) + struct.pack('<I', len(params))
        pay += b''.join(param(k, v) for k, v in params)
        body.append(_chunk(m, 'ScriptAction', 2, pay))
    for aname, params in getattr(s, 'else_actions', []):
        pay = struct.pack('<I', ACTION_ENUM[aname]) + struct.pack('<I', _key(m, aname, 3)) + struct.pack('<I', len(params))
        pay += b''.join(param(k, v) for k, v in params)
        body.append(_chunk(m, 'ScriptActionFalse', 2, pay))
    return _chunk(m, 'Script', 2, b''.join(body))


def _script_entry_bytes(m, entry):
    if isinstance(entry, ScriptGroup):
        body = _wstr(entry.name) + bytes((1, 1))
        body += b''.join(_script_bytes(m, script) for script in entry.scripts)
        return _chunk(m, 'ScriptGroup', 2, body)
    return _script_bytes(m, entry)


def write_sides(m, extra_sides, scripts_by_side, extra_teams=()):
    """extra_sides: list of dicts {name, display, faction, enemies, color}; scripts_by_side: {name: [Script]}."""
    sides, teams, rest = parse_sides(m)
    S = lambda n: m.sid(n)
    for e in extra_sides:
        d = [(_key(m, 'playerName', 3), e['name']), (_key(m, 'playerIsHuman', 0), 0),
             (_key(m, 'playerDisplayName', 4), e['display'].encode('utf-16le')),
             (_key(m, 'playerFaction', 3), e['faction']),
             (_key(m, 'playerEnemies', 3), e.get('enemies', '')), (_key(m, 'playerAllies', 3), '')]
        if 'start_index' in e:      # own skirmish start number: AI attack paths are labelled <Kind><index+1>
            d.append((_key(m, 'multiplayerStartIndex', 1), int(e['start_index'])))
        if 'color' in e:
            col = e['color'] - (1 << 32) if e['color'] > 0x7FFFFFFF else e['color']
            d.append((_key(m, 'playerColor', 1), col))
        sides.append(d)
        teams.append([(_key(m, 'teamName', 3), 'team' + e['name']), (_key(m, 'teamOwner', 3), e['name']),
                      (_key(m, 'teamIsSingleton', 0), 1)])
        for tn in e.get('teams', []):        # extra teams of this side (e.g. insurgent attack waves)
            teams.append([(_key(m, 'teamName', 3), tn), (_key(m, 'teamOwner', 3), e['name']),
                          (_key(m, 'teamIsSingleton', 0), 1)])
    for tn, owner in extra_teams:          # teams of a side already in the map (e.g. SkirmishGLA zombie teams)
        teams.append([(_key(m, 'teamName', 3), tn), (_key(m, 'teamOwner', 3), owner),
                      (_key(m, 'teamIsSingleton', 0), 1)])
    names = []
    for d in sides:
        nm = next((v for k, v in d if k >> 8 == m.sid('playerName')), '')
        names.append(nm)
    out = [struct.pack('<I', len(sides))]
    for d in sides:
        out.append(write_dict(d) + struct.pack('<I', 0))
    out.append(struct.pack('<I', len(teams)))
    for d in teams:
        out.append(write_dict(d))
    lists = b''.join(_chunk(m, 'ScriptList', 1, b''.join(_script_entry_bytes(m, s) for s in scripts_by_side.get(nm, [])))
                     for nm in names)
    out.append(_chunk(m, 'PlayerScriptsList', 1, lists))
    for c in m.chunks:
        if c[0] == 'SidesList':
            c[2] = b''.join(out)
