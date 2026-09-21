"""
Find cover art for tracks that have none, and embed it.

Sources: the public Deezer API (no key needed, and it knows this catalogue
best — the library was collected from there); fallback: MusicBrainz plus
the Cover Art Archive, which is more accurate for Western releases.

What's found is NOT applied blindly: the artist name in the response is
checked against the tag, and anything that doesn't match is flagged as
doubtful. A wrong cover is worse than none, so the default is a dry run.

The library folder and the cover size come from the settings.

  python src\\fetch_covers.py            # show what was found
  python src\\fetch_covers.py --apply    # embed what matched
"""

import argparse
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import certifi
from mutagen.id3 import APIC, ID3
from PIL import Image

import settings

# Set from the settings in main()
LIBRARY = None
SIZE = (500, 500)

SSL_CTX = ssl.create_default_context(cafile=certifi.where())
UA = "SortedMusic/1.0 (personal library tagger)"  # ASCII only: HTTP headers are encoded as latin-1


from musiclib import first_artist, norm, strip_edition, strip_feat


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=20) as r:
        return json.loads(r.read().decode())


def get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as r:
        return r.read()


# ------------------------------------------------------------ search


def deezer_album(artist, album, strict=True):
    q = f'artist:"{artist}" album:"{album}"' if strict else f"{artist} {album}"
    d = get_json(f"https://api.deezer.com/search/album?q={urllib.parse.quote(q)}&limit=5")
    for it in d.get("data", []):
        yield {
            "artist": (it.get("artist") or {}).get("name"),
            "title": it.get("title"),
            "url": it.get("cover_big") or it.get("cover_xl"),
            "via": "deezer/album" + ("" if strict else "~"),
        }


def deezer_track(artist, title, strict=True):
    q = f'artist:"{artist}" track:"{title}"' if strict else f"{artist} {title}"
    d = get_json(f"https://api.deezer.com/search?q={urllib.parse.quote(q)}&limit=5")
    for it in d.get("data", []):
        alb = it.get("album") or {}
        yield {
            "artist": (it.get("artist") or {}).get("name"),
            "title": alb.get("title") or it.get("title"),
            "url": alb.get("cover_big") or alb.get("cover_xl"),
            "via": "deezer/track" + ("" if strict else "~"),
        }


def musicbrainz(artist, album):
    q = urllib.parse.quote(f'artist:"{artist}" AND release:"{album}"')
    d = get_json(f"https://musicbrainz.org/ws/2/release?query={q}&fmt=json&limit=5")
    for rel in d.get("releases", []):
        credit = rel.get("artist-credit") or [{}]
        yield {
            "artist": credit[0].get("name"),
            "title": rel.get("title"),
            "url": f"https://coverartarchive.org/release/{rel['id']}/front-500",
            "via": "musicbrainz",
        }


def find_cover(artist, album, is_single, title, first_track=None):
    """Go through the sources and return the first candidate with a working image."""
    a1 = first_artist(artist)  # in case the artist is comma-glued
    attempts = []
    if is_single:
        t = strip_feat(title)
        attempts += [
            lambda: deezer_track(artist, t),
            lambda: deezer_album(artist, t),
            lambda: deezer_track(a1, t),
            lambda: deezer_track(artist, t, strict=False),
            lambda: musicbrainz(a1, t),
        ]
    else:
        base = strip_edition(album)
        attempts += [
            lambda: deezer_album(artist, album),
            lambda: deezer_album(artist, base),  # without the edition suffix
            lambda: deezer_album(a1, base),
            lambda: deezer_album(artist, base, strict=False),
            lambda: musicbrainz(artist, base),
        ]
        if first_track:
            # compilations often have a different album title in each
            # catalogue, but a specific track is found more reliably
            attempts.append(lambda: deezer_track(artist, strip_feat(first_track)))

    for make in attempts:
        try:
            candidates = list(make())
        except Exception as e:
            print(f"      (source unavailable: {e})")
            continue
        for c in candidates:
            if not c.get("url"):
                continue
            c["artist_match"] = norm(c.get("artist")) == norm(artist)
            try:
                data = get_bytes(c["url"])
                Image.open(io.BytesIO(data)).verify()
            except Exception:
                continue
            c["data"] = data
            return c
        time.sleep(0.3)  # don't hit the API more often than needed
    return None


