"""ReMoment REST client (standard library). The contract is ReMomentServer/docs/api/library.md and server-info.md.

    c = Client("http://remoment.local"); c.info()
    albums, next_cursor = c.albums()
    c.stream_url(track_id); c.cover_url(album["artwork"])

Browsing uses scope=files by default: only the library's own files, which is what /stream/{track} can serve to this device
(DLNA and Spotify tracks play on the server's speakers, not here).
"""
import os
import sys
from urllib.parse import quote, urlencode

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from tvkit import net  # noqa: E402

API_VERSION = 1
COVER_SIZES = (32, 48, 64, 96, 120, 160)          # LiteArtwork::SIZES on the server
COVER_COLOURS = (2, 4, 16, 64, 256)


class ReMomentError(Exception):
    pass


def normalise_host(host):
    host = (host or "").strip().rstrip("/")
    if not host:
        raise ReMomentError("no server address")
    return host if host.startswith(("http://", "https://")) else "http://" + host


def track_entry(t, album=None):
    """What the app queues: a flat dict, whether the track came from an album, a playlist or a search."""
    album = album or {}
    return {"id": t["id"], "title": t.get("name") or "?", "artist": t.get("artist_name") or (album.get("artist") or {}).get("name") or "",
            "album": t.get("album_name") or album.get("name") or "", "dur": t.get("duration"), "art": t.get("artwork") or album.get("artwork"),
            "playable": t.get("playable", True) is not False}


class Client:
    def __init__(self, host, scope="files", timeout=10):
        self.host, self.scope, self.timeout = normalise_host(host), scope, timeout
        self.api, self.base = self.host + "/api", self.host
        self.name = "ReMoment"

    # ---- low level ----
    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v not in (None, "")}
        url = self.api + path + ("?" + urlencode(params) if params else "")
        return net.get_json(url, timeout=self.timeout)

    def _scoped(self, **params):
        return {"scope": self.scope, **params} if self.scope else params

    def info(self):
        """Connects: where the API and the streams live, and whether this server speaks a version we know."""
        d = self.get("/info")
        if not isinstance(d, dict) or "api_base_url" not in d and "base_url" not in d:
            raise ReMomentError("not a ReMoment server")
        if int(d.get("api_version", API_VERSION)) > API_VERSION:
            raise ReMomentError("server is newer than this client")
        self.base = (d.get("base_url") or self.host).rstrip("/")
        self.api = (d.get("api_base_url") or self.base + "/api").rstrip("/")
        self.name = d.get("name") or "ReMoment"
        return d

    # ---- browsing ----
    def albums(self, cursor=None, sort="name"):
        d = self.get("/library/albums", **self._scoped(cursor=cursor, sort=sort))
        return d.get("data", []), d.get("next_cursor")

    def artists(self, cursor=None, sort="name"):
        d = self.get("/library/artists", **self._scoped(cursor=cursor, sort=sort))
        return d.get("data", []), d.get("next_cursor")

    def artist(self, artist_id):
        return self.get(f"/library/artists/{int(artist_id)}", source="files")

    def album(self, album_id):
        return self.get(f"/library/albums/{int(album_id)}")

    def playlists(self, cursor=None):
        d = self.get("/library/playlists", **self._scoped(cursor=cursor))
        return d.get("data", []), d.get("next_cursor")

    def playlist(self, playlist_id):
        return self.get(f"/library/playlists/{int(playlist_id)}", library=self.scope or None)

    def recent(self, limit=30):
        return self.get("/library/recent", **self._scoped(limit=limit)).get("data", [])

    def favorites(self):
        return self.get("/library/favorites", **self._scoped())

    def search(self, q):
        if len(q.strip()) < 2:
            return {"artists": [], "albums": [], "tracks": []}
        return self.get("/library/search", **self._scoped(q=q.strip()[:100]))

    # ---- things to play and show ----
    def stream_url(self, track_id):
        return f"{self.base}/stream/{int(track_id)}"

    def cover_url(self, artwork, size=160, colours=64):
        """URL of the tiny BMP cover (decoded by tvkit.images), or None when the cover is not processed yet."""
        h = (artwork or {}).get("hash")
        if not h or size not in COVER_SIZES or colours not in COVER_COLOURS:
            return None
        return f"{self.api}/artwork/{quote(h)}/{size}.bmp?colors={colours}"

    def fetch_cover(self, artwork, size=160, colours=64):
        url = self.cover_url(artwork, size, colours)
        return net.request(url, timeout=self.timeout, limit=2_000_000) if url else None
