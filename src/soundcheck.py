"""
Sound Check: measure each Active track's loudness and write it as an iTunNORM tag.

The iPod's own Sound Check setting then evens out the volume between tracks.
Loudness is measured with ffmpeg (EBU R128) and compared with a target of -16.5 LUFS.
The iPod sync copies the value into the iPod's database.

    python src/soundcheck.py          # how many tracks would be measured
    python src/soundcheck.py --apply  # measure and write the tags
"""

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings
import tags as audiotags
from i18n import _

TARGET_LUFS = -16.5
MAX_SOUNDCHECK = 65534


def soundcheck_from_lufs(lufs):
    """The iPod's Sound Check number: 1000 = no change, larger = quieter track (needs turning down)."""
    gain_db = TARGET_LUFS - lufs
    return max(1, min(MAX_SOUNDCHECK, round(1000 * 10 ** (-gain_db / 10))))


def norm_string(soundcheck):
    """The iTunNORM text: ten hex fields, the first two on the 1000 scale, the next two on the 2500 scale."""
    quarter = min(MAX_SOUNDCHECK * 3, round(soundcheck * 2.5))
    fields = [soundcheck, soundcheck, quarter, quarter, 0x24CAC, 0x24CAC, 0x7FFF, 0x7FFF, 0x24CAC, 0x24CAC]
    return "".join(f" {v:08X}" for v in fields)


def soundcheck_from_norm(text):
    """Back from the tag text to the iPod number; 0 when there's nothing usable."""
    try:
        a, b = (int(x, 16) for x in text.split()[:2])
    except ValueError:
        return 0
    return min(max(a, b), MAX_SOUNDCHECK)


def measure(ffmpeg, path):
    """Integrated loudness in LUFS, or None."""
    r = subprocess.run([ffmpeg, "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace")
    found = re.findall(r"^\s*I:\s+(-?[\d.]+)\s+LUFS", r.stderr, re.M)
    return float(found[-1]) if found else None


def main():
    ap = argparse.ArgumentParser(description=_("measure loudness and write Sound Check tags"))
    ap.add_argument("--apply", action="store_true", help=_("measure and write the tags"))
    args = ap.parse_args()

    cfg = settings.require()
    active = settings.library_paths(cfg)[1]
    todo = [os.path.join(p, f) for p, _d, files in os.walk(active)
            for f in audiotags.audio_files(files) if not audiotags.norm_tag(os.path.join(p, f))]
    print(_("Active tracks without Sound Check: {n}").format(n=len(todo)))
    if not args.apply or not todo:
        if todo:
            print(_("Nothing written. Add --apply (it takes a while: every track is analysed)."))
        return

    ffmpeg = settings.ffmpeg(cfg)
    if not ffmpeg:
        sys.exit(_("ffmpeg not found. Set its path on the Settings screen."))
    done = 0
    for n, fp in enumerate(todo, 1):
        lufs = measure(ffmpeg, fp)
        if lufs is None:
            print("  " + _("! could not measure: {file}").format(file=os.path.relpath(fp, active)))
            continue
        audiotags.set_norm_tag(fp, norm_string(soundcheck_from_lufs(lufs)))
        done += 1
        if n % 25 == 0:
            print(f"  {n} / {len(todo)}")
    print(_("Sound Check written: {n} tracks").format(n=done))
    print(_("Run the iPod sync to copy it to the iPod, then turn Sound Check on in its Settings."))


if __name__ == "__main__":
    main()
