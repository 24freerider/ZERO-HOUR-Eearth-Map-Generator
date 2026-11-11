"""RefPack (EA "EAR" compression): the format Zero Hour uses for compressed .map files (the official maps are stored so).
A compressed map = b'EAR\\0' + uncompressed size (uint32 LE) + RefPack stream. The game unpacks it when loading."""
import struct

MAX_OFF = 131072
MAX_LEN = 1028


def unpack(src):
    p = 2
    flags = src[0] << 8 | src[1]
    if flags & 0x8000:
        if flags & 0x0100: p += 4
        size = int.from_bytes(src[p:p + 4], 'big'); p += 4
    else:
        if flags & 0x0100: p += 3
        size = int.from_bytes(src[p:p + 3], 'big'); p += 3
    out = bytearray()
    n_src = len(src)
    while p < n_src:
        b0 = src[p]
        if b0 < 0x80:
            b1 = src[p + 1]; p += 2
            lit = b0 & 3; n = ((b0 & 0x1c) >> 2) + 3; off = ((b0 & 0x60) << 3) + b1 + 1
        elif b0 < 0xC0:
            b1, b2 = src[p + 1], src[p + 2]; p += 3
            lit = b1 >> 6; n = (b0 & 0x3f) + 4; off = ((b1 & 0x3f) << 8) + b2 + 1
        elif b0 < 0xE0:
            b1, b2, b3 = src[p + 1], src[p + 2], src[p + 3]; p += 4
            lit = b0 & 3; n = ((b0 & 0x0c) << 6) + b3 + 5; off = ((b0 & 0x10) << 12) + (b1 << 8) + b2 + 1
        elif b0 < 0xFC:
            lit = ((b0 & 0x1f) << 2) + 4; n = 0; off = 0; p += 1
        else:
            lit = b0 & 3; p += 1
            out += src[p:p + lit]
            break
        out += src[p:p + lit]; p += lit
        if n:
            start = len(out) - off
            if off >= n:
                out += out[start:start + n]
            else:
                for k in range(n): out.append(out[start + k])
    return bytes(out), size


def pack(data, chain=16):
    """Greedy LZ with hash chains, written as RefPack commands."""
    data = bytes(data)
    n = len(data)
    if n >= 1 << 24:
        out = bytearray(b'\x90\xfb') + n.to_bytes(4, 'big')
    else:
        out = bytearray(b'\x10\xfb') + n.to_bytes(3, 'big')
    head = {}
    i = 0; lit_start = 0

    def flush_literals(upto):
        nonlocal lit_start
        L = upto - lit_start
        while L > 3:
            k = min(112, L - (L & 3))
            out.append(0xE0 | ((k - 4) >> 2))
            out.extend(data[lit_start:lit_start + k])
            lit_start += k; L -= k
        return L

    while i < n - 3:
        key = data[i:i + 4]
        cands = head.get(key)
        best_len = 0; best_off = 0
        if cands:
            lim = min(MAX_LEN, n - i)
            for j in reversed(cands):
                off = i - j
                if off > MAX_OFF: break
                if data[j + best_len:j + best_len + 1] != data[i + best_len:i + best_len + 1] and best_len:
                    continue
                # extend the match fast in blocks
                L = 4
                while L < lim:
                    step = min(32, lim - L)
                    if data[j + L:j + L + step] == data[i + L:i + L + step]:
                        L += step
                    else:
                        while L < lim and data[j + L] == data[i + L]: L += 1
                        break
                if L > best_len:
                    best_len, best_off = L, off
                    if L >= lim: break
            if len(cands) >= chain:
                del cands[0]
            cands.append(i)
        else:
            head[key] = [i]
        ok = best_len >= 4 and (best_off <= 16384 or best_len >= 5)
        if not ok:
            i += 1
            continue
        lit = flush_literals(i)
        L, off = best_len, best_off - 1
        if L <= 10 and best_off <= 1024:
            out.append(((off >> 3) & 0x60) | ((L - 3) << 2) | lit); out.append(off & 0xff)
        elif L <= 67 and best_off <= 16384:
            out.append(0x80 | (L - 4)); out.append((lit << 6) | (off >> 8)); out.append(off & 0xff)
        else:
            out.append(0xC0 | (((off >> 16) & 1) << 4) | (((L - 5) >> 8) << 2) | lit)
            out.append((off >> 8) & 0xff); out.append(off & 0xff); out.append((L - 5) & 0xff)
        out += data[lit_start:lit_start + lit]
        # remember a few positions inside the match (keeps it fast)
        end = i + L
        for q in range(i + 1, min(end, n - 3), 4 if L > 16 else 1):
            k2 = data[q:q + 4]
            c2 = head.get(k2)
            if c2 is None: head[k2] = [q]
            else:
                if len(c2) >= chain: del c2[0]
                c2.append(q)
        i = end; lit_start = end
    lit = flush_literals(n)
    out.append(0xFC | lit); out += data[lit_start:lit_start + lit]
    return bytes(out)


def compress_map(raw):
    return b'EAR\x00' + struct.pack('<I', len(raw)) + pack(raw)


def decompress_map(d):
    if d[:4] != b'EAR\x00':
        return d
    raw, size = unpack(d[8:])
    return raw
