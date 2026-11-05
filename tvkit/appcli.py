"""`python3 launcher.py apps ...`: manage apps from a shell (over SSH, no TV needed). You are at the keyboard, so there is no confirmation dialog;
a question on the terminal replaces it, and --yes answers it for scripts.

    apps list                          built-in, external and installed apps
    apps repo list | add NAME ADDRESS | remove NAME
    apps search [TEXT]                 what the repositories offer, and what is installed or has an update
    apps install ID_OR_FILE.zip [--repo NAME] [--yes]
    apps update [ID] [--yes]
    apps remove ID [--purge] [--yes]   (kept in a backup folder; --purge also deletes the app's data)
    apps rollback ID                   go back to the version before the last update or removal
"""
import hashlib
import os
import sys

from . import apps, manifest, settings


def _confirm(question, yes, input_fn, interactive):
    if yes:
        return True
    if not interactive:
        print(f"{question}\nNot asked: this is not a terminal. Run it again with --yes to go ahead.", file=sys.stderr)
        return False
    return input_fn(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _show_plan(plan, source, out):
    m = plan["manifest"]
    out(f"{plan['action'].capitalize()} {m['name']} {m['version']}" + (f" (now {plan['from']})" if plan["from"] else "") + f"  [{plan['size'] // 1024 or 1} KB, from {source}]")
    out(f"  {m['description']}" if m["description"] else "  (no description)")
    for line in apps.describe_permissions(m["permissions"]) or ["asks for no special access (this is what it says, not something enforced)"]:
        out(f"  - {line}")
    out("  It runs with the same rights as the launcher. Only install apps from sources you trust.")


def run(argv, reserved, repos, out=print, input_fn=input, interactive=None, store=None):
    """argv after the word `apps`. reserved: ids installed apps may not take. repos: () -> [{name, url}]. Returns an exit code."""
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if not argv or argv[0] in ("-h", "--help", "help"):
        out(__doc__.strip())
        return 0
    cmd, rest = argv[0], argv[1:]
    flags = {a for a in rest if a.startswith("--")}
    pos = [a for a in rest if not a.startswith("--")]
    yes = "--yes" in flags
    store = store or apps.Store(repos)
    try:
        if cmd == "list":
            for a in sorted(apps.installed(), key=lambda a: a["id"]):
                out(f"{a['id']:<16} {a['version']:<8} installed   {a['name']}")
            out(f"(built-in and external apps are not listed here; {len(reserved)} ids are reserved)")
            return 0
        if cmd == "repo":
            return _repo(pos, repos, out)
        if cmd == "search":
            text = (pos[0] if pos else "").lower()
            have = {a["id"]: a["version"] for a in apps.installed()}
            shown = 0
            for r in store.catalog():
                if not r["ok"]:
                    out(f"! {r['name']}: {r['error']}")
                    continue
                for s in r["skipped"]:
                    out(f"! {r['name']}: skipped {s}")
                for e in r["apps"]:
                    if text and text not in (e["id"] + " " + e["name"] + " " + e["description"]).lower():
                        continue
                    state = ("installed" if e["id"] in have and not manifest.newer(e["version"], have[e["id"]]) else f"update from {have[e['id']]}" if e["id"] in have else
                             "built-in" if e["id"] in reserved else "")
                    out(f"{e['id']:<16} {e['version']:<8} {r['name']:<12} {e['name']:<14} {state}")
                    shown += 1
            if not shown and not repos():
                out("No repositories yet: add one with  apps repo add NAME ADDRESS")
            return 0
        if cmd == "install":
            return _install(pos, flags, reserved, store, yes, out, input_fn, interactive, repos)
        if cmd == "update":
            return _update(pos, reserved, store, yes, out, input_fn, interactive)
        if cmd == "remove":
            if len(pos) != 1:
                out("usage: apps remove ID [--purge] [--yes]")
                return 2
            if not _confirm(f"Remove {pos[0]}?" + (" Its data is deleted too." if "--purge" in flags else " Its data is kept."), yes, input_fn, interactive):
                return 1
            apps.remove(pos[0], purge="--purge" in flags)
            out(f"Removed {pos[0]} (undo with: apps rollback {pos[0]})")
            return 0
        if cmd == "rollback" and len(pos) == 1:
            r = apps.rollback(pos[0])
            out(f"{r['id']} is back at version {r['version']}")
            return 0
    except apps.AppsError as ex:
        out(f"Error: {ex}")
        return 1
    out(f"Unknown command: {cmd}\n\n" + __doc__.strip())
    return 2


def _repo(pos, repos, out):
    current = list(repos())
    if not pos or pos[0] == "list":
        for r in current:
            out(f"{r['name']:<20} {r['url']}")
        if not current:
            out("No repositories.")
        return 0
    try:
        if pos[0] == "add" and len(pos) == 3:
            settings.set_many({"repositories": [r for r in current if r["name"] != pos[1]] + [{"name": pos[1], "url": pos[2]}]})
            out(f"Added {pos[1]}")
            return 0
        if pos[0] == "remove" and len(pos) == 2:
            if not any(r["name"] == pos[1] for r in current):
                out(f"No repository called {pos[1]}")
                return 1
            settings.set_many({"repositories": [r for r in current if r["name"] != pos[1]]})
            out(f"Removed {pos[1]}")
            return 0
    except settings.SettingsError as ex:
        out(f"Error: {ex}")
        return 1
    out("usage: apps repo list | add NAME ADDRESS | remove NAME")
    return 2


def _install(pos, flags, reserved, store, yes, out, input_fn, interactive, repos):
    if len(pos) != 1:
        out("usage: apps install ID_OR_FILE.zip [--repo NAME] [--yes]")
        return 2
    target = pos[0]
    if target.endswith(".zip") or os.path.isfile(os.path.expanduser(target)):
        data = apps.read_source(target, apps.MAX_PACKAGE)
        plan = apps.check_install(data, reserved)
        _show_plan(plan, f"file, sha256 {hashlib.sha256(data).hexdigest()[:16]}... (no repository vouches for it)", out)
        if not _confirm("Install this?", yes, input_fn, interactive):
            return 1
        r = apps.install_package(data, reserved)
    else:
        want_repo = next((a.split("=", 1)[1] for a in flags if a.startswith("--repo=")), None)
        found = [(r["name"], e) for r in store.catalog() if r["ok"] and want_repo in (None, r["name"]) for e in r["apps"] if e["id"] == target]
        if not found:
            out(f"No app called {target} in the repositories (try: apps search)")
            return 1
        if len(found) > 1:
            out(f"{target} is offered by more than one repository ({', '.join(n for n, _ in found)}): choose with --repo=NAME")
            return 1
        name, entry = found[0]
        data, plan = apps.download_and_check(entry, reserved)
        _show_plan(plan, name + ("" if entry["package"].startswith("https://") or not entry["package"].startswith("http") else " (plain http: not encrypted)"), out)
        if not _confirm("Install this?", yes, input_fn, interactive):
            return 1
        r = apps.install_package(data, reserved, expect=entry)
    out(f"{'Updated' if r['action'] == 'update' else 'Installed'} {r['name']} {r['version']}. It appears in the menu the next time the launcher starts (or when its list reloads).")
    return 0


def _update(pos, reserved, store, yes, out, input_fn, interactive):
    wanted = []
    for r in store.catalog():
        if r["ok"]:
            wanted += [(r["name"], e, cur) for e, cur in apps.updates(r["apps"]) if not pos or e["id"] == pos[0]]
    if not wanted:
        out("Everything is up to date.")
        return 0
    code = 0
    for name, entry, cur in wanted:
        data, plan = apps.download_and_check(entry, reserved)
        _show_plan(plan, name, out)
        if _confirm("Update?", yes, input_fn, interactive):
            r = apps.install_package(data, reserved, expect=entry)
            out(f"Updated {r['name']} to {r['version']}")
        else:
            code = 1
    return code
