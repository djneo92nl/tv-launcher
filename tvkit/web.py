"""The web interface: remote control, apps, settings and system pages for a phone on the same network. Standard library only, one thread per request.

Security model (this server can change settings and, later, install code, so it is much stricter than the TV clients' own remote):
  * Pairing: the TV shows a 6-digit PIN (valid 5 minutes, single use). POST /api/pair is rate limited per address and locks everyone out for 3 minutes after
    10 wrong tries. A paired phone gets a random 256-bit token as an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored (devices.json, mode 0600).
  * Every API call needs that cookie. Every POST also needs the X-TVL header and a JSON body (a page on another site cannot send either without a CORS
    preflight, and this server never answers one).
  * The Host header must be this machine (an IP address, localhost, its host name or *.local), which stops DNS-rebinding attacks; a present Origin must match Host.
  * Strict Content-Security-Policy (scripts and styles only from this server), no-store, nosniff. The page builds the screen with textContent, never innerHTML.
  * Risky actions (shutdown, restart, installing or removing apps) are not done on the phone's word alone: the TV shows a dialog and waits for OK (see Confirmations).
  * No TLS: the PIN and cookie cross the LAN in clear text. Keep it on a network you trust; do not forward the port.

The host (the launcher) supplies the data; see the Host notes below.
"""
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import threading
import time

from . import control, net, settings
from . import webpage

PIN_TTL_S, PIN_TRIES, PIN_WINDOW_S, LOCK_AFTER, LOCK_S = 300, 5, 60, 10, 180
MAX_BODY = 32_768
MAX_DEVICES = 20
COOKIE = "tvl"
CONFIRM_TTL_S = 60


def devices_path():
    return os.environ.get("TVL_DEVICES") or os.path.expanduser("~/.config/tv-launcher/devices.json")


