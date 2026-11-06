"""Shell: the part every app shares. It owns one mpv (idle, full window, drawing ASS overlays) and the inputs
(terminal keys, USB keyboard / remote through evdev, HDMI-CEC, keys pressed in mpv's window), and gives an app a tiny contract:

    class MyApp(Shell):
        def handle(self, cmd): ...          # "up" "down" "left" "right" "enter" "back" "quit" "digit:3" "volup" ...
        def tick(self, now): ...            # about 4 times a second (optional)
        def draw(self, now): return "\\n".join(ui.background(), ...)       # ASS; redrawn only when the text changes

    if __name__ == "__main__":
        run_app(MyApp)

`run()` returns the exit code; 0 means "back to the launcher". Commands are the ones in inputs.py (the same names the TV clients use).
"""
import argparse
import logging
import os
import queue
import signal
import sys
import threading
import time

from . import config, control, inputs, mpvctl, ui
from .mpvctl import Mpv

log = logging.getLogger("tvkit")
OVL = 1                       # the overlay layer the shell draws into; apps that want more can use 2..9


class Shell:
    name = "app"
    mpv_args = []             # extra mpv options for this app (e.g. a photo viewer wants --image-display-duration=inf)

    def __init__(self, cfg):
        self.cfg = cfg
        self.q = queue.Queue()
        self.mpv = None
        self.inputs = []
        self.win = {}
        self.mpv_deaths = []
        self.stopping = False
        self.exit_code = 0
        self._force = False
        self._drawn = None

    # ---- what an app overrides ----
    def start(self):
        """Called once after the screen is open."""

    def handle(self, cmd):
        if cmd in ("quit", "back"):
            self.quit()
        elif cmd in ("volup", "voldown") and self.mpv:
            self.mpv.command("add", "volume", 5 if cmd == "volup" else -5)
        elif cmd == "mute" and self.mpv:
            self.mpv.command("cycle", "mute")

    def on_mpv(self, ev):
        """Any other mpv event (end-file, file-loaded ...), for apps that play things."""

    def tick(self, now):
        """Periodic work; keep it short (anything slow goes in a thread that sets a flag)."""

    def draw(self, now):
        return ui.background()

    def quit(self, code=0):
        self.stopping = True
        self.exit_code = code

    actions = ()                                  # actions other programs may ask this one to do over its control socket (the launcher offers some)

    def do_action(self, name):
        return False

    def status(self):
        """What the web interface shows about this app. Apps override it (and call super()) to add what they are showing or playing.
        Called from another thread: build the dict from plain values and do not touch mpv."""
        return {"app": self.app_id, "screen": "", "playing": None, "text_input": bool(inputs.TEXT_MODE)}

    @property
    def app_id(self):
        """The id the launcher started this app under (TVL_APP_ID), else the class name. Used for the control socket and the data folder."""
        return os.environ.get("TVL_APP_ID") or self.name

    @property
    def data_dir(self):
        """A private folder for this app's own files; it survives updates. Created on first use."""
        base = os.environ.get("TVL_DATA_DIR") or os.path.expanduser("~/.local/share/tv-launcher/data")
        d = os.path.join(base, self.app_id)
        os.makedirs(d, mode=0o700, exist_ok=True)
        return d

    # ---- the screen ----
    def open_screen(self):
        ui.set_safe((self.cfg.get("display") or {}).get("safe_percent", 7))      # some screens crop the edges: the margin is a setting
        prof = self.cfg["prof"]
        argv = [a for a in prof["mpv"] if not a.startswith(("--volume=", "--mute="))] + list(self.mpv_args)
        self.mpv = Mpv(argv, dict(inputs.KEY_BINDINGS), mono=bool(prof.get("mono")), log_dir=self.cfg["cache_dir"])
        self.mpv.res = (ui.W, 576)
        for i, prop in enumerate(("osd-width", "osd-height"), 1):
            self.mpv.command("observe_property", i, prop)
        threading.Thread(target=self._pump, args=(self.mpv,), daemon=True, name="mpv-events").start()
        self.inputs = [inputs.KeyboardInput()]
        if self.cfg.get("evdev", True) and sys.platform.startswith("linux"):
            self.inputs.append(inputs.EvdevInput())
        cec = dict(self.cfg.get("cec", {}))
        if cec.get("enabled"):
            self.inputs.append(inputs.CecInput(cec.get("name", "TV Launcher"), cec.get("wake_tv", True),
                                               cec.get("binary", "cec-client"), cec.get("log_level", 16)))
        for src in self.inputs:
            src.start(lambda c: self.q.put(("cmd", c)))
        self._force = True

    def _pump(self, mpv):
        while True:
            ev = mpv.events.get()
            self.q.put(("mpv", (mpv, ev)))
            if ev.get("event") == "mpv-exited":
                return

    def close_screen(self):
        """Hand everything back (the display, the keyboard): the next program needs them."""
        for src in self.inputs:
            try:
                src.stop()
            except Exception:
                log.exception("stopping an input")
        self.inputs = []
        if self.mpv:
            self.mpv.quit()
            self.mpv = None
        time.sleep(0.2)

    def window_scale(self):
        """(sx, sy): canvas pixels to window pixels, for things mpv draws in window coordinates (images)."""
        w, h = self.win.get("osd-width"), self.win.get("osd-height")
        return (w / ui.W, h / 576) if w and h else (1.0, 1.0)

    def revive(self):
        """mpv died: start a new one (an unattended box must come back), but not in a loop."""
        now = time.time()
        self.mpv_deaths = [t for t in self.mpv_deaths if now - t < 300] + [now]
        why = mpvctl.why_it_died(getattr(self.mpv, "err_path", None))
        log.error("mpv exited unexpectedly%s; see %s/mpv.log and mpv.err", f" ({why})" if why else "", self.cfg["cache_dir"])
        if len(self.mpv_deaths) > 4:
            return False
        for src in self.inputs:
            src.stop()
        self.inputs = []
        self.open_screen()
        return True

    def next_event(self, timeout=0.25):
        """One thing that happened: ("cmd", name), ("mpv", event), ("resize", None) or ("dead", None); None if nothing did."""
        try:
            kind, val = self.q.get(timeout=timeout)
        except queue.Empty:
            return None
        if kind == "cmd":
            return kind, val
        if kind == "app":                                   # an event an app (or the launcher) put on the queue itself: handed through untouched
            return kind, val
        if kind == "call":                                  # work handed back from a background thread, see later()
            try:
                val()
            except Exception:
                log.exception("error in a callback")
            return "tick", None
        mpv, ev = val
        if mpv is not self.mpv:
            return None
        name = ev.get("event")
        if name == "client-message" and ev.get("args", [None])[0] == "tv":
            return "cmd", ev["args"][1]
        if name == "property-change" and ev.get("name") in ("osd-width", "osd-height"):
            self.win[ev["name"]] = ev.get("data")
            w, h = self.win.get("osd-width"), self.win.get("osd-height")
            if w and h:
                width = max(512, min(1280, round(576 * w / h)))
                if abs(width - ui.W) >= 4:
                    ui.set_canvas(width)
                    self.mpv.res = (ui.W, 576)
                    self._force = True
                    return "resize", None
            return None
        if name == "mpv-exited" and not self.stopping:
            if ev.get("code") == 0:                          # the window was closed (or q pressed in it): that is a quit, not a crash
                return "closed", None
            return ("dead", None) if not self.revive() else ("resize", None)
        return "mpv", ev

    def later(self, fn):
        """Run fn() on the main loop's thread (call it from a background thread when a download is done, then touch the app's state safely)."""
        self.q.put(("call", fn))

    def refresh(self):
        """Redraw at the next loop even if the picture is the same (after something outside drew over it)."""
        self._force = True

    # ---- the loop ----
    def step(self, timeout=0.25):
        """One turn of the loop: wait for an event, handle it, tick, redraw if the picture changed. Returns False once the app should stop."""
        ev = self.next_event(timeout)
        try:
            if ev and ev[0] == "cmd":
                self.handle(ev[1])
            elif ev and ev[0] == "mpv":
                self.on_mpv(ev[1])
            elif ev and ev[0] == "closed":
                self.quit(0)
                return False
            elif ev and ev[0] == "dead":
                self.quit(1)
                return False
            self.tick(time.time())
            ass = self.draw(time.time())
        except Exception:
            log.exception("error in %s", self.name)       # one bad event must not take the screen down
            time.sleep(0.3)
            return not self.stopping
        if self.mpv and (ass != self._drawn or self._force):
            self.mpv.overlay(OVL, ass)
            self._drawn, self._force = ass, False
        return not self.stopping

    def run(self):
        survive_hangup()
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        signal.signal(signal.SIGINT, lambda *_: self.quit())
        self.open_screen()
        self.control = control.ControlServer(self, self.app_id)
        try:
            self.control.start()
        except OSError:
            log.exception("could not open the control socket: the web remote cannot reach this app")
        try:
            self.start()
            while self.step():
                pass
        finally:
            self.control.stop()
            self.close_screen()
        return self.exit_code


