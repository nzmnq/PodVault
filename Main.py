"""
Music Utility — the main program.

Run:  run.bat / ./run.sh      (or: python Main.py)

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
from i18n import N_, _  # noqa: E402
import tui       # noqa: E402
from tui import BOLD, FG, INV, RESET  # noqa: E402

FILTERS = [
    (N_("all"), lambda a: True),
    (N_("Active only"), lambda a: a["state"] == "A"),
    (N_("Archive only"), lambda a: a["state"] == "R"),
    (N_("Ukrainian-language"), lambda a: a["ua"]),
    (N_("missing cover art"), lambda a: a["no_art"] > 0),
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
            print(_("\n  Reading the library..."))
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
            print(_("\n  Could not start it: {error}").format(error=e))
            code = -1

        if code:
            print(f"\n{FG['red']}  " + _("Exit code: {code}").format(code=code) + RESET)
        tui.hide_cursor()
        tui.pause()
        self.albums = None       # the contents may have changed
        return code

    def ask_apply(self, title, script, extra=(), flag="--apply", question=None,
                  apply_extra=()):
        """Dry run first, then ask whether to apply for real.

        apply_extra is added only to the real run — e.g. --yes for tools
        that would otherwise ask again themselves, in their own style.
        Returns True if the changes were applied without an error.
        """
        question = question or _("Apply the changes?")
        if self.run_tool(_("{title} — dry run").format(title=title), script, extra):
            return False     # the dry run failed: nothing sensible to apply
        tui.clear()
        print("\n".join(tui.header(title)))
        if tui.confirm(question):
            return self.run_tool(_("{title} — applying").format(title=title), script, [*extra, flag, *apply_extra]) == 0
        return False

    def need_library(self):
        if self.library_ok():
            return True
        tui.clear()
        print("\n".join(tui.header(_("NO LIBRARY FOLDER"))))
        print(_("\n  The library folder isn't set or doesn't exist:"))
        print(f"  {FG['grey']}{self.cfg.get('library_dir') or _('(empty)')}{RESET}")
        print(_("\n  Open Settings and set it."))
        tui.pause()
        return False

    # ------------------------------------------------------- main screen

    def screen_main(self):
        L = lambda fn: (lambda: self.need_library() and fn())  # noqa: E731
        items = [
            (_("LIBRARY"), None),
            (_("Add new tracks"), L(self.screen_incoming)),
            (_("Active / Archive markup"), L(self.screen_albums)),
            (_("Genres (AI suggests, you correct)"), L(self.screen_genres)),
            (_("Find missing cover art"),
             L(lambda: self.ask_apply("Cover art", "fetch_covers.py"))),
            (_("Check tags"),
             L(lambda: self.ask_apply("Check tags", "verify_clean.py", flag="--fix",
                                      question="Remove v2.4 frames if any were found (--fix)?"))),
            (_("What's missing from my likes"), L(self.screen_missing)),
            (_("Export the list to a file"), L(self.export_file)),
            (_("IPOD"), None),
            (_("Sync the iPod"), L(self.screen_sync)),
            (_("AI vibe playlist"), L(self.screen_vibe)),
            (_("AUDIO TOOLS"), None),
            (_("Convert FLAC to ALAC"),
             lambda: self.run_tool("FLAC -> ALAC", "flac_to_alac.py")),
            (_("Download from the tracklist"),
             lambda: self.run_tool("Tracklist downloader", "metadata_download.py")),
            (_("Fetch covers for the tracklist (iTunes)"),
             lambda: self.run_tool("Tracklist covers", "fetch_cover.py")),
            (_("Spatial sound processing"),
             lambda: self.run_tool("Spatial sound", "sur_sound.py")),
            ("", None),
            (_("Open the window (iTunes-style)"), self.open_window),
            (_("Initial build from an old collection"),
             lambda: self.ask_apply("Initial build", "build_clean.py")),
            (_("Settings"), self.screen_settings),
        ]
        selectable = [i for i, (_title, fn) in enumerate(items) if fn]
        cur = selectable[0]
        while True:
            albums = self.load()
            w, h = tui.size()

            if self.library_ok():
                s = library.stats(albums)
                lines = tui.header(
                    _("MUSIC UTILITY"),
                    _("{albums} albums · {tracks} tracks").format(
                        albums=len(albums), tracks=sum(a['tracks'] for a in albums)), w)
                lines.append("")
                parts = (("active", "green", _("Active"), _("-> to the iPod")),
                         ("archive", "yellow", _("Archive"), _("-> stays on disk")))
                nw = max(len(p[2]) for p in parts)
                for key, colour, name, where in parts:
                    lines.append(
                        f"  {FG[colour]}{name.ljust(nw)}{RESET} "
                        + _("{albums:3d} alb.  {tracks:5d} tr.  {gb:5.1f} GB").format(
                            albums=s[key]['albums'], tracks=s[key]['tracks'], gb=s[key]['gb'])
                        + f"  {FG['grey']}{where}{RESET}")
                if s["no_art"]:
                    lines.append(f"  {FG['grey']}"
                                 + _("without cover art: {n} tracks").format(n=s['no_art'])
                                 + RESET)
            else:
                lines = tui.header(_("MUSIC UTILITY"), _("library folder not set"), w)
                lines.append("")
                lines.append(f"  {FG['yellow']}"
                             + _("The library folder isn't set or doesn't exist. Open Settings.")
                             + RESET)
            lines.append("")

            for i, (label, fn) in enumerate(items):
                if fn is None:
                    lines.append(f"  {FG['grey']}{label}{RESET}" if label else "")
                elif i == cur:
                    lines.append(f"    {INV} {label} {RESET}")
                else:
                    lines.append(f"     {label}")

            lines.append("")
            lines += tui.footer([("↑↓", _("select")), ("Enter", _("open")), ("q", _("quit"))], w)
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

            sub = (_("Active {active} · Archive {archive}").format(active=n_act, archive=n_arc)
                   + (f" · {FG['magenta']}" + _("unsaved: {n}").format(n=changed)
                      + f"{RESET}{FG['grey']}" if changed else ""))
            lines = tui.header(_("MARKUP"), sub, w)
            lines.append(f"  {_('filter:')} {BOLD}{_(name)}{RESET}"
                         + (f"   {_('search:')} {BOLD}{query}{RESET}" if query else "")
                         + f"   {FG['grey']}"
                         + _("({shown} of {total})").format(shown=len(rows), total=len(albums))
                         + RESET)
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
                meta = _("{tracks:3d} tr. {mb:5.0f} MB").format(tracks=a['tracks'],
                                                             mb=a['bytes'] / 1024 / 1024)
                flags = ("" if not a["ua"] else f" {FG['cyan']}UA{RESET}")
                flags += ("" if not a["no_art"] else f" {FG['red']}!art{RESET}")

                avail = w - 26
                line = f" {badge} {tui.pad(title, avail)} {FG['grey']}{meta}{RESET}{flags}"
                lines.append(f"{INV}{line}{RESET}" if i == cur else line)

            lines.append("")
            lines += tui.footer([
                ("↑↓", _("select")), ("A/R", _("mark")), (_("Space"), _("toggle")),
                ("f", _("filter")), ("/", _("search")), ("s", _("save")), ("Esc", _("back")),
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
                query = tui.prompt(_("Search (empty to clear): "))
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
                        _("{n} unsaved changes. Leave and lose them?").format(n=changed)):
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
        print("\n".join(tui.header(_("SAVING"), _("albums to move: {n}").format(n=len(todo)))))
        print()
        for a in todo[:20]:
            arrow = (_("-> Archive") if self.state_of(a) == "R" else _("-> Active "))
            print(f"  {arrow}  {a['artist']} — {a['album']}  "
                  + _("({n} tr.)").format(n=a['tracks']))
        if len(todo) > 20:
            print(_("  ... {n} more").format(n=len(todo) - 20))

        if not tui.confirm(_("Move the files?")):
            return False

        print()
        for n, a in enumerate(todo, 1):
            library.move(a, self.marks[a["path"]])
            print("\r" + _("  moved {n}/{total}").format(n=n, total=len(todo)), end="", flush=True)
        library.prune_empty()
        self.marks.clear()
        self.albums = library.scan()
        print(_("\n\n  Done: {n} albums.").format(n=len(todo)))
        tui.pause()
        return True

    # ------------------------------------------------------------ library tools

    def screen_incoming(self):
        tui.clear()
        print("\n".join(tui.header(_("ADD NEW TRACKS"), _("where to take them from"))))
        default = settings.resolve(self.cfg.get("incoming_dir")) or ""
        print(_("\n  Default (from Settings): {path}").format(path=f"{FG['grey']}{default}{RESET}"))
        path = tui.prompt(_("Folder (Enter for default): ")) or default
        if not os.path.isdir(path):
            print(f"\n  {FG['red']}" + _("No such folder: {path}").format(path=path) + RESET)
            tui.pause()
            return

        target = self.cfg.get("new_tracks_target", "active")
        other = "archive" if target == "active" else "active"
        tui.clear()
        names = {"active": _("Active"), "archive": _("Archive")}
        print("\n".join(tui.header(_("WHERE TO PUT THEM"))))
        print(_("""
  {b}Active{r}  — goes to the iPod on the next sync.
  {b}Archive{r} — set aside; reaches the iPod only once you mark it [A].

  Default from Settings: {b}{target}{r}
