"""Configuration: config.json (profiles = the mpv command lines per machine, plus settings the apps read)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_path():
    return os.environ.get("TVL_CONFIG") or os.path.join(ROOT, "config.json")


def auto_profile():
    """The profile for this machine: mac on a Mac, desktop on a Linux desktop (X11 / Wayland), pi on a Raspberry Pi without a desktop."""
    if sys.platform == "darwin":
        return "mac"
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return "desktop"
    try:
        with open("/proc/device-tree/model", "rb") as f:
            if b"Raspberry Pi" in f.read():
                return "pi"
    except OSError:
        pass
    return "desktop"


def load(path=None, profile=None):
    """cfg with cfg["prof"] (the chosen profile) and cfg["cache_dir"] expanded.
    The profile is, in order: the argument, $TVL_PROFILE, "profile" in config.json, and with "auto" (the default) the machine's own (auto_profile)."""
    with open(path or default_path()) as f:
        cfg = json.load(f)
    cfg["profile"] = profile or os.environ.get("TVL_PROFILE") or cfg.get("profile", "auto")
    if cfg["profile"] == "auto":
        cfg["profile"] = auto_profile()
    if cfg["profile"] not in cfg["profiles"]:
        raise SystemExit(f"unknown profile {cfg['profile']!r}; known: {', '.join(cfg['profiles'])}")
    cfg["cache_dir"] = os.path.expanduser(cfg.get("cache_dir", "~/.cache/tv-launcher"))
    cfg["prof"] = cfg["profiles"][cfg["profile"]]
    return cfg
