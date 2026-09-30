"""
Sync the iPod with the Active folder.

Goal: the device holds exactly what's in <library>\\Active. What was moved
to Archive leaves the iPod, what's new arrives, every synced track gets its
cover (if the iPod shows none) and its genre from the file.

The iPod's own databases are read and written by podsync (vendor/podsync,
see ipod.py), so no iTunes is needed — on Windows, macOS or Linux — and
playlists can go onto the iPod too.

Matching is BY TAGS and duration: a file on the iPod has its own internal
path that has nothing in common with ours, and it keeps the tags it had
when it was copied. Only tracks positively recognised as archive are
deleted; unknown ones stay (--rescue copies them off the iPod).

Safety:
  - dry run by default;
  - before every write the iPod's database folders (iPod_Control\\iTunes and
    \\Artwork, ~250 MB) are copied to <reports>\\ipod-backups; --restore puts
    the latest copy back;
  - podsync checks the volume and locks it, refuses to write if the database
    changed since it was read, and reads the new database back — every
    track and every file — before it counts as written;
  - files of deleted tracks are removed only after that;
  - it refuses to run while iTunes is open: iTunes would write its own copy
    of the database over ours when the iPod is ejected.

  python src/ipod_sync.py                          # what would change
  python src/ipod_sync.py --apply                  # do it
  python src/ipod_sync.py --playlist X.m3u8 --apply   # also add a playlist
  python src/ipod_sync.py --restore                # put the last backup back
  python src/ipod_sync.py --rescue --apply         # save tracks only on the iPod
  python src/ipod_sync.py --disk E: --apply        # mirror for Rockbox / disk mode
"""

import argparse
import os
import random
import re
import shutil
import string
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ipod        # podsync lives in vendor/podsync; importing ipod puts it on the path
import settings
import smart_presets
import soundcheck
import tags as audiotags
from i18n import _
from musiclib import norm, strip_feat

# Set from the settings by configure() — module-level so that the helper
# functions below can use them, and so tests can import the module.
LIBRARY = ACTIVE = ARCHIVE = None
DURATION_TOLERANCE = 3  # seconds


def configure(cfg=None):
    global LIBRARY, ACTIVE, ARCHIVE, DURATION_TOLERANCE
    cfg = cfg or settings.require()
    LIBRARY, ACTIVE, ARCHIVE = settings.library_paths(cfg)
    DURATION_TOLERANCE = int(cfg["duration_tolerance"])
    return cfg


ASSUME_YES = False  # --yes: confirmation already given, don't ask


def confirmed(question):
    """Ask for confirmation with a typed word.

    The BOM is stripped explicitly: PowerShell 5.1 prepends it to a string
    piped into a program, so 'yes' arrives as '\\ufeffyes'.
    """
    if ASSUME_YES:
        print(question + _("yes (--yes)"))
        return True
    try:
        ans = input(question)
    except EOFError:
        return False
    # 'да' is Russian for 'yes'
    return ans.replace("﻿", "").strip().lower() in ("yes", "y", "да", _("yes"), _("y"))


def active_files():
    out = []
    for root, _skip, files in os.walk(ACTIVE):
        for fn in audiotags.audio_files(files):
            out.append(os.path.join(root, fn))
    return sorted(out)


# ---------------------------------------------------------------- matching


def artist_variants(artist):
    """Artist keys a track can be recognised by.

    The iPod holds tracks with OLD tags — from before the cleanup: co-artists
    glued into one field ('Lil Peep/ Lil Tracy', 'wifiskeleton, Jaydes').
    Ours have only the main artist in TPE1. So try both the whole string
    (that's how 'AC/DC' survives) and the first name before a separator.
    """
    a = (artist or "").strip()
    out = {norm(a)}
    first = re.split(r"\s*[/;,]\s*|\s+&\s+|\s+feat\.?\s+", a, maxsplit=1)[0]
    out.add(norm(first))
    out.discard("")
    return out


