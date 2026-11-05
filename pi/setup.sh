#!/bin/sh
# Install TV Launcher on Raspberry Pi OS Lite.   Run on the Pi from this folder:
#   sudo sh pi/setup.sh [--spelunkr DIR] [--iptv DIR] [--enable]
#   --spelunkr DIR   also install the Spelunkr TV client (its repo folder) into /opt/spelunkr-tv
#   --iptv DIR       also install the IPTV client (its repo folder) into /opt/spelunkr-iptv
#   --enable         start the launcher on every boot (and stop spelunkr-tv.service from doing so)
set -e
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SPELUNKR=""; IPTV=""; ENABLE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --spelunkr) SPELUNKR="$2"; shift 2 ;;
    --iptv) IPTV="$2"; shift 2 ;;
    --enable) ENABLE=1; shift ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
done

apt-get update
apt-get install -y mpv python3 ca-certificates cec-utils
D=/opt/tv-launcher
mkdir -p $D/apps $D/tvkit
cp "$HERE"/launcher.py $D/
cp "$HERE"/tvkit/*.py $D/tvkit/
cp -R "$HERE"/apps/. $D/apps/
for f in config.json launcher.json; do [ -f $D/$f ] || cp "$HERE/$f" $D/$f; done      # your edits survive an update
chown -R pi:pi $D

install_client() {      # $1 = repo folder, $2 = destination
  [ -f "$1/tv.py" ] || { echo "$1/tv.py not found"; exit 1; }
  mkdir -p "$2/fonts"
  cp "$1"/*.py "$2"/
  [ -f "$2/config.json" ] || cp "$1"/config.json "$2"/config.json
  [ -d "$1/fonts" ] && cp "$1"/fonts/*.ttf "$2"/fonts/ 2>/dev/null || true
  chown -R pi:pi "$2"
  echo "installed $1 into $2"
}
[ -z "$SPELUNKR" ] || install_client "$SPELUNKR" /opt/spelunkr-tv
[ -z "$IPTV" ] || install_client "$IPTV" /opt/spelunkr-iptv

# reboot and shut down without a password, for these two commands only
SUDOERS=/etc/sudoers.d/tv-launcher
printf 'pi ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff\n' > "$SUDOERS.tmp"
chmod 440 "$SUDOERS.tmp"
if visudo -cf "$SUDOERS.tmp" >/dev/null; then mv "$SUDOERS.tmp" "$SUDOERS"; else rm -f "$SUDOERS.tmp"; echo "sudoers rule not installed"; fi

install -m 644 "$HERE"/pi/tv-launcher.service /etc/systemd/system/tv-launcher.service
systemctl daemon-reload
if [ "$ENABLE" = 1 ]; then
  systemctl disable spelunkr-tv 2>/dev/null || true
  systemctl enable --now tv-launcher
  echo "Launcher enabled: it starts on every boot. Log: ~pi/.cache/tv-launcher/launcher.log"
else
  echo "Installed, not enabled. Try it:  sudo systemctl disable --now spelunkr-tv; sudo systemctl enable --now tv-launcher"
fi
