"""
Sync the iPod with the Active folder.

Goal: the device holds exactly what's in <library>\\Active. What was moved
to Archive leaves the iPod, what's new arrives. The library folder, the
default mode, the playlist name and the disk subfolder come from the
settings; the command line can override the mode.

The script never writes the device database (iTunesDB) in any mode: it's
a closed binary format, and a bad write wipes all music on the iPod at
once. Everything is done through iTunes itself.

  --mode library
    The iTunes library is brought in line with Active: what's missing is
    added, entries pointing into Archive are removed. iTunes stays in
    "sync entire library" mode, no playlists needed.

  --mode playlist
    A playlist equal to Active is kept, and the iPod syncs from it.
    The library isn't touched at all, which makes this the most cautious
    mode: removing a track from a playlist and deleting it from the
    library are different things.

    But archive tracks stay in the iTunes library. If the iPod syncs the
    ENTIRE library — the default — they still reach the device and play
    in global shuffle. The iPod must be switched to syncing only this
    playlist.

  --mode device
    Cleans the iPod itself. Needed when it's set to "Manually manage
    music": then iTunes removes NOTHING from the device on its own, and
    tracks moved to Archive stay on the player and play in shuffle,
    however much the library is cleaned.

    A connected iPod shows up in iTunes as a separate source, and deleting
    from it is supported — the same as deleting a track by hand in iTunes,
    just in bulk. iTunes edits iTunesDB itself.

    Matching is BY TAGS: a file on the iPod has its own internal path that
    has nothing in common with ours.

  --disk X:
    Mirrors the folder for Rockbox or disk mode. Deletion here is real,
    so only the subfolder the script manages is touched, and it asks for
    confirmation.

Dry run by default.

  python src\\ipod_sync.py                      # what would change
  python src\\ipod_sync.py --apply              # mode from the settings
  python src\\ipod_sync.py --mode playlist --apply
  python src\\ipod_sync.py --mode device --apply
  python src\\ipod_sync.py --disk E: --apply
"""

import argparse
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mutagen.id3 import ID3
from mutagen.mp3 import MP3

import settings
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
        print(f"{question} yes (--yes)")
        return True
    try:
        ans = input(question)
    except EOFError:
        return False
    # 'да' is Russian for 'yes'
    return ans.replace("\ufeff", "").strip().lower() in ("yes", "y", "да")


def active_files():
    out = []
    for root, _, files in os.walk(ACTIVE):
        for fn in sorted(f for f in files if f.lower().endswith(".mp3")):
            out.append(os.path.join(root, fn))
    return sorted(out)


def key(path):
    """Path comparison key: case doesn't matter on Windows."""
    return os.path.normcase(os.path.abspath(path))


def under(path, folder):
    return key(path).startswith(key(folder) + os.sep)


# ------------------------------------------------------------ via iTunes


RPC_E_CALL_REJECTED = -2147418111   # iTunes is busy (e.g. still starting)


def when_ready(fn, seconds=120):
    """Call fn, retrying while iTunes rejects calls because it's busy."""
    import time
    deadline = time.time() + seconds
    while True:
        try:
            return fn()
        except Exception as e:
            busy = getattr(e, "hresult", None) == RPC_E_CALL_REJECTED or \
                (e.args and e.args[0] == RPC_E_CALL_REJECTED)
            if not busy or time.time() > deadline:
                raise
            time.sleep(2)


def itunes_connect():
    """Connect to iTunes, starting it if needed."""
    try:
        import comtypes.client
    except ImportError:
        sys.exit("comtypes is missing. Install: python\\python.exe -m pip install comtypes")
    try:
        itunes = comtypes.client.CreateObject("iTunes.Application")
        when_ready(lambda: itunes.Version)
        return itunes
    except Exception as e:
        sys.exit(
            f"Could not connect to iTunes: {e}\n\n"
            "Check that iTunes is installed and not showing a dialog, then try again."
        )


def find_playlist(itunes, name):
    src = itunes.LibrarySource
    for i in range(1, src.Playlists.Count + 1):
        pl = src.Playlists.Item(i)
        if pl.Name == name:
            return pl
    return None


def track_location(track):
    """Path to the track's file, if it's a file track. Streams have none."""
    try:
        return track.Location or None
    except Exception:
        return None


def read_library(itunes):
    """Sort the library: what points into Active, what into Archive."""
    lib = itunes.LibraryPlaylist
    total = lib.Tracks.Count
    print(f"  tracks in the library: {total}")

    in_active, in_archive = {}, {}
    print("  reading the library...", end="", flush=True)
    for i in range(1, total + 1):
        t = lib.Tracks.Item(i)
        loc = track_location(t)
        if not loc:
            continue
        if under(loc, ACTIVE):
            in_active[key(loc)] = t
        elif under(loc, ARCHIVE):
            in_archive[key(loc)] = t
        if i % 500 == 0:
            print(".", end="", flush=True)
    print()
    return in_active, in_archive


def check_copy_setting(itunes, sample):
    """Make sure iTunes doesn't copy added files into its own folder.

    If "Copy files to iTunes Media folder when adding to library" is on,
    adding 1300 tracks silently duplicates the whole library — an extra
    10 GB and a second copy that will drift away from ours. Tested on one
    file rather than trusting the settings.
    """
    lib = itunes.LibraryPlaylist
    before = lib.Tracks.Count
    try:
        itunes.LibraryPlaylist.AddFile(sample)
    except Exception as e:
        return None, f"could not add a test file: {e}"
    if lib.Tracks.Count <= before:
        return None, "the test file was not added"

    t = lib.Tracks.Item(lib.Tracks.Count)
    loc = track_location(t)
    copied = bool(loc) and not under(loc, LIBRARY)
    if copied:
        # clean up after ourselves: the copied file and the entry
        try:
            t.Delete()
        except Exception:
            pass
        return False, loc
    return True, None


