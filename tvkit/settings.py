"""Settings you can change from the web page: a typed schema, validation, and the overlay file they are saved in.

config.json stays as installed (and as you edit it by hand); what the web page changes goes to ~/.config/tv-launcher/settings.json
(mode 0600, written atomically) and is merged on top by tvkit.config.load. Keys are dotted paths ("clock.h24"); the "launcher." keys belong to
launcher.json's own settings (autostart, title, hidden apps ...). Secrets (the Jellyfin token) are never settings and never leave the machine.

    settings.set_many({"clock.h24": False})     # validates everything first, then writes once; raises SettingsError
    settings.schema()                            # what the web form is built from
"""
import copy
import json
import os
import re

from . import net

URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$")
HOST_RE = re.compile(r"^(https?://)?[A-Za-z0-9._-]+(:\d{1,5})?(/[^\s]*)?$")
APP_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


class SettingsError(ValueError):
    pass


def path():
    return os.environ.get("TVL_SETTINGS") or os.path.expanduser("~/.config/tv-launcher/settings.json")


# ---- the schema ----
def field(key, label, kind, default=None, group="General", **kw):
    return {"key": key, "label": label, "type": kind, "default": default, "group": group, **kw}


SCHEMA = [
    field("launcher.title", "Menu title", "str", "TV LAUNCHER", "Menu", max=24),
    field("launcher.autostart", "Start this app at boot", "app", None, "Menu", help="Empty: show the menu. Any key at boot cancels it."),
    field("launcher.autostart_seconds", "Seconds before it starts", "int", 6, "Menu", min=1, max=60),
    field("launcher.remember_last", "Menu opens on the app you used last", "bool", True, "Menu"),
    field("launcher.hidden", "Hidden apps", "applist", [], "Menu", help="Apps left out of the menu (they stay installed)."),
    field("launcher.order", "App order", "applist", [], "Menu", help="Apps listed here come first, in this order."),
    field("clock.h24", "24-hour clock", "bool", True, "Clock"),
    field("clock.seconds", "Show seconds", "bool", False, "Clock"),
    field("photos.dirs", "Photo folders", "paths", ["~/Pictures", "/media", "/mnt"], "Photos", help="USB sticks under /media and /mnt are found automatically."),
    field("photos.seconds", "Seconds per photo", "int", 8, "Photos", min=2, max=300),
    field("photos.shuffle", "Shuffle", "bool", True, "Photos"),
    field("feeds", "News feeds", "feeds", [], "News", max_items=9),
    field("media.roots", "Media folders", "paths", ["/media", "/mnt", "~/Videos", "~/Movies", "~/Music"], "Media"),
    field("remoment.host", "ReMoment server", "host", "http://remoment.local", "ReMoment"),
    field("jellyfin.host", "Jellyfin server", "host", "http://jellyfin.local:8096", "Jellyfin"),
    field("web.enabled", "Web interface", "bool", True, "Web", help="Remote control and settings from a phone on your network."),
    field("web.port", "Web port", "int", 8090, "Web", min=1024, max=65535),
]
BY_KEY = {f["key"]: f for f in SCHEMA}


def schema():
    return copy.deepcopy(SCHEMA)


