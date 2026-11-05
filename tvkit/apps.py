"""Installing and managing apps: packages, repositories and the installer. Standard library only.

A package is a zip with app.json and app.py at its root (see tvkit/manifest.py). A repository is any place that serves an index.json:

    {"name": "My apps", "apps": [{"id": "weather", "name": "WEATHER", "version": "1.0.0", "description": "...", "icon": "info",
                                   "url": "weather-1.0.0.zip", "sha256": "<64 hex>", "size": 12345, "tvkit": 1, "permissions": [...]}]}

`url` may be relative to the index. tools/make_repo.py builds such a folder from apps on disk.

What the installer guarantees (and what it does not):
  * the download must match the SHA-256 in the index, and its manifest must match what the index promised;
  * the zip is checked before anything is written: size, file count, unpacked size, compression ratio, no absolute or ../ paths,
    no hidden files, no symlinks or special files, app.json valid, app.py present;
  * every .py file must compile (a syntax error is refused, nothing is imported or run during installation);
  * the app is unpacked into a temporary folder and moved into place in one step; an update keeps the previous version for rollback;
  * an installed app cannot take the id of a built-in or external app.
It does NOT sandbox the app: an installed app runs with the same rights as the launcher. The hash only proves the file is the one the index named,
so it is only as trustworthy as the index and the connection it came over (use https). Only install from repositories you trust.
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import stat
import threading
import time
import zipfile
from urllib.parse import urljoin, urlparse

from . import manifest, net

MAX_PACKAGE = 5 * 1024 * 1024
MAX_UNPACKED = 20 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024
MAX_FILES = 200
MAX_RATIO = 100                       # an unpacked file may be at most this many times its packed size (zip bombs)
MAX_INDEX = 1024 * 1024
MAX_INDEX_APPS = 200
RESERVED_IDS = {"launcher", "remote", "reboot", "poweroff", "settings", "system", "store"}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")


class AppsError(Exception):
    """A problem the user can read: it names what is wrong, never a path outside the apps folder or a secret."""


# ---- where things live ----
def user_apps_dir():
    return os.environ.get("TVL_USER_APPS") or os.path.expanduser("~/.local/share/tv-launcher/apps")


def backup_dir():
    return os.path.join(os.path.dirname(user_apps_dir().rstrip("/")), "apps-backup")


def data_dir():
    return os.environ.get("TVL_DATA_DIR") or os.path.expanduser("~/.local/share/tv-launcher/data")


# ---- reading from a repository: http(s), file:// or a folder on disk ----
def _is_http(url):
    return url.startswith(("http://", "https://"))


def read_source(url, limit):
    try:
        if _is_http(url):
            data = net.request(url, timeout=20, limit=limit + 1)
            if len(data) > limit:
                raise AppsError("the file is larger than allowed")
            return data
        path = os.path.expanduser(url[7:] if url.startswith("file://") else url)
        with open(path, "rb") as f:
            data = f.read(limit + 1)
    except net.NetError as ex:
        raise AppsError(f"could not download ({ex})") from None
    except OSError as ex:
        raise AppsError(f"could not read the file ({ex.strerror or 'error'})") from None
    if len(data) > limit:
        raise AppsError("the file is larger than allowed")
    return data


def index_location(url):
    if _is_http(url):
        return url if url.endswith(".json") else url.rstrip("/") + "/index.json"
    path = os.path.expanduser(url[7:] if url.startswith("file://") else url)
    return path if path.endswith(".json") else os.path.join(path, "index.json")


def _resolve(index_loc, ref):
    """The place a package can be read from: absolute http(s) as given, anything else relative to the index and kept inside its folder."""
    if not isinstance(ref, str) or not ref:
        raise AppsError("a package address is missing")
    if _is_http(ref):
        return ref
    if "\0" in ref or ref.startswith("//") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", ref):
        raise AppsError("a package address must be http(s) or a relative path")
    if _is_http(index_loc):
        out = urljoin(index_loc, ref)
        if not _is_http(out):
            raise AppsError("a package address must be http(s) or a relative path")
        return out
    base = os.path.dirname(os.path.abspath(index_loc))
    out = os.path.normpath(os.path.join(base, ref))
    if out != base and not out.startswith(base + os.sep):
        raise AppsError("a package path must stay inside the repository folder")
    return out


def parse_index(data, location, repo_name=None):
    """{"name", "apps": [entry], "skipped": [str]} from a decoded index.json. An entry the launcher cannot use is skipped and named, not fatal."""
    if not isinstance(data, dict) or not isinstance(data.get("apps"), list):
        raise AppsError("this does not look like a repository (no list of apps)")
    entries, skipped = [], []
    for raw in data["apps"][:MAX_INDEX_APPS]:
        label = raw.get("id", "?") if isinstance(raw, dict) else "?"
        try:
            if not isinstance(raw, dict):
                raise AppsError("not an object")
            m = manifest.validate({k: raw.get(k) for k in ("id", "name", "version", "description", "icon", "tvkit", "permissions", "author") if k in raw})
            sha = raw.get("sha256")
            size = raw.get("size")
            if not isinstance(sha, str) or not SHA_RE.match(sha):
                raise AppsError("sha256 missing or not 64 hex characters")
            if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_PACKAGE:
                raise AppsError("size missing or too large")
            entries.append({**{k: m[k] for k in ("id", "name", "version", "description", "icon", "tvkit", "permissions")}, "author": m.get("author", ""),
                            "sha256": sha, "size": size, "package": _resolve(location, raw.get("url")), "repo": repo_name or str(data.get("name") or "")[:40]})
        except (manifest.ManifestError, AppsError) as ex:
            skipped.append(f"{label}: {ex}")
    return {"name": str(data.get("name") or repo_name or "")[:40], "apps": entries, "skipped": skipped}


def fetch_index(repo):
    """repo: {"name", "url"}. Returns the parsed index; raises AppsError with a readable reason."""
    loc = index_location(repo["url"])
    raw = read_source(loc, MAX_INDEX)
    try:
        data = json.loads(raw)
    except ValueError:
        raise AppsError("the repository's index.json is not valid JSON") from None
    return parse_index(data, loc, repo.get("name"))


class Store:
    """The repositories' indexes, cached for a minute so the web page can reload cheaply. `repos` is a function returning [{name, url}]."""

    def __init__(self, repos, ttl=60, clock=time.time):
        self.repos, self.ttl, self.clock = repos, ttl, clock
        self.cache, self.lock = {}, threading.Lock()

    def refresh(self):
        with self.lock:
            self.cache.clear()

    def _index(self, repo):
        key = (repo["name"], repo["url"])
        with self.lock:
            hit = self.cache.get(key)
            if hit and self.clock() - hit[0] < self.ttl:
                return hit[1]
        try:
            result = {"ok": True, **fetch_index(repo)}
        except AppsError as ex:
            result = {"ok": False, "error": str(ex), "apps": [], "skipped": []}
        with self.lock:
            self.cache[key] = (self.clock(), result)
        return result

    def catalog(self):
        out = []
        for repo in self.repos():
            idx = self._index(repo)
            out.append({"name": repo["name"], "url": repo["url"], "ok": idx["ok"], "error": idx.get("error", ""), "count": len(idx["apps"]), "skipped": idx["skipped"], "apps": idx["apps"]})
        return out

    def find(self, repo_name, app_id):
        for r in self.catalog():
            if r["name"] == repo_name:
                return next((a for a in r["apps"] if a["id"] == app_id), None)
        return None


