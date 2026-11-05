#!/usr/bin/env python3
"""ReMoment: browse your ReMoment music library with covers, search it, and play it here through mpv.
Server address: "remoment.host" in config.json (default http://remoment.local)."""
import os
import queue
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))    # the repo root, for tvkit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tvkit import images, inputs, ui
from tvkit.images import ImageError
from tvkit.net import NetError
from tvkit.player import Playback
from tvkit.shell import Shell, run_app

import remoment_client as rm

HOME = [("albums", "ALBUMS", "Every album in your own files"), ("artists", "ARTISTS", "A to Z"), ("playlists", "PLAYLISTS", "Your playlists"),
        ("recent", "RECENTLY PLAYED", "Albums you listened to lately"), ("favourites", "FAVOURITES", "Albums, artists and tracks you hearted"),
        ("search", "SEARCH", "Find an artist, album or track")]
IMG_GRID, IMG_ONE = 10, 40            # mpv overlay ids for covers: 10.. for the grid, 40 for the single cover of a screen
CELL_H = 188


class Screen:
    def __init__(self, kind, title, view=None, loader=None, **extra):
        self.kind, self.title, self.view, self.loader = kind, title, view, loader
        self.cursor, self.loading, self.error, self.done = None, False, "", False
        self.__dict__.update(extra)


def fmt_time(sec):
    return ui.mmss(sec) if sec else ""


