"""Pictures without an imaging library: decode a BMP (ReMoment's /api/artwork/*.bmp) or a PNG (Jellyfin's ?format=Png; its Bmp option is
ignored by real servers, which send JPEG) into raw BGRA, and show it with mpv's overlay-add. JPEG is not supported: ask for PNG or BMP. mpv draws images in WINDOW pixels, not our 576-high canvas: scale with Shell.window_scale().
"""
import os
import struct
import zlib


class ImageError(ValueError):
    pass


def parse_bmp(data):
    """(width, height, bgra bytes, top-down) from an uncompressed 8-bit (palette), 24-bit or 32-bit BMP."""
    if data[:2] != b"BM" or len(data) < 54:
        raise ImageError("not a BMP")
    off = struct.unpack_from("<I", data, 10)[0]
    hdr = struct.unpack_from("<I", data, 14)[0]
    if hdr < 40:
        raise ImageError("unsupported BMP header")
    w, h, _planes, bpp, comp = struct.unpack_from("<iiHHI", data, 18)
    if comp not in (0, 3) or bpp not in (8, 24, 32) or w <= 0 or h == 0 or w > 4096 or abs(h) > 4096:
        raise ImageError("unsupported BMP")
    top_down, h = h < 0, abs(h)
    stride = ((w * bpp + 31) // 32) * 4
    if off + stride * h > len(data):
        raise ImageError("truncated BMP")
    palette = []
    if bpp == 8:
        ncol = struct.unpack_from("<I", data, 46)[0] or 256
        pal_at = 14 + hdr
        palette = [data[pal_at + 4 * i:pal_at + 4 * i + 3] + b"\xff" for i in range(ncol)]
    out = bytearray()
    rows = range(h) if top_down else range(h - 1, -1, -1)       # BMP rows are normally bottom-up: emit top-down
    for r in rows:
        row = data[off + r * stride: off + r * stride + stride]
        if bpp == 32:
            out += row[:w * 4]
        elif bpp == 24:
            for x in range(w):
                out += row[x * 3:x * 3 + 3] + b"\xff"
        else:
            for x in range(w):
                out += palette[row[x]] if row[x] < len(palette) else b"\0\0\0\xff"
    return w, h, bytes(out)


_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def parse_png(data):
    """(width, height, bgra bytes) from a non-interlaced 8-bit PNG (grey, grey+alpha, palette, RGB or RGBA)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImageError("not a PNG")
    pos, idat, palette, hdr = 8, [], b"", None
    while pos + 8 <= len(data):
        n, kind = struct.unpack_from(">I4s", data, pos)
        chunk = data[pos + 8:pos + 8 + n]
        if len(chunk) < n:
            raise ImageError("truncated PNG")
        if kind == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
        pos += 12 + n
    if not hdr:
        raise ImageError("PNG without a header")
    w, h, depth, ctype, _comp, _filt, interlace = hdr
    if depth != 8 or ctype not in _PNG_CHANNELS or interlace or not (0 < w <= 4096 and 0 < h <= 4096):
        raise ImageError("unsupported PNG")
    ch = _PNG_CHANNELS[ctype]
    stride = w * ch
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        raise ImageError("damaged PNG") from None
    if len(raw) < (stride + 1) * h:
        raise ImageError("truncated PNG")
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(ch, stride):
                row[i] = (row[i] + row[i - ch]) & 255
        elif f == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                row[i] = (row[i] + (((row[i - ch] if i >= ch else 0) + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a, b, c = (row[i - ch] if i >= ch else 0), prev[i], (prev[i - ch] if i >= ch else 0)
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                row[i] = (row[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            raise ImageError("damaged PNG")
        rows.append(row)
        prev = row
    out = bytearray()
    for row in rows:
        if ctype == 6:
            for i in range(0, stride, 4):
                out += bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))
        elif ctype == 2:
            for i in range(0, stride, 3):
                out += bytes((row[i + 2], row[i + 1], row[i], 255))
        elif ctype == 0:
            for v in row:
                out += bytes((v, v, v, 255))
        elif ctype == 4:
            for i in range(0, stride, 2):
                out += bytes((row[i], row[i], row[i], row[i + 1]))
        else:
            for v in row:
                out += bytes((palette[v * 3 + 2], palette[v * 3 + 1], palette[v * 3], 255)) if v * 3 + 2 < len(palette) else b"\0\0\0\xff"
    return w, h, bytes(out)


def parse_image(data):
    """(width, height, bgra) from a BMP or a PNG, decided by the first bytes."""
    if data[:2] == b"BM":
        return parse_bmp(data)
    if data[:4] == b"\x89PNG":
        return parse_png(data)
    raise ImageError("unsupported picture format (ask the server for PNG or BMP)")


def make_png(w, h, pixel, filter_type=0, ctype=2):
    """A PNG from pixel(x, y) -> (r, g, b[, a]); every row uses `filter_type` (0-4); for tests."""
    ch = _PNG_CHANNELS[ctype]
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            px = pixel(x, y)
            row += bytes(px[:ch] if ctype != 0 else px[:1])
        rows.append(row)
    raw, prev = bytearray(), bytearray(w * ch)
    for row in rows:
        out = bytearray(len(row))
        for i in range(len(row)):
            a, b, c = (row[i - ch] if i >= ch else 0), prev[i], (prev[i - ch] if i >= ch else 0)
            pred = (0, a, b, (a + b) >> 1, (a if abs(b - c) <= abs(a - c) and abs(b - c) <= abs(a + b - 2 * c) else b if abs(a - c) <= abs(a + b - 2 * c) else c))[filter_type]
            out[i] = (row[i] - pred) & 255
        raw += bytes([filter_type]) + out
        prev = row

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def show(mpv, oid, x, y, w, h, bgra):
    """Draw w x h BGRA pixels at window position x, y as overlay `oid` (0..63). Replaces whatever that id showed."""
    path = os.path.join(mpv.dir, f"img{oid}.bgra")
    with open(path, "wb") as f:
        f.write(bgra)
    mpv.command("overlay-add", oid, int(x), int(y), path, 0, "bgra", w, h, w * 4)


def hide(mpv, oid):
    mpv.command("overlay-remove", oid)


def make_bmp(w, h, pixel):
    """A 24-bit BMP from pixel(x, y) -> (r, g, b); for tests and for the spike."""
    stride = ((w * 24 + 31) // 32) * 4
    rows = bytearray()
    for y in range(h - 1, -1, -1):
        row = bytearray()
        for x in range(w):
            r, g, b = pixel(x, y)
            row += bytes((b, g, r))
        rows += row + b"\0" * (stride - len(row))
    return b"BM" + struct.pack("<IHHI", 54 + len(rows), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(rows), 2835, 2835, 0, 0) + bytes(rows)
