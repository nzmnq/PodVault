"""
AI vibe playlists: describe a mood in words, get a playlist on the iPod.

Two halves:

  1. Listening. Every Active track is analysed once — tempo, loudness,
     brightness, bass, how busy and how dynamic it is — from a 45-second
     excerpt, and the numbers are cached (reports\\vibe_features.json), so
     only new tracks are analysed next time.

  2. Choosing. Claude gets the whole catalogue — tags plus those numbers —
     and the vibe you typed, picks the tracks that fit, orders them and
     names the playlist. It knows most artists and songs by name; the
     numbers help with everything it doesn't know.

The playlist is saved as .m3u8 next to the reports. --apply (or --push-last
later) also creates it on the iPod, written into the iPod's database by
podsync (through ipod_sync.py, a backup first, no iTunes). Picked tracks not
on the iPod yet are copied along; nothing is deleted or changed. If a
playlist with that name exists, the new one gets a number.

The model is reached through ai.py: Claude Code on a subscription, free
Gemini, or the paid Anthropic API (setting 'AI through', or --backend).

  python src/vibe.py analyze                        # analyse new tracks
  python src/vibe.py "rainy night, slow, a bit sad"  # show the pick
  python src/vibe.py "gym, loud and fast" --count 40 --apply
  python src/vibe.py --push-last                    # send the last pick
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

import ai
import settings
import tags as audiotags
from i18n import _

EXCERPT_SECONDS = 45
FEATURES_VERSION = 1   # bump when the analysis changes, to redo the cache


# ------------------------------------------------------------ listening


def _excerpt(path):
    """Mono float samples from a stretch after the intro, and the rate."""
    if path.lower().endswith(".m4a"):
        return _excerpt_ffmpeg(path)
    import soundfile as sf
    with sf.SoundFile(path) as f:
        rate = f.samplerate
        total = f.frames
        want = EXCERPT_SECONDS * rate
        start = max(0, min(int(total * 0.3), total - want))
        f.seek(start)
        x = f.read(min(want, total), dtype="float32", always_2d=True)
    return x.mean(axis=1), rate


def _excerpt_ffmpeg(path, rate=22050):
    """The same excerpt through ffmpeg: libsndfile can't decode AAC or Apple Lossless."""
    import subprocess
    ffmpeg = settings.ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed to analyse .m4a")
    length = audiotags.info(path)["length"]
    start = max(0.0, min(length * 0.3, length - EXCERPT_SECONDS))
    raw = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{start:.2f}", "-t", str(EXCERPT_SECONDS),
                          "-i", path, "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                         capture_output=True, check=True, stdin=subprocess.DEVNULL).stdout
    return np.frombuffer(raw, dtype="<f4"), rate


def _tempo(onset, frame_rate):
    """BPM from the autocorrelation of the onset envelope (60–180)."""
    o = onset - onset.mean()
    if not o.any():
        return None
    ac = np.correlate(o, o, mode="full")[len(o) - 1:]
    lags = np.arange(len(ac))
    bpm = np.where(lags > 0, 60.0 * frame_rate / np.maximum(lags, 1), 0)
    ok = (bpm >= 60) & (bpm <= 180)
    if not ok.any():
        return None
    # a mild preference for ~115 BPM, so half/double tempo don't win on noise
    weight = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1) / 115.0) / 1.0) ** 2)
    score = np.where(ok, ac * weight, -np.inf)
    return float(round(bpm[int(np.argmax(score))]))


def analyse(path):
    """The numbers for one track, or None if it can't be decoded."""
    try:
        x, rate = _excerpt(path)
    except Exception:
        return None
    if len(x) < rate * 5:
        return None
    n, hop = 2048, 1024
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop]
    spec = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1))
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    power = spec ** 2
    total = power.sum(axis=1) + 1e-12

    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    rms_db = 20 * np.log10(rms)
    loud = rms_db > rms_db.max() - 40            # ignore silent gaps
    centroid = (power * freqs).sum(axis=1) / total
    bass = power[:, freqs < 150].sum(axis=1) / total

    logspec = np.log1p(spec)
    flux = np.maximum(np.diff(logspec, axis=0), 0).sum(axis=1)
    frame_rate = rate / hop
    peaks = (flux[1:-1] > flux[:-2]) & (flux[1:-1] >= flux[2:]) & \
            (flux[1:-1] > flux.mean() + flux.std())

    return {
        "bpm": _tempo(flux, frame_rate),
        "loudness": float(rms_db[loud].mean()),
        "dynamics": float(rms_db[loud].std()),
        "brightness": float(centroid[loud].mean()),
        "bass": float(bass[loud].mean()),
        "busyness": float(peaks.sum() / (len(x) / rate)),
    }


def track_tags(path):
    try:
        t = audiotags.read(path)
    except audiotags.Unreadable:
        return {}
    return {"artist": t["artist"], "title": t["title"], "album": t["album"],
            "genre": t["genre"], "style": t["grouping"], "year": t["year"]}


