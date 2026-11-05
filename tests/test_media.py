import os
import tempfile
import time
import unittest

import support  # noqa: F401
from support import FakeMpv, fake_shell, load_app, press
from tvkit import player

media = load_app("media")


def prop(name, data):
    return {"event": "property-change", "name": name, "data": data}


class PositionsTests(unittest.TestCase):
    def test_saves_resumes_and_marks_done(self):
        with tempfile.TemporaryDirectory() as d:
            p = player.Positions(d)
            self.assertIsNone(p.resume_at("a"))
            p.update("a", 12, 100)
            self.assertIsNone(p.resume_at("a"))                  # too near the start to bother
            p.update("a", 400, 1000)
            p.save()
            self.assertEqual(player.Positions(d).resume_at("a"), 400)     # survives a restart
            p.update("a", 960, 1000)
            self.assertIsNone(p.resume_at("a"))                  # watched to (nearly) the end
            self.assertTrue(p.get("a")["done"])

    def test_old_entries_are_forgotten(self):
        with tempfile.TemporaryDirectory() as d:
            p = player.Positions(d)
            for i in range(player.MAX_ENTRIES + 20):
                p.update(f"k{i}", 100, 1000)
                p.data[f"k{i}"]["t"] = i
            p.save()
            self.assertEqual(len(p.data), player.MAX_ENTRIES)
            self.assertNotIn("k0", p.data)

    def test_corrupt_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "positions.json"), "w") as f:
                f.write("{nope")
            self.assertEqual(player.Positions(d).data, {})


class PlaybackTests(unittest.TestCase):
    def make(self, d):
        self.mpv = FakeMpv()
        self.pos = player.Positions(d)
        return player.Playback(self.mpv, self.pos)

    def test_observes_and_starts_at_the_saved_position(self):
        with tempfile.TemporaryDirectory() as d:
            pb = self.make(d)
            self.assertEqual([c[2] for c in self.mpv.commands if c[0] == "observe_property"], ["time-pos", "duration", "pause"])
            self.pos.update("f", 300, 1000)
            starts = []
            self.mpv.play = lambda url, start=0, **kw: starts.append((url, start))
            pb.start("file:///f", "F", key="f")
            self.assertEqual(starts, [("file:///f", 300)])
            self.assertIn("RESUMED AT 5:00", "\n".join(pb.osd(time.time())))
            pb.start("file:///g", "G", key="g")
            self.assertEqual(starts[-1], ("file:///g", 0))

    def test_events_update_state_and_eof_ends(self):
        with tempfile.TemporaryDirectory() as d:
            pb = self.make(d)
            pb.start("u", "T", key="k")
            pb.on_mpv(prop("time-pos", 50.0))
            pb.on_mpv(prop("duration", 100.0))
            pb.on_mpv(prop("pause", True))
            self.assertEqual((pb.pos, pb.dur, pb.paused), (50.0, 100.0, True))
            self.assertIn("PAUSED", "\n".join(pb.osd(time.time() + 99)))      # a paused picture keeps its bar
            self.assertIsNone(pb.on_mpv({"event": "end-file", "reason": "stop"}))      # we replaced the file ourselves
            self.assertTrue(pb.active)
            self.assertEqual(pb.on_mpv({"event": "end-file", "reason": "eof"}), "ended")
            self.assertTrue(self.pos.get("k")["done"])
            self.assertFalse(pb.active)

    def test_error_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            pb = self.make(d)
            pb.start("u", "T")
            self.assertEqual(pb.on_mpv({"event": "end-file", "reason": "error"}), "error")

    def test_keys(self):
        with tempfile.TemporaryDirectory() as d:
            pb = self.make(d)
            self.assertFalse(pb.handle("right"))                 # nothing playing: not ours
            pb.start("u", "T")
            for cmd, expect in (("right", ("seek", 10, "relative")), ("left", ("seek", -10, "relative")), ("up", ("seek", 60, "relative")),
                                ("down", ("seek", -60, "relative")), ("enter", ("cycle", "pause")), ("captions", ("cycle", "sub")),
                                ("menu", ("cycle", "audio"))):
                self.assertTrue(pb.handle(cmd), cmd)
                self.assertIn(expect, self.mpv.commands)
            self.assertFalse(pb.handle("quit"))

    def test_position_is_saved_on_stop_and_periodically(self):
        with tempfile.TemporaryDirectory() as d:
            pb = self.make(d)
            pb.start("u", "T", key="k")
            pb.on_mpv(prop("time-pos", 200.0))
            pb.on_mpv(prop("duration", 1000.0))
            pb.last_save = 0
            pb.tick(time.time())
            self.assertEqual(player.Positions(d).resume_at("k"), 200)
            pb.on_mpv(prop("time-pos", 250.0))
            pb.stop()
            self.assertEqual(player.Positions(d).resume_at("k"), 250)
            self.assertIn(("stop",), self.mpv.commands)

    def test_audio_card_is_permanent(self):
        with tempfile.TemporaryDirectory() as d:
            pb = self.make(d)
            pb.start("u", "Some Tune", audio_only=True)
            out = "\n".join(pb.osd(time.time() + 999))
            self.assertIn("NOW PLAYING", out)
            self.assertIn("Some Tune", out)

    def test_rebind_after_an_mpv_restart(self):
        with tempfile.TemporaryDirectory() as d:
            pb = self.make(d)
            pb.start("u", "T")
            new = FakeMpv()
            pb.rebind(new)
            self.assertFalse(pb.active)
            self.assertEqual(len([c for c in new.commands if c[0] == "observe_property"]), 3)


