"""A small control socket for every app, so the web interface (running in the launcher) can press keys in an app and read what it shows.

The socket is a Unix socket, mode 0600, in a private folder (mode 0700): only the user who runs the launcher can talk to it. Line-delimited JSON, one request per line:

    {"cmd": "key", "name": "up"}          # one of KEYS: the same names the keyboard and the remote produce
    {"cmd": "text", "text": "blue hour"}  # typed into an on-screen keyboard (printable ASCII, up to 64 characters)
    {"cmd": "status"}                     # -> {"ok": true, "status": {...}} what the app shows (see Shell.status)
    {"cmd": "quit"}                       # leave the app (back to the launcher)
    {"cmd": "do", "action": "pair"}       # ask the socket's owner to do one of the actions it offers (the launcher: pair, restart, poweroff)

Nothing here can run code or read files: the vocabulary is closed.
"""
import hashlib
import json
import os
import socket
import threading

KEYS = {"up", "down", "left", "right", "enter", "back", "info", "menu", "captions", "last", "volup", "voldown", "mute", "quit",
        "red", "green", "yellow", "blue", "guide", "now", "text", "sleep", "bar", "details", "favourite", "hide", "testcard", "wheel_up", "wheel_down"} | {f"digit:{d}" for d in range(10)}
MAX_LINE = 4096
MAX_TEXT = 64


def run_dir():
    """The private folder for the sockets: $TVL_STATE_DIR/run (tests), $XDG_RUNTIME_DIR/tv-launcher, else ~/.cache/tv-launcher/run."""
    base = os.environ.get("TVL_STATE_DIR")
    d = os.path.join(base, "run") if base else os.path.join(os.environ["XDG_RUNTIME_DIR"], "tv-launcher") if os.environ.get("XDG_RUNTIME_DIR") else os.path.expanduser("~/.cache/tv-launcher/run")
    if len(os.path.join(d, "x" * 24 + ".sock")) > 100:                         # Unix socket paths are limited to about 104 characters
        d = os.path.join("/tmp", f"tvl-{os.getuid()}-{hashlib.sha1(d.encode()).hexdigest()[:8]}")
    return d


def socket_path(name):
    return os.path.join(run_dir(), name + ".sock")


def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def text_to_keys(text):
    """The `char:` commands for a string typed on the phone; control characters and non-ASCII are dropped."""
    out = []
    for ch in text[:MAX_TEXT]:
        if ch == " ":
            out.append("char:space")
        elif 32 < ord(ch) < 127:
            out.append("char:" + ch)
    return out


class ControlServer:
    """Serves one app's socket from a thread. `shell` needs: q (a queue.Queue), status() and quit()."""

    def __init__(self, shell, name):
        self.shell, self.name = shell, name
        self.path = socket_path(name)
        self._sock, self._stop = None, threading.Event()

    def start(self):
        private_dir(os.path.dirname(self.path))
        try:
            os.unlink(self.path)                                # a stale socket from a crashed run
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o177)                                    # created 0600: not even briefly open to others
        try:
            s.bind(self.path)
        finally:
            os.umask(old)
        s.listen(64)
        s.settimeout(0.5)
        self._sock = s
        threading.Thread(target=self._serve, daemon=True, name="control-" + self.name).start()

    def stop(self):
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        conn.settimeout(3)
        try:
            buf = b""
            while b"\n" not in buf and len(buf) <= MAX_LINE:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                buf += chunk
            line = buf.split(b"\n", 1)[0]
            reply = self.handle(line) if len(buf) <= MAX_LINE else {"ok": False, "error": "request too long"}
            conn.sendall((json.dumps(reply) + "\n").encode())
        except OSError:
            pass
        finally:
            conn.close()

    def handle(self, line):
        try:
            req = json.loads(line)
        except ValueError:
            return {"ok": False, "error": "not JSON"}
        if not isinstance(req, dict):
            return {"ok": False, "error": "not an object"}
        cmd = req.get("cmd")
        if cmd == "key":
            name = req.get("name")
            if not isinstance(name, str) or name not in KEYS:
                return {"ok": False, "error": "unknown key"}
            self.shell.q.put(("cmd", name))
            return {"ok": True}
        if cmd == "text":
            text = req.get("text")
            if not isinstance(text, str):
                return {"ok": False, "error": "text must be a string"}
            for k in text_to_keys(text):
                self.shell.q.put(("cmd", k))
            return {"ok": True}
        if cmd == "status":
            try:
                return {"ok": True, "status": self.shell.status()}
            except Exception:
                return {"ok": False, "error": "status failed"}
        if cmd == "quit":
            self.shell.q.put(("cmd", "quit"))
            return {"ok": True}
        if cmd == "do":
            action, offered = req.get("action"), getattr(self.shell, "actions", ())
            if not isinstance(action, str) or action not in offered:
                return {"ok": False, "error": "no such action"}
            try:
                return {"ok": bool(self.shell.do_action(action))}
            except Exception:
                return {"ok": False, "error": "action failed"}
        return {"ok": False, "error": "unknown command"}


def ask(name, request, timeout=2.0):
    """Send one request to an app's socket and return its reply dict, or {"ok": False, "error": ...} when it is not running."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(socket_path(name))
        s.sendall((json.dumps(request) + "\n").encode())
        buf = b""
        while b"\n" not in buf and len(buf) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.split(b"\n", 1)[0] or b"{}")
    except (OSError, ValueError):
        return {"ok": False, "error": "not running"}
    finally:
        s.close()
