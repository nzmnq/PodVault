"""
The iPod, through podsync (vendor/podsync, a git submodule).

podsync reads and writes the iPod's own databases — iTunesDB with the tracks
and playlists, ArtworkDB with the small cover copies the screen really draws
— identifies the model, and checks the volume before every write. No iTunes
is involved anywhere. This module is the thin layer the rest of the project
uses: finding the iPod, reading it, the covers check, backups and eject.

    python src\\ipod.py             # what the iPod has: model, tracks, covers
    python src\\ipod.py E:          # the same for a given drive
    python src\\ipod.py --eject     # flush and eject it
"""

import argparse
import datetime
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings

PODSYNC = os.path.join(settings.ROOT, "vendor", "podsync")
if not os.path.isfile(os.path.join(PODSYNC, "podsync", "__init__.py")):
    sys.exit("podsync is missing (vendor/podsync). Fetch it with:\n"
             "  git submodule update --init")
sys.path.insert(0, PODSYNC)

import podsync.hardware as hardware              # noqa: E402
from podsync.library import database             # noqa: E402

# podsync reports refusals and repairs through logging
logging.basicConfig(level=logging.WARNING, format="  podsync: %(message)s")

KEEP_BACKUPS = 3


# ------------------------------------------------------------- the device


def itunes_running():
    """iTunes writes its own copy of the database on eject, over ours."""
    if sys.platform != "win32":
        return False     # the macOS Music app doesn't manage old iPods by itself
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq iTunes.exe"],
                         capture_output=True).stdout
    return b"iTunes.exe" in out


def open_ipod(cfg, given=None):
    """Find and identify the iPod, and make it the device podsync writes to.

    given (or the ipod_mount setting) is a drive or mount point; 'auto' or
    empty looks at every mounted volume.
    """
    given = str(given or cfg.get("ipod_mount") or "").strip()
    if given and given.lower() != "auto":
        dev = hardware.identify_mounted_ipod(given)
        if dev is None:
            sys.exit(f"{given} isn't an iPod (no iPod_Control folder).")
    else:
        found = hardware.find_ipods()
        if not found:
            sys.exit("No iPod found. Connect it (disk mode), or set its drive / mount point\n"
                     "in Settings -> iPod.")
        if len(found) > 1:
            sys.exit("Several iPods are connected: "
                     + ", ".join(d.path for d in found)
                     + "\nChoose one in Settings -> iPod, or pass --ipod.")
        dev = found[0]
    hardware.select_device(dev)
    return dev


def describe(dev):
    model = f" ({dev.model_number})" if dev.model_number else ""
    return f"{dev.display_name or 'iPod'}{model} at {dev.path}"


def load(dev):
    """Tracks and playlists of the iPod as podsync reads them.

    Plays made on the iPod since the last write are already merged in.
    """
    return database.load_device_library(Path(dev.path), raise_on_error=True)


def track_file(root, row):
    """The file of a track on the iPod, or None if it isn't there."""
    from podsync.library.media_paths import find_media_file
    return find_media_file(root, row)


def covered(root, rows):
    """db ids of the tracks the iPod can really draw a cover for.

    An artwork record alone isn't enough: tracks put on the iPod by other
    programs often carry a record with no thumbnail at all, or a thumbnail
    whose .ithmb file is gone. podsync keeps only images with a thumbnail
    that exists; a track counts if its link or its song id points at one.
    """
    from podsync.artwork.writer.chunks import read_existing_artwork
    art = os.path.join(str(root), "iPod_Control", "Artwork")
    images = read_existing_artwork(os.path.join(art, "ArtworkDB"), art)
    songs = {e["song_id"] for e in images.values()}
    out = set()
    for r in rows:
        dbid = r.get("db_track_id")
        if dbid in songs or (r.get("artwork_link") or r.get("mhii_link")) in images:
            out.add(dbid)
    return out


def eject(dev):
    from podsync.hardware.eject import eject_device
    ok, message = eject_device(dev.path, reported_volume_format=dev.reported_volume_format,
                               expected_volume_identity_key=dev.volume_identity_key)
    print(f"  {'OK' if ok else '!!'} {message}")
    return ok


# ---------------------------------------------------------------- backups


def backups_dir(cfg):
    return os.path.join(settings.path("reports_dir", cfg), "ipod-backups")


def backup(cfg, root):
    """Copy the iPod's database folders; returns the backup folder."""
    folder = backups_dir(cfg)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest, n = os.path.join(folder, stamp), 1
    while os.path.exists(dest):          # two writes within one second
        n += 1
        dest = os.path.join(folder, f"{stamp}-{n}")
    for sub in ("iTunes", "Artwork"):
        src = os.path.join(root, "iPod_Control", sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, sub))
    old = sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d)))
    for d in old[:-KEEP_BACKUPS]:
        shutil.rmtree(os.path.join(folder, d), ignore_errors=True)
    return dest


def restore(cfg, root, confirmed):
    """Put the latest backup back. confirmed(question) asks the user."""
    folder = backups_dir(cfg)
    found = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    if not found:
        sys.exit(f"No backups in {folder}.")
    src = os.path.join(folder, found[-1])
    print(f"  latest backup: {src}")
    print("  It replaces the iPod's database with the one from that moment. Tracks")
    print("  added since then vanish from the list (their files stay until the next")
    print("  sync); tracks deleted since then come back only if their files are")
    print("  still on the iPod.")
    if not confirmed("  Type 'yes' to restore: "):
        print("Cancelled.")
        return
    for sub in ("iTunes", "Artwork"):
        b = os.path.join(src, sub)
        if not os.path.isdir(b):
            continue
        target = os.path.join(root, "iPod_Control", sub)
        tmp = target + ".restoring"
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(b, tmp)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(tmp, target)
    print("Restored. Eject the iPod properly before unplugging it.")


# ------------------------------------------------------------------ report


def report(dev):
    db = load(dev)
    rows = db["tracks"]
    have = covered(dev.path, rows)
    bare = [r for r in rows if r.get("db_track_id") not in have]
    lists = [p for p in db["dataset2_standard_playlists"] if not p.get("master_flag")]
    print(f"iPod: {describe(dev)}")
    if dev.disk_size_gb:
        print(f"  {dev.free_space_gb:.1f} of {dev.disk_size_gb:.1f} GB free")
    print(f"  tracks                      : {len(rows)}")
    print(f"  playlists                   : {len(lists)}")
    print(f"  tracks with a cover         : {len(rows) - len(bare)}")
    print(f"  tracks WITHOUT a cover      : {len(bare)}")
    for r in bare[:40]:
        print(f"    {r.get('artist')} — {r.get('album')} — {r.get('title')}")
    if len(bare) > 40:
        print(f"    ... {len(bare) - 40} more")


def main():
    ap = argparse.ArgumentParser(description="what the iPod has (read only)")
    ap.add_argument("ipod", nargs="?", help="the iPod's drive or mount point")
    ap.add_argument("--eject", action="store_true", help="flush and eject the iPod")
    args = ap.parse_args()
    dev = open_ipod(settings.load() or {}, args.ipod)
    if args.eject:
        sys.exit(0 if eject(dev) else 1)
    report(dev)


if __name__ == "__main__":
    main()