def guard_copy_setting(itunes, sample):
    """Stop if iTunes copies files into its own folder."""
    ok, info = check_copy_setting(itunes, sample)
    if ok is False:
        sys.exit(
            "\nSTOPPED: iTunes copies added files into its own folder.\n"
            f"  the test file ended up in: {info}\n\n"
            "Adding the whole Active folder would duplicate ~10 GB and create\n"
            "a second copy of the library that will drift away from ours.\n\n"
            "Turn it off: iTunes -> Edit -> Preferences -> Advanced\n"
            "  -> uncheck \"Copy files to iTunes Media folder when adding to library\".\n"
            "Then run again."
        )
    if ok is None:
        print(f"  (could not run the copy check: {info})")


def sync_mode_library(itunes, apply_changes, wanted, in_lib, from_archive, to_add, files):
    """Bring the iTunes library in line with Active.

    iTunes stays in "sync entire library" mode, so everything on the iPod
    looks normal and shuffle works as always.
    """
    print()
    print("=" * 70)
    print("iTunes LIBRARY = ACTIVE FOLDER")
    print("=" * 70)
    print(f"  in Active on disk          : {len(files)}")
    print(f"  already in the library     : {len(in_lib)}")
    print(f"  to add                     : {len(to_add)}")
    print(f"  to remove (point to Archive): {len(from_archive)}")

    if from_archive:
        print("\n--- WILL LEAVE THE IPOD ---")
        for _, t in from_archive[:15]:
            print(f"  {t.Artist} — {t.Name}")
        if len(from_archive) > 15:
            print(f"  ... {len(from_archive) - 15} more")

    if to_add:
        print("\n--- WILL ARRIVE ON THE IPOD ---")
        for p in to_add[:15]:
            print(f"  {os.path.relpath(p, ACTIVE)}")
        if len(to_add) > 15:
            print(f"  ... {len(to_add) - 15} more")

    if not apply_changes:
        print("\nNothing changed. Add --apply.")
        return

    if to_add:
        guard_copy_setting(itunes, to_add[0])

    added = 0
    for n, p in enumerate(to_add, 1):
        try:
            itunes.LibraryPlaylist.AddFile(p)
            added += 1
        except Exception as e:
            print(f"  ! not added {os.path.basename(p)}: {e}")
        if n % 50 == 0:
            print(f"\r  added {n}/{len(to_add)}", end="", flush=True)
    if to_add:
        print(f"\r  added {added}/{len(to_add)}")

    # We remove library ENTRIES. The files live in the library folder, outside
    # the iTunes Media folder, so iTunes doesn't delete them — they stay in
    # Archive, and the original backup is intact too.
    removed = 0
    for n, (_, t) in enumerate(from_archive, 1):
        try:
            t.Delete()
            removed += 1
        except Exception as e:
            print(f"  ! not removed {t.Name}: {e}")
        if n % 50 == 0:
            print(f"\r  removed {n}/{len(from_archive)}", end="", flush=True)
    if from_archive:
        print(f"\r  removed {removed}/{len(from_archive)}")

    # Check the actual result, not the intent: re-read the library and see
    # whether any entries pointing into Archive are left. Those are exactly
    # what would reach the iPod and play in global shuffle.
    print("\n  checking the result...")
    still_active, still_archive = read_library(itunes)
    if still_archive:
        print(f"\n  !! Library entries from Archive left: {len(still_archive)}")
        for _, t in list(still_archive.items())[:10]:
            print(f"     {t.Artist} — {t.Name}")
        print("     These tracks will reach the iPod. Run again or remove by hand.")
    else:
        print("  OK no Archive entries left in the library")
    missing = len(wanted) - len(still_active)
    if missing > 0:
        print(f"  !! Active tracks not added: {missing}")
    else:
        print(f"  OK the whole Active folder is in the library: {len(still_active)}")

    print("""
Done. The library now matches Active.

Next: connect the iPod and press Sync. Nothing to configure — iTunes in
"entire library" mode will bring the device in line. The archive isn't in
the library, so it can't turn up in shuffle.""")


def find_ipod(itunes):
    """The connected iPod as an iTunes source."""
    ITSourceKindIPod = 2
    for i in range(1, itunes.Sources.Count + 1):
        s = itunes.Sources.Item(i)
        if s.Kind == ITSourceKindIPod:
            return s
    return None


def wait_for_ipod(itunes, seconds=360):
    """The iPod, waiting for it to show up. None if it never does.

    Right after iTunes starts the iPod can take minutes to appear among its
    sources (about 4.5 minutes on the iPod this was written with), and
    during that time iTunes also rejects calls as busy.
    """
    import time
    pod = when_ready(lambda: find_ipod(itunes))
    if pod is not None:
        return pod
    print("  waiting for the iPod to appear in iTunes "
          "(can take a few minutes after iTunes starts)...", flush=True)
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(5)
        pod = when_ready(lambda: find_ipod(itunes))
        if pod is not None:
            return pod
    return None


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
        for root, _, files in os.walk(root_dir):
            for fn in files:
                if not fn.lower().endswith(".mp3"):
                    continue
                p = os.path.join(root, fn)
                try:
                    tags = ID3(p)
                except Exception:
                    continue

                def one(k):
                    v = tags.get(k)
                    return str(v.text[0]) if v and v.text else ""

                keys = device_keys(one("TPE1"), one("TIT2")) | \
                    device_keys(one("TPE2"), one("TIT2"))
                for k in keys:
                    idx.setdefault(k, set()).add(state)
                if state == "A" and keys:
                    try:
                        dur = MP3(p).info.length
                    except Exception:
                        dur = None
                    active_by_key[p] = (keys, dur)
    return idx, active_by_key


