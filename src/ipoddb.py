"""
Read an iPod's own databases directly. READ ONLY — nothing is ever written.

Why: the cover the iPod shows is not the picture inside the mp3 and not what
iTunes reports through COM. The iPod draws small pre-rendered copies that
iTunes stores in iPod_Control\\Artwork\\F*_1.ithmb, indexed by ArtworkDB and
linked to tracks by the track's dbid in iTunesDB. iTunes' Artwork.Count only
describes iTunes' own record, so a track can "have" a cover for iTunes and
still show none on the screen. This module looks at what the iPod really has.

Formats follow the layout documented by the libgpod project for 5th-gen
iPods and similar (mhbd/mhit/mhod in iTunesDB, mhfd/mhii/mhni in ArtworkDB).

iTunes keeps its changes to the iPod in memory and writes these files only
when the iPod is ejected or iTunes quits, so right after a change this
module still sees the old state.

    python src\\ipoddb.py           # summary: tracks without a thumbnail
    python src\\ipoddb.py D:        # the same for a given drive
"""

import os
import struct
import sys


def _u8(b, o):
    return b[o]


def _u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def _string_mhod(b, o):
    """Text of a string mhod (title/artist/album...), or None."""
    total = _u32(b, o + 8)
    if total < 0x28:
        return None
    enc = _u32(b, o + 0x18)          # 1 = UTF-16LE, 2 = UTF-8
    length = _u32(b, o + 0x1C)
    raw = b[o + 0x28:o + 0x28 + length]
    try:
        return raw.decode("utf-8" if enc == 2 else "utf-16-le")
    except UnicodeDecodeError:
        return raw.decode("utf-16-le", errors="replace")


# ------------------------------------------------------------- iTunesDB


MHOD_TITLE, MHOD_LOCATION, MHOD_ALBUM, MHOD_ARTIST = 1, 2, 3, 4


def read_itunesdb(root):
    """Tracks on the iPod: dicts with id, dbid, artwork fields and tags."""
    path = os.path.join(root, "iPod_Control", "iTunes", "iTunesDB")
    with open(path, "rb") as f:
        b = f.read()
    if b[:4] != b"mhbd":
        raise ValueError("not an iTunesDB (no mhbd header)")

    tracks = []
    o = _u32(b, 4)                      # end of the mhbd header
    while o < len(b) - 12:
        tag = b[o:o + 4]
        if tag == b"mhsd":
            hdr, total, kind = _u32(b, o + 4), _u32(b, o + 8), _u32(b, o + 12)
            if kind == 1:               # the track list
                lt = o + hdr
                if b[lt:lt + 4] != b"mhlt":
                    raise ValueError("track list without mhlt")
                count = _u32(b, lt + 8)
                p = lt + _u32(b, lt + 4)
                for _ in range(count):
                    tracks.append(_mhit(b, p))
                    p += _u32(b, p + 8)
            o += total
        else:
            o += 4
    return tracks


def _mhit(b, o):
    if b[o:o + 4] != b"mhit":
        raise ValueError(f"expected mhit at {o:#x}")
    hdr, total, n_mhod = _u32(b, o + 4), _u32(b, o + 8), _u32(b, o + 12)
    t = {
        "id": _u32(b, o + 0x10),
        "length_ms": _u32(b, o + 0x28),
        "dbid": _u64(b, o + 0x70),
        "artwork_count": _u16(b, o + 0x7C),
        "artwork_size": _u32(b, o + 0x80),
        # 1 = has artwork, 2 = none; only meaningful in longer headers
        "has_artwork": _u8(b, o + 0xA5) if hdr > 0xA5 else None,
        "mhii_link": _u32(b, o + 0x160) if hdr >= 0x164 else 0,
        "title": None, "artist": None, "album": None, "location": None,
    }
    p = o + hdr
    for _ in range(n_mhod):
        if b[p:p + 4] != b"mhod":
            break
        kind = _u32(b, p + 12)
        key = {MHOD_TITLE: "title", MHOD_ARTIST: "artist",
               MHOD_ALBUM: "album", MHOD_LOCATION: "location"}.get(kind)
        if key:
            t[key] = _string_mhod(b, p)
        p += _u32(b, p + 8)
    return t


# ------------------------------------------------------------ ArtworkDB


def read_artworkdb(root):
    """Thumbnails on the iPod: dicts with image id, song dbid and formats."""
    path = os.path.join(root, "iPod_Control", "Artwork", "ArtworkDB")
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as f:
        b = f.read()
    if b[:4] != b"mhfd":
        raise ValueError("not an ArtworkDB (no mhfd header)")

    images = []
    o = _u32(b, 4)
    while o < len(b) - 12:
        if b[o:o + 4] == b"mhsd":
            hdr, total, kind = _u32(b, o + 4), _u32(b, o + 8), _u16(b, o + 12)
            if kind == 1:               # the image list
                li = o + hdr
                count = _u32(b, li + 8)
                p = li + _u32(b, li + 4)
                for _ in range(count):
                    images.append(_mhii(b, p))
                    p += _u32(b, p + 8)
            o += total
        else:
            o += 4
    return images


