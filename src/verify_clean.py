"""
Check the library's tags. With --fix, also repair what can be repaired.

Answers the question "did we really get what we intended": does every
track have an Album Artist, is it ID3v2.3 + UTF-16 all over (otherwise an
old iPod garbles Cyrillic), are any v2.4 frames left inside v2.3 tags.

--fix removes those v2.4 frames (it replaces the old strip_v24_frames.py).
When reading a v2.3 tag, mutagen substitutes the newer TDRC for TYER, and
if the file is then saved, both get written. Mixed versions are exactly
what an old iPod trips over. The year is kept: if it lived only in TDRC,
it's moved into TYER.

If the initial build source is set in the settings, the track count is
also compared against it.

  python src\\verify_clean.py          # check
  python src\\verify_clean.py --fix    # check and remove v2.4 frames
"""

import argparse
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mutagen.id3 import ID3, TYER
from mutagen.mp3 import MP3

import settings

# Frames that only exist in ID3v2.4
V24_ONLY = ("TDRC", "TDOR", "TDRL", "TIPL", "TMCL", "TSOA", "TSOP", "TSOT")


def strip_v24(path, tags, present):
    """Remove v2.4 frames from a v2.3 tag without losing the year."""
    if tags.get("TYER") is None and tags.get("TDRC") is not None:
        m = re.search(r"\d{4}", str(tags["TDRC"].text[0]))
        if m:
            tags.add(TYER(encoding=1, text=[m.group(0)]))
    for k in present:
        tags.delall(k)
    tags.save(path, v2_version=3, v1=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="remove v2.4 frames from v2.3 tags")
    args = ap.parse_args()

    cfg = settings.require()
    dest = settings.library_paths(cfg)[0]
    source = settings.path("source_dir", cfg)
    if not os.path.isdir(dest):
        sys.exit(f"Library not found: {dest}")

    total = 0
    no_album_artist = []
    bad_version = []
    bad_encoding = []
    v24_frames = []
    fixed = 0
    no_art = []
    still_multi = []
    bitrates = Counter()
    artists = set()
    albums = set()
    by_artist = defaultdict(set)

    for root, _, files in os.walk(dest):
        for fn in files:
            if not fn.lower().endswith(".mp3"):
                continue
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, dest)
            total += 1

            # translate=False — otherwise mutagen shows what isn't in the file
            tags = ID3(fp, translate=False)
            audio = MP3(fp)
            bitrates[int(audio.info.bitrate / 1000)] += 1

            if tags.version[:2] != (2, 3):
                bad_version.append((rel, tags.version))

            def one(k):
                v = tags.get(k)
                return str(v.text[0]) if v and v.text else None

            aa, ar, al = one("TPE2"), one("TPE1"), one("TALB")
            if not aa:
                no_album_artist.append(rel)
            else:
                artists.add(aa)
                by_artist[aa].add(al)
            if al:
                albums.add((aa, al))

            for k in ("TPE1", "TPE2", "TIT2", "TALB"):
                f = tags.get(k)
                if f is not None and getattr(f, "encoding", None) != 1:
                    bad_encoding.append((rel, k, getattr(f, "encoding", None)))

            present = [k for k in V24_ONLY if tags.get(k) is not None]
            if present:
                if args.fix:
                    strip_v24(fp, tags, present)
                    fixed += 1
                else:
                    v24_frames.append((rel, ", ".join(present)))

            # the artist is glued into one string again — the split didn't work
            if ar and re.search(r"\s*[/;]\s*", ar) and ar.lower() != "ac/dc":
                still_multi.append((rel, ar))

            if not tags.getall("APIC"):
                no_art.append(rel)

    def block(title, items, limit=10):
        mark = "OK " if not items else "!! "
        print(f"\n{mark}{title}: {len(items)}")
        for i in items[:limit]:
            print(f"     {i}")
        if len(items) > limit:
            print(f"     ... {len(items) - limit} more")

    print("=" * 72)
    print(f"CHECKING {dest}")
    print("=" * 72)

    if source and os.path.isdir(source):
        src_count = sum(
            1
            for root, _, files in os.walk(source)
            if ".covers" not in root
            for f in files
            if f.lower().endswith(".mp3")
        )
        # The library legitimately grows: add_incoming.py adds what wasn't in
        # the original source. It's only alarming if there are FEWER tracks.
        print(f"\ntracks in the build source : {src_count}")
        print(f"tracks in the library      : {total}")
        if total < src_count:
            print(f"!! tracks missing: {src_count - total}")
        elif total > src_count:
            print(f"OK all originals present, added since: {total - src_count}")
        else:
            print("OK all tracks present")
    else:
        print(f"\ntracks in the library : {total}")

    print(f"\nartists  : {len(artists)}")
    print(f"albums   : {len(albums)}")
    print(f"bitrates : {dict(bitrates.most_common())}")

    block("no Album Artist", no_album_artist)
    block("not ID3v2.3", bad_version)
    block("not UTF-16 (Cyrillic will break)", bad_encoding)
    if args.fix:
        print(f"\nOK v2.4 frames removed from: {fixed} files")
    else:
        block("v2.4 frames left (run with --fix)", v24_frames)
    block("artist still glued", still_multi)
    block("no embedded cover art", no_art, limit=30)

    singles = [a for a, albs in by_artist.items() if "Singles" in albs]
    print(f"\nOK artists with a 'Singles' album: {len(singles)}")


if __name__ == "__main__":
    main()
