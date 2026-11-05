#!/usr/bin/env python3
"""Jellyfin: browse your Jellyfin movies and shows with posters, resume where you stopped, and play them here through mpv.
Server address: "jellyfin.host" in config.json. The first start asks for your name and password on screen; only the login token is kept
(~/.config/tv-launcher/jellyfin.json, mode 0600)."""
import os
import queue
import sys
import threading
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))    # the repo root, for tvkit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tvkit import images, inputs, net, ui
from tvkit.images import ImageError
from tvkit.net import NetError
from tvkit.player import Playback
from tvkit.shell import Shell, run_app

import jellyfin_client as jf

VIDEO_VIEWS = ("movies", "tvshows", "homevideos", "boxsets", "mixed", "", None)
IMG_GRID, IMG_ONE = 10, 40
GRID_ROWS, CELL_H, POSTER_H = 2, 205, 144
PAGE = 50


class Screen:
    def __init__(self, kind, title, view=None, loader=None, **extra):
        self.kind, self.title, self.view, self.loader = kind, title, view, loader
        self.loading, self.error, self.done, self.start = False, "", False, 0
        self.__dict__.update(extra)


def fmt_runtime(sec):
    m = int(sec // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min" if m else ""


def mark(e):
    if e["played"]:
        return "watched"
    if e["pos"] >= 30:
        return f"resume {ui.mmss(e['pos'])}"
    return ""


class Jellyfin(Shell):
    name = "jellyfin"

    # ---- start / session ----
    def start(self):
        c = self.cfg.get("jellyfin", {})
        self.session_path = os.path.expanduser(c.get("token_file", "~/.config/tv-launcher/jellyfin.json"))
        saved = net.read_json(self.session_path, {}) or {}
        host = c.get("host") or saved.get("host") or "http://jellyfin.local:8096"
        self.client = jf.Client(host, saved.get("device_id") or uuid.uuid4().hex)
        self.pb = Playback(self.mpv, None)
        self.stack, self.note, self.state, self.error = [], "", "connecting", ""
        self.covers, self.cover_q, self.cover_pending, self.img_shown = {}, queue.Queue(), set(), {}
        self.playing = None              # {"entry", "list", "index", "method", "last_report"}
        self.user_text = saved.get("user_name", "")
        self.qc_ok, self.prefer_password = False, False
        threading.Thread(target=self._cover_worker, daemon=True, name="posters").start()
        self.begin(saved if saved.get("host") == self.client.host and saved.get("token") else None)

    def begin(self, saved):
        self.state, self.error = "connecting", ""
        def job():
            self.client.public_info()
            if saved:
                self.client.use(saved)
                if not self.client.check():
                    self.qc_ok = self.client.quick_enabled()
                    return "login"
                return "home"
            self.qc_ok = self.client.quick_enabled()
            return "login"
        self.background(job, self._after_connect, self._connect_failed)

    def _after_connect(self, what):
        self.state = what
        self.stack = []
        if what == "home":
            self.save_session()
            self.push(self.home_screen())
        else:
            self.push_login()

    def _connect_failed(self, msg):
        self.state, self.error = "error", msg

    def save_session(self):
        net.write_private(self.session_path, self.client.session())

    def push_login(self, error=""):
        self.stack = []
        if self.qc_ok and not self.prefer_password:
            self.note = error
            self.push(Screen("quick", "SIGN IN", code="", secret=None, asked=0, polled=0, done=True))
            self.state = "login"
            self.quick_begin(self.cur)
            return
        s = Screen("login", "SIGN IN", step=0, keyboard=ui.Keyboard(self.user_text, full=True, submit_label="NEXT"), done=True)
        self.note = error
        self.push(s)
        self.state = "login"

    def quick_begin(self, s):
        s.code, s.secret, s.asked, s.polled, s.busy = "", None, __import__("time").time(), 0, True
        def got(res):
            s.secret, s.code, s.busy = res[1], res[0], False
            self.note = ""
        def failed(msg):
            s.busy = False
            self.prefer_password = True
            self.push_login(msg + " - use your password instead")
        self.background(lambda: (lambda sc: (sc[1], sc[0]))(self.client.quick_start()), got, failed)

    def quick_tick(self, s, now):
        if s.busy:
            return
        if s.secret and now - s.asked > 280:                      # codes expire after a few minutes: ask for a new one
            self.quick_begin(s)
            return
        if s.secret and now - s.polled >= 3:
            s.polled, secret = now, s.secret
            s.busy = True
            def done(ok):
                s.busy = False
                if ok and self.cur is s:
                    s.busy = True
                    self.background(lambda: self.client.quick_login(secret), self._logged_in, lambda m: (setattr(s, "busy", False), self.note_error(m)))
            self.background(lambda: self.client.quick_approved(secret), done, lambda m: (setattr(s, "busy", False), self.note_error(m)))

    def login_step(self, s):
        text = s.keyboard.text
        if s.step == 0:
            if not text.strip():
                self.note = "Type your Jellyfin name"
                return
            self.user_text = text.strip()
            s.step, s.keyboard = 1, ui.Keyboard("", full=True, secret=True, submit_label="LOGIN")
            self.note = ""
            return
        user, password = self.user_text, text
        self.note = "Signing in..."
        self.background(lambda: self.client.login(user, password), self._logged_in, self._login_failed)

    def _logged_in(self, _):
        self.save_session()
        self.state, self.note = "home", ""
        self.stack = []
        self.push(self.home_screen())

    def _login_failed(self, msg):
        self.push_login(msg)

    def logout(self):
        try:
            os.remove(self.session_path)
        except OSError:
            pass
        self.client.token = None
        self.push_login()

    # ---- background work ----
    def background(self, fn, done, failed=None):
        def run():
            try:
                res = fn()
            except NetError as ex:
                msg = f"Could not reach Jellyfin ({ex})"
                self.later(lambda: (failed or self.note_error)(msg))
            except jf.JellyfinError as ex:
                msg = str(ex)
                self.later(lambda: (failed or self.note_error)(msg))
            except (KeyError, TypeError, ValueError) as ex:
                msg = f"Unexpected answer from Jellyfin ({type(ex).__name__})"
                self.later(lambda: (failed or self.note_error)(msg))
            else:
                self.later(lambda: done(res))
        threading.Thread(target=run, daemon=True, name="jellyfin-io").start()

    def note_error(self, msg):
        self.note = msg

    def _cover_worker(self):
        while True:
            key = self.cover_q.get()
            try:
                self.covers[key] = images.parse_image(self.client.poster(key[0], key[1]))
            except (NetError, ImageError, jf.JellyfinError):
                self.covers[key] = None
            self.later(self.refresh)

    def poster_height(self, canvas_h):
        """Pixels to ask for so the poster fits `canvas_h` canvas units without overshooting: rounded down to a multiple of 8 (a few distinct sizes only)."""
        sy = self.window_scale()[1]
        return max(64, int(canvas_h * sy) // 8 * 8)

    def poster(self, pic, height):
        if not pic:
            return None
        key = (tuple(pic), height)
        if key not in self.covers and key not in self.cover_pending:
            self.cover_pending.add(key)
            self.cover_q.put(key)
        return self.covers.get(key)

    def sync_images(self, wanted):
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
        inputs.TEXT_MODE = screen.kind in ("login", "search")
        self.note = "" if screen.kind != "login" else self.note
        if screen.loader and not screen.done:
            self.load_more(screen)

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop()
            inputs.TEXT_MODE = self.cur.kind in ("login", "search")
            self.note = ""
            return True
        return False

    def load_more(self, screen):
        if screen.loading or screen.done:
            return
        screen.loading, screen.error = True, ""
        start = screen.start
        self.background(lambda: screen.loader(start), lambda res: self._page(screen, res), lambda msg: self._page_failed(screen, msg))

    def _page(self, screen, res):
        items, total = res
        screen.loading = False
        screen.view.items += list(items)
        screen.start += len(items)
        screen.done = total is None or screen.start >= total or not items

    def _page_failed(self, screen, msg):
        screen.loading, screen.error = False, msg

    def grid_screen(self, title, loader):
        cols = max(4, (ui.W - 2 * ui.SX) // 132)
        return Screen("grid", title, ui.GridView([], cols, GRID_ROWS), loader)

    def list_screen(self, title, loader, kind="list"):
        return Screen(kind, title, ui.ListView([], 7), loader)

    def home_screen(self):
        c = self.client
        rows = [("resume", "CONTINUE WATCHING", "Pick up where you stopped"), ("nextup", "NEXT UP", "The next episode of shows you watch")]
        try:
            views = self.views
        except AttributeError:
            views = []
        for v in views:
            rows.append(("view", v["title"].upper(), {"movies": "Movies", "tvshows": "Shows"}.get(v["collection"], "Library"), v))
        rows += [("search", "SEARCH", "Find a film or a show"), ("logout", "SIGN OUT", f"Signed in as {c.user_name or '?'}")]
        s = Screen("home", (c.user_name and "JELLYFIN") or "JELLYFIN", ui.ListView(rows, 7), done=True)
        s.refresh_views = True
        self.background(c.views, lambda v: self._views_loaded(s, v))
        return s

    def _views_loaded(self, s, views):
        self.views = [v for v in views if v["collection"] in VIDEO_VIEWS]
        base = [r for r in s.view.items if r[0] != "view"]
        rows = base[:2] + [("view", v["title"].upper(), {"movies": "Movies", "tvshows": "Shows"}.get(v["collection"], "Library"), v) for v in self.views] + base[2:]
        sel = s.view.current()
        s.view.items = rows
        if sel in rows:
            s.view.sel = rows.index(sel)

    def open_home_item(self, row):
        c, key = self.client, row[0]
        if key == "resume":
            self.push(self.grid_screen("CONTINUE WATCHING", lambda st: (c.resume(), None)))
        elif key == "nextup":
            self.push(self.grid_screen("NEXT UP", lambda st: (c.next_up(), None)))
        elif key == "view":
            self.open_parent(row[3])
        elif key == "search":
            self.push(Screen("search", "SEARCH", keyboard=ui.Keyboard("", submit_label="SEARCH"), done=True))
        elif key == "logout":
            self.logout()

    def open_parent(self, e):
        c = self.client
        self.push(self.grid_screen(e["title"].upper()[:28], lambda st: c.items(e["id"], st, PAGE)))

    def open_entry(self, e, siblings=None):
        """What Enter does on a poster or a list row."""
        c = self.client
        if e["type"] == "Series":
            self.push(self.list_screen(e["title"].upper()[:28], lambda st: (c.seasons(e["id"]), None), "seasons"))
        elif e["type"] == "Season":
            sid = e["series_id"]
            s = self.list_screen(e["title"].upper()[:28], lambda st: (c.episodes(sid, e["id"]), None), "episodes")
            self.push(s)
        elif jf.playable(e):
            self.push(Screen("detail", e["title"].upper()[:28], entry=e, siblings=siblings or [e], done=True,
                             view=ui.ListView(self.detail_rows(e), 3)))
        else:                                                      # a collection or folder: its contents as a poster grid
            self.push(self.grid_screen(e["title"].upper()[:28], lambda st: c.items(e["id"], st, PAGE)))

    def detail_rows(self, e):
        rows = []
        if e["pos"] >= 30 and not e["played"]:
            rows.append(("resume", f"RESUME FROM {ui.mmss(e['pos'])}"))
            rows.append(("start", "PLAY FROM THE START"))
        else:
            rows.append(("start", "PLAY AGAIN" if e["played"] else "PLAY"))
        return rows

    def do_search(self, text):
        if len(text.strip()) < 2:
            self.note = "Type at least two letters"
            return
        self.note = "Searching..."
        c = self.client
        def done(items):
            self.note = ""
            s = self.grid_screen("SEARCH: " + text.upper()[:20], lambda st: (items, None))
            self.push(s)
        self.background(lambda: c.search(text.strip()), done)

    # ---- playing ----
    def play(self, e, start_at, siblings=None):
        sibs = siblings or [e]
        idx = next((i for i, x in enumerate(sibs) if x["id"] == e["id"]), 0)
        self.playing = {"entry": e, "list": sibs, "index": idx, "method": "DirectStream", "last_report": 0, "fell_back": False}
        self.begin_playback(start_at)

    def begin_playback(self, start_at, transcode=False):
        p, e = self.playing, self.playing["entry"]
        self.mpv.command("set", "http-header-fields", self.client.header_option())
        url = self.client.transcode_url(e["id"]) if transcode else self.client.direct_url(e["id"])
        p["method"] = "Transcode" if transcode else "DirectStream"
        p["start_at"] = start_at
        self.pb.start(url, e["title"] if e["type"] != "Episode" else f"{e['sub']}  {e['title']}", audio_only=False, start_at=start_at)
        c, pos, method = self.client, start_at or 0, p["method"]
        threading.Thread(target=lambda: c.report_start(e["id"], pos, method), daemon=True).start()
        p["last_report"] = 0
        self.note = ""

    def finish_playback(self, ended=False):
        p = self.playing
        if not p:
            return
        e = p["entry"]
        pos = (e["runtime"] if ended and e["runtime"] else self.pb.pos) or 0
        self.pb.stop()
        c = self.client
        threading.Thread(target=lambda: c.report_stop(e["id"], pos), daemon=True).start()
        e["pos"], e["played"] = (0, True) if ended else (pos, e["played"])
        self.playing = None

    def next_in_list(self):
        p = self.playing
        i = p["index"] + 1
        while i < len(p["list"]) and not jf.playable(p["list"][i]):
            i += 1
        return p["list"][i] if i < len(p["list"]) else None

    # ---- the shell's contract ----
    def handle(self, cmd):
        s = self.cur
        if self.state in ("connecting", "error"):
            if cmd == "enter" and self.state == "error":
                self.begin(net.read_json(self.session_path) or None)
            elif cmd in ("quit", "back"):
                self.quit()
            return
        if self.playing:
            if cmd in ("back", "quit"):
                self.finish_playback()
                self.refresh_detail()
            elif cmd == "last":
                nxt = self.next_in_list()
                if nxt:
                    sibs = self.playing["list"]
                    self.finish_playback()
                    self.play(nxt, 0, sibs)
            elif not self.pb.handle(cmd):
                super().handle(cmd)
            return
        if s is None:
            return super().handle(cmd)
        if s.kind == "quick":
            if cmd == "enter":
                self.prefer_password = True
                self.push_login()
            elif cmd in ("quit", "back"):
                self.quit()
            return
        if s.kind in ("login", "search"):
            if cmd == "back":
                if s.kind == "login" and s.step == 1:
                    s.step, s.keyboard = 0, ui.Keyboard(self.user_text, full=True, submit_label="NEXT")
                    self.note = ""
                elif s.kind == "login" and self.qc_ok:
                    self.prefer_password = False
                    self.push_login()
                elif not self.pop():
                    self.quit()
                return
            if cmd == "quit":
                self.quit()
                return
            if s.keyboard.handle(cmd) == "submit":
                self.login_step(s) if s.kind == "login" else self.do_search(s.keyboard.text)
            return
        self.note = ""
        if s.view.handle(cmd):
            if s.loader and not s.done and s.view.sel >= len(s.view.items) - 6:
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
        if s.error and not s.view.items:
            s.done = False
            self.load_more(s)
            return
        item = s.view.current()
        if item is None:
            return
        if s.kind == "home":
            self.open_home_item(item)
        elif s.kind == "grid":
            self.open_entry(item, [x for x in s.view.items if jf.playable(x)])
        elif s.kind in ("seasons", "episodes"):
            self.open_entry(item, s.view.items if s.kind == "episodes" else None)
        elif s.kind == "detail":
            e = s.entry
            self.play(e, item[0] == "resume" and e["pos"] or 0, s.siblings)

    def refresh_detail(self):
        s = self.cur
        if s and s.kind == "detail":
            s.view.items = self.detail_rows(s.entry)
            s.view.sel = 0

    def on_mpv(self, ev):
        res = self.pb.on_mpv(ev)
        p = self.playing
        if not p:
            return
        if res == "ended":
            nxt = self.next_in_list() if p["entry"]["type"] == "Episode" else None
            sibs = p["list"]
            self.finish_playback(ended=True)
            if nxt:
                self.play(nxt, 0, sibs)
            else:
                self.refresh_detail()
        elif res == "error":
            if p["method"] == "DirectStream" and not p["fell_back"]:
                p["fell_back"] = True
                self.begin_playback(self.pb.pos or p.get("start_at") or 0, transcode=True)
                self.pb.say("TRYING TRANSCODE", 4)
            else:
                title = p["entry"]["title"]
                self.finish_playback()
                self.refresh_detail()
                self.note = f"Could not play {title}"

    def tick(self, now):
        if self.pb.mpv is not self.mpv and self.mpv:
            self.pb.rebind(self.mpv)
            self.img_shown.clear()
            self.playing = None
        self.pb.tick(now)
        if self.cur is not None and self.cur.kind == "quick":
            self.quick_tick(self.cur, now)
        p = self.playing
        if p and self.pb.active and self.pb.pos is not None and now - p["last_report"] >= 10:
            p["last_report"] = now
            c, e, pos, paused, method = self.client, p["entry"], self.pb.pos, self.pb.paused, p["method"]
            threading.Thread(target=lambda: c.report_progress(e["id"], pos, paused, method), daemon=True).start()

    # ---- drawing ----
    def draw(self, now):
        wanted = {}
        out = [ui.background()]
        s = self.cur
        if self.playing:
            return "\n".join(self.pb.osd(now))
        if self.state in ("connecting", "error"):
            msg = self.error or "Connecting to " + self.client.host
            out += ui.header("JELLYFIN") + [ui.text(ui.W // 2, ui.H // 2 - 20, ui.esc(msg)[:70], 26, ui.WARN if self.error else ui.WHITE, an=5),
                                              ui.text(ui.W // 2, ui.H // 2 + 30, 'Set "jellyfin.host" in config.json', 18, ui.GREY, an=5, bold=False)]
            out += ui.footer("ENTER retry   Q back")
        elif s.kind == "quick":
            code = s.code or "......"
            out += ui.header("SIGN IN") + [
                ui.text(ui.W // 2, ui.SY + 70, "QUICK CONNECT", 22, ui.AMBER, an=8, border=1),
                ui.text(ui.W // 2, ui.SY + 140, " ".join(code), 96, an=8, border=3),
                ui.text(ui.W // 2, ui.SY + 270, "On your phone or computer open Jellyfin,", 20, ui.GREY, an=8, bold=False, border=1),
                ui.text(ui.W // 2, ui.SY + 298, "go to Settings > Quick Connect and type this code.", 20, ui.GREY, an=8, bold=False, border=1),
                ui.text(ui.W // 2, ui.SY + 350, "waiting..." if s.code else "asking the server for a code...", 18, ui.DIM, an=8, bold=False, border=0)]
            out += ui.footer("ENTER sign in with a password instead   Q back", self.note)
        elif s.kind == "login":
            who = "Name" if s.step == 0 else f"Password for {self.user_text}"
            out += ui.header("SIGN IN  -  " + who.upper()[:30]) + s.keyboard.draw(ui.SY + 96) + ui.footer("ARROWS + ENTER type, or use a keyboard   ESC back", self.note)
        elif s.kind == "search":
            out += ui.header("SEARCH") + s.keyboard.draw() + ui.footer("ARROWS + ENTER type, or use a keyboard   ESC back", self.note)
        elif s.kind == "home":
            rows = ui.list_rows(s.view, lambda r: r[1], lambda r: r[2], empty="")
            out += ui.header("JELLYFIN") + rows + ui.footer("UP/DOWN pick   ENTER open   Q leave", self.note)
        elif s.kind == "grid":
            out += self.draw_grid(s, wanted) + ui.footer("ARROWS pick   ENTER open   ESC back   Q leave", s.error or self.note)
        elif s.kind == "detail":
            out += self.draw_detail(s, wanted) + ui.footer("ENTER play   ESC back   Q leave", self.note)
        else:                                                      # seasons / episodes
            lab = (lambda e: e["title"]) if s.kind == "seasons" else (lambda e: f"{e['sub'].split(' ')[0]}  {e['title']}" if e["sub"] else e["title"])
            det = (lambda e: (f"{e['children']} episodes" if e["children"] else "")) if s.kind == "seasons" else (lambda e: "  ".join(x for x in (fmt_runtime(e["runtime"]), mark(e)) if x))
            empty = "Loading..." if s.loading else s.error or "Nothing here"
            out += ui.header(s.title) + ui.list_rows(s.view, lab, det, empty=empty) + ui.footer("ENTER open / play   ESC back   Q leave", self.note or s.error)
        self.sync_images(wanted)
        return "\n".join(out)

    def draw_grid(self, s, wanted):
        out = ui.header(s.title)
        v, h = s.view, self.poster_height(POSTER_H)
        sx = self.window_scale()[0]
        cell_w = (ui.W - 2 * ui.SX) // v.cols
        gx0, gy0 = ui.SX + ((ui.W - 2 * ui.SX) - cell_w * v.cols) // 2, ui.SY + 52
        if not v.items:
            out.append(ui.text(ui.W // 2, ui.H // 2, "Loading..." if s.loading else ui.esc(s.error or "Nothing here"), 24, ui.GREY, an=5, bold=False))
        for n, i in enumerate(v.visible()):
            e = v.items[i]
            cx, cy = gx0 + (n % v.cols) * cell_w, gy0 + (n // v.cols) * CELL_H
            on = i == v.sel
            img = self.poster(e["pic"], h)
            pw = (img[0] / sx) if img else POSTER_H * 2 / 3
            out.append(ui.rect(cx + 2, cy, cx + cell_w - 2, cy + CELL_H - 6, ui.HILITE if on else "&H281810&", "40" if on else "30"))
            if on:
                out += [ui.rect(cx + 2, cy, cx + cell_w - 2, cy + 3, ui.AMBER), ui.rect(cx + 2, cy + CELL_H - 9, cx + cell_w - 2, cy + CELL_H - 6, ui.AMBER),
                        ui.rect(cx + 2, cy, cx + 5, cy + CELL_H - 6, ui.AMBER), ui.rect(cx + cell_w - 5, cy, cx + cell_w - 2, cy + CELL_H - 6, ui.AMBER)]
            if img:
                wanted[IMG_GRID + n] = (((tuple(e["pic"])), h), cx + (cell_w - pw) / 2, cy + 6)
            else:
                out += ui.icon(int(cx + cell_w / 2 - 36), cy + 30, "tv", 4)
            ly = cy + 6 + POSTER_H + 4
            out.append(ui.text(cx + cell_w // 2, ly, ui.esc(e["title"])[:16], 15, ui.AMBER if on else ui.WHITE, an=8, border=1))
            sub = e["sub"] if e["sub"] else ""
            out.append(ui.text(cx + cell_w // 2, ly + 19, ui.esc(mark(e) or sub)[:20], 12, ui.GREY, an=8, bold=False, border=1))
            if e["pos"] and e["runtime"] and not e["played"]:                 # a thin progress line under the poster
                out += ui.progress(cx + 8, cx + cell_w - 8, cy + CELL_H - 14, e["pos"] / e["runtime"])
        if s.loading and v.items:
            out.append(ui.text(ui.W - ui.SX, ui.H - ui.SY - 28, "loading...", 16, ui.DIM, an=9, border=0))
        return out

    def draw_detail(self, s, wanted):
        e = s.entry
        out = ui.header(s.title)
        h = self.poster_height(240)
        sx = self.window_scale()[0]
        img = self.poster(e["pic"], h)
        sy = self.window_scale()[1]
        pw, ph = ((img[0] / sx), (img[1] / sy)) if img else (160, 240)
        x, y = ui.SX, ui.SY + 54
        out.append(ui.rect(x - 3, y - 3, x + pw + 3, y + ph + 3, ui.AMBER))
        if img:
            wanted[IMG_ONE] = ((tuple(e["pic"]), h), x, y)
        else:
            out += ui.icon(int(x + pw / 2 - 40), y + 80, "tv", 5)
        tx = int(x + pw + 28)
        width = max(24, (ui.W - tx - ui.SX) // 9)
        meta = "   ".join(str(z) for z in (e["sub"], fmt_runtime(e["runtime"]), e["rating"]) if z)
        out.append(ui.text(tx, y, ui.esc(e["title"])[:width], 26, border=1))
        out.append(ui.text(tx, y + 36, ui.esc(meta)[:width + 10], 16, ui.AMBER, border=1))
        for i, ln in enumerate(ui.wrap(e["overview"] or "No description.", width + 6)[:6]):
            out.append(ui.text(tx, y + 66 + i * 22, ln, 16, ui.GREY, bold=False, border=1))
        out += ui.list_rows(s.view, lambda r: r[1], x0=tx, y0=y + 200, row_h=46)
        return out


if __name__ == "__main__":
    run_app(Jellyfin)