""").format(b=BOLD, r=RESET, target=names[target]))
        if tui.confirm(_("Put them into {other} this time instead?").format(other=names[other])):
            target = other

        # the dry run doubles as the inspection of the incoming files
        added = self.ask_apply(_("Adding"), "add_incoming.py", [path, "--to", target])
        if added and target == "active":
            tui.clear()
            print("\n".join(tui.header(_("Adding"))))
            if tui.confirm(_("The new tracks are in Active. Sync the iPod now?")):
                self.screen_sync()

    def screen_sync(self):
        tui.clear()
        print("\n".join(tui.header(_("SYNC THE IPOD"),
                                   _("the device should hold exactly what's in Active"))))
        print(_("""
  {b}1. Sync{r} {g}(Enter){r}
     Archive tracks leave the iPod, new Active tracks arrive, covers and
     genres come from the files. Matching by tags and duration; only
     tracks positively recognised as archive are deleted. The iPod's
     database is written by podsync — no iTunes (it must be closed).
     A backup of the database is made before every write.

  {b}2. What's on the iPod{r}
     Model, tracks, playlists, tracks the screen shows without a cover.

  {b}3. Save from the iPod{r}
     Tracks that are on the iPod but in neither Active nor Archive — the
     iPod may hold the only copy. They're copied into the incoming folder,
     then "Add new tracks" brings them into the library.

  {b}4. Mirror to disk{r} {g}(Rockbox or disk mode){r}
     Copy with removal of extras. Only the subfolder the script manages
     is touched.

  {b}5. Restore the last backup{r}
     Puts the iPod's database back as it was before the last sync.

  {b}6. Eject{r}
     Flushes and ejects the iPod, so it can be unplugged.