def _mhii(b, o):
    if b[o:o + 4] != b"mhii":
        raise ValueError(f"expected mhii at {o:#x}")
    hdr, total, n_child = _u32(b, o + 4), _u32(b, o + 8), _u32(b, o + 12)
    img = {"id": _u32(b, o + 0x10), "song_dbid": _u64(b, o + 0x14), "thumbs": []}
    p = o + hdr
    end = o + total
    while p < end - 12:
        if b[p:p + 4] == b"mhni":
            n_hdr = _u32(b, p + 4)
            thumb = {
                "format": _u32(b, p + 0x10),
                "offset": _u32(b, p + 0x14),
                "size": _u32(b, p + 0x18),
                "height": _u16(b, p + 0x20),
                "width": _u16(b, p + 0x22),
                "file": None,
            }
            # the filename is a child mhod (type 3) right after the mhni header
            q = p + n_hdr
            if b[q:q + 4] == b"mhod":
                s_len = _u32(b, q + 0x18)
                raw = b[q + 0x24:q + 0x24 + s_len]
                thumb["file"] = raw.decode("utf-16-le", errors="replace").lstrip(":")
            img["thumbs"].append(thumb)
            p += _u32(b, p + 8)
        else:
            p += 4
    return img


def thumbnail_pixels(root, thumb):
    """Raw RGB565 bytes of one thumbnail, read from its ithmb file."""
    path = os.path.join(root, "iPod_Control", "Artwork", thumb["file"])
    with open(path, "rb") as f:
        f.seek(thumb["offset"])
        return f.read(thumb["size"])


def thumbnail_is_blank(root, thumb):
    data = thumbnail_pixels(root, thumb)
    return not data or not data.strip(b"\x00")


def thumbnail_to_png(root, thumb, out):
    """Save a thumbnail as PNG to look at it. Needs Pillow."""
    from PIL import Image
    data = thumbnail_pixels(root, thumb)
    w, h = thumb["width"], thumb["height"]
    px = bytearray()
    for i in range(0, min(len(data), w * h * 2), 2):
        v = data[i] | (data[i + 1] << 8)
        px += bytes(((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31))
    Image.frombytes("RGB", (w, h), bytes(px)).save(out)


# ------------------------------------------------------------- analysis


def find_root(expected_tracks=None):
    """Drive root of the connected iPod ('D:\\\\'), or None.

    Looks for iPod_Control\\iTunes\\iTunesDB on every drive letter. With
    several iPods connected, expected_tracks (the count iTunes reports)
    picks the right one.
    """
    found = []
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.isfile(os.path.join(root, "iPod_Control", "iTunes", "iTunesDB")):
            found.append(root)
    if len(found) <= 1 or expected_tracks is None:
        return found[0] if found else None
    for root in found:
        try:
            if len(read_itunesdb(root)) == expected_tracks:
                return root
        except (OSError, ValueError):
            pass
    return None


def dbids_with_thumbnail(root):
    """dbids of the tracks the iPod can really draw a cover for.

    An image record alone isn't enough: old records often carry only a
    placeholder (mhaf) and no thumbnail (mhni) at all.
    """
    tracks = read_itunesdb(root)
    by_id = {}
    good = set()
    for img in read_artworkdb(root):
        by_id[img["id"]] = img
        if img["thumbs"]:
            good.add(img["song_dbid"])
    for t in tracks:
        img = by_id.get(t["mhii_link"])
        if img and img["thumbs"]:
            good.add(t["dbid"])
    return {t["dbid"] for t in tracks} & good


def artwork_report(root):
    """Join both databases: which tracks really have a thumbnail."""
    tracks = read_itunesdb(root)
    images = read_artworkdb(root)
    by_song = {}
    by_id = {}
    for img in images:
        if img["thumbs"]:
            by_song.setdefault(img["song_dbid"], []).append(img)
        by_id[img["id"]] = img
    rows = []
    for t in tracks:
        imgs = by_song.get(t["dbid"], [])
        if not imgs and t["mhii_link"] in by_id and by_id[t["mhii_link"]]["thumbs"]:
            imgs = [by_id[t["mhii_link"]]]
        thumbs = [th for img in imgs for th in img["thumbs"]]
        rows.append({**t, "images": len(imgs), "thumbs": thumbs})
    return tracks, images, rows


def main():
    root = (sys.argv[1].rstrip("\\/") + os.sep) if len(sys.argv) > 1 else find_root()
    if not root:
        sys.exit("No iPod drive found (looked for iPod_Control\\iTunes\\iTunesDB).")
    tracks, images, rows = artwork_report(root)
    with_thumb = [r for r in rows if r["thumbs"]]
    flagged = [r for r in rows if r["artwork_count"] or r["has_artwork"] == 1]
    print(f"iPod at {root}")
    print(f"  tracks in iTunesDB               : {len(tracks)}")
    print(f"  images in ArtworkDB              : {len(images)}")
    print(f"  tracks flagged as having artwork : {len(flagged)}")
    print(f"  tracks with a thumbnail on disk  : {len(with_thumb)}")
    missing = [r for r in rows if not r["thumbs"]]
    print(f"  tracks WITHOUT a thumbnail       : {len(missing)}")
    for r in missing[:40]:
        print(f"    {r['artist']} — {r['album']} — {r['title']}"
              f"   [count={r['artwork_count']} has={r['has_artwork']}]")
    if len(missing) > 40:
        print(f"    ... {len(missing) - 40} more")


if __name__ == "__main__":
    main()