def match_device(active_info, device_tracks):
    """Pair Active files with iPod tracks. Returns ({device index: path}, missing).

    See find_missing_on_device for why duration is part of the match.
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


def find_missing_on_device(active_info, device_tracks):
    """Which Active files are missing from the iPod.

    The 'artist + title' key alone isn't enough: the same song often exists
    in several versions — album, live, compilation (Black Sabbath's
    'Paranoid' is in the library three times; the live 'Lithium' and 'Come
    As You Are' from In Utero share titles with the studio ones on
    Nevermind). By key they're indistinguishable, so the check counted the
    live version as delivered because the studio one was already there.

    So a track counts as delivered only if the iPod has a NOT YET CLAIMED
    track with a shared key and the same duration. Duration doesn't depend
    on tags, so it works against the old tags on the device too.
    """
    return match_device(active_info, device_tracks)[1]


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
        _, keys, dur = device[n]
        for k in keys:
            pool.setdefault(k, []).append(dur)
    dupes = []
    for n, (t, keys, dur) in enumerate(device):
        if n in pairs:
            continue
        states = set()
        for k in keys:
            states |= idx.get(k, set())
        if "A" not in states:
            continue
        for k in keys:
            if any(d is None or dur is None or abs(d - dur) <= DURATION_TOLERANCE
                   for d in pool.get(k, [])):
                dupes.append(n)
                break
    return dupes


def delete_device_indices(itunes, device, indices):
    """Delete specific iPod tracks by position, from the end backwards.

    Positions come from a fresh read; deleting from the end keeps the ones
    before intact. Each track is fetched fresh and checked (name and
    duration) right before deletion, so nothing else is removed by mistake.
    """
    # Names are read BEFORE anything is deleted: after the first deletion
    # every reference obtained earlier goes stale, and even reading .Name
    # from it throws 'The track has been deleted'.
    targets = [(n, device[n][0].Name, device[n][2]) for n in sorted(indices, reverse=True)]
    removed = 0
    for n, expected, dur in targets:
        try:
            t = find_ipod(itunes).Playlists.Item(1).Tracks.Item(n + 1)
            if t.Name != expected or (dur is not None and abs(float(t.Duration) - dur) > 1):
                print(f"\n  ! position {n + 1} is no longer '{expected}', skipped")
                continue
            t.Delete()
            removed += 1
        except Exception as e:
            if "deleted" in str(e).lower():
                removed += 1   # it's gone, which is what we wanted
                continue
            print(f"\n  ! '{expected}': {e}")
    return removed


# ------------------------------------------------------------ artwork


# What the iPod screen shows is not iTunes' Artwork.Count: the iPod draws
# small copies iTunes writes into iPod_Control\Artwork. Tracks put on the
# iPod long ago by another program (libgpod) have Artwork.Count = 1 and no
# such copies at all — for iTunes they "have" a cover, the screen shows
# none, and a check by Artwork.Count never repairs them. So the check reads
# the iPod's own database (ipoddb) and matches tracks by their persistent
# ID, which is the dbid in iTunesDB.


def has_artwork(track):
    try:
        return track.Artwork.Count > 0
    except Exception:
        return True   # can't tell — don't touch it


def track_dbid(itunes, track):
    hi = itunes.ITObjectPersistentIDHigh(track)
    lo = itunes.ITObjectPersistentIDLow(track)
    return ((hi & 0xFFFFFFFF) << 32) | (lo & 0xFFFFFFFF)


def device_thumbnails(pod):
    """(drive root, dbids that have a thumbnail on the iPod), or (None, None).

    None when the iPod's files can't be read (not in disk mode): then only
    iTunes' own record is available.
    """
    import ipoddb
    try:
        root = ipoddb.find_root(pod.Playlists.Item(1).Tracks.Count)
        if root:
            return root, ipoddb.dbids_with_thumbnail(root)
    except Exception as e:
        print(f"  (could not read the iPod's artwork database: {e})")
    return None, None


def needs_cover(itunes, track, thumbs):
    """True if the iPod shows no cover for this track."""
    if not has_artwork(track):
        return True
    if thumbs is None:
        return False
    try:
        return track_dbid(itunes, track) not in thumbs
    except Exception:
        return False


def artwork_file(path):
    """An image with this mp3's cover, or None.

    The embedded picture comes first: a 'Singles' folder has ONE folder.jpg —
    the first single's cover — while every single has its own, so taking
    folder.jpg there would give the other singles someone else's picture.
    folder.jpg is only the fallback for files without an embedded cover.
    """
    try:
        pics = ID3(path).getall("APIC")
    except Exception:
        pics = []
    if pics:
        ext = ".png" if "png" in (pics[0].mime or "").lower() else ".jpg"
        tmp = os.path.join(tempfile.gettempdir(), "music_utility_cover" + ext)
        with open(tmp, "wb") as f:
            f.write(pics[0].data)
        return tmp
    folder_jpg = os.path.join(os.path.dirname(path), "folder.jpg")
    return folder_jpg if os.path.isfile(folder_jpg) else None


def give_artwork(track, path):
    """Put the file's cover onto a track ON THE IPOD. True if it worked.

    Tracks copied to a manually managed iPod through iTunes COM can arrive
    without a cover on the device even though the mp3 has one embedded:
    iTunes doesn't always carry it into the device's artwork database.
    AddArtworkFromFile sets it explicitly.
    """
    img = artwork_file(path)
    if not img:
        return False
    try:
        track.AddArtworkFromFile(img)
        return True
    except Exception as e:
        print(f"\n  ! cover not set for {os.path.basename(path)}: {e}")
        return False


def replace_artwork(track, path):
    """Drop the track's cover in iTunes and set it again from the file.

    Setting it again is what makes iTunes regenerate the thumbnails the
    iPod draws. Tracks whose file has no cover are left as they are.
    """
    if not artwork_file(path):
        return False
    try:
        while track.Artwork.Count > 0:
            track.Artwork.Item(1).Delete()
    except Exception as e:
        print(f"\n  ! old cover not removed for {os.path.basename(path)}: {e}")
    return give_artwork(track, path)


def set_covers(itunes, todo):
    """Rewrite covers for [(device index, path)]. Returns the dbids done."""
    done = []
    for i, (n, p) in enumerate(todo, 1):
        # fetch the track fresh: device references go stale easily
        t = find_ipod(itunes).Playlists.Item(1).Tracks.Item(n + 1)
        if replace_artwork(t, p):
            try:
                done.append(track_dbid(itunes, t))
            except Exception:
                pass
        if i % 10 == 0:
            print(f"\r  covers set {i}/{len(todo)}", end="", flush=True)
    return done


def refresh_device_artwork(itunes, active_by_key, text):
    """Rewrite the cover of Active tracks whose artist or album contains text."""
    text = text.lower()
    pod = find_ipod(itunes)
    device = read_device(pod)
    pairs, _ = match_device(active_by_key, [(k, d) for _, k, d in device])
    todo = [(n, p) for n, p in pairs.items()
            if text in (device[n][0].Artist or "").lower()
            or text in (device[n][0].Album or "").lower()]
    return set_covers(itunes, todo), len(todo)


def covers_to_fix(itunes, pod, device, pairs):
    """Active tracks on the iPod that show no cover but whose file has one.

    Returns (todo [(device index, path)], no_cover_in_file count, root).
    """
    root, thumbs = device_thumbnails(pod)
    todo, bare = [], 0
    for n, p in pairs.items():
        if needs_cover(itunes, device[n][0], thumbs):
            if artwork_file(p):
                todo.append((n, p))
            else:
                bare += 1
    return todo, bare, root


def fix_device_artwork(itunes, active_by_key):
    """Give a cover to every Active track the iPod shows without one."""
    pod = find_ipod(itunes)
    device = read_device(pod)
    pairs, _ = match_device(active_by_key, [(k, d) for _, k, d in device])
    todo, _, root = covers_to_fix(itunes, pod, device, pairs)
    return set_covers(itunes, todo), len(todo), root


def write_and_verify(itunes, root, dbids):
    """Make iTunes write its pending changes to the iPod, then check covers.

    iTunes keeps changes to the iPod in memory and writes the device's
    database only when the iPod is ejected or iTunes quits. Quitting keeps
    the drive mounted, so the result can be read back and checked — ejecting
    wouldn't allow that. A cover counts as delivered only if the iPod's own
    artwork database now has a thumbnail for the track.
    """
    import subprocess
    import time
    import ipoddb

    print("\n  closing iTunes so it writes the changes onto the iPod...")
    try:
        itunes.Quit()
    except Exception as e:
        print(f"  ! iTunes did not accept Quit: {e}")
        return
    for _ in range(100):   # up to 5 minutes: writing covers takes a while
        time.sleep(3)
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq iTunes.exe"],
                             capture_output=True).stdout
        if b"iTunes.exe" not in out:
            break
    else:
        print("  !! iTunes is still running after 5 minutes — perhaps it shows a")
        print("     dialog. Close it by hand; the covers are written when it exits.")
        return
    if not root or not dbids:
        return
    try:
        have = ipoddb.dbids_with_thumbnail(root)
    except Exception as e:
        print(f"  ! could not read the iPod back: {e}")
        return
    ok = sum(1 for d in dbids if d in have)
    if ok == len(dbids):
        print(f"  OK the iPod now has a cover thumbnail for all {ok} tracks")
    else:
        print(f"  !! covers on the iPod: {ok}/{len(dbids)}. Run 'covers' again.")


def read_device(pod):
    """iPod tracks: object, keys, duration."""
    pl = pod.Playlists.Item(1)
    out = []
    for j in range(1, pl.Tracks.Count + 1):
        t = pl.Tracks.Item(j)
        try:
            dur = float(t.Duration)
        except Exception:
            dur = None
        out.append((t, device_keys(t.Artist, t.Name), dur))
    return out


def is_archive(t, idx):
    states = set()
    for k in device_keys(t.Artist, t.Name):
        states |= idx.get(k, set())
    return "R" in states and "A" not in states


def delete_archive_from_device(itunes, idx, expected):
    """Delete archive tracks from the iPod.

    References to device tracks can't be collected in advance: after the
    very first deletion iTunes invalidates ALL objects obtained before, and
    Delete() on them throws 'The track has been deleted' without deleting
    anything. The first version deleted exactly one track per run.

    So each track is fetched fresh right before deletion, and the pass runs
    from the end of the list: deleting track N doesn't shift the numbers
    before it.
    """
    removed = 0
    retries = 0
    pod = find_ipod(itunes)
    j = pod.Playlists.Item(1).Tracks.Count
    while j >= 1:
        if retries > 3:
            print(f"\n  ! track #{j}: reference keeps going stale, skipping")
            retries, j = 0, j - 1
            continue
        try:
            tracks = pod.Playlists.Item(1).Tracks
            if j > tracks.Count:
                j = tracks.Count
                continue
            t = tracks.Item(j)
            if is_archive(t, idx):
                t.Delete()
                removed += 1
                if removed % 10 == 0:
                    print(f"\r  deleted {removed}/{expected}", end="", flush=True)
        except Exception as e:
            if "deleted" in str(e).lower():
                # the reference went stale between fetching and calling — refetch
                pod = find_ipod(itunes)
                retries += 1
                continue
            print(f"\n  ! track #{j}: {e}")
        retries = 0
        j -= 1
    return removed


def device_library(itunes):
    """The iPod's library through an interface that has AddFile.

    comtypes hands out device playlists as the base IITPlaylist, which has
    no AddFile. The first version got an AttributeError, swallowed it in a
    catch-all except, and dutifully reported 'not added' for all 170 files.
    """
    from comtypes.gen import iTunesLib
    pod = find_ipod(itunes)
    return pod, pod.Playlists.Item(1).QueryInterface(iTunesLib.IITLibraryPlaylist)


def add_to_device(itunes, paths):
    """Copy files onto the iPod. Returns how many actually landed.

    Each copied track also gets its cover set explicitly if iTunes didn't
    carry it over (see give_artwork).
    """
    import time

    pod, lib = device_library(itunes)
    before = lib.Tracks.Count
    added = 0
    for n, p in enumerate(paths, 1):
        try:
            status = lib.AddFile(p)
            # copying is asynchronous — wait for the file to arrive, but not
            # forever: a stalled copy used to freeze the whole sync
            deadline = time.time() + 180
            while status is not None and status.InProgress:
                if time.time() > deadline:
                    raise RuntimeError("still copying after 3 minutes, skipped")
                time.sleep(0.2)
            added += 1
            new_tracks = getattr(status, "Tracks", None) if status is not None else None
            if new_tracks is not None:
                for i in range(1, new_tracks.Count + 1):
                    t = new_tracks.Item(i)
                    if not has_artwork(t):
                        give_artwork(t, p)
        except Exception as e:
            print(f"\n  ! {os.path.basename(p)}: {e}")
            # the library reference may have gone stale — refetch
            pod, lib = device_library(itunes)

        # Safety check: after the first file make sure the track really
        # appeared on the iPod, not just that the call went through.
        if n == 1:
            now = lib.Tracks.Count
            if now <= before:
                print("\n  !! The first file didn't appear on the iPod — stopping")
                print("     rather than pushing the rest for nothing.")
                return 0
            print(f"  first file landed ({before} -> {now}), continuing")
        if n % 10 == 0:
            print(f"\r  copied {n}/{len(paths)}", end="", flush=True)
    return added


def sync_device(apply_changes):
    """Remove from the iPod itself whatever isn't in Active.

    Needed when the iPod is in manual mode: then iTunes removes nothing
    from the device on its own, and tracks moved to Archive keep sitting on
    the player and turning up in global shuffle, however much the library
    is cleaned.

    We don't write iTunesDB: iTunes edits the device itself, we only tell
    it what to remove — exactly like deleting a track by hand, but in bulk.
    """
    itunes = itunes_connect()
    print(f"iTunes {itunes.Version}   mode: device")

    pod = wait_for_ipod(itunes)
    if pod is None:
        sys.exit(
            "No iPod among the iTunes sources.\n\n"
            "Check that it's connected by cable and visible in iTunes itself.\n"
            "If it's charging-only or locked, iTunes doesn't expose it."
        )
    print(f"  device: {pod.Name}")

    print("  indexing the library...")
    idx, active_by_key = index_library()

    # What's on the iPod now. The device's first playlist is its full
    # library; the others reference the same tracks.
    print("  reading the device...")
    device = read_device(pod)
    dev_total = len(device)
    keep, archive, unknown = [], [], []
    for t, keys, _ in device:
        states = set()
        for k in keys:
            states |= idx.get(k, set())
        # Delete ONLY what's recognised as archive and not as active.
        # Found nowhere — leave it: an extra track in shuffle beats a
        # needed one wiped.
        if "A" in states:
            keep.append(t)
        elif "R" in states:
            archive.append(t)
        else:
            unknown.append(t)

    to_delete = archive
    pairs, to_add = match_device(active_by_key, [(k, d) for _, k, d in device])
    dupes = find_duplicates(pairs, device, idx)
    no_art, bare, _ = covers_to_fix(itunes, pod, device, pairs)

    print()
    print("=" * 70)
    print(f"ON THE DEVICE: {pod.Name}")
    print("=" * 70)
    print(f"  tracks now               : {dev_total}")
    print(f"  recognised as Active     : {len(keep)}   (stay)")
    print(f"    of them extra copies   : {len(dupes)}   <- delete, one copy of each stays")
    print(f"  recognised as Archive    : {len(archive)}   <- delete, this is what plays in shuffle")
    print(f"  not recognised           : {len(unknown)}   (left alone)")
    print(f"  in Active, not on the iPod: {len(to_add)}")
    print(f"  shown without a cover    : {len(no_art)}   (cover will be set from the file)")
    if bare:
        print(f"    + no cover in the file : {bare}   (nothing to set; 'covers' in the menu fetches them)")

    if archive:
        print("\n--- WILL BE DELETED FROM THE IPOD (archive) ---")
        for t in archive[:25]:
            print(f"  {t.Artist} — {t.Album} — {t.Name}")
        if len(archive) > 25:
            print(f"  ... {len(archive) - 25} more")

    if dupes:
        print("\n--- EXTRA COPIES, WILL BE DELETED (one copy of each stays) ---")
        for n in dupes[:25]:
            t = device[n][0]
            print(f"  {t.Artist} — {t.Album} — {t.Name}")
        if len(dupes) > 25:
            print(f"  ... {len(dupes) - 25} more")

    if unknown:
        print("\n--- NOT RECOGNISED, WILL STAY ON THE IPOD ---")
        print("  In neither Active nor Archive. Possibly old versions or")
        print("  things no longer in the library. The script doesn't delete them;")
        print("  --rescue (Sync -> 5 in the menu) copies them off the iPod.")
        for t in unknown[:25]:
            print(f"  {t.Artist} — {t.Album} — {t.Name}")
        if len(unknown) > 25:
            print(f"  ... {len(unknown) - 25} more")

    if to_add:
        print("\n--- IN ACTIVE, NOT ON THE IPOD ---")
        for p in to_add[:15]:
            print(f"  {os.path.relpath(p, ACTIVE)}")
        if len(to_add) > 15:
            print(f"  ... {len(to_add) - 15} more")

    if no_art:
        print("\n--- THE IPOD SHOWS NO COVER ---")
        for n, p in no_art[:15]:
            t = device[n][0]
            src = "from folder.jpg" if artwork_file(p).endswith("folder.jpg") else "embedded in the file"
            print(f"  {t.Artist} — {t.Album} — {t.Name}   [{src}]")
        if len(no_art) > 15:
            print(f"  ... {len(no_art) - 15} more")

    if not apply_changes:
        print("\nNothing changed. Add --apply.")
        return
    if not (to_delete or dupes or to_add or no_art):
        print("\nNothing to do: the iPod already matches Active.")
        return

    if to_delete or dupes:
        print(f"\n  Deleting {len(to_delete) + len(dupes)} tracks FROM THE DEVICE can't be undone.")
        print(f"  The files in {LIBRARY} and the backup stay intact.")
        if not confirmed("  Type 'yes' to confirm: "):
            print("Cancelled.")
            return

    if to_delete:
        removed = delete_archive_from_device(itunes, idx, len(to_delete))
        print(f"\r  deleted {removed}/{len(to_delete)}          ")

    if dupes:
        # positions changed if archive tracks were deleted — read again
        device = read_device(find_ipod(itunes))
        pairs, _ = match_device(active_by_key, [(k, d) for _, k, d in device])
        dupes = find_duplicates(pairs, device, idx)
        removed = delete_device_indices(itunes, device, dupes)
        print(f"  extra copies deleted {removed}/{len(dupes)}")

    if to_add:
        print(f"\n  Copying {len(to_add)} tracks to the iPod...")
        added = add_to_device(itunes, to_add)
        print(f"\r  copied {added}/{len(to_add)}          ")

    # Covers: everything that is on the iPod now (including what was just
    # copied) and still shows no cover gets it from the file.
    print("\n  setting missing covers...")
    covered, wanted_art, root = fix_device_artwork(itunes, active_by_key)
    print(f"\r  covers set {len(covered)}/{wanted_art}          ")

    # Check the actual result: re-read the device and count how much
    # archive is really left on it.
    print("\n  checking the device...")
    pod = find_ipod(itunes)
    device = read_device(pod)
    total_now = len(device)
    left = sum(1 for t, _, _ in device if is_archive(t, idx))
    pairs, missing_now = match_device(active_by_key, [(k, d) for _, k, d in device])
    still_missing = len(missing_now)
    copies_left = find_duplicates(pairs, device, idx)
    if copies_left:
        print(f"  !! extra copies left: {len(copies_left)}")
    else:
        print("  OK no extra copies")
    print(f"  tracks on the iPod: {total_now}")
    if left:
        print(f"  !! archive left: {left}. Run again.")
    else:
        print("  OK no archive left on the iPod")
    if still_missing:
        print(f"  !! Active tracks not delivered: {still_missing}")
    else:
        print("  OK the whole Active folder is on the iPod")

    finish(itunes, root, covered)


def finish(itunes, root, covered):
    """Write pending changes to the iPod (checking covers) and say goodbye."""
    if covered and root:
        write_and_verify(itunes, root, covered)
        print("\nDone. iTunes was closed to write everything onto the iPod.")
        print("Unplug it with 'Safely Remove Hardware' in Windows,")
        print("or open iTunes again and press Eject.")
    else:
        print("\nDone. Eject the iPod in iTunes before unplugging it —")
        print("that's when iTunes writes the changes onto the device.")


def sync_itunes(apply_changes, mode, playlist_name):
    itunes = itunes_connect()
    print(f"iTunes {itunes.Version}   mode: {mode}")

    in_lib, in_archive = read_library(itunes)
    from_archive = list(in_archive.items())

    files = active_files()
    wanted = {key(f): f for f in files}
    to_add = [p for k, p in wanted.items() if k not in in_lib]

    if mode == "library":
        return sync_mode_library(itunes, apply_changes, wanted, in_lib,
                                 from_archive, to_add, files)

    pl = find_playlist(itunes, playlist_name)
    in_pl = {}
    if pl is not None:
        for i in range(1, pl.Tracks.Count + 1):
            t = pl.Tracks.Item(i)
            loc = track_location(t)
            if loc:
                in_pl[key(loc)] = t
    to_remove = [t for k, t in in_pl.items() if k not in wanted]
    already = len(in_pl) - len(to_remove)

    print()
    print("=" * 70)
    print(f"PLAYLIST: {playlist_name}" + ("" if pl is not None else "   (will be created)"))
    print("=" * 70)
    print(f"  in Active on disk          : {len(files)}")
    print(f"  already in the playlist    : {already}")
    print(f"  to add to the library      : {len(to_add)}")
    print(f"  to remove from the playlist: {len(to_remove)}")
    print(f"  library entries from Archive: {len(from_archive)}  (stay in the library)")

    if from_archive:
        print(f"""
  WARNING. {len(from_archive)} tracks from Archive stay in the iTunes library.
  If the iPod is set to sync the ENTIRE library — the default — they still
  reach the device and turn up in global shuffle. The playlist on its own
  does nothing to prevent that.

  To avoid it, do one of two things:
    - switch the iPod to syncing only the playlist "{playlist_name}"
      (iTunes -> iPod -> Music -> Selected playlists...)
    - or use --mode library, which removes these entries from the
      library altogether""")

    if to_remove:
        print("\n--- WILL LEAVE THE IPOD ---")
        for t in to_remove[:15]:
            print(f"  {t.Artist} — {t.Name}")
        if len(to_remove) > 15:
            print(f"  ... {len(to_remove) - 15} more")

    if to_add:
        print("\n--- WILL ARRIVE ON THE IPOD ---")
        for p in to_add[:15]:
            print(f"  {os.path.relpath(p, ACTIVE)}")
        if len(to_add) > 15:
            print(f"  ... {len(to_add) - 15} more")

    if not apply_changes:
        print("\nNothing changed. Add --apply.")
        return

    if pl is None:
        pl = itunes.CreatePlaylist(playlist_name)
        print(f"\nPlaylist created: {playlist_name}")

    # Remove extras FROM THE PLAYLIST. The file on disk and the library
    # entry stay — Delete() on a track in a user playlist only takes it
    # off that playlist.
    for n, t in enumerate(to_remove, 1):
        try:
            t.Delete()
        except Exception as e:
            print(f"  ! not removed {t.Name}: {e}")
        if n % 50 == 0:
            print(f"\r  removed {n}/{len(to_remove)}", end="", flush=True)
    if to_remove:
        print(f"\r  removed {len(to_remove)}/{len(to_remove)}")

    # Add everything not in the playlist. AddFile also puts the track into
    # the library if it wasn't there, so a separate pass over to_add isn't
    # needed — otherwise what's already in the playlist would be doubled.
    missing_in_pl = [p for k, p in wanted.items() if k not in in_pl]
    if missing_in_pl:
        guard_copy_setting(itunes, missing_in_pl[0])
    added = 0
    for n, p in enumerate(missing_in_pl, 1):
        try:
            pl.AddFile(p)
            added += 1
        except Exception as e:
            print(f"  ! not added {os.path.basename(p)}: {e}")
        if n % 50 == 0:
            print(f"\r  added {n}/{len(missing_in_pl)}", end="", flush=True)
    if missing_in_pl:
        print(f"\r  added {added}/{len(missing_in_pl)}")

    print(f"""
