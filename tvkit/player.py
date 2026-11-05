"""Playback for apps that play things (local media now; ReMoment and Jellyfin later): one object that drives the app's mpv.

    pb = Playback(shell.mpv, Positions(cache_dir))
    pb.start(url, "Title", key="/path/or/item-id")     # resumes where it stopped, if it was stopped part-way
    pb.handle(cmd)       # left/right seek 10 s, up/down 60 s, enter pause, c subtitles, s audio track, i show the bar; True if used
    pb.on_mpv(ev)        # call from Shell.on_mpv; returns "ended", "error" or None
    pb.tick(now)         # saves the position every 10 s
    pb.osd(now)          # ASS lines for the title, position bar and notices (draw them from Shell.draw)
    pb.stop()            # saves and stops
"""
import json
import os
import time

from . import ui

OBSERVED = {20: "time-pos", 21: "duration", 22: "pause"}
RESUME_MIN_S, DONE_FRACTION, SAVE_EVERY_S, OSD_SECONDS, MAX_ENTRIES = 30, 0.95, 10, 4, 500


class Positions:
    """Where each thing was stopped, in a small JSON file: {key: {"pos": s, "dur": s, "t": saved-at, "done": bool}}."""

    def __init__(self, cache_dir, name="positions.json"):
        self.path = os.path.join(cache_dir, name)
        try:
            with open(self.path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, key):
        return self.data.get(key)

    def resume_at(self, key):
        """Seconds to start from, or None (never started, too near the start, or finished)."""
        e = self.data.get(key)
        if not e or e.get("done") or e.get("pos", 0) < RESUME_MIN_S:
            return None
        return e["pos"]

    def update(self, key, pos, dur):
        if not key or pos is None:
            return
        done = bool(dur) and pos >= dur * DONE_FRACTION
        self.data[key] = {"pos": 0 if done else round(pos, 1), "dur": round(dur or 0, 1), "t": int(time.time()), "done": done}

    def save(self):
        if len(self.data) > MAX_ENTRIES:                      # forget the oldest
            keep = sorted(self.data.items(), key=lambda kv: kv[1].get("t", 0))[-MAX_ENTRIES:]
            self.data = dict(keep)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w") as f:
                json.dump(self.data, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError:
            pass


class Playback:
    def __init__(self, mpv, positions=None, audio_only=False):
        self.mpv, self.positions = mpv, positions
        self.audio_only = audio_only             # draw a permanent "now playing" card instead of a bar that fades
        self.title, self.key = "", None
        self.active = False
        self.pos = self.dur = None
        self.paused = False
        self.note, self.note_until = "", 0
        self.osd_until = 0
        self.last_save = 0
        for oid, prop in OBSERVED.items():
            mpv.command("observe_property", oid, prop)

    def rebind(self, mpv):
        """A new mpv process (the old one crashed): observe again, and forget what was playing."""
        self.mpv, self.active = mpv, False
        for oid, prop in OBSERVED.items():
            mpv.command("observe_property", oid, prop)

    # ---- control ----
    def start(self, url, title, key=None, audio_only=None, resume=True, start_at=None):
        if self.active:
            self.save()
        if audio_only is not None:
            self.audio_only = audio_only
        self.title, self.key, self.active = title, key, True
        self.pos = self.dur = None
        self.paused = False
        at = start_at if start_at else (self.positions.resume_at(key) if (resume and self.positions and key) else None)
        self.mpv.play(url, start=at or 0)
        if at:
            self.say("RESUMED AT " + ui.mmss(at))
        self.osd_until = time.time() + OSD_SECONDS
        self.last_save = time.time()

    def stop(self):
        if self.active:
            self.save()
            self.active = False
            self.mpv.stop()

    def save(self):
        if self.positions and self.key and self.pos is not None:
            self.positions.update(self.key, self.pos, self.dur)
            self.positions.save()
        self.last_save = time.time()

    def say(self, text, seconds=2.5):
        self.note, self.note_until = text, time.time() + seconds

    def handle(self, cmd):
        """True if the command was a playback command (so the app should not also act on it)."""
        if not self.active:
            return False
        seek = {"left": -10, "right": 10, "down": -60, "up": 60}.get(cmd)
        if seek is not None:
            self.mpv.command("seek", seek, "relative")
            self.say(("+" if seek > 0 else "-") + f"{abs(seek)} s", 1.2)
            self.osd_until = time.time() + OSD_SECONDS
        elif cmd == "enter":
            self.mpv.command("cycle", "pause")
            self.osd_until = time.time() + OSD_SECONDS
        elif cmd == "info":
            self.osd_until = time.time() + OSD_SECONDS
        elif cmd == "captions":
            self.mpv.command("cycle", "sub")
            sid = self.mpv.get("sid")
            self.say("SUBTITLES OFF" if sid in (None, False, "no") else f"SUBTITLES {sid}")
        elif cmd == "menu":
            self.mpv.command("cycle", "audio")
            self.say(f"AUDIO TRACK {self.mpv.get('aid')}")
        else:
            return False
        return True

    # ---- events ----
    def on_mpv(self, ev):
        name = ev.get("event")
        if name == "property-change":
            prop, data = ev.get("name"), ev.get("data")
            if prop == "time-pos" and isinstance(data, (int, float)):
                self.pos = data
            elif prop == "duration" and isinstance(data, (int, float)):
                self.dur = data
            elif prop == "pause":
                self.paused = bool(data)
                self.osd_until = time.time() + OSD_SECONDS
        elif name == "end-file" and self.active:
            reason = ev.get("reason")
            if reason == "eof":
                if self.positions and self.key and self.dur:
                    self.positions.update(self.key, self.dur, self.dur)       # watched to the end
                    self.positions.save()
                self.active = False
                return "ended"
            if reason == "error":
                self.active = False
                return "error"
        return None

    def tick(self, now):
        if self.active and now - self.last_save >= SAVE_EVERY_S:
            self.save()

    def status(self):
        """For Shell.status(): what is playing, or None."""
        if not self.active:
            return None
        return {"title": self.title, "pos": self.pos, "dur": self.dur, "paused": self.paused}

    # ---- drawing ----
    def osd(self, now):
        if not self.active:
            return []
        out = []
        show = self.audio_only or self.paused or now < self.osd_until
        if self.audio_only:
            out += [ui.background(), ui.text(ui.W // 2, ui.H // 2 - 70, "NOW PLAYING", 22, ui.AMBER, an=5, border=1),
                    ui.text(ui.W // 2, ui.H // 2 - 20, ui.esc(self.title)[:44], 34, an=5)]
        if show:
            x0, x1, top = ui.SX, ui.W - ui.SX, ui.H - ui.SY - 92
            if not self.audio_only:
                out.append(ui.rect(x0 - 8, top - 8, x1 + 8, ui.H - ui.SY + 4, ui.BG, "30"))
                out.append(ui.text(x0, top, ui.esc(self.title)[:60], 24, border=1))
            by = top + 40 if not self.audio_only else ui.H // 2 + 50
            frac = (self.pos / self.dur) if self.pos is not None and self.dur else 0
            out += ui.progress(x0, x1, by, frac)
            out.append(ui.text(x0, by + 14, ui.mmss(self.pos or 0), 18, ui.GREY, border=1))
            if self.dur:
                out.append(ui.text(x1, by + 14, "-" + ui.mmss(self.dur - (self.pos or 0)), 18, ui.GREY, an=9, border=1))
            if self.paused:
                out.append(ui.text(ui.W // 2, by + 12, "PAUSED", 22, ui.AMBER, an=8, border=1))
        if self.note and now < self.note_until:
            out += ui.toast(self.note)
        return out
