"""Keep the Linux text console from showing between apps.

Every app has its own mpv, so while the launcher hands over from one to the next (a couple of seconds on a Raspberry Pi 2) nothing owns the display and the
kernel shows the text console: login text, boot messages. Two small things stop that: put the virtual console into graphics mode (the kernel then does not
draw text on it) and fill its framebuffer with black. Both are no-ops anywhere else (a Mac, an SSH session, a desktop terminal) and every failure is ignored:
a TV that shows text for a moment is better than a launcher that refuses to start.
"""
import os
import re
import sys

KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3A, 0, 1


def on_virtual_console(fd=0):
    """True when fd is a Linux virtual console (/dev/tty1 ...): not an SSH pty, not a terminal window."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        return re.fullmatch(r"/dev/tty[0-9]+", os.ttyname(fd)) is not None
    except OSError:
        return False


def set_graphics(on, fd=0):
    """Switch the console between graphics mode (no text drawn) and text mode. True if it was done."""
    if not on_virtual_console(fd):
        return False
    try:
        import fcntl
        fcntl.ioctl(fd, KDSETMODE, KD_GRAPHICS if on else KD_TEXT)
        return True
    except (OSError, ImportError):
        return False


def blank_framebuffer(device="/dev/fb0", sysdir="/sys/class/graphics/fb0"):
    """Fill the console framebuffer with black, so whatever text was on it is gone. True if it was done."""
    try:
        with open(os.path.join(sysdir, "virtual_size")) as f:
            width, height = (int(x) for x in f.read().strip().split(","))
        with open(os.path.join(sysdir, "bits_per_pixel")) as f:
            bpp = int(f.read().strip())
        left, chunk = width * height * bpp // 8, bytes(65536)
        fd = os.open(device, os.O_WRONLY)                      # write-only and never truncating: this is a device, not a file we own
        try:
            while left > 0:
                left -= os.write(fd, chunk[:min(left, len(chunk))])
        finally:
            os.close(fd)
        return True
    except (OSError, ValueError):
        return False


def quiet(fd=0):
    """Black and silent: blank the framebuffer, then stop the console drawing. Returns True if the console is now in graphics mode (so restore() has work to do)."""
    if not on_virtual_console(fd):
        return False
    blank_framebuffer()
    return set_graphics(True, fd)


def restore(fd=0):
    return set_graphics(False, fd)
