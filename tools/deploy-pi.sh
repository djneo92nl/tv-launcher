#!/bin/sh
# Put TV Launcher (and the Spelunkr TV and IPTV clients, if they sit next to it) on a Raspberry Pi in one go.
#   sh tools/deploy-pi.sh pi@192.168.1.169            copy + install, NOT enabled at boot (try it first)
#   sh tools/deploy-pi.sh pi@192.168.1.169 --enable   also start it at every boot
# Run it from your Mac. It asks for the Pi's password once or twice (sudo).
set -e
TARGET="${1:?usage: sh tools/deploy-pi.sh pi@ADDRESS [--enable]}"
ENABLE="$2"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$HERE")"
EXCLUDES="--exclude __pycache__ --exclude .git --exclude dist --exclude .claude --exclude .DS_Store"

echo "== copying TV Launcher"
rsync -a $EXCLUDES "$HERE/" "$TARGET:~/tv-launcher/"
EXTRA=""
if [ -f "$PARENT/TV Tuner/tv.py" ]; then
  echo "== copying Spelunkr TV"
  rsync -a $EXCLUDES "$PARENT/TV Tuner/" "$TARGET:~/spelunkr-tv/"
  EXTRA="$EXTRA --spelunkr ~/spelunkr-tv"
fi
if [ -f "$PARENT/TV Tuner IPTV/tv.py" ]; then
  echo "== copying IPTV"
  rsync -a $EXCLUDES "$PARENT/TV Tuner IPTV/" "$TARGET:~/spelunkr-iptv/"
  EXTRA="$EXTRA --iptv ~/spelunkr-iptv"
fi

echo "== installing (needs the Pi's password)"
ssh -t "$TARGET" "cd ~/tv-launcher && sudo sh pi/setup.sh $EXTRA $ENABLE"

cat <<'NEXT'

Done. To try it by hand first (recommended: the screen shows any error right in the terminal):
  ssh -t PI 'cd /opt/tv-launcher && python3 launcher.py --profile auto'
To start it as a service (and at every boot) once that works:  sh tools/deploy-pi.sh PI --enable
NEXT
