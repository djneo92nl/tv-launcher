#!/bin/sh
# Build "TV Launcher.app" (double-click, with an icon).   Usage: sh mac/make_app.sh [destination-folder]
# The app holds a copy of the launcher and its apps: run this again after changing the code.
# It finds the Spelunkr TV and IPTV clients next to this repo (and in /opt); mpv comes from Homebrew (brew install mpv).
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$HERE")"
DEST="${1:-$HERE/dist}"
APP="$DEST/TV Launcher.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

cp "$HERE"/launcher.py "$HERE"/config.json "$APP/Contents/Resources/app/"
cp -R "$HERE"/tvkit "$HERE"/apps "$APP/Contents/Resources/app/"
find "$APP" -name __pycache__ -type d -prune -exec rm -rf {} +
# the packaged copy cannot see "../TV Tuner", so tell it where this repo's neighbours are
python3 - "$HERE/launcher.json" "$APP/Contents/Resources/app/launcher.json" "$PARENT" <<'PY'
import json, sys
src, dst, parent = sys.argv[1:4]
cfg = json.load(open(src))
cfg["search_dirs"] = sorted(set(cfg.get("search_dirs", []) + [parent]))
json.dump(cfg, open(dst, "w"), indent=2)
PY

ICONSET="$(mktemp -d)/AppIcon.iconset"
python3 "$HERE/desktop/make_icon.py" --iconset "$ICONSET"
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>TV Launcher</string>
  <key>CFBundleDisplayName</key><string>TV Launcher</string>
  <key>CFBundleIdentifier</key><string>local.tv-launcher</string>
  <key>CFBundleExecutable</key><string>tv-launcher</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

# apps start with a minimal PATH: add Homebrew (mpv), and prefer its python3 (the system one is old)
cat > "$APP/Contents/MacOS/tv-launcher" <<'LAUNCH'
#!/bin/sh
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
APPDIR="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
LOG="$HOME/Library/Logs/tv-launcher.log"
exec >>"$LOG" 2>&1
echo "--- $(date) start"
command -v mpv >/dev/null 2>&1 || { echo "mpv not found: brew install mpv"; osascript -e 'display alert "TV Launcher needs mpv" message "Install it with:  brew install mpv"' >/dev/null 2>&1; exit 1; }
for PY in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$PY" ] && exec "$PY" "$APPDIR/launcher.py" "$@"
done
echo "python3 not found"; exit 1
LAUNCH
chmod +x "$APP/Contents/MacOS/tv-launcher"

echo "Built: $APP"
echo "Open it with:  open \"$APP\"     (log: ~/Library/Logs/tv-launcher.log, and ~/.cache/tv-launcher/launcher.log)"
