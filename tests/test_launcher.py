import json
import os
import sys
import tempfile
import unittest

import support  # noqa: F401
import launcher
from support import fake_shell, press
from tvkit import ui


def entries(*ids, confirm=()):
    return [{"id": i, "name": i.upper(), "desc": "d", "kind": "app", "cmd": ["x"], "confirm": i in confirm} for i in ids]


class MenuTests(unittest.TestCase):
    def test_up_down_wrap(self):
        m = launcher.Menu(entries("a", "b", "c"))
        m.handle("down"); m.handle("down"); m.handle("down")
        self.assertEqual(m.current()["id"], "a")
        m.handle("up")
        self.assertEqual(m.current()["id"], "c")

    def test_grid_moves(self):
        m = launcher.Menu(entries("a", "b", "c", "d", "e"))
        m.cols = 2                                         # a b / c d / e
        m.handle("right"); self.assertEqual(m.current()["id"], "b")
        m.handle("down"); self.assertEqual(m.current()["id"], "d")
        m.handle("down"); self.assertEqual(m.current()["id"], "b")      # no tile below d: top of the column
        m.handle("up"); self.assertEqual(m.current()["id"], "d")        # above the first row: bottom of the column
        m.handle("left"); self.assertEqual(m.current()["id"], "c")
        m.handle("down"); self.assertEqual(m.current()["id"], "e")

    def test_enter_returns_entry_and_remembered_selection(self):
        self.assertEqual(launcher.Menu(entries("a", "b"), selected="b").handle("enter")["id"], "b")

    def test_digit_starts_that_entry(self):
        m = launcher.Menu(entries("a", "b", "c"))
        self.assertEqual(m.handle("digit:2")["id"], "b")
        self.assertIsNone(m.handle("digit:9"))

    def test_system_action_needs_second_enter(self):
        m = launcher.Menu(entries("a", "off", confirm=("off",)), selected="off")
        self.assertIsNone(m.handle("enter", now=100))
        self.assertEqual(m.handle("enter", now=101)["id"], "off")

    def test_confirmation_expires_and_is_cancelled_by_moving(self):
        m = launcher.Menu(entries("a", "off", confirm=("off",)), selected="off")
        m.handle("enter", now=100)
        self.assertIsNone(m.handle("enter", now=100 + launcher.Menu.CONFIRM_S + 1))
        m.handle("up")
        self.assertIsNone(m.confirm)

    def test_empty_menu_is_safe(self):
        self.assertIsNone(launcher.Menu([]).handle("enter"))


class EntryTests(unittest.TestCase):
    def test_missing_app_folder_is_left_out(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "tv.py"), "w").close()
            cfg = {"apps": [{"id": "here", "name": "HERE", "dir": d, "cmd": ["python3", "tv.py"]},
                            {"id": "gone", "name": "GONE", "dir": d + "/nope", "cmd": ["python3", "tv.py"]},
                            {"id": "noscript", "name": "NOSCRIPT", "dir": d, "cmd": ["python3", "other.py"]}], "system": []}
            self.assertEqual([e["id"] for e in launcher.build_entries(cfg)], ["here"])

    def test_dir_can_list_places_and_the_first_that_exists_wins(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "second"))
            open(os.path.join(d, "second", "tv.py"), "w").close()
            app = {"id": "x", "name": "X", "cmd": ["python3", "tv.py"], "dir": [os.path.join(d, "first"), os.path.join(d, "second")]}
            self.assertEqual(launcher.app_dir(app), os.path.join(d, "second"))
            self.assertTrue(launcher.app_available(app))
            app["dir"] = [os.path.join(d, "nowhere")]
            self.assertFalse(launcher.app_available(app))

    def test_relative_dirs_are_also_looked_up_in_search_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Sibling Tv App"))
            open(os.path.join(d, "Sibling Tv App", "tv.py"), "w").close()
            app = {"id": "x", "name": "X", "cmd": ["python3", "tv.py"], "dir": ["../Sibling Tv App"]}
            old = list(launcher.SEARCH_DIRS)
            try:
                launcher.SEARCH_DIRS[:] = [d]
                self.assertEqual(launcher.app_dir(app), os.path.join(d, "Sibling Tv App"))
                self.assertTrue(launcher.app_available(app))
            finally:
                launcher.SEARCH_DIRS[:] = old

    def test_relative_dirs_are_inside_the_repo(self):
        e = launcher.build_entries({"apps": [{"id": "c", "name": "C", "dir": "apps/clock", "cmd": ["python3", "app.py"]}], "system": []})
        self.assertEqual([x["id"] for x in e], ["c"])
        self.assertTrue(launcher.app_dir(e[0]).startswith(launcher.ROOT))

    def test_system_actions_only_on_linux(self):
        got = launcher.build_entries({"apps": [], "system": launcher.DEFAULT_CONFIG["system"]})
        self.assertEqual(bool(got), sys.platform.startswith("linux"))

    def test_config_file_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "launcher.json")
            with open(p, "w") as f:
                json.dump({"autostart": "clock", "apps": []}, f)
            cfg = launcher.load_launcher_config(p)
            self.assertEqual((cfg["autostart"], cfg["apps"], cfg["crash_seconds"]), ("clock", [], 10))
            self.assertIsNone(launcher.load_launcher_config(d + "/missing.json")["autostart"])

    def test_every_default_app_has_an_icon_that_exists(self):
        for e in launcher.all_apps(launcher.DEFAULT_CONFIG) + launcher.DEFAULT_CONFIG["system"]:
            self.assertIn(e["icon"], ui.ICONS, e["id"])

    def test_profile_placeholder(self):
        self.assertEqual(launcher.expand(["python3", "tv.py", "--profile", "{profile}"], "hdmi")[-1], "hdmi")


