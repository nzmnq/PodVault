"""
The library: reading what's in it, and the Active / Archive split.

An album's state is simply which folder it sits in, not an entry in a
separate file. So re-listing after the library grows wipes nothing:
decisions already made are visible on disk.

    <library>\\Active\\<Artist>\\<Album>\\    -> goes to the iPod
    <library>\\Archive\\<Artist>\\<Album>\\   -> stays on disk

The library folder comes from the settings. Paths are looked up at call
time, not at import, so this module can be imported before the
first-run screen has created the settings.

Used both by the window and from the command line:

    python src/library.py                 # show contents
    python src/library.py --export f.txt  # export for marking up
    python src/library.py --import f.txt  # apply the markup
    python src/library.py --undo          # show what the last batch of moves did
    python src/library.py --undo --apply  # put those albums back
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import zlib
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings
import tags as audiotags
from i18n import _
from musiclib import norm

# '[R]', '[ R]', '[R ]', '[ ]' — spaces around the letter don't matter.
# The separator is an em dash: titles only ever contain a short hyphen
# ('Minecraft - Volume Alpha'), so the two can't be confused.
LINE = re.compile(r"^\[\s*([ARar]?)\s*\]\s*(.+?)\s+—\s+(.+?)(?:\s{2,}\(.*)?$")


def paths(values=None):
    """(library, Active, Archive) from the settings."""
    return settings.library_paths(values)


OLD_INDEX = ".podvault-index.json"      # where it was kept before: inside the library


def index_file(root):
    """The index of a library, kept with the app's data (settings.DATA), one per library
    folder: not on the library's own drive, which may be a slow network one."""
    import hashlib
    key = hashlib.sha1(os.path.abspath(root).encode("utf-8")).hexdigest()[:12]
    return os.path.join(settings.DATA, "cache", f"library-{key}.json")


def _load_index(root):
    for path in (index_file(root), os.path.join(root, OLD_INDEX)):   # the old one, once
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("v") == 1:
                return data
        except (OSError, ValueError, AttributeError):
            pass
    return {}


def _save_index(root, albums):
    """Best effort: without it the library just gets scanned in full every time."""
    path = index_file(root)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"v": 1, "albums": albums}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(path + ".tmp", path)
    except OSError:
        pass


INDEX_REC = 2       # what a record holds; an older one is read again once (scan)


def _read_album(path, audio):
    """An album's record: its own facts and, per track, what the rest of the program needs —
    so that nothing else has to open a file (on a slow drive that's what costs). Each file is
    read once (tags.summary)."""
    artist = album = None
    years, genres, bitrates = Counter(), Counter(), Counter()
    no_art = 0
    rows = []        # [file, artist, album artist, title, genre, style, length]
    for fn in audio:
        try:
            t = audiotags.summary(os.path.join(path, fn))
        except audiotags.Unreadable:
            continue
        rows.append([fn, t["artist"], t["albumartist"], t["title"], t["genre"], t["grouping"], t["length"]])
        if artist is None:
            artist = t["albumartist"] or t["artist"] or "?"
            album = t["album"] or os.path.basename(path)
        if t["year"]:
            years[t["year"]] += 1
        if t["genre"]:
            genres[t["genre"]] += 1
        if not t["art"]:
            no_art += 1
        bitrates[t["bitrate"]] += 1
    return {
        "artist": artist or "?",
        "album": album or os.path.basename(path),
        "year": years.most_common(1)[0][0] if years else "",
        "genre": genres.most_common(1)[0][0] if genres else "",
        "bitrates": dict(bitrates.most_common()),
        "no_art": no_art,
        "rows": rows,
        "v": INDEX_REC,
    }


def _look(path, known=None):
    """One folder: (subfolders, audio files, their [name, size, mtime], the folder's mtime).

    known: its index entry, for a quick look — when the folder's own time and its file
    names are what the index has, the files aren't looked at one by one (on a network
    drive that's one slow request each). A tag edited inside a file isn't seen that way:
    the folder's time changes when files come and go, not when one is written to.
    """
    with os.scandir(path) as it:
        entries = list(it)
    dirs = [e.path for e in entries if e.is_dir(follow_symlinks=False)]
    audio = audiotags.audio_files(e.name for e in entries if e.is_file())
    mtime = int(os.stat(path).st_mtime) if audio else 0
    if known and known.get("dir") == mtime and [s[0] for s in known["sig"]] == audio:
        return dirs, audio, known["sig"], mtime
    sig = []
    for fn in audio:
        st = os.stat(os.path.join(path, fn))
        sig.append([fn, st.st_size, int(st.st_mtime)])
    return dirs, audio, sig, mtime


