#!/usr/bin/env python3
"""TV Launcher: the home screen a Raspberry Pi boots into, an icon grid of apps.

    python3 launcher.py                 # show the menu
    python3 launcher.py --list          # which apps it found
    python3 launcher.py --run clock     # start one app now, come back to the menu when it quits
    python3 launcher.py --profile hdmi

Each app is its own program (own process, own repo if you like): the launcher closes its mpv and releases the keyboard, runs the app,
and shows the menu again when it quits (`q`). Apps are listed in launcher.json; one whose folder is missing is left out.
Built on tvkit.Shell, the same base the apps use.
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time

from tvkit import config, ui
from tvkit.shell import Shell, setup_logging

ROOT = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("launcher")

DEFAULT_CONFIG = {
    "title": "TV LAUNCHER",
    "autostart": None,                      # an app id: start it this many seconds after boot unless a key is pressed
    "autostart_seconds": 6,
    "remember_last": True,                  # the menu opens on the app you used last
    "crash_seconds": 10,                    # an app that fails within this time is reported on the menu
    "apps": [
        {"id": "clock", "name": "CLOCK", "desc": "A big clock", "icon": "clock", "dir": "apps/clock", "cmd": ["python3", "app.py", "--profile", "{profile}"]},
        {"id": "media", "name": "MEDIA", "desc": "Videos and music from USB sticks and shares", "icon": "folder", "dir": "apps/media",
         "cmd": ["python3", "app.py", "--profile", "{profile}"]},
        {"id": "jellyfin", "name": "JELLYFIN", "desc": "Movies and shows from your Jellyfin server", "icon": "stream", "dir": "apps/jellyfin",
         "cmd": ["python3", "app.py", "--profile", "{profile}"]},
        {"id": "remoment", "name": "REMOMENT", "desc": "Your ReMoment music library, with covers", "icon": "music", "dir": "apps/remoment",
         "cmd": ["python3", "app.py", "--profile", "{profile}"]},
        {"id": "photos", "name": "PHOTOS", "desc": "Slideshow of a folder or a USB stick", "icon": "photos", "dir": "apps/photos",
         "cmd": ["python3", "app.py", "--profile", "{profile}"]},
        {"id": "news", "name": "NEWS", "desc": "Headlines from your RSS feeds", "icon": "news", "dir": "apps/news", "cmd": ["python3", "app.py", "--profile", "{profile}"]},
        {"id": "info", "name": "THIS PI", "desc": "Address, temperature, memory, disk", "icon": "info", "dir": "apps/info", "cmd": ["python3", "app.py", "--profile", "{profile}"]},
        {"id": "spelunkr", "name": "SPELUNKR TV", "desc": "Cable TV from the Internet Archive", "icon": "tv",
         "dir": "/opt/spelunkr-tv", "cmd": ["python3", "tv.py", "--profile", "{profile}"]},
        {"id": "iptv", "name": "IPTV", "desc": "Live channels from your own subscription", "icon": "stream",
         "dir": "/opt/spelunkr-iptv", "cmd": ["python3", "tv.py", "--profile", "{profile}"]},
    ],
    "system": [
        {"id": "reboot", "name": "RESTART", "desc": "Reboot the Pi", "icon": "restart", "cmd": ["sudo", "-n", "systemctl", "reboot"], "confirm": True},
        {"id": "poweroff", "name": "SHUT DOWN", "desc": "Safe to unplug after the screen goes dark", "icon": "power",
         "cmd": ["sudo", "-n", "systemctl", "poweroff"], "confirm": True},
    ],
}


# ---- configuration ----
def load_launcher_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(path) as f:
            user = json.load(f)
    except FileNotFoundError:
        user = {}
    except ValueError as ex:
        raise SystemExit(f"{path}: not valid JSON ({ex})")
    cfg.update(user)
    return cfg


def app_dir(app):
    d = app.get("dir")
    return os.path.join(ROOT, os.path.expanduser(d)) if d else None       # relative folders are inside this repo


def app_available(app):
    """An app with a `dir` needs that folder (and its script) to exist; one without (a system action) is always there."""
    d = app_dir(app)
    if not d:
        return True
    if not os.path.isdir(d):
        return False
    script = next((a for a in app["cmd"][1:] if a.endswith(".py")), None)
    return script is None or os.path.exists(os.path.join(d, script))


def build_entries(cfg):
    """The menu: installed apps first, then the system actions. Each entry gets a `kind` of "app" or "system"."""
    out = []
    for kind, key in (("app", "apps"), ("system", "system")):
        for e in cfg.get(key, []):
            if e.get("id") and e.get("name") and e.get("cmd") and app_available(e):
                out.append({**e, "kind": kind})
    if not sys.platform.startswith("linux"):
        out = [e for e in out if e["kind"] == "app"]          # no reboot / shutdown buttons on a Mac
    return out


def expand(cmd, profile):
    return [a.replace("{profile}", profile) for a in cmd]


# ---- the menu's own logic (no mpv, no processes: tested on its own) ----
class Menu:
    CONFIRM_S = 6

    def __init__(self, entries, selected=None):
        self.entries = entries
        self.sel = 0
        if selected:
            self.sel = next((i for i, e in enumerate(entries) if e["id"] == selected), 0)
        self.note = ""
        self.title_sub = ""
        self.cols = 1                # tiles per row (set by the drawing code from the screen width)
        self.confirm = None          # (entry id, deadline) while a system action waits for a second Enter

    def current(self):
        return self.entries[self.sel] if self.entries else None

    def handle(self, cmd, now=None):
        """Returns an entry to run, or None. Unknown commands do nothing."""
        now = time.time() if now is None else now
        if not self.entries:
            return None
        if self.confirm and now > self.confirm[1]:
            self.confirm = None
        if cmd in ("up", "down", "left", "right", "wheel_up", "wheel_down"):
            self.sel = self._move(cmd)
            self.confirm, self.note = None, ""
        elif cmd.startswith("digit:"):
            i = int(cmd[6:]) - 1                         # 1..9 pick (and start) an entry, 0 is the tenth
            i = 9 if i < 0 else i
            if i < len(self.entries):
                self.sel = i
                return self._start(now)
        elif cmd in ("enter", "click"):
            return self._start(now)
        elif cmd in ("back", "quit"):
            self.confirm = None
        return None

    def _move(self, cmd):
        n, c, i = len(self.entries), max(1, self.cols), self.sel
        if cmd in ("left", "wheel_up"):
            return (i - 1) % n
        if cmd in ("right", "wheel_down"):
            return (i + 1) % n
        if cmd == "down":
            return i + c if i + c < n else i % c          # past the last row: back to the top of the column
        j = i - c
        if j >= 0:
            return j
        j = i
        while j + c < n:                                    # above the first row: the bottom of the column
            j += c
        return j

    def _start(self, now):
        e = self.current()
        if e.get("confirm"):
            if not self.confirm or self.confirm[0] != e["id"]:
                self.confirm = (e["id"], now + self.CONFIRM_S)      # first press only asks
                return None
            self.confirm = None
        self.note = ""
        return e


def home_screen(menu, now_ms, countdown=None):
    out = [ui.background()] + ui.header(menu.title_sub or "HOME", ui.clock(now_ms))
    tiles, menu.cols = ui.tile_grid(menu.entries, menu.sel, menu.confirm[0] if menu.confirm else None)
    out += tiles
    cur = menu.current()
    if cur:
        asking = menu.confirm and menu.confirm[0] == cur["id"]
        out.append(ui.text(ui.W // 2, ui.H - ui.SY - 70, ui.esc("press ENTER again to confirm" if asking else cur.get("desc", ""))[:70], 20, ui.GREY,
                           an=2, bold=False, border=1))
    out += ui.footer("ARROWS pick   ENTER start   1-9 quick start", countdown or menu.note)
    return "\n".join(out)


# ---- running ----
class Launcher(Shell):
    name = "launcher"

    def __init__(self, cfg, lcfg, store_path, entries):
        super().__init__(cfg)
        self.lcfg, self.entries, self.store_path, self.profile = lcfg, entries, store_path, cfg["profile"]
        self.child = None

    # -- remembered state --
    def last_app(self):
        try:
            with open(self.store_path) as f:
                return json.load(f).get("last")
        except (OSError, ValueError):
            return None

    def remember(self, app_id):
        if not self.lcfg.get("remember_last", True):
            return
        try:
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
            with open(self.store_path, "w") as f:
                json.dump({"last": app_id}, f)
        except OSError:
            pass

    # -- the menu: returns the entry to run, or None when asked to stop --
    def menu_loop(self, note="", first=False):
        menu = Menu(self.entries, self.last_app())
        menu.title_sub, menu.note = self.lcfg.get("title", "TV LAUNCHER"), note
        auto = next((e for e in self.entries if e["id"] == self.lcfg.get("autostart")), None)
        deadline = time.time() + self.lcfg["autostart_seconds"] if first and auto and self.lcfg.get("autostart_seconds", 0) > 0 else None
        self.open_screen()
        drawn = None
        try:
            while not self.stopping:
                ev = self.next_event(0.25)
                if ev and ev[0] == "cmd":
                    deadline = None                                 # any key cancels the autostart
                    chosen = menu.handle(ev[1])
                    if chosen:
                        return chosen
                elif ev and ev[0] == "dead":
                    return None
                countdown = None
                if deadline is not None:
                    left = int(deadline - time.time() + 0.999)
                    if left <= 0:
                        return auto
                    countdown = f"STARTING {auto['name']} IN {left}   ANY KEY FOR THE MENU"
                now = time.time()
                if menu.confirm and now > menu.confirm[1]:
                    menu.confirm = None
                if self.mpv:
                    ass = home_screen(menu, int(now * 1000), countdown)
                    if ass != drawn or self._force:
                        self.mpv.overlay(1, ass)
                        drawn, self._force = ass, False
        finally:
            self.close_screen()
        return None

    # -- running an app --
    def run_app(self, entry):
        """Start the entry as a child process and wait for it. Returns a note for the menu ('' when it ended normally)."""
        cmd = expand(entry["cmd"], self.profile)
        cwd = app_dir(entry)
        if cmd[0] == "python3":
            cmd[0] = sys.executable or "python3"
        log.info("starting %s: %s (in %s)", entry["id"], cmd, cwd)
        t0 = time.time()
        try:
            self.child = subprocess.Popen(cmd, cwd=cwd)
        except OSError as ex:
            log.error("could not start %s: %s", entry["id"], ex)
            return f"{entry['name']}: could not start ({ex.strerror or 'error'})"
        try:
            code = self.child.wait()
        finally:
            self.child = None
        log.info("%s exited with code %s after %.0f s", entry["id"], code, time.time() - t0)
        if self.stopping:
            return ""
        if entry["kind"] == "system":
            return f"{entry['name']} did not work (code {code}); see the log" if code else ""
        if code != 0 and time.time() - t0 < self.lcfg.get("crash_seconds", 10):
            return f"{entry['name']} stopped at once (code {code}); see {self.cfg['cache_dir']}"
        return ""

    def quit(self, code=0):
        super().quit(code)
        child = self.child
        if child and child.poll() is None:
            child.terminate()
        self.q.put(("cmd", "back"))              # wakes the menu loop

    def run(self, only=None):
        import signal
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        note, first = "", True
        while not self.stopping:
            if only:
                entry, only = next((e for e in self.entries if e["id"] == only), None), None
                if entry is None:
                    return 2
            else:
                entry = self.menu_loop(note, first)
            first = False
            if entry is None:
                break
            if entry["kind"] == "app":
                self.remember(entry["id"])
            note = self.run_app(entry)
            if entry["kind"] == "system" and not note:
                time.sleep(30)                   # the machine is going down: do not draw the menu again meanwhile
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=config.default_path(), help="config.json (profiles)")
    ap.add_argument("--launcher-config", default=os.path.join(ROOT, "launcher.json"))
    ap.add_argument("--profile")
    ap.add_argument("--list", action="store_true", help="list the apps found and exit")
    ap.add_argument("--run", metavar="ID", help="start this app straight away (the menu returns when it quits)")
    ap.add_argument("--no-autostart", action="store_true")
    a = ap.parse_args(argv)
    lcfg = load_launcher_config(a.launcher_config)
    if a.no_autostart:
        lcfg["autostart"] = None
    entries = build_entries(lcfg)
    if a.list:
        for i, e in enumerate(entries, 1):
            print(f"{i:>2}  {e['id']:<10} {e['kind']:<7} {e['name']}  ({app_dir(e) or '-'})")
        return 0
    if not entries:
        sys.exit("no apps found: see launcher.json")
    cfg = config.load(a.config, a.profile)
    setup_logging(cfg["cache_dir"], "launcher")
    log.info("start: profile=%s apps=%s", cfg["profile"], [e["id"] for e in entries])
    return Launcher(cfg, lcfg, os.path.join(cfg["cache_dir"], "launcher-state.json"), entries).run(a.run)


if __name__ == "__main__":
    sys.exit(main())
