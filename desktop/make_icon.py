#!/usr/bin/env python3
"""Draw the launcher's icon (a 2 x 2 grid of tiles in the launcher's navy and amber) with no imaging library.

    python3 desktop/make_icon.py out.png [size]           # one PNG (Linux desktop entry, default 256)
    python3 desktop/make_icon.py --iconset out.iconset    # all macOS sizes; then: iconutil -c icns out.iconset
"""
import os
import struct
import sys
import zlib

N = 32
BG, TILE, EDGE = (6, 12, 24), (24, 48, 96), (255, 208, 64)
GLYPHS = {0: (255, 208, 64), 1: (224, 224, 240), 2: (96, 160, 255), 3: (255, 96, 96)}      # amber, white, blue, red marks on the four tiles


def design():
    g = [[None] * N for _ in range(N)]

    def rect(x0, y0, x1, y1, c):
        for y in range(y0, y1):
            for x in range(x0, x1):
                g[y][x] = c
    rect(0, 0, N, N, BG)
    for (x, y) in ((0, 0), (N - 1, 0), (0, N - 1), (N - 1, N - 1), (1, 0), (0, 1), (N - 2, 0), (N - 1, 1), (1, N - 1), (0, N - 2), (N - 2, N - 1), (N - 1, N - 2)):
        g[y][x] = None                                       # rounded corners
    for i, (tx, ty) in enumerate(((3, 3), (17, 3), (3, 17), (17, 17))):
        rect(tx, ty, tx + 12, ty + 12, EDGE if i == 0 else TILE)          # the first tile is "selected": an amber frame
        if i == 0:
            rect(tx + 1, ty + 1, tx + 11, ty + 11, TILE)
        c = GLYPHS[i]
        if i == 0:                                           # a play triangle
            for k in range(5):
                rect(tx + 4 + k, ty + 3 + k, tx + 5 + k, ty + 9 - k, c)
        elif i == 1:                                         # a clock
            rect(tx + 3, ty + 3, tx + 9, ty + 4, c); rect(tx + 3, ty + 8, tx + 9, ty + 9, c)
            rect(tx + 3, ty + 3, tx + 4, ty + 9, c); rect(tx + 8, ty + 3, tx + 9, ty + 9, c); rect(tx + 5, ty + 4, tx + 6, ty + 7, c)
        elif i == 2:                                         # a picture
            rect(tx + 3, ty + 3, tx + 9, ty + 9, c); rect(tx + 4, ty + 4, tx + 8, ty + 8, TILE); rect(tx + 4, ty + 7, tx + 6, ty + 8, c); rect(tx + 6, ty + 6, tx + 8, ty + 7, c)
        else:                                                # a note
            rect(tx + 5, ty + 3, tx + 6, ty + 8, c); rect(tx + 6, ty + 3, tx + 9, ty + 4, c); rect(tx + 3, ty + 7, tx + 6, ty + 9, c)
    return g


def png(rows, size):
    raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def render(size):
    g = design()
    return png([[(0, 0, 0, 0) if g[y * N // size][x * N // size] is None else (*g[y * N // size][x * N // size], 255) for x in range(size)] for y in range(size)], size)


def main(argv):
    if len(argv) == 3 and argv[1] == "--iconset":
        os.makedirs(argv[2], exist_ok=True)
        for base in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                with open(os.path.join(argv[2], f"icon_{base}x{base}" + ("@2x" if scale == 2 else "") + ".png"), "wb") as f:
                    f.write(render(base * scale))
    elif len(argv) in (2, 3):
        with open(argv[1], "wb") as f:
            f.write(render(int(argv[2]) if len(argv) == 3 else 256))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
