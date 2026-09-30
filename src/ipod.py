"""
The iPod, through podsync (vendor/podsync, a git submodule).

podsync reads and writes the iPod's own databases — iTunesDB with the tracks
and playlists, ArtworkDB with the small cover copies the screen really draws
— identifies the model, and checks the volume before every write. No iTunes
is involved anywhere. This module is the thin layer the rest of the project
uses: finding the iPod, reading it, the covers check, backups and eject.

    python src/ipod.py             # what the iPod has: model, tracks, covers
    python src/ipod.py E:          # the same for a given drive
    python src/ipod.py --eject     # flush and eject it
"""

import argparse
import datetime
import logging
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings
from i18n import _

PODSYNC = os.path.join(settings.ROOT, "vendor", "podsync")
if not os.path.isfile(os.path.join(PODSYNC, "podsync", "__init__.py")):
    sys.exit(_("podsync is missing (vendor/podsync). Fetch it with:\n"
               "  git submodule update --init"))
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


def mounted(cfg):
    """Roots of the mounted volumes that look like an iPod. Cheap: no probing."""
    given = str(cfg.get("ipod_mount") or "").strip()
    if given and given.lower() != "auto":
        return [given] if os.path.isdir(os.path.join(given, "iPod_Control")) else []
    from podsync.hardware.discovery.scan import _find_ipod_volumes
    return [root for root, _skip in _find_ipod_volumes()]


def open_ipod(cfg, given=None):
    """Find and identify the iPod, and make it the device podsync writes to.

    given (or the ipod_mount setting) is a drive or mount point; 'auto' or
    empty looks at every mounted volume.
    """
    given = str(given or cfg.get("ipod_mount") or "").strip()
    if given and given.lower() != "auto":
        dev = hardware.identify_mounted_ipod(given)
        if dev is None:
            sys.exit(_("{path} isn't an iPod (no iPod_Control folder).").format(path=given))
    else:
        found = hardware.find_ipods()
        if not found:
            sys.exit(_("No iPod found. Connect it (disk mode), or set its drive / mount point\n"
                       "in Settings -> iPod."))
        if len(found) > 1:
            sys.exit(_("Several iPods are connected: {list}\nChoose one in Settings -> iPod, "
                       "or pass --ipod.").format(list=", ".join(d.path for d in found)))
        dev = found[0]
    try:
        hardware.select_device(dev)
    except hardware.UnidentifiedDeviceError:
        sys.exit(unidentified_help(dev.path))
    return dev


def unidentified_help(path):
    """What to do when podsync can't tell the model (an empty SysInfo, e.g. after a restore)."""
    import shlex
    cmd = ["sudo", "env", "PYTHONPATH=" + PODSYNC, sys.executable, "-m", "podsync.hardware.probes.libusb",
           "--write-sysinfo", "--path", path]
    return _("The iPod at {path} isn't identified: its SysInfo file doesn't name the model, and\n"
             "without administrator rights the model can't be read from the device itself.\n"
             "Run once in a terminal (reads the serial number from the iPod and writes SysInfo;\n"
             "the iPod disconnects for a few seconds):\n\n  {cmd}").format(
        path=path, cmd=" ".join(shlex.quote(c) for c in cmd))


def describe(dev):
    model = f" ({dev.model_number})" if dev.model_number else ""
    return f"{dev.display_name or 'iPod'}{model} at {dev.path}"


def load(dev):
    """Tracks and playlists of the iPod as podsync reads them.

    Plays made on the iPod since the last write are already merged in.
    """
    return database.load_device_library(Path(dev.path), raise_on_error=True)


def stats_file(cfg):
    return os.path.join(settings.path("reports_dir", cfg), "ipod_stats.json")


def save_stats(cfg, rows):
    """Keep plays, skips, rating and last-played per track on the PC (reports/ipod_stats.json).

    The iPod's own copy is lost when it's restored or wiped. Keyed by artist | title | album;
    counts only grow, so a track re-added with a zero count keeps its history.
    """
    path = stats_file(cfg)
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    for t in rows:
        new = {"play_count": t.get("play_count") or 0, "skip_count": t.get("skip_count") or 0,
               "rating": t.get("rating") or 0, "last_played": t.get("last_played") or 0}
        if not (new["play_count"] or new["skip_count"] or new["rating"]):
            continue
        key = " | ".join((t.get("artist") or "", t.get("title") or "", t.get("album") or ""))
        old = saved.get(key, {})
        saved[key] = {"play_count": max(new["play_count"], old.get("play_count", 0)),
                      "skip_count": max(new["skip_count"], old.get("skip_count", 0)),
                      "rating": new["rating"] or old.get("rating", 0),
                      "last_played": max(new["last_played"], old.get("last_played", 0))}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=1, sort_keys=True)
    return len(saved)


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
    for d in old[:-int(cfg.get("ipod_backups") or KEEP_BACKUPS)]:
        shutil.rmtree(os.path.join(folder, d), ignore_errors=True)
    return dest


def restore(cfg, root, confirmed):
    """Put the latest backup back. confirmed(question) asks the user."""
    folder = backups_dir(cfg)
    found = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    if not found:
        sys.exit(_("No backups in {folder}.").format(folder=folder))
    src = os.path.join(folder, found[-1])
    print(_("  latest backup: {folder}").format(folder=src))
    print(_("  It replaces the iPod's database with the one from that moment. Tracks\n"
            "  added since then vanish from the list (their files stay until the next\n"
            "  sync); tracks deleted since then come back only if their files are\n"
            "  still on the iPod."))
    if not confirmed(_("  Type 'yes' to restore: ")):
        print(_("Cancelled."))
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
    print(_("Restored. Eject the iPod properly before unplugging it."))


# ------------------------------------------------------------------ report


def report(dev):
    db = load(dev)
    rows = db["tracks"]
    have = covered(dev.path, rows)
    bare = [r for r in rows if r.get("db_track_id") not in have]
    lists = [p for p in db["dataset2_standard_playlists"] if not p.get("master_flag")]
    print(_("iPod: {device}").format(device=describe(dev)))
    if dev.disk_size_gb:
        print(_("  {free} of {total} GB free").format(free=f"{dev.free_space_gb:.1f}",
                                                     total=f"{dev.disk_size_gb:.1f}"))
    rows_out = [(_("tracks"), len(rows)), (_("playlists"), len(lists)),
                (_("tracks with a cover"), len(rows) - len(bare)),
                (_("tracks WITHOUT a cover"), len(bare))]
    width = max(len(label) for label, _v in rows_out)
    for label, value in rows_out:
        print(f"  {label.ljust(width)} : {value}")
    for r in bare[:40]:
        print(f"    {r.get('artist')} — {r.get('album')} — {r.get('title')}")
    if len(bare) > 40:
        print("  " + _("  ... {n} more").format(n=len(bare) - 40))


def main():
    ap = argparse.ArgumentParser(description=_("what the iPod has (read only)"))
    ap.add_argument("ipod", nargs="?", help=_("the iPod's drive or mount point"))
    ap.add_argument("--eject", action="store_true", help=_("flush and eject the iPod"))
    args = ap.parse_args()
    dev = open_ipod(settings.load() or {}, args.ipod)
    if args.eject:
        sys.exit(0 if eject(dev) else 1)
    report(dev)


if __name__ == "__main__":
    main()
