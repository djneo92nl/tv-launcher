"""Jellyfin REST client (standard library). API: https://api.jellyfin.org (tested on 10.11).

    c = Client("http://192.168.1.142:8096", device_id)
    c.login("name", "password")            # -> stores token and user id on c; save them with c.session()
    c.views(); c.resume(); c.next_up(); c.items(parent_id); c.seasons(series_id); c.episodes(series_id, season_id)

The token travels in the X-Emby-Token / Authorization header, never in a URL (so it cannot end up in mpv's log or in an error message).
Playback goes through direct play (/Videos/{id}/stream?static=true) with an HLS transcode as the fallback.
"""
import json
import os
import sys
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from tvkit import net  # noqa: E402

TICKS = 10_000_000                                   # Jellyfin counts time in 100 ns ticks
MEDIA_TYPES = {"Movie", "Series", "Season", "Episode", "BoxSet", "Video", "Folder", "CollectionFolder", "MusicVideo"}
BROWSE_FIELDS = "ProductionYear,Overview,RunTimeTicks,OfficialRating,CommunityRating,Genres"


class JellyfinError(Exception):
    pass


def normalise_host(host):
    host = (host or "").strip().rstrip("/")
    if not host:
        raise JellyfinError("no server address")
    return host if host.startswith(("http://", "https://")) else "http://" + host


def seconds(ticks):
    return (ticks or 0) / TICKS


def item_entry(it):
    """What the app keeps of an item: a flat dict that is easy to draw and to test."""
    ud = it.get("UserData") or {}
    kind = it.get("Type", "")
    title = it.get("Name") or "?"
    sub = ""
    if kind == "Episode":
        s, e = it.get("ParentIndexNumber"), it.get("IndexNumber")
        code = (f"S{s}E{e}" if s is not None and e is not None else f"E{e}" if e is not None else "")
        sub = " ".join(x for x in (code, it.get("SeriesName")) if x)
    elif kind == "Season":
        sub = it.get("SeriesName") or ""
    elif it.get("ProductionYear"):
        sub = str(it["ProductionYear"])
    pic = None                                        # (item id, image tag): episodes borrow their series' poster so a grid stays 2:3
    tags = it.get("ImageTags") or {}
    if kind == "Episode" and it.get("SeriesId") and it.get("SeriesPrimaryImageTag"):
        pic = (it["SeriesId"], it["SeriesPrimaryImageTag"])
    elif tags.get("Primary"):
        pic = (it["Id"], tags["Primary"])
    elif kind == "Season" and it.get("SeriesId") and it.get("SeriesPrimaryImageTag"):
        pic = (it["SeriesId"], it["SeriesPrimaryImageTag"])
    return {"id": it["Id"], "type": kind, "title": title, "sub": sub, "year": it.get("ProductionYear"), "overview": it.get("Overview") or "",
            "runtime": seconds(it.get("RunTimeTicks")), "pos": seconds(ud.get("PlaybackPositionTicks")), "played": bool(ud.get("Played")),
            "pic": pic, "series_id": it.get("SeriesId"), "children": it.get("ChildCount"), "rating": it.get("OfficialRating") or "",
            "unplayed": ud.get("UnplayedItemCount"), "collection": it.get("CollectionType") or ""}


def playable(entry):
    return entry["type"] in ("Movie", "Episode", "Video", "MusicVideo")


