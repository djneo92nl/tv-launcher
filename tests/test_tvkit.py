import json
import os
import tempfile
import time
import unittest

import support  # noqa: F401
from support import fake_shell, load_app, press
from tvkit import config, images, net, ui
from tvkit.shell import Shell


class UiTests(unittest.TestCase):
    def test_icons_are_16_by_16(self):
        for name, (_, rows) in ui.ICONS.items():
            self.assertEqual((len(rows), {len(r) for r in rows}), (16, {16}), name)

    def test_unknown_icon_draws_a_frame(self):
        self.assertTrue(ui.icon(0, 0, "nope"))

    def test_esc_removes_ass_control_characters(self):
        self.assertEqual(ui.esc("a{b}\\c\nd"), "a(b)/c d")

    def test_listview_scrolls_and_wraps(self):
        v = ui.ListView(range(20), rows=5)
        for _ in range(7):
            v.handle("down")
        self.assertEqual((v.sel, v.first), (7, 3))
        v.handle("up"); v.handle("up"); v.handle("up")
        self.assertEqual(v.first, 3 if v.sel >= 3 else v.sel)
        v.sel, v.first = 0, 0
        v.handle("up")
        self.assertEqual((v.sel, v.first), (19, 15))
        v.handle("right")
        self.assertEqual(v.sel, 19)                            # a page past the end stops at the end
        self.assertFalse(ui.ListView([]).handle("down"))

    def test_list_rows_with_nothing_says_so(self):
        self.assertIn("Nothing here", "\n".join(ui.list_rows(ui.ListView([]), str)))

    def test_clock_formats(self):
        self.assertEqual(ui.clock(1_800_000_000_000, h24=True), time.strftime("%H:%M", time.localtime(1_800_000_000)))
        self.assertEqual(ui.mmss(3725), "1:02:05")

    def test_tile_grid_columns_follow_width(self):
        self.assertEqual(ui.grid_cols(8, 768), 4)
        self.assertEqual(ui.grid_cols(2, 768), 2)


class ImageTests(unittest.TestCase):
    def test_bmp_roundtrip_is_top_down_bgra(self):
        bmp = images.make_bmp(3, 2, lambda x, y: (x * 80, y * 100, 7))
        w, h, bgra = images.parse_bmp(bmp)
        self.assertEqual((w, h, len(bgra)), (3, 2, 24))
        self.assertEqual(bgra[0:4], bytes((7, 0, 0, 255)))                 # pixel (0,0): b, g, r, a
        self.assertEqual(bgra[12 + 8:12 + 12], bytes((7, 100, 160, 255)))  # pixel (2,1)

    def test_bad_bmps_are_refused(self):
        for bad in (b"", b"BM" + b"\0" * 10, b"PNG" + b"\0" * 80):
            with self.assertRaises(images.ImageError):
                images.parse_bmp(bad)
        with self.assertRaises(images.ImageError):
            images.parse_bmp(images.make_bmp(4, 4, lambda x, y: (0, 0, 0))[:-5])


class NetTests(unittest.TestCase):
    def test_private_files_are_0600(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "token.json")
            net.write_private(p, {"token": "x"})
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            self.assertEqual(net.read_json(p), {"token": "x"})
            self.assertEqual(net.read_json(p + ".nope", 5), 5)

    def test_errors_never_contain_the_url(self):
        with self.assertRaises(net.NetError) as cm:
            net.request("http://127.0.0.1:1/secret-token-123", timeout=1)
        self.assertNotIn("secret", str(cm.exception))


class ConfigTests(unittest.TestCase):
    def test_default_config_has_profiles_and_expands_cache_dir(self):
        cfg = config.load(profile="pi")
        self.assertEqual(cfg["profile"], "pi")
        self.assertTrue(os.path.isabs(cfg["cache_dir"]))
        self.assertIn("mpv", cfg["prof"])

    def test_unknown_profile_is_an_error(self):
        with self.assertRaises(SystemExit):
            config.load(profile="nope")


class ShellTests(unittest.TestCase):
    def test_commands_reach_the_app_and_the_picture_is_redrawn_only_on_change(self):
        class A(Shell):
            n = 0

            def handle(self, cmd):
                if cmd == "enter":
                    self.n += 1
                else:
                    super().handle(cmd)

            def draw(self, now):
                return f"count {self.n}"
        s = fake_shell(A)
        s.step(0)
        s.step(0)
        press(s, "enter")
        self.assertEqual(s.mpv.overlays[1], "count 1")
        sent = len([c for c in s.mpv.commands])
        s.step(0)
        self.assertEqual(len(s.mpv.commands), sent)
        press(s, "quit")
        self.assertTrue(s.stopping)

    def test_volume_keys_go_to_mpv(self):
        s = fake_shell(Shell)
        press(s, "volup", "mute")
        self.assertIn(("add", "volume", 5), s.mpv.commands)
        self.assertIn(("cycle", "mute"), s.mpv.commands)

    def test_an_error_in_the_app_does_not_stop_the_loop(self):
        class Bad(Shell):
            def handle(self, cmd):
                raise RuntimeError("boom")
        s = fake_shell(Bad)
        s.q.put(("cmd", "up"))
        self.assertTrue(s.step(0))

    def test_resize_event_changes_the_canvas_and_forces_a_redraw(self):
        s = fake_shell(Shell)
        s.step(0)
        old = ui.W
        try:
            s.q.put(("mpv", (s.mpv, {"event": "property-change", "name": "osd-width", "data": 1280})))
            s.q.put(("mpv", (s.mpv, {"event": "property-change", "name": "osd-height", "data": 720})))
            s.step(0); s.step(0)
            self.assertEqual(ui.W, 1024)
            self.assertEqual(s.window_scale(), (1280 / 1024, 720 / 576))
        finally:
            ui.set_canvas(old)