class DrawTests(unittest.TestCase):
    def test_screen_draws_in_both_shapes(self):
        m = launcher.Menu(entries("clock", "iptv", "off", confirm=("off",)))
        m.title_sub, m.note = "TV LAUNCHER", "IPTV stopped at once (code 1)"
        old = ui.W
        try:
            for width in (768, 1024):
                ui.set_canvas(width)
                self.assertIn("IPTV", launcher.home_screen(m, 1_800_000_000_000, countdown="STARTING IN 3"))
            m.handle("right"); m.handle("right"); m.handle("enter")
            self.assertIn("SURE?", launcher.home_screen(m, 1_800_000_000_000))
        finally:
            ui.set_canvas(old)


class CloseTests(unittest.TestCase):
    def test_closing_the_menu_window_ends_the_launcher(self):
        e = {"id": "a", "name": "A", "kind": "app", "cmd": ["x"]}
        L = launcher.Launcher({"prof": {}, "cache_dir": "/tmp", "profile": "mac"}, json.loads(json.dumps(launcher.DEFAULT_CONFIG)), "/tmp/ls.json", [e])
        L.open_screen = lambda: None
        L.close_screen = lambda: None
        L.mpv = support.FakeMpv()
        L.q.put(("mpv", (L.mpv, {"event": "mpv-exited", "code": 0})))
        self.assertIsNone(L.menu_loop())
        self.assertTrue(L.stopping)                                  # run() then stops instead of drawing the menu again


class RunTests(unittest.TestCase):
    def make(self, tmp, cmd):
        e = {"id": "t", "name": "T", "kind": "app", "cmd": cmd, "dir": tmp}
        cfg = {"prof": {}, "cache_dir": tmp, "profile": "pi"}
        return launcher.Launcher(cfg, json.loads(json.dumps(launcher.DEFAULT_CONFIG)), os.path.join(tmp, "l.json"), [e]), e

    def test_clean_exit_gives_no_note_and_crash_does(self):
        with tempfile.TemporaryDirectory() as d:
            L, e = self.make(d, ["python3", "-c", "pass"])
            self.assertEqual(L.run_app(e), "")
            L, e = self.make(d, ["python3", "-c", "import sys; sys.exit(3)"])
            self.assertIn("stopped at once (code 3)", L.run_app(e))

    def test_missing_program_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            L, e = self.make(d, ["definitely-not-a-program"])
            self.assertIn("could not start", L.run_app(e))

    def test_remembers_last_app(self):
        with tempfile.TemporaryDirectory() as d:
            L, _ = self.make(d, ["x"])
            self.assertIsNone(L.last_app())
            L.remember("clock")
            self.assertEqual(L.last_app(), "clock")


if __name__ == "__main__":
    unittest.main()