""").format(b=BOLD, r=RESET, g=FG['grey']))
        print(_("  {b}1{r} sync   {b}2{r} what's on it   {b}3{r} save from it   "
                "{b}4{r} disk   {b}5{r} restore   {b}6{r} eject   "
                "{b}Esc{r} back").format(b=BOLD, r=RESET))
        tui.flush()
        while True:
            k = tui.read_key()
            if k == tui.ESCAPE:
                return
            if k in ("1", tui.ENTER):
                # One clear question here instead of the script asking
                # again in its own "type yes" style.
                if self.ask_apply(
                        _("iPod sync"), "ipod_sync.py",
                        question=_("Apply: back up the iPod's database, then delete the archive "
                                   "tracks, copy new ones, set covers and genres?"),
                        apply_extra=["--yes"]):
                    tui.clear()
                    print("\n".join(tui.header(_("iPod sync"))))
                    if tui.confirm(_("Eject the iPod now?")):
                        self.run_tool(_("Eject the iPod"), "ipod.py", ["--eject"])
                return
            if k == "2":
                self.run_tool(_("What's on the iPod"), "ipod.py")
                return
            if k == "3":
                if self.ask_apply(_("Save from the iPod"), "ipod_sync.py", ["--rescue"],
                                  question=_("Copy these tracks off the iPod into the incoming folder?")):
                    tui.clear()
                    print("\n".join(tui.header(_("Save from the iPod"))))
                    if tui.confirm(_("Add them to the library now?")):
                        self.screen_incoming()
                return
            if k == "4":
                drive = tui.prompt(_("Device drive or mount point (E: or /Volumes/NAME): ")).strip()
                if not drive:
                    return
                self.ask_apply(
                    _("Mirror to disk"), "ipod_sync.py", ["--disk", drive],
                    question=_("Apply: copy new files and DELETE the extras on the device "
                               "(can't be undone)?"),
                    apply_extra=["--yes"])
                return
            if k == "5":
                tui.clear()
                print("\n".join(tui.header(_("Restore the last backup"))))
                if tui.confirm(_("Replace the iPod's database with the latest backup?")):
                    self.run_tool(_("Restore the last backup"), "ipod_sync.py", ["--restore", "--yes"])
                return
            if k == "6":
                self.run_tool(_("Eject the iPod"), "ipod.py", ["--eject"])
                return

    def screen_vibe(self):
        tui.clear()
        print("\n".join(tui.header(_("AI VIBE PLAYLIST"), _("describe a mood, get a playlist"))))
        print(_("""
  Describe it in your own words, in any language:
    {g}rainy night drive, slow, a bit sad
    loud and fast for the gym
    morning coffee, something light{r}

  Claude picks and orders tracks from Active, using what it knows about
  the songs plus tempo/energy measured from the audio (the first run
  analyses the whole library, a few minutes; after that only new tracks).
  The result is saved as an .m3u8 playlist and can go onto the iPod.
  Goes through Claude Code on a Claude subscription, or Google Gemini
  with a free key (aistudio.google.com -> GEMINI_API_KEY), or the paid
  Anthropic API — see Settings -> AI.