def device_keys(artist, title):
    """Track keys: artist + title without features.

    The album is NOT part of the key: we merged singles into a 'Singles'
    album, while on the iPod they sit under their original titles, so not
    a single one would match by album. Paths don't work either — a file on
    the player has its own internal path. Artist and title are what's left.
    """
    t = norm(strip_feat(title or ""))
    # A title with no letters or digits at all (Иван Дорн's '???') used to
    # normalise to nothing, so the track had no key and never matched —
    # the iPod copy showed up as 'not recognised'. Use it as written.
    t = t or (title or "").strip().lower()
    return {(a, t) for a in artist_variants(artist)} if t else set()


def index_library():
    """Index of the whole library — Active and Archive — by track key."""
    idx = {}   # key -> {'A', 'R'}
    active_by_key = {}
    for root_dir, state in ((ACTIVE, "A"), (ARCHIVE, "R")):
        for root, _skip, files in os.walk(root_dir):
            for fn in audiotags.audio_files(files):
                p = os.path.join(root, fn)
                try:
                    t = audiotags.read(p)
                except audiotags.Unreadable:
                    continue
                keys = device_keys(t["artist"], t["title"]) | \
                    device_keys(t["albumartist"], t["title"])
                for k in keys:
                    idx.setdefault(k, set()).add(state)
                if state == "A" and keys:
                    try:
                        dur = audiotags.info(p)["length"]
                    except audiotags.Unreadable:
                        dur = None
                    active_by_key[p] = (keys, dur)
    return idx, active_by_key


def states_of(keys, idx):
    """{'A', 'R'}: where in the library a track with these keys is found."""
    states = set()
    for k in keys:
        states |= idx.get(k, set())
    return states


def match_device(active_info, device_tracks):
    """Pair Active files with iPod tracks. Returns ({device index: path}, missing).

    The 'artist + title' key alone isn't enough: the same song often exists
    in several versions — album, live, compilation (Black Sabbath's
    'Paranoid' is in the library three times; the live 'Lithium' and 'Come
    As You Are' from In Utero share titles with the studio ones on
    Nevermind). By key they're indistinguishable, so the live version would
    count as delivered because the studio one was already there.

    So a file is paired only with a NOT YET CLAIMED iPod track that shares a
    key and has the same duration. Duration doesn't depend on tags, so it
    works against the old tags on the device too.
    """
    pool = {}
    for n, (keys, dur) in enumerate(device_tracks):
        for k in keys:
            pool.setdefault(k, []).append(n)
    used = {}
    missing = []
    for p, (keys, dur) in sorted(active_info.items()):
        match = None
        for k in keys:
            for n in pool.get(k, []):
                if n in used:
                    continue
                d = device_tracks[n][1]
                if dur is None or d is None or abs(d - dur) <= DURATION_TOLERANCE:
                    match = n
                    break
            if match is not None:
                break
        if match is None:
            missing.append(p)
        else:
            used[match] = p
    return used, missing


def find_duplicates(pairs, device, idx):
    """iPod tracks that are extra copies of an Active track already matched.

    A track counts as a copy when it isn't paired itself, is recognised as
    Active, and shares a key and the duration with a paired track. One copy
    — the paired one — always stays. Copies appeared when a title spelled
    'й' as two code points never matched the iPod and was copied again on
    every sync.
    """
    pool = {}
    for n in pairs:
        _skip, keys, dur = device[n]
        for k in keys:
            pool.setdefault(k, []).append(dur)
    dupes = []
    for n, (_skip, keys, dur) in enumerate(device):
        if n in pairs or "A" not in states_of(keys, idx):
            continue
        for k in keys:
            if any(d is None or dur is None or abs(d - dur) <= DURATION_TOLERANCE
                   for d in pool.get(k, [])):
                dupes.append(n)
                break
    return dupes