Done. One last step — configure the iPod once:

  iTunes -> connect the iPod -> Music tab
  -> "Selected playlists, artists, albums, and genres"
  -> tick the playlist "{playlist_name}" -> Apply

After that, just run this script and press Sync.""")


# ------------------------------------------------------ save from the iPod


def rescue_from_ipod(apply_changes, dest):
    """Copy tracks that are on the iPod but in neither Active nor Archive.

    For such a track the iPod may hold the only copy. They're never deleted
    by the sync, but they aren't in the library either — so they're copied
    off the iPod into the incoming folder, where "Add new tracks" fixes
    their tags and brings them in like any other new track.

    Reads the iPod's own database and files directly (disk mode), without
    iTunes. Only reads from the iPod.
    """
    import ipoddb

    root = ipoddb.find_root()
    if not root:
        sys.exit("No iPod drive found. It needs 'Enable disk use' in iTunes "
                 "(always on in manual mode).")
    print(f"  iPod at {root}")
    print("  indexing the library...")
    idx, _ = index_library()

    found = []
    for t in ipoddb.read_itunesdb(root):
        states = set()
        for k in device_keys(t["artist"], t["title"]):
            states |= idx.get(k, set())
        if states:
            continue
        src = os.path.join(root, (t["location"] or "").replace(":", os.sep).lstrip(os.sep))
        name = f"{t['artist'] or 'Unknown'} - {t['title'] or os.path.basename(src)}"
        name = re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(". ")[:150]
        found.append((t, src, os.path.join(dest, name + os.path.splitext(src)[1].lower())))

    print()
    print("=" * 70)
    print("ON THE IPOD, NOT IN THE LIBRARY")
    print("=" * 70)
    if not found:
        print("  nothing — every track on the iPod is in Active or Archive")
        return
    todo = []
    for t, src, dst in found:
        if not os.path.isfile(src):
            state = "file missing on the iPod"
        elif os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
            state = "already saved"
        else:
            state = "will be saved"
            todo.append((src, dst))
        print(f"  {t['artist']} — {t['album']} — {t['title']}   [{state}]")
    print(f"\n  into: {dest}")

    if not apply_changes:
        print("\nNothing copied. Add --apply.")
        return
    os.makedirs(dest, exist_ok=True)
    for src, dst in todo:
        shutil.copy2(src, dst)
    print(f"\nSaved {len(todo)} tracks. Now 'Add new tracks' brings them into the library;")
    print("after that the sync recognises them.")


# -------------------------------------------------------------- to disk


def sync_disk(drive, apply_changes, subdir):
    root = os.path.join(drive if drive.endswith(os.sep) else drive + os.sep, subdir)
    if not os.path.isdir(os.path.splitdrive(root)[0] + os.sep):
        sys.exit(f"Drive not available: {drive}")

    files = active_files()
    want = {}
    for p in files:
        want[os.path.relpath(p, ACTIVE)] = p
    # cover art is needed too
    for r, _, fs in os.walk(ACTIVE):
        for fn in fs:
            if fn.lower() == "folder.jpg":
                p = os.path.join(r, fn)
                want[os.path.relpath(p, ACTIVE)] = p

    have = {}
    if os.path.isdir(root):
        for r, _, fs in os.walk(root):
            for fn in fs:
                p = os.path.join(r, fn)
                have[os.path.relpath(p, root)] = p

    to_copy = [rel for rel in want if rel not in have
               or os.path.getsize(want[rel]) != os.path.getsize(have[rel])]
    to_delete = [rel for rel in have if rel not in want]
    size = sum(os.path.getsize(want[r]) for r in to_copy)

    print("=" * 70)
    print(f"MIRROR TO DISK: {root}")
    print("=" * 70)
    print(f"  should be : {len(want)} files")
    print(f"  to copy   : {len(to_copy)}  ({size / 1024 ** 3:.2f} GB)")
    print(f"  to delete : {len(to_delete)}")

    if to_delete:
        print("\n--- WILL BE DELETED FROM THE DEVICE ---")
        for rel in to_delete[:15]:
            print(f"  {rel}")
        if len(to_delete) > 15:
            print(f"  ... {len(to_delete) - 15} more")

    if not apply_changes:
        print("\nNothing changed. Add --apply.")
        return

    if to_delete:
        print(f"\nDeleting {len(to_delete)} files from the device — this can't be undone.")
        if not confirmed("  Type 'yes' to confirm: "):
            print("Cancelled.")
            return

    for n, rel in enumerate(to_copy, 1):
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(want[rel], dst)
        if n % 50 == 0:
            print(f"\r  copied {n}/{len(to_copy)}", end="", flush=True)
    if to_copy:
        print(f"\r  copied {len(to_copy)}/{len(to_copy)}")

    for rel in to_delete:
        try:
            os.remove(have[rel])
        except OSError as e:
            print(f"  ! not deleted {rel}: {e}")
    # clean up folders that became empty
    for r, dirs, fs in os.walk(root, topdown=False):
        if r != root and not os.listdir(r):
            try:
                os.rmdir(r)
            except OSError:
                pass
    print(f"\nDone: {len(to_copy)} copied, {len(to_delete)} deleted.")


def main():
    cfg = configure()

    ap = argparse.ArgumentParser(description="sync the iPod with the Active folder")
    ap.add_argument("--apply", action="store_true", help="actually apply")
    ap.add_argument("--disk", metavar="X:", help="mirror to a drive (Rockbox / disk mode)")
    ap.add_argument("--subdir", default=cfg["ipod_disk_subdir"],
                    help=f"folder on the device, with --disk (setting: {cfg['ipod_disk_subdir']})")
    ap.add_argument("--mode", choices=("library", "playlist", "device"),
                    default=cfg["ipod_sync_mode"],
                    help="library: library = Active; "
                         "playlist: keep a separate playlist; "
                         "device: clean the iPod itself (for manual mode). "
                         f"Setting: {cfg['ipod_sync_mode']}")
    ap.add_argument("--playlist", default=cfg["ipod_playlist"],
                    help=f"playlist name (setting: {cfg['ipod_playlist']})")
    ap.add_argument("--yes", action="store_true",
                    help="don't ask to confirm deletion (already confirmed)")
    ap.add_argument("--covers-only", action="store_true",
                    help="device: only set missing covers, don't delete or copy anything")
    ap.add_argument("--refresh-covers", metavar="TEXT",
                    help="device: rewrite the cover of tracks whose artist or album "
                         "contains TEXT (for covers iTunes has but the iPod doesn't show)")
    ap.add_argument("--rescue", action="store_true",
                    help="copy tracks that are on the iPod but not in the library "
                         "into <incoming>\\From iPod (reads the iPod, no iTunes needed)")
    args = ap.parse_args()

    global ASSUME_YES
    ASSUME_YES = args.yes

    if not os.path.isdir(ACTIVE):
        sys.exit(f"Active folder not found: {ACTIVE}")

    if args.rescue:
        incoming = settings.path("incoming_dir", cfg)
        if not incoming:
            sys.exit("The incoming folder isn't set (Settings).")
        return rescue_from_ipod(args.apply, os.path.join(incoming, "From iPod"))

    if args.covers_only or args.refresh_covers:
        itunes = itunes_connect()
        if wait_for_ipod(itunes) is None:
            sys.exit("No iPod among the iTunes sources.")
        _, active_by_key = index_library()
        covered, root = [], None
        if args.refresh_covers:
            done, total = refresh_device_artwork(itunes, active_by_key, args.refresh_covers)
            print(f"covers rewritten: {len(done)}/{total}")
            covered += done
            root, _ = device_thumbnails(find_ipod(itunes))
        if args.covers_only:
            done, total, root = fix_device_artwork(itunes, active_by_key)
            print(f"\rmissing covers set: {len(done)}/{total}          ")
            covered += done
        finish(itunes, root, covered)
        return

    if args.disk:
        sync_disk(args.disk, args.apply, args.subdir)
    elif args.mode == "device":
        sync_device(args.apply)
    else:
        sync_itunes(args.apply, args.mode, args.playlist)


if __name__ == "__main__":
    main()
