"""
Find the same track stored twice: in Active and Archive, or as mp3 and m4a.

Tracks match when artist and title (without "feat.") match and the durations
are within the duration tolerance from the settings. Read-only: it only reports.

    python src/duplicates.py
"""

import base64
import json
import os
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings
from i18n import _
from musiclib import norm, strip_feat

# ------------------------------------------------- same audio, any tags
#
# Chromaprint (fpcalc, the AcoustID fingerprint) describes how a track
# sounds, a 32-bit word every 0.124 s. Two files of one recording give
# nearly the same words whatever their tags, bitrate or format. Measured on
# real pairs (SoundCloud m4a against library mp3): the same track shares
# 92-97 % of the bits, different tracks about half, as chance would have it.
# A plain loudness curve was tried first: different songs scored as high as
# the same one, so it's the fingerprint or nothing.

SAME_AUDIO = 0.75       # share of equal bits from which two prints are one recording
STEP = 0.1238           # seconds per word of the print
SECONDS = 120           # how much of each track is fingerprinted


def fpcalc():
    """Path to fpcalc (Chromaprint), or None."""
    import shutil
    found = shutil.which("fpcalc")
    if found:
        return found
    # an app started from the Finder doesn't get the shell's PATH
    for p in ("/opt/homebrew/bin/fpcalc", "/usr/local/bin/fpcalc", os.path.join(settings.DATA, "bin", "fpcalc")):
        if os.path.isfile(p):
            return p
    return None


def fingerprint(exe, path):
    """The raw Chromaprint of the first SECONDS, or None if it can't be decoded."""
    import numpy as np
    r = subprocess.run([exe, "-raw", "-json", "-length", str(SECONDS), path],
                       capture_output=True, text=True, errors="replace")
    try:
        words = json.loads(r.stdout)["fingerprint"]
    except (ValueError, KeyError):
        return None
    return np.array(words, dtype=np.uint32) if len(words) > 40 else None


_BITS = None


def similarity(a, b, length_diff=0.0):
    """Best share of equal bits, one print shifted against the other.

    A window of the first print (200 words, about 25 s, from a quarter in: past the intro)
    is slid along the second over the length difference plus 7 s, all shifts at once (one
    numpy operation; a Python loop over the shifts was most of an import's time). One copy
    may carry a longer intro ('Интернет человек' on SoundCloud is 11 s longer).
    """
    import numpy as np
    global _BITS
    if _BITS is None:
        _BITS = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(1).astype(np.uint16)
    w = min(200, len(a) // 2, len(b) // 2)
    if w < 20:
        return 0.0
    start = min(len(a) // 4, len(a) - w)
    seg = a[start:start + w]
    lag = int(abs(length_diff) / STEP) + 60
    lo, hi = max(0, start - lag), min(len(b) - w, start + lag)
    if hi < lo:
        return 0.0
    windows = np.lib.stride_tricks.sliding_window_view(b, w)[lo:hi + 1]
    differ = _BITS[(windows ^ seg).view(np.uint8)].reshape(len(windows), -1).sum(axis=1)
    return float(1 - differ.min() / (32 * w))


def close_length(a, b):
    """Durations near enough to be worth comparing the audio."""
    return abs(a - b) <= max(15, 0.1 * max(a, b))


class Prints:
    """Fingerprints, cached in <reports>/audio_prints.json by path, size and time."""

    def __init__(self, cfg):
        self.exe = fpcalc()
        self.file = os.path.join(settings.path("reports_dir", cfg), "audio_prints.json")
        try:
            with open(self.file, encoding="utf-8") as f:
                self.cache = json.load(f)
        except (OSError, ValueError):
            self.cache = {}
        self.dirty = False

    def get(self, path, key=None, stamp=None):
        """key: the file it was made from, when path is a temporary copy (a remuxed m4a):
        cached under that, or every run would fingerprint it again. stamp: its size:mtime when
        already known (the library index has it): no stat of each file on a slow drive."""
        import numpy as np
        key = key or path
        if stamp is None:
            st = os.stat(key)
            stamp = f"{st.st_size}:{int(st.st_mtime)}"
        hit = self.cache.get(key)
        if hit and hit[0] == stamp:
            return np.frombuffer(base64.b64decode(hit[1]), np.uint32) if hit[1] else None
        fp = fingerprint(self.exe, path)
        self.cache[key] = [stamp, base64.b64encode(fp.tobytes()).decode() if fp is not None else ""]
        self.dirty = True
        return fp

    def save(self):
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        tmp = self.file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f)
        os.replace(tmp, self.file)


def find(root, tolerance):
    """Groups (lists of dicts) of files that are the same track."""
    import library
    by_key = defaultdict(list)
    # from the library index: no file opened unless it's new or changed
    for fp, rel, size, _mtime, t in library.listing(root):
        key = (norm(t["albumartist"] or t["artist"]), norm(strip_feat(t["title"])))
        if not key[1]:
            continue
        length = t["length"] or 0
        by_key[key].append({
            "path": os.path.relpath(fp, root), "length": length,
            "bitrate": int(size * 8 / length / 1000) if length else 0,     # the average, from the size
            "kind": os.path.splitext(fp)[1].lstrip(".").upper(), "size": size,
            "state": "Archive" if rel.startswith("Archive") else "Active",
        })
    print(_("Comparing artist, title and duration..."))
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
    print(_("Looking for duplicates in {folder}").format(folder=root))
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
    settings.run(main)
