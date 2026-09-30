"""
What the window does, without any Qt: the tools it runs, and the data it shows.

Nothing is reimplemented here. The library markup uses library.py, the iPod
is read through ipod.py (podsync), and every tool runs as the same script
the command line runs.
"""

import io
import os
import re
import sys
import time

import library
import settings
import tags as audiotags
from i18n import N_, _

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cfg():
    """The saved settings, or None before the first run."""
    return settings.load()


# ------------------------------------------------------------------ tools
#
# name: title, what it does (about), script, args from the page's params, the flag that turns the
# dry run into the real thing (no 'apply': the tool runs once, for real),
# extra args for the real run, whether the real run may be cancelled (never
# while the iPod's database is being written), what to re-read afterwards,
# and the question asked before applying.


def _text(params, key):
    v = str(params.get(key) or "").strip()
    if not v:
        raise ValueError(_("Fill in this field first."))
    return v


def _existing(params, key):
    v = _text(params, key)
    if not os.path.exists(v):
        raise ValueError(_("Not found: {path}").format(path=v))
    return v


def _count(params):
    v = str(params.get("count") or "").strip()
    return ["--count", v] if v.isdigit() and int(v) > 0 else []


def _drive(params):
    v = _text(params, "drive")
    if not re.fullmatch(r"[A-Za-z]:\\?|/.+", v):
        raise ValueError(_("A drive like E: or a mount point like /Volumes/NAME is expected."))
    return v


def playlists_dir(c):
    """Where vibe.py saves its picks (as vibe.playlists_dir; vibe.py itself is heavy to import)."""
    return os.path.join(settings.path("reports_dir", c), "playlists")


def playlist_file(path):
    """path, if it's one of the saved vibe playlists."""
    folder = os.path.realpath(playlists_dir(cfg()))
    p = os.path.realpath(str(path or ""))
    if os.path.dirname(p) != folder or not p.lower().endswith(".m3u8") or not os.path.isfile(p):
        raise ValueError(_("That isn't one of the saved playlists."))
    return p


