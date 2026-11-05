import json
import os
import tempfile
import threading
import time
import unittest

import support  # noqa: F401
import launcher
from support import FakeMpv, reset_settings
from tvkit import settings, web


def tearDownModule():
    reset_settings()


def make(web_on=True):
    d = tempfile.mkdtemp()
    os.environ["TVL_SETTINGS"] = os.path.join(d, "settings.json")
    cfg = {"prof": {}, "cache_dir": d, "profile": "pi", "web": {"enabled": web_on}}
    lcfg = launcher.load_launcher_config("/nonexistent")
    entries = launcher.build_entries(lcfg, discover=True, web=web_on)
    L = launcher.Launcher(cfg, lcfg, os.path.join(d, "state.json"), entries)
    L.config_path, L.launcher_path = os.path.join(support.ROOT, "config.json"), os.path.join(d, "launcher.json")
    L.catalog = launcher.all_apps(lcfg)
    L.open_screen = lambda: setattr(L, "mpv", FakeMpv())
    L.close_screen = lambda: None
    return L


def run_loop(L, **kw):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("chosen", L.menu_loop(**kw)), daemon=True)
    t.start()
    return t, out


def wait_for(cond, seconds=3):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def screen(L):
    return (L.mpv.overlays.get(1) or "") if L.mpv else ""


class TileTests(unittest.TestCase):
    def test_remote_tile_comes_before_the_system_actions_and_only_with_the_web_on(self):
        lcfg = launcher.load_launcher_config("/nonexistent")
        on = [e["id"] for e in launcher.build_entries(lcfg, discover=True, web=True)]
        off = [e["id"] for e in launcher.build_entries(lcfg, discover=True, web=False)]
        self.assertIn("remote", on)
        self.assertNotIn("remote", off)
        self.assertEqual(next(e for e in launcher.build_entries(lcfg, discover=True, web=True) if e["id"] == "remote")["kind"], "internal")
        if "reboot" in on:
            self.assertLess(on.index("remote"), on.index("reboot"))
        else:
            self.assertEqual(on[-1], "remote")                                       # no system tiles on a Mac: REMOTE is last

    def test_screens_show_the_address_the_code_and_the_question(self):
        out = launcher.pair_screen("http://192.168.1.5:8090", "482913", 125, 2)
        for text in ("192.168.1.5:8090", "482 913", "2:05", "2 phones"):
            self.assertIn(text, out)
        self.assertIn("PAIRED", launcher.pair_screen("", None, 0, 0, paired="Remko's phone"))
        self.assertIn("1 phone ", launcher.pair_screen("u", "000000", 10, 1))
        c = launcher.confirm_screen({"title": "Shut down this device?", "lines": ["Asked from iPhone."]})
        self.assertIn("Shut down this device?", c)
        self.assertIn("ENTER = yes", c)


class ReloadTests(unittest.TestCase):
    def test_settings_values_cover_the_whole_schema(self):
        L = make()
        vals = L.settings_values()
        self.assertEqual(set(vals), {f["key"] for f in settings.SCHEMA})
        self.assertEqual(vals["clock.h24"], True)
        settings.set_many({"clock.h24": False, "launcher.title": "MY TV"})
        vals = L.settings_values()
        self.assertEqual((vals["clock.h24"], vals["launcher.title"]), (False, "MY TV"))

    def test_hiding_an_app_removes_its_tile_and_keeps_it_known(self):
        L = make()
        L.reload()
        self.assertIn("clock", [e["id"] for e in L.entries])
        settings.set_many({"launcher.hidden": ["clock"]})
        L.reload()
        self.assertNotIn("clock", [e["id"] for e in L.entries])
        self.assertIn("clock", L.known_app_ids())
        self.assertTrue(next(a for a in L.apps() if a["id"] == "clock")["hidden"])

    def test_the_menu_picks_up_changes_made_from_the_web_while_it_is_showing(self):
        L = make()
        L.reload()
        t, out = run_loop(L)
        self.assertTrue(wait_for(lambda: "CLOCK" in screen(L)))
        settings.set_many({"launcher.hidden": ["clock"], "launcher.title": "LIVING ROOM"})
        L.settings_changed(["launcher.hidden", "launcher.title"])
        self.assertTrue(wait_for(lambda: "LIVING ROOM" in screen(L)))
        self.assertNotIn(">CLOCK", screen(L).replace("}CLOCK", ">CLOCK"))
        L.stopping = True
        L.q.put(("cmd", "back"))
        t.join(3)

    def test_the_apps_list_is_in_menu_order_and_marks_hidden_ones(self):
        L = make()
        L.reload()
        ids = [a["id"] for a in L.apps()]
        self.assertEqual(ids[:7], ["clock", "media", "jellyfin", "remoment", "photos", "news", "info"])
        settings.set_many({"launcher.hidden": ["media"], "launcher.order": ["news"]})
        L.reload()
        listed = L.apps()
        self.assertEqual(listed[0]["id"], "news")
        self.assertTrue(next(a for a in listed if a["id"] == "media")["hidden"])

    def test_start_app_and_foreground(self):
        L = make()
        L.reload()
        self.assertEqual(L.foreground(), "launcher")
        self.assertTrue(L.start_app("clock"))
        self.assertFalse(L.start_app("remote"))                                           # only apps can be started, not internal tiles
        L.active = {"id": "news", "name": "NEWS"}
        self.assertEqual(L.foreground(), "news")


