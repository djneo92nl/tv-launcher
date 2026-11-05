import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest

import support  # noqa: F401
import launcher
from support import fake_shell, load_app, press
from tvkit import control, inputs
from tvkit.shell import Shell


class FakeShell:
    def __init__(self):
        import queue
        self.q = queue.Queue()
        self.calls = []

    def status(self):
        return {"app": "fake", "screen": "HOME"}


def raw(path, data, timeout=2):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(path)
    s.sendall(data)
    out = s.recv(65536)
    s.close()
    return json.loads(out)


class ControlServerTests(unittest.TestCase):
    def setUp(self):
        self.shell = FakeShell()
        self.srv = control.ControlServer(self.shell, "ctltest")
        self.srv.start()
        self.addCleanup(self.srv.stop)

    def drain(self):
        out = []
        while not self.shell.q.empty():
            out.append(self.shell.q.get())
        return out

    def test_socket_is_private(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.srv.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.srv.path)).st_mode), 0o700)

    def test_keys_reach_the_queue_and_only_known_ones(self):
        self.assertEqual(control.ask("ctltest", {"cmd": "key", "name": "up"}), {"ok": True})
        self.assertEqual(self.drain(), [("cmd", "up")])
        for bad in ("rm -rf", "char:a", "digit:10", "", None, 5, "UP", ["up"], {"a": 1}):
            self.assertFalse(control.ask("ctltest", {"cmd": "key", "name": bad})["ok"], bad)
        self.assertEqual(self.drain(), [])

    def test_text_becomes_char_events_and_is_cleaned(self):
        control.ask("ctltest", {"cmd": "text", "text": "a b\x00\néZ!"})
        self.assertEqual([v for _, v in self.drain()], ["char:a", "char:space", "char:b", "char:Z", "char:!"])
        control.ask("ctltest", {"cmd": "text", "text": "x" * 500})
        self.assertEqual(len(self.drain()), control.MAX_TEXT)
        self.assertFalse(control.ask("ctltest", {"cmd": "text", "text": 5})["ok"])

    def test_status_and_quit(self):
        self.assertEqual(control.ask("ctltest", {"cmd": "status"}), {"ok": True, "status": {"app": "fake", "screen": "HOME"}})
        control.ask("ctltest", {"cmd": "quit"})
        self.assertEqual(self.drain(), [("cmd", "quit")])

    def test_do_runs_only_actions_the_owner_offers(self):
        done = []
        self.shell.actions = ("pair", "restart")
        self.shell.do_action = lambda name: done.append(name) or name == "pair"
        self.assertEqual(control.ask("ctltest", {"cmd": "do", "action": "pair"}), {"ok": True})
        self.assertEqual(control.ask("ctltest", {"cmd": "do", "action": "restart"}), {"ok": False})           # offered, but it said no
        for bad in ("poweroff", "rm -rf", "", None, 5, ["pair"], {"a": 1}):
            self.assertFalse(control.ask("ctltest", {"cmd": "do", "action": bad})["ok"], bad)
        self.assertEqual(done, ["pair", "restart"])
        self.shell.do_action = lambda name: 1 / 0
        self.assertEqual(control.ask("ctltest", {"cmd": "do", "action": "pair"}), {"ok": False, "error": "action failed"})

    def test_an_app_that_offers_nothing_refuses_every_action(self):
        self.assertFalse(control.ask("ctltest", {"cmd": "do", "action": "pair"})["ok"])

    def test_garbage_and_oversized_requests_are_refused_and_the_server_survives(self):
        for data in (b"not json\n", b"[1,2]\n", b'{"cmd":"exec","code":"x"}\n', b'{"cmd":5}\n', b"\n"):
            self.assertFalse(raw(self.srv.path, data)["ok"], data[:20])
        try:
            self.assertFalse(raw(self.srv.path, b"x" * 10000)["ok"])          # too long: refused, or the connection is simply dropped
        except OSError:
            pass
        self.assertTrue(control.ask("ctltest", {"cmd": "status"})["ok"])

    def test_a_failing_status_is_reported_not_raised(self):
        self.shell.status = lambda: 1 / 0
        self.assertEqual(control.ask("ctltest", {"cmd": "status"}), {"ok": False, "error": "status failed"})

    def test_ask_says_not_running_for_a_missing_socket(self):
        self.assertEqual(control.ask("nobody-here", {"cmd": "status"}), {"ok": False, "error": "not running"})

    def test_a_stale_socket_file_is_replaced_and_stop_removes_it(self):
        self.srv.stop()
        with open(self.srv.path, "w") as f:
            f.write("stale")
        s2 = control.ControlServer(self.shell, "ctltest")
        s2.start()
        self.assertTrue(control.ask("ctltest", {"cmd": "status"})["ok"])
        s2.stop()
        self.assertFalse(os.path.exists(s2.path))

    def test_many_clients_at_once(self):
        results = []
        ts = [threading.Thread(target=lambda: results.append(control.ask("ctltest", {"cmd": "status"})["ok"])) for _ in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(results, [True] * 20)

    def test_long_socket_paths_fall_back_to_a_short_one(self):
        old = os.environ["TVL_STATE_DIR"]
        os.environ["TVL_STATE_DIR"] = "/tmp/" + "x" * 120
        try:
            self.assertLess(len(control.socket_path("clock")), 104)
        finally:
            os.environ["TVL_STATE_DIR"] = old


class ShellIntegrationTests(unittest.TestCase):
    def test_default_status_names_the_app_and_the_text_mode(self):
        s = fake_shell(Shell)
        os.environ["TVL_APP_ID"] = "my-app"
        try:
            self.assertEqual(s.status()["app"], "my-app")
        finally:
            del os.environ["TVL_APP_ID"]
        inputs.TEXT_MODE = True
        try:
            self.assertTrue(s.status()["text_input"])
        finally:
            inputs.TEXT_MODE = False

    def test_data_dir_is_private_and_per_app(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["TVL_DATA_DIR"] = d
            os.environ["TVL_APP_ID"] = "x-app"
            try:
                path = fake_shell(Shell).data_dir
                self.assertEqual(path, os.path.join(d, "x-app"))
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
            finally:
                del os.environ["TVL_DATA_DIR"], os.environ["TVL_APP_ID"]

    def test_a_running_shell_serves_its_own_socket(self):
        class A(Shell):
            name = "ctl-integ"
            got = []

            def handle(self, cmd):
                self.got.append(cmd)
                if cmd == "quit":
                    self.quit()

            def status(self):
                return {"app": "ctl-integ", "screen": "X", "playing": None, "text_input": False}
        s = A({"cache_dir": tempfile.mkdtemp(), "prof": {"mpv": ["mpv"]}, "profile": "test", "evdev": False})
        s.open_screen = lambda: setattr(s, "mpv", support.FakeMpv())
        s.close_screen = lambda: None
        t = threading.Thread(target=s.run, daemon=True)
        t.start()
        for _ in range(50):
            if control.ask("ctl-integ", {"cmd": "status"})["ok"]:
                break
            time.sleep(0.05)
        self.assertEqual(control.ask("ctl-integ", {"cmd": "status"})["status"]["screen"], "X")
        control.ask("ctl-integ", {"cmd": "key", "name": "down"})
        control.ask("ctl-integ", {"cmd": "text", "text": "hi"})
        control.ask("ctl-integ", {"cmd": "quit"})
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(A.got, ["down", "char:h", "char:i", "quit"])
        self.assertFalse(os.path.exists(control.socket_path("ctl-integ")))


class InfoButtonsTests(unittest.TestCase):
    def info(self, offered):
        s = fake_shell(load_app("info").Info)
        s.start = lambda: None
        s.rows, s.at, s.offered, s.sel, s.note = [("ADDRESS", "10.0.0.2", 0)], 1e12, offered, 0, ""
        return s

    def test_buttons_show_only_what_the_launcher_offers_and_enter_asks_for_it(self):
        import unittest.mock as mock
        asked = []
        s = self.info(["pair", "restart", "poweroff"])
        with mock.patch.object(control, "ask", lambda name, req, timeout=2.0: asked.append((name, req)) or {"ok": True}):
            out = s.draw(0)
            for label in ("PAIR A PHONE", "RESTART", "SHUT DOWN"):
                self.assertIn(label, out)
            press(s, "right", "enter")
            self.assertEqual(asked, [("launcher", {"cmd": "do", "action": "restart"})])
            press(s, "left", "left", "enter")                                            # wraps round to the last button
            self.assertEqual(asked[-1][1]["action"], "poweroff")
        self.assertNotIn("RESTART", self.info(["pair"]).draw(0))

    def test_started_by_hand_there_are_no_buttons_and_enter_does_nothing(self):
        s = self.info([])
        self.assertNotIn("SHUT DOWN", s.draw(0))
        press(s, "enter")
        self.assertFalse(s.stopping)

    def test_a_refusal_is_said_not_ignored(self):
        import unittest.mock as mock
        s = self.info(["pair"])
        with mock.patch.object(control, "ask", lambda name, req, timeout=2.0: {"ok": False}):
            press(s, "enter")
        self.assertIn("Not available", s.draw(0))


class AppStatusTests(unittest.TestCase):
    def test_media_and_photos_and_news_report_their_screen(self):
        media = load_app("media")
        d = tempfile.mkdtemp()
        os.makedirs(d + "/Shows")
        open(d + "/Shows/a.mp4", "w").close()
        s = fake_shell(media.Media, {"cache_dir": d + "/.c", "media": {"roots": [d]}})
        s.start()
        press(s, "enter", "enter")
        self.assertEqual(s.status()["screen"], "Shows")
        press(s, "enter")
        st = s.status()
        self.assertEqual((st["screen"], st["playing"]["title"]), ("PLAYING", "a"))
        photos = load_app("photos")
        p = fake_shell(photos.Photos, {"photos": {"dirs": [d + "/none"]}})
        p.start()
        self.assertEqual((p.status()["screen"], p.status()["playing"]), ("PHOTOS", None))
        news = load_app("news")
        n = fake_shell(news.News, {"feeds": []})
        n.start()
        self.assertEqual(n.status()["screen"], "NEWS")


class LauncherRemoteTests(unittest.TestCase):
    def make(self, cmds=None):
        entries = [{"id": i, "name": i.upper(), "kind": "app", "cmd": cmds or ["python3", "-c", "import time; time.sleep(30)"], "desc": "", "icon": "app"} for i in ("one", "two")]
        d = tempfile.mkdtemp()
        L = launcher.Launcher({"prof": {}, "cache_dir": d, "profile": "pi"}, json.loads(json.dumps(launcher.DEFAULT_CONFIG)), d + "/l.json", entries)
        return L

    def test_status_reports_the_menu_and_the_running_app(self):
        L = self.make()
        L.menu = launcher.Menu(L.entries)
        st = L.status()
        self.assertEqual((st["app"], st["screen"], st["running"], st["selected"]), ("launcher", "MENU", None, "one"))
        L.active = L.entries[1]
        self.assertEqual(L.status()["running"], {"id": "two", "name": "TWO"})

    def test_request_start_while_the_menu_shows(self):
        L = self.make()
        self.assertFalse(L.request_start("ghost"))
        self.assertTrue(L.request_start("two"))
        self.assertEqual(L.q.get_nowait(), ("app", ("start", "two")))
        self.assertEqual(L.take_pending()["id"], "two")
        self.assertIsNone(L.take_pending())

    def test_request_start_while_an_app_runs_stops_it_and_starts_the_next(self):
        L = self.make(["python3", "-c", "import time; time.sleep(30)"])
        L.open_screen = lambda: None
        L.close_screen = lambda: None
        started = []
        real = L.run_app

        def run_app(entry):
            started.append(entry["id"])
            if entry["id"] == "two":
                L.stopping = True                                 # end the test once the second app has been started
                return ""
            return real(entry)
        L.run_app = run_app
        L.menu_loop = lambda note="", first=False: L.entries[0]                # the first thing to run is "one"
        t = threading.Thread(target=L.run, daemon=True)
        import signal
        signal.signal = lambda *a: None                           # run() installs handlers; not allowed off the main thread
        t.start()
        for _ in range(100):
            if L.child:
                break
            time.sleep(0.05)
        self.assertEqual(L.active["id"], "one")
        self.assertTrue(L.request_start("two"))
        t.join(10)
        self.assertEqual(started, ["one", "two"])


if __name__ == "__main__":
    unittest.main()
