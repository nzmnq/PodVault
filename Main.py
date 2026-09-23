"""
Music Utility — the main program.

Run:  run.bat      (or: python Main.py)

One text interface over all the tools: the library (Active/Archive
markup, adding tracks, iPod sync, cover art, tag checks) and the audio
utilities (FLAC -> ALAC, tracklist downloader, iTunes covers, spatial
sound). Every tool is still a standalone script in src/ and works from
the command line; the program runs them and shows their output as is.

On the first run a short wizard asks for the basic settings. They are
saved to settings.json and can be changed later on the Settings screen.
"""

import codecs
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
sys.path.insert(0, SRC)

import library   # noqa: E402
import settings  # noqa: E402
import tui       # noqa: E402
from tui import BOLD, FG, INV, RESET  # noqa: E402

FILTERS = [
    ("all", lambda a: True),
    ("Active only", lambda a: a["state"] == "A"),
    ("Archive only", lambda a: a["state"] == "R"),
    ("Ukrainian-language", lambda a: a["ua"]),
    ("missing cover art", lambda a: a["no_art"] > 0),
]


class App:
    def __init__(self):
        self.albums = None
        self.marks = {}          # path -> 'A'/'R', until saved
        self.cfg = settings.load()

    # ------------------------------------------------------------ data

    def library_ok(self):
        lib = settings.resolve(self.cfg.get("library_dir"))
        return bool(lib) and os.path.isdir(lib)

    def load(self, force=False):
        if not self.library_ok():
            self.albums = []
            return self.albums
        if self.albums is None or force:
            tui.clear()
            print("\n  Reading the library...")
            tui.flush()
            self.albums = library.scan()
            self.marks.clear()
        return self.albums

    def state_of(self, a):
        return self.marks.get(a["path"], a["state"])

    # ------------------------------------------------------------ running

    def run_tool(self, title, script, args=()):
        """Run a script from src/ and show its output live."""
        tui.clear()
        tui.show_cursor()
        print("\n".join(tui.header(title, f"{script} {' '.join(args)}".strip())))
        print()
        try:
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            p = subprocess.Popen(
                [sys.executable, os.path.join(SRC, script), *args],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, cwd=HERE,
            )
            # Pass output through as it arrives, not line by line: a prompt
            # without a trailing newline (input("... ")) used to stay in the
            # buffer, so the tool sat waiting for an answer to a question
            # that never reached the screen.
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                chunk = os.read(p.stdout.fileno(), 4096)
                if not chunk:
                    break
                sys.stdout.write(decoder.decode(chunk))
                sys.stdout.flush()
            sys.stdout.write(decoder.decode(b"", final=True))
            p.wait()
            code = p.returncode
        except Exception as e:
            print(f"\n  Could not start it: {e}")
            code = -1

        if code:
            print(f"\n{FG['red']}  Exit code: {code}{RESET}")
        tui.hide_cursor()
        tui.pause()
        self.albums = None       # the contents may have changed
        return code

    def ask_apply(self, title, script, extra=(), flag="--apply", question="Apply the changes?",
                  apply_extra=()):
        """Dry run first, then ask whether to apply for real.

        apply_extra is added only to the real run — e.g. --yes for tools
        that would otherwise ask again themselves, in their own style.
        Returns True if the changes were applied without an error.
        """
        if self.run_tool(f"{title} — dry run", script, extra):
            return False     # the dry run failed: nothing sensible to apply
        tui.clear()
        print("\n".join(tui.header(title)))
        if tui.confirm(question):
            return self.run_tool(f"{title} — applying", script, [*extra, flag, *apply_extra]) == 0
        return False

    def need_library(self):
        if self.library_ok():
            return True
        tui.clear()
        print("\n".join(tui.header("NO LIBRARY FOLDER")))
        print(f"\n  The library folder isn't set or doesn't exist:")
        print(f"  {FG['grey']}{self.cfg.get('library_dir') or '(empty)'}{RESET}")
        print("\n  Open Settings and set it.")
        tui.pause()
        return False

    # ------------------------------------------------------- main screen

    def screen_main(self):
        L = lambda fn: (lambda: self.need_library() and fn())  # noqa: E731
        items = [
            ("LIBRARY", None),
            ("Active / Archive markup", L(self.screen_albums)),
            ("Add new tracks", L(self.screen_incoming)),
            ("Sync the iPod", L(self.screen_sync)),
            ("Find missing cover art",
             L(lambda: self.ask_apply("Cover art", "fetch_covers.py"))),
            ("Check tags",
             L(lambda: self.ask_apply("Check tags", "verify_clean.py", flag="--fix",
                                      question="Remove v2.4 frames if any were found (--fix)?"))),
            ("AI vibe playlist", L(self.screen_vibe)),
            ("Genres (AI suggests, you correct)", L(self.screen_genres)),
            ("What's missing from my likes", L(self.screen_missing)),
            ("Export the list to a file", L(self.export_file)),
            ("AUDIO TOOLS", None),
            ("Convert FLAC to ALAC",
             lambda: self.run_tool("FLAC -> ALAC", "flac_to_alac.py")),
            ("Download from the tracklist",
             lambda: self.run_tool("Tracklist downloader", "metadata_download.py")),
            ("Fetch covers for the tracklist (iTunes)",
             lambda: self.run_tool("Tracklist covers", "fetch_cover.py")),
            ("Spatial sound processing",
             lambda: self.run_tool("Spatial sound", "sur_sound.py")),
            ("Initial build from an old collection",
             lambda: self.ask_apply("Initial build", "build_clean.py")),
            ("", None),
            ("Settings", self.screen_settings),
        ]
        selectable = [i for i, (_, fn) in enumerate(items) if fn]
        cur = selectable[0]
        while True:
            albums = self.load()
            w, h = tui.size()

            if self.library_ok():
                s = library.stats(albums)
                lines = tui.header(
                    "MUSIC UTILITY",
                    f"{len(albums)} albums · {sum(a['tracks'] for a in albums)} tracks", w)
                lines.append("")
                lines.append(
                    f"  {FG['green']}Active {RESET} {s['active']['albums']:3d} alb."
                    f"  {s['active']['tracks']:5d} tr.  {s['active']['gb']:5.1f} GB"
                    f"  {FG['grey']}-> to the iPod{RESET}")
                lines.append(
                    f"  {FG['yellow']}Archive{RESET} {s['archive']['albums']:3d} alb."
                    f"  {s['archive']['tracks']:5d} tr.  {s['archive']['gb']:5.1f} GB"
                    f"  {FG['grey']}-> stays on disk{RESET}")
                if s["no_art"]:
                    lines.append(f"  {FG['grey']}without cover art: {s['no_art']} tracks{RESET}")
            else:
                lines = tui.header("MUSIC UTILITY", "library folder not set", w)
                lines.append("")
                lines.append(f"  {FG['yellow']}The library folder isn't set or doesn't exist."
                             f" Open Settings.{RESET}")
            lines.append("")

            for i, (label, fn) in enumerate(items):
                if fn is None:
                    lines.append(f"  {FG['grey']}{label}{RESET}" if label else "")
                elif i == cur:
                    lines.append(f"    {INV} {label} {RESET}")
                else:
                    lines.append(f"     {label}")

            lines.append("")
            lines += tui.footer([("↑↓", "select"), ("Enter", "open"), ("q", "quit")], w)
            tui.draw(lines)

            k = tui.read_key()
            # 'й' is 'q' on a Russian layout
            if k in ("q", "й", tui.ESCAPE):
                return
            pos = selectable.index(cur)
            if k == tui.UP:
                cur = selectable[(pos - 1) % len(selectable)]
            elif k == tui.DOWN:
                cur = selectable[(pos + 1) % len(selectable)]
            elif k == tui.ENTER:
                items[cur][1]()

    # ------------------------------------------------------- markup screen

    def screen_albums(self):
        albums = self.load()
        cur = top = 0
        fi = 0
        query = ""

        while True:
            w, h = tui.size()
            name, pred = FILTERS[fi]
            rows = [a for a in albums if pred(a)]
            if query:
                q = query.lower()
                rows = [a for a in rows
                        if q in a["artist"].lower() or q in a["album"].lower()]
            cur = max(0, min(cur, len(rows) - 1))

            n_act = sum(1 for a in albums if self.state_of(a) == "A")
            n_arc = len(albums) - n_act
            changed = sum(1 for a in albums if self.state_of(a) != a["state"])

            sub = (f"Active {n_act} · Archive {n_arc}"
                   + (f" · {FG['magenta']}unsaved: {changed}{RESET}{FG['grey']}" if changed else ""))
            lines = tui.header("MARKUP", sub, w)
            lines.append(f"  filter: {BOLD}{name}{RESET}"
                         + (f"   search: {BOLD}{query}{RESET}" if query else "")
                         + f"   {FG['grey']}({len(rows)} of {len(albums)}){RESET}")
            lines.append("")

            body = max(5, h - len(lines) - 4)
            if cur < top:
                top = cur
            elif cur >= top + body:
                top = cur - body + 1

            for i in range(top, min(top + body, len(rows))):
                a = rows[i]
                st = self.state_of(a)
                dirty = st != a["state"]
                badge = (f"{FG['green']} A {RESET}" if st == "A"
                         else f"{FG['yellow']} R {RESET}")
                if dirty:
                    badge = f"{INV}{' A ' if st == 'A' else ' R '}{RESET}"

                title = f"{a['artist']} — {a['album']}"
                meta = f"{a['tracks']:3d} tr. {a['bytes'] / 1024 / 1024:5.0f} MB"
                flags = ("" if not a["ua"] else f" {FG['cyan']}UA{RESET}")
                flags += ("" if not a["no_art"] else f" {FG['red']}!art{RESET}")

                avail = w - 26
                line = f" {badge} {tui.pad(title, avail)} {FG['grey']}{meta}{RESET}{flags}"
                lines.append(f"{INV}{line}{RESET}" if i == cur else line)

            lines.append("")
            lines += tui.footer([
                ("↑↓", "select"), ("A/R", "mark"), ("Space", "toggle"),
                ("f", "filter"), ("/", "search"), ("s", "save"), ("Esc", "back"),
            ], w)
            tui.draw(lines)

            k = tui.read_key()
            if k == tui.UP:
                cur = max(0, cur - 1)
            elif k == tui.DOWN:
                cur = min(len(rows) - 1, cur + 1)
            elif k == tui.PGUP:
                cur = max(0, cur - body)
            elif k == tui.PGDN:
                cur = min(len(rows) - 1, cur + body)
            elif k == tui.HOME:
                cur = 0
            elif k == tui.END:
                cur = len(rows) - 1
            elif k == "f":
                fi, cur, top = (fi + 1) % len(FILTERS), 0, 0
            elif k == "/":
                query = tui.prompt("Search (empty to clear): ")
                cur = top = 0
            # 'ф', 'к', 'ы' are A, R, S on a Russian layout
            elif k and k.lower() in ("a", "ф") and rows:
                self.set_mark(rows[cur], "A")
                cur = min(len(rows) - 1, cur + 1)
            elif k and k.lower() in ("r", "к") and rows:
                self.set_mark(rows[cur], "R")
                cur = min(len(rows) - 1, cur + 1)
            elif k == " " and rows:
                self.set_mark(rows[cur], "R" if self.state_of(rows[cur]) == "A" else "A")
            elif k and k.lower() in ("s", "ы"):
                if changed and self.save(albums):
                    cur = top = 0
            elif k == tui.ESCAPE:
                if changed and not tui.confirm(
                        f"{changed} unsaved changes. Leave and lose them?"):
                    continue
                self.marks.clear()
                return

    def set_mark(self, album, state):
        if state == album["state"]:
            self.marks.pop(album["path"], None)
        else:
            self.marks[album["path"]] = state

    def save(self, albums):
        todo = [a for a in albums if self.state_of(a) != a["state"]]
        tui.clear()
        print("\n".join(tui.header("SAVING", f"albums to move: {len(todo)}")))
        print()
        for a in todo[:20]:
            arrow = ("-> Archive" if self.state_of(a) == "R" else "-> Active ")
            print(f"  {arrow}  {a['artist']} — {a['album']}  ({a['tracks']} tr.)")
        if len(todo) > 20:
            print(f"  ... {len(todo) - 20} more")

        if not tui.confirm("Move the files?"):
            return False

        print()
        for n, a in enumerate(todo, 1):
            library.move(a, self.marks[a["path"]])
            print(f"\r  moved {n}/{len(todo)}", end="", flush=True)
        library.prune_empty()
        self.marks.clear()
        self.albums = library.scan()
        print(f"\n\n  Done: {len(todo)} albums.")
        tui.pause()
        return True

    # ------------------------------------------------------------ library tools

    def screen_incoming(self):
        tui.clear()
        print("\n".join(tui.header("ADD NEW TRACKS", "where to take them from")))
        default = settings.resolve(self.cfg.get("incoming_dir")) or ""
        print(f"\n  Default (from Settings): {FG['grey']}{default}{RESET}")
        path = tui.prompt("Folder (Enter for default): ") or default
        if not os.path.isdir(path):
            print(f"\n  {FG['red']}No such folder: {path}{RESET}")
            tui.pause()
            return

        target = self.cfg.get("new_tracks_target", "active")
        other = "archive" if target == "active" else "active"
        tui.clear()
        print("\n".join(tui.header("WHERE TO PUT THEM")))
        print(f"""
  {BOLD}Active{RESET}  — goes to the iPod on the next sync.
  {BOLD}Archive{RESET} — set aside; reaches the iPod only once you mark it [A].

  Default from Settings: {BOLD}{target.capitalize()}{RESET}
""")
        if tui.confirm(f"Put them into {other.capitalize()} this time instead?"):
            target = other

        # the dry run doubles as the inspection of the incoming files
        added = self.ask_apply("Adding", "add_incoming.py", [path, "--to", target])
        if added and target == "active":
            tui.clear()
            print("\n".join(tui.header("Adding")))
            if tui.confirm("The new tracks are in Active. Sync the iPod now?"):
                self.screen_sync()

    def screen_sync(self):
        default = self.cfg.get("ipod_sync_mode", "device")
        tui.clear()
        print("\n".join(tui.header("SYNC THE IPOD",
                                   "the device should hold exactly what's in Active")))
        print(f"""
  {BOLD}1. Clean the iPod itself{RESET} {FG['grey']}(device){RESET}
     Works in "Manually manage music" mode, where iTunes removes nothing
     from the device on its own. Matching by tags and duration; only
     tracks positively recognised as archive are deleted. Needs iTunes
     running and the iPod connected.

  {BOLD}2. Library = Active{RESET} {FG['grey']}(library){RESET}
     The iTunes library is brought in line with Active, iTunes stays in
     "sync entire library" mode. Needs iTunes running.

  {BOLD}3. Separate playlist{RESET} {FG['grey']}(playlist){RESET}
     The library isn't touched; a playlist equal to Active is kept. The
     iPod must be set once to sync only that playlist, otherwise archive
     tracks still reach it and play in shuffle. Needs iTunes running.

  {BOLD}4. Mirror to disk{RESET} {FG['grey']}(Rockbox or disk mode){RESET}
     Copy with removal of extras. Only the subfolder the script manages
     is touched.

  {BOLD}5. Save from the iPod{RESET} {FG['grey']}(no iTunes needed){RESET}
     Tracks that are on the iPod but in neither Active nor Archive — the
     iPod may hold the only copy. They're copied into the incoming folder,
     then "Add new tracks" brings them into the library.

  {FG['grey']}None of them writes iTunesDB: iTunes edits it itself.{RESET}
""")
        print(f"  {BOLD}Enter{RESET} default ({default})   {BOLD}1{RESET} device   "
              f"{BOLD}2{RESET} library   {BOLD}3{RESET} playlist   {BOLD}4{RESET} disk   "
              f"{BOLD}5{RESET} save from iPod   {BOLD}Esc{RESET} back")
        tui.flush()
        modes = {"1": "device", "2": "library", "3": "playlist"}
        while True:
            k = tui.read_key()
            if k == tui.ESCAPE:
                return
            if k == tui.ENTER:
                k = {v: n for n, v in modes.items()}.get(default, "1")
            if k in modes:
                mode = modes[k]
                if mode == "device":
                    # One clear question here instead of the script asking
                    # again in its own "type yes" style.
                    self.ask_apply(
                        "iPod sync (device)", "ipod_sync.py", ["--mode", mode],
                        question="Apply: delete the archive tracks from the iPod "
                                 "(can't be undone on the device), copy new ones, set covers? "
                                 "If covers change, iTunes is closed at the end to write them.",
                        apply_extra=["--yes"])
                else:
                    self.ask_apply(f"iPod sync ({mode})", "ipod_sync.py", ["--mode", mode])
                return
            if k == "5":
                if self.ask_apply("Save from the iPod", "ipod_sync.py", ["--rescue"],
                                  question="Copy these tracks off the iPod into the incoming folder?"):
                    tui.clear()
                    print("\n".join(tui.header("Save from the iPod")))
                    if tui.confirm("Add them to the library now?"):
                        self.screen_incoming()
                return
            if k == "4":
                drive = tui.prompt("iPod drive letter (e.g. E:): ").strip()
                if not drive:
                    return
                self.ask_apply(
                    "Mirror to disk", "ipod_sync.py", ["--disk", drive],
                    question="Apply: copy new files and DELETE the extras on the device "
                             "(can't be undone)?",
                    apply_extra=["--yes"])
                return

    def screen_vibe(self):
        tui.clear()
        print("\n".join(tui.header("AI VIBE PLAYLIST", "describe a mood, get a playlist")))
        print(f"""
  Describe it in your own words, in any language:
    {FG['grey']}rainy night drive, slow, a bit sad
    loud and fast for the gym
    morning coffee, something light{RESET}

  Claude picks and orders tracks from Active, using what it knows about
  the songs plus tempo/energy measured from the audio (the first run
  analyses the whole library, a few minutes; after that only new tracks).
  The result is saved as an .m3u8 playlist in the reports folder.
  Goes through Claude Code on a Claude subscription, or Google Gemini
  with a free key (aistudio.google.com -> GEMINI_API_KEY), or the paid
  Anthropic API — see Settings -> AI.
""")
        vibe = tui.prompt("Vibe (empty to go back): ").strip()
        if not vibe:
            return
        default = self.cfg.get("vibe_count", 25)
        count = tui.prompt(f"About how many tracks (Enter = {default}): ").strip()
        args = [vibe] + (["--count", count] if count.isdigit() else [])
        # Only the pick: creating playlists on the iPod through iTunes was
        # refused on the iPod this was built with (see vibe.push_to_ipod), so
        # a button for it would mostly fail. `vibe.py --push-last` tries it.
        self.run_tool("AI vibe playlist", "vibe.py", args)

    def screen_genres(self):
        path = settings.path("genres_file", self.cfg)
        while True:
            tui.clear()
            print("\n".join(tui.header("GENRES", "genre + precise style per album")))
            print(f"""
  {BOLD}Genre{RESET}    — broad (Rock, Hip-Hop, Electronic...): the iPod's Genres menu.
  {BOLD}Grouping{RESET} — the precise style (Hyperpop, Cloud Rap...): read by the AI
             playlists, doesn't clutter the iPod menu.

  {BOLD}1{RESET} Suggest   the AI fills in albums not yet in the file
  {BOLD}2{RESET} Edit      open the file and correct what's wrong
  {BOLD}3{RESET} Apply     write the genres into the tags (dry run first)

  {FG['grey']}File: {path}
  Your corrections are never overwritten; the next sync updates the iPod.{RESET}

  {BOLD}Esc{RESET} back""")
            tui.flush()
            k = tui.read_key()
            if k == tui.ESCAPE:
                return
            if k == "1":
                self.run_tool("Genres — suggest", "genres.py", ["suggest"])
            elif k == "2":
                if os.path.isfile(path):
                    os.startfile(path)
                else:
                    print(f"\n  {FG['red']}No file yet — run Suggest first.{RESET}")
                    tui.pause()
            elif k == "3":
                self.ask_apply("Genres", "genres.py", ["apply"],
                               question="Write these genres into the tags?")

    def screen_missing(self):
        tui.clear()
        print("\n".join(tui.header("WHAT'S MISSING", "comparing likes with the library")))
        print(f"""
  You need a list of what you listen to. Any of these works:

  {BOLD}1. Spotify data export{RESET} {FG['grey']}(works without Premium){RESET}
     spotify.com -> Privacy Settings -> Download your data.
     The email arrives within a few days. You need YourLibrary.json.

  {BOLD}2. An Apple Music playlist page{RESET}
     Open the playlist in the browser and save the page as .html.

  {BOLD}3. A plain text list{RESET}
     One album per line: Artist — Album

  A folder with several such files works too.
""")
        path = tui.prompt("Path to the file or folder (Enter to cancel): ")
        if not path:
            return
        if not os.path.exists(path):
            print(f"\n  {FG['red']}Not found{RESET}")
            tui.pause()
            return
        self.run_tool("Importing likes", "import_likes.py", [path])
        self.run_tool("What's missing", "find_missing.py")

    def export_file(self):
        albums = self.load()
        path = os.path.join(settings.path("reports_dir", self.cfg), "split.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        library.export_list(albums, path)
        tui.clear()
        print("\n".join(tui.header("EXPORT")))
        print(f"\n  List written: {BOLD}{path}{RESET}")
        print(f"\n  {FG['grey']}Letters are filled in from the current state.")
        print("  Edit it and come back — to apply:")
        print(f"  python src\\library.py --import \"{path}\" --apply{RESET}")
        tui.pause()

    # ------------------------------------------------------------ settings

    def edit_field(self, values, f):
        """Ask for a new value of one setting. Returns True if it changed."""
        cur = values.get(f.key)
        if f.kind == "choice":
            i = f.options.index(cur) if cur in f.options else -1
            values[f.key] = f.options[(i + 1) % len(f.options)]
            return True
        print(f"\n\n {BOLD}{f.label}{RESET}")
        print(f" {FG['grey']}{f.help}{RESET}")
        print(f" current: {cur if cur not in (None, '') else '(empty)'}")
        hint = "Enter keeps it"
        if f.optional:
            hint += ", '-' clears it"
        raw = tui.prompt(f"New value ({hint}): ")
        if raw == "":
            return False
        if raw == "-" and f.optional:
            raw = ""
        val, err = settings.validate(f.key, raw)
        if err:
            print(f"\n {FG['red']}Not saved: {err}{RESET}")
            tui.pause()
            return False
        values[f.key] = val
        return True

    def screen_settings(self):
        values = dict(self.cfg)
        fields = settings.FIELDS
        cur = 0
        dirty = False
        while True:
            w, h = tui.size()
            lines = tui.header("SETTINGS", settings.FILE, w)
            section = None
            for i, f in enumerate(fields):
                if f.section != section:
                    section = f.section
                    lines.append(f"  {FG['grey']}{section.upper()}{RESET}")
                v = values.get(f.key)
                shown = str(v) if v not in (None, "") else "(empty)"
                if f.kind == "choice":
                    shown = f"{v}   {FG['grey']}[{' / '.join(f.options)}]{RESET}"
                row = f"   {tui.pad(f.label, 26)} {tui.fit(shown, max(10, w - 34))}"
                lines.append(f"{INV}{row}{RESET}" if i == cur else row)

            f = fields[cur]
            lines.append("")
            lines.append(f"  {FG['grey']}{tui.fit(f.help, w - 4)}{RESET}")
            resolved = settings.resolve(values.get(f.key)) if f.kind in ("dir", "file") else None
            if resolved and resolved != values.get(f.key):
                lines.append(f"  {FG['grey']}-> {tui.fit(resolved, w - 7)}{RESET}")

            warns = settings.warnings(values)
            if warns:
                lines.append("")
                for msg in warns[:4]:
                    lines.append(f"  {FG['yellow']}! {tui.fit(msg, w - 6)}{RESET}")

            lines.append("")
            lines += tui.footer([
                ("↑↓", "select"), ("Enter", "edit / switch"), ("s", "save"),
                ("Esc", "back"),
            ] + ([("", f"{FG['magenta']}unsaved changes{RESET}")] if dirty else []), w)
            tui.draw(lines)

            k = tui.read_key()
            if k == tui.UP:
                cur = (cur - 1) % len(fields)
            elif k == tui.DOWN:
                cur = (cur + 1) % len(fields)
            elif k == tui.ENTER:
                if self.edit_field(values, fields[cur]):
                    dirty = True
            elif k and k.lower() in ("s", "ы"):
                settings.save(values)
                self.cfg = settings.load()
                self.albums = None
                dirty = False
            elif k == tui.ESCAPE:
                if dirty and not tui.confirm("Leave without saving the changes?"):
                    continue
                return

    def first_run(self):
        """Ask for the basic settings on the very first start."""
        values = settings.defaults()
        tui.clear()
        print("\n".join(tui.header("MUSIC UTILITY — FIRST RUN", "a few basic settings")))
        print(f"""
  Answer a few questions; everything can be changed later on the
  Settings screen. Press Enter to accept the value in [brackets].

  The answers are saved to {settings.FILE}
  (git-ignored — personal paths don't end up in the repository).
""")
        tui.show_cursor()
        for f in settings.FIELDS:
            if not f.first_run:
                continue
            print(f"\n {BOLD}{f.label}{RESET}")
            print(f" {FG['grey']}{f.help}{RESET}")
            if f.kind == "choice":
                print(f" {FG['grey']}options: {', '.join(f.options)}{RESET}")
            while True:
                default = values.get(f.key)
                shown = f" [{default}]" if default not in (None, "") else (
                    " [empty]" if f.optional else "")
                raw = input(f" > {shown} ").strip()
                if raw == "":
                    raw = str(default or "")
                val, err = settings.validate(f.key, raw)
                if err:
                    print(f"   {FG['red']}{err}{RESET}")
                    continue
                if f.kind == "dir" and val and f.key == "library_dir":
                    p = settings.resolve(val)
                    if not os.path.isdir(p):
                        ans = input(f"   Folder doesn't exist. Create {p}? [y/n] ").strip().lower()
                        if ans in ("y", "yes", "д", "да"):
                            os.makedirs(p, exist_ok=True)
                        else:
                            continue
                values[f.key] = val
                break

        settings.save(values)
        self.cfg = settings.load()
        print(f"\n {FG['green']}Saved.{RESET}")
        for msg in settings.warnings(self.cfg):
            print(f" {FG['yellow']}! {msg}{RESET}")
        tui.hide_cursor()
        tui.pause()


def selftest():
    """Check the non-interactive parts without a console."""
    cfg = settings.load()
    if cfg is None:
        sys.exit("selftest needs settings.json — run Main.py once first")
    app = App()
    albums = app.load()
    s = library.stats(albums)
    print(f"albums:   {len(albums)}")
    print(f"active:   {s['active']}")
    print(f"archive:  {s['archive']}")
    print("\nfilters:")
    for name, pred in FILTERS:
        print(f"  {name}: {sum(1 for a in albums if pred(a))}")
    if albums:
        print("\nmoving to the same place doesn't move files:", end=" ")
        a = albums[0]
        before = a["path"]
        library.move(a, a["state"])
        print("ok" if a["path"] == before else "ERROR")
    print("\nsettings warnings:")
    for msg in settings.warnings(cfg) or ["(none)"]:
        print(f"  {msg}")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    tui.enable_ansi()
    try:
        app = App()
        if app.cfg is None:
            app.first_run()
        tui.hide_cursor()
        app.screen_main()
    except KeyboardInterrupt:
        pass
    finally:
        tui.show_cursor()
        tui.clear()
        print("Bye.")


if __name__ == "__main__":
    main()
