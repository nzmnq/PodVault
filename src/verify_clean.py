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

  python src/verify_clean.py          # check
  python src/verify_clean.py --fix    # check and remove v2.4 frames
"""

import argparse
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mutagen.id3 import ID3
from mutagen.mp3 import MP3

import settings
from i18n import _
from musiclib import V24_ONLY, drop_v24_frames


def strip_v24(path, tags):
    """Remove v2.4 frames from a v2.3 tag without losing the year."""
    drop_v24_frames(tags)
    tags.save(path, v2_version=3, v1=2)


def main():
    ap = argparse.ArgumentParser(description=_("check the library's tags"))
    ap.add_argument("--fix", action="store_true", help=_("remove v2.4 frames from v2.3 tags"))
    args = ap.parse_args()

    cfg = settings.require()
    dest = settings.library_paths(cfg)[0]
    source = settings.path("source_dir", cfg)
    if not os.path.isdir(dest):
        sys.exit(_("Library not found: {folder}").format(folder=dest))

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

    for root, _skip, files in os.walk(dest):
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
                    strip_v24(fp, tags)
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
            print("   " + _("  ... {n} more").format(n=len(items) - limit))

    print("=" * 72)
    print(_("CHECKING {folder}").format(folder=dest))
    print("=" * 72)

    if source and os.path.isdir(source):
        src_count = sum(
            1
            for root, _skip, files in os.walk(source)
            if ".covers" not in root
            for f in files
            if f.lower().endswith(".mp3")
        )
        # The library legitimately grows: add_incoming.py adds what wasn't in
        # the original source. It's only alarming if there are FEWER tracks.
        print(_("\ntracks in the build source: {n}").format(n=src_count))
        print(_("tracks in the library: {n}").format(n=total))
        if total < src_count:
            print(_("!! tracks missing: {n}").format(n=src_count - total))
        elif total > src_count:
            print(_("OK all originals present, added since: {n}").format(n=total - src_count))
        else:
            print(_("OK all tracks present"))
    else:
        print(_("\ntracks in the library: {n}").format(n=total))

    print(_("\nartists: {n}").format(n=len(artists)))
    print(_("albums: {n}").format(n=len(albums)))
    print(_("bitrates: {list}").format(list=dict(bitrates.most_common())))

    block(_("no Album Artist"), no_album_artist)
    block(_("not ID3v2.3"), bad_version)
    block(_("not UTF-16 (Cyrillic will break)"), bad_encoding)
    if args.fix:
        print(_("\nOK v2.4 frames removed from: {n} files").format(n=fixed))
    else:
        block(_("v2.4 frames left (run with --fix)"), v24_frames)
    block(_("artist still glued"), still_multi)
    block(_("no embedded cover art"), no_art, limit=30)

    singles = [a for a, albs in by_artist.items() if "Singles" in albs]
    print(_("\nOK artists with a 'Singles' album: {n}").format(n=len(singles)))


if __name__ == "__main__":
    main()
