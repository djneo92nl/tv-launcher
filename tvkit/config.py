"""Configuration: config.json (profiles = the mpv command lines per machine, plus settings the apps read)."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_path():
    return os.environ.get("TVL_CONFIG") or os.path.join(ROOT, "config.json")


def load(path=None, profile=None):
    """cfg with cfg["prof"] (the chosen profile) and cfg["cache_dir"] expanded. Environment TVL_PROFILE picks a profile too."""
    with open(path or default_path()) as f:
        cfg = json.load(f)
    cfg["profile"] = profile or os.environ.get("TVL_PROFILE") or cfg.get("profile", "mac")
    if cfg["profile"] not in cfg["profiles"]:
        raise SystemExit(f"unknown profile {cfg['profile']!r}; known: {', '.join(cfg['profiles'])}")
    cfg["cache_dir"] = os.path.expanduser(cfg.get("cache_dir", "~/.cache/tv-launcher"))
    cfg["prof"] = cfg["profiles"][cfg["profile"]]
    return cfg
