import json
import os
import struct
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


class PngTests(unittest.TestCase):
    def test_every_filter_type_and_colour_type_decodes_to_the_same_pixels(self):
        px = lambda x, y: ((x * 40 + y * 7) % 256, (y * 60) % 256, (x * y * 13) % 256, (x * 50 + 30) % 256)
        for f in range(5):
            w, h, bgra = images.parse_png(images.make_png(5, 4, px, filter_type=f))
            self.assertEqual((w, h, len(bgra)), (5, 4, 80), f)
            r, g, b, _ = px(3, 2)
            self.assertEqual(bgra[(2 * 5 + 3) * 4:(2 * 5 + 3) * 4 + 4], bytes((b, g, r, 255)), f"filter {f}")
        w, h, bgra = images.parse_png(images.make_png(3, 2, px, ctype=6, filter_type=4))
        r, g, b, a = px(1, 1)
        self.assertEqual(bgra[(1 * 3 + 1) * 4:(1 * 3 + 1) * 4 + 4], bytes((b, g, r, a)))
        w, h, bgra = images.parse_png(images.make_png(3, 2, lambda x, y: (x * 90, 0, 0), ctype=0))
        self.assertEqual(bgra[8:12], bytes((180, 180, 180, 255)))

    def test_parse_image_chooses_by_content_and_refuses_jpeg_and_damage(self):
        self.assertEqual(images.parse_image(images.make_png(2, 2, lambda x, y: (1, 2, 3)))[:2], (2, 2))
        self.assertEqual(images.parse_image(images.make_bmp(2, 2, lambda x, y: (1, 2, 3)))[:2], (2, 2))
        for bad in (b"\xff\xd8\xff\xe0JFIF", b"", b"\x89PNG\r\n\x1a\n", images.make_png(4, 4, lambda x, y: (0, 0, 0))[:-30]):
            with self.assertRaises(images.ImageError):
                images.parse_image(bad)

    def test_a_png_bomb_is_refused(self):
        big = images.make_png(2, 2, lambda x, y: (0, 0, 0)).replace(struct.pack(">I", 2) + struct.pack(">I", 2), struct.pack(">I", 99999) + struct.pack(">I", 2), 1)
        with self.assertRaises(images.ImageError):
            images.parse_png(big)


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

    def test_auto_profile_follows_the_machine(self):
        import unittest.mock as mock
        with mock.patch.object(config.sys, "platform", "darwin"):
            self.assertEqual(config.auto_profile(), "mac")
        with mock.patch.object(config.sys, "platform", "linux"), mock.patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            self.assertEqual(config.auto_profile(), "desktop")
        with mock.patch.object(config.sys, "platform", "linux"), mock.patch.dict(os.environ, {}, clear=True):
            self.assertIn(config.auto_profile(), ("pi", "desktop"))

    def test_default_profile_is_automatic_and_a_name_still_wins(self):
        self.assertIn(config.load()["profile"], ("mac", "desktop", "pi"))
        self.assertEqual(config.load(profile="hdmi")["profile"], "hdmi")

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

    def test_closing_the_window_quits_but_a_crash_restarts(self):
        s = fake_shell(Shell)
        s.revive = lambda: True                                   # stands in for starting a new mpv
        s.q.put(("mpv", (s.mpv, {"event": "mpv-exited", "code": 0})))
        self.assertFalse(s.step(0))
        self.assertEqual(s.exit_code, 0)
        s2 = fake_shell(Shell)
        s2.revive = lambda: True
        s2.q.put(("mpv", (s2.mpv, {"event": "mpv-exited", "code": 11})))
        self.assertTrue(s2.step(0))                               # a segfault: carry on with a new mpv
        self.assertFalse(s2.stopping)
        s3 = fake_shell(Shell)
        s3.revive = lambda: False                                 # restarting keeps failing: give up with an error code
        s3.q.put(("mpv", (s3.mpv, {"event": "mpv-exited", "code": 11})))
        self.assertFalse(s3.step(0))
        self.assertEqual(s3.exit_code, 1)

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


class GridKeyboardTests(unittest.TestCase):
    def test_grid_moves_and_scrolls_a_row_at_a_time(self):
        g = ui.GridView(range(11), 4, 2)                        # rows: 0-3, 4-7, 8-10
        g.handle("down"); g.handle("down")
        self.assertEqual((g.sel, g.first), (8, 4))              # the page follows the selection by a row
        g.handle("right"); g.handle("right")
        self.assertEqual(g.sel, 10)
        g.handle("up")
        self.assertEqual(g.sel, 6)
        g.sel = 6
        g.handle("down")
        self.assertEqual(g.sel, 10)                             # short last row: its last item
        g.handle("down")
        self.assertEqual(g.sel, 10)
        self.assertFalse(ui.GridView([]).handle("down"))
        self.assertTrue(list(ui.GridView(range(3), 4, 2).visible()) == [0, 1, 2])

    def test_keyboard_typing_actions_and_submit(self):
        k = ui.Keyboard()
        k.handle("enter"); k.handle("right"); k.handle("enter")            # a, b
        self.assertEqual(k.text, "ab")
        k.handle("char:z"); k.handle("char:space"); k.handle("char:q")
        self.assertEqual(k.text, "abz q")
        k.handle("backspace")
        self.assertEqual(k.text, "abz ")
        k.r, k.c = len(k.rows), 1                                      # DELETE
        k.handle("enter")
        self.assertEqual(k.text, "abz")
        k.c = 2                                                              # CLEAR
        k.handle("enter")
        self.assertEqual(k.text, "")
        k.c = 3                                                              # SEARCH
        self.assertEqual(k.handle("enter"), "submit")
        self.assertIsNone(k.handle("volup"))
        self.assertIn("SEARCH", "\n".join(k.draw()))

    def test_keyboard_cursor_stays_inside_the_short_action_row(self):
        k = ui.Keyboard()
        k.c = 9
        k.handle("down"); k.handle("down"); k.handle("down"); k.handle("down")
        self.assertEqual((k.r, k.c), (4, 3))

    def test_full_keyboard_has_shift_symbols_and_hides_secrets(self):
        k = ui.Keyboard(full=True, secret=True, submit_label="LOGIN")
        k.handle("enter")
        k.r, k.c = len(k.rows), 1                                           # SHIFT
        k.handle("enter")
        k.r, k.c = 0, 0
        k.handle("enter")
        k.r, k.c = 4, 0
        k.handle("enter")
        self.assertEqual(k.text, "aA!")
        out = "\n".join(k.draw())
        self.assertNotIn("aA!", out)
        self.assertIn("***_", out)
        self.assertIn("LOGIN", out)
        k.r, k.c = len(k.rows), 4
        self.assertEqual(k.handle("enter"), "submit")

    def test_colours(self):
        self.assertEqual(ui.ass_colour("#1b2c3d"), "&H3D2C1B&")
        self.assertEqual(ui.ass_colour(None), ui.AMBER)
        self.assertEqual(ui.ass_colour("#zzzzzz"), ui.AMBER)

    def test_list_rows_can_sit_beside_something(self):
        v = ui.ListView(["a", "b"], 7)
        self.assertIn("\\pos(254,", "\n".join(ui.list_rows(v, str, x0=250)))

    def test_later_runs_on_the_loop_thread(self):
        s = fake_shell(Shell)
        seen = []
        import threading
        threading.Thread(target=lambda: s.later(lambda: seen.append(threading.current_thread().name))).start()
        for _ in range(3):
            s.step(0.05)
        self.assertEqual(seen, [threading.current_thread().name])


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
