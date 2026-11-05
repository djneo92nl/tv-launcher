# Web interface

Control the TV from a phone on the same network: a remote, the apps, your settings, and restart / shut down. It runs inside the launcher (one thread), so it keeps working while an app is on screen.
Off with `"web": {"enabled": false}` in `config.json` (or Settings > Web). Port 8090 (Settings > Web port).

## Pairing

1. On the TV open **THIS PI** and choose **PAIR A PHONE** (restart and shut down are there too). It shows the address (`http://<ip>:8090`) and a 6-digit code, valid for 5 minutes.
2. On the phone open that address, type the code and a name for the phone. The phone stays paired (a cookie) until you sign it out or remove it under System.
3. Paired phones are listed under System; any of them can be removed, and so can the one you are holding.

The code is single-use, new each time pairing is opened, and not shown anywhere else (not in the log, not in the API).

## What it protects against

| risk | what stops it |
|---|---|
| Someone on the network guessing the code | 5 tries per minute per address; after 10 wrong tries from anywhere the code is dead and pairing locks for 3 minutes; constant-time comparison |
| A stolen `devices.json` | only SHA-256 hashes of the 256-bit tokens are stored (file mode 0600) |
| A web page on another site driving the TV | every POST needs the `X-TVL` header and a JSON body, so a browser must send a CORS preflight, which is never answered; a present `Origin` must match; cookie is `SameSite=Strict` |
| DNS rebinding | the `Host` header must be an IP address, `localhost`, this machine's name or `*.local` |
| Script injection through names in a feed, a manifest or a server | the page builds everything with `textContent`; the Content-Security-Policy allows scripts and styles only from this server and no inline code at all |
| Switching the TV off from the sofa-less corner of the network | Restart, Shut down and installing, updating or removing apps are not done on the phone's word: the TV shows a dialog, ENTER allows, BACK refuses, and it expires after 60 seconds |
| A crashing or hostile app | each app has its own process and socket; the control vocabulary is closed (known keys, typed text, status, quit) and cannot run code or read files |

**Not covered: there is no TLS.** The code and the cookie cross your network in clear text. Use it on a network you trust and do not forward the port to the internet.

## Screen edges

Some TVs and monitors crop the picture, so the first and last letters of a line can be cut off. Settings > Display > **Screen edge margin** (percent, default 7) pulls everything away from the edges;
raise it until nothing is cut. It is a percentage, so it works the same on 4:3 and 16:9 screens.

## Settings

The Settings tab is generated from `tvkit/settings.py`. Changes are validated, then saved to `~/.config/tv-launcher/settings.json` (mode 0600), which is merged over `config.json`;
the file you edit by hand is never rewritten. Apps read their settings when they start, so a change shows the next time you open the app; the menu (title, hidden apps, order, autostart) updates at once.
Secrets (your Jellyfin token) are never settings and never go through the web interface.

## API (for scripts and Home Assistant)

All calls need the cookie from pairing and (for POST) the headers `X-TVL: 1` and `Content-Type: application/json`.

```
GET  /api/session              {paired, name, server}            (no cookie needed)
POST /api/pair                 {pin, name}                        (no cookie needed)
GET  /api/status               the launcher's and the running app's status (screen, what plays, position)
POST /api/key {name}           up down left right enter back info menu captions last volup voldown mute digit:0-9 ...
POST /api/text {text}          typed into an on-screen keyboard (printable ASCII, 64 characters)
POST /api/home                 leave the running app
GET  /api/apps                 POST /api/apps/start {id}
GET  /api/settings             POST /api/settings {changes: {"clock.h24": false}}
GET  /api/devices              POST /api/devices/revoke {id}      POST /api/logout
POST /api/system {action}      restart | poweroff  ->  202 {confirm: id}; GET /api/confirm/<id> -> pending | allowed | denied | expired
GET  /api/log
GET  /api/store                the repositories and what they offer      POST /api/store/refresh
POST /api/store/install {repo, id}   ->  202 {job}     POST /api/store/remove {id}   ->  202 {job}      GET /api/job/<id>
```

Spelunkr TV and IPTV are separate programs with their own web remote (`python3 tv.py --web`); they are not reachable through this interface yet.
