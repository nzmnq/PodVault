"""
Does Active fit on the iPod? If not, suggest which albums to move to Archive.

The size to fit into is the "Music space on the iPod" setting (or --gb). Albums are
suggested least-played first, using the play counts kept in reports/ipod_stats.json
(they are saved every time you sync); albums never played go first, biggest first.
Read-only: --out writes the suggestions as a marked-up list that
"Apply the edited list" understands.

    python src/fit_ipod.py [--gb 60] [--out reports/fit.txt]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ipod
import library
import settings
from i18n import _
from musiclib import norm


def plays_by_album(cfg):
    try:
        with open(ipod.stats_file(cfg), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for key, v in saved.items():
        album = norm(key.split(" | ")[-1])
        out[album] = out.get(album, 0) + v.get("play_count", 0)
    return out


def suggest(albums, plays, limit_bytes):
    """Albums of Active to demote until the rest fits, least-played first."""
    active = [a for a in albums if a["state"] == "A"]
    total = sum(a["bytes"] for a in active)
    order = sorted(active, key=lambda a: (plays.get(norm(a["album"]), 0) / max(a["tracks"], 1), -a["bytes"]))
    picked = []
    for a in order:
        if total <= limit_bytes:
            break
        picked.append(a)
        total -= a["bytes"]
    return picked, total


def main():
    ap = argparse.ArgumentParser(description=_("suggest what to move out of Active to fit the iPod"))
    ap.add_argument("--gb", type=float, help=_("space on the iPod in GB (default: the setting)"))
    ap.add_argument("--out", metavar="FILE", help=_("write the suggestions as a list for 'Apply the edited list'"))
    args = ap.parse_args()

    cfg = settings.require()
    gb = args.gb or int(cfg.get("ipod_capacity_gb") or 0)
    if gb <= 0:
        sys.exit(_("Set \"Music space on the iPod, GB\" in Settings first."))
    albums = library.scan(with_audio=False)
    active = sum(a["bytes"] for a in albums if a["state"] == "A")
    print(_("Active: {have} GB, the iPod: {gb} GB").format(have=f"{active / 1024 ** 3:.1f}", gb=gb))
    if active <= gb * 1024 ** 3:
        print(_("Everything in Active fits."))
        return

    plays = plays_by_album(cfg)
    if not plays:
        print(_("No play counts saved yet (they are saved when you sync): going by size only."))
    picked, left = suggest(albums, plays, gb * 1024 ** 3)
    print(_("Suggested for Archive: {n} albums, Active would be {left} GB\n").format(
        n=len(picked), left=f"{left / 1024 ** 3:.1f}"))
    for a in picked:
        n = plays.get(norm(a["album"]), 0)
        print(f"  {a['artist']} — {a['album']}   " + _("{mb} MB, plays: {n}").format(
            mb=f"{a['bytes'] / 1024 / 1024:.0f}", n=n))

    if args.out:
        names = {id(a) for a in picked}
        for a in albums:
            if id(a) in names:
                a["state"] = "R"
        library.export_list(albums, args.out)
        print("\n" + _("List for marking up: {file}").format(file=args.out))
    print(_("Nothing was moved."))


if __name__ == "__main__":
    main()
