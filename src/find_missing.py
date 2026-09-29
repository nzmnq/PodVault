"""
Compare a "what I listen to" list with the library and report what's missing.

Reads <reports>/likes.json — a neutral format that import_likes.py
produces from a Spotify data export, an Apple Music page or a plain text
list. It knows nothing about the source itself.

Works in three modes, depending on what was known:

  BY ALBUM     — if the full album tracklist is known: shows "5 of 12
                 present", the whole album goes into the report
  BY TRACK     — if there's no tracklist: checks that the specific liked
                 tracks are present. If the album is unknown too (Apple
                 Music pages), the track is looked for across all of the
                 artist's albums
  BY PRESENCE  — if the list has only an album title: checks whether the
                 album is in the library at all

Downloads nothing and changes nothing. Output:
  <reports>/missing.csv   — a table with search links
  <reports>/missing.json  — the same in detail
"""

import csv
import json
import os
import sys
import urllib.parse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import settings
import tags as audiotags
from i18n import _
from musiclib import norm, strip_edition, strip_feat

# Set from the settings in main()
REPORT_DIR = LIKES = LIBRARY = None


def key_title(s):
    """Track key without features: we added them to the library ourselves,
    while the source writes them differently or not at all."""
    return norm(strip_feat(s))


def load_library():
    """What's already there: albums with their tracks, and all tracks by artist."""
    albums = defaultdict(lambda: {"artist": None, "album": None, "titles": set(),
                                  "state": "A"})
    by_artist = defaultdict(set)
    # (artist, title) -> {'A', 'R'}: where each track lies, for sources that
    # don't know the album and so can't use the per-album state
    track_state = defaultdict(set)
    if not os.path.isdir(LIBRARY):
        sys.exit(_("Library not found: {folder}").format(folder=LIBRARY))

    for root, _skip, files in os.walk(LIBRARY):
        rel = os.path.relpath(root, LIBRARY).split(os.sep)[0]
        state = "R" if rel == "Archive" else "A"
        for fn in audiotags.audio_files(files):
            try:
                t = audiotags.read(os.path.join(root, fn))
            except audiotags.Unreadable:
                continue
            aa = t["albumartist"] or t["artist"]
            ti = t["title"]
            if not (aa and ti):
                continue
            al = t["album"]
            rec = albums[(norm(aa), norm(strip_edition(al)))]
            rec["artist"] = rec["artist"] or aa
            rec["album"] = rec["album"] or al
            rec["state"] = state
            rec["titles"].add(key_title(ti))
            by_artist[norm(aa)].add(key_title(ti))
            track_state[(norm(aa), key_title(ti))].add(state)
    return albums, by_artist, track_state


def data_liked(data, row):
    """The liked titles of the source entry a report row came from."""
    for a in data["albums"]:
        if a["artist"] == row["artist"] and a["album"] == row["album"]:
            return a.get("liked") or []
    return []


def links(artist, album):
    q = urllib.parse.quote_plus(f"{artist} {album}")
    return (f"https://bandcamp.com/search?q={q}&item_type=a",
            f"https://music.apple.com/search?term={q}")


