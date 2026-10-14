"""Smoke checks that run anywhere without a library or an iPod.

    python -m unittest discover tests
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
# a throwaway settings file: nothing here may touch the real one
os.environ["MUSIC_UTILITY_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.json")

from gui import backend  # noqa: E402  (plain Python: no Qt imported)
import settings  # noqa: E402
settings.DATA = tempfile.mkdtemp()      # the library index and caches too: never the checkout's


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


class EveryToolRuns(unittest.TestCase):
    def test_every_tool_names_a_script_that_exists(self):
        # a tool without one raised KeyError in the window's click handler, and PyQt aborts on that
        # a real folder too: 'Add new tracks' refused an empty one and so hid its missing script
        here = {"folder": ROOT, "path": __file__, "file": __file__}
        for tool in backend.TOOLS:
            for applying in (False, True):
                try:
                    steps = backend.tool_steps(tool, here, applying)
                except (ValueError, SystemExit):
                    continue                    # needs parameters / has nothing to apply: refused properly
                for step in steps:
                    self.assertTrue(os.path.isfile(os.path.join(backend.SRC, step[0])), (tool, step))


class IncomingNames(unittest.TestCase):
    def test_glued_artists_are_split_and_listed_names_stay_whole(self):
        import add_incoming
        from musiclib import norm
        protect = {norm(n): n for n in add_incoming.DEFAULT_PROTECT}
        split = add_incoming.split_artists
        # a glued name once in the library used to protect itself and stay one artist
        self.assertEqual(split("санкен, левко", protect), ["санкен", "левко"])
        self.assertEqual(split("A feat. B, C", protect), ["A", "B", "C"])
        self.assertEqual(split("125, Rue Montmartre", protect), ["125, Rue Montmartre"])
        self.assertEqual(split("ac/dc", protect), ["AC/DC"])
        self.assertEqual(add_incoming.with_feats("330", ["#keyoo"]), "330 (feat. #keyoo)")
        # one (feat. …) group: new names join it, two groups become one
        self.assertEqual(add_incoming.with_feats("Lemonade (feat. NAV)", ["Gunna", "NAV"]),
                         "Lemonade (feat. NAV, Gunna)")
        self.assertEqual(add_incoming.with_feats(
            "Hole In The Sun (feat. COS & Conway) [From Cyberpunk 2077] (feat. Raney Shockne)", []),
            "Hole In The Sun (feat. COS & Conway, Raney Shockne) [From Cyberpunk 2077]")

    def test_soundcloud_titles(self):
        import soundcloud
        soundcloud.selfcheck()


class ProgressLine(unittest.TestCase):
    def test_progress_draws_by_the_clock(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            p = settings.Progress("read: {n}/{total}", total=3, every=100)
            for _i in range(3):
                p.tick()                       # the first one is due at once (never drawn before)
            p.end()
        self.assertIn("read: 1/3", out.getvalue())
        self.assertIn("read: 3/3", out.getvalue())


class SettingsValues(unittest.TestCase):
    def test_zero_means_not_set_only_where_the_default_is_zero(self):
        import settings
        self.assertEqual(settings.validate("ipod_capacity_gb", "0"), (0, None))
        self.assertIsNotNone(settings.validate("ipod_backups", "0")[1])


class SettingsPlace(unittest.TestCase):
    def test_installed_copy_keeps_settings_with_the_user_and_a_checkout_keeps_its_own(self):
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        installed, home = os.path.realpath(tempfile.mkdtemp()), tempfile.mkdtemp()
        for name in ("src", "locale"):
            shutil.copytree(os.path.join(root, name), os.path.join(installed, name))
        env = {k: v for k, v in os.environ.items()
               if k not in ("MUSIC_UTILITY_SETTINGS", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}
        env.update(HOME=home, USERPROFILE=home, APPDATA=os.path.join(home, "AppData"))

        def where(folder):
            out = subprocess.run([sys.executable, "-c", "import settings; print(settings.FILE); print(settings.DATA)"],
                                 cwd=os.path.join(folder, "src"), env=env, capture_output=True, text=True, check=True)
            return out.stdout.split("\n")[:2]

        file, data = where(installed)
        self.assertTrue(file.startswith(home) and data.startswith(home), (file, data))
        open(os.path.join(installed, "settings.json"), "w").close()   # a portable copy: settings beside it
        self.assertEqual(where(installed), [os.path.join(installed, "settings.json"), installed])


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


class PlaylistsAndRatings(unittest.TestCase):
    def test_playlist_files(self):
        import playlists
        cfg = {"reports_dir": tempfile.mkdtemp()}
        p = playlists.new(cfg, "Night: drive", ["/a.mp3"])
        self.assertTrue(p.endswith("Night_ drive.m3u8"))             # no ':' in a file name
        playlists.add(p, ["/a.mp3", "/b.mp3"])
        self.assertEqual(playlists.read(p), ["/a.mp3", "/b.mp3"])    # no doubles
        self.assertTrue(playlists.new(cfg, "Night: drive").endswith("Night_ drive 2.m3u8"))
        playlists.keep_on_ipod(cfg, "Night_ drive", True)
        p2 = playlists.rename(cfg, p, "Drive")
        self.assertEqual(playlists.lists(cfg)["keep"], ["Drive"])     # the mark goes with the name
        self.assertEqual([n for n, _paths in playlists.kept(cfg)], ["Drive"])
        self.assertTrue(os.path.isfile(p2))

    def test_ratings_go_where_they_were_changed(self):
        import ipod_sync
        import library
        import tags
        import trackstats
        root = tempfile.mkdtemp()
        album = os.path.join(root, "Active", "Band", "Record")
        os.makedirs(album)
        files = []
        for n, title in enumerate(("One", "Two", "Three", "Four"), 1):
            f = sample(album, "mp3", f"0{n}")
            tags.write(f, {"artist": "Band", "albumartist": "Band", "album": "Record", "title": title})
            files.append(f)
        cfg = {"reports_dir": tempfile.mkdtemp()}
        listing = library.tracks(root)
        ipod_sync.LIBRARY = root
        k = lambda t: trackstats.key("Band", t, "Record")
        trackstats.save_ratings(cfg, {
            k("One"): {"rating": 80, "synced": 40},      # changed in PodVault -> written
            k("Two"): {"rating": 40, "synced": 40},      # changed on the iPod -> taken
            k("Three"): {"rating": 60, "synced": 60},    # unchanged
        })
        on_ipod = [{"rating": 40}, {"rating": 100}, {"rating": 60}, {"rating": 20}]
        pairs = {n: f for n, f in enumerate(files)}
        push, pull = ipod_sync.rating_plan(pairs, on_ipod, listing, cfg)
        self.assertEqual(push, {0: 80})
        self.assertEqual(pull, {k("Two"): 100, k("Four"): 20})          # Four: rated only on the iPod


class KeptPlaylists(unittest.TestCase):
    def test_a_kept_playlist_keeps_its_archive_track_on_the_ipod(self):
        import ipod_sync
        import library
        import playlists
        import tags
        root = tempfile.mkdtemp()
        active = os.path.join(root, "Active", "Band", "Live")
        archive = os.path.join(root, "Archive", "Band", "Old")
        os.makedirs(active)
        os.makedirs(archive)
        a = sample(active, "mp3", "01")
        tags.write(a, {"artist": "Band", "albumartist": "Band", "album": "Live", "title": "New"})
        r = sample(archive, "mp3", "01")
        tags.write(r, {"artist": "Band", "albumartist": "Band", "album": "Old", "title": "Old song"})
        cfg = {"reports_dir": tempfile.mkdtemp(), "library_dir": root, "duration_tolerance": 3}
        playlists.write(playlists.path_of(cfg, "Mix"), [r, a])
        playlists.keep_on_ipod(cfg, "Mix", True)
        playlists.save_lists(cfg, {"keep": ["Mix"], "managed": ["Mix", "Gone"]})
        db = {"tracks": [{"artist": "Band", "title": "New", "album": "Live", "length": 0, "track_id": 1},
                         {"artist": "Band", "title": "Old song", "album": "Old", "length": 0, "track_id": 2}],
              "dataset2_standard_playlists": [{"title": "Mix", "items": [{"track_id": 1}]},
                                              {"title": "Gone", "items": []}]}
        ipod_sync.LIBRARY = root
        real = (settings.require, ipod_sync.ipod.covered, ipod_sync.has_cover)
        settings.require = lambda: cfg
        ipod_sync.ipod.covered = lambda path, tracks: set()
        ipod_sync.has_cover = lambda p: False
        try:
            plan = ipod_sync.make_plan(type("Dev", (), {"path": root})(), db, listing=library.tracks(root))
        finally:
            settings.require, ipod_sync.ipod.covered, ipod_sync.has_cover = real
        self.assertEqual(plan["archive"], [])                   # the Archive track stays: it's in Mix
        self.assertEqual(sorted(plan["pairs"]), [0, 1])
        self.assertEqual(plan["keep_changed"], ["Mix"])         # the iPod's Mix lacks a track
        self.assertEqual(plan["drop"], ["Gone"])                # no longer kept: taken off
        self.assertTrue(ipod_sync.has_changes(plan))


class ScannedFlag(unittest.TestCase):
    def test_scanned_skips_the_drive_only_after_a_scan(self):
        import library
        import tags
        root = tempfile.mkdtemp()
        album = os.path.join(root, "Active", "Band", "Record")
        os.makedirs(album)
        tags.write(sample(album, "mp3", "01"), {"artist": "Band", "albumartist": "Band",
                                                 "album": "Record", "title": "One"})
        self.assertFalse(library.just_scanned(root))       # nothing read yet: --scanned is ignored
        calls = []
        real = library.scan
        library.scan = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            self.assertEqual(len(library.listing(root, scanned=True)), 1)
            self.assertEqual(len(calls), 1)                  # it read the drive after all
            self.assertTrue(library.just_scanned(root))
            self.assertEqual(len(library.listing(root, scanned=True)), 1)
            self.assertEqual(len(calls), 1)                  # now it trusted the scan just made
        finally:
            library.scan = real
        # a tag written into the library: the next --scanned has to look again
        real_paths = library.paths
        library.paths = lambda values=None: (root, os.path.join(root, "Active"), os.path.join(root, "Archive"))
        try:
            library.forget({os.path.join(album, "01.mp3")})
        finally:
            library.paths = real_paths
        self.assertFalse(library.just_scanned(root))


class IpodRetag(unittest.TestCase):
    def test_renamed_track_is_updated_in_place_and_keeps_its_plays(self):
        import ipod_sync
        import library
        import tags
        root = tempfile.mkdtemp()
        album = os.path.join(root, "Active", "ooes", "мне не страшно")
        os.makedirs(album)
        p = sample(album, "mp3", "01")
        tags.write(p, {"artist": "ooes", "albumartist": "ooes", "album": "мне не страшно",
                       "title": "слизывай (feat. Apex)", "track": "2/8", "year": "2023"})
        ipod_sync.LIBRARY = root
        listing = library.tracks(root)
        on_ipod = [{"artist": "ooes, Apex", "album_artist": "ooes, Apex", "album": "мне не страшно",
                    "title": "слизывай", "play_count": 41, "sort_artist": "ooes, Apex"}]
        pairs, _missing = ipod_sync.match_device(
            {p: (ipod_sync.device_keys("ooes", "слизывай (feat. Apex)"), None)},
            [(ipod_sync.device_keys("ooes, Apex", "слизывай"), None)])
        self.assertEqual(pairs, {0: p})          # the old copy on the iPod is recognised
        d = ipod_sync.retag_plan(pairs, on_ipod, listing)[0]
        self.assertEqual((d["artist"], d["album_artist"], d["title"], d["track_number"]),
                         ("ooes", "ooes", "слизывай (feat. Apex)", 2))
        self.assertIsNone(d["sort_artist"])
        self.assertNotIn("play_count", d)


class SinglesMode(unittest.TestCase):
    def test_own_album_by_default_merged_on_request(self):
        import add_incoming
        def single(title):
            return {"album": None, "artists": ["Band"], "aa_raw": "Band", "title": title, "genre": None,
                    "src": "x.m4a", "track": None, "track_total": None}
        lib = {("band", "hit"): {"intro"}, ("band", "singles"): {"old one"}}
        spell = lambda n: n
        own, _n = add_incoming.build_plan([single("Song (feat. X)"), single("Hit")], {}, spell, "/L", lib)
        self.assertEqual(sorted((p["album"], p["track"]) for p in own),
                         [("Hit - Single", 1), ("Song", 1)])     # 'Hit' is a real album of theirs
        merged, _n = add_incoming.build_plan([single("Song"), single("Hit")], {}, spell, "/L", lib, merged=True)
        self.assertEqual(sorted((p["album"], p["track"], p["track_total"]) for p in merged),
                         [("Singles", 2, 3), ("Singles", 3, 3)])  # numbered on after the one there

    def test_settings_from_before_keep_the_old_way(self):
        import json
        with open(settings.FILE, "w", encoding="utf-8") as f:
            json.dump({"library_dir": "/L"}, f)
        self.assertEqual(settings.load()["singles"], "merged")
        settings.save({**settings.load(), "singles": "own"})
        self.assertEqual(settings.load()["singles"], "own")
        os.remove(settings.FILE)


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

    def test_index_skips_unchanged_and_moved_albums(self):
        import library
        import tags
        root = tempfile.mkdtemp()
        album = os.path.join(root, "Active", "Band", "Record")
        os.makedirs(album)
        tags.write(sample(album, "mp3", "01"), {**self.FIELDS, "track": "1"})
        first = library.scan(root)
        self.assertEqual(library.cached(root), first)           # what the window shows at once
        read, library._read_album = library._read_album, None    # any read now fails
        try:
            self.assertEqual(library.scan(root), first)
            moved = os.path.join(root, "Archive", "Band", "Record")
            shutil.move(album, moved)
            self.assertEqual([(a["state"], a["album"]) for a in library.scan(root)], [("R", "Земля")])
        finally:
            library._read_album = read
        f = os.path.join(moved, os.listdir(moved)[0])
        tags.write(f, {"genre": "Jazz"})
        os.utime(f, (1, 1))                                       # a changed file is read again
        self.assertEqual(library.scan(root)[0]["genre"], "Jazz")


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


class GenresSuggestCarriesOn(unittest.TestCase):
    def test_a_stopped_run_keeps_its_answers_and_the_next_one_carries_on(self):
        import ai
        import genres
        import tags
        root = tempfile.mkdtemp()
        for n in range(5):
            album = os.path.join(root, "lib", "Active", f"Band{n}", "Record")
            os.makedirs(album)
            tags.write(sample(album, "mp3", "1"), {"artist": f"Band{n}", "album": "Record"})
        cfg = {"library_dir": os.path.join(root, "lib")}
        path = os.path.join(root, "genres.txt")
        asked = []

        def fake(cfg, system, user, schema, backend=None):
            rows = user.splitlines()[1:]
            asked.append([r.split(" | ")[1] for r in rows])
            if len(asked) == 2:
                raise RuntimeError("limit hit")
            return {"albums": [{"id": i, "genre": "Rock", "style": "Indie"} for i in range(len(rows))]}

        old = genres.BATCH, ai.ask_json, ai.pick_backend
        genres.BATCH, ai.ask_json, ai.pick_backend = 2, fake, lambda cfg, b=None: "api"
        try:
            with self.assertRaises(SystemExit):
                genres.suggest(cfg, path, None)                       # batch 2 fails
            self.assertEqual(sorted(genres.read_file(path)), ["Band0/Record", "Band1/Record"])
            genres.suggest(cfg, path, None)                           # carries on from Band2
        finally:
            genres.BATCH, ai.ask_json, ai.pick_backend = old
        self.assertEqual(asked, [["Band0", "Band1"], ["Band2", "Band3"], ["Band2", "Band3"], ["Band4"]])
        self.assertEqual(len(genres.read_file(path)), 5)


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


class IdentityCrossesProcesses(unittest.TestCase):
    def test_the_administrator_process_answer_reaches_the_window_intact(self):
        import ipod
        info = {"SerialNumber": "2Z7253DUV9M", "FamilyID": 6, "vpd_raw_xml": b"<plist><dict/></plist>",
                "_usb_vendor_info": {"not": "json"}}
        back = ipod.parse_identity("asking...\r" + ipod.identity_line(info) + "\rdone")   # osascript: \r
        self.assertEqual((back["SerialNumber"], back["vpd_raw_xml"]), ("2Z7253DUV9M", info["vpd_raw_xml"]))
        self.assertNotIn("_usb_vendor_info", back)
        self.assertIsNone(ipod.parse_identity("no answer"))


class ToolsSayHowTheyEnded(unittest.TestCase):
    def test_a_problem_on_the_way_makes_the_exit_code_say_so(self):
        import subprocess
        code = ("import sys; sys.path.insert(0, 'src'); import settings\n"
                "def ok(): print('fine')\n"
                "def bumpy(): settings.problem('one file was unreadable')\n"
                "settings.run(ok if sys.argv[1] == 'ok' else bumpy)\n")
        good = subprocess.run([sys.executable, "-c", code, "ok"], capture_output=True, text=True, cwd=ROOT)
        bumpy = subprocess.run([sys.executable, "-c", code, "bumpy"], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(good.returncode, 0)
        self.assertEqual(bumpy.returncode, 3)
        self.assertIn("! one file was unreadable", bumpy.stdout)
        self.assertIn("1 thing went wrong", bumpy.stdout.strip().splitlines()[-1])


class HeavyWorkRunsApart(unittest.TestCase):
    def test_a_child_process_answers_and_its_errors_come_back_as_messages(self):
        self.assertTrue(backend.in_child("python_exe"))
        with self.assertRaises(RuntimeError) as e:
            backend.in_child("ipod_read")             # no settings in the test setup
        self.assertTrue(str(e.exception))


class QuickLookAtStart(unittest.TestCase):
    def test_unchanged_folders_are_trusted_and_new_files_still_seen(self):
        import library
        import tags
        root = tempfile.mkdtemp()
        album = os.path.join(root, "Active", "Band", "Record")
        os.makedirs(album)
        tags.write(sample(album, "mp3", "1"), {"album": "Record", "artist": "Band", "title": "One"})
        self.assertEqual(library.scan(root)[0]["tracks"], 1)
        before = os.stat(album)
        tags.write(os.path.join(album, "1.mp3"), {"genre": "Jazz"})      # a tag inside a file:
        os.utime(os.path.join(album, "1.mp3"), (1, 1))                     # (a later time, really)
        os.utime(album, ns=(before.st_atime_ns, before.st_mtime_ns))       # the folder's time stays
        self.assertEqual(library.scan(root, quick=True)[0]["genre"], "")  # quick: not looked at
        self.assertEqual(library.scan(root)[0]["genre"], "Jazz")          # the full check sees it
        tags.write(sample(album, "mp3", "2"), {"album": "Record", "artist": "Band", "title": "Two"})
        self.assertEqual(library.scan(root, quick=True)[0]["tracks"], 2)  # a new file: seen


if __name__ == "__main__":
    unittest.main()
