"""Smoke checks that run anywhere without a library or an iPod.

    python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
# a throwaway settings file: nothing here may touch the real one
os.environ["MUSIC_UTILITY_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.json")

from gui import backend  # noqa: E402  (plain Python: no Qt imported)


class ToolArgs(unittest.TestCase):
    def test_tools_that_read_a_field(self):
        # _text was once shadowed by a string: every such tool failed
        self.assertEqual(backend.tool_steps("vibe", {"vibe": "rain", "count": "10"}, False),
                         [["vibe.py", "rain", "--count", "10"]])
        self.assertEqual(backend.tool_steps("disk", {"drive": "/Volumes/X"}, False),
                         [["ipod_sync.py", "--disk", "/Volumes/X"]])
        self.assertEqual(backend.tool_steps("disk", {"drive": "E:"}, False),
                         [["ipod_sync.py", "--disk", "E:"]])

    def test_empty_field_is_a_message_not_a_crash(self):
        with self.assertRaises(ValueError):
            backend.tool_steps("vibe", {}, False)
        with self.assertRaises(ValueError):
            backend.tool_steps("disk", {"drive": "nonsense"}, False)


class DiskMirror(unittest.TestCase):
    def test_missing_device_is_refused(self):
        import ipod_sync
        missing = os.path.join(tempfile.mkdtemp(), "not-mounted")
        with self.assertRaises(SystemExit) as e:
            ipod_sync.sync_disk(missing, False, "Music")
        self.assertIn("Drive not available", str(e.exception))
        self.assertFalse(os.path.exists(missing))


class Compilations(unittest.TestCase):
    def test_compilation_artist_comes_from_the_setting(self):
        import build_clean
        build_clean.VARIOUS = "Various Artists"
        for names in ({"Various Artists", "X"}, {"Разные исполнители", "X"}, {"Abba", "Queen"}):
            self.assertEqual(build_clean.resolve_conflict(names), ("Various Artists", "compilation"))


class MoveUndo(unittest.TestCase):
    def test_last_batch_is_put_back(self):
        import library
        import settings
        lib = tempfile.mkdtemp()
        album = os.path.join(lib, "Active", "A", "B")
        os.makedirs(album)
        open(os.path.join(album, "1.mp3"), "wb").close()
        real_paths = library.paths
        library.paths = lambda values=None: (lib, os.path.join(lib, "Active"), os.path.join(lib, "Archive"))
        library.settings = type("S", (), {"path": staticmethod(lambda k, v=None: lib)})
        try:
            a = {"path": album, "state": "A", "artist": "A", "album": "B"}
            library.begin_batch()
            library.move(a, "R")
            self.assertFalse(os.path.exists(album))
            undone, skipped = library.undo(apply=True)
            self.assertEqual((len(undone), len(skipped)), (1, 0))
            self.assertTrue(os.path.exists(os.path.join(album, "1.mp3")))
            self.assertEqual(library.undo(apply=True), ([], []))
        finally:
            library.settings, library.paths = settings, real_paths


class SmartPresets(unittest.TestCase):
    def test_presets_select_by_plays(self):
        import ipod  # noqa: F401  (puts podsync on the path)
        import smart_presets
        from podsync.itdb.writer.track import TrackRecord
        recs = [TrackRecord(title=str(i), location=":iPod_Control:Music:F00:A.mp3", db_track_id=i,
                            play_count=n, rating=r, date_added=i)
                for i, (n, r) in enumerate([(0, 0), (5, 100), (2, 40)], 1)]
        got = {p.name: p.track_ids for p in smart_presets.build(recs, {"Recently added"})}
        self.assertEqual(got["Never played"], [1])
        self.assertEqual(got["Most played"], [2, 3])
        self.assertEqual(got["Top rated"], [2])
        self.assertNotIn("Recently added", got)


DATA = os.path.join(ROOT, "tests", "data")
MP3_SILENCE = (b"\xff\xfb\x90\x64" + b"\x00" * 413) * 20     # half a second of MPEG frames


def sample(folder, kind, name="track"):
    """A fresh audio file to tag: 'mp3', 'aac' or 'alac'."""
    import shutil
    if kind == "mp3":
        path = os.path.join(folder, name + ".mp3")
        with open(path, "wb") as f:
            f.write(MP3_SILENCE)
    else:
        path = os.path.join(folder, name + ".m4a")
        shutil.copy(os.path.join(DATA, f"silence_{kind}.m4a"), path)
    return path


class TagsForEveryFormat(unittest.TestCase):
    """The library holds mp3 and m4a; every tool goes through tags.py for both."""

    FIELDS = {"artist": "Океан Ельзи", "albumartist": "Океан Ельзи", "album": "Земля",
              "title": "Така, як ти", "genre": "Rock", "grouping": "Post-punk", "year": "2013",
              "track": "3/12", "disc": "1/2"}

    def test_round_trip(self):
        import tags
        for kind, filetype, label in (("mp3", "mp3", "MPEG audio file"), ("aac", "m4a", "AAC audio file"),
                                      ("alac", "m4a", "Apple Lossless audio file")):
            with self.subTest(kind=kind):
                p = sample(tempfile.mkdtemp(), kind)
                tags.write(p, {**self.FIELDS, "year": "2013-05-01"})
                got = tags.read(p)
                self.assertEqual({k: got[k] for k in self.FIELDS}, self.FIELDS)
                self.assertEqual(got["artists"], ["Океан Ельзи"])
                info = tags.info(p)
                self.assertEqual((info["filetype"], info["kind"]), (filetype, label))
                self.assertFalse(got["art"])
                tags.set_cover(p, b"\xff\xd8\xff\xe0jpeg")
                self.assertEqual(tags.cover(p), b"\xff\xd8\xff\xe0jpeg")
                tags.write(p, {"grouping": "", "genre": None})
                got = tags.read(p)
                self.assertEqual((got["genre"], got["grouping"], got["title"]), ("", "", "Така, як ти"))
                tags.set_cover(p, None)
                self.assertFalse(tags.read(p)["art"])

    def test_lyrics_survive_tag_rewrites(self):
        import tags
        from mutagen.id3 import ID3, USLT
        from mutagen.mp4 import MP4
        p = sample(tempfile.mkdtemp(), "mp3")
        t = ID3()
        t.add(USLT(encoding=1, lang="eng", desc="", text="la la la"))
        tags.save_id3(t, p)
        tags.write(p, self.FIELDS, replace=True)
        self.assertEqual(ID3(p).getall("USLT")[0].text, "la la la")
        p = sample(tempfile.mkdtemp(), "aac")
        f = MP4(p)
        f.tags["\xa9lyr"] = ["la la la"]
        f.save()
        tags.write(p, self.FIELDS, replace=True)
        self.assertEqual(MP4(p).tags["\xa9lyr"], ["la la la"])

    def test_info_reports_bit_depth(self):
        import tags
        self.assertEqual(tags.info(sample(tempfile.mkdtemp(), "alac"))["bits"], 16)

    def test_ipod_stats_only_grow(self):
        import ipod
        cfg = {"reports_dir": tempfile.mkdtemp()}
        row = {"artist": "A", "title": "T", "album": "X", "play_count": 5, "rating": 80, "last_played": 100}
        ipod.save_stats(cfg, [row, {"artist": "B", "title": "U", "album": "Y"}])   # no activity: not kept
        self.assertEqual(ipod.save_stats(cfg, [{**row, "play_count": 0, "rating": 0, "last_played": 0,
                                                "skip_count": 1}]), 1)
        with open(ipod.stats_file(cfg), encoding="utf-8") as f:
            import json
            self.assertEqual(json.load(f)["A | T | X"],
                             {"play_count": 5, "skip_count": 1, "rating": 80, "last_played": 100})

    def test_sound_check_round_trip(self):
        import soundcheck
        import tags as audiotags
        self.assertEqual(soundcheck.soundcheck_from_lufs(-16.5), 1000)
        self.assertGreater(soundcheck.soundcheck_from_lufs(-10), 1000)       # louder: turn it down
        text = soundcheck.norm_string(1234)
        self.assertEqual(soundcheck.soundcheck_from_norm(text), 1234)
        for kind in ("mp3", "aac", "alac"):
            with tempfile.TemporaryDirectory() as d:
                path = sample(d, kind)
                self.assertEqual(audiotags.norm_tag(path), "")
                audiotags.set_norm_tag(path, text)
                audiotags.set_norm_tag(path, text)
                self.assertEqual(audiotags.norm_tag(path), text.strip())

    def test_mp3_stays_what_an_old_ipod_reads(self):
        import tags
        from mutagen.id3 import ID3
        p = sample(tempfile.mkdtemp(), "mp3")
        tags.write(p, self.FIELDS)
        t = ID3(p, translate=False)
        self.assertEqual(t.version[:2], (2, 3))
        self.assertTrue(all(t[k].encoding == 1 for k in ("TPE1", "TIT2", "TALB")))   # UTF-16
        self.assertNotIn("TDRC", t)

    def test_library_and_incoming_take_m4a(self):
        import library
        import tags
        root = tempfile.mkdtemp()
        album = os.path.join(root, "Active", "Band", "Record")
        os.makedirs(album)
        for n, kind in enumerate(("mp3", "aac", "alac"), 1):
            tags.write(sample(album, kind, f"{n:02d}"), {**self.FIELDS, "track": str(n)})
        with open(os.path.join(album, "cover.flac"), "wb"):
            pass                                                    # not audio the library takes
        albums = library.scan(root)
        self.assertEqual([(a["album"], a["tracks"], a["genre"]) for a in albums], [("Земля", 3, "Rock")])


class GenresFollowTheFile(unittest.TestCase):
    def test_tags_that_differ_from_the_genres_file_are_rewritten_once(self):
        import genres
        import tags
        root = tempfile.mkdtemp()
        album = os.path.join(root, "lib", "Active", "Band", "Record")
        os.makedirs(album)
        for n, kind in ((1, "mp3"), (2, "aac")):
            tags.write(sample(album, kind, str(n)), {"genre": "Pop"})
        with open(os.path.join(root, "genres.txt"), "w", encoding="utf-8") as f:
            f.write("Band/Record | Rock | Post-punk\n")
        cfg = {"library_dir": os.path.join(root, "lib"),
               "genres_file": os.path.join(root, "genres.txt")}
        self.assertEqual(genres.keep_tags_in_line(cfg, write=False), 1)   # dry run: counted only
        self.assertEqual(tags.read(os.path.join(album, "1.mp3"))["genre"], "Pop")
        self.assertEqual(genres.keep_tags_in_line(cfg), 1)
        for fn in ("1.mp3", "2.m4a"):                                      # both formats
            t = tags.read(os.path.join(album, fn))
            self.assertEqual((t["genre"], t["grouping"]), ("Rock", "Post-punk"))
        self.assertEqual(genres.keep_tags_in_line(cfg), 0)                # nothing left to do


class Translations(unittest.TestCase):
    """Every string goes through _(): a placeholder that doesn't match its .format() is a
    KeyError at run time, in a message nobody may see until it's needed."""

    @staticmethod
    def fields(s):
        import string
        return {f for _lit, f, _spec, _conv in string.Formatter().parse(s) if f}

    def test_placeholders_match_format_arguments(self):
        import ast
        import glob
        bad = []
        files = [os.path.join(ROOT, "Main.py")] + glob.glob(os.path.join(ROOT, "src", "**", "*.py"),
                                                            recursive=True)
        for path in files:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                fn = getattr(node, "func", None)
                if not (isinstance(fn, ast.Attribute) and fn.attr == "format"
                        and getattr(getattr(fn.value, "func", None), "id", None) in ("_", "n_")):
                    continue
                if any(k.arg is None for k in node.keywords):
                    continue                                   # .format(**meta)
                given = {k.arg for k in node.keywords}
                for a in fn.value.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                            and self.fields(a.value) != given:
                        bad.append(f"{os.path.relpath(path, ROOT)}:{node.lineno} {a.value[:50]!r}")
        self.assertEqual(bad, [])

    def test_english_without_a_catalog(self):
        import i18n
        self.assertEqual(i18n._("Sync the iPod"), "Sync the iPod")
        self.assertEqual(i18n.n_("{n} track", "{n} tracks", 2).format(n=2), "2 tracks")

    def test_json_catalog_and_plural_forms(self):
        import i18n
        c = i18n.Catalog({"_plural": i18n.PLURAL_RULES["uk"], "Save": "Зберегти", "Empty": "",
                          "{n} track": ["{n} трек", "{n} треки", "{n} треків"]})
        self.assertEqual(c.gettext("Save"), "Зберегти")
        self.assertEqual(c.gettext("Empty"), "Empty")          # not translated yet: English
        self.assertEqual([c.ngettext("{n} track", "{n} tracks", n).format(n=n) for n in (1, 3, 5, 21)],
                         ["1 трек", "3 треки", "5 треків", "21 трек"])

    def test_extract_finds_settings_and_plurals(self):
        import i18n
        found = i18n.extract()
        self.assertIn("Interface language", found)             # a Field label
        self.assertIn("Audio tools", found)                    # a Field section
        self.assertEqual(found.get("{n} track"), "{n} tracks")  # plural() / n_()


if __name__ == "__main__":
    unittest.main()