def audio_stamp(path):
    """What identifies the audio: tag edits (genres!) must not trigger a
    re-analysis, so the file's size and mtime can't be used."""
    try:
        i = audiotags.info(path)
        return f"{i['length']:.3f}:{i['bps']}"      # unchanged since mp3-only days: the cache stays valid
    except audiotags.Unreadable:
        st = os.stat(path)
        return f"{st.st_size}:{int(st.st_mtime)}"


def active_tracks(active):
    out = []
    for root, _skip, files in os.walk(active):
        for fn in files:
            if audiotags.is_audio(fn):
                out.append(os.path.join(root, fn))
    return sorted(out)


def cache_file(cfg):
    return os.path.join(settings.path("reports_dir", cfg), "vibe_features.json")


def load_cache(cfg):
    try:
        with open(cache_file(cfg), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == FEATURES_VERSION:
            return data["tracks"]
    except (OSError, ValueError, KeyError):
        pass
    return {}


def save_cache(cfg, tracks):
    path = cache_file(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": FEATURES_VERSION, "tracks": tracks}, f, ensure_ascii=False)
    os.replace(tmp, path)


def update_features(cfg, active):
    """Analyse whatever is new or changed. Returns {path: entry}."""
    cache = load_cache(cfg)
    files = active_tracks(active)
    fresh = {}
    todo = []
    for p in files:
        stamp = audio_stamp(p)
        old = cache.get(p)
        if old and old.get("stamp") != stamp:
            # entries from before audio_stamp() used size:mtime — keep them
            # if the file is untouched since
            st = os.stat(p)
            if old.get("stamp") == f"{st.st_size}:{int(st.st_mtime)}":
                old = {**old, "stamp": stamp}
        if old and old.get("stamp") == stamp:
            # tags are cheap to read and may have changed (genres, styles)
            fresh[p] = {**old, "tags": track_tags(p)}
        else:
            todo.append((p, stamp))
    if todo:
        print(_("  analysing {n} tracks (once; cached afterwards)...").format(n=len(todo)))
    for i, (p, stamp) in enumerate(todo, 1):
        fresh[p] = {"stamp": stamp, "tags": track_tags(p), "audio": analyse(p)}
        if i % 25 == 0:
            print("\r" + _("  analysed {n}/{total}").format(n=i, total=len(todo)), end="", flush=True)
            save_cache(cfg, {**cache, **fresh})
    if todo:
        print("\r" + _("  analysed {n}/{total}").format(n=len(todo), total=len(todo)) + "      ")
    save_cache(cfg, fresh)   # also drops tracks no longer in Active
    return fresh


# ------------------------------------------------------------- choosing


AUDIO_KEYS = ("loudness", "busyness", "brightness", "bass", "dynamics")


def percentiles(entries):
    """Audio numbers as 0–100 ranks within the library: easier to read."""
    out = {}
    for k in AUDIO_KEYS:
        vals = sorted(e["audio"][k] for e in entries.values() if e["audio"])
        if not vals:
            continue
        arr = np.array(vals)
        for p, e in entries.items():
            if e["audio"]:
                rank = np.searchsorted(arr, e["audio"][k], side="right") / len(arr)
                out.setdefault(p, {})[k] = int(round(rank * 100))
    return out


def catalogue(entries):
    """One compact line per track, numbered. Returns (text, [paths])."""
    ranks = percentiles(entries)
    paths = sorted(entries, key=lambda p: (entries[p]["tags"].get("artist", "").lower(), p))
    lines = ["id | artist | title | album | genre | style | year | bpm | loud | busy | bright | bass | dyn"]
    for i, p in enumerate(paths):
        t = entries[p]["tags"]
        a = entries[p]["audio"] or {}
        r = ranks.get(p, {})
        cells = [str(i), t.get("artist", ""), t.get("title", ""), t.get("album", ""),
                 t.get("genre", ""), t.get("style", ""), t.get("year", ""),
                 str(int(a["bpm"])) if a.get("bpm") else "",
                 *(str(r.get(k, "")) for k in AUDIO_KEYS)]
        lines.append(" | ".join(c.replace("|", "/") for c in cells))
    return "\n".join(lines), paths


SYSTEM = """You build playlists from one person's music library for their iPod.

The library is below, one track per line. 'genre' is broad, 'style' is the
precise style the owner confirmed — trust it over your own guesses. Besides
the tags, each track has
numbers measured from the audio: bpm, and five 0-100 ranks within this
library — loud (loudness), busy (how many hits and onsets per second),
bright (treble vs. dark), bass (low-end weight), dyn (how much the volume
moves; low = flat and compressed). Tempo detection can be off by a factor
of two. Use what you know about the artists and songs first, and the
numbers for whatever you don't know or to break ties.

Pick tracks that truly fit the requested vibe — fewer is better than
padding with poor fits — and order them so the playlist flows. Avoid more
than two tracks in a row by the same artist. Name the playlist in the
language the request is written in, short enough for an iPod screen
(max 30 characters), without quotes or emoji.

LIBRARY
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "track_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["name", "description", "track_ids"],
    "additionalProperties": False,
}


# ------------------------------------------------------------ delivering


def safe_filename(s):
    return "".join("_" if c in '<>:"/\\|?*' else c for c in s).strip() or "playlist"


def playlists_dir(cfg):
    return os.path.join(settings.path("reports_dir", cfg), "playlists")


def write_m3u(cfg, name, picked):
    """Save the pick; it also becomes the one --push-last sends."""
    folder = playlists_dir(cfg)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, safe_filename(name) + ".m3u8")
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for p in picked:
            f.write(p + "\n")
    with open(os.path.join(folder, "last.json"), "w", encoding="utf-8") as f:
        json.dump({"name": name, "m3u": path}, f, ensure_ascii=False)
    return path


def read_last(cfg):
    """Path of the last pick's .m3u8, so it can be sent without asking again."""
    try:
        with open(os.path.join(playlists_dir(cfg), "last.json"), encoding="utf-8") as f:
            m3u = json.load(f)["m3u"]
    except (OSError, ValueError, KeyError):
        sys.exit(_("No saved pick yet — describe a vibe first."))
    if not os.path.isfile(m3u):
        sys.exit(_("The last pick's file is gone: {file}").format(file=m3u))
    return m3u


def push_to_ipod(cfg, m3u):
    """Put the playlist on the iPod, written into its database by podsync.

    A backup of the iPod's database is made first. Picked tracks the iPod
    doesn't have yet are copied; nothing is deleted or changed.
    """
    import ipod_sync
    ipod_sync.ASSUME_YES = True
    ipod_sync.run(cfg, True, playlist=m3u, playlist_only=True)


def main():
    cfg = settings.require()
    _skip1, active, _skip2 = settings.library_paths(cfg)

    ap = argparse.ArgumentParser(description=_("AI vibe playlists"))
    ap.add_argument("vibe", nargs="?", default="", help=_("the mood in your own words, or 'analyze'"))
    count = int(cfg.get("vibe_count") or 25)
    ap.add_argument("--count", type=int, default=count,
                    help=_("about how many tracks (setting: {value})").format(value=count))
    ap.add_argument("--apply", action="store_true", help=_("also create the playlist on the iPod"))
    ap.add_argument("--backend", choices=ai.BACKENDS,
                    help=_("cli = Claude Code on a subscription, gemini = Google Gemini "
                           "(free key), api = Anthropic API key (setting: {value})").format(
                        value=cfg.get('vibe_backend', 'auto')))
    ap.add_argument("--push-last", action="store_true",
                    help=_("put the last pick on the iPod, without asking the AI again"))
    args = ap.parse_args()

    if args.push_last:
        return push_to_ipod(cfg, read_last(cfg))
    if not args.vibe.strip():
        ap.error(_("describe the vibe, e.g. \"rainy night, slow\""))
    if not os.path.isdir(active):
        sys.exit(_("Active folder not found: {folder}").format(folder=active))
    entries = update_features(cfg, active)
    no_audio = sum(1 for e in entries.values() if not e["audio"])
    print(_("  {n} tracks in Active").format(n=len(entries))
          + (_(", {n} couldn't be analysed").format(n=no_audio) if no_audio else ""))
    if args.vibe.strip().lower() == "analyze":
        return

    text, paths = catalogue(entries)
    backend = ai.pick_backend(cfg, args.backend)
    print(_("  asking {ai} for \"{vibe}\"... (usually under a minute)").format(
        ai=ai.backend_name(backend), vibe=args.vibe))
    answer = ai.ask_json(cfg, SYSTEM + text, f"Vibe: {args.vibe}\n\nAbout {args.count} tracks.",
                         SCHEMA, backend)
    seen = set()
    picked = []
    for i in answer["track_ids"]:
        if 0 <= i < len(paths) and i not in seen:
            seen.add(i)
            picked.append(paths[i])
    if not picked:
        sys.exit(_("The AI found nothing that fits. Try a broader description."))

    name = answer["name"].strip()[:30] or "Vibe"
    print()
    print("=" * 70)
    print(f"{name}   " + _("({n} tracks)").format(n=len(picked)))
    print("=" * 70)
    print(f"  {answer['description']}\n")
    for n, p in enumerate(picked, 1):
        t = entries[p]["tags"]
        print(f"  {n:2d}. {t.get('artist')} — {t.get('title')}")
    m3u = write_m3u(cfg, name, picked)
    print(_("\n  saved: {file}").format(file=m3u))

    if not args.apply:
        print(_("\n--push-last creates it on the iPod."))
        return
    push_to_ipod(cfg, m3u)


if __name__ == "__main__":
    main()
