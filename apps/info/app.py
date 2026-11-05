#!/usr/bin/env python3
"""This Pi: address, temperature, memory, disk and power warnings, refreshed every two seconds."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))    # the repo root, for tvkit

import os
import shutil
import socket
import subprocess
import threading

from tvkit import control, ui
from tvkit.shell import Shell, run_app

THROTTLE_BITS = {0: "UNDER-VOLTAGE NOW", 1: "ARM FREQUENCY CAPPED", 2: "THROTTLED", 3: "SOFT TEMPERATURE LIMIT",
                 16: "under-voltage happened", 17: "frequency cap happened", 18: "throttling happened", 19: "soft limit happened"}


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def vcgencmd(*args):
    try:
        return subprocess.run(["vcgencmd", *args], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def throttled_text(hexvalue):
    """"0x50005" -> ["UNDER-VOLTAGE NOW", ...]; [] when everything is fine."""
    try:
        v = int(hexvalue.split("=")[-1], 16)
    except ValueError:
        return []
    return [text for bit, text in THROTTLE_BITS.items() if v >> bit & 1]


def address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))              # UDP "connect" sends nothing: it only asks the kernel which interface it would use
            return s.getsockname()[0]
    except OSError:
        return "no network"


def meminfo(text):
    d = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        if v.split():
            d[k] = int(v.split()[0]) * 1024
    return d


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def fmt_uptime(sec):
    d, rem = divmod(int(sec), 86400)
    h, rem = divmod(rem, 3600)
    return (f"{d} d " if d else "") + f"{h} h {rem // 60} min"


def gather():
    """[(label, value, level)] where level is 0 fine, 1 warning."""
    rows = [("HOSTNAME", socket.gethostname(), 0), ("ADDRESS", address(), 0)]
    up = read("/proc/uptime")
    if up:
        rows.append(("UPTIME", fmt_uptime(float(up.split()[0])), 0))
    try:
        rows.append(("LOAD", " ".join(f"{x:.2f}" for x in os.getloadavg()), 0))
    except OSError:
        pass
    temp = read("/sys/class/thermal/thermal_zone0/temp")
    c = int(temp) / 1000 if temp and temp.strip().isdigit() else None
    if c is None:
        t = vcgencmd("measure_temp")                      # "temp=48.0'C"
        try:
            c = float(t.split("=")[1].split("'")[0])
        except (IndexError, ValueError):
            c = None
    if c is not None:
        rows.append(("TEMPERATURE", f"{c:.1f} C", 1 if c >= 75 else 0))
    mem = meminfo(read("/proc/meminfo"))
    if mem.get("MemTotal"):
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        rows.append(("MEMORY", f"{fmt_bytes(avail)} free of {fmt_bytes(mem['MemTotal'])}", 1 if avail < mem["MemTotal"] * 0.1 else 0))
    du = shutil.disk_usage("/")
    rows.append(("DISK", f"{fmt_bytes(du.free)} free of {fmt_bytes(du.total)}", 1 if du.free < du.total * 0.05 else 0))
    flags = throttled_text(vcgencmd("get_throttled"))
    if vcgencmd("get_throttled"):
        rows.append(("POWER", ", ".join(flags) if flags else "OK", 1 if flags else 0))
    return rows


BUTTONS = {"pair": "PAIR A PHONE", "restart": "RESTART", "poweroff": "SHUT DOWN"}


class Info(Shell):
    name = "info"

    def start(self):
        self.rows, self.at = gather(), 0
        self.offered, self.sel, self.note = [], 0, ""
        threading.Thread(target=self._ask_launcher, daemon=True, name="info-actions").start()

    def _ask_launcher(self):
        """Which actions the launcher offers on this machine. Empty when this app was started by hand (no launcher to ask)."""
        st = control.ask("launcher", {"cmd": "status"}).get("status") or {}
        self.later(lambda: setattr(self, "offered", [a for a in ("pair", "restart", "poweroff") if a in st.get("actions", [])]))

    def handle(self, cmd):
        if self.offered and cmd in ("left", "right", "up", "down"):
            self.sel = (self.sel + (1 if cmd in ("right", "down") else -1)) % len(self.offered)
        elif self.offered and cmd == "enter":
            action = self.offered[self.sel]
            r = control.ask("launcher", {"cmd": "do", "action": action}, timeout=4)
            self.note = "" if r.get("ok") else "Not available right now"          # when it works the launcher takes the screen over
        else:
            super().handle(cmd)

    def status(self):
        st = super().status()
        st["screen"] = "THIS DEVICE"
        return st

    def tick(self, now):
        if now - self.at > 2:
            self.rows, self.at = gather(), now

    def draw(self, now):
        out = [ui.background()] + ui.header("THIS DEVICE")
        for i, (label, value, level) in enumerate(self.rows):
            y = ui.SY + 62 + i * 36
            out.append(ui.text(ui.SX + 4, y + 6, label, 16, ui.DIM, border=1))
            out.append(ui.text(ui.SX + 190, y, ui.esc(value), 24, ui.WARN if level else ui.WHITE, border=1))
        if self.offered:
            n, gap = len(self.offered), 14
            bw = min(200, (ui.W - 2 * ui.SX - gap * (n - 1)) // n)
            x0, y0 = (ui.W - (n * bw + (n - 1) * gap)) // 2, ui.H - ui.SY - 100
            for i, a in enumerate(self.offered):
                x, on = x0 + i * (bw + gap), i == self.sel
                out.append(ui.rect(x, y0, x + bw, y0 + 46, ui.HILITE if on else "&H281810&", "40" if on else "30"))
                if on:
                    out += [ui.rect(x, y0, x + bw, y0 + 3, ui.AMBER), ui.rect(x, y0 + 43, x + bw, y0 + 46, ui.AMBER)]
                out.append(ui.text(x + bw // 2, y0 + 14, BUTTONS[a], 17, (ui.RED if a == "poweroff" else ui.AMBER) if on else ui.WHITE, an=8, border=1))
        hint = "LEFT/RIGHT pick   ENTER do it   Q back to the menu" if self.offered else "Q back to the menu"
        return "\n".join(out + ui.footer(hint, self.note))


if __name__ == "__main__":
    run_app(Info)
