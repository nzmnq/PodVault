"""
Build the clean library for an old iPod + iTunes.

Source : the "Initial build source" setting    (read only)
Output : the library folder, <Album Artist>\\<Album>\\NN - Title.mp3
         (or --dest)

The source is an old, unsorted collection: one folder per album, plus
.covers/raw and .covers/ipod_jpg with files named 'Artist - Album.jpg'.

What it does to the tags:
  TPE2 (Album Artist) — set everywhere; it used to be empty, and that's
        exactly why the iPod split albums with features into separate artists
  TPE1 (Artist)       — the track's main artist only
  TIT2 (Title)        — '(feat. X)' is appended if features were in TPE1
                        and aren't in the title yet
  TCON (Genre)        — cleaned of slashes and long compound names
  APIC                — cover art from .covers/ipod_jpg is embedded
  tag version         — ID3v2.3 / UTF-16, otherwise an old iPod garbles Cyrillic

Folders with a single track are merged into a 'Singles' album per artist.

Dry run by default (nothing written). To write for real: --apply

This is the ONE-TIME initial build. It refuses to write into a library that
has already been split into Active/Archive: a full rebuild would ignore the
archive markup and duplicate the library. Use add_incoming.py to add tracks;
to rebuild from scratch, pass --dest with an empty folder.

  python src\\build_clean.py                    # show the plan
  python src\\build_clean.py --apply            # build into the library folder
  python src\\build_clean.py --apply --dest X   # build into another folder
"""

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    TYER,
)
from mutagen.mp3 import MP3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings
from musiclib import norm

# Set from the settings in main()
SOURCE = COVERS = DEST = REPORT_DIR = None

# Tag VALUE for compilations ("Various Artists" in Russian). This is data
# already written into files and synced to the iPod — not interface text,
# so it stays as is.
VARIOUS = "Разные исполнители"

# The artist separator in TPE1 is '/', sometimes followed by a space.
# Windows Explorer draws it as ';', so by eye it looks different.
ARTIST_SPLIT = re.compile(r"\s*[/;]\s*")

# Names where '/' is part of the name, not a separator.
# Checked against all 1408 tracks: there's exactly one such name in the
# whole library. The other 63 values with a slash are real features/collabs.
ARTIST_PROTECT = {"ac/dc": "AC/DC"}

# Frequency ties resolved by hand (key is the normalised name).
ARTIST_OVERRIDE = {"kiss": "KISS"}

# Genres: exact replacements first, then the first token before a separator.
GENRE_EXACT = {
    "Rap/Hip Hop": "Hip-Hop",
    "Films/Games": "Soundtrack",
    "Films/Games/Film Scores": "Soundtrack",
    "Contemporary Pop": "Pop",
    "Pop & Russian Pop": "Pop",
    "Asian Music": "Asian",
}
# Split on '/', ',' and ' & ' with spaces. Without spaces it's left alone,
# otherwise 'R&B' would become 'R'.
GENRE_SPLIT = re.compile(r"\s*/\s*|\s*,\s*|\s+&\s+")
GENRE_FIX = {"rocck": "Rock", "indie-rock": "Indie Rock", "rock pop": "Rock"}


def clean_genre(g):
    if not g:
        return None
    g = g.strip()
    if g in GENRE_EXACT:
        return GENRE_EXACT[g]
    first = GENRE_SPLIT.split(g)[0].strip()
    if not first:
        return None
    return GENRE_FIX.get(first.lower(), first)