def read_device(rows):
    """iPod tracks as (row, keys, duration in seconds)."""
    out = []
    for t in rows:
        length = t.get("length") or 0
        out.append((t, device_keys(t.get("artist"), t.get("title")),
                    length / 1000 if length else None))
    return out


# ------------------------------------------------------------ from the file


def has_cover(path):
    """True if the file has a cover the iPod can get: embedded, or folder art.

    Asks podsync's own extractor, the one the writer will use: the embedded
    picture first (a 'Singles' folder has ONE folder.jpg, while every single
    has its own), a cover image in the folder as the fallback.
    """
    from podsync.artwork.writer.covers import extract_art_with_source
    return extract_art_with_source(path)[0] is not None


def file_genre(path):
    try:
        return audiotags.read(path)["genre"]
    except audiotags.Unreadable:
        return ""


def _pair(raw):
    """'3/12' -> (3, 12)."""
    parts = (raw or "").split("/")
    n = int(parts[0]) if parts[0].strip().isdigit() else 0
    total = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 0
    return n, total


def record_from_file(path, location):
    """A podsync track record for a file (mp3 or m4a) about to be copied onto the iPod."""
    from podsync.itdb.writer.track import TrackRecord
    info, t = audiotags.info(path), audiotags.read(path)
    track, tracks = _pair(t["track"])
    disc, discs = _pair(t["disc"])
    year = t["year"]
    return TrackRecord(
        title=t["title"] or os.path.splitext(os.path.basename(path))[0],
        location=location, size=os.path.getsize(path),
        length=int(info["length"] * 1000), filetype=info["filetype"], filetype_desc=info["kind"],
        bitrate=info["bitrate"], sample_rate=info["sample_rate"],
        artist=t["artist"] or None, album=t["album"] or None,
        album_artist=t["albumartist"] or None, genre=t["genre"] or None,
        grouping=t["grouping"] or None, composer=t["composer"] or None,
        year=int(year) if year.isdigit() else 0,
        track_number=track, total_tracks=tracks,
        disc_number=disc or 1, total_discs=discs or 1,
        date_added=int(time.time()), source_path=path,
        sound_check=soundcheck.soundcheck_from_norm(audiotags.norm_tag(path)))


def new_file_on_ipod(root, ext=".mp3"):
    """A free iTunes-style file name in one of the iPod's Fxx music folders."""
    music = os.path.join(root, "iPod_Control", "Music")
    folders = sorted(d for d in os.listdir(music) if d.upper().startswith("F")) \
        if os.path.isdir(music) else []
    if not folders:
        folders = ["F00"]
        os.makedirs(os.path.join(music, "F00"), exist_ok=True)
    folder = random.choice(folders)
    while True:
        name = "".join(random.choices(string.ascii_uppercase, k=4)) + ext
        dest = os.path.join(music, folder, name)
        if not os.path.exists(dest):
            return dest


# -------------------------------------------------------------------- plan


def read_playlist(m3u):
    with open(m3u, encoding="utf-8-sig") as f:
        paths = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    return os.path.splitext(os.path.basename(m3u))[0], paths


