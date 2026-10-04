"""
Unglue artists already in the library: 'ONDA ANDAR, #keyoo' -> artist
'ONDA ANDAR', title '330 (feat. #keyoo)'. The same rules as adding tracks
(add_incoming.split_artists, artists.txt for names kept whole and aliases).

The album moves to the main artist's folder. Dry run unless --apply.

  python src/add_incoming.py --fix-library [--apply]

split_singles() takes the old 'Singles' albums apart: every single becomes an
album of its own, named after the track (see add_incoming.single_album).

  python src/add_incoming.py --split-singles [--apply]
"""

import os
import shutil
from collections import defaultdict

import tags as audiotags
from add_incoming import SINGLES, safe_name, split_artists, with_feats
from i18n import _
from musiclib import norm, strip_feat


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


def rename_genre_lines(genres_file, moves):
    """The genres file keys albums by 'Artist folder/Album folder': a moved album keeps its
    line under the new key; when the new folder already has one (singles merged), the old
    line goes."""
    if not genres_file or not os.path.exists(genres_file) or not moves:
        return
    with open(genres_file, encoding="utf-8") as f:
        lines = f.read().split("\n")
    keys = {ln.split("|")[0].strip() for ln in lines if "|" in ln}
    out = []
    for ln in lines:
        key = ln.split("|")[0].strip() if "|" in ln and not ln.startswith("#") else None
        if key in moves:
            if moves[key] in keys:
                continue
            ln = moves[key] + " " + ln[ln.index("|"):]
            keys.add(moves[key])
        out.append(ln)
    with open(genres_file, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def snapshot_genres(genres_file):
    """The genres file as it is, into the undo journal, before it's rewritten."""
    if audiotags.JOURNAL and genres_file and os.path.exists(genres_file):
        with open(genres_file, encoding="utf-8") as f:
            audiotags.JOURNAL._add({"do": "genres_file", "file": genres_file, "before": f.read()})


def split_singles(library, apply, genres_file=None, aside=None):
    """Every track of a '<artist>/Singles' folder into an album of its own."""
    folders = sorted(os.path.join(library, side, artist, SINGLES)
                     for side in ("Active", "Archive") if os.path.isdir(os.path.join(library, side))
                     for artist in os.listdir(os.path.join(library, side))
                     if os.path.isdir(os.path.join(library, side, artist, SINGLES)))
    moves, genre_lines = [], {}
    for folder in folders:
        artist_dir = os.path.dirname(folder)
        taken = {d.lower() for d in os.listdir(artist_dir)} - {SINGLES.lower()}
        for fn in audiotags.audio_files(os.listdir(folder)):
            src = os.path.join(folder, fn)
            t = audiotags.read(src)
            album = strip_feat(t["title"]) or t["title"] or os.path.splitext(fn)[0]
            if safe_name(album).lower() in taken:          # the artist has an album of that name
                album += " - Single"
            taken.add(safe_name(album).lower())
            dest = os.path.join(artist_dir, safe_name(album),
                                f"01 - {safe_name(t['title'])}{audiotags.extension(src)}")
            moves.append((src, dest, album, t["art"]))
            rel_old = os.path.relpath(folder, os.path.dirname(os.path.dirname(artist_dir)))
            genre_lines.setdefault(rel_old.split(os.sep, 1)[1].replace(os.sep, "/"), []).append(
                os.path.relpath(os.path.dirname(dest), os.path.dirname(artist_dir)).replace(os.sep, "/"))
    for src, dest, album, _art in moves:
        print(f"  {os.path.relpath(src, library)}")
        print(f"    -> {os.path.relpath(dest, library)}")
    print("\n" + _("Singles albums to take apart: {n}, tracks: {m}").format(n=len(folders), m=len(moves)))
    if not apply:
        print(_("Nothing changed. Run with --apply to do it."))
        return
    for src, dest, album, has_art in moves:
        audiotags.write(src, {"album": album, "track": "1/1", "disc": ""})
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        if audiotags.JOURNAL:
            audiotags.JOURNAL.moved(src, dest)
        # its own folder.jpg: from the embedded cover, else the old folder's shared one
        jpg = os.path.join(os.path.dirname(dest), "folder.jpg")
        pic = audiotags.cover(dest) if has_art else None
        old_jpg = os.path.join(os.path.dirname(src), "folder.jpg")
        if not os.path.exists(jpg) and (pic or os.path.exists(old_jpg)):
            if audiotags.JOURNAL:
                audiotags.JOURNAL.added(jpg)
            if pic:
                with open(jpg, "wb") as f:
                    f.write(pic)
            else:
                shutil.copy2(old_jpg, jpg)
    for folder in folders:
        jpg = os.path.join(folder, "folder.jpg")
        if os.path.exists(jpg) and not audiotags.audio_files(os.listdir(folder)) and aside:
            # set aside, not deleted: undo puts it back
            keep = os.path.join(aside, f"{os.path.basename(os.path.dirname(folder))} - Singles - folder.jpg")
            os.makedirs(aside, exist_ok=True)
            shutil.move(jpg, keep)
            if audiotags.JOURNAL:
                audiotags.JOURNAL.moved(jpg, keep)
        try:
            os.rmdir(folder)
        except OSError:
            pass
    if genres_file and os.path.exists(genres_file):
        snapshot_genres(genres_file)
        with open(genres_file, encoding="utf-8") as f:
            lines = f.read().split("\n")
        out = []
        for ln in lines:
            key = ln.split("|")[0].strip() if "|" in ln and not ln.startswith("#") else None
            if key in genre_lines:
                out += [new + " " + ln[ln.index("|"):] for new in genre_lines[key]]
            else:
                out.append(ln)
        with open(genres_file, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
    print(_("Done: every single is an album of its own."))


def run(library, protect, alias, apply, genres_file=None):
    changes = plan(library, protect, alias)
    if not changes:
        print(_("Every artist is already a single name."))
        return
    moves, renames = 0, {}
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
            renames[os.path.relpath(folder, os.path.join(library, side)).replace(os.sep, "/")] = \
                os.path.relpath(target, os.path.join(library, side)).replace(os.sep, "/")
        for _fp, t, new in items:
            print(f"  {t['artist']} — {t['title']}")
            print(f"    {new['artist']} — {new['title']}")
        if apply:
            move_album(folder, target, items)
    if apply:
        snapshot_genres(genres_file)
        rename_genre_lines(genres_file, renames)
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
    tracks = audiotags.audio_files(os.listdir(folder))
    all_art = all(audiotags.read(os.path.join(folder, fn))["art"] for fn in tracks)
    for fn in tracks:
        src = os.path.join(folder, fn)
        name = fn
        dest, k = os.path.join(target, name), 1
        while os.path.exists(dest):
            k += 1
            base, ext = os.path.splitext(os.path.join(target, name))
            dest = f"{base} ({k}){ext}"
        shutil.move(src, dest)
        if audiotags.JOURNAL:
            audiotags.JOURNAL.moved(src, dest)
    jpg = os.path.join(folder, "folder.jpg")
    if os.path.exists(jpg) and not os.path.exists(os.path.join(target, "folder.jpg")):
        shutil.move(jpg, os.path.join(target, "folder.jpg"))
        if audiotags.JOURNAL:
            audiotags.JOURNAL.moved(jpg, os.path.join(target, "folder.jpg"))
    elif os.path.exists(jpg) and all_art and not audiotags.audio_files(os.listdir(folder)):
        os.remove(jpg)                  # every track carries this cover embedded: nothing is lost
    # only empty folders are removed
    for d in (folder, os.path.dirname(folder)):
        try:
            os.rmdir(d)
        except OSError:
            break
