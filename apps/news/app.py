#!/usr/bin/env python3
"""News: headlines from the RSS / Atom feeds in config.json, then the story. Q leaves, ESC goes back one level."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))    # the repo root, for tvkit

import threading
import time

from tvkit import ui
from tvkit.shell import Shell, run_app

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feeds  # noqa: E402


def ago(ts, now):
    if not ts:
        return ""
    m = int((now - ts) / 60)
    return "just now" if m < 1 else f"{m} min ago" if m < 90 else f"{m // 60} h ago" if m < 2880 else f"{m // 1440} d ago"


class News(Shell):
    name = "news"

    def start(self):
        self.feeds = self.cfg.get("feeds", [])
        self.data = {}                                  # feed index -> list of entries | "error"
        self.screen = "feeds" if len(self.feeds) > 1 else "list"
        self.feed_view = ui.ListView(range(len(self.feeds)), 7)
        self.cur = 0
        self.view = ui.ListView([], 7)
        self.story, self.scroll = None, 0
        if not self.feeds:
            self.screen = "empty"
        threading.Thread(target=self._load, daemon=True, name="feeds").start()

    def _load(self):
        for i, f in enumerate(self.feeds):
            try:
                self.data[i] = feeds.fetch(f["url"], self.cfg["cache_dir"])
            except Exception:
                self.data[i] = "error"
            if self.screen == "list" and i == self.cur:
                self.open_feed(i)

    def open_feed(self, i):
        self.cur = i
        d = self.data.get(i)
        self.view = ui.ListView(d if isinstance(d, list) else [], 7)

    def handle(self, cmd):
        if self.screen == "feeds":
            if self.feed_view.handle(cmd):
                return
            if cmd == "enter":
                self.open_feed(self.feed_view.current())
                self.screen = "list"
                return
        elif self.screen == "list":
            if self.view.handle(cmd):
                return
            if cmd == "enter" and self.view.current():
                self.story, self.scroll, self.screen = self.view.current(), 0, "story"
                return
            if cmd == "back" and len(self.feeds) > 1:
                self.screen = "feeds"
                return
        elif self.screen == "story":
            if cmd in ("up", "down", "left", "right"):
                self.scroll = max(0, self.scroll + {"up": -1, "down": 1, "left": -8, "right": 8}[cmd])
                return
            if cmd == "back":
                self.screen = "list"
                return
        super().handle(cmd)

    def draw(self, now):
        out = [ui.background()]
        if self.screen == "empty":
            return "\n".join(out + ui.header("NEWS") + [ui.text(ui.W // 2, ui.H // 2, 'Add "feeds" to config.json', 26, ui.GREY, an=5, bold=False)] + ui.footer("Q back"))
        if self.screen == "feeds":
            rows = ui.list_rows(self.feed_view, lambda i: self.feeds[i]["name"], lambda i: self._count(i))
            return "\n".join(out + ui.header("NEWS") + rows + ui.footer("UP/DOWN pick   ENTER open   Q back"))
        name = self.feeds[self.cur]["name"] if self.feeds else "NEWS"
        if self.screen == "list":
            d = self.data.get(self.cur)
            empty = "Loading..." if d is None else "Could not load this feed" if d == "error" else "No stories"
            rows = ui.list_rows(self.view, lambda e: e["title"], lambda e: (ago(e.get("published"), now) + "  " + e["summary"].replace("\n", " "))[:90], empty=empty)
            return "\n".join(out + ui.header(name.upper()) + rows + ui.footer("UP/DOWN pick   ENTER read   ESC feeds   Q back"))
        e = self.story
        width = max(30, (ui.W - 2 * ui.SX) // 11)
        lines = ui.wrap(e["title"], width - 12)[:2]
        body = ui.wrap(e["summary"] or "(no text in the feed: press nothing, the link is " + (e["link"] or "missing") + ")", width)
        shown = body[self.scroll:self.scroll + 11]
        out += ui.header(name.upper())
        out += [ui.text(ui.SX, ui.SY + 54 + i * 30, ln, 26, ui.AMBER, border=1) for i, ln in enumerate(lines)]
        y0 = ui.SY + 54 + len(lines) * 30 + 14
        out += [ui.text(ui.SX, y0 + i * 28, ln, 20, border=1, bold=False) for i, ln in enumerate(shown)]
        if e.get("link"):
            out.append(ui.text(ui.SX, ui.H - ui.SY - 28, ui.esc(e["link"])[:70], 14, ui.DIM, bold=False, border=0))
        return "\n".join(out + ui.footer("UP/DOWN scroll   ESC back   Q leave"))

    def _count(self, i):
        d = self.data.get(i)
        return "loading..." if d is None else "could not load" if d == "error" else f"{len(d)} stories"


if __name__ == "__main__":
    run_app(News)
