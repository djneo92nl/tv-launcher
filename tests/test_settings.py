import json
import os
import tempfile
import unittest

import support  # noqa: F401
from support import reset_settings
import launcher
from tvkit import config, manifest, settings, ui


def tearDownModule():
    reset_settings()


def sandbox():
    d = tempfile.mkdtemp()
    os.environ["TVL_SETTINGS"] = os.path.join(d, "s.json")
    return d


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.d = sandbox()

    def test_valid_values_are_cleaned_and_saved_privately_as_a_nested_overlay(self):
        out = settings.set_many({"clock.h24": False, "remoment.host": " http://nas:8080/ ", "launcher.autostart": None,
                                 "feeds": [{"name": " BBC ", "url": "https://x.org/rss"}], "photos.dirs": ["~/Pictures", "/media"]})
        self.assertEqual(out["remoment.host"], "http://nas:8080")
        self.assertEqual(out["feeds"], [{"name": "BBC", "url": "https://x.org/rss"}])
        raw = json.load(open(settings.path()))
        self.assertEqual(raw["clock"], {"h24": False})
        self.assertEqual(raw["launcher"], {"autostart": None})
        self.assertEqual(os.stat(settings.path()).st_mode & 0o777, 0o600)

    def test_every_kind_of_bad_value_is_refused_and_nothing_is_written(self):
        bad = [{"clock.h24": 1}, {"clock.h24": "yes"}, {"photos.seconds": 1}, {"photos.seconds": 301}, {"photos.seconds": True}, {"photos.seconds": "8"},
               {"launcher.title": ""}, {"launcher.title": "x" * 30}, {"launcher.title": "a\nb"}, {"remoment.host": "ftp://x"}, {"remoment.host": "a b"},
               {"remoment.host": 5}, {"feeds": "x"}, {"feeds": [{"name": "x", "url": "javascript:alert(1)"}]}, {"feeds": [{"name": "", "url": "http://a.b"}]},
               {"feeds": [{"name": "n", "url": "http://a.b"}] * 10}, {"photos.dirs": ["relative"]}, {"photos.dirs": "/media"}, {"photos.dirs": ["/a\0b"]},
               {"web.port": 80}, {"web.port": 70000}, {"launcher.autostart": "Not An Id"}, {"launcher.hidden": ["../x"]}, {"nope.key": 1}]
        for change in bad:
            with self.assertRaises(settings.SettingsError, msg=str(change)):
                settings.set_many(change)
        self.assertFalse(os.path.exists(settings.path()))

    def test_one_bad_value_in_a_batch_writes_none_of_them(self):
        settings.set_many({"clock.h24": False})
        with self.assertRaises(settings.SettingsError):
            settings.set_many({"clock.seconds": True, "photos.seconds": 0})
        self.assertEqual(settings.load_flat(), {"clock.h24": False})

    def test_apps_must_be_known_when_a_list_is_given(self):
        settings.set_many({"launcher.autostart": "clock"}, known_apps={"clock", "news"})
        with self.assertRaises(settings.SettingsError):
            settings.set_many({"launcher.autostart": "ghost"}, known_apps={"clock"})
        with self.assertRaises(settings.SettingsError):
            settings.set_many({"launcher.hidden": ["clock", "ghost"]}, known_apps={"clock"})
        self.assertEqual(settings.set_many({"launcher.hidden": ["clock", "clock", "news"]}, known_apps={"clock", "news"})["launcher.hidden"], ["clock", "news"])

    def test_overlay_wins_over_config_and_launcher_keys_stay_apart(self):
        settings.set_many({"clock.h24": False, "launcher.title": "MY TV"})
        cfg = settings.apply_overlay({"clock": {"h24": True, "seconds": True}, "other": 1})
        self.assertEqual(cfg, {"clock": {"h24": False, "seconds": True}, "other": 1})
        self.assertEqual(settings.launcher_overlay(), {"title": "MY TV"})

    def test_a_hand_edited_or_damaged_file_cannot_break_start_up(self):
        for text in ("{nope", "[]", json.dumps({"clock": {"h24": "maybe"}, "unknown": 1, "photos": {"seconds": 5}})):
            with open(settings.path(), "w") as f:
                f.write(text)
            flat = settings.load_flat()
            self.assertTrue(set(flat) <= set(settings.BY_KEY))
        self.assertEqual(settings.load_flat(), {"photos.seconds": 5})
        self.assertIn("clock", config.load(profile="pi"))

    def test_config_load_applies_the_overlay(self):
        settings.set_many({"remoment.host": "http://box"})
        self.assertEqual(config.load(profile="pi")["remoment"]["host"], "http://box")

    def test_launcher_config_applies_the_overlay(self):
        settings.set_many({"launcher.autostart_seconds": 12, "launcher.hidden": ["clock"]})
        cfg = launcher.load_launcher_config("/nonexistent")
        self.assertEqual((cfg["autostart_seconds"], cfg["hidden"]), (12, ["clock"]))

    def test_effective_value_and_reset(self):
        cfg = {"clock": {"h24": False}}
        self.assertFalse(settings.effective(cfg, {}, "clock.h24"))
        self.assertEqual(settings.effective(cfg, {}, "photos.seconds"), 8)
        self.assertEqual(settings.effective({}, {"title": "T"}, "launcher.title"), "T")
        settings.set_many({"clock.h24": False, "clock.seconds": True})
        settings.reset(["clock.h24"])
        self.assertEqual(settings.load_flat(), {"clock.seconds": True})

    def test_schema_describes_every_field_for_the_web_form(self):
        for f in settings.schema():
            self.assertTrue({"key", "label", "type", "default", "group"} <= set(f), f)
            if f["default"] is not None:
                settings.clean(f["key"], f["default"])                       # a default is itself a valid value


