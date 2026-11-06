"""The flat look of the IPTV client as drawing helpers: dark-navy screen, amber title, white clock, translucent blue selection bar,
grey hints, the default bold font. Everything returns ASS text for mpv's osd-overlay; the canvas is 576 high and its width follows
the window shape (set_canvas). Safe area: 54 / 40 px in from the edges so a CRT's overscan cannot clip anything."""
import datetime
import textwrap

W, H = 768, 576
SAFE_PCT = 7                      # how much of each edge a TV may hide (overscan): a percentage of the canvas, see set_safe()
SX, SY = 54, 40                   # the same margin in canvas units (7% of 768 x 576); kept up to date by set_canvas / set_safe

BG, AMBER, WHITE = "&H180C04&", "&H40D0FF&", "&HFFFFFF&"
RED = "&H5050FF&"
GREY, DIM, HILITE, LINE, WARN = "&HC0C0C0&", "&H909090&", "&H603018&", "&H806040&", "&H80C0FF&"


def set_safe(percent):
    """How far from each edge things are drawn, as a percentage of the canvas (0 to 15). Some TVs and monitors crop the picture; raise it until nothing is cut off."""
    global SAFE_PCT, SX, SY
    SAFE_PCT = max(0, min(15, float(percent)))
    SX, SY = round(W * SAFE_PCT / 100), round(H * SAFE_PCT / 100)


def set_canvas(width):
    global W
    W = int(width)
    set_safe(SAFE_PCT)


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


