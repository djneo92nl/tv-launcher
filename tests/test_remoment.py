import os
import sys
import tempfile
import time
import unittest

import support  # noqa: F401
from support import fake_shell, load_app, press
from tvkit import images, inputs, net

sys.path.insert(0, os.path.join(support.ROOT, "tools"))
import fake_remoment  # noqa: E402

app = load_app("remoment")
rm = app.rm


def pump(shell, until, seconds=5):
    """Run the app's loop until until() is true (background threads hand their results back through the loop)."""
    end = time.time() + seconds
    while time.time() < end:
        shell.step(0.02)
        if until():
            return True
    return False


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = fake_remoment.serve()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.c = rm.Client(self.url)
        self.c.info()

    def test_host_is_normalised(self):
        self.assertEqual(rm.normalise_host("remoment.local/"), "http://remoment.local")
        self.assertEqual(rm.normalise_host("https://x:8080"), "https://x:8080")
        with self.assertRaises(rm.ReMomentError):
            rm.normalise_host(" ")

    def test_info_sets_the_api_and_refuses_strangers(self):
        self.assertEqual(self.c.name, "Fake ReMoment")
        self.assertTrue(self.c.api.endswith("/api"))
        c = rm.Client(self.url)
        c.get = lambda path, **kw: {"hello": 1}
        with self.assertRaises(rm.ReMomentError):
            c.info()
        c.get = lambda path, **kw: {"api_version": 2, "base_url": "x"}
        with self.assertRaises(rm.ReMomentError):
            c.info()

    def test_albums_page_with_the_cursor_and_scope(self):
        first, nxt = self.c.albums()
        second, nxt2 = self.c.albums(nxt)
        self.assertEqual((len(first), len(second), nxt2), (6, 6, None))
        self.assertTrue(any("scope=files" in p and "/library/albums" in p for p in fake_remoment.Handler.log))

    def test_detail_search_playlists_and_recent(self):
        a = self.c.album(1)
        self.assertEqual(len(a["tracks"]), 4)
        self.assertEqual(self.c.artist(1)["name"], "The Aeolians")
        self.assertEqual(self.c.search("blue")["albums"][0]["name"], "Blue Monday")
        self.assertEqual(self.c.search("a"), {"artists": [], "albums": [], "tracks": []})        # too short: no request at all
        self.assertEqual(self.c.playlists()[0][0]["name"], "Evening mix")
        self.assertEqual(len(self.c.playlist(1)["tracks"]), 3)
        self.assertEqual(len(self.c.recent()), 2)
        self.assertEqual(len(self.c.favorites()["albums"]), 1)

    def test_cover_urls_and_decoding_of_an_8_bit_bmp(self):
        art = self.c.album(1)["artwork"]
        self.assertIn("/artwork/", self.c.cover_url(art))
        self.assertIsNone(self.c.cover_url(None))
        self.assertIsNone(self.c.cover_url(art, size=300))                  # not a size the server makes
        w, h, bgra = images.parse_bmp(self.c.fetch_cover(art, 120))
        self.assertEqual((w, h, len(bgra)), (120, 120, 120 * 120 * 4))
        self.assertEqual(bgra[3], 255)

    def test_track_entry_fills_in_from_the_album(self):
        e = rm.track_entry({"id": 5, "name": "T", "duration": 7, "playable": False}, {"name": "Alb", "artist": {"name": "Art"}, "artwork": {"hash": "h"}})
        self.assertEqual((e["artist"], e["album"], e["art"], e["playable"]), ("Art", "Alb", {"hash": "h"}, False))

    def test_errors_do_not_leak_the_address(self):
        c = rm.Client("http://127.0.0.1:1/secret", timeout=1)
        with self.assertRaises(net.NetError) as cm:
            c.info()
        self.assertNotIn("secret", str(cm.exception))


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = fake_remoment.serve()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def make(self, host=None):
        s = fake_shell(app.ReMoment, {"cache_dir": tempfile.mkdtemp(), "remoment": {"host": host or self.url}})
        s.start()
        self.addCleanup(lambda: setattr(inputs, "TEXT_MODE", False))
        return s

    def home(self):
        s = self.make()
        self.assertTrue(pump(s, lambda: s.connected and s.cur is not None))
        return s

    def open_albums(self, s):
        press(s, "enter")
        self.assertTrue(pump(s, lambda: len(s.cur.view.items) == 6))

    def test_connects_and_shows_the_home_menu(self):
        s = self.home()
        self.assertEqual(s.cur.kind, "home")
        self.assertIn("ALBUMS", s.draw(time.time()))

    def test_unreachable_server_says_so_and_enter_retries(self):
        s = self.make("http://127.0.0.1:1")
        self.assertTrue(pump(s, lambda: s.conn_error))
        self.assertIn("Could not reach ReMoment", s.draw(time.time()))
        s.client = rm.Client(self.url)
        press(s, "enter")
        self.assertTrue(pump(s, lambda: s.connected))

    def test_album_grid_pages_in_more_albums_near_the_end(self):
        s = self.home()
        self.open_albums(s)
        press(s, "down", "right", "right")                       # towards the end of the first page of six
        self.assertTrue(pump(s, lambda: len(s.cur.view.items) == 12))

    def test_covers_load_in_the_background_and_become_image_overlays(self):
        s = self.home()
        self.open_albums(s)
        self.assertTrue(pump(s, lambda: any(c[0] == "overlay-add" for c in s.mpv.commands)))
        add = [c for c in s.mpv.commands if c[0] == "overlay-add"][0]
        self.assertEqual((add[6], add[7], add[8]), ("bgra", 120, 120))   # a 120 px BMP as raw BGRA (no window size known: size 120)
        self.assertTrue(os.path.exists(add[4]))

    def test_album_without_a_cover_gets_a_placeholder_not_an_error(self):
        s = self.home()
        self.open_albums(s)
        out = s.draw(time.time())
        self.assertIn("Echoes", out)                                # album 5 has no artwork

    def test_play_album_then_autoplay_next_and_end(self):
        s = self.home()
        self.open_albums(s)
        press(s, "enter")                                         # album 1
        self.assertTrue(pump(s, lambda: s.cur.kind == "album" and len(s.cur.view.items) == 5))
        press(s, "enter")                                         # PLAY ALBUM
        self.assertTrue(s.pb.active)
        self.assertTrue(s.mpv.played[-1].endswith("/stream/11"))
        s.on_mpv({"event": "end-file", "reason": "eof"})
        self.assertTrue(s.mpv.played[-1].endswith("/stream/12"))
        press(s, "info")
        self.assertTrue(s.now_open)
        self.assertIn("NOW PLAYING", s.draw(time.time()))
        press(s, "right")
        self.assertTrue(s.mpv.played[-1].endswith("/stream/13"))
        press(s, "left")
        self.assertTrue(s.mpv.played[-1].endswith("/stream/12"))
        press(s, "back")                                          # leaves the now-playing screen, the music goes on
        self.assertFalse(s.now_open)
        self.assertTrue(s.pb.active)
        self.assertIn("Absolution - part 2", s.draw(time.time()))   # the strip at the bottom
        for _ in range(2):
            s.on_mpv({"event": "end-file", "reason": "eof"})
        s.on_mpv({"event": "end-file", "reason": "eof"})            # the last track ended
        self.assertFalse(s.pb.active)

    def test_an_album_that_is_not_in_the_own_files_does_not_play(self):
        s = self.home()
        self.open_albums(s)
        press(s, "down", "right", "right")                         # album 7 (Spotify only in the fake)
        self.assertTrue(pump(s, lambda: len(s.cur.view.items) == 12))
        s.cur.view.sel = 6
        press(s, "enter")
        self.assertTrue(pump(s, lambda: s.cur.kind == "album" and s.cur.view.items))
        n = len(s.mpv.played)
        press(s, "enter")
        self.assertEqual(len(s.mpv.played), n)
        self.assertIn("Not in your own files", s.note)

    def test_unplayable_track_is_skipped_but_not_forever(self):
        s = self.home()
        s.play_queue([{"id": i, "title": f"t{i}", "artist": "", "album": "", "dur": 1, "art": None, "playable": True} for i in (1, 2, 3, 4)], 0)
        s.on_mpv({"event": "end-file", "reason": "error"})
        self.assertTrue(s.mpv.played[-1].endswith("/stream/2"))
        s.on_mpv({"event": "end-file", "reason": "error"})
        s.on_mpv({"event": "end-file", "reason": "error"})
        self.assertIn("Could not play", s.note)
        self.assertFalse(s.now_open)

    def test_search_with_a_keyboard_and_open_a_result(self):
        s = self.home()
        press(s, "down", "down", "down", "down", "down", "enter")   # SEARCH
        self.assertEqual(s.cur.kind, "search")
        self.assertTrue(inputs.TEXT_MODE)
        press(s, "char:b", "char:l", "char:u")
        press(s, "up", "right", "right", "right", "enter")            # up from the top row wraps to the action row: SEARCH
        self.assertTrue(pump(s, lambda: s.cur.kind == "results"))
        self.assertFalse(inputs.TEXT_MODE)
        kinds = [i["kind"] for i in s.cur.view.items]
        self.assertIn("album", kinds)
        self.assertIn("BLU", s.cur.title)
        press(s, "enter")                                             # the first hit is the artist "Blue Hour": its albums
        self.assertTrue(pump(s, lambda: s.cur.kind == "albums" and s.cur.view.items))
        self.assertEqual([a["name"] for a in s.cur.view.items], ["Blue Monday", "Glass", "Lantern"])

    def test_search_needs_two_letters(self):
        s = self.home()
        press(s, "down", "down", "down", "down", "down", "enter", "char:b")
        press(s, "up", "right", "right", "right", "enter")
        self.assertIn("at least two", s.note)
        self.assertEqual(s.cur.kind, "search")

    def test_typing_q_in_the_search_box_does_not_quit(self):
        s = self.home()
        press(s, "down", "down", "down", "down", "down", "enter", "char:q")
        self.assertFalse(s.stopping)
        self.assertEqual(s.cur.keyboard.text, "q")

    def test_playlist_skips_tracks_that_are_not_local(self):
        s = self.home()
        press(s, "down", "down", "enter")                            # PLAYLISTS
        self.assertTrue(pump(s, lambda: s.cur.view.items))
        press(s, "enter")
        self.assertTrue(pump(s, lambda: s.cur.kind == "playlist" and s.cur.view.items))
        self.assertEqual([t["id"] for t in s.cur.view.items], [11, 32])   # 99 is "missing locally"
        press(s, "enter")
        self.assertTrue(s.mpv.played[-1].endswith("/stream/11"))

    def test_back_goes_up_and_then_leaves(self):
        s = self.home()
        self.open_albums(s)
        press(s, "back")
        self.assertEqual(s.cur.kind, "home")
        press(s, "back")
        self.assertTrue(s.stopping)

    def test_keys_in_the_list_do_not_seek_but_global_keys_work_while_playing(self):
        s = self.home()
        s.play_queue([{"id": i, "title": f"t{i}", "artist": "", "album": "", "dur": 1, "art": None, "playable": True} for i in (1, 2)], 0)
        press(s, "menu")
        self.assertIn(("cycle", "pause"), s.mpv.commands)
        press(s, "last")
        self.assertTrue(s.mpv.played[-1].endswith("/stream/2"))


if __name__ == "__main__":
    unittest.main()