def safe_name(s, maxlen=120):
    """A name that's valid on the Windows filesystem."""
    s = re.sub(r'[<>:"/\\|?*]', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(". ")  # Windows can't handle names ending with a dot
    return (s[:maxlen].strip() or "_")


SHORT_PART_WARNINGS = []


def tag_values(tags, frame):
    """All of a frame's values, splitting several artists in one string.

    'Lil Peep/ Lil Tracy' -> ['Lil Peep', 'Lil Tracy']
    'AC/DC'               -> ['AC/DC']   (protected name, not split)
    """
    v = tags.get(frame)
    if v is None:
        return []
    out = []
    for x in v.text:
        x = str(x).strip()
        if not x:
            continue
        if frame != "TPE1":
            out.append(x)
            continue
        if x.lower() in ARTIST_PROTECT:
            out.append(ARTIST_PROTECT[x.lower()])
            continue
        parts = [p.strip() for p in ARTIST_SPLIT.split(x) if p.strip()]
        # A very short piece almost always means we cut a name like 'AC/DC'
        # in half. Not blocked, but recorded for review.
        if len(parts) > 1 and any(len(p) <= 3 for p in parts):
            SHORT_PART_WARNINGS.append(x)
        out.extend(parts)
    return out


def tag_first(tags, frame):
    vals = tag_values(tags, frame)
    return vals[0] if vals else None


def parse_pair(raw):
    if not raw:
        return None, None
    m = re.match(r"^(\d+)(?:/(\d+))?", str(raw))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


# ---------------------------------------------------------------- covers


def load_covers():
    """Map album -> Album Artist candidates from the cover file names.

    Names look like 'Artist - Album.jpg'. Albums with features have several
    covers — one per artist combination ('Lil Peep', 'Lil Peep_ Lil Tracy', ...).
    """
    by_album = defaultdict(lambda: {"artists": set(), "art": None})
    for sub in ("ipod_jpg", "raw"):  # ipod_jpg takes priority — it's already sized for the iPod
        d = os.path.join(COVERS, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in (".jpg", ".jpeg", ".png") or " - " not in stem:
                continue
            artist, album = stem.split(" - ", 1)
            rec = by_album[norm(album)]
            rec["artists"].add(artist.strip())
            if rec["art"] is None or sub == "ipod_jpg":
                rec["art"] = os.path.join(d, fn)
    return by_album


def resolve_conflict(candidates):
    """Pick the album's real artist out of several candidates.

    'Lil Peep', 'Lil Peep_ Lil Tracy', 'Lil Peep_ Horse Head' -> 'Lil Peep'
    Works when the shortest candidate is a prefix of all the others.
    If there's no common prefix, it's a compilation.
    """
    if not candidates:
        return None, "none"
    if len(candidates) == 1:
        return next(iter(candidates)), "covers"
    # 'разные' = 'various' — matches the downloader's Russian compilation name
    if any(norm(c) == norm(VARIOUS) or "разные" in c.lower() for c in candidates):
        return VARIOUS, "compilation"
    shortest = min(candidates, key=lambda c: (len(norm(c)), c))
    base = norm(shortest)
    if base and all(norm(c).startswith(base) for c in candidates):
        return shortest, "prefix"
    return VARIOUS, "compilation"


# ---------------------------------------------------- canonical names


def build_canon(albums_raw):
    """Reduce the spellings of one name to a single variant.

    The most frequent spelling wins. Forms from file names (where '?' and
    ':' became '_') lose to forms from tags, because tags have the real
    punctuation: 'Где Фантом?' rather than 'Где Фантом_'.
    """
    # Only names from tags are counted. Names from .covers don't vote: their
    # punctuation is already mangled by the filesystem ('AC_DC',
    # 'Где Фантом_'), and in a vote they could beat the real spelling.
    # The lookup by normalised key still maps them onto it:
    # 'AC_DC' -> 'acdc' -> 'AC/DC'.
    counts = defaultdict(Counter)
    for alb in albums_raw:
        for t in alb["tracks"]:
            for name in t["artists"]:
                counts[norm(name)][name] += 1

    canon, ties = {}, []
    for key, variants in counts.items():
        if key in ARTIST_OVERRIDE:
            canon[key] = ARTIST_OVERRIDE[key]
            continue
        top = max(variants.values())
        best = sorted(n for n, c in variants.items() if c == top)
        if len(best) > 1:
            ties.append({"variants": best, "counts": dict(variants)})
        canon[key] = best[0]
    return canon, ties


# -------------------------------------------------------------- features


# 'при уч' is the Russian 'feat.'
FEAT_RE = re.compile(r"\((?:feat|ft|featuring|при уч)[^)]*\)", re.I)


def merge_feats(title, feats):
    """Append features to the title if they aren't there yet."""
    if not feats:
        return title
    existing = norm(title)
    missing = [f for f in feats if norm(f) and norm(f) not in existing]
    if not missing:
        return title
    return f"{title} (feat. {', '.join(missing)})"


# -------------------------------------------------------------- plan


def read_album(folder, covers):
    path = os.path.join(SOURCE, folder)
    mp3s = sorted(f for f in os.listdir(path) if f.lower().endswith(".mp3"))
    tracks = []
    for fn in mp3s:
        fp = os.path.join(path, fn)
        try:
            audio, tags = MP3(fp), ID3(fp)
        except Exception as e:
            print(f"  ! skipped {folder}\\{fn}: {e}", file=sys.stderr)
            continue
        tn, tt = parse_pair(tag_first(tags, "TRCK"))
        dn, dt = parse_pair(tag_first(tags, "TPOS"))
        tracks.append(
            {
                "src": fp,
                "title": tag_first(tags, "TIT2") or os.path.splitext(fn)[0],
                "artists": tag_values(tags, "TPE1"),
                "genre": tag_first(tags, "TCON"),
                "year": tag_first(tags, "TDRC") or tag_first(tags, "TYER"),
                "track": tn,
                "track_total": tt,
                "disc": dn,
                "disc_total": dt,
                "bitrate": int(audio.info.bitrate / 1000),
            }
        )
    if not tracks:
        return None
    rec = covers.get(norm(folder), {"artists": set(), "art": None})
    return {
        "folder": folder,
        "album": tag_first(ID3(tracks[0]["src"]), "TALB") or folder,
        "tracks": tracks,
        "cover_artists": rec["artists"],
        "art": rec["art"],
    }


def plan():
    covers = load_covers()
    folders = [
        f
        for f in sorted(os.listdir(SOURCE))
        if os.path.isdir(os.path.join(SOURCE, f)) and f != ".covers"
    ]

    raw = [a for a in (read_album(f, covers) for f in folders) if a]
    canon, ties = build_canon(raw)

    def C(name):
        return canon.get(norm(name), name) if name else name

    singles = defaultdict(list)
    regular = []
    notes = []

    for alb in raw:
        first = Counter(t["artists"][0] for t in alb["tracks"] if t["artists"])
        fallback = first.most_common(1)[0][0] if first else "Unknown Artist"

        artist, how = resolve_conflict(alb["cover_artists"])

        # A cover name that appears in no tag is a gluing done by the
        # downloader: 'Lil Peep_Lil Tracy' instead of 'Lil Peep'. The common
        # prefix rule doesn't catch it, because there's only one cover and
        # nothing to compare it with. Check against real names from tags.
        if artist and how != "compilation" and norm(artist) not in canon:
            notes.append(
                {
                    "kind": "cover_not_in_tags",
                    "folder": alb["folder"],
                    "rejected": artist,
                    "used": fallback,
                }
            )
            artist, how = fallback, "fallback"

        if artist is None:
            artist, how = fallback, "guess"
            notes.append({"kind": "no_cover", "folder": alb["folder"], "used": artist})
        elif how in ("prefix", "compilation"):
            notes.append(
                {
                    "kind": how,
                    "folder": alb["folder"],
                    "used": artist,
                    "candidates": sorted(alb["cover_artists"]),
                }
            )
        album_artist = C(artist)

        for t in alb["tracks"]:
            names = [C(a) for a in t["artists"]] or [album_artist]
            primary = names[0]
            feats = names[1:]
            t["out_artist"] = primary
            t["out_title"] = merge_feats(t["title"], feats)
            t["out_genre"] = clean_genre(t["genre"])
            t["feats"] = feats

        alb["album_artist"] = album_artist
        alb["art_source"] = how

        if len(alb["tracks"]) == 1:
            singles[album_artist].append(alb)
        else:
            regular.append(alb)

    out = []
    for alb in regular:
        adir = os.path.join(DEST, safe_name(alb["album_artist"]), safe_name(alb["album"]))
        for t in alb["tracks"]:
            n = t["track"] or 0
            out.append(
                {
                    **t,
                    "album_artist": alb["album_artist"],
                    "album": alb["album"],
                    "art": alb["art"],
                    "dest": os.path.join(adir, f"{n:02d} - {safe_name(t['out_title'])}.mp3"),
                }
            )

    # singles: one 'Singles' album per artist, numbered by title
    for artist, albs in sorted(singles.items()):
        adir = os.path.join(DEST, safe_name(artist), "Singles")
        ordered = sorted(albs, key=lambda a: norm(a["tracks"][0]["out_title"]))
        for i, alb in enumerate(ordered, 1):
            t = alb["tracks"][0]
            out.append(
                {
                    **t,
                    "album_artist": artist,
                    "album": "Singles",
                    "track": i,
                    "track_total": len(ordered),
                    "disc": None,
                    "disc_total": None,
                    "art": alb["art"],
                    "dest": os.path.join(adir, f"{i:02d} - {safe_name(t['out_title'])}.mp3"),
                }
            )

    return out, notes, ties, singles


# -------------------------------------------------------------- write


def write_track(item):
    os.makedirs(os.path.dirname(item["dest"]), exist_ok=True)
    shutil.copy2(item["src"], item["dest"])

    tags = ID3(item["dest"])
    # clear what we overwrite, so no old values are left behind
    for frame in ("TPE1", "TPE2", "TIT2", "TALB", "TCON", "TRCK", "TPOS", "APIC"):
        tags.delall(frame)

    enc = 1  # UTF-16 with BOM: the only encoding in which an old iPod
    # shows Cyrillic instead of garbage
    tags.add(TPE1(encoding=enc, text=[item["out_artist"]]))
    tags.add(TPE2(encoding=enc, text=[item["album_artist"]]))
    tags.add(TIT2(encoding=enc, text=[item["out_title"]]))
    tags.add(TALB(encoding=enc, text=[item["album"]]))
    if item["out_genre"]:
        tags.add(TCON(encoding=enc, text=[item["out_genre"]]))
    if item["track"]:
        trck = f"{item['track']}/{item['track_total']}" if item["track_total"] else str(item["track"])
        tags.add(TRCK(encoding=enc, text=[trck]))
    if item["disc"]:
        tpos = f"{item['disc']}/{item['disc_total']}" if item["disc_total"] else str(item["disc"])
        tags.add(TPOS(encoding=enc, text=[tpos]))
    tags.delall("TDRC")  # a v2.4 frame; an old iPod won't understand it in a v2.3 tag
    tags.delall("TYER")
    if item["year"]:
        m = re.search(r"\d{4}", str(item["year"]))
        if m:
            tags.add(TYER(encoding=enc, text=[m.group(0)]))

    if item["art"] and os.path.isfile(item["art"]):
        with open(item["art"], "rb") as f:
            data = f.read()
        tags.add(APIC(encoding=0, mime="image/jpeg", type=3, desc="", data=data))

    # v2.3: an old iPod doesn't understand v2.4
    tags.save(item["dest"], v2_version=3, v1=2)

    folder_jpg = os.path.join(os.path.dirname(item["dest"]), "folder.jpg")
    if item["art"] and os.path.isfile(item["art"]) and not os.path.exists(folder_jpg):
        shutil.copy2(item["art"], folder_jpg)


def main():
    global SOURCE, COVERS, DEST, REPORT_DIR
    cfg = settings.require()

    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write files")
    ap.add_argument("--limit", type=int, help="process only N tracks (for testing)")
    ap.add_argument("--dest", help="build into this folder instead of the library folder")
    args = ap.parse_args()

    SOURCE = settings.path("source_dir", cfg)
    if not SOURCE:
        sys.exit("The initial build source isn't set. Set it on the Settings "
                 "screen in Main.py (only needed for the one-time build).")
    if not os.path.isdir(SOURCE):
        sys.exit(f"Source not found: {SOURCE}")
    COVERS = os.path.join(SOURCE, ".covers")
    DEST = os.path.abspath(args.dest) if args.dest else settings.library_paths(cfg)[0]
    REPORT_DIR = settings.path("reports_dir", cfg)

    items, notes, ties, singles = plan()
    if args.limit:
        items = items[: args.limit]

    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "items": [
                    {k: v for k, v in i.items() if k not in ("art",)} for i in items
                ],
                "notes": notes,
                "ties": ties,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    artists = sorted({i["album_artist"] for i in items})
    feats = [i for i in items if i["feats"]]
    retitled = [i for i in items if i["out_title"] != i["title"]]
    regenred = [i for i in items if i["out_genre"] != i["genre"]]
    no_art = [i for i in items if not i["art"]]

    print("=" * 72)
    print(f"{'PLAN' if not args.apply else 'WRITING'}: {len(items)} tracks -> {len(artists)} artists")
    print("=" * 72)
    print(f"  Album Artist will be set    : {len(items)} (was 0)")
    print(f"  tracks with features in TPE1: {len(feats)}")
    print(f"  titles that will change     : {len(retitled)}")
    print(f"  genres that will change     : {len(regenred)}")
    print(f"  merged into 'Singles'       : {sum(len(v) for v in singles.values())} tracks for {len(singles)} artists")
    print(f"  without cover art           : {len(no_art)}")

    if ties:
        print(f"\n  !! COULDN'T PICK A SPELLING ({len(ties)}):")
        for t in ties:
            print(f"     {t['counts']}")

    if SHORT_PART_WARNINGS:
        uniq = sorted(set(SHORT_PART_WARNINGS))
        print(f"\n  !! SPLIT INTO SUSPICIOUSLY SHORT PIECES ({len(uniq)}):")
        print("     (maybe this is one name, not several — like 'AC/DC')")
        for w in uniq:
            print(f"     {w!r}")

    print("\n--- sample changed titles ---")
    for i in retitled[:12]:
        print(f"  {i['title']!r}")
        print(f"    -> {i['out_title']!r}   [Artist: {i['out_artist']} | AlbumArtist: {i['album_artist']}]")

    print("\n--- decisions on doubtful albums ---")
    for n in notes:
        print(f"  [{n['kind']}] {n['folder']} -> {n['used']}")

    if not args.apply:
        print("\nNothing written. Run with --apply to build.")
        print(f"Full plan: {os.path.join(REPORT_DIR, 'plan.json')}")
        return

    # After the Active/Archive split the library root is outside both folders.
    # Writing there would put all 1408 tracks next to the split ones —
    # a duplicated library, with the archive markup ignored.
    if any(os.path.isdir(os.path.join(DEST, d)) for d in ("Active", "Archive")):
        sys.exit(
            f"\nSTOPPED: {DEST} is already split into Active/Archive.\n"
            "A full rebuild would write every track into the library root,\n"
            "outside both folders, duplicating the library and ignoring the\n"
            "archive markup. To add new tracks use add_incoming.py.\n"
            "To rebuild from scratch, pass --dest with an empty folder."
        )

    for n, item in enumerate(items, 1):
        write_track(item)
        if n % 100 == 0:
            print(f"  ... {n}/{len(items)}")
    print(f"\nDone: {len(items)} tracks in {DEST}")


if __name__ == "__main__":
    main()
