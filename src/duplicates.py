"""
Find the same track stored twice: in Active and Archive, or as mp3 and m4a.

Tracks match when artist and title (without "feat.") match and the durations
are within the duration tolerance from the settings. Read-only: it only reports.

    python src/duplicates.py
"""

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings
import tags as audiotags
from i18n import _
from musiclib import norm, strip_feat


def find(root, tolerance):
    """Groups (lists of dicts) of files that are the same track."""
    by_key = defaultdict(list)
    for path, _dirs, files in os.walk(root):
        for fn in audiotags.audio_files(files):
            fp = os.path.join(path, fn)
            try:
                t = audiotags.read(fp)
                i = audiotags.info(fp)
            except audiotags.Unreadable:
                continue
            key = (norm(t["albumartist"] or t["artist"]), norm(strip_feat(t["title"])))
            if not key[1]:
                continue
            by_key[key].append({
                "path": os.path.relpath(fp, root), "length": i["length"], "bitrate": i["bitrate"],
                "kind": i["filetype"], "size": os.path.getsize(fp),
                "state": "Archive" if os.path.relpath(fp, root).startswith("Archive") else "Active",
            })
    groups = []
    for rows in by_key.values():
        rows.sort(key=lambda r: r["length"])
        cluster = [rows[0]]
        for r in rows[1:] + [None]:
            if r is not None and r["length"] - cluster[-1]["length"] <= tolerance:
                cluster.append(r)
                continue
            if len(cluster) > 1:
                groups.append(cluster)
            cluster = [r]
    return sorted(groups, key=lambda g: g[0]["path"].lower())


def main():
    cfg = settings.require()
    root = settings.library_paths(cfg)[0]
    if not os.path.isdir(root):
        sys.exit(_("Library not found: {folder}").format(folder=root))
    groups = find(root, int(cfg["duration_tolerance"]))
    if not groups:
        print(_("No duplicates found."))
        return
    print(_("The same track more than once: {n}").format(n=len(groups)))
    wasted = 0
    for g in groups:
        print()
        for r in g:
            print(f"  [{r['state'][0]}] {r['path']}   " + _("{kind}, {kbps} kbps, {mb} MB").format(
                kind=r["kind"], kbps=r["bitrate"], mb=f"{r['size'] / 1024 / 1024:.1f}"))
        wasted += sum(r["size"] for r in g) - max(r["size"] for r in g)
    print("\n" + _("Space the extra copies take: {mb} MB").format(mb=f"{wasted / 1024 / 1024:.0f}"))
    print(_("Nothing was changed. Delete or move the extra copies yourself."))


if __name__ == "__main__":
    main()
