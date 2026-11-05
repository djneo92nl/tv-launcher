#!/usr/bin/env python3
"""A small pretend Jellyfin server for tests and demos: python3 tools/fake_jellyfin.py [--port 8097] [--video file.mp4]
Login: user "kid", password "secret". Library: Movies (6), Shows (2 series with seasons and episodes). Token in a header, never in a URL."""
import argparse
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN, UID = "tok-123", "u" * 32
TICK = 10_000_000


def mid(n):
    return f"{n:032x}"


MOVIES = [{"Id": mid(100 + i), "Name": n, "Type": "Movie", "ProductionYear": 2000 + i, "RunTimeTicks": (90 + i) * 60 * TICK, "Overview": f"{n} is a film about nothing much.",
           "ImageTags": {"Primary": "t1"} if i != 3 else {}, "UserData": {"PlaybackPositionTicks": 0, "Played": False}}
          for i, n in enumerate(["Alien Rain", "Blue Valley", "Cold Harbour", "Dust Devils", "Empty Rooms", "Fox Hunt"])]
MOVIES[1]["UserData"] = {"PlaybackPositionTicks": 1500 * TICK, "Played": False}       # half-watched
MOVIES[2]["UserData"] = {"PlaybackPositionTicks": 0, "Played": True}
SHOWS = [{"Id": mid(200), "Name": "Night Shift", "Type": "Series", "ProductionYear": 2019, "Overview": "Nurses at night.", "ImageTags": {"Primary": "s1"}, "ChildCount": 2, "UserData": {"UnplayedItemCount": 5}},
         {"Id": mid(210), "Name": "Old Radio", "Type": "Series", "ProductionYear": 1980, "Overview": "Voices.", "ImageTags": {"Primary": "s2"}, "ChildCount": 1, "UserData": {}}]
SEASONS = {mid(200): [{"Id": mid(201), "Name": "Season 1", "Type": "Season", "SeriesId": mid(200), "SeriesName": "Night Shift", "IndexNumber": 1, "ImageTags": {"Primary": "x"}, "ChildCount": 3},
                      {"Id": mid(202), "Name": "Season 2", "Type": "Season", "SeriesId": mid(200), "SeriesName": "Night Shift", "IndexNumber": 2, "ChildCount": 2}],
           mid(210): [{"Id": mid(211), "Name": "Season 1", "Type": "Season", "SeriesId": mid(210), "SeriesName": "Old Radio", "IndexNumber": 1, "ChildCount": 1}]}
EPS = {mid(201): [{"Id": mid(300 + n), "Name": f"Episode {n}", "Type": "Episode", "SeriesId": mid(200), "SeriesName": "Night Shift", "SeriesPrimaryImageTag": "s1", "ParentIndexNumber": 1,
                   "IndexNumber": n, "RunTimeTicks": 40 * 60 * TICK, "Overview": f"Night {n}.", "UserData": {"PlaybackPositionTicks": 600 * TICK if n == 2 else 0, "Played": n == 1}} for n in (1, 2, 3)],
       mid(202): [{"Id": mid(310 + n), "Name": f"Later {n}", "Type": "Episode", "SeriesId": mid(200), "SeriesName": "Night Shift", "SeriesPrimaryImageTag": "s1", "ParentIndexNumber": 2,
                   "IndexNumber": n, "RunTimeTicks": 40 * 60 * TICK, "UserData": {}} for n in (1, 2)],
       mid(211): [{"Id": mid(320), "Name": "Pilot", "Type": "Episode", "SeriesId": mid(210), "SeriesName": "Old Radio", "ParentIndexNumber": 1, "IndexNumber": 1, "RunTimeTicks": 30 * 60 * TICK, "UserData": {}}]}
VIEWS = [{"Id": mid(1), "Name": "Movies", "Type": "CollectionFolder", "CollectionType": "movies", "ImageTags": {}},
         {"Id": mid(2), "Name": "Shows", "Type": "CollectionFolder", "CollectionType": "tvshows", "ImageTags": {}},
         {"Id": mid(3), "Name": "Music", "Type": "CollectionFolder", "CollectionType": "music", "ImageTags": {}}]
PARENTS = {mid(1): MOVIES, mid(2): SHOWS}
EVERYTHING = {x["Id"]: x for x in MOVIES + SHOWS + [e for v in SEASONS.values() for e in v] + [e for v in EPS.values() for e in v]}