""").format(g=FG['grey'], r=RESET))
        vibe = tui.prompt(_("Vibe (empty to go back): ")).strip()
        if not vibe:
            return
        default = self.cfg.get("vibe_count", 25)
        count = tui.prompt(_("About how many tracks (Enter = {n}): ").format(n=default)).strip()
        args = [vibe] + (["--count", count] if count.isdigit() else [])
        # The pick is made once and saved; confirming sends that same pick
        # rather than asking the AI again for a different one.
        if self.run_tool(_("AI vibe playlist"), "vibe.py", args):
            return   # no pick this time — don't offer to send an older one
        tui.clear()
        print("\n".join(tui.header(_("AI VIBE PLAYLIST"))))
        if tui.confirm(_("Create this playlist on the iPod? (written into its database; "
                         "picked tracks not on it yet are copied, nothing is deleted)")):
            self.run_tool(_("AI vibe playlist — to the iPod"), "vibe.py", ["--push-last"])

    def screen_genres(self):
        path = settings.path("genres_file", self.cfg)
        while True:
            tui.clear()
            print("\n".join(tui.header(_("GENRES"), _("genre + precise style per album"))))
            print(_("""
  {b}Genre{r}    — broad (Rock, Hip-Hop, Electronic...): the iPod's Genres menu.
  {b}Grouping{r} — the precise style (Hyperpop, Cloud Rap...): read by the AI
             playlists, doesn't clutter the iPod menu.

  {b}1{r} Suggest   the AI fills in albums not yet in the file
  {b}2{r} Edit      open the file and correct what's wrong
  {b}3{r} Apply     write the genres into the tags (dry run first)

  {g}File: {path}
  Your corrections are never overwritten; the next sync updates the iPod.{r}

  {b}Esc{r} back""").format(b=BOLD, r=RESET, g=FG['grey'], path=path))
            tui.flush()
            k = tui.read_key()
            if k == tui.ESCAPE:
                return
            if k == "1":
                self.run_tool(_("Genres — suggest"), "genres.py", ["suggest"])
            elif k == "2":
                if os.path.isfile(path):
                    settings.open_path(path)
                else:
                    print(f"\n  {FG['red']}" + _("No file yet — run Suggest first.") + RESET)
                    tui.pause()
            elif k == "3":
                self.ask_apply(_("Genres"), "genres.py", ["apply"],
                               question=_("Write these genres into the tags?"))

    def screen_missing(self):
        tui.clear()
        print("\n".join(tui.header(_("WHAT'S MISSING"), _("comparing likes with the library"))))
        print(_("""
  You need a list of what you listen to. Any of these works:

  {b}1. Spotify data export{r} {g}(works without Premium){r}
     spotify.com -> Privacy Settings -> Download your data.
     The email arrives within a few days. You need YourLibrary.json.

  {b}2. An Apple Music playlist page{r}
     Open the playlist in the browser and save the page as .html.

  {b}3. A plain text list{r}
     One album per line: Artist — Album

  A folder with several such files works too.