# ---- looking inside a package (nothing is written) ----
def _safe_member(name):
    if not name or len(name) > 120 or "\0" in name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise AppsError("the package has an unsafe file name")
    parts = name.rstrip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise AppsError("the package has an unsafe file name")
    if any(p.startswith(".") for p in parts):
        raise AppsError("the package contains hidden files")


def inspect_package(data):
    """(manifest, ZipFile, [ZipInfo of files]) after every check that needs no writing. Raises AppsError."""
    if len(data) > MAX_PACKAGE:
        raise AppsError("the package is larger than allowed")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise AppsError("the download is not a zip file") from None
    infos = z.infolist()
    if not infos or len(infos) > MAX_FILES:
        raise AppsError(f"a package needs 1 to {MAX_FILES} files")
    files, total, seen = [], 0, set()
    for i in infos:
        _safe_member(i.filename)
        if i.filename in seen:
            raise AppsError("the package lists a file twice")
        seen.add(i.filename)
        kind = stat.S_IFMT(i.external_attr >> 16)                    # the file-type bits; zero when the tool that made the zip did not set them
        if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
            raise AppsError("the package contains a symbolic link or special file")
        if i.flag_bits & 0x1:
            raise AppsError("the package is encrypted")
        if i.is_dir():
            continue
        if i.file_size > MAX_FILE:
            raise AppsError("a file in the package is too large")
        total += i.file_size
        if i.file_size > 100_000 and i.compress_size and i.file_size / i.compress_size > MAX_RATIO:
            raise AppsError("the package looks like a zip bomb")
        files.append(i)
    if total > MAX_UNPACKED:
        raise AppsError("the package unpacks to more than allowed")
    names = {i.filename for i in files}
    if "app.json" not in names or "app.py" not in names:
        raise AppsError("a package needs app.json and app.py at its top level")
    meta = next(i for i in files if i.filename == "app.json")
    if meta.file_size > manifest.MAX_MANIFEST_BYTES:
        raise AppsError("app.json is too large")
    try:
        m = manifest.validate(json.loads(z.read(meta)))
    except manifest.ManifestError as ex:                              # first: ManifestError is a ValueError too
        raise AppsError(f"app.json: {ex}") from None
    except ValueError:
        raise AppsError("app.json is not valid JSON") from None
    for info in files:                                                # a package that cannot even compile is refused here, before anyone is asked to approve it
        if info.filename.endswith(".py"):
            try:
                compile(z.read(info), info.filename, "exec")
            except (SyntaxError, ValueError) as ex:
                raise AppsError(f"{info.filename} has a syntax error ({type(ex).__name__}); nothing was installed") from None
    return m, z, files


