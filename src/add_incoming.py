"""
Add new tracks to the library.

Brings them to the same shape as everything else (see build_clean.py):
Album Artist set, features moved from the artist into the title, genre
cleaned, tag in ID3v2.3/UTF-16. Incoming files already have cover art
embedded, so their own is kept.

New files arrive as a flat pile with no folders, so the tags are the only
source of structure. The dry run (the default) shows what's in them —
albums, bitrates, ID3 versions, cover art, unreadable files — and which
tracks are already in the library. It replaces the old scan_incoming.py.

Where new tracks go (Active or Archive) and which folder they're picked up
from come from the settings; both can be overridden on the command line.

  python src\\add_incoming.py                      # show the plan
  python src\\add_incoming.py --apply              # add
  python src\\add_incoming.py --apply --to archive # set aside in the archive
  python src\\add_incoming.py --apply --replace    # and overwrite dupes
  python src\\add_incoming.py "D:\\other\\folder"   # a different source
"""

import argparse
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TCON, TIT2, TPE1, TPE2, TPOS, TRCK, TYER
from mutagen.mp3 import MP3

import settings
from musiclib import drop_v24_frames, norm, strip_edition, strip_feat

# Incoming files glue co-artists with a comma ('wifiskeleton, Jaydes'), the
# older part of the library with a slash. Both are split, but the comma only
# when the whole name isn't already in the library: '125, Rue Montmartre'
# is a real name.
SPLIT_SLASH = re.compile(r"\s*[/;]\s*")
SPLIT_COMMA = re.compile(r"\s*,\s*")

ARTIST_PROTECT = {"ac/dc": "AC/DC"}

GENRE_EXACT = {
    "Rap/Hip Hop": "Hip-Hop",
    "Films/Games": "Soundtrack",
    "Films/Games/Film Scores": "Soundtrack",
    "Contemporary Pop": "Pop",
    "Pop & Russian Pop": "Pop",
}
GENRE_SPLIT = re.compile(r"\s*/\s*|\s*,\s*|\s+&\s+")


def clean_genre(g):
    if not g:
        return None
    g = g.strip()
    if g in GENRE_EXACT:
        return GENRE_EXACT[g]
    first = GENRE_SPLIT.split(g)[0].strip()
    return first or None


def safe_name(s, maxlen=120):
    s = re.sub(r'[<>:"/\\|?*]', "_", s or "")
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    return s[:maxlen].strip() or "_"


def one(tags, key):
    v = tags.get(key)
    return str(v.text[0]).strip() if v and v.text else None


def parse_pair(raw):
    if not raw:
        return None, None
    m = re.match(r"^(\d+)(?:/(\d+))?", str(raw))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def split_artists(value, known):
    """Split an artist string into individual names.

    `known` is the set of normalised names already seen in the library.
    If the whole string is among them it isn't split: that's how real
    names with a comma or slash inside survive.
    """
    if not value:
        return []
    v = value.strip()
    if v.lower() in ARTIST_PROTECT:
        return [ARTIST_PROTECT[v.lower()]]
    if norm(v) in known:
        return [v]
    parts = [p.strip() for p in SPLIT_SLASH.split(v) if p.strip()]
    out = []
    for p in parts:
        if norm(p) in known or "," not in p:
            out.append(p)
        else:
            out.extend(x.strip() for x in SPLIT_COMMA.split(p) if x.strip())
    return out or [v]


