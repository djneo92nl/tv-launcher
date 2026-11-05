#!/bin/sh
# Install TV Launcher on a Linux desktop (X11 or Wayland) for the current user: an application-menu entry with an icon.
#   sh desktop/install.sh               install (copy to ~/.local/share/tv-launcher, add the menu entry)
#   sh desktop/install.sh --uninstall   remove both
# Needs mpv and python3 (sudo apt install mpv python3). Spelunkr TV and IPTV are found next to this repo, in ~/spelunkr-tv, ~/spelunkr-iptv or /opt.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
DIR="$DATA/tv-launcher"
ENTRY="$DATA/applications/tv-launcher.desktop"

if [ "$1" = "--uninstall" ]; then
  rm -rf "$DIR" "$ENTRY"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" 2>/dev/null || true
  echo "Removed $DIR and $ENTRY"
  exit 0
fi

command -v mpv >/dev/null 2>&1 || echo "note: mpv is not installed yet (sudo apt install mpv)"
mkdir -p "$DIR" "$DATA/applications"
cp "$HERE"/launcher.py "$DIR"/
cp -R "$HERE"/tvkit "$HERE"/apps "$DIR"/
find "$DIR" -name __pycache__ -type d -prune -exec rm -rf {} +
[ -f "$DIR/config.json" ] || cp "$HERE/config.json" "$DIR/config.json"            # your edits survive an update
python3 - "$HERE/launcher.json" "$DIR/launcher.json" "$(dirname "$HERE")" <<'PY'
import json, os, sys
src, dst, parent = sys.argv[1:4]
cfg = json.load(open(dst)) if os.path.exists(dst) else json.load(open(src))
cfg["search_dirs"] = sorted(set(cfg.get("search_dirs", []) + [parent]))
json.dump(cfg, open(dst, "w"), indent=2)
PY
python3 "$HERE/desktop/make_icon.py" "$DIR/tv-launcher.png" 256

cat > "$ENTRY" <<DESKTOP
[Desktop Entry]
Type=Application
Name=TV Launcher
Comment=Home menu for Spelunkr TV, IPTV, Jellyfin, ReMoment and more
Exec=python3 "$DIR/launcher.py"
Icon=$DIR/tv-launcher.png
Terminal=false
Categories=AudioVideo;Video;Player;
Keywords=tv;iptv;jellyfin;media;
DESKTOP
chmod 644 "$ENTRY"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" 2>/dev/null || true
echo "Installed. Find \"TV Launcher\" in your application menu, or run:  python3 $DIR/launcher.py"
echo "Log: ~/.cache/tv-launcher/launcher.log.  To autostart at login, copy $ENTRY to ~/.config/autostart/"
