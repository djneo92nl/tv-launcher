"""Shared helpers for the tests: no network, no mpv."""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_app(name):
    """The module of apps/<name>/app.py (apps are scripts, not packages)."""
    path = os.path.join(ROOT, "apps", name, "app.py")
    sys.path.insert(0, os.path.dirname(path))
    spec = importlib.util.spec_from_file_location("app_" + name.strip("_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeMpv:
    """Stands in for tvkit.mpvctl.Mpv: records what the app asked for."""
    dir = "/tmp"

    def __init__(self):
        self.overlays, self.commands, self.played = {}, [], []

    def overlay(self, oid, ass):
        self.overlays[oid] = ass

    def command(self, *a):
        self.commands.append(a)

    def play(self, url, **kw):
        self.played.append(url)

    def stop(self):
        self.commands.append(("stop",))

    def get(self, prop, timeout=3):
        return {"sid": 1, "aid": 1}.get(prop)

    def quit(self):
        pass


def fake_shell(cls, cfg=None):
    """An app instance with a FakeMpv instead of a real screen; drive it with .q.put(("cmd", "up")) and .step(0)."""
    cfg = {"cache_dir": "/tmp/tvl-test", "prof": {"mpv": ["mpv"]}, "profile": "test", **(cfg or {})}
    s = cls(cfg)
    s.mpv = FakeMpv()
    return s


def press(shell, *cmds):
    for c in cmds:
        shell.q.put(("cmd", c))
        shell.step(0)