def _album(root, rel, sig, rec):
    return {
        **{k: v for k, v in rec.items() if k not in ("rows", "v")},
        "bitrates": {int(k): v for k, v in rec["bitrates"].items()},   # JSON keys are strings
        "path": os.path.normpath(os.path.join(root, rel)),
        "state": "R" if rel.split("/")[0] == "Archive" else "A",
        "tracks": len(sig),
        "bytes": sum(s[1] for s in sig),
        # changes when any of its files does (an embedded cover too): the window's kept
        # cover thumbnails go by it, without asking the (maybe network) drive again
        "rev": f"{zlib.crc32(json.dumps(sig).encode()):08x}",
    }


def _sorted(albums):
    return sorted(albums, key=lambda a: (a["artist"].lower(), a["album"].lower()))


def indexed_tags(root=None):
    """('A' | 'R', artist, album artist, title) of every track the index knows, without
    opening a file; albums whose record is from an older index are skipped (see scan)."""
    root = root or paths()[0]
    for rel, e in _load_index(root).get("albums", {}).items():
        state = "R" if rel.split("/")[0] == "Archive" else "A"
        for row in e["rec"].get("rows", []):
            yield state, row[1], row[2], row[3]


def cached(root=None):
    """The library as the index last saw it, without looking at a single file: shown at
    once while scan() checks the drive. [] when there's no index yet."""
    root = root or paths()[0]
    old = _load_index(root).get("albums", {})
    return _sorted(_album(root, rel, e["sig"], e["rec"]) for rel, e in old.items())


def scan(root=None, progress=None, quick=False):
    """Library contents: one record per album.

    Artist and album come from TAGS, not folder names: on disk AC/DC is
    stored as 'AC_DC', because a slash isn't allowed in a file name.

    Reading tags is the slow part (very slow on a network drive), so what
    was read is kept in the app's data (index_file): an album whose files
    have the same names, sizes and times is taken from there. A moved album
    is recognized by its files too. progress(done, total, seconds_left)
    is called along the way: total 0 while the folders are still being
    listed (done = albums found so far); seconds_left is None until known.

    quick: trust an album folder whose own time and file names haven't changed (see _look):
    the window's check at start. Without it every file is looked at, which also notices a
    tag written inside a file — after a tool has changed tags, and "Read the library again".
    """
    global _expected
    root = root or paths()[0]
    old = _load_index(root).get("albums", {})
    _expected = len(old)
    by_sig = {json.dumps(e["sig"]): e for e in old.values()}

    # 1. list the folders: names, sizes and times, no file is opened. On a network drive this
    # is what takes the time, which is why the window shows cached() first and runs this in
    # the background. A level's folders are listed in parallel: one by one is a network round
    # trip each, waited out in turn (a Time Capsule, cold: 15-20 s for 415 folders, 6 s so).
    from concurrent.futures import ThreadPoolExecutor
    found, level = [], [root]
    pool = ThreadPoolExecutor(16)
    while level:
        nxt = []
        rels = [os.path.relpath(path, root).replace(os.sep, "/") for path in level]
        looks = pool.map(lambda p, r: _look(p, old.get(r) if quick else None), level, rels)
        for path, rel, (dirs, audio, sig, mtime) in zip(level, rels, looks):
            nxt += dirs
            if not audio:
                continue
            hit = old.get(rel)
            if not hit or hit["sig"] != sig:
                hit = by_sig.get(json.dumps(sig))
            # a record from an older version of the index: read once more, then never again
            # a file that couldn't be read (the drive didn't answer, still being copied) is
            # tried again: kept as it was, its album showed as '?' until a file changed
            ok = (hit and hit["rec"].get("v") == INDEX_REC
                  and len(hit["rec"].get("rows", [])) == len(audio))
            found.append([path, rel, audio, sig, hit["rec"] if ok else None, mtime])
            if progress:
                progress(len(found), 0, None)
        level = sorted(nxt)
    pool.shutdown()

    # 2. read the tags of what's new or changed
    todo = [f for f in found if f[4] is None]
    started = saved = time.time()
    if progress and todo:
        progress(0, len(todo), None)
    for done, f in enumerate(todo, 1):
        f[4] = _read_album(f[0], f[2])
        if progress:
            spent = time.time() - started
            progress(done, len(todo), spent / done * (len(todo) - done) if done >= 3 else None)
        if time.time() - saved > 30:    # an interrupted first scan isn't lost
            _save_index(root, {**old, **{g[1]: {"sig": g[3], "rec": g[4], "dir": g[5]} for g in found if g[4]}})
            saved = time.time()

    index = {rel: {"sig": sig, "rec": rec, "dir": mtime} for path, rel, audio, sig, rec, mtime in found}
    if todo or index != old:
        _save_index(root, index)
    return _sorted(_album(root, rel, sig, rec) for path, rel, audio, sig, rec, _m in found)


