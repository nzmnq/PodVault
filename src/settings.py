"""
User settings.

Everything that used to be hard-coded — library location, incoming folder,
iPod options, tool folders, the ffmpeg path — lives in settings.json in the
project root. The file is created by the first-run wizard in Main.py and can
be edited later on the Settings screen (or by hand).

settings.json is git-ignored: it holds personal paths and must not end up
in a public repository.

Relative paths are resolved against the project root, so the defaults work
wherever the project is cloned.
"""

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "settings.json")


class Field:
    def __init__(self, key, default, kind, label, help_text, section, first_run=False,
                 options=None, optional=False):
        self.key = key
        self.default = default
        self.kind = kind            # dir | file | choice | int | text
        self.label = label
        self.help = help_text
        self.section = section
        self.first_run = first_run  # asked by the first-run wizard
        self.options = options or []
        self.optional = optional    # may be left empty


FIELDS = [
    # ------------------------------------------------------------- library
    Field("library_dir", "", "dir", "Library folder",
          "Holds Active/ (goes to the iPod) and Archive/ (stays on disk).",
          "Library", first_run=True),
    Field("incoming_dir", os.path.join(os.path.expanduser("~"), "Downloads"), "dir",
          "Incoming folder",
          "Where new tracks are picked up from by 'Add new tracks'.",
          "Library", first_run=True),
    Field("new_tracks_target", "active", "choice", "New tracks go to",
          "Active = on the iPod at the next sync, Archive = set aside.",
          "Library", first_run=True, options=["active", "archive"]),
    Field("source_dir", "", "dir", "Initial build source",
          "Only for the one-time build of the library from an old, unsorted "
          "collection (build_clean.py). Leave empty if you don't need it.",
          "Library", first_run=True, optional=True),
    Field("reports_dir", "reports", "dir", "Reports folder",
          "Where the tools write their reports.", "Library"),

    # ---------------------------------------------------------------- iPod
    Field("ipod_sync_mode", "device", "choice", "iPod sync mode",
          "device = clean the iPod itself (works in manual mode); "
          "library = iTunes library equals Active; "
          "playlist = keep a separate playlist.",
          "iPod", first_run=True, options=["device", "library", "playlist"]),
    Field("ipod_playlist", "iPod Active", "text", "Playlist name",
          "Used by the 'playlist' sync mode.", "iPod"),
    Field("ipod_disk_subdir", "Music", "text", "Folder on the device",
          "Used when mirroring to a Rockbox / disk-mode iPod.", "iPod"),
    Field("duration_tolerance", 3, "int", "Duration tolerance, s",
          "Two versions of a song count as the same file only if their "
          "lengths differ by at most this much.", "iPod"),

    # --------------------------------------------------------------- cover
    Field("cover_size", 500, "int", "Cover size, px",
          "Size covers are resized to when embedded.", "Covers"),

    # --------------------------------------------------------------- tools
    Field("ffmpeg_path", "auto", "file", "ffmpeg",
          "'auto' looks in PATH, then in bin/. Needed by the conversion, "
          "download and spatial-sound tools.", "Tools"),
    Field("tracklist_file", os.path.join("data", "tracklist.txt"), "file",
          "Tracklist file", "Used by the downloader and the iTunes cover fetcher.",
          "Tools"),
    Field("download_dir", os.path.join("data", "iPod_Music"), "dir",
          "Download folder", "Output of the downloader.", "Tools"),
    Field("cookies_file", "cookies.txt", "file", "Cookies file",
          "Optional cookies for the downloader.", "Tools", optional=True),
    Field("flac_input_dir", os.path.join("data", "input_folder"), "dir",
          "FLAC input folder", "FLAC files to convert to ALAC.", "Tools"),
    Field("alac_output_dir", os.path.join("data", "ALAC_Output"), "dir",
          "ALAC output folder", "Converted ALAC files.", "Tools"),
    Field("spatial_input_dir", os.path.join("data", "iPod_Music"), "dir",
          "Spatial input folder", "Files for spatial-sound processing.", "Tools"),
    Field("spatial_output_dir", os.path.join("data", "Spatial_processed"), "dir",
          "Spatial output folder", "Output of spatial-sound processing.", "Tools"),
]

BY_KEY = {f.key: f for f in FIELDS}


def defaults():
    return {f.key: f.default for f in FIELDS}


def exists():
    return os.path.isfile(FILE)


def load():
    """Settings merged over the defaults, or None if never set up."""
    if not exists():
        return None
    with open(FILE, encoding="utf-8") as f:
        stored = json.load(f)
    values = defaults()
    values.update({k: v for k, v in stored.items() if k in BY_KEY})
    return values


def save(values):
    clean = {k: values.get(k, BY_KEY[k].default) for k in BY_KEY}
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


def require():
    """Settings for command-line tools. Exits if the wizard was never run."""
    values = load()
    if values is None:
        sys.exit(
            "No settings yet.\n"
            "Run Main.py (or run.bat) once — it asks for the basic settings\n"
            f"and saves them to {FILE}."
        )
    return values


def resolve(value):
    """A stored path as an absolute path; relative ones are project-relative."""
    if value in (None, ""):
        return None
    value = os.path.expandvars(os.path.expanduser(str(value)))
    return value if os.path.isabs(value) else os.path.normpath(os.path.join(ROOT, value))


def path(key, values=None):
    values = values if values is not None else require()
    return resolve(values.get(key))


def library_paths(values=None):
    """(library, Active, Archive) as absolute paths."""
    lib = path("library_dir", values)
    if not lib:
        sys.exit("The library folder isn't set. Open Settings in Main.py.")
    return lib, os.path.join(lib, "Active"), os.path.join(lib, "Archive")


def ffmpeg(values=None):
    """Path to ffmpeg, or None if it can't be found."""
    values = values if values is not None else require()
    v = str(values.get("ffmpeg_path") or "auto")
    if v.lower() != "auto":
        p = resolve(v)
        return p if p and os.path.isfile(p) else None
    found = shutil.which("ffmpeg")
    if found:
        return found
    for name in ("ffmpeg.exe", "ffmpeg"):
        p = os.path.join(ROOT, "bin", name)
        if os.path.isfile(p):
            return p
    return None


def validate(key, raw):
    """Turn user input into a stored value. Returns (value, error)."""
    f = BY_KEY[key]
    raw = (raw or "").strip().strip('"')
    if raw == "":
        if f.optional:
            return "", None
        return None, "this setting can't be empty"
    if f.kind == "int":
        try:
            n = int(raw)
        except ValueError:
            return None, "a whole number is expected"
        if n <= 0:
            return None, "must be greater than zero"
        return n, None
    if f.kind == "choice":
        if raw.lower() not in f.options:
            return None, f"one of: {', '.join(f.options)}"
        return raw.lower(), None
    return raw, None


# Folders the user points at, which must already exist. The tool folders
# (data/...) are created by the tools on first use, so a warning about them
# would only be noise.
MUST_EXIST = ("library_dir", "incoming_dir", "source_dir")


def warnings(values):
    """Human-readable problems with the current settings (not fatal)."""
    out = []
    for f in FIELDS:
        v = values.get(f.key)
        if f.key in MUST_EXIST and v not in (None, ""):
            p = resolve(v)
            if not os.path.isdir(p):
                out.append(f"{f.label}: folder doesn't exist yet — {p}")
        if f.key == "ffmpeg_path" and ffmpeg(values) is None:
            out.append("ffmpeg: not found — the conversion, download and "
                       "spatial-sound tools won't work until it's set")
    return out
