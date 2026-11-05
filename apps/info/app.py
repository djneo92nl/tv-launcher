#!/usr/bin/env python3
"""This Pi: address, temperature, memory, disk and power warnings, refreshed every two seconds."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))    # the repo root, for tvkit

import os
import shutil
import socket
import subprocess

from tvkit import ui
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


class Info(Shell):
    name = "info"

    def start(self):
        self.rows, self.at = gather(), 0

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
            y = ui.SY + 64 + i * 46
            out.append(ui.text(ui.SX + 4, y + 6, label, 18, ui.DIM, border=1))
            out.append(ui.text(ui.SX + 200, y, ui.esc(value), 26, ui.WARN if level else ui.WHITE, border=1))
        return "\n".join(out + ui.footer("Q back to the menu"))


if __name__ == "__main__":
    run_app(Info)