def make_plan(dev, db, playlist_file=None, playlist_only=False, smart=False):
    """Everything the sync would do, without touching anything.

    playlist_only: just add the playlist; of the tracks only its own ones
    missing from the iPod are copied, nothing is deleted or changed.
    """
    idx, active_by_key = index_library()
    tracks = db["tracks"]
    device = read_device(tracks)
    pairs, to_add = match_device(active_by_key, [(k, d) for _skip, k, d in device])
    dupes = set(find_duplicates(pairs, device, idx))

    archive, unknown = [], []
    for n, (_skip1, keys, _skip2) in enumerate(device):
        states = states_of(keys, idx)
        # Delete ONLY what's recognised as archive and not as active. Found
        # nowhere — leave it: an extra track in shuffle beats a needed one wiped.
        if "A" not in states:
            (archive if "R" in states else unknown).append(n)

    thumbs = ipod.covered(dev.path, tracks)
    no_cover = [n for n, p in pairs.items() if tracks[n].get("db_track_id") not in thumbs]
    covers = [n for n in no_cover if has_cover(pairs[n])]
    regenre = [(n, g) for n, p in pairs.items()
               if (g := file_genre(p)) and (tracks[n].get("genre") or "") != g]
    playlist = read_playlist(playlist_file) if playlist_file else None

    if playlist_only or smart:
        wanted = {os.path.normcase(p) for p in playlist[1]} if playlist else set()
        to_add = [p for p in to_add if os.path.normcase(p) in wanted]
        dupes, archive, covers, no_cover, regenre = set(), [], [], [], []
    have = {r.get("title") for r in db.get("dataset2_standard_playlists") or []}
    new_smart = [n for n, _p, _r in smart_presets.presets() if n not in have] if smart else []
    return {"tracks": tracks, "pairs": pairs, "to_add": to_add, "dupes": sorted(dupes),
            "archive": archive, "unknown": unknown, "covers": covers,
            "bare": len(no_cover) - len(covers), "regenre": regenre, "playlist": playlist,
            "smart": new_smart}


def has_changes(plan):
    return any(plan[k] for k in ("archive", "dupes", "to_add", "covers", "regenre", "playlist", "smart"))


def show_plan(dev, plan):
    t = plan["tracks"]

    def name(n):
        x = t[n]
        return f"{x.get('artist')} — {x.get('album')} — {x.get('title')}"

    rows = [(_("tracks now"), len(t), ""),
            (_("recognised as Active"), len(plan['pairs']), _("(stay)")),
            (_("extra copies"), len(plan['dupes']), _("<- delete, one copy of each stays")),
            (_("recognised as Archive"), len(plan['archive']), _("<- delete")),
            (_("not recognised"), len(plan['unknown']), _("(left alone)")),
            (_("in Active, not on the iPod"), len(plan['to_add']), _("<- copy")),
            (_("shown without a cover"), len(plan['covers']), _("(set from the file)"))]
    if plan["bare"]:
        rows.append(("  + " + _("no cover in the file"), plan['bare'],
                     _("(nothing to set; 'covers' in the menu fetches them)")))
    rows.append((_("genre differs from file"), len(plan['regenre']), _("(updated)")))
    if plan["playlist"]:
        rows.append((_("playlist to add"), plan['playlist'][0],
                     _("({n} tracks)").format(n=len(plan['playlist'][1]))))
    if plan["smart"]:
        rows.append((_("smart playlists to add"), len(plan["smart"]), "(" + ", ".join(plan["smart"]) + ")"))
    width = max(len(label) for label, _v, _n in rows)

    print()
    print("=" * 70)
    print(_("THE IPOD: {device}").format(device=ipod.describe(dev)))
    print("=" * 70)
    for label, value, note in rows:
        print(f"  {label.ljust(width)} : {value}   {note}".rstrip())
    for title, items in ((_("WILL BE DELETED (archive)"), plan["archive"]),
                         (_("EXTRA COPIES, WILL BE DELETED"), plan["dupes"]),
                         (_("NOT RECOGNISED, STAY (--rescue copies them off the iPod)"),
                          plan["unknown"])):
        if items:
            print(f"\n--- {title} ---")
            for n in items[:20]:
                print(f"  {name(n)}")
            if len(items) > 20:
                print(_("  ... {n} more").format(n=len(items) - 20))
    if plan["to_add"]:
        print("\n--- " + _("WILL BE COPIED") + " ---")
        for p in plan["to_add"][:20]:
            print(f"  {os.path.relpath(p, ACTIVE)}")
        if len(plan["to_add"]) > 20:
            print(_("  ... {n} more").format(n=len(plan['to_add']) - 20))


# ------------------------------------------------------------------- apply


