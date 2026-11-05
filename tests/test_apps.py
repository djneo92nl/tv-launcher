import hashlib
import http.server
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
import zipfile

import support  # noqa: F401
import launcher
from tvkit import apps, manifest, settings

sys.path.insert(0, os.path.join(support.ROOT, "tools"))
import make_repo  # noqa: E402

RESERVED = {"clock", "media", "news"}


def manifest_json(**kw):
    return json.dumps({"id": "demo", "name": "DEMO", "version": "1.0.0", "description": "A demo app", "icon": "tv", **kw})


def pkg(files=None, m=None, extra=None):
    """A zip as bytes. `files` replaces the default content; `extra` is a list of (ZipInfo-or-name, data) written as given (for hostile entries)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        content = files if files is not None else {"app.json": m or manifest_json(), "app.py": "print('hi')\n"}
        for name, data in content.items():
            z.writestr(name, data)
        for item, data in (extra or []):
            z.writestr(item, data)
    return buf.getvalue()


def entry_for(data, **kw):
    return {"id": "demo", "version": "1.0.0", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), **kw}


class AppsCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.saved = {k: os.environ.get(k) for k in ("TVL_USER_APPS", "TVL_DATA_DIR")}
        os.environ["TVL_USER_APPS"] = os.path.join(self.d, "apps")
        os.environ["TVL_DATA_DIR"] = os.path.join(self.d, "data")
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.d, ignore_errors=True)

    def install(self, data, **kw):
        return apps.install_package(data, kw.pop("reserved", RESERVED), **kw)

    def leftovers(self):
        base = apps.user_apps_dir()
        return sorted(n for n in os.listdir(base)) if os.path.isdir(base) else []

    def refused(self, data, text=None, **kw):
        with self.assertRaises(apps.AppsError) as cm:
            self.install(data, **kw)
        if text:
            self.assertIn(text, str(cm.exception))
        self.assertEqual([n for n in self.leftovers() if n != "demo"], [], "a temporary folder was left behind")
        return cm.exception


class InstallTests(AppsCase):
    def test_a_good_package_installs_privately_and_the_launcher_discovers_it(self):
        r = self.install(pkg())
        self.assertEqual((r["id"], r["version"], r["action"], r["from"]), ("demo", "1.0.0", "install", None))
        folder = os.path.join(apps.user_apps_dir(), "demo")
        self.assertEqual(sorted(os.listdir(folder)), ["app.json", "app.py"])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(folder, "app.py")).st_mode), 0o644)
        found = launcher.discover_apps([("user", apps.user_apps_dir())])
        self.assertEqual([(e["id"], e["source"], e["version"]) for e in found], [("demo", "user", "1.0.0")])
        self.assertEqual([a["id"] for a in apps.installed()], ["demo"])
        self.assertEqual(self.leftovers(), ["demo"])

    def test_subfolders_and_data_files_are_allowed(self):
        self.install(pkg({"app.json": manifest_json(), "app.py": "x = 1\n", "lib/helper.py": "y = 2\n", "data/names.txt": "a\n"}))
        self.assertTrue(os.path.exists(os.path.join(apps.user_apps_dir(), "demo", "lib", "helper.py")))

    def test_hostile_names_are_refused_before_anything_is_written(self):
        for bad in ("../evil.py", "/etc/evil.py", "a/../../evil.py", "..\\evil.py", "C:/evil.py", "a//b.py", "./x.py", ".hidden", "sub/.secret", "a/./b.py"):
            data = pkg(extra=[(bad, "boom")])
            self.refused(data, "unsafe file name" if "hidden" not in bad and "secret" not in bad else "hidden files")
        self.assertFalse(os.path.exists(os.path.join(self.d, "evil.py")))

    def test_symlinks_and_special_files_are_refused(self):
        for mode in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO):
            info = zipfile.ZipInfo("link.py")
            info.external_attr = (mode | 0o777) << 16
            self.refused(pkg(extra=[(info, "/etc/passwd")]), "symbolic link or special file")

    def test_zip_tool_style_permissions_are_fine(self):
        info = zipfile.ZipInfo("extra.txt")
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        info2 = zipfile.ZipInfo("noattr.txt")
        self.install(pkg(extra=[(info, "x"), (info2, "y")]))

    def test_encrypted_duplicate_too_many_big_and_bomb_packages(self):
        enc = bytearray(pkg())
        # set the "encrypted" bit in the central directory flags of the first entry
        i = enc.rfind(b"PK\x01\x02")
        enc[i + 8] |= 1
        self.refused(bytes(enc), "encrypted")
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dup = pkg(extra=[("app.py", "second")])
        self.refused(dup, "twice")
        many = pkg({**{"app.json": manifest_json(), "app.py": "x=1\n"}, **{f"f{i}.txt": "x" for i in range(apps.MAX_FILES)}})
        self.refused(many, "files")
        big = pkg({"app.json": manifest_json(), "app.py": "x=1\n", "blob.bin": b"A" * (apps.MAX_FILE + 10)})          # compresses well, so the zip itself is small
        self.refused(big, "too large")
        bomb = pkg({"app.json": manifest_json(), "app.py": "x=1\n", "zeros.bin": b"\0" * (4 * 1024 * 1024)})
        self.refused(bomb, "zip bomb")
        table = bytes(97 + (b & 1) for b in range(256))                                # two symbols, random order: compresses about 8 to 1, far from a "bomb"
        total = pkg({"app.json": manifest_json(), "app.py": "x=1\n", **{f"r{i}.bin": os.urandom(apps.MAX_FILE - 10).translate(table) for i in range(5)}})
        self.refused(total, "more than allowed")

    def test_not_a_zip_and_missing_or_bad_manifest_and_missing_app_py(self):
        self.refused(b"not a zip at all", "not a zip")
        self.refused(b"", "not a zip")
        self.refused(pkg({"app.py": "x=1\n"}), "app.json and app.py")
        self.refused(pkg({"app.json": manifest_json()}), "app.json and app.py")
        self.refused(pkg({"app.json": "{oops", "app.py": "x=1\n"}), "not valid JSON")
        self.refused(pkg({"app.json": manifest_json(id="Bad Id"), "app.py": "x=1\n"}), "app.json")
        self.refused(pkg({"app.json": manifest_json(tvkit=99), "app.py": "x=1\n"}), "newer TV Launcher")
        self.refused(pkg({"app.json": "x" * (manifest.MAX_MANIFEST_BYTES + 1), "app.py": "x=1\n"}), "too large")
        self.refused(pkg({"sub/app.json": manifest_json(), "sub/app.py": "x=1\n"}), "top level")

    def test_a_syntax_error_is_refused_and_nothing_is_installed(self):
        self.refused(pkg({"app.json": manifest_json(), "app.py": "def broken(:\n"}), "syntax error")
        self.refused(pkg({"app.json": manifest_json(), "app.py": "x = 1\n", "lib/bad.py": "class\n"}), "lib/bad.py")
        self.assertEqual(self.leftovers(), [])

    def test_built_in_and_reserved_ids_cannot_be_taken(self):
        self.refused(pkg({"app.json": manifest_json(id="clock"), "app.py": "x=1\n"}), "built-in app")
        self.refused(pkg({"app.json": manifest_json(id="launcher"), "app.py": "x=1\n"}), "built-in app")
        self.refused(pkg({"app.json": manifest_json(id="remote"), "app.py": "x=1\n"}), "built-in app")

    def test_the_package_must_match_what_the_index_promised(self):
        data = pkg()
        self.refused(data, "not the app the repository promised", expect=entry_for(data, id="other"))
        self.refused(data, "not the app the repository promised", expect=entry_for(data, version="2.0.0"))
        self.install(data, expect=entry_for(data))


class UpdateTests(AppsCase):
    def v(self, version, py="x = 1\n"):
        return pkg({"app.json": manifest_json(version=version), "app.py": py})

    def test_update_needs_a_newer_version_and_keeps_the_old_one_for_rollback(self):
        self.install(self.v("1.0.0", "print('one')\n"))
        self.refused(self.v("1.0.0"), "already installed")
        self.refused(self.v("0.9.0"), "already installed")
        r = self.install(self.v("1.1.0", "print('two')\n"))
        self.assertEqual((r["action"], r["from"], r["version"]), ("update", "1.0.0", "1.1.0"))
        self.assertIn("two", open(os.path.join(apps.user_apps_dir(), "demo", "app.py")).read())
        self.assertEqual(manifest.read(os.path.join(apps.backup_dir(), "demo"))["version"], "1.0.0")
        back = apps.rollback("demo")
        self.assertEqual(back["version"], "1.0.0")
        self.assertIn("one", open(os.path.join(apps.user_apps_dir(), "demo", "app.py")).read())
        self.assertEqual(apps.rollback("demo")["version"], "1.1.0")                 # and forward again

    def test_a_broken_update_leaves_the_working_version_alone(self):
        self.install(self.v("1.0.0", "print('works')\n"))
        self.refused(self.v("1.1.0", "def broken(:\n"), "syntax error")
        self.assertIn("works", open(os.path.join(apps.user_apps_dir(), "demo", "app.py")).read())
        self.assertEqual(apps.installed()[0]["version"], "1.0.0")

    def test_versions_compare_as_numbers(self):
        self.install(self.v("1.9.0"))
        self.install(self.v("1.10.0"))
        self.assertEqual(apps.installed()[0]["version"], "1.10.0")

    def test_remove_keeps_a_backup_and_data_unless_purged(self):
        self.install(self.v("1.0.0"))
        data = os.path.join(apps.data_dir(), "demo")
        os.makedirs(data)
        open(os.path.join(data, "state.json"), "w").close()
        self.assertEqual(apps.remove("demo")["id"], "demo")
        self.assertEqual(apps.installed(), [])
        self.assertTrue(os.path.exists(os.path.join(data, "state.json")))
        self.assertEqual(apps.rollback("demo")["version"], "1.0.0")                 # removal can be undone
        apps.remove("demo", purge=True)
        self.assertFalse(os.path.exists(data))

    def test_remove_and_rollback_refuse_bad_ids_and_unknown_apps(self):
        for bad in ("", "../x", "Clock", None, "a/b"):
            with self.assertRaises(apps.AppsError):
                apps.remove(bad)
            with self.assertRaises(apps.AppsError):
                apps.rollback(bad)
        with self.assertRaises(apps.AppsError) as cm:
            apps.remove("clock")
        self.assertIn("built-in", str(cm.exception))
        with self.assertRaises(apps.AppsError):
            apps.rollback("never-installed")

    def test_updates_lists_only_newer_versions_of_installed_apps(self):
        self.install(self.v("1.0.0"))
        entries = [{"id": "demo", "version": "1.2.0"}, {"id": "demo2", "version": "5.0.0"}]
        self.assertEqual([(e["version"], cur) for e, cur in apps.updates(entries)], [("1.2.0", "1.0.0")])
        self.assertEqual(apps.updates([{"id": "demo", "version": "1.0.0"}]), [])


class IndexTests(AppsCase):
    def idx(self, **kw):
        sha = "a" * 64
        e = {"id": "weather", "name": "WEATHER", "version": "1.0.0", "description": "d", "icon": "info", "tvkit": 1, "url": "weather.zip", "sha256": sha, "size": 100}
        e.update(kw)
        return {"name": "Test repo", "apps": [e]}

    def test_relative_and_absolute_urls_resolve(self):
        i = apps.parse_index(self.idx(), "https://example.org/apps/index.json", "Repo")
        self.assertEqual(i["apps"][0]["package"], "https://example.org/apps/weather.zip")
        i = apps.parse_index(self.idx(url="https://cdn.example.org/w.zip"), "https://example.org/apps/index.json")
        self.assertEqual(i["apps"][0]["package"], "https://cdn.example.org/w.zip")
        i = apps.parse_index(self.idx(), "/srv/repo/index.json")
        self.assertEqual(i["apps"][0]["package"], "/srv/repo/weather.zip")

    def test_bad_entries_are_skipped_with_a_reason_and_good_ones_stay(self):
        good = self.idx()["apps"][0]
        bad = [self.idx(sha256="xyz")["apps"][0], self.idx(sha256="A" * 64)["apps"][0], self.idx(size=0)["apps"][0], self.idx(size=apps.MAX_PACKAGE + 1)["apps"][0],
               self.idx(size=True)["apps"][0], self.idx(id="Bad Id")["apps"][0], self.idx(tvkit=99)["apps"][0], self.idx(url="../up.zip")["apps"][0],
               self.idx(url="file:///etc/passwd")["apps"][0], self.idx(url="javascript:alert(1)")["apps"][0], self.idx(url=None)["apps"][0], "not an object"]
        i = apps.parse_index({"name": "x", "apps": [good] + bad}, "/srv/repo/index.json")
        self.assertEqual([a["id"] for a in i["apps"]], ["weather"])
        self.assertEqual(len(i["skipped"]), len(bad))
        i = apps.parse_index({"name": "x", "apps": [self.idx(url="//evil.example/x.zip")["apps"][0]]}, "https://example.org/index.json")
        self.assertEqual(i["apps"], [])                                          # a scheme-relative address is not http(s)

    def test_not_a_repository(self):
        for bad in (None, [], {"apps": "x"}, {"nothing": 1}):
            with self.assertRaises(apps.AppsError):
                apps.parse_index(bad, "/x/index.json")

    def test_index_locations(self):
        self.assertEqual(apps.index_location("https://h/repo"), "https://h/repo/index.json")
        self.assertEqual(apps.index_location("https://h/repo/"), "https://h/repo/index.json")
        self.assertEqual(apps.index_location("https://h/my.json"), "https://h/my.json")
        self.assertEqual(apps.index_location("file:///srv/r"), "/srv/r/index.json")


class RepositoryEndToEnd(AppsCase):
    def build(self, *folders, name="Local"):
        out = os.path.join(self.d, "repo")
        make_repo.main(["--name", name, "--out", out, *folders])
        return out

    def app_folder(self, id_="weather", version="1.0.0", py="x = 1\n"):
        f = os.path.join(self.d, "src", id_)
        os.makedirs(f, exist_ok=True)
        open(os.path.join(f, "app.json"), "w").write(manifest_json(id=id_, name=id_.upper(), version=version))
        open(os.path.join(f, "app.py"), "w").write(py)
        return f

    def test_make_repo_then_install_from_a_local_folder(self):
        out = self.build(self.app_folder())
        store = apps.Store(lambda: [{"name": "Local", "url": out}])
        cat = store.catalog()
        self.assertEqual((cat[0]["ok"], cat[0]["count"]), (True, 1))
        entry = store.find("Local", "weather")
        data, plan = apps.download_and_check(entry, RESERVED)
        self.assertEqual(plan["action"], "install")
        self.assertEqual(apps.install_package(data, RESERVED, expect=entry)["id"], "weather")

    def test_a_tampered_package_is_refused_by_the_checksum(self):
        out = self.build(self.app_folder())
        entry = apps.Store(lambda: [{"name": "Local", "url": out}]).find("Local", "weather")
        path = os.path.join(out, "weather-1.0.0.zip")
        original = open(path, "rb").read()
        evil = pkg({"app.json": manifest_json(id="weather", version="1.0.0"), "app.py": "import os; os.system('x')\n"})
        open(path, "wb").write(evil)
        with self.assertRaises(apps.AppsError) as cm:
            apps.download_and_check(entry, RESERVED)
        self.assertTrue("size" in str(cm.exception) or "checksum" in str(cm.exception))
        open(path, "wb").write(original[:-1] + bytes([original[-1] ^ 1]))               # same size, one bit different
        with self.assertRaises(apps.AppsError) as cm:
            apps.download_and_check(entry, RESERVED)
        self.assertIn("checksum", str(cm.exception))
        self.assertEqual(apps.installed(), [])

    def test_over_http_and_a_lying_server(self):
        out = self.build(self.app_folder())
        handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=out, **k)
        handler.log_message = lambda *a: None
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: Quiet(*a, directory=out, **k))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        store = apps.Store(lambda: [{"name": "Web", "url": url}])
        entry = store.find("Web", "weather")
        self.assertTrue(entry["package"].startswith(url))
        data, plan = apps.download_and_check(entry, RESERVED)
        self.assertEqual(apps.install_package(data, RESERVED, expect=entry)["version"], "1.0.0")
        lie = {**entry, "size": entry["size"] + 1}
        with self.assertRaises(apps.AppsError):
            apps.download_and_check(lie, RESERVED)

    def test_unreachable_and_broken_repositories_are_reported_not_raised(self):
        broken = os.path.join(self.d, "broken")
        os.makedirs(broken)
        open(os.path.join(broken, "index.json"), "w").write("{nope")
        store = apps.Store(lambda: [{"name": "Gone", "url": "/definitely/not/here"}, {"name": "Broken", "url": broken}, {"name": "Dead", "url": "http://127.0.0.1:1"}])
        cat = {r["name"]: r for r in store.catalog()}
        for name in ("Gone", "Broken", "Dead"):
            self.assertFalse(cat[name]["ok"], name)
            self.assertTrue(cat[name]["error"])
        self.assertNotIn("/definitely", cat["Gone"]["error"])                             # no paths in messages
        self.assertNotIn("127.0.0.1", cat["Dead"]["error"])

    def test_the_index_is_cached_and_refresh_forgets_it(self):
        out = self.build(self.app_folder())
        now = [0]
        store = apps.Store(lambda: [{"name": "Local", "url": out}], ttl=60, clock=lambda: now[0])
        self.assertEqual(store.catalog()[0]["count"], 1)
        self.build(self.app_folder(), self.app_folder("second"))
        self.assertEqual(store.catalog()[0]["count"], 1)                                   # still the cached answer
        now[0] = 61
        self.assertEqual(store.catalog()[0]["count"], 2)
        self.build(self.app_folder())
        store.refresh()
        self.assertEqual(store.catalog()[0]["count"], 1)

    def test_make_repo_leaves_out_hidden_files_caches_and_bad_apps_and_is_reproducible(self):
        f = self.app_folder()
        os.makedirs(os.path.join(f, "__pycache__"))
        open(os.path.join(f, "__pycache__", "x.pyc"), "w").close()
        open(os.path.join(f, ".secret"), "w").write("key")
        os.symlink("/etc/passwd", os.path.join(f, "link.txt"))
        bad = os.path.join(self.d, "src", "bad")
        os.makedirs(bad)
        open(os.path.join(bad, "app.json"), "w").write(manifest_json(id="bad"))
        open(os.path.join(bad, "app.py"), "w").write("def x(:\n")
        out = self.build(f, bad)
        names = zipfile.ZipFile(os.path.join(out, "weather-1.0.0.zip")).namelist()
        self.assertEqual(sorted(names), ["app.json", "app.py"])
        self.assertEqual([a["id"] for a in json.load(open(os.path.join(out, "index.json")))["apps"]], ["weather"])
        first = open(os.path.join(out, "weather-1.0.0.zip"), "rb").read()
        out2 = os.path.join(self.d, "repo2")
        make_repo.main(["--name", "Local", "--out", out2, f])
        self.assertEqual(first, open(os.path.join(out2, "weather-1.0.0.zip"), "rb").read())


class SettingsRepoTests(unittest.TestCase):
    def test_repositories_are_validated(self):
        ok = [{"name": "Official", "url": "https://apps.example.org/"}, {"name": "USB", "url": "/media/usb/apps"}, {"name": "Home dir", "url": "~/repo"}]
        self.assertEqual(settings.clean("repositories", ok), ok)
        for bad in ([{"name": "x", "url": "ftp://x"}], [{"name": "", "url": "https://x.y"}], [{"name": "a", "url": "https://x.y"}] * 2, [{"name": "n"}], "x",
                    [{"name": "n%", "url": "https://x.y"}], [{"name": "n", "url": "javascript:alert(1)"}], [{"name": f"n{i}", "url": "https://x.y"} for i in range(6)]):
            with self.assertRaises(settings.SettingsError, msg=str(bad)):
                settings.clean("repositories", bad)


if __name__ == "__main__":
    unittest.main()
