#!/usr/bin/env python3
"""A small pretend ReMoment server for tests and demos: python3 tools/fake_remoment.py [--port 8099] [--audio file.mp3]
Serves the shapes of ReMomentServer/docs/api (info, albums, artists, playlists, search, tiny BMP covers, /stream/{id})."""
import argparse
import json
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
import io
import math

ARTISTS = [{"id": i, "name": n} for i, n in enumerate(["The Aeolians", "Blue Hour", "Cassini", "Dust & Echo", "Evening Choir"], 1)]
ALBUM_NAMES = ["Absolution", "Blue Monday", "Carbon", "Daydream", "Echoes", "Fever", "Glass", "Harbour", "Indigo", "Juniper", "Kestrel", "Lantern"]
ALBUMS = [{"id": i, "name": n, "artist": ARTISTS[(i - 1) % 5], "year": 1990 + i} for i, n in enumerate(ALBUM_NAMES, 1)]
PAGE = 6


def art(i):
    return {"hash": f"{i:032x}", "proxy_120": f"/storage/artwork/{i:032x}/120.jpg", "colors": ["#%02x%02x%02x" % ((i * 53) % 200 + 40, (i * 97) % 200 + 40, (i * 29) % 200 + 40)],
            "safe_colors": ["#%02x%02x%02x" % ((i * 53) % 150 + 100, (i * 97) % 150 + 100, (i * 29) % 150 + 100)]}


def album_item(a):
    return {"id": a["id"], "name": a["name"], "artist_name": a["artist"]["name"], "year": a["year"], "artwork": art(a["id"]) if a["id"] != 5 else None, "favorite": False}


def tracks_of(a):
    return [{"id": a["id"] * 10 + n, "name": f"{a['name']} - part {n}", "duration": 8 + n, "playable": True, "favorite": False} for n in range(1, 5)]


def bmp8(w, h, colour):
    """An 8-bit palette BMP like PHP's imagebmp makes: header, 256 four-byte palette entries, bottom-up rows."""
    r, g, b = colour
    pal = b"".join(bytes((min(255, b * k // 255), min(255, g * k // 255), min(255, r * k // 255), 0)) for k in range(256))
    stride = (w + 3) // 4 * 4
    rows = b"".join(bytes(((x * 255 // w + y * 255 // h) // 2) for x in range(w)) + b"\0" * (stride - w) for y in range(h - 1, -1, -1))
    off = 54 + len(pal)
    return b"BM" + struct.pack("<IHHI", off + len(rows), 0, 0, off) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 8, 0, len(rows), 2835, 2835, 256, 0) + pal + rows


def tone(track_id, seconds=12):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        f = 220 + (track_id % 12) * 30
        w.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * f * t / 22050))) for t in range(22050 * seconds)))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    audio = None
    log = []

    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200, headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def page(self, items, q):
        start = int((q.get("cursor") or ["0"])[0] or 0)
        nxt = start + PAGE
        return {"data": items[start:nxt], "next_cursor": str(nxt) if nxt < len(items) else None}

    def do_GET(self):
        u = urlparse(self.path)
        q, p = parse_qs(u.query), u.path
        Handler.log.append(self.path)
        host = self.headers.get("Host", "localhost")
        if p == "/api/info":
            return self.send({"name": "Fake ReMoment", "api_version": 1, "base_url": f"http://{host}", "api_base_url": f"http://{host}/api", "artwork_base_url": f"http://{host}"})
        if p == "/api/library/albums":
            return self.send(self.page([album_item(a) for a in ALBUMS], q))
        if p == "/api/library/artists":
            return self.send(self.page([{"id": a["id"], "name": a["name"], "album_count": sum(1 for x in ALBUMS if x["artist"]["id"] == a["id"]), "artwork": None, "favorite": False} for a in ARTISTS], q))
        if p.startswith("/api/library/artists/"):
            aid = int(p.rsplit("/", 1)[1])
            return self.send({"id": aid, "name": ARTISTS[aid - 1]["name"], "albums": [album_item(a) | {"kind": "album"} for a in ALBUMS if a["artist"]["id"] == aid]})
        if p.startswith("/api/library/albums/"):
            a = ALBUMS[int(p.rsplit("/", 1)[1]) - 1]
            return self.send({"id": a["id"], "name": a["name"], "artist": a["artist"], "year": a["year"], "artwork": art(a["id"]) if a["id"] != 5 else None,
                              "sources": ["files"] if a["id"] != 7 else ["spotify"], "playable": True, "tracks": tracks_of(a)})
        if p == "/api/library/playlists":
            return self.send({"data": [{"id": 1, "name": "Evening mix", "track_count": 3, "owner": None, "artwork": None}], "next_cursor": None})
        if p == "/api/library/playlists/1":
            ts = [{"id": 11, "name": "Absolution - part 1", "artist_name": "The Aeolians", "duration": 9, "artwork": art(1), "playable": True, "missing_locally": False},
                  {"id": 99, "name": "Only on Spotify", "artist_name": "Nobody", "duration": 9, "artwork": None, "playable": False, "missing_locally": True},
                  {"id": 32, "name": "Carbon - part 2", "artist_name": "Cassini", "duration": 10, "artwork": art(3), "playable": True, "missing_locally": False}]
            return self.send({"id": 1, "name": "Evening mix", "tracks": ts})
        if p == "/api/library/recent":
            return self.send({"data": [album_item(ALBUMS[2]), album_item(ALBUMS[0])]})
        if p == "/api/library/favorites":
            return self.send({"albums": [album_item(ALBUMS[1])], "artists": [], "tracks": []})
        if p == "/api/library/search":
            term = (q.get("q") or [""])[0].lower()
            if len(term) < 2:
                return self.send({"message": "q"}, code=422)
            return self.send({"artists": [{"id": a["id"], "name": a["name"], "album_count": 1, "artwork": None} for a in ARTISTS if term in a["name"].lower()],
                              "albums": [album_item(a) for a in ALBUMS if term in a["name"].lower()],
                              "tracks": [{"id": t["id"], "name": t["name"], "artist_name": a["artist"]["name"], "album_id": a["id"], "album_name": a["name"], "duration": t["duration"], "artwork": art(a["id"])}
                                         for a in ALBUMS for t in tracks_of(a) if term in t["name"].lower()][:20]})
        if p.startswith("/api/artwork/") and p.endswith(".bmp"):
            h = p.split("/")[3]
            size = int(p.rsplit("/", 1)[1].split(".")[0])
            n = int(h, 16)
            return self.send(bmp8(size, size, ((n * 53) % 200 + 40, (n * 97) % 200 + 40, (n * 29) % 200 + 40)), "image/bmp")
        if p.startswith("/stream/"):
            tid = int(p.split("/")[2].split(".")[0])
            if tid == 99:
                return self.send({"message": "nope"}, code=404)
            data = open(Handler.audio, "rb").read() if Handler.audio else tone(tid)
            ctype = "audio/mpeg" if Handler.audio else "audio/wav"
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                a, b = int(a or 0), int(b) if b else len(data) - 1
                return self.send(data[a:b + 1], ctype, 206, {"Content-Range": f"bytes {a}-{b}/{len(data)}"})
            return self.send(data, ctype)
        self.send({"message": "not found"}, code=404)


def serve(port=0, audio=None):
    """Start in a thread; returns (server, base url). port 0 picks a free one."""
    Handler.audio, Handler.log = audio, []
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--audio")
    a = ap.parse_args()
    srv, url = serve(a.port, a.audio)
    print("fake ReMoment at", url)
    threading.Event().wait()
