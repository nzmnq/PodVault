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

import i18n
from i18n import _

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# MUSIC_UTILITY_SETTINGS points at another settings file (a test setup, a second library)
FILE = os.environ.get("MUSIC_UTILITY_SETTINGS") or os.path.join(ROOT, "settings.json")


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
    # ------------------------------------------------------------ interface
    Field("language", "auto", "choice", "Interface language",
          "Auto = the system's language, English if there's no translation for it. "
          "The list is every translation in locale/. Takes effect after a restart.",
          "Interface", options=["auto", *i18n.languages()]),

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
    Field("genres_file", os.path.join("data", "genres.txt"), "file", "Genres file",
          "Genre and style per album: suggested by the AI, edited by you, "
          "written into the tags by 'Genres'.", "Library"),
    Field("cover_size", 500, "int", "Cover size, px",
          "Size covers are resized to when embedded.", "Library"),
    Field("reports_dir", "reports", "dir", "Working folder",
          "Reports, iPod database backups, saved playlists and the analysis cache.",
          "Library"),

    # ---------------------------------------------------------------- iPod
    Field("ipod_mount", "auto", "text", "iPod drive / mount point",
          "'auto' finds it (a drive letter on Windows, /Volumes on macOS, "
          "/media, /run/media or /mnt on Linux); or e.g. E:\\ or /media/me/IPOD.",
          "iPod"),
    Field("ipod_disk_subdir", "Music", "text", "Folder on the device",
          "Used when mirroring to a Rockbox / disk-mode iPod.", "iPod"),
    Field("duration_tolerance", 3, "int", "Duration tolerance, s",
          "Two versions of a song count as the same file only if their "
          "lengths differ by at most this much.", "iPod"),
    Field("ipod_backups", 3, "int", "Database backups to keep",
          "A backup of the iPod's database is made before every write; "
          "older ones are deleted.", "iPod"),

    # ------------------------------------------------------------------ AI
    Field("vibe_backend", "auto", "choice", "AI through",
          "Auto picks the first that works: Claude Code, then Gemini "
          "(GEMINI_API_KEY), then the Anthropic API (ANTHROPIC_API_KEY, paid).",
          "AI", options=["auto", "cli", "gemini", "api"]),
    Field("vibe_count", 25, "int", "Tracks per playlist",
          "About how many tracks to pick; asked each time, this is the default.",
          "AI"),
    Field("claude_cli_path", "auto", "file", "Claude Code (claude)",
          "'auto' looks in PATH, then in the copy bundled with the Claude "
          "desktop app. Log in once with `claude`.", "AI"),
    Field("claude_cli_model", "", "text", "Claude Code model",
          "Empty = your subscription's default; or e.g. sonnet, opus.",
          "AI", optional=True),
    Field("gemini_model", "gemini-3.8-flash", "text", "Gemini model",
          "Any model your key can use; Flash models are in the free tier "
          "(key from aistudio.google.com).", "AI"),
    Field("anthropic_model", "claude-opus-5", "text", "Anthropic API model",
          "Model for the Anthropic API mode.", "AI"),

    # ---------------------------------------------------------- audio tools
    Field("ffmpeg_path", "auto", "file", "ffmpeg",
          "'auto' looks in PATH, then in bin/. Needed by the tools below; "
          "the library and the iPod don't need it.", "Audio tools"),
    Field("tracklist_file", os.path.join("data", "tracklist.txt"), "file",
          "Tracklist file", "Used by the downloader and the iTunes cover fetcher.",
          "Audio tools"),
    Field("download_dir", os.path.join("data", "iPod_Music"), "dir",
          "Download folder", "Output of the downloader.", "Audio tools"),
    Field("cookies_file", "cookies.txt", "file", "Cookies file",
          "Optional cookies for the downloader.", "Audio tools", optional=True),
    Field("flac_input_dir", os.path.join("data", "input_folder"), "dir",
          "FLAC input folder", "FLAC files to convert to ALAC.", "Audio tools"),
    Field("alac_output_dir", os.path.join("data", "ALAC_Output"), "dir",
          "ALAC output folder", "Converted ALAC files.", "Audio tools"),
    Field("spatial_input_dir", os.path.join("data", "iPod_Music"), "dir",
          "Spatial input folder",
          "Files for spatial-sound processing (the download folder by default).",
          "Audio tools"),
    Field("spatial_output_dir", os.path.join("data", "Spatial_processed"), "dir",
          "Spatial output folder", "Output of spatial-sound processing.", "Audio tools"),

    # -------------------------------------------------------- initial build
    Field("source_dir", "", "dir", "Old collection",
          "Only for the one-time build of the library from an old, unsorted "
          "collection.", "Initial build", first_run=True, optional=True),
    Field("various_artists", "Various Artists", "text", "Compilation artist",
          "Album artist written for compilations, in any language.",
          "Initial build"),
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
        sys.exit(_(
            "No settings yet.\n"
            "Run Main.py (run.bat / run.sh) once — it asks for the basic settings\n"
            "and saves them to {file}.").format(file=FILE))
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
        sys.exit(_("The library folder isn't set. Open Settings in Main.py."))
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


def claude_cli(values=None):
    """Path to the Claude Code command line, or None if it can't be found."""
    values = values if values is not None else require()
    v = str(values.get("claude_cli_path") or "auto")
    if v.lower() != "auto":
        p = resolve(v)
        return p if p and os.path.isfile(p) else None
    found = shutil.which("claude")
    if found:
        return found
    # the Claude desktop app keeps its own copy, one folder per version
    if sys.platform == "win32":
        bundled, exe = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code"), "claude.exe"
    elif sys.platform == "darwin":
        bundled = os.path.expanduser("~/Library/Application Support/Claude/claude-code")
        exe = os.path.join("claude.app", "Contents", "MacOS", "claude")
    else:
        bundled, exe = os.path.expanduser("~/.config/Claude/claude-code"), "claude"
    try:
        versions = [d for d in os.listdir(bundled)
                    if os.path.isfile(os.path.join(bundled, d, exe))]
    except OSError:
        versions = []

    def version_key(d):
        return [int(x) if x.isdigit() else 0 for x in d.split(".")]

    if versions:
        return os.path.join(bundled, max(versions, key=version_key), exe)
    return None


def open_path(path):
    """Open a file or folder with the system's default app."""
    if sys.platform == "win32":
        os.startfile(path)
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def validate(key, raw):
    """Turn user input into a stored value. Returns (value, error)."""
    f = BY_KEY[key]
    raw = (raw or "").strip().strip('"')
    if raw == "":
        if f.optional:
            return "", None
        return None, _("this setting can't be empty")
    if f.kind == "int":
        try:
            n = int(raw)
        except ValueError:
            return None, _("a whole number is expected")
        if n <= 0:
            return None, _("must be greater than zero")
        return n, None
    if f.kind == "choice":
        if raw.lower() not in f.options:
            return None, _("one of: {options}").format(options=', '.join(f.options))
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
                out.append(_("{setting}: folder doesn't exist yet — {path}").format(
                    setting=_(f.label), path=p))
        if f.key == "ffmpeg_path" and ffmpeg(values) is None:
            out.append(_("ffmpeg: not found — the conversion, download and "
                         "spatial-sound tools won't work until it's set"))
    return out