def list_rows(view, label, detail=None, y0=SY + 56, row_h=52, empty="Nothing here", x0=SX, x1=None):
    """Draw the visible rows between x0 and x1 (default: the full width). label(item) and detail(item) give the two lines (detail is optional and greyer)."""
    x1 = x1 or W - SX
    if not view.items:
        return [text((x0 + x1) // 2, H // 2, esc(empty), 24, GREY, an=5, bold=False)]
    out = []
    for n, i in enumerate(view.visible()):
        y, it, sel = y0 + n * row_h, view.items[i], i == view.sel
        if sel:
            out.append(rect(x0 - 6, y, x1 + 6, y + row_h - 4, HILITE, "60"))
        out.append(text(x0 + 4, y + 5, esc(label(it))[:max(12, (x1 - x0) // 10)], 24, border=1))
        if detail and detail(it):
            out.append(text(x0 + 4, y + 31, esc(detail(it))[:max(20, (x1 - x0) // 7)], 15, GREY if sel else DIM, bold=False, border=1))
    if len(view.items) > view.rows:                                 # where we are in a long list
        out.append(text(x1, H - SY - 28, f"{view.sel + 1} / {len(view.items)}", 16, DIM, an=9, border=0))
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
    "remote": ("a", ["................", "....wwwwwwww....", "....w......w....", "....w.bbbb.w....", "....w.bbbb.w....", "....w......w....", "....w.aaaa.w....", "....w......w....",
                      "....w.a..a.w....", "....w......w....", "....w.a..a.w....", "....w......w....", "....wwwwwwww....", "................", "................", "................"]),
    "app": ("a", _FRAME),
}
PX = 6


def _path(boxes):
    return " ".join(f"m {int(x0)} {int(y0)} l {int(x1)} {int(y0)} {int(x1)} {int(y1)} {int(x0)} {int(y1)}" for x0, y0, x1, y1 in boxes)


def rects(boxes, color, alpha="00"):
    """Many rectangles of one colour as ONE drawing event. mpv's text renderer redraws every event whenever anything changes, so fewer events is faster
    (a 16x16 icon used to be about 25 events; on a Raspberry Pi 2 that adds up across a screen of icons)."""
    return f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{color}\\1a&H{alpha}&\\p1}}{_path(boxes)}{{\\p0}}"


def icon(x, y, name, px=PX):
    """A 16x16 pixel icon: one drawing event per colour (usually two or three). Unknown names get a plain frame."""
    colour, rows = ICONS.get(name, ICONS["app"])
    by_colour = {}
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
            by_colour.setdefault(col, []).append((x + c * px, y + r * px, x + end * px, y + (r + 1) * px))
            c = end
    return [rects(boxes, col) for col, boxes in by_colour.items()]


# tile size, gap, icon pixel size, label size: the largest that fits every tile in the space under the header and above the description
TILE_SIZES = ((128, 134, 14, 5, 18), (112, 120, 12, 5, 16), (100, 108, 10, 4, 15), (88, 96, 8, 4, 14))


def tile_layout(n, width=None, height=None):
    """{tw, th, gap, px, font, cols, rows} for n tiles on a canvas: tiles shrink, never overflow into the description and hint lines."""
    width, height = width or W, height or H
    avail_w, avail_h = width - 2 * SX, height - 2 * SY - 70 - 110
    for tw, th, gap, px, font in TILE_SIZES:
        cols = max(1, min(n, (avail_w + gap) // (tw + gap)))
        rows = -(-max(n, 1) // cols)
        if rows * (th + gap) - gap <= avail_h:
            break
    return {"tw": tw, "th": th, "gap": gap, "px": px, "font": font, "cols": cols, "rows": rows}


def grid_cols(n, width=None):
    return tile_layout(n, width)["cols"]


def tile_grid(tiles, sel, confirm_id=None):
    """tiles: [{id, name, icon}]. Returns (ass lines, columns). Numbered 1-9, the selected one framed in amber."""
    n = len(tiles)
    m = tile_layout(n)
    tw, th, gap, px, cols, rows = m["tw"], m["th"], m["gap"], m["px"], m["cols"], m["rows"]
    gx0 = (W - (cols * tw + (cols - 1) * gap)) // 2
    gy0 = SY + 70 + max(0, (H - 2 * SY - 70 - 110 - rows * th - (rows - 1) * gap) // 2)
    out = []
    for i, e in enumerate(tiles):
        x, y, on = gx0 + (i % cols) * (tw + gap), gy0 + (i // cols) * (th + gap), i == sel
        out.append(rect(x, y, x + tw, y + th, HILITE if on else "&H281810&", "40" if on else "30"))
        if on:
            out.append(rects([(x, y, x + tw, y + 3), (x, y + th - 3, x + tw, y + th), (x, y, x + 3, y + th), (x + tw - 3, y, x + tw, y + th)], AMBER))
        out += icon(x + (tw - 16 * px) // 2, y + 8, e.get("icon") or e["id"], px)
        out.append(text(x + tw // 2, y + th - m["font"] - 12, "SURE?" if confirm_id == e["id"] else esc(e["name"])[:max(8, tw // 9)], m["font"], AMBER if on else WHITE, an=8, border=1))
        if i < 10:
            out.append(text(x + 8, y + 6, str((i + 1) % 10), 14, AMBER if on else DIM, border=1))
    return out, cols


def ass_colour(hex_colour, fallback=AMBER):
    """"#rrggbb" (as the ReMoment / Jellyfin APIs give colours) to ASS "&HBBGGRR&"."""
    h = (hex_colour or "").lstrip("#")
    if len(h) != 6 or any(c not in "0123456789abcdefABCDEF" for c in h):
        return fallback
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


# ---- grid (covers, posters) ----
class GridView:
    """Items laid out `cols` wide and `rows` high per page; arrows move the selection, the page follows it."""

    def __init__(self, items, cols=4, rows=2):
        self.items, self.cols, self.rows, self.sel = list(items), cols, rows, 0

    def current(self):
        return self.items[self.sel] if self.items else None

    @property
    def first(self):
        """Index of the first visible item: the page (row-aligned) holding the selection, scrolled a row at a time."""
        return max(0, self.sel // self.cols - self.rows + 1) * self.cols

    def visible(self):
        return range(self.first, min(self.first + self.cols * self.rows, len(self.items)))

    def handle(self, cmd):
        n = len(self.items)
        if not n:
            return False
        c = self.cols
        if cmd in ("left", "wheel_up"):
            self.sel = (self.sel - 1) % n
        elif cmd in ("right", "wheel_down"):
            self.sel = (self.sel + 1) % n
        elif cmd == "down":
            if self.sel + c < n:
                self.sel += c
            elif self.sel // c < (n - 1) // c:                 # a short last row: go to its last item
                self.sel = n - 1
        elif cmd == "up":
            self.sel = self.sel - c if self.sel - c >= 0 else self.sel
        else:
            return False
        return True

    def near_end(self, margin=None):
        return self.sel >= len(self.items) - (margin or self.cols * 2)


# ---- on-screen keyboard (for a remote with only arrows and OK) ----
KEY_ROWS = ["abcdefghij", "klmnopqrst", "uvwxyz0123", "456789'.-&"]
KEY_ROWS_FULL = KEY_ROWS + ["!@#$%^*()_", "+=?/:;,~<>"]          # for passwords and addresses
KEY_ACTIONS = ["SPACE", "DELETE", "CLEAR", "SEARCH"]


class Keyboard:
    """Letters in a grid plus an action row. handle(cmd) returns "submit" when the last action is pressed; typed letters (char:x, backspace) work too.
    full=True adds symbol rows and a SHIFT key; secret=True shows dots instead of the text; `submit_label` names the last action (SEARCH, LOGIN, NEXT)."""

    def __init__(self, text="", full=False, secret=False, submit_label="SEARCH"):
        self.text, self.r, self.c, self.secret = text, 0, 0, secret
        self.rows = KEY_ROWS_FULL if full else KEY_ROWS
        self.actions = (["SPACE", "SHIFT", "DELETE", "CLEAR", submit_label] if full else ["SPACE", "DELETE", "CLEAR", submit_label])
        self.shift = False

    def _row_len(self, r):
        return len(self.actions) if r == len(self.rows) else len(self.rows[r])

    def _key(self, r, c):
        ch = self.rows[r][c]
        return ch.upper() if self.shift else ch

    def handle(self, cmd):
        last = len(self.rows)
        if cmd == "up":
            self.r = (self.r - 1) % (last + 1)
        elif cmd == "down":
            self.r = (self.r + 1) % (last + 1)
        elif cmd == "left":
            self.c = (self.c - 1) % self._row_len(self.r)
        elif cmd == "right":
            self.c = (self.c + 1) % self._row_len(self.r)
        elif cmd == "enter":
            if self.r < last:
                self.text += self._key(self.r, self.c)
            else:
                act = self.actions[min(self.c, len(self.actions) - 1)]
                if act == "SPACE":
                    self.text += " "
                elif act == "SHIFT":
                    self.shift = not self.shift
                elif act == "DELETE":
                    self.text = self.text[:-1]
                elif act == "CLEAR":
                    self.text = ""
                else:
                    return "submit"
        elif cmd == "backspace":
            self.text = self.text[:-1]
        elif cmd.startswith("char:"):
            ch = cmd[5:]
            self.text += " " if ch == "space" else ch
        else:
            return None
        self.c = min(self.c, self._row_len(self.r) - 1)
        return "changed"

    def draw(self, y0=SY + 110):
        shown = "*" * len(self.text) if self.secret else esc(self.text)
        cell = min(54, (W - 2 * SX) // 10, (H - y0 - 90) // (len(self.rows) + 1))
        out = [rect(SX, y0 - 52, W - SX, y0 - 8, "&H281810&"), text(SX + 14, y0 - 44, shown + "_", 28, border=1)]
        x0 = (W - cell * 10) // 2
        for r, row in enumerate(self.rows):
            for c, ch in enumerate(row):
                on = (r, c) == (self.r, self.c)
                x, y = x0 + c * cell, y0 + r * cell
                out.append(rect(x + 2, y + 2, x + cell - 2, y + cell - 2, HILITE if on else "&H281810&", "40" if on else "30"))
                out.append(text(x + cell // 2, y + cell // 2, esc(self._key(r, c)), 22, AMBER if on else WHITE, an=5, border=1))
        ay, aw = y0 + len(self.rows) * cell + 8, (cell * 10) // len(self.actions)
        for c, name in enumerate(self.actions):
            on = (len(self.rows), c) == (self.r, self.c)
            x = x0 + c * aw
            out.append(rect(x + 2, ay, x + aw - 2, ay + 38, HILITE if on else "&H281810&", "40" if on else "30"))
            out.append(text(x + aw // 2, ay + 19, name + (" *" if name == "SHIFT" and self.shift else ""), 17, AMBER if on else WHITE, an=5, border=1))
        return out
