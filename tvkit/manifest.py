"""App manifests: the app.json every app folder carries (built in or installed). One reader and one validator, used by the launcher and by the installer.

{
  "id": "clock", "name": "CLOCK", "version": "1.0.0", "description": "A big clock",
  "icon": "clock",                       # a name from tvkit.ui.ICONS, or {"colour": "a", "rows": [16 strings of 16 characters]}
  "tvkit": 1,                            # the lowest framework version the app needs
  "order": 10,                           # menu position among apps (lower first); installed apps default to 100
  "permissions": [{"kind": "network", "detail": "your Jellyfin server"}]      # shown before an install; NOT enforced (Python cannot sandbox an app)
}
The entry point is always app.py, started as `python3 app.py --profile <profile>` inside the app's folder.
"""
import json
import os
import re

from . import ui

TVKIT_VERSION = 1
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
VERSION_RE = re.compile(r"^\d{1,4}(\.\d{1,4}){0,2}$")
PERMISSION_KINDS = ("network", "files", "exec", "system", "devices")
MAX_MANIFEST_BYTES = 20_000


class ManifestError(ValueError):
    pass


def version_tuple(v):
    return tuple(int(x) for x in v.split("."))


def newer(a, b):
    """True when version a is higher than version b."""
    pad = lambda t: t + (0,) * (3 - len(t))
    return pad(version_tuple(a)) > pad(version_tuple(b))


def _text(d, key, limit, required=True):
    v = d.get(key, "" if not required else None)
    if not isinstance(v, str) or (required and not v.strip()) or len(v) > limit or any(ord(c) < 32 for c in v):
        raise ManifestError(f"{key} must be text of 1-{limit} characters" if required else f"{key} must be text up to {limit} characters")
    return v.strip()


def validate(d):
    """A cleaned copy of a manifest dict, or ManifestError saying what is wrong."""
    if not isinstance(d, dict):
        raise ManifestError("the manifest must be an object")
    out = {"id": d.get("id")}
    if not isinstance(out["id"], str) or not ID_RE.match(out["id"]):
        raise ManifestError("id must be lower-case letters, digits and dashes, starting with a letter (2-32 characters)")
    out["name"] = _text(d, "name", 24)
    out["version"] = d.get("version")
    if not isinstance(out["version"], str) or not VERSION_RE.match(out["version"]):
        raise ManifestError("version must look like 1.2.3")
    out["description"] = _text(d, "description", 90, required=False)
    icon = d.get("icon", "app")
    if isinstance(icon, str):
        if icon not in ui.ICONS:
            raise ManifestError(f"unknown icon {icon!r}")
    elif isinstance(icon, dict):
        rows = icon.get("rows")
        if icon.get("colour") not in ("a", "r") or not isinstance(rows, list) or len(rows) != 16 or not all(isinstance(r, str) and len(r) == 16 and set(r) <= set(".wabrg") for r in rows):
            raise ManifestError('a custom icon needs "colour" (a or r) and 16 "rows" of 16 characters from . w a b r g')
    else:
        raise ManifestError("icon must be a name or a 16x16 picture")
    out["icon"] = icon
    tv = d.get("tvkit", 1)
    if isinstance(tv, bool) or not isinstance(tv, int) or tv < 1:
        raise ManifestError("tvkit must be a whole number")
    if tv > TVKIT_VERSION:
        raise ManifestError(f"needs a newer TV Launcher (framework {tv}, this one is {TVKIT_VERSION})")
    out["tvkit"] = tv
    order = d.get("order", 100)
    if isinstance(order, bool) or not isinstance(order, int) or not 0 <= order <= 1000:
        raise ManifestError("order must be a number from 0 to 1000")
    out["order"] = order
    perms = d.get("permissions", [])
    if not isinstance(perms, list) or len(perms) > 16:
        raise ManifestError("permissions must be a short list")
    out["permissions"] = []
    for p in perms:
        if not isinstance(p, dict) or p.get("kind") not in PERMISSION_KINDS:
            raise ManifestError("a permission needs a kind: " + ", ".join(PERMISSION_KINDS))
        out["permissions"].append({"kind": p["kind"], "detail": _text(p, "detail", 80, required=False)})
    if "author" in d:
        out["author"] = _text(d, "author", 60, required=False)
    return out


def read(folder):
    """The validated manifest of an app folder (app.json next to app.py)."""
    p = os.path.join(folder, "app.json")
    try:
        if os.path.getsize(p) > MAX_MANIFEST_BYTES:
            raise ManifestError("app.json is too large")
        with open(p) as f:
            d = json.load(f)
    except OSError:
        raise ManifestError("no app.json") from None
    except ValueError:
        raise ManifestError("app.json is not valid JSON") from None
    m = validate(d)
    if not os.path.isfile(os.path.join(folder, "app.py")):
        raise ManifestError("no app.py")
    return m


def icon_name(manifest):
    """The icon name to use in ui: a custom picture is registered as app:<id> the first time."""
    icon = manifest["icon"]
    if isinstance(icon, str):
        return icon
    name = "app:" + manifest["id"]
    ui.ICONS[name] = (icon["colour"], list(icon["rows"]))
    return name


def entry(folder, manifest, source):
    """A launcher menu entry for an app folder."""
    return {"id": manifest["id"], "name": manifest["name"], "desc": manifest["description"], "icon": icon_name(manifest), "dir": folder,
            "cmd": ["python3", "app.py", "--profile", "{profile}"], "kind": "app", "version": manifest["version"], "source": source,
            "order": manifest["order"], "permissions": manifest["permissions"]}