class Client:
    def __init__(self, host, device_id, timeout=15, name="TV Launcher"):
        self.host, self.timeout, self.device_id, self.name = normalise_host(host), timeout, device_id, name
        self.token = self.user_id = self.user_name = None

    # ---- low level ----
    def auth_header(self):
        h = f'MediaBrowser Client="{self.name}", Device="{self.name}", DeviceId="{self.device_id}", Version="0.1"'
        return h + (f', Token="{self.token}"' if self.token else "")

    def headers(self):
        h = {"Authorization": self.auth_header()}
        if self.token:
            h["X-Emby-Token"] = self.token
        return h

    def get(self, path, **params):
        params = {k: v for k, v in params.items() if v not in (None, "")}
        return net.get_json(self.host + path + ("?" + urlencode(params) if params else ""), headers=self.headers(), timeout=self.timeout)

    def post(self, path, body=None):
        return net.request(self.host + path, data=body if body is not None else b"", headers=self.headers(), method="POST", timeout=self.timeout)

    # ---- session ----
    def public_info(self):
        d = net.get_json(self.host + "/System/Info/Public", timeout=self.timeout)
        if not isinstance(d, dict) or "ServerName" not in d and "Version" not in d:
            raise JellyfinError("not a Jellyfin server")
        return d

    def login(self, user, password):
        try:
            d = net.get_json(self.host + "/Users/AuthenticateByName", data={"Username": user, "Pw": password},
                             headers={"Authorization": self.auth_header()}, timeout=self.timeout)
        except net.NetError as ex:
            if "401" in str(ex):
                raise JellyfinError("wrong name or password") from None
            raise
        if not isinstance(d, dict) or not d.get("AccessToken") or not (d.get("User") or {}).get("Id"):
            raise JellyfinError("unexpected login answer")
        self.token, self.user_id, self.user_name = d["AccessToken"], d["User"]["Id"], d["User"].get("Name") or user
        return self.session()

    def session(self):
        return {"host": self.host, "token": self.token, "user_id": self.user_id, "user_name": self.user_name, "device_id": self.device_id}

    def use(self, saved):
        self.token, self.user_id, self.user_name = saved.get("token"), saved.get("user_id"), saved.get("user_name")

    def check(self):
        """True while the saved token still works (a 401 means it was revoked: log in again)."""
        try:
            self.get(f"/Users/{self.user_id}")
            return True
        except net.NetError as ex:
            if "401" in str(ex):
                return False
            raise

    # ---- browsing ----
    def views(self):
        return [item_entry(i) for i in self.get("/UserViews", userId=self.user_id).get("Items", [])]

    def resume(self, limit=20):
        d = self.get(f"/Users/{self.user_id}/Items/Resume", Limit=limit, MediaTypes="Video", Fields=BROWSE_FIELDS)
        return [item_entry(i) for i in d.get("Items", [])]

    def next_up(self, limit=20):
        d = self.get("/Shows/NextUp", userId=self.user_id, Limit=limit, Fields=BROWSE_FIELDS)
        return [item_entry(i) for i in d.get("Items", [])]

    def items(self, parent_id, start=0, limit=50, sort="SortName"):
        """(entries, total). A library, a collection or a folder; music is left out (this client plays video)."""
        d = self.get(f"/Users/{self.user_id}/Items", ParentId=parent_id, StartIndex=start, Limit=limit, SortBy=sort, SortOrder="Ascending",
                     Fields=BROWSE_FIELDS, ExcludeItemTypes="Audio,MusicAlbum,MusicArtist,Photo,PhotoAlbum")
        return [item_entry(i) for i in d.get("Items", [])], d.get("TotalRecordCount", 0)

    def item(self, item_id):
        return item_entry(self.get(f"/Users/{self.user_id}/Items/{item_id}"))

    def seasons(self, series_id):
        return [item_entry(i) for i in self.get(f"/Shows/{series_id}/Seasons", userId=self.user_id, Fields=BROWSE_FIELDS).get("Items", [])]

    def episodes(self, series_id, season_id):
        return [item_entry(i) for i in self.get(f"/Shows/{series_id}/Episodes", userId=self.user_id, seasonId=season_id, Fields=BROWSE_FIELDS).get("Items", [])]

    def search(self, term, limit=30):
        d = self.get(f"/Users/{self.user_id}/Items", SearchTerm=term, Recursive="true", IncludeItemTypes="Movie,Series,Episode", Limit=limit, Fields=BROWSE_FIELDS)
        return [item_entry(i) for i in d.get("Items", [])]

    # ---- pictures ----
    def poster(self, pic, height):
        """Raw BMP bytes of a poster scaled to `height` pixels (a multiple of 48 keeps the number of distinct downloads small)."""
        item_id, tag = pic
        q = {"fillHeight": height, "fillWidth": height * 2 // 3, "quality": 85, "format": "Bmp", "tag": tag}
        return net.request(f"{self.host}/Items/{item_id}/Images/Primary?{urlencode(q)}", headers=self.headers(), timeout=self.timeout, limit=4_000_000)

    # ---- playback ----
    def direct_url(self, item_id):
        return f"{self.host}/Videos/{item_id}/stream?{urlencode({'static': 'true', 'MediaSourceId': item_id})}"

    def transcode_url(self, item_id, max_kbps=4000):
        q = {"MediaSourceId": item_id, "VideoCodec": "h264", "AudioCodec": "aac,mp3", "MaxStreamingBitrate": max_kbps * 1000,
             "TranscodingMaxAudioChannels": 2, "SegmentContainer": "ts", "PlaySessionId": self.device_id[:8] + item_id[:8]}
        return f"{self.host}/Videos/{item_id}/master.m3u8?{urlencode(q)}"

    def header_option(self):
        """The mpv --http-header-fields value that authorises a stream (set as a property so the token is never in a URL)."""
        return f"X-Emby-Token: {self.token}"

    def _report(self, path, item_id, pos_s, **extra):
        body = {"ItemId": item_id, "MediaSourceId": item_id, "PositionTicks": int(pos_s * TICKS), **extra}
        try:
            self.post(path, json.dumps(body).encode())
        except net.NetError:
            pass                                                    # progress is a courtesy: a hiccup must not stop the film

    def report_start(self, item_id, pos_s, method="DirectStream"):
        self._report("/Sessions/Playing", item_id, pos_s, PlayMethod=method, CanSeek=True)

    def report_progress(self, item_id, pos_s, paused=False, method="DirectStream"):
        self._report("/Sessions/Playing/Progress", item_id, pos_s, IsPaused=paused, PlayMethod=method, CanSeek=True)

    def report_stop(self, item_id, pos_s):
        self._report("/Sessions/Playing/Stopped", item_id, pos_s)