def resolve_album_artist(candidates):
    """The album's real artist out of a set of variants.

    'wifiskeleton', 'wifiskeleton, Jaydes' -> 'wifiskeleton'
    The shortest variant that's a prefix of all the others wins.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return next(iter(candidates))
    shortest = min(candidates, key=lambda c: (len(norm(c)), c))
    base = norm(shortest)
    if base and all(norm(c).startswith(base) for c in candidates):
        return shortest
    return None


# ------------------------------------------------------- what's there


def load_library(library):
    """What's already there: albums, tracks and name spellings."""
    albums = defaultdict(set)   # (artist, base album) -> track titles
    names = Counter()           # normalised name -> count
    spelling = {}
    for root, _, files in os.walk(library):
        for fn in sorted(f for f in files if f.lower().endswith(".mp3")):
            try:
                tags = ID3(os.path.join(root, fn))
            except Exception:
                continue
            aa = one(tags, "TPE2") or one(tags, "TPE1")
            al = one(tags, "TALB")
            ti = one(tags, "TIT2")
            if not aa:
                continue
            names[norm(aa)] += 1
            spelling.setdefault(norm(aa), aa)
            ar = one(tags, "TPE1")
            if ar:
                names[norm(ar)] += 1
                spelling.setdefault(norm(ar), ar)
            if al and ti:
                albums[(norm(aa), norm(strip_edition(al)))].add(norm(strip_feat(ti)))
    return albums, set(names), spelling


# -------------------------------------------------------------- plan


OTHER_AUDIO = (".m4a", ".flac", ".wav", ".ogg", ".opus", ".aac", ".wma", ".alac", ".aiff")


def read_incoming(src, known):
    """Read every mp3 in the folder. Returns (files, unreadable).

    Other audio formats are reported as unreadable rather than skipped
    silently: the library is mp3-only, so they need converting first.
    """
    files, broken = [], []
    for root, _, fs in os.walk(src):
        for fn in sorted(f for f in fs if f.lower().endswith(OTHER_AUDIO)):
            broken.append((fn, "not an mp3 — convert it first"))
        for fn in sorted(f for f in fs if f.lower().endswith(".mp3")):
            fp = os.path.join(root, fn)
            try:
                audio = MP3(fp)
                tags = ID3(fp)
            except ID3NoHeaderError:
                broken.append((fn, "no ID3 tag"))
                continue
            except Exception as e:
                broken.append((fn, str(e)))
                continue
            tn, tt = parse_pair(one(tags, "TRCK"))
            dn, dt = parse_pair(one(tags, "TPOS"))
            files.append(
                {
                    "src": fp,
                    "file": fn,
                    "artists": split_artists(one(tags, "TPE1"), known),
                    "aa_raw": one(tags, "TPE2") or one(tags, "TPE1"),
                    "album": one(tags, "TALB"),
                    "title": one(tags, "TIT2") or os.path.splitext(fn)[0],
                    "genre": one(tags, "TCON"),
                    "year": one(tags, "TDRC") or one(tags, "TYER"),
                    "track": tn, "track_total": tt, "disc": dn, "disc_total": dt,
                    "bitrate": int(audio.info.bitrate / 1000),
                    "has_art": bool(tags.getall("APIC")),
                    "ver": ".".join(map(str, tags.version)),
                }
            )
    return files, broken


def build_plan(files, known, spelling, dest_root):
    # group by the base album title, so that the three variants of
    # 'wifiskeleton, X' merge into one album
    groups = defaultdict(list)
    for f in files:
        groups[norm(strip_edition(f["album"] or ""))].append(f)

    plan, notes = [], []
    for key, g in groups.items():
        cands = {f["aa_raw"] for f in g if f["aa_raw"]}
        aa = resolve_album_artist(cands)
        if aa is None:
            counts = Counter(f["artists"][0] for f in g if f["artists"])
            aa = counts.most_common(1)[0][0] if counts else "Unknown Artist"
            notes.append({"kind": "compilation?", "album": g[0]["album"], "used": aa,
                          "candidates": sorted(cands)})
        else:
            parts = split_artists(aa, known)
            if len(parts) > 1:
                notes.append({"kind": "glued", "album": g[0]["album"],
                              "used": parts[0], "candidates": [aa]})
                aa = parts[0]
        aa = spelling.get(norm(aa), aa)

        album = g[0]["album"]
        for f in g:
            names = [spelling.get(norm(a), a) for a in f["artists"]] or [aa]
            feats = [n for n in names[1:] if norm(n) not in norm(f["title"])]
            title = f["title"] + (f" (feat. {', '.join(feats)})" if feats else "")
            plan.append({
                **f,
                "album_artist": aa,
                "album": album,
                "out_artist": names[0],
                "out_title": title,
                "out_genre": clean_genre(f["genre"]),
                "dest": os.path.join(
                    dest_root, safe_name(aa), safe_name(album),
                    f"{f['track'] or 0:02d} - {safe_name(title)}.mp3"),
            })
    return plan, notes