def apply_plan(cfg, dev, db, generation, plan):
    """Write the plan onto the iPod.

    Follows podsync's own write sequence: check the volume, lock it (and
    refuse if the database changed since it was read — generation), write,
    read back, then clear the play-count files merged into the new database.
    """
    from podsync.hardware.safety.guard import WriteLock
    from podsync.hardware.safety.readiness import check_write_ready, lock_key_for, recheck_write_ready
    from podsync.itdb.writer.playlist import PlaylistRecord
    from podsync.library import database
    from podsync.library.media_paths import expected_media_path, location_for_media_path
    from podsync.library.playlists import assemble_playlists
    from podsync.library.tracks import record_from_row
    from podsync.itdb.writer.track import generate_db_track_id

    root = dev.path
    tracks = plan["tracks"]
    remove = set(plan["archive"]) | set(plan["dupes"])
    genre = dict(plan["regenre"])

    profile = check_write_ready(root)

    def revalidate():
        nonlocal profile
        profile = recheck_write_ready(profile)

    with WriteLock(root, volume_key=lock_key_for(profile),
                   expected_database_generation=generation) as guard:
        print(_("\n  backing up the iPod's database..."))
        print(f"  -> {ipod.backup(cfg, root)}")

        records, cover_from, by_path = [], {}, {}
        used = {t.get("db_track_id") for t in tracks}
        for n, t in enumerate(tracks):
            if n in remove:
                continue
            rec = record_from_row(t)
            if n in genre:
                rec.genre = genre[n]
            if n in plan["pairs"]:
                if not rec.sound_check:
                    rec.sound_check = soundcheck.soundcheck_from_norm(audiotags.norm_tag(plan["pairs"][n]))
                by_path[os.path.normcase(plan["pairs"][n])] = rec.db_track_id
                if n in plan["covers"]:
                    cover_from[rec.db_track_id] = plan["pairs"][n]
            records.append(rec)

        copied = []
        try:
            for i, p in enumerate(plan["to_add"], 1):
                revalidate()
                dest = new_file_on_ipod(root, audiotags.extension(p))
                shutil.copy2(p, dest)
                copied.append(dest)
                rec = record_from_file(p, location_for_media_path(root, dest))
                while not rec.db_track_id or rec.db_track_id in used:
                    rec.db_track_id = generate_db_track_id()
                used.add(rec.db_track_id)
                records.append(rec)
                cover_from[rec.db_track_id] = p
                by_path[os.path.normcase(p)] = rec.db_track_id
                if i % 10 == 0:
                    print("\r" + _("  copied {n}/{total}").format(n=i, total=len(plan['to_add'])),
                          end="", flush=True)
            if plan["to_add"]:
                print("\r" + _("  copied {n}/{total}").format(n=len(plan['to_add']),
                                                          total=len(plan['to_add'])) + "      ")

            # Existing playlists (regular, folders, smart) are rebuilt for the
            # new track list; deleted tracks simply drop out of them.
            (m_name, m_id, playlists, pm_name, pm_id, podcast_pls,
             smart_pls) = assemble_playlists(
                tracks, db.get("dataset2_standard_playlists") or [],
                db.get("dataset3_podcast_playlists") or [],
                db.get("dataset5_smart_playlists") or [], records,
                time_context=db.get("device_time_context"))

            if plan["playlist"]:
                title, paths = plan["playlist"]
                existing = {p.name for p in playlists}
                final, k = title, 1
                while final in existing:
                    k += 1
                    final = f"{title} {k}"
                ids = [by_path[os.path.normcase(p)] for p in paths
                       if os.path.normcase(p) in by_path]
                playlists.append(PlaylistRecord(name=final, track_ids=ids))
                missing = len(paths) - len(ids)
                print(_("  playlist '{name}': {n} tracks").format(name=final, n=len(ids))
                      + (" " + _("({n} not on the iPod)").format(n=missing) if missing else ""))

            if plan["smart"]:
                existing = {p.name for p in playlists}
                fresh = smart_presets.build(records, existing)
                playlists.extend(fresh)
                print(_("  smart playlists added: {n}").format(n=len(fresh)))

            print(_("  writing the database (podsync reads it back afterwards)..."))
            written = database.save_device_library(
                root, records, pc_file_paths=cover_from or None,
                playlists=playlists, podcast_playlists=podcast_pls,
                smart_playlists=smart_pls, master_playlist_name=m_name,
                master_playlist_id=m_id, podcast_master_playlist_name=pm_name,
                podcast_master_playlist_id=pm_id, raise_on_error=True,
                before_database_replace=guard.assert_database_unchanged,
                before_device_mutation=revalidate)
            if not written:
                raise RuntimeError(_("podsync did not write the database (see the messages above)"))
        except BaseException:
            # the old database is still in place: take back the files we added
            for f in copied:
                try:
                    os.remove(f)
                except OSError:
                    pass
            raise

        # plays made on the iPod are merged into the database now
        database.clear_device_play_state(root, before_device_mutation=revalidate)

        # only now, with the new database verified, delete the removed files —
        # never one a remaining track still points at
        kept = {os.path.normcase(str(expected_media_path(root, r.location))) for r in records}
        gone = 0
        for n in remove:
            f = expected_media_path(root, tracks[n])
            if f and f.is_file() and os.path.normcase(str(f)) not in kept:
                try:
                    f.unlink()
                    gone += 1
                except OSError as e:
                    print(_("  ! file not deleted: {file}: {error}").format(file=f, error=e))
        if remove:
            print(_("  deleted {n} tracks ({files} files)").format(n=len(remove), files=gone))

    now = ipod.load(dev)["tracks"]
    print(_("\n  OK tracks on the iPod: {n} (read back, every file in place)").format(n=len(now)))
    if cover_from:
        have = ipod.covered(root, now)
        got = sum(1 for d in cover_from if d in have)
        print(f"  {'OK' if got == len(cover_from) else '!!'} "
              + _("covers written: {n}/{total}").format(n=got, total=len(cover_from)))
    print(_("\nDone. Eject the iPod before unplugging it (Sync -> Eject in the menu)."))


