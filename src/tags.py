"""
Tags of the library's audio files, one interface for every format.

The library holds .mp3 (ID3 tags) and .m4a (MP4 atoms: AAC or Apple
Lossless, what the downloader and the FLAC -> ALAC converter produce). Every
tool reads and writes through here and never touches ID3 frames or MP4
atoms itself, so a new format is one more branch in this file.

Fields, all strings ('' when missing):
    artist, albumartist, album, title, genre, grouping, composer, year,
    track ('3' or '3/12'), disc ('1' or '1/2')
read() adds 'artists' (every value of the artist field: a tag may hold several),
'art' and 'version'.

mp3 keeps the rules an old iPod needs: ID3v2.3, UTF-16 text, no v2.4-only
frames (see musiclib.drop_v24_frames).
"""

import os
import re

from mutagen import MutagenError
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCOM, TCON, TIT1, TIT2, TPE1, TPE2, TPOS, TRCK, TYER
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

from musiclib import drop_v24_frames

AUDIO_EXTS = (".mp3", ".m4a")
FIELDS = ("artist", "albumartist", "album", "title", "genre", "grouping", "composer", "year",
          "track", "disc")

_ID3 = {"artist": TPE1, "albumartist": TPE2, "album": TALB, "title": TIT2, "genre": TCON,
        "grouping": TIT1, "composer": TCOM, "year": TYER, "track": TRCK, "disc": TPOS}
_MP4 = {"artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb", "title": "\xa9nam",
        "genre": "\xa9gen", "grouping": "\xa9grp", "composer": "\xa9wrt", "year": "\xa9day",
        "track": "trkn", "disc": "disk"}


class Unreadable(Exception):
    """The file can't be read as tagged audio; str() says why."""


def is_audio(name):
    return name.lower().endswith(AUDIO_EXTS)


def audio_files(names):
    """The library's audio files among names, sorted."""
    return sorted(n for n in names if is_audio(n))


def _mp4(path):
    return path.lower().endswith(".m4a")


def _pair(v):
    """MP4 (3, 12) -> '3/12'."""
    n, total = (list(v) + [0, 0])[:2]
    return f"{n}/{total}" if total else (str(n) if n else "")


def _unpair(s):
    """'3/12' -> (3, 12); '' -> None."""
    m = re.match(r"^\s*(\d+)(?:\s*/\s*(\d+))?", s or "")
    return (int(m.group(1)), int(m.group(2) or 0)) if m else None


def read(path):
    """{field: str} plus 'art' (bool: a picture is embedded) and 'version' ('2.3.0', 'MP4')."""
    try:
        if _mp4(path):
            t = MP4(path).tags or {}
            out = {}
            for f, key in _MP4.items():
                v = t.get(key)
                if not v:
                    out[f] = ""
                elif f in ("track", "disc"):
                    out[f] = _pair(v[0])
                else:
                    out[f] = str(v[0]).strip()
            out["artists"] = [str(x).strip() for x in t.get("\xa9ART") or [] if str(x).strip()]
            if not out["genre"] and t.get("gnre"):                # an old numeric genre
                from mutagen._constants import GENRES
                i = t["gnre"][0] - 1
                out["genre"] = GENRES[i] if 0 <= i < len(GENRES) else ""
            out["year"] = out["year"][:4]
            out["art"] = bool(t.get("covr"))
            out["version"] = "MP4"
            return out
        tags = ID3(path)
    except ID3NoHeaderError:
        raise Unreadable("no ID3 tag") from None
    except (MutagenError, OSError, ValueError) as e:
        raise Unreadable(str(e)) from None

    def one(key):
        v = tags.get(key)
        return str(v.text[0]).strip() if v is not None and getattr(v, "text", None) else ""
    out = {f: one(frame.__name__) for f, frame in _ID3.items()}
    tpe1 = tags.get("TPE1")
    out["artists"] = [str(x).strip() for x in (tpe1.text if tpe1 is not None else []) if str(x).strip()]
    out["year"] = (out["year"] or one("TDRC"))[:4]
    out["art"] = bool(tags.getall("APIC"))
    out["version"] = ".".join(map(str, tags.version))
    return out


def info(path):
    """{'length' s, 'bitrate' kbps, 'bps' (exact), 'sample_rate', 'filetype' ('mp3'/'m4a'), 'kind'}."""
    try:
        if _mp4(path):
            i = MP4(path).info
            lossless = (getattr(i, "codec", "") or "").lower() == "alac"
            kind = "Apple Lossless audio file" if lossless else "AAC audio file"
            filetype = "m4a"
        else:
            i = MP3(path).info
            kind, filetype = "MPEG audio file", "mp3"
    except (MutagenError, OSError, ValueError) as e:
        raise Unreadable(str(e)) from None
    return {"length": i.length or 0, "bitrate": int((i.bitrate or 0) / 1000), "bps": i.bitrate or 0,
            "sample_rate": getattr(i, "sample_rate", 44100) or 44100,
            "filetype": filetype, "kind": kind}


def write(path, values, replace=False):
    """Set the fields in values; '' or None removes one. replace: clear every field first."""
    if replace:
        values = {**{f: None for f in FIELDS}, **values}
    if "year" in values:                                  # just the year, in every format
        m = re.search(r"\d{4}", str(values["year"] or ""))
        values = {**values, "year": m.group(0) if m else ""}
    if _mp4(path):
        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        for field, v in values.items():
            key = _MP4[field]
            if field in ("track", "disc"):
                pair = _unpair(v)
                if pair:
                    f.tags[key] = [pair]
                else:
                    f.tags.pop(key, None)
            elif v:
                f.tags[key] = [str(v)]
            else:
                f.tags.pop(key, None)
                if field == "genre":
                    f.tags.pop("gnre", None)
        f.save()
        return
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for field, v in values.items():
        frame = _ID3[field]
        tags.delall(frame.__name__)
        if field == "year":
            tags.delall("TDRC")
        if v:
            tags.add(frame(encoding=1, text=[str(v)]))   # UTF-16: an old iPod garbles Cyrillic otherwise
    save_id3(tags, path)


def save_id3(tags, path):
    """Save an ID3 tag the way an old iPod reads it: v2.3, no v2.4-only frames."""
    drop_v24_frames(tags)
    tags.save(path, v2_version=3, v1=2)


def cover(path):
    """The embedded picture's bytes, or None."""
    try:
        if _mp4(path):
            covr = (MP4(path).tags or {}).get("covr")
            return bytes(covr[0]) if covr else None
        pics = ID3(path).getall("APIC")
        return pics[0].data if pics else None
    except (MutagenError, OSError, ValueError):
        return None


def set_cover(path, jpeg):
    """Replace the embedded picture with a JPEG; None removes it."""
    if _mp4(path):
        f = MP4(path)
        if f.tags is None:
            f.add_tags()
        if jpeg:
            f.tags["covr"] = [MP4Cover(jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
        else:
            f.tags.pop("covr", None)
        f.save()
        return
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    tags.delall("APIC")
    if jpeg:
        tags.add(APIC(encoding=0, mime="image/jpeg", type=3, desc="", data=jpeg))
    save_id3(tags, path)


def extension(path):
    """'.mp3' or '.m4a' — what a copy of this file must be called."""
    return os.path.splitext(path)[1].lower()
