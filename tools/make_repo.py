#!/usr/bin/env python3
"""Build a repository folder (zips + index.json) from apps on disk, ready to put on any static web server, a GitHub Pages site or a USB stick.

    python3 tools/make_repo.py --name "My apps" --out public/ apps/clock ../my-app          # each argument is an app folder (with app.json and app.py)
    python3 tools/make_repo.py --name "My apps" --out public/ --from-dir ../my-apps         # or: every sub-folder that has an app.json

The zips are reproducible (sorted files, fixed timestamps), so rebuilding an unchanged app gives the same checksum. Apps are checked with the same rules the installer uses;
a folder that would be refused is reported and left out. Serve the output folder and add its address as a repository (Settings > Apps, or `launcher.py apps repo add`).
"""
import argparse
import hashlib
import io
import json
import os
import stat
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tvkit import apps, manifest  # noqa: E402

SKIP_DIRS = {"__pycache__", ".git", "tests", "node_modules"}
SKIP_SUFFIX = (".pyc", ".pyo", ".bak", ".orig", ".swp")
STAMP = (2020, 1, 1, 0, 0, 0)


def build_zip(folder):
    """The package bytes for an app folder: deterministic, regular files only, hidden files and caches left out."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(files):
                if name.startswith(".") or name.endswith(SKIP_SUFFIX):
                    continue
                path = os.path.join(root, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                arc = os.path.relpath(path, folder).replace(os.sep, "/")
                info = zipfile.ZipInfo(arc, STAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                with open(path, "rb") as f:
                    z.writestr(info, f.read())
    return buf.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="*", help="app folders")
    ap.add_argument("--from-dir", help="a folder whose sub-folders are apps")
    ap.add_argument("--out", required=True, help="where to write index.json and the zips")
    ap.add_argument("--name", default="My apps")
    a = ap.parse_args(argv)
    folders = list(a.folders)
    if a.from_dir:
        folders += [os.path.join(a.from_dir, n) for n in sorted(os.listdir(a.from_dir)) if os.path.isfile(os.path.join(a.from_dir, n, "app.json"))]
    if not folders:
        sys.exit("no app folders given")
    os.makedirs(a.out, exist_ok=True)
    entries, problems = [], 0
    for folder in folders:
        try:
            m = manifest.read(folder)
            data = build_zip(folder)
            m2, _, _ = apps.inspect_package(data)                       # the same checks the installer will make
        except (manifest.ManifestError, apps.AppsError) as ex:
            print(f"left out {folder}: {ex}", file=sys.stderr)
            problems += 1
            continue
        fn = f"{m['id']}-{m['version']}.zip"
        with open(os.path.join(a.out, fn), "wb") as f:
            f.write(data)
        entries.append({"id": m["id"], "name": m["name"], "version": m["version"], "description": m["description"], "icon": m["icon"], "tvkit": m["tvkit"],
                        "permissions": m["permissions"], "author": m.get("author", ""), "url": fn, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        print(f"{fn}  {len(data):>7} bytes  {entries[-1]['sha256'][:12]}")
    if any(e["id"] for e in entries) and len({e["id"] for e in entries}) != len(entries):
        sys.exit("two apps have the same id")
    with open(os.path.join(a.out, "index.json"), "w") as f:
        json.dump({"name": a.name, "apps": entries}, f, indent=2)
        f.write("\n")
    print(f"wrote {len(entries)} app(s) to {a.out}/index.json" + (f" ({problems} left out)" if problems else ""))
    return 1 if problems and not entries else 0


if __name__ == "__main__":
    sys.exit(main())