def verify_hash(data, expected):
    if not isinstance(expected, str) or not SHA_RE.match(expected) or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected):
        raise AppsError("the download does not match the checksum in the repository: it was changed or damaged")


def installed():
    """[manifest + {"folder"}] of the apps in the user's apps folder."""
    out = []
    try:
        names = sorted(os.listdir(user_apps_dir()))
    except OSError:
        return out
    for n in names:
        folder = os.path.join(user_apps_dir(), n)
        if n.startswith((".", "_")) or not os.path.isdir(folder):
            continue
        try:
            out.append({**manifest.read(folder), "folder": folder})
        except manifest.ManifestError:
            continue
    return out


def check_install(data, reserved_ids, expect=None):
    """What installing this package would do, or AppsError. expect: the index entry it must match (id and version)."""
    m, _, _ = inspect_package(data)
    if m["id"] in reserved_ids or m["id"] in RESERVED_IDS:
        raise AppsError(f"{m['id']} is the name of a built-in app and cannot be replaced")
    if expect and (m["id"] != expect["id"] or m["version"] != expect["version"]):
        raise AppsError("the package is not the app the repository promised (id or version differ)")
    cur = next((a for a in installed() if a["id"] == m["id"]), None)
    action = "install"
    if cur:
        if not manifest.newer(m["version"], cur["version"]):
            raise AppsError(f"{m['name']} {cur['version']} is already installed")
        action = "update"
    return {"manifest": m, "action": action, "from": cur["version"] if cur else None, "size": len(data)}