def main():
    global REPORT_DIR, LIKES, LIBRARY
    cfg = settings.require()
    REPORT_DIR = settings.path("reports_dir", cfg)
    LIKES = os.path.join(REPORT_DIR, "likes.json")
    LIBRARY = settings.library_paths(cfg)[0]

    if not os.path.exists(LIKES):
        sys.exit(_("No {file}\nFirst run: python src/import_likes.py <file>").format(file=LIKES))

    with open(LIKES, encoding="utf-8") as f:
        data = json.load(f)

    print(_("Reading the library..."))
    lib, by_artist, track_state = load_library()
    print(_("  albums: {n}").format(n=len(lib)))

    missing, partial, complete = [], [], []

    for a in data["albums"]:
        artist, album = a["artist"], a["album"]
        key = (norm(artist), norm(strip_edition(album)))
        # no album in the source -> look across all of the artist's albums
        have_album = lib.get(key) if album else None
        have = have_album["titles"] if have_album else by_artist.get(norm(artist), set())

        full = [t for t in a.get("tracks") or [] if t]
        liked = [t for t in a.get("liked") or [] if t]
        wanted_src = full or liked
        wanted = {key_title(t) for t in wanted_src}

        if full:
            mode = "album"        # the whole tracklist is known — "N of M present"
        elif liked:
            mode = "track"        # only the liked tracks are known
        else:
            # neither a tracklist nor tracks: the list has just an album title.
            # Then the only meaningful question is whether it's there at all.
            mode = "presence"

        if mode == "presence":
            present = {album} if have_album else set()
            absent = set() if have_album else {album}
            wanted_src = [album]
        else:
            present = wanted & have
            absent = wanted - have
        bc, it = links(artist, album)

        row = {
            "mode": mode,
            "artist": artist,
            "album": album,
            "year": a.get("year", ""),
            "tracks_total": len(wanted) if mode != "presence" else 0,
            "tracks_have": len(present) if mode != "presence" else 0,
            "tracks_missing": len(absent) if mode != "presence" else 0,
            "liked_here": len(liked),
            "missing_titles": ([] if mode == "presence" else
                               sorted(t for t in wanted_src if key_title(t) in absent)),
            "in_library_as": have_album["album"] if have_album else None,
            # With a known album its folder decides. Without one, the entry
            # counts as archived only if every present track is archive-only.
            "in_archive": (have_album["state"] == "R" if have_album else
                           bool(present) and mode != "presence" and all(
                               track_state.get((norm(artist), t)) == {"R"} for t in present)),
            "spotify_url": a.get("url", ""),
            "bandcamp": bc,
            "itunes": it,
        }
        (complete if not absent else (missing if not present else partial)).append(row)

    missing.sort(key=lambda r: (-r["liked_here"], r["artist"].lower()))
    partial.sort(key=lambda r: (-r["tracks_missing"], r["artist"].lower()))

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "missing.json"), "w", encoding="utf-8") as f:
        json.dump({"source": data.get("source"), "missing": missing,
                   "partial": partial, "complete": len(complete)},
                  f, ensure_ascii=False, indent=2)

    cols = ["status", "artist", "album", "year", "tracks_have", "tracks_total",
            "liked_here", "in_archive", "bandcamp", "itunes", "spotify_url"]
    csv_path = os.path.join(REPORT_DIR, "missing.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in missing:
            w.writerow({**r, "status": "MISSING"})
        for r in partial:
            w.writerow({**r, "status": "PARTIAL"})

    by_mode = defaultdict(int)
    for r in missing + partial + complete:
        by_mode[r["mode"]] += 1

    print()
    print("=" * 72)
    print(_("SOURCE: {source}   ({date})").format(source=data.get('source', '?'),
                                                  date=data.get('fetched_at', '')))
    print(_("ALBUMS IN THE LIST: {n}").format(n=len(data['albums'])))
    print("=" * 72)
    rows = [(_("complete"), len(complete)), (_("partial"), len(partial)), (_("missing"), len(missing))]
    width = max(len(label) for label, _v in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")
    if by_mode.get("track") or by_mode.get("presence"):
        print()
        if by_mode.get("album"):
            print(_("  {n} entries compared BY ALBUM (full tracklist known)").format(n=by_mode['album']))
        if by_mode.get("track"):
            print(_("  {n} entries — BY TRACK: these sources don't give album tracklists,\n"
                    "     so 'complete' here means 'all liked tracks are present'").format(n=by_mode['track']))
        if by_mode.get("presence"):
            print(_("  {n} entries — BY PRESENCE ONLY: the list had just an album title,\n"
                    "     so it was only checked whether the album is in the library").format(
                n=by_mode['presence']))

    in_arc = [r for r in complete if r["in_archive"]]
    if in_arc:
        print("\n--- " + _("PRESENT, BUT IN THE ARCHIVE ({n})").format(n=len(in_arc)) + " ---")
        print(_("  No need to download — just mark them [A] in the markup."))
        for r in in_arc[:15]:
            if r["album"]:
                print(f"  {r['artist']} — {r['album']}")
            else:
                # the source had no album (Apple Music page) — name the tracks
                liked = ", ".join(a for a in data_liked(data, r)[:3])
                print(f"  {r['artist']} — {liked}")
        if len(in_arc) > 15:
            print(_("  ... {n} more").format(n=len(in_arc) - 15))

    if missing:
        print("\n--- " + _("MISSING (top 25)") + " ---")
        for r in missing[:25]:
            if r["liked_here"]:
                n = _("{n} liked").format(n=r['liked_here'])
            elif r["tracks_total"]:
                n = _("{n} tr.").format(n=r['tracks_total'])
            else:
                n = _("album")          # the list had only the title
            print(f"  {n:>9}  {r['artist']} — {r['album'] or _('(album unknown)')}"
                  + (f" [{r['year']}]" if r["year"] else ""))
        if len(missing) > 25:
            print(_("  ... {n} more").format(n=len(missing) - 25))

    if partial:
        print("\n--- " + _("PARTIAL (top 20)") + " ---")
        for r in partial[:20]:
            print(_("  missing {n:2d} of {total:2d}").format(n=r['tracks_missing'], total=r['tracks_total'])
                  + f"  {r['artist']} — {r['album'] or _('(album unknown)')}")
            for t in r["missing_titles"][:3]:
                print(f"       · {t}")
            if len(r["missing_titles"]) > 3:
                print("       · " + _("... {n} more").format(n=len(r['missing_titles']) - 3))
        if len(partial) > 20:
            print(_("  ... {n} more").format(n=len(partial) - 20))

    print(_("\nTable with links: {file}").format(file=csv_path))


if __name__ == "__main__":
    main()
