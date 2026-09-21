"""
The library: reading what's in it, and the Active / Archive split.

An album's state is simply which folder it sits in, not an entry in a
separate file. So re-listing after the library grows wipes nothing:
decisions already made are visible on disk.

    <library>\\Active\\<Artist>\\<Album>\\    -> goes to the iPod
    <library>\\Archive\\<Artist>\\<Album>\\   -> stays on disk

The library folder comes from the settings. Paths are looked up at call
time, not at import, so Main.py can import this module before the
first-run wizard has created the settings.

Used both by the TUI and from the command line:

    python src\\library.py                 # show contents
    python src\\library.py --export f.txt  # export for marking up
    python src\\library.py --import f.txt  # apply the markup
"""

import argparse
import os
import re
import shutil
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mutagen.id3 import ID3
from mutagen.mp3 import MP3

import settings
from musiclib import norm

# Letters that don't exist in Russian. A sign of the Ukrainian LANGUAGE,
# not of genre: folk can't be told apart from rock by tags, a human decides.
UA_LETTERS = set("іїєґІЇЄҐ")

# '[R]', '[ R]', '[R ]', '[ ]' — spaces around the letter don't matter.
# The separator is an em dash: titles only ever contain a short hyphen
# ('Minecraft - Volume Alpha'), so the two can't be confused.
LINE = re.compile(r"^\[\s*([ARar]?)\s*\]\s*(.+?)\s+—\s+(.+?)(?:\s{2,}\(.*)?$")


def paths(values=None):
    """(library, Active, Archive) from the settings."""
    return settings.library_paths(values)


def _one(tags, key):
    v = tags.get(key)
    return str(v.text[0]).strip() if v and v.text else None


def scan(root=None, with_audio=True):
    """Library contents: one record per album.

    Artist and album come from TAGS, not folder names: on disk AC/DC is
    stored as 'AC_DC', because a slash isn't allowed in a file name.
    """
    root = root or paths()[0]
    albums = []
    for path, dirs, files in os.walk(root):
        mp3 = sorted(f for f in files if f.lower().endswith(".mp3"))
        if not mp3:
            continue

        rel = os.path.relpath(path, root)
        head = rel.split(os.sep)[0]
        state = "R" if head == "Archive" else "A"

        artist = album = None
        size = 0
        years, genres, bitrates, texts = Counter(), Counter(), Counter(), []
        no_art = 0

        for fn in mp3:
            fp = os.path.join(path, fn)
            size += os.path.getsize(fp)
            try:
                tags = ID3(fp)
            except Exception:
                continue
            if artist is None:
                artist = _one(tags, "TPE2") or _one(tags, "TPE1") or "?"
                album = _one(tags, "TALB") or os.path.basename(path)
            if _one(tags, "TYER"):
                years[_one(tags, "TYER")] += 1
            if _one(tags, "TCON"):
                genres[_one(tags, "TCON")] += 1
            if not tags.getall("APIC"):
                no_art += 1
            texts += [artist, album, _one(tags, "TIT2")]
            if with_audio:
                try:
                    bitrates[int(MP3(fp).info.bitrate / 1000)] += 1
                except Exception:
                    pass

        albums.append({
            "path": path,
            "state": state,
            "artist": artist or "?",
            "album": album or os.path.basename(path),
            "tracks": len(mp3),
            "bytes": size,
            "year": years.most_common(1)[0][0] if years else "",
            "genre": genres.most_common(1)[0][0] if genres else "",
            "bitrates": dict(bitrates.most_common()),
            "no_art": no_art,
            "ua": any(UA_LETTERS & set(t) for t in texts if t),
        })

    albums.sort(key=lambda a: (a["artist"].lower(), a["album"].lower()))
    return albums


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
        "ua": sum(1 for a in albums if a["ua"]),
    }


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
    if os.path.exists(dest):
        # destination folder exists — move the files in and drop the empty one
        for fn in os.listdir(src):
            shutil.move(os.path.join(src, fn), os.path.join(dest, fn))
        os.rmdir(src)
    else:
        shutil.move(src, dest)

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
    ua = [a for a in albums if a["ua"]]
    rest = [a for a in albums if not a["ua"]]

    def block(rows):
        out = []
        for a in rows:
            meta = f"{a['tracks']} tr., {a['bytes'] / 1024 / 1024:.0f} MB"
            if a["year"]:
                meta += f", {a['year']}"
            if a["genre"]:
                meta += f", {a['genre']}"
            out.append(f"[{a['state']}] {a['artist']} — {a['album']}   ({meta})")
        return out

    lines = [
        "# MARKUP: [A] goes to the iPod, [R] stays in the archive.",
        "# Letters are pre-filled from the current state — change only what you need.",
        "# Lines starting with # are ignored.",
        "",
        f"# UKRAINIAN-LANGUAGE — {len(ua)} albums",
        "# Detected by the letters і/ї/є/ґ. This is about LANGUAGE, not genre:",
        "# folk, rock and rap are all mixed in here.",
        "",
    ]
    lines += block(ua)
    lines += ["", f"# EVERYTHING ELSE — {len(rest)} albums", ""]
    lines += block(rest)
    lines += ["", f"# TOTAL: {len(albums)} albums, "
                  f"{sum(a['tracks'] for a in albums)} tracks"]

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


def main():
    ap = argparse.ArgumentParser(description="library contents and the Active/Archive split")
    ap.add_argument("--export", metavar="FILE", help="export the list for marking up")
    ap.add_argument("--import", dest="imp", metavar="FILE", help="apply the markup from a file")
    ap.add_argument("--apply", action="store_true", help="with --import: actually move albums")
    args = ap.parse_args()

    root = paths()[0]
    if not os.path.isdir(root):
        sys.exit(f"Library not found: {root}")

    albums = scan()
    s = stats(albums)

    print(f"Albums: {len(albums)}   tracks: {sum(a['tracks'] for a in albums)}")
    for name, key in (("Active ", "active"), ("Archive", "archive")):
        p = s[key]
        print(f"  {name}: {p['albums']:3d} albums, {p['tracks']:4d} tracks, "
              f"{p['gb']:.1f} GB, {p['artists']} artists")
    if s["no_art"]:
        print(f"  without cover art: {s['no_art']} tracks")

    if args.export:
        export_list(albums, args.export)
        print(f"\nList for marking up: {args.export}")
        return

    if args.imp:
        marks, bad = parse_list(args.imp)
        for n, line in bad[:10]:
            print(f"  ! line {n} not understood: {line}")

        todo = [a for a in albums
                if marks.get((norm(a["artist"]), norm(a["album"])), a["state"]) != a["state"]]
        unknown = [a for a in albums
                   if (norm(a["artist"]), norm(a["album"])) not in marks]

        print(f"\nAlbums to move: {len(todo)}")
        for a in todo:
            arrow = "-> Archive" if marks[(norm(a["artist"]), norm(a["album"]))] == "R" else "-> Active"
            print(f"  {arrow}  {a['artist']} — {a['album']}  ({a['tracks']} tr.)")
        if unknown:
            print(f"\nNot in the markup, will stay where they are: {len(unknown)}")
            for a in unknown[:10]:
                print(f"  {a['artist']} — {a['album']}")

        if not args.apply:
            print("\nNothing moved. Add --apply.")
            return
        moved = apply_marks(albums, marks)
        print(f"\nMoved: {len(moved)}")


if __name__ == "__main__":
    main()