# ---- changing the apps folder ----
def _move_aside(final, bak):
    shutil.rmtree(bak, ignore_errors=True)
    os.makedirs(os.path.dirname(bak), mode=0o700, exist_ok=True)
    shutil.move(final, bak)


def install_package(data, reserved_ids, expect=None):
    """Check, unpack to a temporary folder, compile-check, then move into place in one step. Returns {"id", "version", "action", "from"}."""
    plan = check_install(data, reserved_ids, expect)
    m = plan["manifest"]
    _, z, files = inspect_package(data)
    base = user_apps_dir()
    os.makedirs(base, mode=0o755, exist_ok=True)
    final, tmp = os.path.join(base, m["id"]), os.path.join(base, f".tmp-{m['id']}-{secrets.token_hex(4)}")
    os.makedirs(tmp, mode=0o755)
    try:
        written = 0
        for info in files:
            dest = os.path.join(tmp, *info.filename.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            left = info.file_size
            with z.open(info) as src, open(dest, "wb") as out:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    left -= len(chunk)
                    written += len(chunk)
                    if left < 0 or written > MAX_UNPACKED:
                        raise AppsError("a file in the package is larger than it says")
                    out.write(chunk)
            if left != 0:
                raise AppsError("a file in the package is smaller than it says")
            os.chmod(dest, 0o644)
        had = os.path.isdir(final)
        bak = os.path.join(backup_dir(), m["id"])
        if had:
            _move_aside(final, bak)
        try:
            os.rename(tmp, final)
        except OSError:
            if had:
                shutil.move(bak, final)                           # put the old version back
            raise AppsError("could not move the app into place")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"id": m["id"], "name": m["name"], "version": m["version"], "action": plan["action"], "from": plan["from"]}


def remove(app_id, purge=False):
    """Take an installed app out (its folder is kept in the backup folder so it can be restored). purge also deletes its data."""
    if not manifest.ID_RE.match(app_id or ""):
        raise AppsError("not an app id")
    final = os.path.join(user_apps_dir(), app_id)
    if not os.path.isdir(final):
        raise AppsError(f"{app_id} is not an installed app (built-in apps cannot be removed)")
    _move_aside(final, os.path.join(backup_dir(), app_id))
    if purge:
        shutil.rmtree(os.path.join(data_dir(), app_id), ignore_errors=True)
    return {"id": app_id}


def rollback(app_id):
    """Swap the installed version with the one kept from before the last update or removal."""
    if not manifest.ID_RE.match(app_id or ""):
        raise AppsError("not an app id")
    bak, final = os.path.join(backup_dir(), app_id), os.path.join(user_apps_dir(), app_id)
    if not os.path.isdir(bak):
        raise AppsError("there is no earlier version to go back to")
    try:
        old = manifest.read(bak)
    except manifest.ManifestError:
        raise AppsError("the saved earlier version is damaged") from None
    tmp = bak + ".swap"
    shutil.rmtree(tmp, ignore_errors=True)
    if os.path.isdir(final):
        shutil.move(final, tmp)
    shutil.move(bak, final)
    if os.path.isdir(tmp):
        shutil.move(tmp, bak)
    return {"id": app_id, "version": old["version"]}


def updates(entries):
    """Index entries that are newer than the installed version of the same app: [(entry, installed_version)]."""
    have = {a["id"]: a["version"] for a in installed()}
    return [(e, have[e["id"]]) for e in entries if e["id"] in have and manifest.newer(e["version"], have[e["id"]])]


def download_and_check(entry, reserved_ids):
    """Fetch an index entry's package, verify its hash and size, and check it. Returns (data, plan). Raises AppsError."""
    data = read_source(entry["package"], MAX_PACKAGE)
    if len(data) != entry["size"]:
        raise AppsError("the download is not the size the repository promised")
    verify_hash(data, entry["sha256"])
    return data, check_install(data, reserved_ids, expect=entry)


def describe_permissions(perms):
    return [f"{p['kind']}: {p['detail']}" if p.get("detail") else p["kind"] for p in perms]
