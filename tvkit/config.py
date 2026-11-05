"""Configuration: config.json (profiles = the mpv command lines per machine, plus settings the apps read)."""
import json
import os
import sys

from . import settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_path():
    return os.environ.get("TVL_CONFIG") or os.path.join(ROOT, "config.json")


def hdmi_connected(drm_dir="/sys/class/drm"):
    """True when the display driver reports a connected HDMI output (a Pi on HDMI); False for composite or nothing plugged in."""
    try:
        names = sorted(os.listdir(drm_dir))
    except OSError:
        return False
    for n in names:
        if "-HDMI-" in n:
            try:
                with open(os.path.join(drm_dir, n, "status")) as f:
                    if f.read().strip() == "connected":
                        return True
            except OSError:
                continue
    return False


def auto_profile(drm_dir="/sys/class/drm"):
    """The profile for this machine: mac on a Mac, desktop on a Linux desktop (X11 / Wayland), on a Raspberry Pi without a desktop
    hdmi when an HDMI display is connected and pi (composite) otherwise."""
    if sys.platform == "darwin":
        return "mac"
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return "desktop"
    try:
        with open("/proc/device-tree/model", "rb") as f:
            if b"Raspberry Pi" in f.read():
                return "hdmi" if hdmi_connected(drm_dir) else "pi"
    except OSError:
        pass
    return "desktop"


def load(path=None, profile=None):
    """cfg with cfg["prof"] (the chosen profile) and cfg["cache_dir"] expanded.
    The profile is, in order: the argument, $TVL_PROFILE, "profile" in config.json, and with "auto" (the default) the machine's own (auto_profile)."""
    with open(path or default_path()) as f:
        cfg = json.load(f)
    settings.apply_overlay(cfg)                       # what the web page saved (~/.config/tv-launcher/settings.json) wins over config.json
    cfg["profile"] = profile or os.environ.get("TVL_PROFILE") or cfg.get("profile", "auto")
    if cfg["profile"] == "auto":
        cfg["profile"] = auto_profile()
    if cfg["profile"] not in cfg["profiles"]:
        raise SystemExit(f"unknown profile {cfg['profile']!r}; known: {', '.join(cfg['profiles'])}")
    cfg["cache_dir"] = os.path.expanduser(cfg.get("cache_dir", "~/.cache/tv-launcher"))
    cfg["prof"] = cfg["profiles"][cfg["profile"]]
    return cfg