class ReMoment(Shell):
    name = "remoment"

    # ---- start / connection ----
    def start(self):
        c = self.cfg.get("remoment", {})
        self.client = rm.Client(c.get("host", "http://remoment.local"), c.get("scope", "files"))
        self.pb = Playback(self.mpv, None, audio_only=True)
        self.stack, self.now_open, self.note = [], False, ""
        self.connected, self.conn_error = False, ""
        self.queue, self.qi, self.fail_run = [], -1, 0
        self.covers, self.cover_q, self.cover_pending = {}, queue.Queue(), set()
        self.img_shown = {}
        self.search_text = ""
        threading.Thread(target=self._cover_worker, daemon=True, name="covers").start()
        self.connect()

    def connect(self):
        self.conn_error = ""
        self.background(self.client.info, lambda _: self._connected(), lambda msg: setattr(self, "conn_error", msg))

    def _connected(self):
        self.connected = True
        self.push(Screen("home", "REMOMENT", ui.ListView(HOME, 6)))

    # ---- background work ----
    def background(self, fn, done, failed=None):
        """Run fn() in a thread; done(result) / failed(message) run back on the main loop."""
        def run():
            try:
                res = fn()
            except NetError as ex:
                msg = f"Could not reach ReMoment ({ex})"
                self.later(lambda: (failed or self.note_error)(msg))
            except (rm.ReMomentError, KeyError, TypeError, ValueError) as ex:
                self.later(lambda: (failed or self.note_error)(f"Unexpected answer from ReMoment ({type(ex).__name__})"))
            else:
                self.later(lambda: done(res))
        threading.Thread(target=run, daemon=True, name="remoment-io").start()

    def note_error(self, msg):
        self.note = msg

    def _cover_worker(self):
        while True:
            key, art = self.cover_q.get()
            try:
                self.covers[key] = images.parse_bmp(self.client.fetch_cover(art, key[1]))
            except (NetError, ImageError, rm.ReMomentError):
                self.covers[key] = None                          # no cover: a placeholder is drawn, and we do not ask again
            self.later(self.refresh)

    def cover_size(self, biggest=False):
        """The largest server cover that fits a ~150 unit slot on this window (covers are drawn in window pixels)."""
        sx = self.window_scale()[0]
        fit = [s for s in rm.COVER_SIZES if s <= (175 if biggest else 150) * sx]
        return fit[-1] if fit else rm.COVER_SIZES[0]

    def cover(self, art, size):
        """(w, h, bgra) once loaded, None if there is none (yet or ever). Starts the download the first time."""
        h = (art or {}).get("hash")
        if not h:
            return None
        key = (h, size)
        if key not in self.covers and key not in self.cover_pending:
            self.cover_pending.add(key)
            self.cover_q.put((key, art))
        return self.covers.get(key)

    def sync_images(self, wanted):
        """wanted: {overlay id: ((hash, size), x, y)} in canvas units. Shows, moves and hides mpv image overlays to match."""
        if not self.mpv:
            return
        sx, sy = self.window_scale()
        for oid in list(self.img_shown):
            if oid not in wanted:
                images.hide(self.mpv, oid)
                del self.img_shown[oid]
        for oid, (key, x, y) in wanted.items():
            img = self.covers.get(key)
            state = (key, round(x * sx), round(y * sy))
            if img is None:
                if oid in self.img_shown:
                    images.hide(self.mpv, oid)
                    del self.img_shown[oid]
            elif self.img_shown.get(oid) != state:
                images.show(self.mpv, oid, state[1], state[2], *img)
                self.img_shown[oid] = state

    # ---- screens ----
    @property
    def cur(self):
        return self.stack[-1] if self.stack else None

    def push(self, screen):
        self.stack.append(screen)
        inputs.TEXT_MODE = screen.kind == "search"
        self.note = ""
        if screen.loader and not screen.done:
            self.load_more(screen)

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop()
            inputs.TEXT_MODE = self.cur.kind == "search"
            self.note = ""
            return True
        return False

    def load_more(self, screen):
        if screen.loading or screen.done:
            return
        screen.loading, screen.error = True, ""
        self.background(lambda: screen.loader(screen.cursor), lambda res: self._page(screen, res), lambda msg: self._page_failed(screen, msg))

    def _page(self, screen, res):
        items, nxt = res if isinstance(res, tuple) else (res, None)
        screen.loading, screen.cursor, screen.done = False, nxt, nxt is None
        if screen.kind == "album":
            return
        screen.view.items += list(items)

    def _page_failed(self, screen, msg):
        screen.loading, screen.error = False, msg

    def albums_screen(self, title, loader):
        cols = max(3, (ui.W - 2 * ui.SX) // 165)
        return Screen("albums", title, ui.GridView([], cols, 2), loader)

    def open_album(self, album_id):
        s = Screen("album", "ALBUM", ui.ListView([], 7), lambda cur: self._load_album(s, album_id))
        s.album = None
        self.push(s)

    def _load_album(self, s, album_id):
        a = self.client.album(album_id)
        entries = [rm.track_entry(t, a) for t in a.get("tracks", [])]
        def apply():
            s.album, s.title = a, (a.get("name") or "ALBUM").upper()
            s.tracks = entries
            s.view.items = [{"kind": "all"}] + [{"kind": "track", **e} for e in entries]
        self.later(apply)
        return [], None

    def open_artist(self, artist_id):
        s = self.albums_screen("ARTIST", lambda cur: self._load_artist(s, artist_id))
        self.push(s)

    def _load_artist(self, s, artist_id):
        a = self.client.artist(artist_id)
        self.later(lambda: setattr(s, "title", (a.get("name") or "ARTIST").upper()))
        return a.get("albums", []), None

    def open_playlist(self, pl):
        def load(cur):
            d = self.client.playlist(pl["id"])
            ents = [rm.track_entry(t) for t in d.get("tracks", [])]
            return [{"kind": "track", **e, "info": f"{e['artist']}   {fmt_time(e['dur'])}"} for e in ents if e["playable"]], None
        self.push(Screen("playlist", pl.get("name", "PLAYLIST").upper(), ui.ListView([], 7), load))

    def open_results(self, title, data):
        items = [{"kind": "artist", "title": a["name"], "info": f"{a.get('album_count', 0)} albums", "id": a["id"]} for a in data.get("artists", [])]
        items += [{"kind": "album", "title": a["name"], "info": f"album  {a.get('artist_name', '')}", "id": a["id"]} for a in data.get("albums", [])]
        items += [{"kind": "track", **rm.track_entry(t), "info": f"{t.get('artist_name', '')}  -  {t.get('album_name', '')}"} for t in data.get("tracks", [])]
        s = Screen("results", title, ui.ListView(items, 7))
        s.done = True
        self.push(s)

    def do_search(self):
        text = self.cur.keyboard.text
        if len(text.strip()) < 2:
            self.note = "Type at least two letters"
            return
        self.note = "Searching..."
        self.background(lambda: self.client.search(text), lambda d: (self.open_results("SEARCH: " + text.upper(), d), setattr(self, "note", "")))

    def open_home_item(self, key):
        c = self.client
        if key == "albums":
            self.push(self.albums_screen("ALBUMS", lambda cur: c.albums(cur)))
        elif key == "artists":
            self.push(Screen("artists", "ARTISTS", ui.ListView([], 7), lambda cur: c.artists(cur)))
        elif key == "playlists":
            self.push(Screen("playlists", "PLAYLISTS", ui.ListView([], 7), lambda cur: c.playlists(cur)))
        elif key == "recent":
            self.push(self.albums_screen("RECENTLY PLAYED", lambda cur: (c.recent(), None)))
        elif key == "favourites":
            s = Screen("results", "FAVOURITES", ui.ListView([], 7), lambda cur: self._favs(s))
            self.push(s)
        elif key == "search":
            self.push(Screen("search", "SEARCH", keyboard=ui.Keyboard(self.search_text), done=True))
        elif key == "now":
            self.now_open = True

    def _favs(self, s):
        d = self.client.favorites()
        items = [{"kind": "artist", "title": a["name"], "info": f"{a.get('album_count', 0)} albums", "id": a["id"]} for a in d.get("artists", [])]
        items += [{"kind": "album", "title": a["name"], "info": f"album  {a.get('artist_name', '')}", "id": a["id"]} for a in d.get("albums", [])]
        items += [{"kind": "track", **rm.track_entry(t), "info": f"{t.get('artist_name', '')}  -  {t.get('album_name', '')}"} for t in d.get("tracks", [])]
        return items, None

    # ---- playing ----
    def play_queue(self, entries, index):
        """entries: track dicts as rm.track_entry makes them; plays entries[index] and goes on to the following ones."""
        picked = entries[index]
        self.queue = [e for e in entries if e.get("playable", True)]
        self.qi = next((i for i, e in enumerate(self.queue) if e["id"] == picked["id"]), 0) if self.queue else -1
        self.fail_run = 0
        if self.queue:
            self.play_current()

    def play_current(self):
        t = self.queue[self.qi]
        self.pb.start(self.client.stream_url(t["id"]), t["title"], audio_only=True, resume=False)
        self.note = ""

    def step_track(self, delta):
        if not self.queue:
            return
        i = self.qi + delta
        if 0 <= i < len(self.queue):
            self.qi = i
            self.play_current()
        elif delta > 0:                                         # past the last track: the music is over
            self.pb.stop()
            self.now_open = False

    def now_track(self):
        return self.queue[self.qi] if self.pb.active and 0 <= self.qi < len(self.queue) else None

    # ---- the shell's contract ----
    def handle(self, cmd):
        s = self.cur
        if cmd.startswith("_"):
            return
        if not self.connected:
            if cmd == "enter":
                self.connect()
            elif cmd in ("quit", "back"):
                self.quit()
            return
        if s and s.kind == "search":                            # typing: letters must not trigger the global keys
            if cmd in ("back",):
                self.search_text = s.keyboard.text
                self.pop()
                return
            if cmd == "quit":
                self.quit()
                return
            if s.keyboard.handle(cmd) == "submit":
                self.search_text = s.keyboard.text
                self.do_search()
            return
        if self.now_open:
            if cmd in ("back", "info"):
                self.now_open = False
            elif cmd == "left":
                self.step_track(-1)
            elif cmd == "right":
                self.step_track(1)
            elif not self.pb.handle(cmd):
                super().handle(cmd)
            return
        if cmd == "info" and self.pb.active:
            self.now_open = True
            return
        if cmd == "menu" and self.pb.active:
            self.pb.mpv.command("cycle", "pause")
            return
        if cmd == "last" and self.pb.active:
            self.step_track(1)
            return
        if s is None:
            return super().handle(cmd)
        self.note = ""
        if s.view.handle(cmd):
            if s.loader and not s.done and s.view.sel >= len(s.view.items) - 4:          # near the end of what is loaded: fetch the next page
                self.load_more(s)
            return
        if cmd == "enter":
            self.activate(s)
        elif cmd == "back":
            if not self.pop():
                self.quit()
        else:
            super().handle(cmd)

    def activate(self, s):
        item = s.view.current()
        if s.error and not s.view.items:
            s.done = False
            self.load_more(s)
            return
        if item is None:
            return
        if s.kind == "home":
            self.open_home_item(item[0])
        elif s.kind == "albums":
            self.open_album(item["id"])
        elif s.kind == "artists":
            self.open_artist(item["id"])
        elif s.kind == "playlists":
            self.open_playlist(item)
        elif s.kind == "album":
            if getattr(s, "album", None) and "files" not in (s.album.get("sources") or ["files"]):
                self.note = "Not in your own files: it can only play on the server's speakers"
            elif item["kind"] == "all":
                self.play_queue(s.tracks, 0)
            else:
                self.play_queue(s.tracks, next(i for i, t in enumerate(s.tracks) if t["id"] == item["id"]))
        elif s.kind in ("playlist", "results"):
            if item["kind"] == "track":
                tracks = [x for x in s.view.items if x["kind"] == "track"]
                self.play_queue(tracks, tracks.index(item))
            elif item["kind"] == "album":
                self.open_album(item["id"])
            elif item["kind"] == "artist":
                self.open_artist(item["id"])

    def on_mpv(self, ev):
        res = self.pb.on_mpv(ev)
        if res == "ended":
            self.fail_run = 0
            self.step_track(1)
        elif res == "error":
            self.fail_run += 1
            t = self.queue[self.qi]["title"] if 0 <= self.qi < len(self.queue) else "track"
            self.note = f"Could not play {t}"
            if self.fail_run < 3 and self.qi + 1 < len(self.queue):
                self.step_track(1)
            else:
                self.now_open = False

    def tick(self, now):
        if self.pb.mpv is not self.mpv and self.mpv:
            self.pb.rebind(self.mpv)
            self.img_shown.clear()
        self.pb.tick(now)

    # ---- drawing ----
    def draw(self, now):
        wanted = {}
        out = [ui.background()]
        s = self.cur
        if not self.connected:
            msg = self.conn_error or "Connecting to " + self.client.host
            out += ui.header("REMOMENT") + [ui.text(ui.W // 2, ui.H // 2 - 20, ui.esc(msg)[:70], 26, ui.WARN if self.conn_error else ui.WHITE, an=5),
                                              ui.text(ui.W // 2, ui.H // 2 + 30, 'Set "remoment.host" in config.json', 18, ui.GREY, an=5, bold=False)]
            out += ui.footer("ENTER retry   Q back")
        elif self.now_open:
            out += self.draw_now(now, wanted)
        elif s.kind == "home":
            rows = ui.list_rows(s.view, lambda e: e[1], lambda e: e[2], empty="")
            out += ui.header(self.client.name.upper()) + rows + self.strip(now) + ui.footer("UP/DOWN pick   ENTER open   Q leave", self.note)
        elif s.kind == "albums":
            out += self.draw_grid(s, wanted) + self.strip(now) + ui.footer("ARROWS pick   ENTER open   ESC back   Q leave", s.error or self.note)
        elif s.kind == "album":
            out += self.draw_album(s, wanted) + self.strip(now) + ui.footer("ENTER play   ESC back   I now playing   Q leave", s.error or self.note)
        elif s.kind == "search":
            out += ui.header("SEARCH") + s.keyboard.draw() + ui.footer("ARROWS + ENTER type, or use a keyboard   ESC back", self.note)
        else:
            lab = (lambda e: e["name"]) if s.kind in ("artists", "playlists") else (lambda e: e["title"])
            det = (lambda e: f"{e.get('album_count', e.get('track_count', 0))} " + ("albums" if s.kind == "artists" else "tracks")) if s.kind in ("artists", "playlists") \
                else (lambda e: e.get("info") or (fmt_time(e.get("dur")) if e["kind"] == "track" else ""))
            empty = "Loading..." if s.loading else s.error or "Nothing here"
            out += ui.header(s.title[:30]) + ui.list_rows(s.view, lab, det, empty=empty) + self.strip(now)
            out += ui.footer("ENTER open / play   ESC back   I now playing   Q leave", self.note or s.error)
        self.sync_images(wanted)
        return "\n".join(out)

    def strip(self, now):
        """One line at the bottom while something plays: what, and how far."""
        t = self.now_track()
        if not t:
            return []
        pos = f"{ui.mmss(self.pb.pos or 0)}" + (f" / {ui.mmss(self.pb.dur)}" if self.pb.dur else "")
        return [ui.rect(ui.SX - 6, ui.H - ui.SY - 58, ui.W - ui.SX + 6, ui.H - ui.SY - 32, ui.HILITE, "50"),
                ui.text(ui.SX + 2, ui.H - ui.SY - 55, ui.esc(("PAUSED  " if self.pb.paused else "") + t["title"] + "  -  " + t["artist"])[:60], 18, ui.AMBER, border=1),
                ui.text(ui.W - ui.SX, ui.H - ui.SY - 55, pos, 18, ui.GREY, an=9, border=1)]

    def draw_grid(self, s, wanted):
        out = ui.header(s.title[:30])
        v, size = s.view, self.cover_size()
        sx = self.window_scale()[0]
        cell_w = (ui.W - 2 * ui.SX) // v.cols
        gx0, gy0 = ui.SX + ((ui.W - 2 * ui.SX) - cell_w * v.cols) // 2, ui.SY + 56
        if not v.items:
            out.append(ui.text(ui.W // 2, ui.H // 2, "Loading..." if s.loading else ui.esc(s.error or "Nothing here"), 24, ui.GREY, an=5, bold=False))
        for n, i in enumerate(v.visible()):
            a = v.items[i]
            cx, cy = gx0 + (n % v.cols) * cell_w, gy0 + (n // v.cols) * CELL_H
            on = i == v.sel
            art = a.get("artwork")
            img = self.cover(art, size)
            cwid = (img[0] / sx) if img else size / sx
            out.append(ui.rect(cx + 2, cy, cx + cell_w - 2, cy + CELL_H - 6, ui.HILITE if on else "&H281810&", "40" if on else "30"))
            if on:
                out += [ui.rect(cx + 2, cy, cx + cell_w - 2, cy + 3, ui.AMBER), ui.rect(cx + 2, cy + CELL_H - 9, cx + cell_w - 2, cy + CELL_H - 6, ui.AMBER),
                        ui.rect(cx + 2, cy, cx + 5, cy + CELL_H - 6, ui.AMBER), ui.rect(cx + cell_w - 5, cy, cx + cell_w - 2, cy + CELL_H - 6, ui.AMBER)]
            px, py = cx + (cell_w - cwid) / 2, cy + 8
            if img:
                wanted[IMG_GRID + n] = ((art["hash"], size), px, py)
            else:
                out += ui.icon(int(cx + cell_w / 2 - 40), cy + 28, "music", 5)
            ly = cy + 8 + cwid + 6
            out.append(ui.text(cx + cell_w // 2, ly, ui.esc(a.get("name", ""))[:19], 16, ui.AMBER if on else ui.WHITE, an=8, border=1))
            sub = a.get("artist_name") or ""
            out.append(ui.text(cx + cell_w // 2, ly + 22, ui.esc(sub)[:22], 13, ui.GREY, an=8, bold=False, border=1))
        if s.loading and v.items:
            out.append(ui.text(ui.W - ui.SX, ui.H - ui.SY - 28, "loading...", 16, ui.DIM, an=9, border=0))
        return out

    def draw_album(self, s, wanted):
        a = getattr(s, "album", None)
        out = ui.header(s.title[:30])
        size, sx = self.cover_size(True), self.window_scale()[0]
        art = a.get("artwork") if a else None
        img = self.cover(art, size)
        cwid = (img[0] / sx) if img else size / sx
        x = ui.SX
        out.append(ui.rect(x - 3, ui.SY + 53, x + cwid + 3, ui.SY + 56 + cwid + 3, ui.AMBER))        # a thin frame, visible round the cover
        if img:
            wanted[IMG_ONE] = ((art["hash"], size), x, ui.SY + 56)
        else:
            out += ui.icon(int(x + cwid / 2 - 40), ui.SY + 90, "music", 5)
        if a:
            ty = ui.SY + 56 + cwid + 14
            out.append(ui.text(x, ty, ui.esc((a.get("artist") or {}).get("name", ""))[:24], 18, ui.AMBER, border=1))
            out.append(ui.text(x, ty + 26, ui.esc(f"{a.get('year') or ''}   {len(getattr(s, 'tracks', []))} tracks"), 15, ui.GREY, bold=False, border=1))
        list_x = int(x + cwid + 28)
        out += ui.list_rows(s.view, lambda e: "PLAY ALBUM" if e["kind"] == "all" else e["title"],
                            lambda e: "" if e["kind"] == "all" else fmt_time(e.get("dur")), x0=list_x,
                            empty="Loading..." if s.loading else s.error or "No tracks")
        return out

    def draw_now(self, now, wanted):
        t = self.now_track()
        out = ui.header("NOW PLAYING")
        if not t:
            return out + [ui.text(ui.W // 2, ui.H // 2, "Nothing playing", 26, ui.GREY, an=5, bold=False)] + ui.footer("ESC back")
        size, sx = self.cover_size(True), self.window_scale()[0]
        img = self.cover(t.get("art"), size)
        cwid = (img[0] / sx) if img else size / sx
        accent = ui.ass_colour(((t.get("art") or {}).get("safe_colors") or [None])[0])
        x, y = ui.SX, ui.SY + 70
        out.append(ui.rect(x - 4, y - 4, x + cwid + 4, y + cwid + 4, accent))
        if img:
            wanted[IMG_ONE] = ((t["art"]["hash"], size), x, y)
        else:
            out += ui.icon(int(x + cwid / 2 - 40), int(y + cwid / 2 - 40), "music", 5)
        tx = int(x + cwid + 34)
        out += [ui.text(tx, y + 6, ui.esc(t["title"])[:34], 30, border=2),
                ui.text(tx, y + 54, ui.esc(t["artist"])[:36], 24, accent, border=1),
                ui.text(tx, y + 88, ui.esc(t["album"])[:40], 18, ui.GREY, bold=False, border=1),
                ui.text(tx, y + 124, f"track {self.qi + 1} of {len(self.queue)}", 15, ui.DIM, bold=False, border=0)]
        if self.qi + 1 < len(self.queue):
            n = self.queue[self.qi + 1]
            out.append(ui.text(tx, y + 150, ui.esc("next: " + n["title"])[:44], 15, ui.DIM, bold=False, border=0))
        by = ui.H - ui.SY - 92
        frac = (self.pb.pos / self.pb.dur) if self.pb.pos is not None and self.pb.dur else 0
        out += ui.progress(ui.SX, ui.W - ui.SX, by, frac, accent)
        out += [ui.text(ui.SX, by + 14, ui.mmss(self.pb.pos or 0), 18, ui.GREY, border=1),
                ui.text(ui.W - ui.SX, by + 14, ("-" + ui.mmss(self.pb.dur - (self.pb.pos or 0))) if self.pb.dur else "", 18, ui.GREY, an=9, border=1)]
        if self.pb.paused:
            out.append(ui.text(ui.W // 2, by + 12, "PAUSED", 22, accent, an=8, border=1))
        return out + ui.footer("LEFT/RIGHT track   UP/DOWN seek   ENTER pause   ESC back", self.note)


if __name__ == "__main__":
    run_app(ReMoment)
