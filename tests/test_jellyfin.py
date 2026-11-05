import json
import os
import sys
import tempfile
import time
import unittest

import support  # noqa: F401
from support import fake_shell, load_app, press
from test_remoment import pump
from tvkit import images, inputs, net

sys.path.insert(0, os.path.join(support.ROOT, "tools"))
import fake_jellyfin as fj  # noqa: E402

app = load_app("jellyfin")
jf = app.jf


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = fj.serve()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        fj.Handler.quick, fj.Handler.quick_polls_needed, fj.Handler.polls = True, 2, 0

    def logged_in(self):
        c = jf.Client(self.url, "dev1")
        c.login("kid", "secret")
        return c

    def test_login_wrong_password_and_server_errors_are_told_apart(self):
        c = jf.Client(self.url, "dev1")
        with self.assertRaises(jf.JellyfinError) as cm:
            c.login("kid", "nope")
        self.assertIn("wrong name or password", str(cm.exception))
        fj.Handler.fail_login_with = 500
        try:
            with self.assertRaises(net.NetError) as cm2:
                c.login("kid", "secret")
            self.assertIn("500", str(cm2.exception))
        finally:
            fj.Handler.fail_login_with = None

    def test_token_goes_in_headers_never_in_urls_or_errors(self):
        c = self.logged_in()
        c.views(); c.resume(); c.direct_url("x"); c.transcode_url("x")
        self.assertNotIn(fj.TOKEN, c.direct_url("x") + c.transcode_url("x"))
        self.assertTrue(all(fj.TOKEN not in path for _, path, _ in fj.Handler.log))
        self.assertIn(fj.TOKEN, c.header_option())

    def test_check_detects_a_revoked_token(self):
        c = self.logged_in()
        self.assertTrue(c.check())
        c.token = "revoked"
        self.assertFalse(c.check())

    def test_browsing(self):
        c = self.logged_in()
        self.assertEqual([v["title"] for v in c.views()], ["Movies", "Shows", "Music"])
        items, total = c.items(fj.mid(1))
        self.assertEqual((total, items[1]["pos"], items[2]["played"]), (6, 1500.0, True))
        self.assertEqual([e["title"] for e in c.resume()], ["Blue Valley", "Episode 2"])
        self.assertEqual(c.next_up()[0]["sub"], "S1E3 Night Shift")
        self.assertEqual(len(c.seasons(fj.mid(200))), 2)
        self.assertEqual([e["title"] for e in c.episodes(fj.mid(200), fj.mid(201))], ["Episode 1", "Episode 2", "Episode 3"])
        self.assertEqual(c.search("alien")[0]["title"], "Alien Rain")

    def test_item_entry_poster_rules(self):
        ep = {"Id": "e", "Type": "Episode", "Name": "E", "SeriesId": "s", "SeriesPrimaryImageTag": "t", "ImageTags": {"Primary": "own"}}
        self.assertEqual(jf.item_entry(ep)["pic"], ("s", "t"))                       # an episode shows its series' poster
        self.assertEqual(jf.item_entry({"Id": "m", "Type": "Movie", "ImageTags": {"Primary": "p"}})["pic"], ("m", "p"))
        self.assertIsNone(jf.item_entry({"Id": "m", "Type": "Movie"})["pic"])
        self.assertTrue(jf.playable(jf.item_entry({"Id": "m", "Type": "Movie"})))
        self.assertFalse(jf.playable(jf.item_entry({"Id": "m", "Type": "Series"})))

    def test_poster_decodes_and_reports_are_sent(self):
        c = self.logged_in()
        w, h, _ = images.parse_image(c.poster((fj.mid(100), "t1"), 96))
        self.assertEqual((w, h), (64, 96))
        c.report_start("abc", 12.5)
        c.report_progress("abc", 20, paused=True)
        c.report_stop("abc", 30)
        posts = [(p, json.loads(b)) for m, p, b in fj.Handler.log if m == "POST" and p.startswith("/Sessions")]
        self.assertEqual([p for p, _ in posts][-3:], ["/Sessions/Playing", "/Sessions/Playing/Progress", "/Sessions/Playing/Stopped"])
        self.assertEqual(posts[-3][1]["PositionTicks"], 125_000_000)
        self.assertTrue(posts[-2][1]["IsPaused"])

    def test_reports_never_raise(self):
        c = jf.Client("http://127.0.0.1:1", "d", timeout=1)
        c.token = "x"
        c.report_progress("a", 1)                       # a dead server must not stop the film

    def test_quick_connect_calls(self):
        c = jf.Client(self.url, "dev9")
        self.assertTrue(c.quick_enabled())
        secret, code = c.quick_start()
        self.assertEqual((secret, code), ("sec-1", "482913"))
        fj.Handler.quick_polls_needed = 2
        self.assertFalse(c.quick_approved(secret))
        self.assertTrue(c.quick_approved(secret))
        s = c.quick_login(secret)
        self.assertEqual((s["token"], s["user_name"]), (fj.TOKEN, "kid"))
        fj.Handler.quick = False
        self.assertFalse(jf.Client(self.url, "d").quick_enabled())

    def test_host_is_normalised(self):
        self.assertEqual(jf.normalise_host("nas:8096/"), "http://nas:8096")
        with self.assertRaises(jf.JellyfinError):
            jf.normalise_host("")


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = fj.serve()

    def setUp(self):
        fj.Handler.quick, fj.Handler.quick_polls_needed, fj.Handler.polls = True, 2, 0

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def make(self, saved=None, host=None):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "cfg", "jellyfin.json")
        if saved:
            net.write_private(path, saved)
        s = fake_shell(app.Jellyfin, {"cache_dir": d, "jellyfin": {"host": host or self.url, "token_file": path}})
        s.start()
        self.addCleanup(lambda: setattr(inputs, "TEXT_MODE", False))
        s.path = path
        return s

    def logged_in(self):
        s = self.make({"host": self.url, "token": fj.TOKEN, "user_id": fj.UID, "user_name": "kid", "device_id": "d1"})
        self.assertTrue(pump(s, lambda: s.cur is not None and s.cur.kind == "home" and any(r[0] == "view" for r in s.cur.view.items)))
        return s

    def open_row(self, s, title):
        s.cur.view.sel = next(i for i, r in enumerate(s.cur.view.items) if r[1] == title)
        press(s, "enter")

    def test_first_start_asks_for_name_and_password_then_saves_only_the_token(self):
        fj.Handler.quick = False
        s = self.make()
        self.assertTrue(pump(s, lambda: s.cur is not None and s.cur.kind == "login"))
        self.assertTrue(inputs.TEXT_MODE)
        for ch in "kid":
            press(s, "char:" + ch)
        s.cur.keyboard.r, s.cur.keyboard.c = len(s.cur.keyboard.rows), 4                 # NEXT
        press(s, "enter")
        self.assertEqual(s.cur.step, 1)
        self.assertTrue(s.cur.keyboard.secret)
        for ch in "secret":
            press(s, "char:" + ch)
        self.assertNotIn("secret", s.draw(time.time()).replace("secret", "", 0) if False else "".join(c for c in s.draw(time.time()) if c.isalnum()).replace("Passwordfor", ""))
        s.cur.keyboard.r, s.cur.keyboard.c = len(s.cur.keyboard.rows), 4                 # LOGIN
        press(s, "enter")
        self.assertTrue(pump(s, lambda: s.cur.kind == "home"))
        saved = json.load(open(s.path))
        self.assertEqual((saved["token"], saved["user_name"]), (fj.TOKEN, "kid"))
        self.assertNotIn("secret", json.dumps(saved))
        self.assertEqual(os.stat(s.path).st_mode & 0o777, 0o600)

    def test_wrong_password_returns_to_the_login_with_a_message(self):
        fj.Handler.quick = False
        s = self.make()
        self.assertTrue(pump(s, lambda: s.cur is not None and s.cur.kind == "login"))
        s.user_text = "kid"
        s.cur.step, s.cur.keyboard = 1, app.ui.Keyboard("bad", full=True, secret=True, submit_label="LOGIN")
        s.login_step(s.cur)
        self.assertTrue(pump(s, lambda: "wrong name or password" in s.note))
        self.assertEqual((s.cur.kind, s.cur.step), ("login", 0))
        self.assertFalse(os.path.exists(s.path))

    def test_quick_connect_shows_a_code_and_signs_in_when_it_is_approved(self):
        fj.Handler.quick_polls_needed = 2
        s = self.make()
        self.assertTrue(pump(s, lambda: s.cur is not None and s.cur.kind == "quick" and s.cur.code))
        self.assertEqual(s.cur.code, "482913")
        self.assertFalse(inputs.TEXT_MODE)
        self.assertIn("QUICK CONNECT", s.draw(time.time()))
        self.assertTrue(pump(s, lambda: (setattr(s.cur, "polled", 0) if s.cur.kind == "quick" and not s.cur.busy else None) or s.cur.kind == "home", seconds=8))
        saved = json.load(open(s.path))
        self.assertEqual((saved["token"], saved["user_name"]), (fj.TOKEN, "kid"))
        self.assertTrue(any(p == "/Users/AuthenticateWithQuickConnect" for _, p, _ in fj.Handler.log))
        self.assertTrue(all("Pw" not in b.decode() for _, p, b in fj.Handler.log if p == "/Users/AuthenticateWithQuickConnect"))

    def test_quick_connect_can_be_swapped_for_a_password_and_back(self):
        s = self.make()
        self.assertTrue(pump(s, lambda: s.cur is not None and s.cur.kind == "quick" and s.cur.code))
        press(s, "enter")
        self.assertEqual(s.cur.kind, "login")
        self.assertTrue(inputs.TEXT_MODE)
        press(s, "back")
        self.assertTrue(pump(s, lambda: s.cur.kind == "quick" and s.cur.code))

    def test_without_quick_connect_the_password_screen_comes_first(self):
        fj.Handler.quick = False
        s = self.make()
        self.assertTrue(pump(s, lambda: s.cur is not None and s.cur.kind == "login"))

    def test_an_unapproved_code_is_renewed_after_a_while(self):
        s = self.make()
        self.assertTrue(pump(s, lambda: s.cur is not None and s.cur.kind == "quick" and s.cur.code))
        n = len([1 for _, p, _ in fj.Handler.log if p == "/QuickConnect/Initiate"])
        s.cur.asked -= 400
        self.assertTrue(pump(s, lambda: len([1 for _, p, _ in fj.Handler.log if p == "/QuickConnect/Initiate"]) > n))

    def test_saved_login_goes_straight_to_home_and_a_revoked_token_asks_again(self):
        s = self.logged_in()
        self.assertEqual([r[1] for r in s.cur.view.items][:4], ["CONTINUE WATCHING", "NEXT UP", "MOVIES", "SHOWS"])
        self.assertNotIn("MUSIC", [r[1] for r in s.cur.view.items])                        # this client plays video
        bad = self.make({"host": self.url, "token": "revoked", "user_id": fj.UID, "user_name": "kid", "device_id": "d1"})
        self.assertTrue(pump(bad, lambda: bad.cur is not None and bad.cur.kind == "quick"))      # a revoked token: sign in again

    def test_unreachable_server_says_so(self):
        s = self.make(host="http://127.0.0.1:1")
        self.assertTrue(pump(s, lambda: s.state == "error"))
        self.assertIn("Could not reach Jellyfin", s.draw(time.time()))

    def test_movie_grid_posters_and_progress(self):
        s = self.logged_in()
        self.open_row(s, "MOVIES")
        self.assertTrue(pump(s, lambda: len(s.cur.view.items) == 6))
        self.assertTrue(pump(s, lambda: any(c[0] == "overlay-add" for c in s.mpv.commands)))
        out = s.draw(time.time())
        self.assertIn("Alien Rain", out)
        self.assertIn("resume 25:00", out)
        self.assertIn("watched", out)

    def test_play_with_resume_choice_header_and_progress_reports(self):
        s = self.logged_in()
        self.open_row(s, "MOVIES")
        self.assertTrue(pump(s, lambda: len(s.cur.view.items) == 6))
        press(s, "right", "enter")                                                         # Blue Valley, half watched
        self.assertEqual(s.cur.kind, "detail")
        self.assertEqual([r[0] for r in s.cur.view.items], ["resume", "start"])
        self.assertIn("RESUME FROM 25:00", s.draw(time.time()))
        press(s, "enter")                                                                  # resume
        self.assertTrue(s.pb.active)
        self.assertIn("/Videos/" + fj.mid(101) + "/stream?static=true", s.mpv.played[-1])
        self.assertNotIn(fj.TOKEN, s.mpv.played[-1])
        self.assertIn(("set", "http-header-fields", "X-Emby-Token: " + fj.TOKEN), s.mpv.commands)
        self.assertTrue(pump(s, lambda: any(p == "/Sessions/Playing" for _, p, _ in fj.Handler.log[-20:])))
        s.on_mpv({"event": "property-change", "name": "time-pos", "data": 1600.0})
        s.playing["last_report"] = 0
        s.tick(time.time())
        self.assertTrue(pump(s, lambda: any(p == "/Sessions/Playing/Progress" for _, p, _ in fj.Handler.log[-20:])))
        press(s, "back")                                                                   # stop
        self.assertFalse(s.pb.active)
        self.assertTrue(pump(s, lambda: any(p == "/Sessions/Playing/Stopped" for _, p, _ in fj.Handler.log[-20:])))
        stop = json.loads([b for _, p, b in fj.Handler.log if p == "/Sessions/Playing/Stopped"][-1])
        self.assertEqual(stop["PositionTicks"], 16_000_000_000)
        self.assertEqual(s.cur.kind, "detail")

    def test_fallback_to_transcode_once_then_gives_up(self):
        s = self.logged_in()
        e = jf.item_entry(fj.MOVIES[0])
        s.play(e, 0)
        s.on_mpv({"event": "end-file", "reason": "error"})
        self.assertIn("master.m3u8", s.mpv.played[-1])
        self.assertEqual(s.playing["method"], "Transcode")
        s.on_mpv({"event": "end-file", "reason": "error"})
        self.assertIsNone(s.playing)
        self.assertIn("Could not play Alien Rain", s.note)

    def test_series_to_season_to_episode_and_autoplay_next(self):
        s = self.logged_in()
        self.open_row(s, "SHOWS")
        self.assertTrue(pump(s, lambda: len(s.cur.view.items) == 2))
        press(s, "enter")
        self.assertTrue(pump(s, lambda: s.cur.kind == "seasons" and len(s.cur.view.items) == 2))
        press(s, "enter")
        self.assertTrue(pump(s, lambda: s.cur.kind == "episodes" and len(s.cur.view.items) == 3))
        out = s.draw(time.time())
        self.assertIn("watched", out)
        self.assertIn("resume 10:00", out)
        press(s, "down", "down", "enter")                                                   # episode 3
        press(s, "enter")
        self.assertTrue(s.mpv.played[-1].endswith(fj.mid(303) + "/stream?static=true&MediaSourceId=" + fj.mid(303)))
        s.on_mpv({"event": "end-file", "reason": "eof"})                                    # last in the season: back to the detail
        self.assertIsNone(s.playing)
        self.assertEqual(s.cur.kind, "detail")
        self.assertIn("PLAY AGAIN", s.draw(time.time()))

    def test_autoplay_goes_to_the_next_episode(self):
        s = self.logged_in()
        eps = [jf.item_entry(x) for x in fj.EPS[fj.mid(201)]]
        s.play(eps[0], 0, eps)
        s.on_mpv({"event": "end-file", "reason": "eof"})
        self.assertTrue(s.mpv.played[-1].endswith(fj.mid(302) + "/stream?static=true&MediaSourceId=" + fj.mid(302)))
        press(s, "last")
        self.assertTrue(s.mpv.played[-1].endswith(fj.mid(303) + "/stream?static=true&MediaSourceId=" + fj.mid(303)))

    def test_continue_watching_next_up_and_search(self):
        s = self.logged_in()
        press(s, "enter")
        self.assertTrue(pump(s, lambda: len(s.cur.view.items) == 2))
        press(s, "back")
        press(s, "down", "enter")
        self.assertTrue(pump(s, lambda: len(s.cur.view.items) == 1))
        press(s, "back")
        self.open_row(s, "SEARCH")
        self.assertEqual(s.cur.kind, "search")
        for ch in "alien":
            press(s, "char:" + ch)
        s.cur.keyboard.r, s.cur.keyboard.c = len(s.cur.keyboard.rows), 3
        press(s, "enter")
        self.assertTrue(pump(s, lambda: s.cur.kind == "grid" and s.cur.view.items))
        self.assertEqual(s.cur.view.items[0]["title"], "Alien Rain")

    def test_sign_out_forgets_the_token(self):
        s = self.logged_in()
        self.assertTrue(os.path.exists(s.path))
        self.open_row(s, "SIGN OUT")
        self.assertFalse(os.path.exists(s.path))
        self.assertEqual(s.cur.kind, "login")

    def test_back_leaves(self):
        s = self.logged_in()
        press(s, "back")
        self.assertTrue(s.stopping)


if __name__ == "__main__":
    unittest.main()