ROW_FIELDS = ("artist", "albumartist", "title", "genre", "grouping", "length")


def tracks(root=None, fresh=True, quick=False, progress=None):
    """Every track of the library: [(path, album folder relative to root, size, mtime, tags)],
    tags: {artist, albumartist, title, genre, grouping, length} — from the index, no file
    opened here (the scan opens the new and changed ones; on a slow drive that's the cost).

    fresh: list the drive first, the way scan() does; quick as there. Without it: the index
    as it is — for the window, which keeps it fresh itself. A file whose tags this process
    wrote (tags.WRITTEN) is read again: the index has them from before.
    """
    root = root or paths()[0]
    if fresh:
        scan(root, quick=quick, progress=progress)
    out = []
    for rel, e in _load_index(root).get("albums", {}).items():
        rows = {r[0]: dict(zip(ROW_FIELDS, r[1:])) for r in e["rec"].get("rows", [])}
        for fn, size, mtime in e["sig"]:
            path = os.path.normpath(os.path.join(root, rel, fn))
            t = rows.get(fn)
            if path in audiotags.WRITTEN or t is None:
                try:
                    s = audiotags.summary(path)
                except audiotags.Unreadable:
                    continue
                t = {k: s[k] for k in ROW_FIELDS}
            out.append((path, rel, size, mtime, t))
    return out


def album_info(root=None):
    """{album folder relative to root: its record without the rows} — album name, no_art...
    from the index as it is: call tracks() or scan() first for a fresh one."""
    root = root or paths()[0]
    return {rel: {k: v for k, v in e["rec"].items() if k != "rows"}
            for rel, e in _load_index(root).get("albums", {}).items()}


def listing(root=None, quick=False):
    """tracks() for a tool: the drive listed, progress on the command line. When the window
    runs a tool's --apply right after its dry run, it sets PODVAULT_QUICK: the dry run has
    just looked at every file, so the folders' own times are trusted (see scan's quick)."""
    global _open
    quick = quick or os.environ.get("PODVAULT_QUICK") == "1"
    out = tracks(root, fresh=True, quick=quick, progress=_cli_progress)
    if _open:               # the listing ended mid-line: what comes next starts on its own
        print(flush=True)
        _open = False
    return out


def forget(written):
    """Their tags were written (tags.WRITTEN): the albums holding these files are read again
    by the next scan, quick or not — a tag rewritten in place can keep a file's size and time."""
    try:
        root = paths()[0]
    except Exception:           # no settings: nothing indexed either
        return
    albums = _load_index(root).get("albums", {})
    hit = {os.path.relpath(os.path.dirname(p), root).replace(os.sep, "/") for p in written} & set(albums)
    for rel in hit:
        albums[rel]["rec"]["v"] = 0
        albums[rel].pop("dir", None)
    if hit:
        _save_index(root, albums)


def stats(albums):
    def part(state):
        g = [a for a in albums if a["state"] == state]
        return {
            "albums": len(g),
            "tracks": sum(a["tracks"] for a in g),
            "gb": sum(a["bytes"] for a in g) / 1024 ** 3,
            "artists": len({a["artist"] for a in g}),
        }
    return {
        "active": part("A"),
        "archive": part("R"),
        "no_art": sum(a["no_art"] for a in albums),
    }


_batch = None


def begin_batch():
    """Moves made from now on are undone together."""
    global _batch
    _batch = time.strftime("%Y-%m-%d %H:%M:%S")


def journal_file():
    return os.path.join(settings.path("reports_dir"), "moves.jsonl")


def _journal(entry):
    global _batch
    if _batch is None:
        begin_batch()
    f = journal_file()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"batch": _batch, **entry}, ensure_ascii=False) + "\n")