def survive_hangup():
    """Under systemd, ignore SIGHUP: a console hangup (a getty taking tty1 back, a VT switch) must not kill a TV box, and systemd would count that death as a clean exit.
    Run by hand (no INVOCATION_ID) the default stays, so closing an SSH session still ends the program."""
    if os.environ.get("INVOCATION_ID"):
        signal.signal(signal.SIGHUP, signal.SIG_IGN)


def setup_logging(cache_dir, name):
    os.makedirs(cache_dir, exist_ok=True)
    h = logging.FileHandler(os.path.join(cache_dir, f"{name}.log"))
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    root.addHandler(logging.StreamHandler(sys.stderr))
    root.handlers[-1].setLevel(logging.WARNING)


def run_app(app_class, argv=None, extra_args=None):
    """The main() of an app: parse --profile / --config, build the class with the config, run it, exit with its code."""
    ap = argparse.ArgumentParser(description=(app_class.__doc__ or app_class.name).strip().splitlines()[0])
    ap.add_argument("--profile")
    ap.add_argument("--config")
    if extra_args:
        extra_args(ap)
    a = ap.parse_args(argv)
    cfg = config.load(a.config, a.profile)
    cfg["args"] = a
    setup_logging(cfg["cache_dir"], app_class.name)
    log.info("start %s: profile=%s", app_class.name, cfg["profile"])
    sys.exit(app_class(cfg).run())