def unique_dests(items):
    """Give every item a destination nothing else uses.

    The same song downloaded twice maps to one path: the second copy is
    dropped (it would only be a duplicate on the iPod). Different tracks
    that happen to map to one path — or onto a file already sitting there —
    get a ' (2)' suffix instead of silently overwriting it.
    Returns (items to write, renamed count, dropped count).
    """
    taken = {}
    keep, renamed, dropped = [], 0, 0
    for it in items:
        ident = (norm(it["album_artist"]), norm(it["album"] or ""), norm(it["out_title"]))
        first = taken.get(os.path.normcase(it["dest"]))
        if first == ident:
            dropped += 1
            continue
        base, ext = os.path.splitext(it["dest"])
        dest, n = it["dest"], 1
        while os.path.normcase(dest) in taken or (os.path.exists(dest) and not it.get("replace")):
            n += 1
            dest = f"{base} ({n}){ext}"
        if dest != it["dest"]:
            renamed += 1
        taken.setdefault(os.path.normcase(it["dest"]), ident)
        taken[os.path.normcase(dest)] = ident
        it["dest"] = dest
        keep.append(it)
    return keep, renamed, dropped


def write_track(item):
    os.makedirs(os.path.dirname(item["dest"]), exist_ok=True)
    shutil.copy2(item["src"], item["dest"])

    tags = ID3(item["dest"])
    for fr in ("TPE1", "TPE2", "TIT2", "TALB", "TCON", "TRCK", "TPOS", "TDRC", "TYER"):
        tags.delall(fr)

    enc = 1  # UTF-16: otherwise an old iPod garbles Cyrillic
    tags.add(TPE1(encoding=enc, text=[item["out_artist"]]))
    tags.add(TPE2(encoding=enc, text=[item["album_artist"]]))
    tags.add(TIT2(encoding=enc, text=[item["out_title"]]))
    tags.add(TALB(encoding=enc, text=[item["album"]]))
    if item["out_genre"]:
        tags.add(TCON(encoding=enc, text=[item["out_genre"]]))
    if item["track"]:
        t = f"{item['track']}/{item['track_total']}" if item["track_total"] else str(item["track"])
        tags.add(TRCK(encoding=enc, text=[t]))
    if item["disc"]:
        d = f"{item['disc']}/{item['disc_total']}" if item["disc_total"] else str(item["disc"])
        tags.add(TPOS(encoding=enc, text=[d]))
    if item["year"]:
        m = re.search(r"\d{4}", str(item["year"]))
        if m:
            tags.add(TYER(encoding=enc, text=[m.group(0)]))
    # Frames the source file brought along (TIPL from its IPLS, sort
    # frames...) would otherwise be written into the v2.3 tag as v2.4 ones.
    # Before the tools were merged a separate step stripped them afterwards.
    drop_v24_frames(tags)
    tags.save(item["dest"], v2_version=3, v1=2)

    folder_jpg = os.path.join(os.path.dirname(item["dest"]), "folder.jpg")
    pics = tags.getall("APIC")
    if pics and not os.path.exists(folder_jpg):
        with open(folder_jpg, "wb") as f:
            f.write(pics[0].data)


