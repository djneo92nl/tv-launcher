"""Small HTTP helpers (standard library): JSON / bytes with a timeout, a size limit and a fixed User-Agent; private files for tokens.
TLS is verified (urllib's default context). Errors raise NetError with a short message that never contains the URL (it may carry a token)."""
import json
import os
import urllib.error
import urllib.request

UA = "tv-launcher/0.1 (personal, non-commercial)"


class NetError(Exception):
    pass


def request(url, data=None, headers=None, method=None, timeout=15, limit=20_000_000):
    h = {"User-Agent": UA, **(headers or {})}
    if isinstance(data, (dict, list)):
        data, h["Content-Type"] = json.dumps(data).encode(), "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit)
    except urllib.error.HTTPError as ex:
        raise NetError(f"server said {ex.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as ex:
        raise NetError(type(ex).__name__) from None


def get_json(url, **kw):
    try:
        return json.loads(request(url, **kw) or b"null")
    except ValueError:
        raise NetError("not JSON") from None


def write_private(path, data):
    """Write JSON to `path` with mode 0600 in a 0700 folder (tokens, logins)."""
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
