#!/usr/bin/env python3
"""Template: copy this folder to apps/<id>/, rename the class, add an entry to launcher.json. A new app is about this size."""
import os
import sys

# The launcher puts tvkit on PYTHONPATH for every app, wherever its folder is. This line only matters when you run the file by hand from inside the repo.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from tvkit import ui
from tvkit.shell import Shell, run_app


class Template(Shell):
    name = "template"                 # log file name, and the app's id in log lines

    def start(self):
        self.count = 0

    def handle(self, cmd):            # "up" "down" "left" "right" "enter" "back" "quit" "digit:N" "volup" ...
        if cmd == "enter":
            self.count += 1
        else:
            super().handle(cmd)       # back / quit leave the app, volume keys work

    def draw(self, now):              # ASS for mpv; redrawn only when it changes
        return "\n".join([ui.background()] + ui.header("TEMPLATE") + [ui.text(ui.W // 2, ui.H // 2, f"Enter pressed {self.count} times", 32, an=5)]
                         + ui.footer("ENTER count   Q back to the menu"))


if __name__ == "__main__":
    run_app(Template)