# ------------------------------------------------------------ main


def collect_missing():
    """Albums that have tracks without embedded cover art."""
    groups = defaultdict(lambda: {"artist": None, "album": None, "files": []})
    for root, _, files in os.walk(LIBRARY):
        for fn in sorted(f for f in files if f.lower().endswith(".mp3")):
            fp = os.path.join(root, fn)
            tags = ID3(fp)
            if tags.getall("APIC"):
                continue

            def one(k):
                v = tags.get(k)
                return str(v.text[0]) if v and v.text else None

            g = groups[os.path.relpath(root, LIBRARY)]
            g["artist"] = g["artist"] or one("TPE2") or one("TPE1")
            g["album"] = g["album"] or one("TALB")
            g["files"].append({"path": fp, "title": one("TIT2") or fn})
    return groups


def embed(path, data, folder_jpg):
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.size != SIZE:
        im = im.resize(SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    jpg = buf.getvalue()

    tags = ID3(path)
    tags.delall("APIC")
    tags.add(APIC(encoding=0, mime="image/jpeg", type=3, desc="", data=jpg))
    # mutagen turns TYER into TDRC when reading v2.3 and writes both on save.
    # TDRC is a v2.4 frame; an old iPod won't understand it in a v2.3 tag.
    tags.delall("TDRC")
    tags.save(path, v2_version=3, v1=2)

    if not os.path.exists(folder_jpg):
        with open(folder_jpg, "wb") as f:
            f.write(jpg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="embed what was found")
    args = ap.parse_args()

    global LIBRARY, SIZE
    cfg = settings.require()
    LIBRARY = settings.library_paths(cfg)[0]
    SIZE = (int(cfg["cover_size"]), int(cfg["cover_size"]))
    if not os.path.isdir(LIBRARY):
        sys.exit(f"Library not found: {LIBRARY}")

    groups = collect_missing()
    total = sum(len(g["files"]) for g in groups.values())
    print(f"Tracks without cover art: {total} in {len(groups)} albums\n")
    found, doubtful, missing = [], [], []

    for rel, g in sorted(groups.items()):
        is_single = (g["album"] or "").lower() == "singles"
        print(f"{rel}")

        if is_single:
            # in Singles every track is its own release with its own cover,
            # so search for each one separately, not once per folder
            targets = [(f, f["title"]) for f in g["files"]]
        else:
            targets = [(None, None)]

        for f, title in targets:
            label = f"  · {title}" if title else f"  · {g['album']}"
            hit = find_cover(
                g["artist"], g["album"], is_single, title,
                first_track=g["files"][0]["title"] if g["files"] else None,
            )
            if not hit:
                print(f"{label} -> not found")
                missing.append((rel, title or g["album"]))
                continue

            mark = "OK" if hit["artist_match"] else "?? "
            print(f"{label} -> [{mark}] {hit['artist']} — {hit['title']}  ({hit['via']})")

            files = [f] if f else g["files"]
            rec = (rel, title or g["album"], hit, files)
            (found if hit["artist_match"] else doubtful).append(rec)

    print()
    print("=" * 70)
    print(f"  artist matched   : {len(found)}")
    print(f"  artist different : {len(doubtful)}")
    print(f"  not found        : {len(missing)}")

    if doubtful:
        print("\n  Doubtful (artist in the response didn't match the tag):")
        for rel, what, hit, _ in doubtful:
            print(f"    {rel} / {what}")
            print(f"      suggested: {hit['artist']} — {hit['title']}")
    if missing:
        print("\n  Not found anywhere:")
        for rel, what in missing:
            print(f"    {rel} / {what}")

    if not args.apply:
        print("\nNothing embedded. Run with --apply to apply only the matches.")
        return

    n = 0
    for rel, what, hit, files in found:
        folder_jpg = os.path.join(os.path.dirname(files[0]["path"]), "folder.jpg")
        for f in files:
            embed(f["path"], hit["data"], folder_jpg)
            n += 1
    print(f"\nCovers embedded: {n}")
    if doubtful:
        print(f"Doubtful ({len(doubtful)}) skipped — better check them by eye.")


if __name__ == "__main__":
    main()