def bmp24(w, h, seed):
    stride = (w * 3 + 3) // 4 * 4
    rows = b"".join(bytes(b for x in range(w) for b in ((seed * 40 + y * 255 // h) % 256, (x * 255 // w), (seed * 90) % 256)) + b"\0" * (stride - w * 3) for y in range(h - 1, -1, -1))
    return b"BM" + struct.pack("<IHHI", 54 + len(rows), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(rows), 2835, 2835, 0, 0) + rows


def png_rgb(w, h, seed):
    import zlib
    raw = b"".join(b"\0" + bytes(b for x in range(w) for b in ((x * 255 // w), (seed * 40 + y * 255 // h) % 256, (seed * 90) % 256)) for y in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Handler(BaseHTTPRequestHandler):
    log = []                 # (method, path, body)
    polls = 0
    video = None
    fail_login_with = None   # set to a status code to make login fail (e.g. 500)
    quick = True             # Quick Connect enabled
    quick_polls_needed = 2   # the code is "approved" after this many polls

    def log_message(self, *a):
        pass

    def send(self, body, code=200, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(data)

    def authed(self):
        return self.headers.get("X-Emby-Token") == TOKEN or f'Token="{TOKEN}"' in (self.headers.get("Authorization") or "")

    def page(self, items, q):
        start, limit = int((q.get("StartIndex") or ["0"])[0]), int((q.get("Limit") or ["100"])[0])
        return {"Items": items[start:start + limit], "TotalRecordCount": len(items), "StartIndex": start}

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        u = urlparse(self.path)
        Handler.log.append(("POST", u.path, body))
        if u.path == "/QuickConnect/Initiate":
            if not Handler.quick:
                return self.send({"error": "off"}, 401)
            Handler.polls = 0
            return self.send({"Secret": "sec-1", "Code": "482913", "Authenticated": False})
        if u.path == "/Users/AuthenticateWithQuickConnect":
            if json.loads(body or b"{}").get("Secret") == "sec-1" and Handler.polls >= Handler.quick_polls_needed:
                return self.send({"AccessToken": TOKEN, "User": {"Id": UID, "Name": "kid"}})
            return self.send({"error": "not approved"}, 401)
        if u.path == "/Users/AuthenticateByName":
            if Handler.fail_login_with:
                return self.send({"error": "x"}, Handler.fail_login_with)
            d = json.loads(body or b"{}")
            if d.get("Username") == "kid" and d.get("Pw") == "secret":
                return self.send({"AccessToken": TOKEN, "User": {"Id": UID, "Name": "kid"}})
            return self.send({"error": "bad"}, 401)
        if not self.authed():
            return self.send({"error": "auth"}, 401)
        self.send(b"", 204)

    def do_GET(self):
        u = urlparse(self.path)
        q, p = parse_qs(u.query), u.path
        Handler.log.append(("GET", self.path, b""))
        if p == "/System/Info/Public":
            return self.send({"ServerName": "fake-jellyfin", "Version": "10.11.0", "Id": "abc"})
        if p == "/QuickConnect/Enabled":
            return self.send(Handler.quick)
        if p == "/QuickConnect/Connect":
            Handler.polls += 1
            return self.send({"Authenticated": Handler.polls >= Handler.quick_polls_needed and q.get("secret") == ["sec-1"], "Code": "482913"})
        if not self.authed():
            return self.send({"error": "auth"}, 401)
        if p == f"/Users/{UID}":
            return self.send({"Id": UID, "Name": "kid"})
        if p == "/UserViews":
            return self.send({"Items": VIEWS, "TotalRecordCount": 3})
        if p == f"/Users/{UID}/Items/Resume":
            ep = EPS[mid(201)][1]
            return self.send({"Items": [MOVIES[1], ep], "TotalRecordCount": 2})
        if p == "/Shows/NextUp":
            return self.send({"Items": [EPS[mid(201)][2]], "TotalRecordCount": 1})
        if p == f"/Users/{UID}/Items":
            if q.get("SearchTerm"):
                t = q["SearchTerm"][0].lower()
                return self.send({"Items": [x for x in EVERYTHING.values() if t in x["Name"].lower() and x["Type"] in ("Movie", "Series", "Episode")], "TotalRecordCount": 1})
            return self.send(self.page(PARENTS.get((q.get("ParentId") or [""])[0], []), q))
        if p.startswith(f"/Users/{UID}/Items/"):
            it = EVERYTHING.get(p.rsplit("/", 1)[1])
            return self.send(it) if it else self.send({}, 404)
        if p.startswith("/Shows/") and p.endswith("/Seasons"):
            return self.send({"Items": SEASONS.get(p.split("/")[2], []), "TotalRecordCount": 1})
        if p.startswith("/Shows/") and p.endswith("/Episodes"):
            return self.send({"Items": EPS.get((q.get("seasonId") or [""])[0], []), "TotalRecordCount": 1})
        if p.startswith("/Items/") and "/Images/Primary" in p:
            h = int((q.get("fillHeight") or ["144"])[0])
            seed = int(p.split("/")[2], 16) % 7
            if (q.get("format") or [""])[0] == "Png":                  # like a real server: PNG when asked, otherwise the original (a "JPEG")
                return self.send(png_rgb(h * 2 // 3, h, seed), 200, "image/png")
            return self.send(b"\xff\xd8\xff\xe0JFIF", 200, "image/jpeg")
        if p.startswith("/Videos/"):
            if p.endswith("/master.m3u8"):
                return self.send(b"#EXTM3U\n", 200, "application/vnd.apple.mpegurl")
            if Handler.video:
                return self.send(open(Handler.video, "rb").read(), 200, "video/mp4")
            return self.send(b"x" * 100, 200, "video/mp4")
        self.send({"message": "nf"}, 404)


def serve(port=0, video=None):
    Handler.log, Handler.video, Handler.fail_login_with, Handler.quick, Handler.quick_polls_needed, Handler.polls = [], video, None, True, 2, 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--video")
    a = ap.parse_args()
    srv, url = serve(a.port, a.video)
    print("fake Jellyfin at", url, "(user kid / password secret)")
    threading.Event().wait()