class ManifestTests(unittest.TestCase):
    def good(self, **kw):
        return {"id": "my-app", "name": "MY APP", "version": "1.2.3", "description": "d", "icon": "tv", **kw}

    def test_a_good_manifest_passes_with_defaults(self):
        m = manifest.validate(self.good())
        self.assertEqual((m["order"], m["tvkit"], m["permissions"]), (100, 1, []))

    def test_bad_manifests(self):
        rows = [".w" * 8] * 16
        bad = [None, [], self.good(id="Bad"), self.good(id="a"), self.good(id="1abc"), self.good(id="../x"), self.good(name=""), self.good(name="x" * 30),
               self.good(version="1.x"), self.good(version=1), self.good(icon="nope"), self.good(icon=5), self.good(icon={"colour": "a", "rows": ["."] * 15}),
               self.good(icon={"colour": "z", "rows": rows}), self.good(icon={"colour": "a", "rows": ["x" * 16] * 16}), self.good(tvkit=99), self.good(tvkit="1"),
               self.good(order=-1), self.good(order=5000), self.good(permissions=[{"kind": "root"}]), self.good(permissions="all"), self.good(description="x" * 200)]
        for m in bad:
            with self.assertRaises(manifest.ManifestError, msg=str(m)):
                manifest.validate(m)

    def test_custom_icon_is_registered_under_the_app_name(self):
        m = manifest.validate(self.good(icon={"colour": "a", "rows": ["." * 16] * 16}))
        self.assertEqual(manifest.icon_name(m), "app:my-app")
        self.assertIn("app:my-app", ui.ICONS)
        self.assertTrue(ui.icon(0, 0, "app:my-app") == [])                      # an empty picture draws nothing, but it is a valid one

    def test_versions_compare_numerically(self):
        self.assertTrue(manifest.newer("1.10", "1.9"))
        self.assertTrue(manifest.newer("2", "1.99.99"))
        self.assertFalse(manifest.newer("1.0.0", "1"))
        self.assertFalse(manifest.newer("1.2", "1.2.1"))

    def test_read_needs_app_json_and_app_py(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(manifest.ManifestError):
                manifest.read(d)
            with open(os.path.join(d, "app.json"), "w") as f:
                json.dump(self.good(), f)
            with self.assertRaises(manifest.ManifestError):
                manifest.read(d)                                                 # no app.py yet
            open(os.path.join(d, "app.py"), "w").close()
            self.assertEqual(manifest.read(d)["id"], "my-app")
            with open(os.path.join(d, "app.json"), "w") as f:
                f.write("{oops")
            with self.assertRaises(manifest.ManifestError):
                manifest.read(d)

    def test_every_builtin_app_has_a_valid_manifest(self):
        base = os.path.join(support.ROOT, "apps")
        for name in os.listdir(base):
            if not name.startswith("_") and os.path.isdir(os.path.join(base, name)):
                self.assertEqual(manifest.read(os.path.join(base, name))["id"], name)


class DiscoveryTests(unittest.TestCase):
    def make_app(self, base, folder, **kw):
        d = os.path.join(base, folder)
        os.makedirs(d)
        open(os.path.join(d, "app.py"), "w").close()
        with open(os.path.join(d, "app.json"), "w") as f:
            json.dump({"id": folder, "name": folder.upper(), "version": "1.0.0", **kw}, f)

    def test_finds_valid_apps_skips_the_rest_and_builtin_ids_win(self):
        with tempfile.TemporaryDirectory() as b, tempfile.TemporaryDirectory() as u:
            self.make_app(b, "clock", order=10)
            self.make_app(u, "clock", order=1)                                   # an installed app cannot take a built-in id
            self.make_app(u, "extra", order=300)
            os.makedirs(os.path.join(u, "broken")); open(os.path.join(u, "broken", "app.json"), "w").write("{")
            os.makedirs(os.path.join(u, "_template")); os.makedirs(os.path.join(u, ".hidden"))
            got = launcher.discover_apps([("builtin", b), ("user", u)])
            self.assertEqual([(e["id"], e["source"]) for e in got], [("clock", "builtin"), ("extra", "user")])
            self.assertEqual(got[1]["cmd"], ["python3", "app.py", "--profile", "{profile}"])
            self.assertTrue(launcher.app_available(got[1]))

    def test_a_missing_apps_folder_is_fine(self):
        self.assertEqual(launcher.discover_apps([("user", "/definitely/not/here")]), [])

    def test_hidden_and_order_settings(self):
        apps = [{"id": i, "name": i.upper(), "order": o} for i, o in (("a", 30), ("b", 10), ("c", 20), ("d", 40))]
        self.assertEqual([a["id"] for a in launcher.order_apps(apps, {})], ["b", "c", "a", "d"])
        self.assertEqual([a["id"] for a in launcher.order_apps(apps, {"hidden": ["c"]})], ["b", "a", "d"])
        self.assertEqual([a["id"] for a in launcher.order_apps(apps, {"order": ["d", "a", "ghost"]})], ["d", "a", "b", "c"])
        self.assertEqual([a["id"] for a in launcher.order_apps(apps, {"order": ["c"], "hidden": ["c"]})], ["b", "a", "d"])

    def test_the_default_menu_has_the_builtin_apps_in_manifest_order(self):
        reset_settings()
        ids = [e["id"] for e in launcher.build_entries(launcher.load_launcher_config("/nonexistent"), discover=True) if e["kind"] == "app"]
        self.assertEqual(ids[:7], ["clock", "media", "jellyfin", "remoment", "photos", "news", "info"])


if __name__ == "__main__":
    unittest.main()