def run(cfg, apply_changes, ipod_path=None, playlist=None, playlist_only=False,
        do_restore=False, smart=False):
    """The whole sync; also called by vibe.py to add a playlist."""
    from podsync.hardware.safety.guard import snapshot_database_state

    configure(cfg)
    itunes = _("iTunes is running. Close it first: on eject it would write its own\n"
               "copy of the iPod's database over the one written here.")
    if ipod.itunes_running():
        if apply_changes or do_restore:
            sys.exit(itunes)
        print(f"  ! {itunes}\n")
    dev = ipod.open_ipod(cfg, ipod_path)
    if do_restore:
        return ipod.restore(cfg, dev.path, confirmed)

    if not (playlist_only or smart):
        # the library's tags first, so the iPod gets the genres the file holds
        import genres
        genres.keep_tags_in_line(cfg, write=apply_changes)
    generation = snapshot_database_state(dev.path)
    db = ipod.load(dev)
    plan = make_plan(dev, db, playlist, playlist_only, smart)
    show_plan(dev, plan)

    if not apply_changes:
        print(_("\nNothing changed. Add --apply."))
        return
    n = ipod.save_stats(cfg, db["tracks"])
    print(_("  play stats of {n} tracks saved to {file}").format(n=n, file=ipod.stats_file(cfg)))
    if not has_changes(plan):
        print(_("\nNothing to do: the iPod already matches Active."))
        return
    n_del = len(plan["archive"]) + len(plan["dupes"])
    if n_del:
        print(_("\n  Deleting {n} tracks from the iPod (a backup of the database is made first).")
              .format(n=n_del))
        print(_("  The files in {folder} stay intact.").format(folder=LIBRARY))
        if not confirmed(_("  Type 'yes' to confirm: ")):
            print(_("Cancelled."))
            return
    apply_plan(cfg, dev, db, generation, plan)


