#!/usr/bin/env python3
"""Clock: a big clock and the date. ENTER switches 12 / 24 hour, I shows seconds."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))    # the repo root, for tvkit

import datetime

from tvkit import ui
from tvkit.shell import Shell, run_app


def face(t, h24, seconds):
    """(time text, date text) for a datetime."""
    fmt = ("%H:%M" if h24 else "%I:%M") + (":%S" if seconds else "")
    big = t.strftime(fmt)
    return (big if h24 else big.lstrip("0")), f"{t.strftime('%A')} {t.day} {t.strftime('%B %Y')}"


class Clock(Shell):
    name = "clock"

    def start(self):
        c = self.cfg.get("clock", {})
        self.h24, self.seconds = bool(c.get("h24", True)), bool(c.get("seconds", False))

    def handle(self, cmd):
        if cmd == "enter":
            self.h24 = not self.h24
        elif cmd == "info":
            self.seconds = not self.seconds
        else:
            super().handle(cmd)

    def draw(self, now):
        t = datetime.datetime.fromtimestamp(now)
        big, date = face(t, self.h24, self.seconds)
        shift = (t.hour * 60 + t.minute) % 9 - 4            # drifts a few pixels each minute so a CRT or OLED does not burn in
        cx, cy = ui.W // 2 + shift * 3, ui.H // 2 + shift * 2
        return "\n".join([ui.background(), ui.text(cx, cy - 20, big, 170 if not self.seconds else 130, an=5, border=3),
                          ui.text(cx, cy + 90, ui.esc(date), 34, ui.AMBER, an=5, border=1)])


if __name__ == "__main__":
    run_app(Clock)
