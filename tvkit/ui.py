"""The flat look of the IPTV client as drawing helpers: dark-navy screen, amber title, white clock, translucent blue selection bar,
grey hints, the default bold font. Everything returns ASS text for mpv's osd-overlay; the canvas is 576 high and its width follows
the window shape (set_canvas). Safe area: 54 / 40 px in from the edges so a CRT's overscan cannot clip anything."""
import datetime
import textwrap

W, H = 768, 576
SX, SY = 54, 40

BG, AMBER, WHITE = "&H180C04&", "&H40D0FF&", "&HFFFFFF&"
GREY, DIM, HILITE, LINE, WARN = "&HC0C0C0&", "&H909090&", "&H603018&", "&H806040&", "&H80C0FF&"


def set_canvas(width):
    global W
    W = int(width)


def esc(s):
    return str(s).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(s, width):
    """Lines of at most `width` characters (for the body text of a story, a description)."""
    out = []
    for para in str(s).split("\n"):
        out += textwrap.wrap(esc(para), width) or [""]
    return out


def clock(now_ms=None, h24=False):
    t = datetime.datetime.fromtimestamp((now_ms / 1000) if now_ms else datetime.datetime.now().timestamp())
    return t.strftime("%H:%M") if h24 else t.strftime("%I:%M %p").lstrip("0")


def mmss(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def rect(x0, y0, x1, y1, color, alpha="00"):
    return (f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{color}\\1a&H{alpha}&\\p1}}"
            f"m {int(x0)} {int(y0)} l {int(x1)} {int(y0)} {int(x1)} {int(y1)} {int(x0)} {int(y1)}{{\\p0}}")


def text(x, y, s, size, color=WHITE, an=7, bold=True, border=2):
    return f"{{\\an{an}\\pos({int(x)},{int(y)})\\fs{size}\\b{int(bold)}\\bord{border}\\shad0\\1c{color}\\3c&H000000&}}{s}"


def background():
    return rect(0, 0, W, H, BG)


def header(title, right=None, h24=False):
    """Amber title top-left, the clock (or `right`) top-right, a thin rule under both."""
    return [text(SX, SY - 6, esc(title), 30, AMBER), text(W - SX, SY - 6, esc(right) if right is not None else clock(h24=h24), 30, an=9),
            rect(SX - 4, SY + 38, W - SX + 4, SY + 40, LINE)]