# ------------------------------------------------------ save from the iPod


def rescue_from_ipod(cfg, apply_changes, dest, ipod_path=None):
    """Copy tracks that are on the iPod but in neither Active nor Archive.

    For such a track the iPod may hold the only copy. They're never deleted
    by the sync, but they aren't in the library either — so they're copied
    off the iPod into the incoming folder, where "Add new tracks" fixes
    their tags and brings them in like any other new track. Only reads
    from the iPod.
    """

    dev = ipod.open_ipod(cfg, ipod_path)
    print(_("  iPod: {device}").format(device=ipod.describe(dev)))
    print(_("  indexing the library..."))
    idx, _skip = index_library()

    found = []
    for t in ipod.load(dev)["tracks"]:
        if states_of(device_keys(t.get("artist"), t.get("title")), idx):
            continue
        src = ipod.track_file(dev.path, t)
        ext = os.path.splitext(str(src or t.get("location") or ""))[1].lower()
        name = f"{t.get('artist') or 'Unknown'} - {t.get('title') or 'Untitled'}"
        name = re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(". ")[:150]
        found.append((t, src, os.path.join(dest, name + ext)))

    print()
    print("=" * 70)
    print(_("ON THE IPOD, NOT IN THE LIBRARY"))
    print("=" * 70)
    if not found:
        print(_("  nothing — every track on the iPod is in Active or Archive"))
        return
    todo = []
    for t, src, dst in found:
        if src is None:
            state = _("file missing on the iPod")
        elif os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
            state = _("already saved")
        else:
            state = _("will be saved")
            todo.append((src, dst))
        print(f"  {t.get('artist')} — {t.get('album')} — {t.get('title')}   [{state}]")
    print(_("\n  into: {folder}").format(folder=dest))

    if not apply_changes:
        print(_("\nNothing copied. Add --apply."))
        return
    os.makedirs(dest, exist_ok=True)
    for src, dst in todo:
        shutil.copy2(src, dst)
    print(_("\nSaved {n} tracks. Now 'Add new tracks' brings them into the library;").format(n=len(todo)))
    print(_("after that the sync recognises them."))


# -------------------------------------------------------------- to disk


