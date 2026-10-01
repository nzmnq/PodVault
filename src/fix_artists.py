"""
Unglue artists already in the library: 'ONDA ANDAR, #keyoo' -> artist
'ONDA ANDAR', title '330 (feat. #keyoo)'. The same rules as adding tracks
(add_incoming.split_artists, artists.txt for names kept whole and aliases).

The album moves to the main artist's folder; singles join that artist's
'Singles' and are numbered on from it. Dry run unless --apply.

  python src/add_incoming.py --fix-library [--apply]
"""

import os
import shutil
from collections import defaultdict

import tags as audiotags
from add_incoming import SINGLES, safe_name, split_artists, with_feats
from i18n import _
from musiclib import norm


def plan(library, protect, alias):
    """{album folder: [(path, tags, new values)]} for the albums that change."""
    def spell(n):
        return alias.get(norm(n), n)

    import library as lib
    out = defaultdict(list)
    for fp, _rel, _size, _mtime, t in lib.listing(library):       # the index: no file opened
        root = os.path.dirname(fp)
        names = [spell(n) for n in split_artists(t["artist"], protect)] or [t["artist"]]
        aa = spell((split_artists(t["albumartist"], protect) or names)[0])
        new = {"artist": names[0], "albumartist": aa,
               "title": with_feats(t["title"], [n for n in names[1:] if norm(n) != norm(names[0])])}
        if any(new[k] != t[k] for k in new):
            out[root].append((fp, t, new))
    return out


def run(library, protect, alias, apply):
    changes = plan(library, protect, alias)
    if not changes:
        print(_("Every artist is already a single name."))
        return
    moves = 0
    for folder, items in sorted(changes.items()):
        rel = os.path.relpath(folder, library)
        side = rel.split(os.sep)[0]                     # Active / Archive
        album = os.path.basename(folder)
        aa = items[0][2]["albumartist"]
        target = os.path.join(library, side, safe_name(aa), album)
        print(f"\n{rel}")
        if os.path.normcase(target) != os.path.normcase(folder):
            print("  -> " + os.path.relpath(target, library))
            moves += 1
        for _fp, t, new in items:
            print(f"  {t['artist']} — {t['title']}")
            print(f"    {new['artist']} — {new['title']}")
        if apply:
            move_album(folder, target, items)
    print("\n" + _("Albums to fix: {n}, of them moved to another artist: {m}").format(n=len(changes), m=moves))
    if not apply:
        print(_("Nothing changed. Run with --apply to fix them."))


def move_album(folder, target, items):
    """Write the new tags; then, if the album belongs to another artist now, move it there."""
    for fp, _t, new in items:
        audiotags.write(fp, new)
    if os.path.normcase(target) == os.path.normcase(folder):
        return
    os.makedirs(target, exist_ok=True)
    singles = os.path.basename(target) == SINGLES
    have = len(audiotags.audio_files(os.listdir(target)))
    tracks = audiotags.audio_files(os.listdir(folder))
    all_art = all(audiotags.read(os.path.join(folder, fn))["art"] for fn in tracks)
    for n, fn in enumerate(tracks, have + 1):
        src = os.path.join(folder, fn)
        name = fn
        if singles:
            title = audiotags.read(src)["title"]
            audiotags.write(src, {"track": f"{n}/{have + len(tracks)}"})
            name = f"{n:02d} - {safe_name(title)}{audiotags.extension(src)}"
        dest, k = os.path.join(target, name), 1
        while os.path.exists(dest):
            k += 1
            base, ext = os.path.splitext(os.path.join(target, name))
            dest = f"{base} ({k}){ext}"
        shutil.move(src, dest)
    jpg = os.path.join(folder, "folder.jpg")
    if os.path.exists(jpg) and not os.path.exists(os.path.join(target, "folder.jpg")):
        shutil.move(jpg, os.path.join(target, "folder.jpg"))
    elif os.path.exists(jpg) and all_art and not audiotags.audio_files(os.listdir(folder)):
        os.remove(jpg)                  # every track carries this cover embedded: nothing is lost
    # only empty folders are removed
    for d in (folder, os.path.dirname(folder)):
        try:
            os.rmdir(d)
        except OSError:
            break