""").format(b=BOLD, r=RESET, g=FG['grey']))
        path = tui.prompt(_("Path to the file or folder (Enter to cancel): "))
        if not path:
            return
        if not os.path.exists(path):
            print(f"\n  {FG['red']}" + _("Not found") + RESET)
            tui.pause()
            return
        self.run_tool(_("Importing likes"), "import_likes.py", [path])
        self.run_tool(_("What's missing"), "find_missing.py")

    def export_file(self):
        albums = self.load()
        path = os.path.join(settings.path("reports_dir", self.cfg), "split.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        library.export_list(albums, path)
        tui.clear()
        print("\n".join(tui.header(_("EXPORT"))))
        print(_("\n  List written: {path}").format(path=f"{BOLD}{path}{RESET}"))
        print(f"\n  {FG['grey']}" + _("Letters are filled in from the current state."))
        print(_("  Edit it and come back — to apply:"))
        print(f"  python src/library.py --import \"{path}\" --apply{RESET}")
        tui.pause()

    def open_window(self):
        """Start the PyQt6 window as its own process; this menu stays usable."""
        try:
            import PyQt6  # noqa: F401
        except ImportError:
            tui.clear()
            print("\n".join(tui.header(_("THE WINDOW"))))
            print(_("\n  PyQt6 isn't installed. Install it with:"))
            print(f"  {sys.executable} -m pip install -r requirements.txt")
            tui.pause()
            return
        exe = sys.executable
        windowed = os.path.join(os.path.dirname(exe), "pythonw.exe")
        subprocess.Popen([windowed if os.path.isfile(windowed) else exe,
                          os.path.join(HERE, "Main.py"), "--gui"], cwd=HERE)

    # ------------------------------------------------------------ settings

    def edit_field(self, values, f):
        """Ask for a new value of one setting. Returns True if it changed."""
        cur = values.get(f.key)
        if f.kind == "choice":
            i = f.options.index(cur) if cur in f.options else -1
            values[f.key] = f.options[(i + 1) % len(f.options)]
            return True
        print(f"\n\n {BOLD}{_(f.label)}{RESET}")
        print(f" {FG['grey']}{_(f.help)}{RESET}")
        print(_(" current: {value}").format(value=cur if cur not in (None, '') else _('(empty)')))
        hint = _("Enter keeps it, '-' clears it") if f.optional else _("Enter keeps it")
        raw = tui.prompt(_("New value ({hint}): ").format(hint=hint))
        if raw == "":
            return False
        if raw == "-" and f.optional:
            raw = ""
        val, err = settings.validate(f.key, raw)
        if err:
            print(f"\n {FG['red']}" + _("Not saved: {error}").format(error=err) + RESET)
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
            lines = tui.header(_("SETTINGS"), settings.FILE, w)
            section = None
            lw = min(max(len(_(f.label)) for f in fields), w // 2)
            for i, f in enumerate(fields):
                if f.section != section:
                    section = f.section
                    lines.append(f"  {FG['grey']}{_(section).upper()}{RESET}")
                v = values.get(f.key)
                shown = str(v) if v not in (None, "") else _("(empty)")
                if f.kind == "choice":
                    shown = f"{v}   {FG['grey']}[{' / '.join(f.options)}]{RESET}"
                row = f"   {tui.pad(_(f.label), lw)} {tui.fit(shown, max(10, w - lw - 8))}"
                lines.append(f"{INV}{row}{RESET}" if i == cur else row)

            f = fields[cur]
            lines.append("")
            lines.append(f"  {FG['grey']}{tui.fit(_(f.help), w - 4)}{RESET}")
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
                ("↑↓", _("select")), ("Enter", _("edit / switch")), ("s", _("save")),
                ("Esc", _("back")),
            ] + ([("", f"{FG['magenta']}{_('unsaved changes')}{RESET}")] if dirty else []), w)
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
                if dirty and not tui.confirm(_("Leave without saving the changes?")):
                    continue
                return

    def first_run(self):
        """Ask for the basic settings on the very first start."""
        values = settings.defaults()
        tui.clear()
        print("\n".join(tui.header(_("MUSIC UTILITY — FIRST RUN"), _("a few basic settings"))))
        print(_("""
  Answer a few questions; everything can be changed later on the
  Settings screen. Press Enter to accept the value in [brackets].

  The answers are saved to {file}
  (git-ignored — personal paths don't end up in the repository).
""").format(file=settings.FILE))
        tui.show_cursor()
        for f in settings.FIELDS:
            if not f.first_run:
                continue
            print(f"\n {BOLD}{_(f.label)}{RESET}")
            print(f" {FG['grey']}{_(f.help)}{RESET}")
            if f.kind == "choice":
                print(f" {FG['grey']}" + _("options: {list}").format(list=', '.join(f.options)) + RESET)
            while True:
                default = values.get(f.key)
                shown = f" [{default}]" if default not in (None, "") else (
                    " [" + _("empty") + "]" if f.optional else "")
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
                        ans = input(_("   Folder doesn't exist. Create {path}? [y/n] ").format(path=p)).strip().lower()
                        if ans in ("y", "yes", "д", "да", _("y"), _("yes")):
                            os.makedirs(p, exist_ok=True)
                        else:
                            continue
                values[f.key] = val
                break

        settings.save(values)
        self.cfg = settings.load()
        print(f"\n {FG['green']}{_('Saved.')}{RESET}")
        for msg in settings.warnings(self.cfg):
            print(f" {FG['yellow']}! {msg}{RESET}")
        tui.hide_cursor()
        tui.pause()


def selftest():
    """Check the non-interactive parts without a console."""
    cfg = settings.load()
    if cfg is None:
        sys.exit(_("selftest needs settings.json — run Main.py once first"))
    app = App()
    albums = app.load()
    s = library.stats(albums)
    print(_("albums:   {n}").format(n=len(albums)))
    print(_("active:   {stats}").format(stats=s['active']))
    print(_("archive:  {stats}").format(stats=s['archive']))
    print(_("\nfilters:"))
    for name, pred in FILTERS:
        print(f"  {_(name)}: {sum(1 for a in albums if pred(a))}")
    if albums:
        print(_("\nmoving to the same place doesn't move files:"), end=" ")
        a = albums[0]
        before = a["path"]
        library.move(a, a["state"])
        print(_("ok") if a["path"] == before else _("ERROR"))
    print(_("\nsettings warnings:"))
    for msg in settings.warnings(cfg) or [_("(none)")]:
        print(f"  {msg}")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    if "--gui" in sys.argv:
        from gui.window import run
        sys.exit(run())
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
        print(_("Bye."))


if __name__ == "__main__":
    main()