def sync_disk(drive, apply_changes, subdir):
    """Mirror Active as plain folders, for Rockbox or disk mode."""
    # E: alone means the drive's current folder, not its root
    if len(drive) == 2 and drive[1] == ":":
        drive += os.sep
    root = os.path.join(drive, subdir)
    # the device itself must be there: a mistyped mount point must not
    # become a new folder on the system disk
    if not os.path.isdir(drive):
        sys.exit(_("Drive not available: {drive}").format(drive=drive))

    files = active_files()
    want = {}
    for p in files:
        want[os.path.relpath(p, ACTIVE)] = p
    # cover art is needed too
    for r, _skip, fs in os.walk(ACTIVE):
        for fn in fs:
            if fn.lower() == "folder.jpg":
                p = os.path.join(r, fn)
                want[os.path.relpath(p, ACTIVE)] = p

    have = {}
    if os.path.isdir(root):
        for r, _skip, fs in os.walk(root):
            for fn in fs:
                p = os.path.join(r, fn)
                have[os.path.relpath(p, root)] = p

    to_copy = [rel for rel in want if rel not in have
               or os.path.getsize(want[rel]) != os.path.getsize(have[rel])]
    to_delete = [rel for rel in have if rel not in want]
    size = sum(os.path.getsize(want[r]) for r in to_copy)

    print("=" * 70)
    print(_("MIRROR TO DISK: {folder}").format(folder=root))
    print("=" * 70)
    rows = [(_("should be"), _("{n} files").format(n=len(want))),
            (_("to copy"), _("{n}  ({gb} GB)").format(n=len(to_copy), gb=f"{size / 1024 ** 3:.2f}")),
            (_("to delete"), str(len(to_delete)))]
    width = max(len(label) for label, _v in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")

    if to_delete:
        print("\n--- " + _("WILL BE DELETED FROM THE DEVICE") + " ---")
        for rel in to_delete[:15]:
            print(f"  {rel}")
        if len(to_delete) > 15:
            print(_("  ... {n} more").format(n=len(to_delete) - 15))

    if not apply_changes:
        print(_("\nNothing changed. Add --apply."))
        return

    if to_delete:
        print(_("\nDeleting {n} files from the device — this can't be undone.").format(n=len(to_delete)))
        if not confirmed(_("  Type 'yes' to confirm: ")):
            print(_("Cancelled."))
            return

    for n, rel in enumerate(to_copy, 1):
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(want[rel], dst)
        if n % 50 == 0:
            print("\r" + _("  copied {n}/{total}").format(n=n, total=len(to_copy)), end="", flush=True)
    if to_copy:
        print("\r" + _("  copied {n}/{total}").format(n=len(to_copy), total=len(to_copy)))

    for rel in to_delete:
        try:
            os.remove(have[rel])
        except OSError as e:
            print(_("  ! not deleted {file}: {error}").format(file=rel, error=e))
    # clean up folders that became empty
    for r, dirs, fs in os.walk(root, topdown=False):
        if r != root and not os.listdir(r):
            try:
                os.rmdir(r)
            except OSError:
                pass
    print(_("\nDone: {copied} copied, {deleted} deleted.").format(copied=len(to_copy), deleted=len(to_delete)))


def main():
    cfg = configure()

    ap = argparse.ArgumentParser(description=_("sync the iPod with the Active folder"))
    ap.add_argument("--apply", action="store_true", help=_("actually apply"))
    ap.add_argument("--yes", action="store_true",
                    help=_("don't ask to confirm deletion (already confirmed)"))
    ap.add_argument("--ipod", metavar="PATH", help=_("the iPod's drive or mount point"))
    ap.add_argument("--playlist", metavar="M3U8", help=_("also put this playlist on the iPod"))
    ap.add_argument("--playlist-only", action="store_true",
                    help=_("with --playlist: add only the playlist (and copy its tracks "
                           "missing from the iPod), delete or change nothing"))
    ap.add_argument("--smart", action="store_true",
                    help=_("add ready-made smart playlists (never played, most played, top rated, "
                           "recently added); changes nothing else"))
    ap.add_argument("--restore", action="store_true",
                    help=_("put the latest backup of the iPod's database back"))
    ap.add_argument("--rescue", action="store_true",
                    help=_("copy tracks that are on the iPod but not in the library "
                           "into <incoming>\\From iPod"))
    ap.add_argument("--disk", metavar="DRIVE",
                    help=_("mirror to a drive: E: or /Volumes/NAME (Rockbox / disk mode)"))
    ap.add_argument("--subdir", default=cfg["ipod_disk_subdir"],
                    help=_("folder on the device, with --disk (setting: {value})").format(
                        value=cfg['ipod_disk_subdir']))
    args = ap.parse_args()

    global ASSUME_YES
    ASSUME_YES = args.yes

    if not os.path.isdir(ACTIVE):
        sys.exit(_("Active folder not found: {folder}").format(folder=ACTIVE))

    if args.rescue:
        incoming = settings.path("incoming_dir", cfg)
        if not incoming:
            sys.exit(_("The incoming folder isn't set (Settings)."))
        return rescue_from_ipod(cfg, args.apply, os.path.join(incoming, "From iPod"), args.ipod)
    if args.disk:
        return sync_disk(args.disk, args.apply, args.subdir)
    run(cfg, args.apply, args.ipod, args.playlist, args.playlist_only, args.restore, args.smart)


if __name__ == "__main__":
    main()