def footer(hint, note=""):
    out = [text(W // 2, H - SY + 8, esc(hint), 16, DIM, an=2, border=0)]
    if note:
        out.append(text(W // 2, H - SY - 28, esc(note)[:80], 20, WARN, an=2))
    return out


def toast(msg):
    """A small box near the bottom, over whatever is playing."""
    w = min(W - 2 * SX, 12 + 12 * len(msg))
    x0, y0 = (W - w) // 2, H - SY - 74
    return [rect(x0, y0, x0 + w, y0 + 40, BG, "20"), text(W // 2, y0 + 8, esc(msg)[:60], 22, an=8, border=1)]


def progress(x0, x1, y, frac, color=AMBER):
    frac = min(1.0, max(0.0, frac))
    return [rect(x0, y, x1, y + 8, "&H605040&"), rect(x0, y, x0 + (x1 - x0) * frac, y + 8, color)]


# ---- scrolling list ----
class ListView:
    """A list with a selection and a window of `rows` rows. Arrows move, left/right page."""

    def __init__(self, items, rows=8):
        self.items, self.rows, self.sel, self.first = list(items), rows, 0, 0

    def current(self):
        return self.items[self.sel] if self.items else None

    def handle(self, cmd):
        n = len(self.items)
        if not n:
            return False
        step = {"up": -1, "wheel_up": -1, "down": 1, "wheel_down": 1, "left": -self.rows, "right": self.rows}.get(cmd)
        if step is None:
            return False
        if abs(step) == 1:
            self.sel = (self.sel + step) % n
        else:
            self.sel = max(0, min(n - 1, self.sel + step))
        self.first = min(max(self.first, self.sel - self.rows + 1), self.sel) if self.sel >= self.first else self.sel
        self.first = max(0, min(self.first, max(0, n - self.rows)))
        return True

    def visible(self):
        return range(self.first, min(self.first + self.rows, len(self.items)))


def list_rows(view, label, detail=None, y0=SY + 56, row_h=52, empty="Nothing here"):
    """Draw the visible rows. label(item) and detail(item) give the two lines (detail is optional and greyer)."""
    if not view.items:
        return [text(W // 2, H // 2, esc(empty), 24, GREY, an=5, bold=False)]
    out = []
    for n, i in enumerate(view.visible()):
        y, it, sel = y0 + n * row_h, view.items[i], i == view.sel
        if sel:
            out.append(rect(SX - 6, y, W - SX + 6, y + row_h - 4, HILITE, "60"))
        out.append(text(SX + 4, y + 5, esc(label(it))[:max(20, (W - 2 * SX) // 10)], 24, border=1))
        if detail and detail(it):
            out.append(text(SX + 4, y + 31, esc(detail(it))[:max(30, (W - 2 * SX) // 9)], 15, GREY if sel else DIM, bold=False, border=1))
    if len(view.items) > view.rows:                                 # where we are in a long list
        out.append(text(W - SX, H - SY - 28, f"{view.sel + 1} / {len(view.items)}", 16, DIM, an=9, border=0))
    return out


# ---- icon tiles ----
ICON_COLOURS = {"w": "&HF0F0F0&", "a": AMBER, "b": "&HC07830&", "r": "&H5050FF&", "g": "&H909090&", "p": "&HC0A040&"}
_FRAME = ["................"] * 3 + [".wwwwwwwwwwwwww."] + [".w............w."] * 8 + [".wwwwwwwwwwwwww."] + ["................"] * 3
ICONS = {
    "tv": ("a", ["................", "....a......a....", ".....a....a.....", "......a..a......", ".wwwwwwwwwwwwww.",
                 ".wbbbbbbbbbbbbw.", ".wbbbbbbbbbbbbw.", ".wbbbbbbbbbbbbw.", ".wbbbbbbbbbbbbw.", ".wbbbbbbbbbbbbw.",
                 ".wbbbbbbbbbbbbw.", ".wbbbbbbbbbbbbw.", ".wwwwwwwwwwwwww.", "....ww....ww....", "................", "................"]),
    "stream": ("a", ["................", "................", ".wwwwwwwwwwwwww.", ".wbbbbbbbbbbbbw.", ".wbbbaabbbbbbbw.", ".wbbbaaaabbbbbw.",
                     ".wbbbaaaaaabbbw.", ".wbbbaaaaaabbbw.", ".wbbbaaaabbbbbw.", ".wbbbaabbbbbbbw.", ".wbbbbbbbbbbbbw.",
                     ".wwwwwwwwwwwwww.", ".......ww.......", "....wwwwwwww....", "................", "................"]),
    "restart": ("a", ["................", ".....aaaaaa.....", "...aa......aa.a.", "..aa........aaa.", "..a.........aaaa", "..a.............",
                      "..a.............", "..a.............", "..a.............", "..aa............", "...aa......aa...", ".....aaaaaaaa...",
                      "................", "................", "................", "................"]),
    "power": ("r", ["................", ".......rr.......", ".......rr.......", "...rr..rr..rr...", "..rrr..rr..rrr..", ".rr....rr....rr.",
                    ".rr....rr....rr.", ".rr..........rr.", ".rr..........rr.", ".rr..........rr.", "..rr........rr..",
                    "...rrr....rrr...", ".....rrrrrr.....", "................", "................", "................"]),
    "clock": ("a", ["................", ".....wwwwww.....", "...ww......ww...", "..w..........w..", "..w....a.....w..", ".w.....a......w.",
                    ".w.....a......w.", ".w.....aaaa...w.", ".w............w.", ".w............w.", "..w..........w..", "..w..........w..",
                    "...ww......ww...", ".....wwwwww.....", "................", "................"]),
    "photos": ("a", ["................", "................", ".wwwwwwwwwwwwww.", ".wbbbbbbbbbbbbw.", ".wbbbbbbbbbaabw.", ".wbbbbbbbbaaaaw.",
                     ".wbbbbbbbbbaabw.", ".wbbbbabbbbbbbw.", ".wbbbaaabbbbbbw.", ".wbbaaaaabbbbbw.", ".wbaaaaaaabbbbw.",
                     ".wwwwwwwwwwwwww.", "................", "................", "................", "................"]),
    "info": ("a", ["................", ".....aaaaaa.....", "...aa......aa...", "..a....aa....a..", "..a....aa....a..", "..a..........a..",
                    "..a....aa....a..", "..a....aa....a..", "..a....aa....a..", "..a....aa....a..", "...aa......aa...", ".....aaaaaa.....",
                    "................", "................", "................", "................"]),
    "news": ("a", ["................", ".wwwwwwwwwwwwww.", ".waaaa.wwwwwww..", ".waaaa..........", ".waaaa.wwwwwww..", ".w......wwwwww..",
                    ".wwwwww.wwwwww..", ".w..............", ".wwwwwwwwwwwwww.", ".w..............", ".wwwwwwwwwwwwww.", ".wwwwwwwwwwwwww.",
                    "................", "................", "................", "................"]),
    "folder": ("a", ["................", "................", "..aaaaa.........", "..aaaaaaaaaaaa..", "..aaaaaaaaaaaa..", "..aaaaaaaaaaaa..",
                      "..aaaaaaaaaaaa..", "..aaaaaaaaaaaa..", "..aaaaaaaaaaaa..", "..aaaaaaaaaaaa..", "................", "................",
                      "................", "................", "................", "................"]),
    "music": ("a", ["................", "......aaaaaaaa..", "......aaaaaaaa..", "......aa....aa..", "......aa....aa..", "......aa....aa..",
                     "......aa....aa..", "....aaa...aaa...", "...aaaa..aaaa...", "....aa....aa....", "................", "................",
                     "................", "................", "................", "................"]),
    "app": ("a", _FRAME),
}
PX = 6


def icon(x, y, name, px=PX):
    """A 16x16 pixel icon as rectangles (a run of one colour is one rectangle). Unknown names get a plain frame."""
    colour, rows = ICONS.get(name, ICONS["app"])
    out = []
    for r, row in enumerate(rows):
        c = 0
        while c < len(row):
            ch = row[c]
            if ch == ".":
                c += 1
                continue
            end = c
            while end < len(row) and row[end] == ch:
                end += 1
            col = ICON_COLOURS["w" if ch == "w" else "b" if ch == "b" else colour if ch in "ar" else "g"]
            out.append(rect(x + c * px, y + r * px, x + end * px, y + (r + 1) * px + 1, col))
            c = end
    return out


TILE_W, TILE_H, GAP = 144, 150, 16


def grid_cols(n, width=None):
    return max(1, min(n, ((width or W) - 2 * SX + GAP) // (TILE_W + GAP)))


def tile_grid(tiles, sel, confirm_id=None):
    """tiles: [{id, name, icon}]. Returns (ass lines, columns). Numbered 1-9, the selected one framed in amber."""
    n = len(tiles)
    cols = grid_cols(n)
    rows = (n + cols - 1) // cols
    gx0 = (W - (cols * TILE_W + (cols - 1) * GAP)) // 2
    gy0 = SY + 70 + max(0, (H - 2 * SY - 70 - 120 - rows * TILE_H - (rows - 1) * GAP) // 2)
    out = []
    for i, e in enumerate(tiles):
        x, y, on = gx0 + (i % cols) * (TILE_W + GAP), gy0 + (i // cols) * (TILE_H + GAP), i == sel
        out.append(rect(x, y, x + TILE_W, y + TILE_H, HILITE if on else "&H281810&", "40" if on else "30"))
        if on:
            out += [rect(x, y, x + TILE_W, y + 3, AMBER), rect(x, y + TILE_H - 3, x + TILE_W, y + TILE_H, AMBER),
                    rect(x, y, x + 3, y + TILE_H, AMBER), rect(x + TILE_W - 3, y, x + TILE_W, y + TILE_H, AMBER)]
        out += icon(x + (TILE_W - 16 * PX) // 2, y + 10, e.get("icon") or e["id"])
        out.append(text(x + TILE_W // 2, y + TILE_H - 34, "SURE?" if confirm_id == e["id"] else esc(e["name"])[:14], 19,
                        AMBER if on else WHITE, an=8, border=1))
        if i < 10:
            out.append(text(x + 10, y + 8, str((i + 1) % 10), 16, AMBER if on else DIM, border=1))
    return out, cols
