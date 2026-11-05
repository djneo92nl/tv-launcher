# TV Launcher

A small app framework for a TV box (a Raspberry Pi on HDMI or composite, or any Linux laptop or Mac): it boots into an **icon grid**, you pick an
app with the arrow keys, a remote (HDMI-CEC) or `1`-`9`, and `q` brings you back. Everything is drawn by `mpv`; Python standard library only.

| app | what | needs |
|---|---|---|
| **clock** | big clock and date, drifts a few pixels so nothing burns in | nothing |
| **media** | browse USB sticks, shares and folders; plays video and music with subtitles, seeks, remembers where you stopped, plays the next file | nothing |
| **remoment** | your ReMoment music library: album cover grid, artists, playlists, recently played, favourites, search (on-screen keyboard), now playing with the cover's colours; plays through mpv | a ReMoment server |
| **jellyfin** | your Jellyfin movies and shows: sign-in with Quick Connect (a code on the TV, approved in Jellyfin) or an on-screen password keyboard (only the token is kept, mode 0600), continue watching, next up, poster grids, series / seasons / episodes, search, resume, progress reported back, direct play with an HLS transcode fallback | a Jellyfin server |
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

## As a desktop app

**Mac** (`brew install mpv` first):

```bash
sh mac/make_app.sh            # builds dist/TV Launcher.app, with an icon
open "dist/TV Launcher.app"   # log: ~/Library/Logs/tv-launcher.log
```

**Linux desktop** (X11 or Wayland; `sudo apt install mpv python3`):

```bash
sh desktop/install.sh              # adds "TV Launcher" to your application menu (copies to ~/.local/share/tv-launcher)
sh desktop/install.sh --uninstall
```

The profile is chosen for the machine (`mac`, `desktop`, or `pi` on a Pi without a desktop; override with `--profile` or `"profile"` in `config.json`).
Spelunkr TV and IPTV are found in `/opt`, in `~/spelunkr-tv` / `~/spelunkr-iptv`, or in folders next to this repo (`TV Tuner`, `TV Tuner IPTV`); the packaged copies
remember where this repo's neighbours are (`search_dirs` in `launcher.json`). Closing the window quits the launcher; if mpv crashes, it is restarted.
The Linux desktop entry is **untested on real Linux hardware** (the installer ran in a throwaway home folder on the Mac, and the Mac app was launched and closed).
Re-run the build or the installer after changing code: the packaged copy is a copy (your `config.json` is kept on Linux).

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

## Web interface (phone remote, settings)

On the TV open **THIS PI** and choose **PAIR A PHONE**, then type the address and 6-digit code it shows on your phone. You get a remote (with a text box that types into the on-screen keyboards), the list of apps (start, hide),
a settings page, and Restart / Shut down. Changes are saved to `~/.config/tv-launcher/settings.json`, merged over `config.json`. Restart and shut down ask for OK on the TV first.
Security model, API and limits (no TLS): [docs/WEB.md](docs/WEB.md). Apps get a small control socket for free (`tvkit/control.py`) and can report what they show by overriding `Shell.status()`.
Each app also has an `app.json` manifest (`tvkit/manifest.py`); the launcher discovers built-in apps and, later, installed ones from it.

## Installing more apps

Apps can come from a repository you add (a folder, a USB stick, a web server): **Store** tab on the phone, or `python3 launcher.py apps install ...` in a shell. Installing, updating and removing from the
phone ask for OK on the TV, check a SHA-256 and the package's contents first, and keep the old version for rollback. Installed apps run with the same rights as the launcher, so only add repositories you trust.
Build your own repository with `tools/make_repo.py`. Details and limits: [docs/APP-PACKAGES.md](docs/APP-PACKAGES.md).

## Writing an app

See [docs/APPS.md](docs/APPS.md): an app is one class with `handle`, `tick` and `draw`, about 40 lines for the template.

## Layout

| path | what |
|---|---|
| `launcher.py`, `launcher.json` | the home menu |
| `tvkit/shell.py` | the part every app shares: mpv, inputs, resize, crash restart, the loop |
| `tvkit/ui.py` | the flat IPTV look as drawing functions, list view, icon grid |
| `tvkit/player.py` | playback for apps: resume positions, seek / pause / subtitles keys, the position bar |
| `tvkit/web.py`, `tvkit/webpage.py` | the web interface: pairing, auth, API, the page |
| `tvkit/apps.py`, `tvkit/appcli.py`, `tools/make_repo.py` | packages, repositories, the installer, the `apps` command, the repository builder |
| `tvkit/control.py`, `tvkit/settings.py`, `tvkit/manifest.py` | the per-app control socket, settings schema and overlay, app manifests |
| `tvkit/net.py`, `tvkit/images.py`, `tvkit/config.py` | HTTP, BMP and PNG pictures, config |
| `tvkit/mpvctl.py`, `tvkit/inputs.py` | vendored from the TV Tuner repo (keyboard, USB / evdev, CEC, mpv JSON IPC) |
| `apps/` | the apps, and `_template` |
| `tools/fake_remoment.py`, `tools/fake_jellyfin.py` | pretend ReMoment and Jellyfin servers for tests and demos |
| `pi/` | `setup.sh` and the systemd unit |
| `mac/`, `desktop/` | the Mac app builder, the Linux desktop entry installer, and the icon drawing |

## License

GPL-3.0, see [LICENSE](LICENSE). `tvkit/mpvctl.py`, `tvkit/inputs.py` and `apps/news/feeds.py` come from the author's TV Tuner project. The launcher only starts the separate `mpv` program, it does not include it.
