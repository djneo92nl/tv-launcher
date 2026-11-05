#!/usr/bin/env python3
"""Media: browse USB sticks, network shares and local folders and play video and music. Remembers where you stopped."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))    # the repo root, for tvkit

import time

from tvkit import net, ui
from tvkit.player import Playback, Positions
from tvkit.shell import Shell, run_app

VIDEO = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".webm", ".flv", ".ogv", ".divx", ".3gp"}
AUDIO = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".aiff", ".alac"}
DEFAULT_ROOTS = ["/media", "/mnt", "~/Videos", "~/Movies", "~/Music"]


def natural_key(name):
    """'Episode 2' sorts before 'Episode 10'."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def is_media(name):
    return os.path.splitext(name)[1].lower() in VIDEO | AUDIO


def is_audio(path):
    return os.path.splitext(path)[1].lower() in AUDIO


def find_roots(roots):
    """Folders to start from. /media and /mnt are searched two levels down for mount points (USB sticks, shares); if there are none
    the folder itself is used when it has anything in it. Other roots are used as they are when they exist."""
    out = []
    for r in roots:
        r = os.path.expanduser(r)
        if not os.path.isdir(r):
            continue
        if r.rstrip("/") in ("/media", "/mnt"):
            mounts = []
            for a in sorted(os.listdir(r)):
                pa = os.path.join(r, a)
                if os.path.ismount(pa):
                    mounts.append(pa)
                elif os.path.isdir(pa):
                    mounts += [os.path.join(pa, b) for b in sorted(os.listdir(pa)) if os.path.ismount(os.path.join(pa, b))]
            if mounts:
                out += mounts
                continue
            if any(not n.startswith(".") for n in os.listdir(r)):
                out.append(r)
        else:
            out.append(r)
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def list_dir(path):
    """[{name, path, dir, size}]: folders first, then media files, each sorted naturally. Hidden entries and other files are left out."""
    dirs, files = [], []
    try:
        names = os.listdir(path)
    except OSError:
        return []
    for n in names:
        if n.startswith("."):
            continue
        p = os.path.join(path, n)
        if os.path.isdir(p):
            dirs.append({"name": n, "path": p, "dir": True, "size": 0})
        elif is_media(n):
            try:
                size = os.path.getsize(p)
            except OSError:
                size = 0
            files.append({"name": n, "path": p, "dir": False, "size": size})
    key = lambda e: natural_key(e["name"])
    return sorted(dirs, key=key) + sorted(files, key=key)


def fmt_size(n):
    for unit, div in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def title_of(name):
    return os.path.splitext(name)[0].replace("_", " ").replace(".", " ").strip() or name


class Media(Shell):
    name = "media"
    mpv_args = ["--sub-auto=fuzzy", "--keep-open=no"]

    def start(self):
        c = self.cfg.get("media", {})
        self.roots = find_roots(c.get("roots", DEFAULT_ROOTS))
        self.state_path = os.path.join(self.cfg["cache_dir"], "media-state.json")
        self.positions = Positions(self.cfg["cache_dir"], "media-positions.json")
        self.pb = Playback(self.mpv, self.positions)
        self.folder = None                    # None = the list of roots
        self.queue, self.queue_i = [], 0      # the files of the folder being played from
        self.view = ui.ListView([], 7)
        self.note = ""
        self.open(net.read_json(self.state_path, {}).get("folder"))

    # ---- browsing ----
    def open(self, folder, select=None):
        if folder and not (os.path.isdir(folder) and self.under_root(folder)):
            folder = None
        self.folder = folder
        items = list_dir(folder) if folder else [{"name": os.path.basename(r) or r, "path": r, "dir": True, "size": 0, "root": True} for r in self.roots]
        self.view = ui.ListView(items, 7)
        if select:
            for i, e in enumerate(items):
                if e["path"] == select:
                    self.view.sel = i
                    self.view.first = max(0, min(i, len(items) - 7))
        if folder:
            net.write_private(self.state_path, {"folder": folder})

    def under_root(self, path):
        return any(path == r or path.startswith(r.rstrip("/") + "/") for r in self.roots)

    def up(self):
        """One level up; from a root back to the list of roots. False when already at the list of roots."""
        if self.folder is None:
            return False
        parent = os.path.dirname(self.folder)
        self.open(parent if parent != self.folder and self.under_root(parent) else None, select=self.folder)
        return True

    def play_index(self, i):
        e = self.queue[i]
        self.queue_i = i
        self.pb.start(e["path"], title_of(e["name"]), key=e["path"], audio_only=is_audio(e["path"]))

    # ---- the shell's contract ----
    def handle(self, cmd):
        if self.pb.active:
            if self.pb.handle(cmd):
                return
            if cmd in ("quit", "back"):
                self.pb.stop()
                self.open(self.folder, select=self.queue[self.queue_i]["path"] if self.queue else None)
                return
            super().handle(cmd)                                    # volume, mute
            return
        self.note = ""
        if self.view.handle(cmd):
            return
        if cmd == "enter" and self.view.current():
            e = self.view.current()
            if e["dir"]:
                self.open(e["path"])
            else:
                self.queue = [x for x in self.view.items if not x["dir"]]
                self.play_index(next(i for i, x in enumerate(self.queue) if x["path"] == e["path"]))
        elif cmd == "back":
            if not self.up():
                self.quit()
        else:
            super().handle(cmd)

    def on_mpv(self, ev):
        result = self.pb.on_mpv(ev)
        if result == "ended":
            if self.queue_i + 1 < len(self.queue):
                self.play_index(self.queue_i + 1)                  # the next file in the folder
            else:
                self.open(self.folder, select=self.queue[self.queue_i]["path"])
        elif result == "error":
            self.note = "Could not play " + self.queue[self.queue_i]["name"]
            self.open(self.folder, select=self.queue[self.queue_i]["path"])

    def tick(self, now):
        if self.pb.mpv is not self.mpv and self.mpv:               # mpv was restarted after a crash: playback is gone
            self.pb.rebind(self.mpv)
        self.pb.tick(now)

    def detail(self, e):
        if e.get("root"):
            return e["path"]
        if e["dir"]:
            return "folder"
        p = self.positions.get(e["path"])
        mark = "   WATCHED" if p and p.get("done") else f"   RESUME {ui.mmss(p['pos'])}" if p and p.get("pos", 0) >= 30 else ""
        return fmt_size(e["size"]) + mark

    def draw(self, now):
        if self.pb.active:
            return "\n".join(self.pb.osd(now))
        title = (os.path.basename(self.folder) if self.folder else "MEDIA").upper() or "MEDIA"
        empty = ("No USB stick, share or folder found" if self.folder is None else "No media in this folder")
        rows = ui.list_rows(self.view, lambda e: e["name"] if not e["dir"] else e["name"] + "/", self.detail, empty=empty)
        hint = "ENTER open / play   ESC up   Q leave" if self.folder else "ENTER open   Q leave"
        return "\n".join([ui.background()] + ui.header(title) + rows + ui.footer(hint, self.note if self.note else ""))


if __name__ == "__main__":
    run_app(Media)
