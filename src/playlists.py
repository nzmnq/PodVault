"""
Saved playlists: <reports>/playlists/<name>.m3u8, one file path a line. The AI vibe
playlists and the ones made by hand are the same kind of file.

<reports>/playlists/ipod.json says which of them are kept on the iPod —
{"keep": [name, ...], "managed": [name, ...]} — "managed" being what the last sync
put there, so a playlist that is no longer kept can be taken off it again.
"""

import json
import os

import settings

INVALID = '<>:"/\\|?*'


def folder(cfg):
    return os.path.join(settings.path("reports_dir", cfg), "playlists")


def path_of(cfg, name):
    return os.path.join(folder(cfg), name + ".m3u8")


def clean_name(name):
    return "".join("_" if c in INVALID else c for c in (name or "")).strip() or "Playlist"


def read(path):
    """[file paths] of a playlist, in order."""
    with open(path, encoding="utf-8-sig") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def write(path, paths):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for p in paths:
            f.write(p + "\n")
    os.replace(path + ".tmp", path)


def new(cfg, name, paths=()):
    """A new playlist file; a name already taken gets ' 2', ' 3'... Returns its path."""
    base = clean_name(name)
    final, k = base, 1
    while os.path.exists(path_of(cfg, final)):
        k += 1
        final = f"{base} {k}"
    path = path_of(cfg, final)
    write(path, list(paths))
    return path


def add(path, paths):
    """Append tracks, leaving out the ones already in it."""
    have = read(path)
    known = {os.path.normcase(p) for p in have}
    write(path, have + [p for p in paths if os.path.normcase(p) not in known])


def lists(cfg):
    try:
        with open(os.path.join(folder(cfg), "ipod.json"), encoding="utf-8") as f:
            d = json.load(f)
        return {"keep": list(d.get("keep") or []), "managed": list(d.get("managed") or [])}
    except (OSError, ValueError):
        return {"keep": [], "managed": []}


def save_lists(cfg, d):
    os.makedirs(folder(cfg), exist_ok=True)
    with open(os.path.join(folder(cfg), "ipod.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def keep_on_ipod(cfg, name, on):
    d = lists(cfg)
    d["keep"] = [n for n in d["keep"] if n != name] + ([name] if on else [])
    save_lists(cfg, d)


def is_kept(cfg, name):
    return name in lists(cfg)["keep"]


def rename(cfg, path, name):
    """Rename a playlist file; its 'kept on the iPod' goes with it. Returns the new path."""
    old = os.path.splitext(os.path.basename(path))[0]
    new_path = path_of(cfg, clean_name(name))
    if os.path.exists(new_path) and os.path.normcase(new_path) != os.path.normcase(path):
        raise ValueError(f"“{clean_name(name)}” exists already")
    os.replace(path, new_path)
    d = lists(cfg)
    d["keep"] = [clean_name(name) if n == old else n for n in d["keep"]]
    save_lists(cfg, d)
    return new_path


def kept(cfg):
    """[(name, [paths that exist])] of the saved playlists kept on the iPod."""
    out = []
    for name in lists(cfg)["keep"]:
        p = path_of(cfg, name)
        if os.path.isfile(p):
            out.append((name, [x for x in read(p) if os.path.isfile(x)]))
    return out