TOOLS = {
    "sync": dict(title=N_("Sync the iPod"), script="ipod_sync.py", args=lambda p: [],
                 apply="--apply", apply_extra=["--yes"], cancel_apply=False,
                 refresh={"ipod", "library"},     # the sync also writes the file's genres into tags
                 confirm=N_("Back up the iPod's database, then delete the archive tracks, "
                         "copy the new ones, set covers and genres?")),
    "rescue": dict(title=N_("Save tracks from the iPod"), script="ipod_sync.py",
                   args=lambda p: ["--rescue"], apply="--apply", refresh=set(),
                   confirm=N_("Copy these tracks off the iPod into the incoming folder?")),
    "disk": dict(title=N_("Mirror to disk"), script="ipod_sync.py",
                 args=lambda p: ["--disk", _drive(p)], apply="--apply", apply_extra=["--yes"],
                 refresh=set(), confirm=N_("Copy the new files and DELETE the extras on the "
                                        "device? Deleting can't be undone.")),
    "restore": dict(title=N_("Restore the iPod's database"), script="ipod_sync.py",
                    args=lambda p: ["--restore", "--yes"], cancel=False, refresh={"ipod"}),
    "eject": dict(title=N_("Eject the iPod"), script="ipod.py", args=lambda p: ["--eject"],
                  cancel=False, refresh={"ipod"}),
    "playlist_to_ipod": dict(title=N_("Playlist to the iPod"), script="ipod_sync.py",
                             args=lambda p: ["--playlist", playlist_file(p.get("file")),
                                             "--playlist-only"],
                             apply="--apply", apply_extra=["--yes"], cancel_apply=False,
                             refresh={"ipod"},
                             confirm=N_("Create this playlist on the iPod? Picked tracks not on "
                                     "it yet are copied; nothing is deleted.")),
    "incoming": dict(title=N_("Add new tracks"),
                     args=lambda p: [_existing(p, "folder"), "--to",
                                     "archive" if p.get("to") == "archive" else "active"],
                     apply="--apply", refresh={"library", "ipod"},
                     confirm=N_("Fix the tags and add these tracks to the library?")),
    "covers": dict(title=N_("Find missing cover art"), script="fetch_covers.py", args=lambda p: [],
                   apply="--apply", refresh={"library", "ipod"},
                   confirm=N_("Embed the covers that were found?")),
    "tags": dict(title=N_("Check tags"), script="verify_clean.py", args=lambda p: [],
                 apply="--fix", refresh={"library"},
                 confirm=N_("Remove the v2.4 frames that were found?")),
    "vibe": dict(title=N_("AI vibe playlist"), script="vibe.py",
                 args=lambda p: [_text(p, "vibe")] + _count(p), refresh={"playlists"}),
    "vibe_analyze": dict(title=N_("Analyse the library for playlists"), script="vibe.py",
                         args=lambda p: ["analyze"], refresh=set()),
    "genres_suggest": dict(title=N_("Genres — the AI suggests"),
                           args=lambda p: ["suggest"], refresh={"genres"}),
    "genres_apply": dict(title=N_("Genres — write the tags"), script="genres.py",
                         args=lambda p: ["apply"], apply="--apply",
                         refresh={"library", "ipod", "genres"},
                         confirm=N_("Write these genres into the tags?")),
    "marks": dict(title=N_("Apply the edited list"), script="library.py",
                  args=lambda p: ["--import", _existing(p, "path")], apply="--apply",
                  refresh={"library"}, confirm=N_("Move the albums the way the list says?")),
    "undo": dict(title=N_("Undo the last moves"), script="library.py", args=lambda p: ["--undo"],
                 apply="--apply", refresh={"library"},
                 confirm=N_("Put the albums from the last batch of moves back?")),
    "duplicates": dict(title=N_("Find duplicates"), script="duplicates.py", args=lambda p: [],
                       refresh=set()),
    "fit": dict(title=N_("Fit Active to the iPod"), script="fit_ipod.py",
                args=lambda p: ["--out", os.path.join(settings.path("reports_dir", cfg()), "fit.txt")],
                refresh=set()),
    "soundcheck": dict(title=N_("Sound Check (even out the volume)"), script="soundcheck.py",
                       args=lambda p: [], apply="--apply", refresh={"library"},
                       confirm=N_("Measure the loudness of every Active track and write the tags? It takes a while.")),
    "smart": dict(title=N_("Add smart playlists"), script="ipod_sync.py", args=lambda p: ["--smart"],
                  apply="--apply", apply_extra=["--yes"], cancel_apply=False, refresh={"ipod"},
                  confirm=N_("Add the ready-made smart playlists to the iPod? Nothing else is changed.")),
    "likes": dict(title=N_("What's missing from my likes"),
                  steps=lambda p: [["import_likes.py", _existing(p, "path")], ["find_missing.py"]],
                  refresh=set()),
    "flac": dict(title=N_("Convert FLAC to ALAC"), script="flac_to_alac.py", args=lambda p: [],
                 refresh=set()),
    "build": dict(title=N_("Initial build from an old collection"), script="build_clean.py",
                  args=lambda p: [], apply="--apply", refresh={"library"},
                  confirm=N_("Build the library from the old collection?")),
}