def lan_ip():
    """This machine's address on the network (a UDP "connect" sends nothing; it only asks the kernel which interface it would use)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# ---- paired devices ----
class Devices:
    """{id: {name, hash, created, last_seen}} in a 0600 file. Only the SHA-256 of a token is stored: a stolen file cannot be used to log in."""

    def __init__(self, path=None):
        self.path, self.lock = path or devices_path(), threading.Lock()
        data = net.read_json(self.path, {})
        self.data = data if isinstance(data, dict) else {}

    def _save(self):
        net.write_private(self.path, self.data)

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def add(self, name):
        name = (name or "Phone").strip()[:40] or "Phone"
        token = secrets.token_urlsafe(32)
        with self.lock:
            if len(self.data) >= MAX_DEVICES:                       # forget the one not seen for longest
                oldest = min(self.data, key=lambda k: self.data[k].get("last_seen", 0))
                del self.data[oldest]
            did = secrets.token_hex(4)
            self.data[did] = {"name": "".join(c for c in name if c.isprintable()), "hash": self._hash(token), "created": int(time.time()), "last_seen": int(time.time())}
            self._save()
        return did, token

    def check(self, token):
        """The device id for a token, or None. The token is 256 random bits, so looking its hash up directly leaks nothing useful."""
        if not token or len(token) > 100:
            return None
        h = self._hash(token)
        with self.lock:
            for did, d in self.data.items():
                if hmac.compare_digest(d["hash"], h):
                    if time.time() - d.get("last_seen", 0) > 60:         # note use, but do not write the file on every request
                        d["last_seen"] = int(time.time())
                        self._save()
                    return did
        return None

    def name(self, did):
        return self.data.get(did, {}).get("name", "")

    def list(self):
        with self.lock:
            return [{"id": k, "name": v["name"], "created": v["created"], "last_seen": v["last_seen"]} for k, v in sorted(self.data.items(), key=lambda kv: -kv[1]["last_seen"])]

    def revoke(self, did):
        with self.lock:
            if did not in self.data:
                return False
            del self.data[did]
            self._save()
            return True


# ---- the PIN ----
class Pairing:
    def __init__(self, clock=time.time):
        self.clock, self.lock = clock, threading.Lock()
        self.pin, self.expires = None, 0
        self.tries = {}                    # address -> [timestamps]
        self.fails, self.locked_until = [], 0
        self.last_success = None           # (name, time) for the TV to say "paired"

    def open(self):
        """A fresh PIN, shown on the TV. Returns (pin, expires_at)."""
        with self.lock:
            self.pin, self.expires = "%06d" % secrets.randbelow(1_000_000), self.clock() + PIN_TTL_S
            return self.pin, self.expires

    def close(self):
        with self.lock:
            self.pin = None

    def is_open(self):
        return bool(self.pin) and self.clock() < self.expires

    def attempt(self, address, pin):
        """"ok", "wrong", "closed", "slow" (too many tries from this address) or "locked" (everyone, for a while)."""
        now = self.clock()
        with self.lock:
            if now < self.locked_until:
                return "locked"
            recent = [t for t in self.tries.get(address, []) if now - t < PIN_WINDOW_S]
            if len(recent) >= PIN_TRIES:
                self.tries[address] = recent
                return "slow"
            recent.append(now)
            self.tries[address] = recent
            if not self.pin or now >= self.expires:
                return "closed"
            if isinstance(pin, str) and hmac.compare_digest(pin.encode(), self.pin.encode()):
                self.pin, self.fails = None, []
                return "ok"
            self.fails = [t for t in self.fails if now - t < LOCK_S] + [now]
            if len(self.fails) >= LOCK_AFTER:
                self.locked_until, self.fails, self.pin = now + LOCK_S, [], None
                return "locked"
            return "wrong"


# ---- confirmations on the TV ----
class Confirmations:
    """Actions that wait for OK on the TV. The launcher shows `current()` and calls answer(); the phone polls state()."""

    def __init__(self, clock=time.time):
        self.clock, self.lock, self.items, self.order = clock, threading.Lock(), {}, []

    def request(self, title, lines, action):
        cid = secrets.token_hex(6)
        with self.lock:
            self.items[cid] = {"id": cid, "title": title, "lines": list(lines), "action": action, "state": "pending", "t": self.clock()}
            self.order.append(cid)
            for old in self.order[:-20]:                              # keep the table small
                self.items.pop(old, None)
            self.order = self.order[-20:]
        return cid

    def _expire(self):
        for it in self.items.values():
            if it["state"] == "pending" and self.clock() - it["t"] > CONFIRM_TTL_S:
                it["state"] = "expired"

    def current(self):
        with self.lock:
            self._expire()
            return next((it for it in (self.items[c] for c in self.order if c in self.items) if it["state"] == "pending"), None)

    def answer(self, cid, allow):
        """Run the action when allowed (in its own thread, so the TV's loop is never held up). True if this answer settled a pending request."""
        with self.lock:
            self._expire()
            it = self.items.get(cid)
            if not it or it["state"] != "pending":
                return False
            it["state"] = "allowed" if allow else "denied"
            action = it["action"] if allow else None
        if action:
            threading.Thread(target=action, daemon=True, name="confirmed-action").start()
        return True

    def state(self, cid):
        with self.lock:
            self._expire()
            it = self.items.get(cid)
            return {"state": it["state"], "title": it["title"]} if it else None


# ---- the server ----
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "TVLauncher"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # -- plumbing --
    @property
    def web(self):
        return self.server.web

    def send_bytes(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", webpage.CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, code, obj, extra=None):
        self.send_bytes(code, json.dumps(obj).encode(), "application/json", extra)

    def fail(self, code, message):
        self.send_json(code, {"ok": False, "error": message})

    def host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        if not name:
            return False
        if re.fullmatch(r"\[?[0-9a-f:.]+\]?", name):                              # an IP address
            try:
                ipaddress.ip_address(name.strip("[]"))
                return True
            except ValueError:
                return False
        return name == "localhost" or name.endswith(".local") or name == socket.gethostname().lower() or name == socket.gethostname().lower().split(".")[0]

    def origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        m = re.fullmatch(r"https?://([^/]+)", origin.strip().lower())
        return bool(m) and m.group(1) == (self.headers.get("Host") or "").strip().lower()

    def device(self):
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return self.web.devices.check(v)
        return None

    def body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "bad length"
        if n > MAX_BODY:
            return None, "too large"
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return None, "JSON only"
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None, "not JSON"
        return (d, None) if isinstance(d, dict) else (None, "not an object")

    # -- verbs --
    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_OPTIONS(self):                                                         # no CORS: another site's page gets nothing from us
        self.fail(405, "not allowed")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS

    def route(self, method):
        if not self.host_ok() or not self.origin_ok():
            return self.fail(403, "forbidden")
        path = self.path.split("?", 1)[0]
        if method == "GET" and path in webpage.ASSETS:
            body, ctype = webpage.ASSETS[path]
            return self.send_bytes(200, body, ctype)
        if not path.startswith("/api/"):
            return self.fail(404, "not found")
        if method == "POST" and self.headers.get("X-TVL") != "1":
            return self.fail(403, "forbidden")
        body = {}
        if method == "POST":
            body, err = self.body()
            if err:
                return self.fail(413 if err == "too large" else 400, err)
        if path == "/api/session" and method == "GET":
            did = self.device()
            return self.send_json(200, {"paired": bool(did), "name": self.web.devices.name(did) if did else "", "server": self.web.host.name})
        if path == "/api/pair" and method == "POST":
            return self.pair(body)
        did = self.device()
        if not did:
            return self.fail(401, "not paired")
        h = self.web.handlers.get((method, path))
        if h is None:
            if method == "GET" and path.startswith("/api/confirm/"):
                st = self.web.confirmations.state(path.rsplit("/", 1)[1])
                return self.send_json(200, st) if st else self.fail(404, "no such request")
            return self.fail(404, "not found")
        try:
            code, obj = h(self, did, body)
        except Exception:
            self.web.log.exception("web handler failed: %s %s", method, path)
            return self.fail(500, "something went wrong")
        self.send_json(code, obj)

    def pair(self, body):
        result = self.web.pairing.attempt(self.client_address[0], body.get("pin"))
        if result == "ok":
            did, token = self.web.devices.add(body.get("name") if isinstance(body.get("name"), str) else "Phone")
            self.web.pairing.last_success = (self.web.devices.name(did), time.time())
            cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={60 * 60 * 24 * 365}"
            return self.send_json(200, {"ok": True}, {"Set-Cookie": cookie})
        messages = {"wrong": "That code is not right.", "closed": "Open REMOTE on the TV first, then enter the code it shows.",
                    "slow": "Too many tries. Wait a minute.", "locked": "Too many wrong codes. Try again in a few minutes."}
        return self.fail(429 if result in ("slow", "locked") else 403, messages[result])


class WebServer:
    """Owns the stores and the HTTP server. `host` is the launcher (or a stand-in in tests); it provides:
        name                      shown in the page
        apps() -> [dict]          id, name, desc, icon, version, source, hidden
        status() -> dict          the launcher's own status (including "running": {id, name} or None)
        foreground() -> str       which control socket the remote keys go to: the running app's id, else "launcher"
        start_app(id) -> bool
        settings_values() -> {key: value};  settings_changed(keys)
        confirm(title, lines, action) -> id   (bring the TV to a place where the dialog can be shown)
        system(action)            "restart" | "poweroff": called only after OK on the TV
        log_tail(n) -> [str]
    """

    def __init__(self, host, port=8090, bind="0.0.0.0", devices=None, pairing=None, confirmations=None, log=None):
        import logging
        self.host, self.log = host, log or logging.getLogger("web")
        self.devices, self.pairing, self.confirmations = devices or Devices(), pairing or Pairing(), confirmations or Confirmations()
        self.handlers = {("GET", "/api/status"): self.h_status, ("POST", "/api/key"): self.h_key, ("POST", "/api/text"): self.h_text, ("POST", "/api/home"): self.h_home,
                         ("GET", "/api/apps"): self.h_apps, ("POST", "/api/apps/start"): self.h_start, ("GET", "/api/settings"): self.h_settings_get,
                         ("POST", "/api/settings"): self.h_settings_set, ("GET", "/api/devices"): self.h_devices, ("POST", "/api/devices/revoke"): self.h_revoke,
                         ("POST", "/api/logout"): self.h_logout, ("POST", "/api/system"): self.h_system, ("GET", "/api/log"): self.h_log}
        self.httpd = http.server.ThreadingHTTPServer((bind, port), Handler)
        self.httpd.daemon_threads = True
        self.httpd.web = self
        self.port = self.httpd.server_address[1]
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.05), daemon=True, name="web")
        self.thread.start()

    def stop(self):
        if self.thread:                              # shutdown() waits for serve_forever, so only ask when it is running
            self.httpd.shutdown()
        self.httpd.server_close()

    def url(self):
        return f"http://{lan_ip()}:{self.port}"

    # -- handlers: (handler, device id, body) -> (status, object) --
    def h_status(self, h, did, body):
        st = self.host.status()
        running = st.get("running")
        app = control.ask(running["id"], {"cmd": "status"}).get("status") if running else None
        return 200, {"launcher": st, "app": app, "server": self.host.name, "device": self.devices.name(did)}

    def h_key(self, h, did, body):
        name = body.get("name")
        if not isinstance(name, str) or name not in control.KEYS:
            return 400, {"ok": False, "error": "unknown key"}
        r = control.ask(self.host.foreground(), {"cmd": "key", "name": name})
        return (200, r) if r.get("ok") else (409, r)

    def h_text(self, h, did, body):
        text = body.get("text")
        if not isinstance(text, str):
            return 400, {"ok": False, "error": "text must be a string"}
        r = control.ask(self.host.foreground(), {"cmd": "text", "text": text})
        return (200, r) if r.get("ok") else (409, r)

    def h_home(self, h, did, body):
        st = self.host.status()
        if st.get("running"):
            control.ask(st["running"]["id"], {"cmd": "quit"})
        return 200, {"ok": True}

    def h_apps(self, h, did, body):
        return 200, {"apps": self.host.apps()}

    def h_start(self, h, did, body):
        app_id = body.get("id")
        if not isinstance(app_id, str) or not self.host.start_app(app_id):
            return 404, {"ok": False, "error": "no such app"}
        return 200, {"ok": True}

    def h_settings_get(self, h, did, body):
        return 200, {"schema": settings.schema(), "values": self.host.settings_values()}

    def h_settings_set(self, h, did, body):
        changes = body.get("changes")
        if not isinstance(changes, dict) or not changes:
            return 400, {"ok": False, "error": "nothing to change"}
        try:
            cleaned = settings.set_many(changes, known_apps=self.host.known_app_ids())
        except settings.SettingsError as ex:
            return 400, {"ok": False, "error": str(ex)}
        self.host.settings_changed(list(cleaned))
        return 200, {"ok": True, "values": self.host.settings_values()}

    def h_devices(self, h, did, body):
        return 200, {"devices": [{**d, "this": d["id"] == did} for d in self.devices.list()]}

    def h_revoke(self, h, did, body):
        target = body.get("id")
        return (200, {"ok": True}) if isinstance(target, str) and self.devices.revoke(target) else (404, {"ok": False, "error": "no such device"})

    def h_logout(self, h, did, body):
        self.devices.revoke(did)
        return 200, {"ok": True}

    def h_system(self, h, did, body):
        action = body.get("action")
        if action not in ("restart", "poweroff"):
            return 400, {"ok": False, "error": "unknown action"}
        label = "Restart" if action == "restart" else "Shut down"
        cid = self.host.confirm(f"{label} this device?", [f"Asked from {self.devices.name(did)}."], lambda: self.host.system(action))
        return 202, {"ok": True, "confirm": cid}

    def h_log(self, h, did, body):
        return 200, {"lines": self.host.log_tail(60)}