class MenuLoopTests(unittest.TestCase):
    def pick_remote(self, L):
        L.q.put(("cmd", "digit:%d" % ([e["id"] for e in L.entries].index("remote") + 1)))

    def test_remote_tile_shows_a_pin_and_a_good_pairing_returns_to_the_menu(self):
        L = make()
        L.setup_web(True, 0, L.config_path, L.launcher_path)
        self.addCleanup(L.web.stop)
        t, out = run_loop(L)
        self.assertTrue(wait_for(lambda: "TV LAUNCHER" in screen(L) or "REMOTE" in screen(L)))
        self.pick_remote(L)
        self.assertTrue(wait_for(lambda: L.pairing.pin and " ".join([L.pairing.pin[:3], L.pairing.pin[3:]]).replace(" ", " ") in screen(L)))
        self.assertTrue(L.pairing.is_open())
        pin = L.pairing.pin
        self.assertIn(str(L.web.port), screen(L))
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", L.web.port, timeout=3)
        c.request("POST", "/api/pair", json.dumps({"pin": pin, "name": "Test phone"}), {"Host": f"127.0.0.1:{L.web.port}", "X-TVL": "1", "Content-Type": "application/json"})
        self.assertEqual(c.getresponse().status, 200)
        self.assertTrue(wait_for(lambda: "PAIRED" in screen(L)))
        self.assertTrue(wait_for(lambda: "PAIRED" not in screen(L) and "CLOCK" in screen(L), 6))
        self.assertFalse(L.pairing.is_open())
        L.stopping = True
        L.q.put(("cmd", "back"))
        t.join(3)

    def test_back_closes_the_pairing_screen_and_the_pin_with_it(self):
        L = make()
        L.setup_web(True, 0, L.config_path, L.launcher_path)
        self.addCleanup(L.web.stop)
        t, out = run_loop(L)
        self.assertTrue(wait_for(lambda: "CLOCK" in screen(L)))
        self.pick_remote(L)
        self.assertTrue(wait_for(lambda: L.pairing.is_open()))
        L.q.put(("cmd", "back"))
        self.assertTrue(wait_for(lambda: not L.pairing.is_open()))
        self.assertTrue(wait_for(lambda: "CLOCK" in screen(L)))
        L.stopping = True
        L.q.put(("cmd", "back"))
        t.join(3)

    def test_a_question_from_the_phone_takes_over_the_screen_and_the_keys(self):
        L = make()
        L.reload()
        ran = []
        t, out = run_loop(L)
        self.assertTrue(wait_for(lambda: "CLOCK" in screen(L)))
        cid = L.confirm("Restart this device?", ["Asked from a phone."], lambda: ran.append(1))
        self.assertTrue(wait_for(lambda: "Restart this device?" in screen(L)))
        L.q.put(("cmd", "digit:1"))                                                       # would start an app: the dialog owns the keys
        time.sleep(0.4)
        self.assertTrue(t.is_alive())
        self.assertEqual(ran, [])
        L.q.put(("cmd", "enter"))
        self.assertTrue(wait_for(lambda: ran == [1]))
        self.assertTrue(wait_for(lambda: "Restart this device?" not in screen(L)))
        self.assertEqual(L.confirms.state(cid)["state"], "allowed")
        L.stopping = True
        L.q.put(("cmd", "back"))
        t.join(3)

    def test_back_says_no(self):
        L = make()
        L.reload()
        ran = []
        t, out = run_loop(L)
        self.assertTrue(wait_for(lambda: "CLOCK" in screen(L)))
        cid = L.confirm("Shut down this device?", [], lambda: ran.append(1))
        self.assertTrue(wait_for(lambda: "Shut down this device?" in screen(L)))
        L.q.put(("cmd", "back"))
        self.assertTrue(wait_for(lambda: L.confirms.state(cid)["state"] == "denied"))
        time.sleep(0.2)
        self.assertEqual(ran, [])
        L.stopping = True
        L.q.put(("cmd", "back"))
        t.join(3)

    def test_without_the_web_interface_the_tile_is_absent_and_a_busy_port_is_reported(self):
        L = make(web_on=False)
        L.setup_web(False, 8090, L.config_path, L.launcher_path)
        self.assertNotIn("remote", [e["id"] for e in L.entries])
        busy = make()
        first = web.WebServer(busy, port=0, bind="0.0.0.0")        # the same address the launcher binds, so the port really is taken
        self.addCleanup(first.stop)
        busy.setup_web(True, first.port, busy.config_path, busy.launcher_path)           # that port is taken
        self.assertIsNone(busy.web)
        self.assertIn("could not start", busy.web_error)

    def test_system_action_without_such_an_entry_is_a_noop(self):
        L = make()
        L.reload()
        L.entries = [e for e in L.entries if e["id"] not in ("reboot", "poweroff")]
        L.system("poweroff")                                                              # no crash, nothing started


if __name__ == "__main__":
    unittest.main()