def _read_journal():
    try:
        with open(journal_file(), encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
    except FileNotFoundError:
        return []


def undo(apply=False):
    """Reverse the last batch of moves. Returns (undone, skipped): lists of journal entries."""
    rows = _read_journal()
    if not rows:
        return [], []
    last = rows[-1]["batch"]
    batch = [r for r in rows if r["batch"] == last]
    undone, skipped = [], []
    for r in reversed(batch):
        # a merged move mixed the files with ones already there: not reversible
        if r["merged"] or not os.path.isdir(r["dest"]) or os.path.exists(r["src"]):
            skipped.append(r)
            continue
        if apply:
            os.makedirs(os.path.dirname(r["src"]), exist_ok=True)
            shutil.move(r["dest"], r["src"])
        undone.append(r)
    if apply:
        rest = [r for r in rows if r["batch"] != last] + [r for r in batch if r in skipped]
        with open(journal_file(), "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rest)
        prune_empty()
    return undone, skipped


def move(album, target):
    """Move an album to Active or Archive. Returns the new path.

    Safe to call repeatedly: if the album is already there, nothing happens.
    """
    if album["state"] == target:
        return album["path"]

    root, active, archive = paths()
    dest_root = archive if target == "R" else active
    src = album["path"]
    rel = os.path.relpath(src, root)
    rel = re.sub(r"^(Active|Archive)[\\/]", "", rel)
    dest = os.path.join(dest_root, rel)

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    merged = os.path.exists(dest)
    if merged:
        # destination folder exists — move the files in and drop the empty one
        for fn in os.listdir(src):
            shutil.move(os.path.join(src, fn), os.path.join(dest, fn))
        os.rmdir(src)
    else:
        shutil.move(src, dest)
    _journal({"src": src, "dest": dest, "merged": merged, "album": f"{album['artist']} — {album['album']}"})

    album["path"], album["state"] = dest, target
    return dest


def prune_empty(root=None):
    """Remove artist folders that became empty."""
    lib, active, archive = paths()
    root = root or lib
    removed = 0
    for path, dirs, files in os.walk(root, topdown=False):
        if path in (root, active, archive):
            continue
        try:
            if not os.listdir(path):
                os.rmdir(path)
                removed += 1
        except OSError:
            pass
    return removed


def apply_marks(albums, marks):
    """Apply a markup {(norm(artist), norm(album)): 'A'|'R'}."""
    moved = []
    begin_batch()
    for a in albums:
        target = marks.get((norm(a["artist"]), norm(a["album"])))
        if target and target != a["state"]:
            move(a, target)
            moved.append(a)
    prune_empty()
    return moved


# ------------------------------------------------------- text exchange


def export_list(albums, path):
    """Export the contents to a text file for marking up by hand.

    The current state is filled in right away, so decisions already made
    are not lost.
    """
    def block(rows):
        out = []
        for a in rows:
            meta = _("{tracks} tr., {mb} MB").format(tracks=a['tracks'], mb=f"{a['bytes'] / 1024 / 1024:.0f}")
            if a["year"]:
                meta += f", {a['year']}"
            if a["genre"]:
                meta += f", {a['genre']}"
            out.append(f"[{a['state']}] {a['artist']} — {a['album']}   ({meta})")
        return out

    # the comment lines are for the reader: parse_list skips everything after '#'
    lines = [
        "# " + _("MARKUP: [A] goes to the iPod, [R] stays in the archive."),
        "# " + _("Letters are pre-filled from the current state — change only what you need."),
        "# " + _("Lines starting with # are ignored."),
        "",
    ]
    lines += block(albums)
    lines += ["", "# " + _("TOTAL: {albums} albums, {tracks} tracks").format(
        albums=len(albums), tracks=sum(a['tracks'] for a in albums))]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def parse_list(path):
    """Read a marked-up file. Returns (marks, unparsed lines)."""
    marks, bad = {}, []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = LINE.match(s)
            if not m:
                bad.append((n, s))
                continue
            marks[(norm(m.group(2)), norm(m.group(3)))] = m.group(1).upper() or "A"
    return marks, bad


# -------------------------------------------------------------- CLI


_expected = 0       # albums the last scan found: while listing, how far along it is
_shown_at = 0.0     # when the progress line was last drawn
_open = False       # a progress line drawn without its newline yet


def _cli_progress(done, total, left):
    """scan()'s progress on the command line (and in the window's log, whose bar reads
    the 'n/total' in it)."""
    global _shown_at, _open
    # drawn by the clock, not every N albums: every 20 it stood still for 15 s on a slow
    # drive (an album takes nearly a second there) and looked like 20 being read at a time
    now = time.time()
    due = now - _shown_at >= .3 or (total and done == total)
    if not due:
        return
    _shown_at = now
    if not total and _expected:
        # listing the folders: the count isn't known yet, last time's is close enough
        n = max(done, _expected)
        print("\r" + _("  folders listed: {n}/{total} albums").format(n=done, total=n)
              + f"  · {100 * done // n}%", end="", flush=True)
        _open = True
        return
    if total:
        text = _("  tags read: {n}/{total} albums").format(n=done, total=total) + f"  · {100 * done // total}%"
        print("\r" + text, end="\n" if done == total else "", flush=True)
        _open = done != total
    else:
        print("\r" + _("  folders listed: {n} albums").format(n=done), end="", flush=True)
        _open = True


def main():
    ap = argparse.ArgumentParser(description=_("library contents and the Active/Archive split"))
    ap.add_argument("--export", metavar="FILE", help=_("export the list for marking up"))
    ap.add_argument("--import", dest="imp", metavar="FILE", help=_("apply the markup from a file"))
    ap.add_argument("--undo", action="store_true", help=_("take back the last batch of moves"))
    ap.add_argument("--apply", action="store_true", help=_("with --import / --undo: actually move albums"))
    args = ap.parse_args()

    root = paths()[0]
    if not os.path.isdir(root):
        sys.exit(_("Library not found: {folder}").format(folder=root))

    if args.undo:
        undone, skipped = undo(args.apply)
        if not undone and not skipped:
            print(_("Nothing to undo."))
        for r in undone:
            print("  " + _("back: {album}").format(album=r["album"]))
        for r in skipped:
            settings.problem(_("cannot undo (merged into an existing folder or already changed): {album}")
                             .format(album=r["album"]))
        if undone and not args.apply:
            print(_("\nNothing moved. Add --apply."))
        elif undone:
            print(_("\nPut back: {n}").format(n=len(undone)))
        return

    print(_("Reading the library: {folder}").format(folder=root))
    albums = scan(progress=_cli_progress)
    s = stats(albums)

    print(_("Albums: {albums}   tracks: {tracks}").format(albums=len(albums),
                                                        tracks=sum(a['tracks'] for a in albums)))
    names = {"active": _("Active"), "archive": _("Archive")}
    width = max(len(n) for n in names.values())
    for key, name in names.items():
        p = s[key]
        print(f"  {name.ljust(width)}: " + _("{albums:3d} albums, {tracks:4d} tracks, {gb} GB, {artists} artists")
              .format(albums=p['albums'], tracks=p['tracks'], gb=f"{p['gb']:.1f}", artists=p['artists']))
    if s["no_art"]:
        print(_("  without cover art: {n} tracks").format(n=s['no_art']))

    if args.export:
        export_list(albums, args.export)
        print(_("\nList for marking up: {file}").format(file=args.export))
        return

    if args.imp:
        marks, bad = parse_list(args.imp)
        for n, line in bad[:10]:
            settings.problem(_("line {n} not understood: {line}").format(n=n, line=line))

        todo = [a for a in albums
                if marks.get((norm(a["artist"]), norm(a["album"])), a["state"]) != a["state"]]
        unknown = [a for a in albums
                   if (norm(a["artist"]), norm(a["album"])) not in marks]

        print(_("\nAlbums to move: {n}").format(n=len(todo)))
        for a in todo:
            arrow = _("-> Archive") if marks[(norm(a["artist"]), norm(a["album"]))] == "R" else _("-> Active")
            print(f"  {arrow}  {a['artist']} — {a['album']}  " + _("({n} tr.)").format(n=a['tracks']))
        if unknown:
            print(_("\nNot in the markup, will stay where they are: {n}").format(n=len(unknown)))
            for a in unknown[:10]:
                print(f"  {a['artist']} — {a['album']}")

        if not args.apply:
            print(_("\nNothing moved. Add --apply."))
            return
        moved = apply_marks(albums, marks)
        print(_("\nMoved: {n}").format(n=len(moved)))


if __name__ == "__main__":
    settings.run(main)
