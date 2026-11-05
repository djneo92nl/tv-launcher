# Writing an app

An app is a folder `apps/<id>/` with an `app.py`. The launcher starts it as its own process (own mpv, own keyboard), so a bug
in one app cannot take the menu down, and an app can live in a different repo (set `dir` to its absolute path in `launcher.json`).

Copy `apps/_template/app.py`. The whole contract:

```python
from tvkit import ui
from tvkit.shell import Shell, run_app

class MyApp(Shell):
    name = "my-app"                      # log file name
    mpv_args = []                        # extra mpv options this app needs (the photo app: --image-display-duration=inf)

    def start(self): ...                 # once, screen is open
    def handle(self, cmd): ...           # "up" "down" "left" "right" "enter" "back" "quit" "digit:3" "volup" "voldown" "mute" "info" "menu" ...
    def tick(self, now): ...             # about 4 times a second; slow work goes in a thread
    def on_mpv(self, ev): ...            # mpv events (end-file, file-loaded) for apps that play things
    def draw(self, now): return "..."    # ASS text; mpv is only told when it changes

if __name__ == "__main__":
    run_app(MyApp)
```

`super().handle(cmd)` does the standard things: `q` / ESC leave the app (exit code 0 = back to the menu), volume and mute go to mpv.
`self.mpv` is the `Mpv` controller (`play(url)`, `command(...)`, `get(prop)`); `self.cfg` is config.json plus `cfg["args"]`.
Add the app to `launcher.json`: `{"id": "my-app", "name": "MY APP", "desc": "...", "icon": "app", "dir": "apps/my-app", "cmd": ["python3", "app.py", "--profile", "{profile}"]}`.

## Drawing (`tvkit/ui.py`)

The IPTV look: `background()`, `header(title)`, `footer(hint, note)`, `ListView` + `list_rows(...)` for long lists, `tile_grid` for icon grids,
`toast(msg)`, `progress(...)`, `wrap(text, width)` and `text(x, y, s, size, color, an, bold, border)` / `rect(...)` for the rest.
The canvas is 576 high and 768 wide (4:3) or 1024 wide (16:9): use `ui.W`, never a hard-coded width. Keep 54 / 40 px free at the edges (CRT overscan).
Icons are 16x16 strings in `ui.ICONS`; add yours there.

## Pictures

mpv draws ASS text and shapes from `draw()`; real pictures (covers, posters) go through `tvkit/images.py`: `parse_image(bytes)` then `show(mpv, id, x, y, w, h, bgra)`.
Ask servers for BMP or PNG, both decoded with the standard library (ReMoment: `/api/artwork/<hash>/<size>.bmp`; Jellyfin: `?format=Png`, because a real Jellyfin ignores `format=Bmp` and sends JPEG, which cannot be decoded without an imaging library). `images.parse_image(bytes)` picks the decoder. **Pictures are drawn above all text and shapes**
(measured on mpv for the Mac): put labels beside or below a cover, never on it, and hide pictures (`images.hide`) when a menu or toast has to cover them.
mpv wants window pixels, not canvas pixels: multiply by `self.window_scale()`.

## Playing things (`tvkit/player.py`)

`Playback(self.mpv, Positions(cache_dir))`: `start(url, title, key=...)` resumes from where that key was stopped, `handle(cmd)` takes the playback keys
(left / right seek 10 s, up / down 60 s, Enter pause, `c` subtitles, `s` audio track, `i` the bar), `on_mpv(ev)` returns `"ended"` or `"error"`,
`tick(now)` saves the position every 10 s, `osd(now)` gives the title / position bar / notices to put in your `draw()`, `stop()` saves and stops.
`apps/media/app.py` is the worked example (it also plays the next file in the folder when one ends). ReMoment and Jellyfin use the same object.

## Covers and grids

`ui.GridView(items, cols, rows)` for cover / poster grids, `ui.Keyboard` for an on-screen keyboard (letters also arrive as `char:x` and `backspace` from a real
keyboard while `tvkit.inputs.TEXT_MODE` is True), `ui.ass_colour("#rrggbb")` for colours from an API, and `Shell.later(fn)` to hand a result from a download thread
back to the main loop. `apps/remoment/app.py` shows the whole pattern: a worker thread fetches BMP covers, `images.show` puts them above the text, labels sit below.
Choose the cover size from what fits the window (`window_scale()`): the server makes 32 to 160 px covers.

## Network (`tvkit/net.py`)

`get_json`, `request` (timeout, size limit, fixed User-Agent, TLS verified, errors never contain the URL) and `write_private` for tokens (0600). Never log a token.

## Tests

`tests/support.py` has `fake_shell(MyApp)` (an app with a recording fake mpv), `press(app, "down", "enter")` and `load_app("my-app")`.
Run them all: `python3 -m unittest discover -s tests`. No network and no mpv needed.
