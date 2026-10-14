"""
What PodVault knows about a track beyond its tags, the way iTunes shows it: how
often it was played and skipped, when last, and its rating.

Plays and skips come from the iPod: every sync saves them (ipod.save_stats ->
<reports>/ipod_stats.json). Ratings can be set in PodVault as well as on the iPod;
they live in <reports>/ratings.json as {key: {"rating": 0-100, "synced": 0-100}}:
"synced" is the value the iPod had after the last sync, so the sync can tell who
changed it (ipod_sync.rating_plan): PodVault -> written to the iPod, the iPod ->
taken into PodVault. A rating is 0-100, 20 a star, as on the iPod.

A track is known by its artist, title and album (compared loosely, musiclib.norm):
after a sync the iPod has the library's names.
"""

import json
import os

import settings
from musiclib import norm

STAR = 20


def key(artist, title, album):
    return "|".join(norm(x) for x in (artist, title, album))


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _ratings_file(cfg):
    return os.path.join(settings.path("reports_dir", cfg), "ratings.json")


def plays(cfg):
    """{key: {play_count, skip_count, last_played, rating}} as the iPod last had them."""
    raw = _read(os.path.join(settings.path("reports_dir", cfg), "ipod_stats.json"))
    out = {}
    for k, v in raw.items():
        parts = (k.split(" | ") + ["", "", ""])[:3]
        out[key(*parts)] = v
    return out


def ratings(cfg):
    return _read(_ratings_file(cfg))


def save_ratings(cfg, data):
    path = _ratings_file(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(path + ".tmp", path)


def set_rating(cfg, artist, title, album, value):
    """Rate a track in PodVault (0-100); the next sync writes it onto the iPod."""
    data = ratings(cfg)
    k = key(artist, title, album)
    entry = data.get(k) or {"synced": (plays(cfg).get(k) or {}).get("rating", 0)}
    entry["rating"] = max(0, min(100, int(value)))
    data[k] = entry
    save_ratings(cfg, data)


def info(stats, rated, artist, title, album):
    """(plays, rating 0-100, last played) for a track: PodVault's rating, else the iPod's."""
    k = key(artist, title, album)
    s = stats.get(k) or {}
    r = rated.get(k)
    return s.get("play_count", 0), (r["rating"] if r else s.get("rating", 0)), s.get("last_played", 0)


def stars(rating):
    """'★★★☆☆' for a rating."""
    n = round((rating or 0) / STAR)
    return "★" * n + "☆" * (5 - n)
