#!/usr/bin/env python3
"""Photos: a slideshow of the folders in config.json ("photos.dirs", USB sticks under /media and /mnt included). Left/right step, ENTER pauses."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))    # the repo root, for tvkit

import os
import random
import time

from tvkit import ui
from tvkit.shell import Shell, run_app

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff")
MAX_FILES = 20000


def find_photos(dirs, limit=MAX_FILES):
    """Image files under the folders, sorted; hidden folders and macOS resource forks are skipped."""
    out = []
    for d in dirs:
        d = os.path.expanduser(d)
        for root, subdirs, files in os.walk(d):
            subdirs[:] = sorted(s for s in subdirs if not s.startswith("."))
            for f in sorted(files):
                if f.lower().endswith(EXTS) and not f.startswith("."):
                    out.append(os.path.join(root, f))
                    if len(out) >= limit:
                        return out
    return out


class Photos(Shell):
    name = "photos"
    mpv_args = ["--image-display-duration=inf", "--keep-open=no", "--loop-file=no"]

    def start(self):
        c = self.cfg.get("photos", {})
        self.dirs = c.get("dirs", ["~/Pictures"])
        self.seconds = max(2, int(c.get("seconds", 8)))
        self.shuffle = bool(c.get("shuffle", True))
        self.files = find_photos(self.dirs)
        self.i, self.playing, self.last, self.toast_until, self.toast_text, self.failures = -1, True, 0, 0, "", 0
        if self.shuffle:
            random.shuffle(self.files)
        if self.files:
            self.go(0)

    def go(self, step):
        if not self.files or not self.mpv:
            return
        self.i = (self.i + step) % len(self.files)
        self.last = time.time()
        self.mpv.play(self.files[self.i])

    def say(self, text):
        self.toast_text, self.toast_until = text, time.time() + 2.5

    def handle(self, cmd):
        if cmd in ("right", "down"):
            self.go(1)
        elif cmd in ("left", "up"):
            self.go(-1)
        elif cmd == "enter":
            self.playing = not self.playing
            self.say("PLAYING" if self.playing else "PAUSED")
        elif cmd == "info" and self.files:
            self.say(f"{self.i + 1}/{len(self.files)}  {os.path.basename(self.files[self.i])}")
        elif cmd == "menu":
            self.shuffle = not self.shuffle
            cur = self.files[self.i] if self.files and self.i >= 0 else None
            self.files.sort()
            if self.shuffle:
                random.shuffle(self.files)
            self.i = self.files.index(cur) if cur in self.files else 0
            self.say("SHUFFLE ON" if self.shuffle else "IN ORDER")
        else:
            super().handle(cmd)

    def on_mpv(self, ev):
        if ev.get("event") == "end-file" and ev.get("reason") == "error":       # an unreadable picture: skip it, but not forever
            self.failures += 1
            if self.failures <= len(self.files):
                self.go(1)
        elif ev.get("event") == "file-loaded":
            self.failures = 0

    def tick(self, now):
        if self.files and self.playing and now - self.last >= self.seconds:
            self.go(1)

    def draw(self, now):
        if not self.files:
            where = ", ".join(self.dirs)
            return "\n".join([ui.background()] + ui.header("PHOTOS") + [ui.text(ui.W // 2, ui.H // 2 - 20, "No photos found", 34, an=5),
                             ui.text(ui.W // 2, ui.H // 2 + 30, ui.esc("Looked in " + where)[:70], 18, ui.GREY, an=5, bold=False)]
                             + ui.footer("Put pictures in a folder or plug in a USB stick   Q back"))
        return "\n".join(ui.toast(self.toast_text)) if time.time() < self.toast_until else ""


if __name__ == "__main__":
    run_app(Photos)