# What each tool does, shown before it runs.
ABOUT = {
    "incoming": N_('Takes a folder of new tracks (mp3 or m4a), fixes their tags (Album Artist, features, '
                 "genre, cover; ID3v2.3 for mp3) and moves them into the library. What's already in the library is skipped. "
                 'You see everything first and confirm.'),
    "covers": N_('For albums without a cover: Deezer first, then MusicBrainz. A cover is only used '
               'when the artist matches. The next sync gives the iPod copies their covers.'),
    "tags": N_('Album Artist set; for mp3 also ID3v2.3 in UTF-16 (an old iPod garbles Cyrillic '
             'otherwise) and no v2.4 frames. The latter can be repaired right away.'),
    "likes": N_('Compares what you listen to with the library: complete, partial, missing, or present '
              'but sitting in Archive (then just mark it Active). Any of these: a Spotify data '
              'export (YourLibrary.json), an Apple Music playlist saved from the browser as .html, '
              'or a text list with one “Artist — Album” per line. A folder with several such files '
              'works too.'),
    "flac": N_('FLAC files from the input folder → .m4a (ALAC) in the output folder (both in '
             'Settings → Audio tools). Always 16-bit, 44.1 / 48 kHz: an old iPod cannot play Hi-Res. Needs ffmpeg.'),
    "build": N_('One-time: builds the library from an old, unsorted collection (Settings → Initial '
              'build → Old collection). Shown as a dry run first.'),
    "marks": N_('The list written by "Export the list", with the [A] / [R] letters changed by hand: '
                'the albums are moved to match. Shown as a dry run first.'),
    "undo": N_('Puts back the albums moved by the last "Apply" or by dragging in the window. '
               'Albums merged into an existing folder cannot be undone. Shown as a dry run first.'),
    "duplicates": N_("Lists tracks stored more than once (Active and Archive, mp3 and m4a). "
                     "Only a report: nothing is deleted."),
    "fit": N_('Compares Active with the "Music space on the iPod" setting and suggests the least-played '
              'albums to move to Archive. Writes reports/fit.txt, which "Apply the edited list" can use. '
              'Nothing is moved.'),
    "soundcheck": N_("Measures each Active track's loudness with ffmpeg and writes an iTunNORM tag. The iPod sync "
                     "copies it to the iPod; then switch Sound Check on in the iPod's Settings. Needs ffmpeg."),
    "smart": N_('Adds "Never played", "Most played", "Top rated" and "Recently added" to the iPod. They follow the '
                'play counts the iPod keeps and are refreshed on each sync. Existing playlists are left alone.'),
    "rescue": N_('Tracks on the iPod that are in neither Active nor Archive — the iPod may hold the '
               'only copy — are copied into the incoming folder.'),
    "genres_suggest": N_("The AI fills in a genre and a style for albums that aren't in the genres file yet. "
                       'Your corrections are never overwritten.'),
}
for _name, _about in ABOUT.items():
    TOOLS[_name]["about"] = _about

def tool_steps(tool, params, applying):
    """[[script, args...], ...] for one run. Raises ValueError on bad params."""
    spec = TOOLS[tool]
    if applying and not spec.get("apply"):
        raise ValueError(_("This tool has nothing to apply."))
    if "steps" in spec:
        return spec["steps"](params)
    args = spec["args"](params)
    if applying:
        args = [*args, spec["apply"], *spec.get("apply_extra", [])]
    return [[spec["script"], *args]]


