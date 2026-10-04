"""
Scrobble what was played on the iPod to Last.fm.

The iPod counts plays since the last sync (podsync reads them as recent_playcount)
and the time of the last one. On every sync that writes the iPod, those plays are
queued (<reports>/scrobble_queue.json) and sent: the last play at its real time,
earlier ones spaced back by the track's length — the iPod keeps no more than that.
Last.fm takes plays up to 14 days old; older ones are dropped with a note. A play
already queued or sent is never queued again (artist, title, time), so a sync run
twice, or one that stopped half-way, doesn't scrobble anything twice.

Setup: an API account at last.fm/api/account/create gives an API key and a
shared secret (Settings -> Last.fm). Then "Connect Last.fm" opens the Last.fm page
where you allow PodVault; the session it gets is kept in the app's data, not in
the settings. Your Last.fm password never passes through here.

  python src/lastfm.py login     # connect (opens the browser, waits for "Allow")
  python src/lastfm.py send      # send what's queued
"""

import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings
from i18n import _

API = "https://ws.audioscrobbler.com/2.0/"
MAX_AGE = 14 * 24 * 3600        # Last.fm refuses older scrobbles
BATCH = 50                      # per request, Last.fm's limit
KEEP_SENT = 20000               # keys of sent plays remembered against doubles


def _session_file():
    return os.path.join(settings.DATA, "cache", "lastfm_session.json")


def _queue_file(cfg):
    return os.path.join(settings.path("reports_dir", cfg), "scrobble_queue.json")


def keys(cfg):
    """(api key, secret) from the settings, or None."""
    k, s = (cfg.get("lastfm_api_key") or "").strip(), (cfg.get("lastfm_api_secret") or "").strip()
    return (k, s) if k and s else None


def session():
    try:
        with open(_session_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def call(method, params, api_key, secret, post=False):
    """A signed Last.fm API call; returns the JSON answer, raises RuntimeError on an API error."""
    p = {**params, "method": method, "api_key": api_key}
    p["api_sig"] = hashlib.md5(("".join(k + str(p[k]) for k in sorted(p)) + secret).encode("utf-8")).hexdigest()
    p["format"] = "json"
    data = urllib.parse.urlencode(p).encode()
    req = urllib.request.Request(API, data=data) if post else urllib.request.Request(API + "?" + data.decode())
    req.add_header("User-Agent", "PodVault")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        out = json.loads(e.read().decode("utf-8") or "{}")
    if "error" in out:
        raise RuntimeError(f"Last.fm {out['error']}: {out.get('message', '')}")
    return out


def login(cfg):
    import webbrowser
    k = keys(cfg)
    if not k:
        sys.exit(_("Set the Last.fm API key and secret in Settings first (last.fm/api/account/create)."))
    token = call("auth.getToken", {}, *k)["token"]
    url = f"https://www.last.fm/api/auth/?api_key={k[0]}&token={token}"
    print(_("Allow PodVault on the Last.fm page that opens (or open it yourself):"))
    print("  " + url, flush=True)
    webbrowser.open(url)
    for n in range(60):             # three minutes
        time.sleep(3)
        try:
            s = call("auth.getSession", {"token": token}, *k)["session"]
        except RuntimeError:
            if n % 10 == 9:
                print(_("  still waiting for “Allow”..."), flush=True)
            continue
        os.makedirs(os.path.dirname(_session_file()), exist_ok=True)
        with open(_session_file(), "w", encoding="utf-8") as f:
            json.dump({"name": s["name"], "key": s["key"]}, f)
        print(_("Connected as {name}. Plays are scrobbled on every iPod sync.").format(name=s["name"]))
        return
    sys.exit(_("No answer from Last.fm in three minutes. Try again."))


# --------------------------------------------------------------- the queue


def _load(cfg):
    try:
        with open(_queue_file(cfg), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"queue": [], "sent": []}


def _save(cfg, data):
    path = _queue_file(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data["sent"] = data["sent"][-KEEP_SENT:]
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def plays(rows, names=None, before=None):
    """Plays to scrobble from the iPod's tracks: [{artist, track, album, timestamp, duration}].

    names: {track index: (artist, title, album)} — the library's names where they differ.
    before: the play stats saved by the last sync (ipod.save_stats). Only plays the total
    grew by since then count: a sync that had nothing to write leaves the iPod's "recent"
    count as it was, and the next one would otherwise send those plays again.
    """
    out = []
    for n, t in enumerate(rows):
        count, last = t.get("recent_playcount") or 0, t.get("last_played") or 0
        if before is not None:
            key = " | ".join((t.get("artist") or "", t.get("title") or "", t.get("album") or ""))
            grew = (t.get("play_count") or 0) - before.get(key, {}).get("play_count", 0)
            count = min(count, max(0, grew))
        if count <= 0 or not last:
            continue
        artist, title, album = (names or {}).get(n) or (t.get("artist"), t.get("title"), t.get("album"))
        if not (artist and title):
            continue
        length = int((t.get("length") or 0) / 1000) or 180
        for i in range(count):      # the last at its time, the ones before it a track's length apart
            out.append({"artist": artist, "track": title, "album": album or "",
                        "timestamp": int(last - i * length), "duration": length})
    return out


def queue(cfg, new):
    """Add plays to the queue, leaving out any already queued or sent. Returns how many."""
    data = _load(cfg)
    seen = set(data["sent"]) | {_key(p) for p in data["queue"]}
    fresh = [p for p in new if _key(p) not in seen]
    data["queue"] += fresh
    _save(cfg, data)
    return len(fresh)


def _key(p):
    return f"{p['artist']}|{p['track']}|{p['timestamp']}"


def send(cfg, quiet=False):
    """Send the queue. Nothing happens when Last.fm isn't set up; what fails stays queued."""
    k, s = keys(cfg), session()
    data = _load(cfg)
    if not data["queue"]:
        return 0
    if not (k and s):
        if not quiet:
            print(_("Last.fm isn't connected: {n} plays wait in the queue.").format(n=len(data["queue"])))
        return 0
    now = time.time()
    old = [p for p in data["queue"] if now - p["timestamp"] > MAX_AGE]
    if old:
        print(_("  {n} plays are older than 14 days: Last.fm doesn't take them").format(n=len(old)))
    todo = sorted((p for p in data["queue"] if now - p["timestamp"] <= MAX_AGE), key=lambda p: p["timestamp"])
    done = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        params = {"sk": s["key"]}
        for j, p in enumerate(batch):
            for field in ("artist", "track", "album", "timestamp", "duration"):
                if p.get(field) not in ("", None):
                    params[f"{field}[{j}]"] = p[field]
        try:
            call("track.scrobble", params, *k, post=True)
        except (RuntimeError, OSError) as e:
            settings.problem(_("Last.fm: {error} — the rest stays queued").format(error=e))
            break
        data["sent"] += [_key(p) for p in batch]
        done += len(batch)
    sent = set(data["sent"])
    data["queue"] = [p for p in todo if _key(p) not in sent]
    _save(cfg, data)
    print(_("  scrobbled to Last.fm ({name}): {n} plays").format(name=s["name"], n=done))
    return done


def main():
    cfg = settings.require()
    step = sys.argv[1] if len(sys.argv) > 1 else "send"
    if step == "login":
        login(cfg)
    elif step == "send":
        if not send(cfg):
            print(_("Nothing to send."))
    else:
        sys.exit(_("usage: python src/lastfm.py login | send"))


if __name__ == "__main__":
    settings.run(main)
