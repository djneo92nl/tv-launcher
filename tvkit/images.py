"""Pictures without an imaging library: decode a BMP (what ReMoment's /api/artwork/*.bmp and Jellyfin's ?format=Bmp give)
into raw BGRA, and show it with mpv's overlay-add. mpv draws images in WINDOW pixels, not our 576-high canvas: scale with Shell.window_scale().
"""
import os
import struct


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
