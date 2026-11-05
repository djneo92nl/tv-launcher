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

mpv draws ASS text and shapes from `draw()`; real pictures (covers, posters) go through `tvkit/images.py`: `parse_bmp(bytes)` then `show(mpv, id, x, y, w, h, bgra)`.
Ask servers for BMP (ReMoment: `/api/artwork/<hash>/<size>.bmp`; Jellyfin: `?format=Bmp`). **Pictures are drawn above all text and shapes**
(measured on mpv for the Mac): put labels beside or below a cover, never on it, and hide pictures (`images.hide`) when a menu or toast has to cover them.
mpv wants window pixels, not canvas pixels: multiply by `self.window_scale()`.

## Network (`tvkit/net.py`)

`get_json`, `request` (timeout, size limit, fixed User-Agent, TLS verified, errors never contain the URL) and `write_private` for tokens (0600). Never log a token.

## Tests

`tests/support.py` has `fake_shell(MyApp)` (an app with a recording fake mpv), `press(app, "down", "enter")` and `load_app("my-app")`.
Run them all: `python3 -m unittest discover -s tests`. No network and no mpv needed.