class HelperTests(unittest.TestCase):
    def test_natural_sort_and_media_types(self):
        names = ["Episode 10.mkv", "episode 2.mkv", "Episode 1.mkv"]
        self.assertEqual(sorted(names, key=media.natural_key), ["Episode 1.mkv", "episode 2.mkv", "Episode 10.mkv"])
        self.assertTrue(media.is_media("a.MKV") and media.is_media("b.flac"))
        self.assertFalse(media.is_media("notes.txt"))
        self.assertTrue(media.is_audio("x.mp3") and not media.is_audio("x.mp4"))

    def test_title_and_size(self):
        self.assertEqual(media.title_of("My_Film.2019.mkv"), "My Film 2019")
        self.assertEqual(media.fmt_size(2_700_000), "2.6 MB")


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = self.t.name
        for f in ("Show/Episode 2.mp4", "Show/Episode 10.mp4", "Show/Episode 1.mp4", "Show/cover.jpg", "Film.mkv", ".hidden.mkv", "notes.txt", "Music/a.mp3"):
            p = os.path.join(self.root, f)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("x")

    def tearDown(self):
        self.t.cleanup()

    def app(self):
        s = fake_shell(media.Media, {"cache_dir": os.path.join(self.root, ".cache"), "media": {"roots": [self.root]}})
        s.start()
        return s

    def test_list_dir_folders_first_files_sorted_and_filtered(self):
        got = [e["name"] for e in media.list_dir(self.root)]
        self.assertEqual(got, ["Music", "Show", "Film.mkv"])
        self.assertEqual([e["name"] for e in media.list_dir(self.root + "/Show")], ["Episode 1.mp4", "Episode 2.mp4", "Episode 10.mp4"])
        self.assertEqual(media.list_dir(self.root + "/nope"), [])

    def test_roots(self):
        self.assertEqual(media.find_roots([self.root, "/definitely/not/here"]), [self.root])
        self.assertEqual(media.find_roots([self.root, self.root]), [self.root])

    def test_browse_play_next_and_back(self):
        s = self.app()
        self.assertEqual([e["name"] for e in s.view.items], [os.path.basename(self.root)])      # the list of roots
        press(s, "enter")
        self.assertEqual(s.folder, self.root)
        press(s, "down", "enter")                                                              # Show/
        self.assertEqual(s.folder, self.root + "/Show")
        press(s, "enter")                                                                      # play Episode 1
        self.assertTrue(s.pb.active)
        self.assertTrue(s.mpv.played[-1].endswith("Episode 1.mp4"))
        s.on_mpv({"event": "end-file", "reason": "eof"})                                       # autoplay the next file
        self.assertTrue(s.mpv.played[-1].endswith("Episode 2.mp4"))
        press(s, "right")                                                                      # a playback key, not a list key
        self.assertIn(("seek", 10, "relative"), s.mpv.commands)
        press(s, "back")                                                                       # stops, back in the list on that file
        self.assertFalse(s.pb.active)
        self.assertEqual(s.view.current()["name"], "Episode 2.mp4")
        press(s, "back")
        self.assertEqual(s.folder, self.root)
        self.assertEqual(s.view.current()["name"], "Show")                                     # the folder we came from is selected
        press(s, "back")
        self.assertIsNone(s.folder)
        press(s, "back")
        self.assertTrue(s.stopping)

    def test_last_file_returns_to_the_list(self):
        s = self.app()
        press(s, "enter", "down", "enter", "down", "down", "enter")                            # Episode 10, the last one
        self.assertTrue(s.mpv.played[-1].endswith("Episode 10.mp4"))
        s.on_mpv({"event": "end-file", "reason": "eof"})
        self.assertFalse(s.pb.active)
        self.assertEqual(s.view.current()["name"], "Episode 10.mp4")

    def test_unplayable_file_says_so(self):
        s = self.app()
        press(s, "enter", "down", "down")
        press(s, "enter")
        s.on_mpv({"event": "end-file", "reason": "error"})
        self.assertIn("Could not play Film.mkv", s.draw(time.time()))

    def test_remembers_the_last_folder(self):
        s = self.app()
        press(s, "enter", "down", "enter")
        s2 = self.app()
        self.assertEqual(s2.folder, self.root + "/Show")

    def test_cannot_escape_the_roots(self):
        s = self.app()
        s.open("/etc")
        self.assertIsNone(s.folder)

    def test_empty_roots_say_so(self):
        s = fake_shell(media.Media, {"cache_dir": self.root + "/.c", "media": {"roots": ["/definitely/not/here"]}})
        s.start()
        self.assertIn("No USB stick", s.draw(time.time()))

    def test_resume_marker_in_the_list(self):
        s = self.app()
        press(s, "enter", "down", "down")
        film = os.path.join(self.root, "Film.mkv")
        s.positions.update(film, 125, 1000)
        self.assertIn("RESUME 2:05", s.detail({"name": "Film.mkv", "path": film, "dir": False, "size": 1}))
        s.positions.update(film, 990, 1000)
        self.assertIn("WATCHED", s.detail({"name": "Film.mkv", "path": film, "dir": False, "size": 1}))


if __name__ == "__main__":
    unittest.main()