# ---- validation: every value is checked and cleaned before it is stored ----
def clean(key, value, known_apps=None):
    f = BY_KEY.get(key)
    if not f:
        raise SettingsError(f"unknown setting {key!r}")
    kind = f["type"]
    try:
        if kind == "bool":
            if not isinstance(value, bool):
                raise SettingsError("must be on or off")
            return value
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise SettingsError("must be a whole number")
            if not f["min"] <= value <= f["max"]:
                raise SettingsError(f"must be between {f['min']} and {f['max']}")
            return value
        if kind == "str":
            if not isinstance(value, str) or not value.strip() or len(value) > f.get("max", 80) or any(ord(c) < 32 for c in value):
                raise SettingsError("must be text, up to %d characters" % f.get("max", 80))
            return value.strip()
        if kind == "host":
            if not isinstance(value, str) or not HOST_RE.match(value.strip()) or len(value) > 200:
                raise SettingsError("must be an address like http://server:8096")
            return value.strip().rstrip("/")
        if kind == "app":
            if value in (None, ""):
                return None
            if not isinstance(value, str) or not APP_ID_RE.match(value) or (known_apps is not None and value not in known_apps):
                raise SettingsError("must be an installed app")
            return value
        if kind == "applist":
            if not isinstance(value, list) or len(value) > 64:
                raise SettingsError("must be a list of apps")
            out = []
            for v in value:
                if not isinstance(v, str) or not APP_ID_RE.match(v) or (known_apps is not None and v not in known_apps):
                    raise SettingsError(f"{v!r} is not an installed app")
                if v not in out:
                    out.append(v)
            return out
        if kind == "paths":
            if not isinstance(value, list) or len(value) > 32:
                raise SettingsError("must be a list of folders")
            out = []
            for v in value:
                if not isinstance(v, str) or not v.strip() or len(v) > 300 or "\0" in v or not (v.startswith(("/", "~"))):
                    raise SettingsError(f"{v!r} must be a full path starting with / or ~")
                out.append(v.strip())
            return out
        if kind == "feeds":
            if not isinstance(value, list) or len(value) > f["max_items"]:
                raise SettingsError(f"at most {f['max_items']} feeds")
            out = []
            for v in value:
                if not isinstance(v, dict) or not isinstance(v.get("name"), str) or not isinstance(v.get("url"), str):
                    raise SettingsError("each feed needs a name and a url")
                name, url = v["name"].strip(), v["url"].strip()
                if not name or len(name) > 40 or any(ord(c) < 32 for c in name):
                    raise SettingsError("a feed name must be 1-40 characters")
                if not URL_RE.match(url) or len(url) > 300:
                    raise SettingsError(f"{url!r} is not an http(s) address")
                out.append({"name": name, "url": url})
            return out
    except KeyError:
        raise SettingsError("invalid setting") from None
    raise SettingsError(f"unsupported type {kind}")


# ---- the overlay file ----
def _nest(flat):
    out = {}
    for key, value in flat.items():
        d = out
        *parents, last = key.split(".")
        for p in parents:
            d = d.setdefault(p, {})
        d[last] = value
    return out


def _flatten(tree, prefix=""):
    out = {}
    for k, v in tree.items():
        key = prefix + k
        if key in BY_KEY or not isinstance(v, dict):
            out[key] = v
        else:
            out.update(_flatten(v, key + "."))
    return out


def load_flat():
    """{dotted key: value} of what the overlay holds, ignoring anything not in the schema (a hand-edited file must not break start-up)."""
    data = net.read_json(path(), {})
    flat = _flatten(data) if isinstance(data, dict) else {}
    out = {}
    for key, value in flat.items():
        if key in BY_KEY:
            try:
                out[key] = clean(key, value)
            except SettingsError:
                continue
    return out


def deep_merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def apply_overlay(cfg):
    """Merge the saved settings into a config dict (everything but the launcher.* keys, which belong to launcher.json)."""
    flat = {k: v for k, v in load_flat().items() if not k.startswith("launcher.")}
    return deep_merge(cfg, _nest(flat))


def launcher_overlay():
    return {k.split(".", 1)[1]: v for k, v in load_flat().items() if k.startswith("launcher.")}


def effective(cfg, lcfg, key):
    """The value in force for a key: what the merged config / launcher config holds, else the schema default."""
    tree = lcfg if key.startswith("launcher.") else cfg
    node = tree
    parts = key.split(".", 1)[1].split(".") if key.startswith("launcher.") else key.split(".")
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return copy.deepcopy(BY_KEY[key]["default"])
        node = node[p]
    return copy.deepcopy(node)


def set_many(changes, known_apps=None):
    """Validate every change, then write them all in one atomic step. Returns the cleaned {key: value}. Nothing is written if any is invalid."""
    cleaned = {k: clean(k, v, known_apps) for k, v in changes.items()}
    flat = load_flat()
    flat.update(cleaned)
    net.write_private(path(), _nest(flat))
    return cleaned


def reset(keys):
    flat = load_flat()
    for k in keys:
        flat.pop(k, None)
    net.write_private(path(), _nest(flat))
