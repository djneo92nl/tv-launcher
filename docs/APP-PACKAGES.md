# App packages and repositories

Apps beyond the built-in ones come from a **repository**: any place that serves an `index.json` and the zips it lists (a folder, a USB stick, a static web server, GitHub Pages).
Nothing is contacted until you add a repository, and nothing is installed without your OK.

## Using it

On the phone: **Store** tab > Install. The TV then shows what is about to happen (name, version, where from, size, what the app says it needs) and waits: **ENTER installs, BACK cancels**.
Removing and updating work the same way. If the app is running, it is stopped first.

From a shell (works over SSH, no TV needed; you answer on the terminal, or add `--yes`):

```bash
python3 launcher.py apps repo add Official https://apps.example.org/     # an address, or a folder on this device
python3 launcher.py apps search                                          # what is on offer; installed, update available
python3 launcher.py apps install weather                                 # or a local file: apps install ./weather-1.0.0.zip
python3 launcher.py apps update                                          # every app that has a newer version
python3 launcher.py apps remove weather [--purge]                        # kept in a backup folder; --purge also deletes its data
python3 launcher.py apps rollback weather                                # back to the version before the last update or removal
```

Installed apps live in `~/.local/share/tv-launcher/apps/<id>/`, their data in `~/.local/share/tv-launcher/data/<id>/` (it survives updates and removal), backups in `apps-backup/`.

## What you can and cannot trust

**An installed app runs with the same rights as the launcher** (the same user, the same files, the network). The checks below stop accidents and tampering in transit; they do **not** make a malicious app safe.
Only add repositories you trust. The permissions an app lists (`network`, `files`, `exec`, `system`, `devices`) are shown to you before you approve, but they are the app's own claim and are **not enforced**.

What the installer does check, before anything is written:

* the download must match the **SHA-256 and size** in the repository's index, and the package's own manifest must match what the index promised (id and version);
* the zip must be sane: at most 5 MB, 200 files, 20 MB unpacked, no file over 5 MB, no zip bombs (compression ratio), no encryption, no duplicate names;
* no absolute paths, `..`, backslashes, drive letters, hidden files, **symlinks or special files**;
* `app.json` must be valid and `app.py` present at the top level; **every `.py` must compile** (nothing is imported or run while installing);
* an installed app cannot take the id of a built-in or external app (`clock`, `spelunkr` ...) or a reserved name;
* an update must be a **newer version**; it is unpacked beside the old one and moved into place in one step, the old version kept for rollback; a failed update leaves the working version alone;
* from the web page the phone only names *which app from which repository*; the address and checksum always come from the repository you configured.

The hash is only as trustworthy as the index and the connection it came over, so prefer **https**. Plain http repositories work (marked "not encrypted" on the page and the TV) but anyone on the network path could swap both index and package.
Real signatures need a cryptography library that is not in the standard library, so they are a possible later addition.

## Making a package

A package is a zip with these at its **top level** (no wrapping folder): `app.json`, `app.py`, and anything else the app needs (other `.py` files, data). Python standard library and `tvkit` only: there is no pip.

```json
{"id": "weather", "name": "WEATHER", "version": "1.0.0", "description": "Today's forecast", "icon": "info", "tvkit": 1, "order": 150,
 "permissions": [{"kind": "network", "detail": "api.open-meteo.com"}]}
```

`icon` is a name from `tvkit/ui.py` (`tv`, `clock`, `photos`, `music`, `news`, `info`, `folder`, `stream` ...) or a custom 16x16 picture (`{"colour": "a", "rows": [16 strings of 16 characters from . w a b r g]}`).
`tvkit` is the lowest framework version the app needs. `id`: lower-case letters, digits, dashes. See [APPS.md](APPS.md) for how to write the app itself.
The launcher starts it as `python3 app.py --profile <profile>` in its folder with `tvkit` already importable.

## Making a repository

```bash
python3 tools/make_repo.py --name "My apps" --out public/ path/to/weather path/to/another      # or --from-dir path/to/my-apps
```

writes `public/index.json` and one zip per app (reproducible: the same app gives the same bytes and checksum). An app that would be refused by the installer is reported and left out.
Put the folder on any static web server (or a USB stick) and add its address as a repository. `index.json` looks like:

```json
{"name": "My apps", "apps": [{"id": "weather", "name": "WEATHER", "version": "1.0.0", "description": "...", "icon": "info", "tvkit": 1,
                              "permissions": [], "url": "weather-1.0.0.zip", "sha256": "<64 hex>", "size": 1234}]}
```

`url` is relative to the index, or an absolute http(s) address. A relative address cannot leave the repository's folder. An entry the launcher cannot use (bad checksum field, too new `tvkit`) is skipped and named on the page, and the rest still work.
