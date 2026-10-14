"""
Find cover art for tracks that have none, and embed it.

Sources, none of them needs a key: Apple Music's public search (covers up to
3000 px, small underground releases too), then the public Deezer API, then
MusicBrainz plus the Cover Art Archive, which is more accurate for Western
releases. (Spotify was tried: its API answers only with an app's keys, and
keys shipped in open source are as good as published.) An album's own cover — embedded in one
of its tracks, or folder.jpg — always comes first.

What's found is NOT applied blindly: the artist name in the response is
checked against the tag, and anything that doesn't match is flagged as
doubtful. A wrong cover is worse than none, so the default is a dry run.

The library folder and the cover size come from the settings.

  python src/fetch_covers.py            # show what was found
  python src/fetch_covers.py --apply    # embed what matched
"""

import argparse
import io
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import certifi
from PIL import Image

import settings
import tags as audiotags
from i18n import _

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


def itunes(artist, name, kind="album"):
    """Apple Music's public search (no key): kind 'album' or 'track'.

    The search for 'artist name' sometimes finds nothing although the release is there
    (друШлак's EP): then the artist's own albums or songs are listed and compared by name,
    ' - EP' / ' - Single' and features left out.
    """
    import re
    entity = "album" if kind == "album" else "song"
    q = urllib.parse.urlencode({"term": f"{artist} {name}", "entity": entity, "limit": 10})
    rows = get_json(f"https://itunes.apple.com/search?{q}").get("results", [])
    if not rows:
        q = urllib.parse.urlencode({"term": artist, "entity": "musicArtist", "limit": 1})
        found = get_json(f"https://itunes.apple.com/search?{q}").get("results", [])
        if found:
            rows = get_json(f"https://itunes.apple.com/lookup?id={found[0]['artistId']}"
                            f"&entity={entity}&limit=200").get("results", [])[1:]
    want = norm(strip_feat(strip_edition(name)))

    def bare(s):
        return norm(strip_feat(re.sub(r"\s+-\s+(EP|Single)$", "", s or "")))
    for x in rows:
        got = bare(x.get("collectionName") if kind == "album" else x.get("trackName"))
        if not (want and got and (want in got or got in want)) or not x.get("artworkUrl100"):
            continue
        if not same_artist(artist, x.get("artistName")):     # its search matches loosely
            continue
        yield {
            "artist": x.get("artistName"),
            "title": x.get("collectionName"),
            "url": x["artworkUrl100"].replace("100x100bb", "1200x1200bb"),
            "via": "apple/" + kind,
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


def same_artist(ours, theirs):
    """Our artist among the catalogue's credits ('друШлак & бліда поганка' counts for друШлак)."""
    import re
    names = re.split(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b)\s*", theirs or "", flags=re.I)
    return norm(ours) in {norm(n) for n in names} | {norm(theirs)}


def find_cover(artist, album, is_single, title, first_track=None):
    """Go through the sources and return the first candidate with a working image."""
    a1 = first_artist(artist)  # in case the artist is comma-glued
    attempts = []
    if is_single:
        t = strip_feat(title)
        attempts += [
            lambda: itunes(artist, t, "track"),
            lambda: deezer_track(artist, t),
            lambda: deezer_album(artist, t),
            lambda: deezer_track(a1, t),
            lambda: deezer_track(artist, t, strict=False),
            lambda: musicbrainz(a1, t),
        ]
    else:
        base = strip_edition(album)
        attempts += [
            lambda: itunes(artist, base, "album"),
            lambda: deezer_album(artist, album),
            lambda: deezer_album(artist, base),  # without the edition suffix
            lambda: deezer_album(a1, base),
            lambda: deezer_album(artist, base, strict=False),
            lambda: musicbrainz(artist, base),
        ]
        if first_track:
            # compilations often have a different album title in each
            # catalogue, but a specific track is found more reliably
            attempts.append(lambda: itunes(artist, strip_feat(first_track), "track"))
            attempts.append(lambda: deezer_track(artist, strip_feat(first_track)))
            # the strict query misses some Cyrillic titles the plain one finds; the artist is still checked
            attempts.append(lambda: deezer_track(artist, strip_feat(first_track), strict=False))

    for make in attempts:
        try:
            candidates = list(make())
        except Exception as e:
            settings.problem(_("source unavailable: {error}").format(error=e))
            continue
        for c in candidates:
            if not c.get("url"):
                continue
            c["artist_match"] = same_artist(artist, c.get("artist"))
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
    """Albums that have tracks without embedded cover art.

    'local' is a track of the same folder that has one: an album's cover is
    taken from there (or from folder.jpg) before anything is searched online.
    """
    groups = defaultdict(lambda: {"artist": None, "album": None, "files": [], "local": None})
    with_art = {}
    # the index counts each album's tracks without a cover: only those albums are opened
    import library
    library.listing(LIBRARY)
    todo = sorted(rel for rel, rec in library.album_info(LIBRARY).items() if rec.get("no_art"))
    files = [(rel, os.path.join(LIBRARY, rel, fn)) for rel in todo
             for fn in audiotags.audio_files(os.listdir(os.path.join(LIBRARY, rel)))]
    seen = settings.Progress(_("tags read: {n}/{total} files"), total=len(files), every=10)
    for rel, fp in files:
        seen.tick()
        try:
            t = audiotags.read(fp)
        except audiotags.Unreadable:
            continue
        if t["art"]:
            with_art.setdefault(rel, fp)
            continue
        g = groups[rel]
        g["artist"] = g["artist"] or t["albumartist"] or t["artist"] or None
        g["album"] = g["album"] or t["album"] or None
        g["files"].append({"path": fp, "title": t["title"] or os.path.basename(fp)})
    seen.end()
    for rel, g in groups.items():
        g["local"] = with_art.get(rel)
    return groups


def local_cover(rel, g):
    """The album's own cover: embedded in one of its tracks, else folder.jpg. None for
    'Singles' (every single has its own) or when there's none."""
    if (g["album"] or "").lower() == "singles":
        return None
    data, via = (audiotags.cover(g["local"]), _("embedded in another track")) if g["local"] else (None, None)
    jpg = os.path.join(LIBRARY, rel, "folder.jpg")
    if not data and os.path.exists(jpg):
        with open(jpg, "rb") as f:
            data, via = f.read(), "folder.jpg"
    if not data:
        return None
    return {"artist": g["artist"], "title": g["album"], "data": data, "via": via, "artist_match": True}


def embed(path, data, folder_jpg):
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.size != SIZE:
        im = im.resize(SIZE, Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    jpg = buf.getvalue()

    audiotags.set_cover(path, jpg)

    if not os.path.exists(folder_jpg):
        with open(folder_jpg, "wb") as f:
            f.write(jpg)


def main():
    ap = argparse.ArgumentParser(description=_("find cover art for albums without one"))
    ap.add_argument("--apply", action="store_true", help=_("embed what was found"))
    args = ap.parse_args()

    global LIBRARY, SIZE
    cfg = settings.require()
    LIBRARY = settings.library_paths(cfg)[0]
    SIZE = (int(cfg["cover_size"]), int(cfg["cover_size"]))
    if not os.path.isdir(LIBRARY):
        sys.exit(_("Library not found: {folder}").format(folder=LIBRARY))

    print(_("Looking for tracks without cover art in {folder}").format(folder=LIBRARY))
    groups = collect_missing()
    total = sum(len(g["files"]) for g in groups.values())
    print(_("Tracks without cover art: {tracks} in {albums} albums").format(tracks=total, albums=len(groups)))
    if groups:
        print(_("Searching Apple Music, Deezer, then MusicBrainz, album by album:\n"))
    found, doubtful, missing = [], [], []

    for rel, g in sorted(groups.items()):
        # the old 'Singles' album, or a single filed as an album of its own (one track, its title)
        is_single = (g["album"] or "").lower() == "singles" or (
            len(g["files"]) == 1 and norm(strip_feat(g["files"][0]["title"])) in
            (norm(g["album"]), norm((g["album"] or "").removesuffix(" - Single"))))
        print(f"{rel}")

        if is_single:
            # in Singles every track is its own release with its own cover,
            # so search for each one separately, not once per folder
            targets = [(f, f["title"]) for f in g["files"]]
        else:
            targets = [(None, None)]

        for f, title in targets:
            label = f"  · {title}" if title else f"  · {g['album']}"
            hit = local_cover(rel, g) if not is_single else None
            hit = hit or find_cover(
                g["artist"], g["album"], is_single, title,
                first_track=g["files"][0]["title"] if g["files"] else None,
            )
            if not hit:
                print(f"{label} -> " + _("not found"))
                missing.append((rel, title or g["album"]))
                continue

            mark = "OK" if hit["artist_match"] else "?? "
            print(f"{label} -> [{mark}] {hit['artist']} — {hit['title']}  ({hit['via']})")

            files = [f] if f else g["files"]
            rec = (rel, title or g["album"], hit, files)
            (found if hit["artist_match"] else doubtful).append(rec)

    print()
    print("=" * 70)
    rows = [(_("artist matched"), len(found)), (_("artist different"), len(doubtful)),
            (_("not found"), len(missing))]
    width = max(len(label) for label, _v in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")

    if doubtful:
        print(_("\n  Doubtful (artist in the response didn't match the tag):"))
        for rel, what, hit, _skip in doubtful:
            print(f"    {rel} / {what}")
            print("      " + _("suggested: {artist} — {title}").format(artist=hit['artist'], title=hit['title']))
    if missing:
        print(_("\n  Not found anywhere:"))
        for rel, what in missing:
            print(f"    {rel} / {what}")

    if not args.apply:
        if found:
            print(_("\nNothing embedded. Run with --apply to apply only the matches."))
        else:
            settings.nothing_to_apply()
        return

    n = 0
    print()
    for rel, what, hit, files in found:
        folder_jpg = os.path.join(os.path.dirname(files[0]["path"]), "folder.jpg")
        print(_("  embedding: {album} ({n} tracks)").format(album=f"{rel} / {what}", n=len(files)))
        for f in files:
            embed(f["path"], hit["data"], folder_jpg)
            n += 1
    print(_("\nCovers embedded: {n}").format(n=n))
    if doubtful:
        print(_("Doubtful ({n}) skipped — better check them by eye.").format(n=len(doubtful)))


if __name__ == "__main__":
    settings.run(main)
