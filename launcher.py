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
import signal
import subprocess
import sys
import threading
import time

from tvkit import appcli, apps as appsmod, config, control, manifest, settings, ui
from tvkit import web as webmod
from tvkit.shell import Shell, setup_logging

ROOT = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("launcher")

DEFAULT_CONFIG = {
    "title": "TV LAUNCHER",
    "autostart": None,                      # an app id: start it this many seconds after boot unless a key is pressed
    "autostart_seconds": 6,
    "remember_last": True,                  # the menu opens on the app you used last
    "crash_seconds": 10,                    # an app that fails within this time is reported on the menu
    "search_dirs": [],                      # extra folders to look in for apps given as a relative "dir" (the packaged apps use it)
    "apps": [                               # external clients (their own programs, in their own repos); built-in and installed apps come from their app.json
        {"id": "spelunkr", "name": "SPELUNKR TV", "desc": "Cable TV from the Internet Archive", "icon": "tv", "order": 200, "source": "external",
         "dir": ["/opt/spelunkr-tv", "~/spelunkr-tv", "../TV Tuner", "../spelunkr-tv"], "cmd": ["python3", "tv.py", "--profile", "{profile}"]},
        {"id": "iptv", "name": "IPTV", "desc": "Live channels from your own subscription", "icon": "stream", "order": 210, "source": "external",
         "dir": ["/opt/spelunkr-iptv", "~/spelunkr-iptv", "../TV Tuner IPTV", "../spelunkr-iptv"], "cmd": ["python3", "tv.py", "--profile", "{profile}"]},
    ],
    "hidden": [],                           # app ids left out of the menu
    "order": [],                            # app ids that come first, in this order
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
    cfg.update(settings.launcher_overlay())          # what the web page saved wins over launcher.json
    return cfg


SEARCH_DIRS = []                            # set from launcher.json by main()


def candidates(d):
    """Folders a `dir` entry can mean: absolute and ~ paths as they are; relative ones inside this repo and inside each search dir."""
    d = os.path.expanduser(d)
    if os.path.isabs(d):
        return [d]
    return [os.path.normpath(os.path.join(ROOT, d))] + [os.path.normpath(os.path.join(base, os.path.basename(d.rstrip("/")))) for base in SEARCH_DIRS]


def app_dir(app):
    """The folder of an app: `dir` is one path or a list of places to look, the first that exists wins (else the first, so errors name it)."""
    d = app.get("dir")
    if not d:
        return None
    options = [c for entry in ([d] if isinstance(d, str) else d) for c in candidates(entry)]
    return next((c for c in options if os.path.isdir(c)), options[0])


def app_available(app):
    """An app with a `dir` needs that folder (and its script) to exist; one without (a system action) is always there."""
    d = app_dir(app)
    if not d:
        return True
    if not os.path.isdir(d):
        return False
    script = next((a for a in app["cmd"][1:] if a.endswith(".py")), None)
    return script is None or os.path.exists(os.path.join(d, script))


user_apps_dir = appsmod.user_apps_dir


def discover_apps(roots=None):
    """Menu entries from the app.json of every folder under apps/ (built in) and the user's apps folder (installed).
    A folder without a valid manifest is skipped (and noted in the log); an installed app cannot take a built-in app's id."""
    roots = roots or [("builtin", os.path.join(ROOT, "apps")), ("user", user_apps_dir())]
    found, seen = [], set()
    for source, base in roots:
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            folder = os.path.join(base, name)
            if name.startswith(("_", ".")) or not os.path.isdir(folder):
                continue
            try:
                m = manifest.read(folder)
            except manifest.ManifestError as ex:
                log.warning("skipping app folder %s: %s", folder, ex)
                continue
            if m["id"] in seen:
                log.warning("skipping %s: the id %s is already taken", folder, m["id"])
                continue
            seen.add(m["id"])
            found.append(manifest.entry(folder, m, source))
    return found


def all_apps(cfg, discover=True):
    """Every app the launcher knows (before hiding and ordering): discovered ones, then the externally listed ones that are installed."""
    apps = discover_apps() if discover else []
    ids = {a["id"] for a in apps}
    for e in cfg.get("apps", []):
        if e.get("id") and e.get("name") and e.get("cmd") and e["id"] not in ids and app_available(e):
            apps.append({**e, "kind": "app"})
            ids.add(e["id"])
    return apps


def order_apps(apps, cfg):
    """Hidden apps removed; `order` ids first in that order; the rest by their manifest order, then name."""
    hidden, first = set(cfg.get("hidden") or []), list(cfg.get("order") or [])
    rest = sorted((a for a in apps if a["id"] not in hidden), key=lambda a: (a.get("order", 100), a["name"]))
    ranked = [next(a for a in rest if a["id"] == i) for i in first if any(a["id"] == i for a in rest)]
    return ranked + [a for a in rest if a["id"] not in first]


REMOTE_ENTRY = {"id": "remote", "name": "REMOTE", "desc": "Control this TV from your phone, change settings", "icon": "remote", "kind": "internal", "cmd": ["-"]}


def build_entries(cfg, discover=False, web=False):
    """The menu: installed apps first, then REMOTE (when the web interface is on), then the system actions.
    Each entry gets a `kind` of "app", "internal" (handled by the launcher itself) or "system"."""
    out = order_apps(all_apps(cfg, discover), cfg)
    if web:
        out.append(dict(REMOTE_ENTRY))
    for e in cfg.get("system", []):
        if e.get("id") and e.get("name") and e.get("cmd") and app_available(e):
            out.append({**e, "kind": "system"})
    if not sys.platform.startswith("linux"):
        out = [e for e in out if e["kind"] != "system"]       # no reboot / shutdown buttons on a Mac
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


def last_line(path, limit=64):
    """The last non-empty line of a small text file (an app's stderr), shortened; '' if there is none."""
    try:
        with open(path, errors="replace") as f:
            lines = [ln.strip() for ln in f.read()[-4000:].splitlines() if ln.strip()]
    except OSError:
        return ""
    return (lines[-1][:limit] if lines else "")


def confirm_screen(item):
    out = [ui.background()] + ui.header("CONFIRM")
    out.append(ui.text(ui.W // 2, ui.H // 2 - 90, ui.esc(item["title"])[:40], 34, ui.AMBER, an=5, border=2))
    for i, line in enumerate(item["lines"][:6]):
        out.append(ui.text(ui.W // 2, ui.H // 2 - 30 + i * 30, ui.esc(line)[:70], 20, ui.WHITE, an=5, bold=False, border=1))
    out.append(ui.text(ui.W // 2, ui.H // 2 + 150, "ENTER = yes      BACK = no", 24, ui.GREY, an=5, border=1))
    return "\n".join(out + ui.footer("A request from your phone. It is cancelled after a minute."))


def pair_screen(url, pin, seconds, devices, paired=None):
    out = [ui.background()] + ui.header("REMOTE")
    if paired:
        out.append(ui.text(ui.W // 2, ui.H // 2 - 20, "PAIRED", 60, ui.AMBER, an=5, border=3))
        out.append(ui.text(ui.W // 2, ui.H // 2 + 50, ui.esc(paired)[:30], 28, ui.WHITE, an=5, border=1))
        return "\n".join(out)
    code = pin or "------"
    out += [ui.text(ui.W // 2, ui.SY + 56, "On your phone open", 20, ui.GREY, an=8, bold=False, border=1),
            ui.text(ui.W // 2, ui.SY + 84, ui.esc(url), 30, ui.WHITE, an=8, border=2),
            ui.text(ui.W // 2, ui.SY + 150, "and type this code", 20, ui.GREY, an=8, bold=False, border=1),
            ui.text(ui.W // 2, ui.SY + 180, code[:3] + " " + code[3:], 96, ui.AMBER, an=8, border=3),
            ui.text(ui.W // 2, ui.SY + 310, f"valid for {seconds // 60}:{seconds % 60:02d}", 20, ui.DIM, an=8, bold=False, border=0),
            ui.text(ui.W // 2, ui.SY + 350, f"{devices} phone{'s' if devices != 1 else ''} paired already", 18, ui.GREY, an=8, bold=False, border=0)]
    return "\n".join(out + ui.footer("ENTER or BACK closes this screen"))


# ---- running ----
class Launcher(Shell):
    name = "launcher"

    def __init__(self, cfg, lcfg, store_path, entries):
        super().__init__(cfg)
        self.lcfg, self.entries, self.store_path, self.profile = lcfg, entries, store_path, cfg["profile"]
        self.child = None
        self.active = None                      # the entry that is running as a child right now
        self.pending = None                     # an app id someone (the web interface) asked to start next
        self.menu = None
        self.lock = threading.Lock()
        self.mpv_failed = False                 # mpv kept dying and we gave up: exit with an error so systemd (and you) can tell
        self.web, self.web_error = None, None
        self.pairing, self.confirms = webmod.Pairing(), webmod.Confirmations()
        self.catalog, self.reload_flag = [], False
        self.config_path, self.launcher_path = config.default_path(), os.path.join(ROOT, "launcher.json")

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

    # -- what the web interface sees and may ask for --
    def status(self):
        st = super().status()
        a = self.active
        st["app"], st["running"] = "launcher", ({"id": a["id"], "name": a["name"]} if a else None)
        st["screen"] = "MENU"
        st["selected"] = self.menu.current()["id"] if self.menu and self.menu.current() else None
        return st

    def request_start(self, app_id):
        """Start an app now (from another thread): the menu launches it, or a running app is asked to quit first. False if there is no such app."""
        with self.lock:
            entry = next((e for e in self.entries if e["id"] == app_id and e["kind"] == "app"), None)
            if entry is None:
                return False
            self.pending = app_id
            child = self.child
        if child and child.poll() is None:
            child.terminate()
            threading.Timer(6.0, lambda: child.poll() is None and child.kill()).start()          # an app that ignores SIGTERM is killed
        else:
            self.q.put(("app", ("start", app_id)))
        return True

    def take_pending(self):
        with self.lock:
            app_id, self.pending = self.pending, None
        return next((e for e in self.entries if e["id"] == app_id), None) if app_id else None

    # ---- the web interface: what it asks of the launcher (see tvkit.web.WebServer) ----
    name = "TV Launcher"

    def apps(self):
        """Every app in menu order, hidden ones included (marked), so the page lists what the menu would."""
        hidden = set(self.lcfg.get("hidden") or [])
        ordered = order_apps(self.catalog, {"order": self.lcfg.get("order") or []})
        return [{"id": a["id"], "name": a["name"], "desc": a.get("desc", ""), "icon": a.get("icon", "app"), "version": a.get("version", "-"),
                 "source": a.get("source", "external"), "hidden": a["id"] in hidden} for a in ordered]

    def known_app_ids(self):
        return {a["id"] for a in self.catalog}

    def foreground(self):
        a = self.active
        return a["id"] if a else "launcher"

    def start_app(self, app_id):
        return self.request_start(app_id)

    def settings_values(self):
        cfg = config.load(self.config_path, self.cfg["profile"])
        lcfg = load_launcher_config(self.launcher_path)
        return {f["key"]: settings.effective(cfg, lcfg, f["key"]) for f in settings.SCHEMA}

    def settings_changed(self, keys):
        if any(k.startswith("launcher.") for k in keys):
            self.reload_flag = True                      # the menu loop rebuilds itself on its next turn (this is called from a web thread)
            self.q.put(("app", ("wake", None)))

    def reload(self):
        """Re-read launcher.json and the saved settings, and rebuild the menu entries."""
        self.lcfg = load_launcher_config(self.launcher_path)
        SEARCH_DIRS[:] = [os.path.expanduser(x) for x in self.lcfg.get("search_dirs", [])]
        self.catalog = all_apps(self.lcfg)
        self.entries = build_entries(self.lcfg, discover=True, web=self.web is not None)
        self.reload_flag = False

    def repositories(self):
        return config.load(self.config_path, self.cfg["profile"]).get("repositories", [])

    def reserved_ids(self):
        return reserved_ids(self.lcfg)

    def apps_changed(self):
        """An app was installed, updated or removed: rebuild the menu on its next turn."""
        self.reload_flag = True
        self.q.put(("app", ("wake", None)))

    def bring_home(self):
        """Stop the running app so the TV can show a dialog (the launcher's screen is closed while an app has the display)."""
        child = self.child
        if child and child.poll() is None:
            child.terminate()
            threading.Timer(6.0, lambda: child.poll() is None and child.kill()).start()
        self.q.put(("app", ("wake", None)))

    def confirm(self, title, lines, action):
        cid = self.confirms.request(title, lines, action)
        self.bring_home()
        return cid

    def system(self, action):
        entry = next((e for e in self.entries if e["id"] == {"restart": "reboot", "poweroff": "poweroff"}[action]), None)
        if not entry:
            log.warning("%s was confirmed but this platform has no such action", action)
            return
        log.info("system action: %s", action)
        subprocess.Popen(entry["cmd"])

    def log_tail(self, n=60):
        try:
            with open(os.path.join(self.cfg["cache_dir"], "launcher.log"), errors="replace") as f:
                return [ln.rstrip()[:300] for ln in f.readlines()[-n:]]
        except OSError:
            return []

    def setup_web(self, enabled, port, config_path, launcher_path):
        self.config_path, self.launcher_path = config_path, launcher_path
        self.web = self.web_error = None
        if not enabled:
            return
        try:
            self.web = webmod.WebServer(self, port=port, pairing=self.pairing, confirmations=self.confirms)
            self.web.start()
            log.info("web interface on %s", self.web.url())
        except OSError as ex:
            self.web_error = f"The web interface could not start on port {port} ({ex.strerror or 'error'})"
            log.error(self.web_error)
        self.reload()

    # -- the menu: returns the entry to run, or None when asked to stop --
    def menu_loop(self, note="", first=False):
        if self.reload_flag:
            self.reload()
        menu = self.menu = Menu(self.entries, self.last_app())
        menu.title_sub, menu.note = self.lcfg.get("title", "TV LAUNCHER"), note
        auto = next((e for e in self.entries if e["id"] == self.lcfg.get("autostart") and e["kind"] == "app"), None)
        deadline = time.time() + self.lcfg["autostart_seconds"] if first and auto and self.lcfg.get("autostart_seconds", 0) > 0 else None
        self.open_screen()
        drawn, pairing_until, paired_msg = None, 0, None
        try:
            while not self.stopping:
                ev = self.next_event(0.25)
                now = time.time()
                if self.reload_flag:
                    sel = menu.current()["id"] if menu.current() else None
                    self.reload()
                    menu.entries = self.entries
                    menu.sel = next((i for i, e in enumerate(self.entries) if e["id"] == sel), 0)
                    menu.title_sub = self.lcfg.get("title", "TV LAUNCHER")
                    drawn = None
                dialog = self.confirms.current()
                if ev and ev[0] == "cmd":
                    deadline = None                                 # any key cancels the autostart
                    if dialog:                                      # a question from the phone owns the keys until it is answered
                        if ev[1] in ("enter", "click"):
                            self.confirms.answer(dialog["id"], True)
                        elif ev[1] in ("back", "quit"):
                            self.confirms.answer(dialog["id"], False)
                    elif pairing_until:
                        if ev[1] in ("back", "quit", "enter", "click"):
                            pairing_until, paired_msg = 0, None
                            self.pairing.close()
                    else:
                        chosen = menu.handle(ev[1])
                        if chosen and chosen["kind"] == "internal":
                            if self.web:
                                self.pairing.open()
                                pairing_until, paired_msg = now + webmod.PIN_TTL_S, None
                            else:
                                menu.note = self.web_error or "The web interface is switched off"
                        elif chosen:
                            return chosen
                elif ev and ev[0] == "app" and ev[1][0] == "start":
                    chosen = self.take_pending()
                    if chosen:
                        return chosen
                elif ev and ev[0] in ("dead", "closed"):          # a closed window quits the launcher; a crash gave up restarting
                    if ev[0] == "closed":
                        self.stopping = True
                    else:
                        self.mpv_failed = True
                        log.error("giving up: mpv keeps dying (see %s/mpv.err)", self.cfg["cache_dir"])
                    return None
                countdown = None
                if deadline is not None:
                    left = int(deadline - now + 0.999)
                    if left <= 0:
                        return auto
                    countdown = f"STARTING {auto['name']} IN {left}   ANY KEY FOR THE MENU"
                if menu.confirm and now > menu.confirm[1]:
                    menu.confirm = None
                if pairing_until:
                    ok = self.pairing.last_success
                    if ok and ok[1] > pairing_until - webmod.PIN_TTL_S:
                        paired_msg, pairing_until = ok[0], now + 3        # show who paired for a moment, then go back to the menu
                        self.pairing.last_success = None
                    elif paired_msg is None and not self.pairing.is_open():
                        pairing_until = 0
                    elif paired_msg is not None and now > pairing_until:
                        pairing_until, paired_msg = 0, None
                if self.mpv:
                    if dialog:
                        ass = confirm_screen(dialog)
                    elif pairing_until:
                        ass = pair_screen(self.web.url() if self.web else "", self.pairing.pin, max(0, int(self.pairing.expires - now)), len(self.web.devices.list()) if self.web else 0, paired_msg)
                    else:
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
        # every app can `import tvkit` wherever its folder is (installed apps live outside this repo)
        env = {**os.environ, "TVL_APP_ID": entry["id"], "PYTHONPATH": os.pathsep.join(p for p in (ROOT, os.environ.get("PYTHONPATH")) if p)}
        err_path = os.path.join(self.cfg["cache_dir"], entry["id"] + ".err")
        try:
            os.makedirs(self.cfg["cache_dir"], exist_ok=True)
            err = open(err_path, "wb")                       # what the app prints to stderr: the reason, if it dies at once
        except OSError:
            err = subprocess.DEVNULL
        try:
            with self.lock:
                self.child = subprocess.Popen(cmd, cwd=cwd, env=env, stderr=err)
                self.active = entry
        except OSError as ex:
            log.error("could not start %s: %s", entry["id"], ex)
            return f"{entry['name']}: could not start ({ex.strerror or 'error'})"
        try:
            code = self.child.wait()
        finally:
            with self.lock:
                self.child, self.active = None, None
            if hasattr(err, "close"):
                err.close()
        log.info("%s exited with code %s after %.0f s", entry["id"], code, time.time() - t0)
        if self.stopping:
            return ""
        if entry["kind"] == "system":
            return f"{entry['name']} did not work (code {code}); see the log" if code else ""
        if code != 0 and time.time() - t0 < self.lcfg.get("crash_seconds", 10):
            why = last_line(err_path)
            return f"{entry['name']} stopped at once: {why}" if why else f"{entry['name']} stopped at once (code {code}); see {self.cfg['cache_dir']}"
        return ""

    def quit(self, code=0):
        super().quit(code)
        child = self.child
        if child and child.poll() is None:
            child.terminate()
        self.q.put(("cmd", "back"))              # wakes the menu loop

    def run(self, only=None):
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        note, first = "", True
        self.control = control.ControlServer(self, "launcher")
        try:
            self.control.start()
        except OSError:
            log.exception("could not open the launcher's control socket")
        try:
            return self._run(only, note, first)
        finally:
            self.control.stop()
            if self.web:
                self.web.stop()

    def _run(self, only, note, first):
        while not self.stopping:
            entry = self.take_pending()                         # an app the web interface asked for wins over showing the menu
            if entry is None and only:
                entry, only = next((e for e in self.entries if e["id"] == only), None), None
                if entry is None:
                    return 2
            elif entry is None:
                entry = self.menu_loop(note, first)
            first = False
            if entry is None:
                break
            if entry["kind"] == "app":
                self.remember(entry["id"])
            note = self.run_app(entry)
            if entry["kind"] == "system" and not note:
                time.sleep(30)                   # the machine is going down: do not draw the menu again meanwhile
        return 1 if self.mpv_failed else 0


def reserved_ids(lcfg):
    """Ids an installed app may not take: built-in apps, the external clients (installed here or not), and the launcher's own names."""
    ids = {a["id"] for a in discover_apps() if a["source"] != "user"}
    ids |= {e["id"] for e in DEFAULT_CONFIG["apps"] + list(lcfg.get("apps", [])) if e.get("id")}
    return ids | {e["id"] for e in lcfg.get("system", []) if e.get("id")} | appsmod.RESERVED_IDS


def apps_command(argv):
    """`launcher.py apps ...`: needs no display and no mpv."""
    cfg_path = os.environ.get("TVL_CONFIG") or config.default_path()
    lcfg = load_launcher_config(os.path.join(ROOT, "launcher.json"))
    repos = lambda: config.load(cfg_path, "mac" if sys.platform == "darwin" else None).get("repositories", [])
    return appcli.run(argv, reserved_ids(lcfg), repos)


def main(argv=None):
    if (argv if argv is not None else sys.argv[1:])[:1] == ["apps"]:
        return apps_command((argv if argv is not None else sys.argv[1:])[1:])
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
    SEARCH_DIRS[:] = [os.path.expanduser(x) for x in lcfg.get("search_dirs", [])]
    entries = build_entries(lcfg, discover=True)
    if a.list:
        for i, e in enumerate(entries, 1):
            print(f"{i:>2}  {e['id']:<10} {e['kind']:<7} {e['name']}  ({app_dir(e) or '-'})")
        return 0
    if not entries:
        sys.exit("no apps found: see launcher.json")
    cfg = config.load(a.config, a.profile)
    setup_logging(cfg["cache_dir"], "launcher")
    log.info("start: profile=%s apps=%s", cfg["profile"], [e["id"] for e in entries])
    L = Launcher(cfg, lcfg, os.path.join(cfg["cache_dir"], "launcher-state.json"), entries)
    web_cfg = cfg.get("web", {})
    L.setup_web(bool(web_cfg.get("enabled", True)) and not os.environ.get("TVL_NO_WEB"), int(web_cfg.get("port", 8090)), a.config, a.launcher_config)
    if not L.web:
        L.reload()
    try:
        return L.run(a.run)
    except Exception:
        log.exception("the launcher crashed")           # into launcher.log, not only the console: nobody is watching a TV box's console
        return 1


if __name__ == "__main__":
    sys.exit(main())
