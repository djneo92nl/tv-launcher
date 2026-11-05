# TV Launcher

A small app framework for a TV box (a Raspberry Pi on HDMI or composite, or any Linux laptop or Mac): it boots into an **icon grid**, you pick an
app with the arrow keys, a remote (HDMI-CEC) or `1`-`9`, and `q` brings you back. Everything is drawn by `mpv`; Python standard library only.

| app | what | needs |
|---|---|---|
| **clock** | big clock and date, drifts a few pixels so nothing burns in | nothing |
| **media** | browse USB sticks, shares and folders; plays video and music with subtitles, seeks, remembers where you stopped, plays the next file | nothing |
| **remoment** | your ReMoment music library: album cover grid, artists, playlists, recently played, favourites, search (on-screen keyboard), now playing with the cover's colours; plays through mpv | a ReMoment server |
| **jellyfin** | your Jellyfin movies and shows: on-screen sign-in (only the token is kept, mode 0600), continue watching, next up, poster grids, series / seasons / episodes, search, resume, progress reported back, direct play with an HLS transcode fallback | a Jellyfin server |
| **photos** | slideshow of folders and USB sticks (`photos.dirs`), shuffle, pause | nothing |
| **news** | RSS / Atom headlines and stories (`feeds`), cached 15 min, works offline from the cache | network |
| **info** | address, temperature, memory, disk, under-voltage warnings | nothing |
| Spelunkr TV, IPTV | the sibling repos, started as external apps from `/opt/spelunkr-tv` and `/opt/spelunkr-iptv` | their own setup |


```bash
python3 launcher.py --list           # which apps it found
python3 launcher.py --profile mac    # try it on a Mac (brew install mpv)
python3 apps/clock/app.py            # or run one app by itself
python3 -m unittest discover -s tests
```

## On a Pi

```bash
sudo sh pi/setup.sh --spelunkr ../"TV Tuner" --iptv ../"TV Tuner IPTV" --enable
```

Installs to `/opt/tv-launcher` (and the two TV clients, if given, into their own folders), lets the menu restart and shut down the Pi, and boots into the menu.
Keep your own settings in `/opt/tv-launcher/config.json` (profiles = the mpv command line for pi / hdmi / desktop, plus `feeds`, `photos`, `clock`, `media.roots`, `remoment.host`, `jellyfin.host`)
and `launcher.json` (`autostart`: an app id to start a few seconds after boot unless a key is pressed). An update keeps both files.
Log: `~/.cache/tv-launcher/launcher.log`, and one log per app next to it.

**Untested on a real Pi.** The menu, the start / return cycle and every app ran on a Mac; the Pi profiles are copied from the TV Tuner repo, where they are
also marked as untested for some boards.

## Writing an app

See [docs/APPS.md](docs/APPS.md): an app is one class with `handle`, `tick` and `draw`, about 40 lines for the template.

## Layout

| path | what |
|---|---|
| `launcher.py`, `launcher.json` | the home menu |
| `tvkit/shell.py` | the part every app shares: mpv, inputs, resize, crash restart, the loop |
| `tvkit/ui.py` | the flat IPTV look as drawing functions, list view, icon grid |
| `tvkit/player.py` | playback for apps: resume positions, seek / pause / subtitles keys, the position bar |
| `tvkit/net.py`, `tvkit/images.py`, `tvkit/config.py` | HTTP, BMP pictures, config |
| `tvkit/mpvctl.py`, `tvkit/inputs.py` | vendored from the TV Tuner repo (keyboard, USB / evdev, CEC, mpv JSON IPC) |
| `apps/` | the apps, and `_template` |
| `tools/fake_remoment.py`, `tools/fake_jellyfin.py` | pretend ReMoment and Jellyfin servers for tests and demos |
| `pi/` | `setup.sh` and the systemd unit |