def main():
    cfg = settings.require()
    library, active, archive = settings.library_paths(cfg)

    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default=settings.path("incoming_dir", cfg))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--replace", action="store_true",
                    help="overwrite tracks that are already in the library")
    ap.add_argument("--to", choices=("archive", "active"), default=cfg["new_tracks_target"],
                    help=f"where to put new tracks (setting: {cfg['new_tracks_target']})")
    args = ap.parse_args()

    if not args.source or not os.path.isdir(args.source):
        sys.exit(f"Folder not found: {args.source}")

    # After the Active/Archive split, writing into the library root would
    # put tracks outside both folders — so the target is always one of them.
    dest_root = archive if args.to == "archive" else active

    lib_albums, known, spelling = load_library(library)
    files, broken = read_incoming(args.source, known)
    plan, notes = build_plan(files, known, spelling, dest_root)

    fresh, dupes = [], []
    for it in plan:
        key = (norm(it["album_artist"]), norm(strip_edition(it["album"])))
        have = lib_albums.get(key, set())
        (dupes if norm(strip_feat(it["out_title"])) in have else fresh).append(it)

    by_album = defaultdict(list)
    for it in plan:
        by_album[(it["album_artist"], it["album"])].append(it)

    print("=" * 74)
    print(f"INCOMING: {len(plan)} tracks in {len(by_album)} albums   ({args.source})")
    print("=" * 74)
    print(f"  new to the library : {len(fresh)}")
    print(f"  already there      : {len(dupes)}")
    print(f"  unreadable         : {len(broken)}")
    print(f"  will go into       : {args.to.upper()}  ({dest_root})")
    if args.to == "archive":
        print("                       won't reach the iPod until you mark it [A]")
    else:
        print("                       reaches the iPod on the next sync")

    print("\n--- ALBUMS ---")
    for (aa, al), items in sorted(by_album.items(), key=lambda x: (-len(x[1]), x[0])):
        n_dup = sum(1 for i in items if i in dupes)
        mark = f"  ({n_dup} already there)" if n_dup else ""
        br = sorted({i["bitrate"] for i in items})
        art = sum(1 for i in items if i["has_art"])
        print(f"  {len(items):3d} tr.  {aa} — {al}{mark}")
        print(f"          {br} kbps, cover on {art}/{len(items)}")

    no_album = [f for f in files if not f["album"]]
    if no_album:
        print(f"\n--- NO ALBUM IN TAGS ({len(no_album)}) ---")
        for f in no_album[:20]:
            print(f"  {f['file']}   artist={f['aa_raw']!r} title={f['title']!r}")

    if notes:
        print("\n--- UNGLUED ARTIST STRINGS ---")
        for n in notes:
            print(f"  [{n['kind']}] {n['album']} -> {n['used']}")
            print(f"        from: {n['candidates']}")

    if dupes:
        print(f"\n--- ALREADY IN THE LIBRARY ({len(dupes)}) ---")
        for it in dupes[:20]:
            print(f"  {it['album_artist']} — {it['album']} / {it['out_title']}")
        if len(dupes) > 20:
            print(f"  ... {len(dupes) - 20} more")

    if broken:
        print(f"\n--- WON'T BE ADDED: unreadable or not mp3 ({len(broken)}) ---")
        for fn, err in broken:
            print(f"  {fn}: {err}")

    if files:
        print("\n--- SUMMARY ---")
        print(f"  bitrates       : {dict(Counter(f['bitrate'] for f in files).most_common())}")
        print(f"  ID3 versions   : {dict(Counter(f['ver'] for f in files).most_common())}")
        print(f"  with cover art : {sum(1 for f in files if f['has_art'])}/{len(files)}")

    if not args.apply:
        print("\nNothing written. Run with --apply to add the new tracks.")
        return

    todo = plan if args.replace else fresh
    for it in todo:
        # --replace overwrites a dupe at its own path, nothing else
        it["replace"] = args.replace and it in dupes
    todo, renamed, dropped = unique_dests(todo)
    if dropped:
        print(f"  {dropped} tracks were in the folder twice; one copy of each is added")
    if renamed:
        print(f"  {renamed} tracks got a ' (2)' suffix: their file name was taken")
    for n, it in enumerate(todo, 1):
        write_track(it)
        if n % 50 == 0:
            print(f"  ... {n}/{len(todo)}")
    print(f"\nTracks added: {len(todo)}")
    if dupes and not args.replace:
        print(f"Skipped as already present: {len(dupes)} (--replace overwrites)")


if __name__ == "__main__":
    main()