class ClockTests(unittest.TestCase):
    def test_faces(self):
        import datetime
        clock = load_app("clock")
        t = datetime.datetime(2026, 11, 5, 9, 5, 7)
        self.assertEqual(clock.face(t, True, False)[0], "09:05")
        self.assertEqual(clock.face(t, False, True)[0], "9:05:07")
        self.assertEqual(clock.face(t, True, False)[1], "Thursday 5 November 2026")

    def test_enter_switches_hours(self):
        s = fake_shell(load_app("clock").Clock)
        s.start()
        before = s.h24
        press(s, "enter")
        self.assertNotEqual(s.h24, before)


class InfoTests(unittest.TestCase):
    def test_throttle_flags(self):
        info = load_app("info")
        self.assertEqual(info.throttled_text("throttled=0x0"), [])
        self.assertEqual(info.throttled_text("throttled=0x50005"),
                         ["UNDER-VOLTAGE NOW", "THROTTLED", "under-voltage happened", "throttling happened"])
        self.assertEqual(info.throttled_text("garbage"), [])

    def test_formats(self):
        info = load_app("info")
        self.assertEqual(info.fmt_uptime(90061), "1 d 1 h 1 min")
        self.assertEqual(info.fmt_bytes(1536), "2 KB")
        self.assertEqual(info.meminfo("MemTotal: 2048 kB\nMemAvailable: 1024 kB")["MemAvailable"], 1024 * 1024)

    def test_gather_always_has_address_and_disk(self):
        labels = [r[0] for r in load_app("info").gather()]
        self.assertIn("ADDRESS", labels)
        self.assertIn("DISK", labels)


class PhotosTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        for name in ("b.jpg", "a.PNG", "notes.txt", ".hidden.jpg", "sub/c.webp", ".trash/d.jpg"):
            p = os.path.join(self.d.name, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()

    def tearDown(self):
        self.d.cleanup()

    def test_finds_images_only_skipping_hidden(self):
        found = [os.path.relpath(p, self.d.name) for p in load_app("photos").find_photos([self.d.name])]
        self.assertEqual(found, ["a.PNG", "b.jpg", "sub/c.webp"])

    def test_slideshow_steps_pauses_and_advances(self):
        photos = load_app("photos")
        s = fake_shell(photos.Photos, {"photos": {"dirs": [self.d.name], "seconds": 2, "shuffle": False}})
        s.start()
        self.assertEqual(len(s.mpv.played), 1)
        press(s, "right")
        self.assertEqual(len(s.mpv.played), 2)
        press(s, "left", "left")
        self.assertEqual(s.i, 1)                                  # wrapped round: 0 -> 1 -> ... three photos
        press(s, "enter")
        s.last = 0
        s.tick(time.time())
        self.assertEqual(len(s.mpv.played), 4)                    # paused: no automatic step
        s.last = time.time()
        press(s, "enter")                                         # playing again; that press's own tick did not advance yet
        self.assertEqual(len(s.mpv.played), 4)
        s.last = 0
        s.tick(time.time())
        self.assertEqual(len(s.mpv.played), 5)

    def test_empty_folder_says_so(self):
        photos = load_app("photos")
        s = fake_shell(photos.Photos, {"photos": {"dirs": [self.d.name + "/sub/nothing"]}})
        s.start()
        self.assertIn("No photos found", s.draw(0))


class NewsTests(unittest.TestCase):
    def test_ago(self):
        news = load_app("news")
        self.assertEqual(news.ago(1000, 1000 + 30), "just now")
        self.assertEqual(news.ago(1000, 1000 + 600), "10 min ago")
        self.assertEqual(news.ago(1000, 1000 + 7200), "2 h ago")
        self.assertEqual(news.ago(None, 5), "")

    def test_navigation_between_feeds_list_and_story(self):
        news = load_app("news")
        s = fake_shell(news.News, {"feeds": [{"name": "A", "url": "u"}, {"name": "B", "url": "v"}]})
        s.start = lambda: None                       # no network thread in a unit test
        s.feeds = [{"name": "A", "url": "u"}, {"name": "B", "url": "v"}]
        s.data = {0: [{"title": "T1", "summary": "S1", "link": "", "published": None}], 1: "error"}
        s.screen, s.feed_view, s.view, s.cur, s.story, s.scroll = "feeds", news.ui.ListView(range(2), 7), news.ui.ListView([], 7), 0, None, 0
        press(s, "enter")
        self.assertEqual(s.screen, "list")
        self.assertIn("T1", s.draw(time.time()))
        press(s, "enter")
        self.assertEqual(s.screen, "story")
        press(s, "back")
        press(s, "back")
        self.assertEqual(s.screen, "feeds")
        press(s, "down", "enter")
        self.assertIn("Could not load", s.draw(time.time()))
        press(s, "quit")
        self.assertTrue(s.stopping)


if __name__ == "__main__":
    unittest.main()
