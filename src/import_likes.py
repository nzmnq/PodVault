"""
Bring a "what I listen to" list into one common format.

Takes what can be had without any subscription or API access and brings it
into the single format find_missing.py reads:

  1. Spotify data export (spotify.com -> Privacy Settings -> Download your
     data). The email arrives within a few days, no Premium needed.
     YourLibrary.json and Playlist*.json are understood.
  2. An Apple Music playlist saved as a web page (.html). Replaces the old
     other/fetch_tracklist_from_html.py; parsed with the standard library,
     so BeautifulSoup is no longer needed.
  3. A plain text list, one album or track per line:
         Artist — Album
         Artist — Album — Track

Output format (<reports>/likes.json):

  {"source": ..., "albums": [
      {"artist", "album", "year", "url",
       "tracks": [...full tracklist, if known...],
       "liked":  [...what was liked...]}]}

None of these sources gives a full album tracklist, so find_missing.py
compares track by track — and says so in its report. When a source has no
album (Apple Music pages), the album stays empty and the track is looked
for across all of the artist's albums.

  python src\\import_likes.py <file or folder>
"""

import json
import os
import re
import sys
import time
from collections import defaultdict
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings


def add(bucket, artist, album, title=None, year="", url=""):
    if not artist:
        return
    # An unknown album stays empty. It used to become 'Singles', and then a
    # track was compared only with the artist's singles and reported missing
    # even though it sat in a proper album.
    album = (album or "").strip()
    rec = bucket[(artist.strip(), album)]
    rec["artist"] = artist.strip()
    rec["album"] = album
    rec["year"] = rec["year"] or year
    rec["url"] = rec["url"] or url
    if title:
        rec["liked"].add(title.strip())


def new_bucket():
    return defaultdict(lambda: {"artist": "", "album": "", "year": "", "url": "",
                                "tracks": [], "liked": set()})


# ------------------------------------------------------------- readers


def from_your_library(data, bucket):
    """YourLibrary.json from the Spotify data export."""
    n = 0
    for t in data.get("tracks") or []:
        add(bucket, t.get("artist"), t.get("album"), t.get("track"))
        n += 1
    for a in data.get("albums") or []:
        add(bucket, a.get("artist"), a.get("album"))
        n += 1
    return n


def from_playlists(data, bucket):
    """Playlist1.json from the Spotify data export."""
    n = 0
    for pl in data.get("playlists") or []:
        for item in pl.get("items") or []:
            t = item.get("track") or {}
            if not t.get("trackName"):
                continue
            add(bucket, t.get("artistName"), t.get("albumName"), t.get("trackName"))
            n += 1
    return n


class AppleMusicPage(HTMLParser):
    """Rows of an Apple Music playlist page saved as HTML.

    A row is a <div role="row"> (or class 'songs-list-row'); inside it the
    title sits in an element with class 'song-name'/'track-title' and the
    artist in one with 'by-line'/'artist'. The same markers the old
    BeautifulSoup script looked for.
    """

    ROW = re.compile(r"songs-list-row", re.I)
    TITLE = re.compile(r"song-name|track-title", re.I)
    ARTIST = re.compile(r"by-line|artist", re.I)

    def __init__(self):
        super().__init__()
        self.rows = []
        self.depth = 0
        self.row_depth = None
        self.cap = None          # ('title'|'artist', depth it started at)
        self.cur = {}
        self.buf = []

    def handle_starttag(self, tag, attrs):
        self.depth += 1
        a = dict(attrs)
        cls = a.get("class") or ""
        if self.row_depth is None and (a.get("role") == "row" or self.ROW.search(cls)):
            self.row_depth, self.cur = self.depth, {}
            return
        if self.row_depth is not None and self.cap is None:
            if self.TITLE.search(cls) and "title" not in self.cur:
                self.cap, self.buf = ("title", self.depth), []
            elif self.ARTIST.search(cls) and "artist" not in self.cur:
                self.cap, self.buf = ("artist", self.depth), []

    def handle_endtag(self, tag):
        if self.cap and self.depth == self.cap[1]:
            text = " ".join("".join(self.buf).split())
            if text:
                self.cur[self.cap[0]] = text
            self.cap = None
        if self.row_depth is not None and self.depth == self.row_depth:
            if self.cur.get("title") and self.cur.get("artist"):
                self.rows.append(self.cur)
            self.row_depth, self.cur = None, {}
        self.depth -= 1

    def handle_data(self, data):
        if self.cap:
            self.buf.append(data)