def python_exe():
    """A console interpreter for the tools, even when the window runs under pythonw."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(console):
            return console
    return exe


# ---------------------------------------------------------------- library


def library_root():
    c = cfg()
    lib = settings.resolve(c.get("library_dir")) if c else None
    return os.path.realpath(lib) if lib and os.path.isdir(lib) else None


def scan_library():
    root = library_root()
    if not root:
        raise RuntimeError(_("The library folder isn't set or doesn't exist. Open Settings."))
    return library.scan(root, with_audio=False)


def _read(p):
    """A file's tags, or every field empty when it can't be read."""
    try:
        return audiotags.read(p)
    except audiotags.Unreadable:
        return {**{f: "" for f in audiotags.FIELDS}, "art": False}


def album_tracks(path):
    out = []
    for fn in audiotags.audio_files(os.listdir(path)):
        p = os.path.join(path, fn)
        t = _read(p)
        try:
            i = audiotags.info(p)
            length, bitrate = i["length"], i["bitrate"]
        except audiotags.Unreadable:
            length, bitrate = 0, 0
        out.append({"file": fn, "track": t["track"].split("/")[0],
                    "title": t["title"] or os.path.splitext(fn)[0],
                    "artist": t["artist"], "genre": t["genre"],
                    "style": t["grouping"], "length": length, "bitrate": bitrate,
                    "art": t["art"]})
    return out


def cover_bytes(path):
    """The album's cover: folder art first (one file for the album), else the first embedded."""
    for name in ("folder.jpg", "cover.jpg", "folder.png", "cover.png", "front.jpg"):
        p = os.path.join(path, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read()
    try:
        names = audiotags.audio_files(os.listdir(path))
    except OSError:
        return None
    for fn in names:
        data = audiotags.cover(os.path.join(path, fn))
        if data:
            return data
    return None


def thumbnail(path, size):
    """JPEG bytes of the album's cover scaled to fit size x size, or b''."""
    raw = cover_bytes(path)
    if not raw:
        return b""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((size, size))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:
        return b""


def save_marks(albums, moves, progress=None):
    """Move albums: {path: 'A' | 'R'}. Returns (moved, errors)."""
    by_path = {a["path"]: a for a in albums}
    todo = [(by_path[p], t) for p, t in moves.items()
            if p in by_path and t in ("A", "R") and by_path[p]["state"] != t]
    done, errors = 0, []
    library.begin_batch()
    for n, (a, target) in enumerate(todo, 1):
        try:
            library.move(a, target)
            done += 1
        except Exception as e:
            errors.append(f"{a['artist']} — {a['album']}: {e}")
        if progress:
            progress(n, len(todo))
    try:
        library.prune_empty()
    except Exception as e:
        errors.append(_("cleaning up empty folders: {error}").format(error=e))
    return done, errors


def marks_file():
    """Where "Export the list" writes and "Apply the edited list" reads by default."""
    return os.path.join(settings.path("reports_dir", cfg()), "split.txt")


def export_list(albums):
    path = marks_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    library.export_list(albums, path)
    return path


# ------------------------------------------------------------------- iPod


def ipod_mounted():
    try:
        import ipod
        return ipod.mounted(cfg() or {})
    except BaseException:           # podsync missing -> ipod.py exits
        return []


def ipod_read():
    """The iPod as the window shows it. Raises RuntimeError with a readable message."""
    c = cfg()
    if not c:
        raise RuntimeError(_("Settings aren't set up yet."))
    try:
        import ipod
        import ipod_sync
        dev = ipod.open_ipod(c)
        db = ipod.load(dev)
    except SystemExit as e:
        raise RuntimeError(str(e.code))
    rows = db["tracks"]
    have = ipod.covered(dev.path, rows)
    states = {}
    try:
        ipod_sync.configure(c)
        idx, _skip = ipod_sync.index_library()
        for n, t in enumerate(rows):
            s = ipod_sync.states_of(ipod_sync.device_keys(t.get("artist"), t.get("title")), idx)
            states[n] = "A" if "A" in s else "R" if "R" in s else ""
    except BaseException:
        states = {}
    tracks = [{"title": t.get("title") or "", "artist": t.get("artist") or "",
               "album": t.get("album") or "", "genre": t.get("genre") or "",
               "length": (t.get("length") or 0) / 1000, "plays": t.get("play_count") or 0,
               "cover": t.get("db_track_id") in have, "state": states.get(n, "?"),
               "size": t.get("size") or 0}
              for n, t in enumerate(rows)]
    lists = [{"title": p.get("title") or "", "count": p.get("mhip_child_count") or 0,
              "folder": bool(p.get("is_folder"))}
             for p in db["dataset2_standard_playlists"] if not p.get("master_flag")]
    master = next((p.get("title") for p in db["dataset2_standard_playlists"]
                   if p.get("master_flag") and p.get("title")), None)
    return {"path": dev.path, "name": dev.ipod_name or master or "iPod", "model": dev.display_name,
            "model_number": dev.model_number, "serial": dev.serial, "firmware": dev.firmware,
            "capacity_gb": dev.disk_size_gb, "free_gb": dev.free_space_gb,
            "music_gb": sum(t["size"] for t in tracks) / 1024 ** 3,
            "color": (dev.color or "").lower(), "tracks": tracks, "playlists": lists}


# -------------------------------------------------------------- playlists


def playlists():
    c = cfg()
    folder = playlists_dir(c) if c else None
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for fn in os.listdir(folder):
        if not fn.lower().endswith(".m3u8"):
            continue
        p = os.path.join(folder, fn)
        try:
            with open(p, encoding="utf-8-sig") as f:
                n = sum(1 for ln in f if ln.strip() and not ln.startswith("#"))
            out.append({"file": p, "name": os.path.splitext(fn)[0], "count": n,
                        "modified": os.path.getmtime(p)})
        except OSError:
            continue
    out.sort(key=lambda x: -x["modified"])
    return out


def playlist_tracks(path):
    with open(path, encoding="utf-8-sig") as f:
        paths = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    out = []
    for p in paths:
        row = {"path": p, "exists": os.path.isfile(p), "title": os.path.basename(p),
               "artist": "", "album": "", "style": "", "length": 0}
        if row["exists"]:
            t = _read(p)
            row.update(title=t["title"] or row["title"], artist=t["artist"],
                       album=t["album"], style=t["grouping"])
            try:
                row["length"] = audiotags.info(p)["length"]
            except audiotags.Unreadable:
                pass
        out.append(row)
    return out


# ----------------------------------------------------------------- genres


def genres_rows():
    import genres
    c = cfg()
    path = settings.path("genres_file", c)
    lib = genres.albums(c)
    known = genres.read_file(path)
    rows = [{"key": k, "part": info["part"], "genre": known.get(k, ("", ""))[0],
             "style": known.get(k, ("", ""))[1], "in_file": k in known}
            for k, info in lib.items()]
    rows += [{"key": k, "part": "", "genre": g, "style": s, "in_file": True}
             for k, (g, s) in known.items() if k not in lib]
    return {"file": path, "choices": list(genres.GENRES), "rows": rows}


def genres_missing():
    """How many library albums have no line in the genres file yet."""
    import genres
    c = cfg()
    if not c:
        return 0
    known = genres.read_file(settings.path("genres_file", c))
    return sum(1 for key in genres.albums(c) if key not in known)


def genres_save(changes):
    """Write {key: (genre, style)} into the genres file, keeping everything else.

    Lines already in the file change in place (comments and order stay); new
    albums are appended. An empty genre removes the album's line.
    """
    import genres
    path = settings.path("genres_file", cfg())
    clean = {}
    for key, (g, s) in changes.items():
        k = str(key).replace("|", "/").replace("\n", " ").strip()
        if k:
            clean[k] = tuple(str(x or "").replace("|", "/").replace("\n", " ").strip()
                             for x in (g, s))
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    else:
        lines = genres.HEADER.rstrip("\n").split("\n")
    out, seen = [], set()
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#"):
            k = s.split("|")[0].strip()
            if k in clean:
                seen.add(k)
                g, st = clean[k]
                if g:
                    out.append(f"{k} | {g} | {st}")
                continue
        out.append(line)
    new = [k for k in clean if k not in seen and clean[k][0]]
    if new:
        out += ["", f"# edited in the window {time.strftime('%Y-%m-%d')}"]
        out += [f"{k} | {clean[k][0]} | {clean[k][1]}" for k in new]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    return len(clean)


# --------------------------------------------------------------- settings


def settings_save(raw, create_library=False):
    """Validate and save; returns {key: error} (empty when saved)."""
    values = cfg() or settings.defaults()
    errors = {}
    for key, v in raw.items():
        if key not in settings.BY_KEY:
            continue
        val, err = settings.validate(key, str(v))
        if err:
            errors[key] = err
        else:
            values[key] = val
    lib = settings.resolve(values.get("library_dir"))
    if not errors and lib and not os.path.isdir(lib):
        if create_library:
            os.makedirs(lib, exist_ok=True)
        else:
            errors["library_dir"] = _("The folder doesn't exist: {path}").format(path=lib)
    if not errors:
        settings.save(values)
    return errors


def open_in_explorer(path):
    if not path or not os.path.exists(path):
        raise ValueError(_("Not there yet: {path}").format(path=path))
    settings.open_path(path)