def from_apple_html(path, bucket):
    """An Apple Music playlist page saved from the browser."""
    p = AppleMusicPage()
    with open(path, encoding="utf-8", errors="replace") as f:
        p.feed(f.read())
    for r in p.rows:
        add(bucket, r["artist"], "", r["title"])
    return len(p.rows)


def from_text(path, bucket):
    """Text list: 'Artist — Album', optionally with a third field for the track."""
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            s = re.sub(r"^\[.?\]\s*", "", s)        # tolerate [A]/[R] marks
            s = re.sub(r"\s{2,}\(.*\)$", "", s)     # and the stats tail
            parts = [p.strip() for p in re.split(r"\s+[—–-]\s+", s)]
            if len(parts) == 1:
                continue
            add(bucket, parts[0], parts[1], parts[2] if len(parts) > 2 else None)
            n += 1
    return n


def write(bucket, source, out):
    """Write likes.json."""
    albums = []
    for rec in bucket.values():
        rec = dict(rec)
        rec["liked"] = sorted(rec["liked"])
        albums.append(rec)
    albums.sort(key=lambda r: (r["artist"].lower(), r["album"].lower()))

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"source": source,
                   "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
                   "albums": albums}, f, ensure_ascii=False, indent=2)
    return albums


def read_json_any(path, bucket):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        if "playlists" in data:
            return from_playlists(data, bucket), "Spotify export: playlists"
        if "tracks" in data or "albums" in data:
            return from_your_library(data, bucket), "Spotify export: library"
    raise ValueError("can't make sense of this JSON's structure")


# ---------------------------------------------------------------- main


def main():
    cfg = settings.require()
    out = os.path.join(settings.path("reports_dir", cfg), "likes.json")

    if len(sys.argv) < 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    target = sys.argv[1]
    if not os.path.exists(target):
        sys.exit(f"Not found: {target}")

    exts = (".json", ".txt", ".html", ".htm")
    files = []
    if os.path.isdir(target):
        for root, _, fs in os.walk(target):
            for fn in sorted(fs):
                if fn.lower().endswith(exts):
                    files.append(os.path.join(root, fn))
        if not files:
            sys.exit(f"No .json, .txt or .html in the folder: {target}")
    else:
        files = [target]

    bucket = new_bucket()
    sources = []
    for path in files:
        name = os.path.basename(path)
        low = path.lower()
        try:
            if low.endswith(".json"):
                n, kind = read_json_any(path, bucket)
            elif low.endswith((".html", ".htm")):
                n, kind = from_apple_html(path, bucket), "Apple Music page"
            else:
                n, kind = from_text(path, bucket), "text list"
        except Exception as e:
            print(f"  skipped {name}: {e}")
            continue
        if n:
            print(f"  {name}: {n} entries ({kind})")
            sources.append(kind)
        else:
            print(f"  {name}: nothing recognised")

    if not bucket:
        sys.exit("Nothing could be read.")

    albums = write(bucket, ", ".join(sorted(set(sources))) or "?", out)
    print(f"\nDone: {out}")
    print(f"  albums       : {len(albums)}")
    print(f"  liked tracks : {sum(len(a['liked']) for a in albums)}")
    print("\nNext: python src\\find_missing.py")


if __name__ == "__main__":
    main()
