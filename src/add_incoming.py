"""
Add new tracks to the library.

Brings them to the same shape as everything else (see build_clean.py):
Album Artist set, features moved from the artist into the title, genre
cleaned; an mp3's tag in ID3v2.3/UTF-16. mp3 and m4a (AAC, Apple Lossless)
are taken, other formats are reported. Incoming files already have cover
art embedded, so their own is kept.

SoundCloud downloads get their real title and artist from the track page
(see soundcloud.py); their fragmented m4a, which an iPod won't play and
which reads as 0 s long, is rewritten as a plain one (no re-encoding).
A single is an album of its own, named after the track, the way every store
files it ('Artist/Title/01 - Title'); ' - Single' is added when the artist
already has an album of that name. With the setting "Singles go to" on
'merged' (the old way, kept for libraries built like that) they go into the
artist's one 'Singles' album instead, numbered on.

A track counts as already there when its tags match a library track, or
when its audio does: the Chromaprint fingerprint (duplicates.py) catches
the same song under another title, spelling or artist.

Names that contain a comma, and aliases, are in artists.txt next to the
genres file — see ARTISTS_HELP.

  python src/add_incoming.py --fix-library        # unglue 'A, B' artists in the library

The dry run also saves its plan (<reports>/incoming_plan.json): every track,
what it becomes and whether it's already there. The window shows it as a
table to correct or untick tracks; '--apply --plan FILE' then adds exactly
that, without reading the library or comparing the audio a second time.

New files arrive as a flat pile with no folders, so the tags are the only
source of structure. The dry run (the default) shows what's in them —
albums, bitrates, tag versions, cover art, unreadable files — and which
tracks are already in the library. It replaces the old scan_incoming.py.

Where new tracks go (Active or Archive) and which folder they're picked up
from come from the settings; both can be overridden on the command line.

  python src/add_incoming.py                      # show the plan
  python src/add_incoming.py --apply              # add
  python src/add_incoming.py --apply --to archive # set aside in the archive
  python src/add_incoming.py --apply --replace    # and overwrite dupes
  python src/add_incoming.py "D:\\other\\folder"   # a different source
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duplicates
import settings
import soundcloud
import tags as audiotags
from i18n import _
from musiclib import norm, strip_edition, strip_feat

# Incoming files glue co-artists with a comma ('wifiskeleton, Jaydes'), the
# older part of the library with a slash, others write 'A feat. B'. All are
# split into the artist and the features (which go into the title).
#
# Only names listed in artists.txt are kept whole. It used to be "whatever
# the library already has", but then a glued name that got in once
# ('санкен, левко') protected itself on every later import.
SPLIT_SLASH = re.compile(r"\s*[/;]\s*")
SPLIT_COMMA = re.compile(r"\s*,\s*")
SPLIT_FEAT = re.compile(r"\s+(?:feat|ft|featuring)\.?\s+", re.I)

ARTISTS_HELP = """\
# Artist names for adding tracks.
# A plain line is a name kept whole although it has a comma or a slash in it.
# 'alias = Name' writes the alias as Name everywhere (case doesn't matter).
"""
DEFAULT_PROTECT = ("AC/DC", "125, Rue Montmartre")
SINGLES = "Singles"      # setting singles = 'merged': every single of an artist in one album


def single_album(title, artist, lib_albums):
    """A single's album: its title without features — or 'Title - Single' when the artist
    already has a real album of that name that doesn't hold this track."""
    album = strip_feat(title) or title
    have = lib_albums.get((norm(artist), norm(album)))
    return f"{album} - Single" if have and norm(strip_feat(title)) not in have else album


def load_artists(cfg):
    """(protected {norm: name}, aliases {norm: name}) from artists.txt."""
    protect = {norm(n): n for n in DEFAULT_PROTECT}
    alias = {}
    path = os.path.join(os.path.dirname(settings.path("genres_file", cfg) or settings.ROOT), "artists.txt")
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(ARTISTS_HELP + "".join(n + "\n" for n in DEFAULT_PROTECT))
        except OSError:
            pass
        return protect, alias
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            a, sep, b = line.partition("=")
            if sep:
                alias[norm(a)] = b.strip()
                protect[norm(b)] = b.strip()
            else:
                protect[norm(line)] = line
    return protect, alias

GENRE_EXACT = {
    "Rap/Hip Hop": "Hip-Hop",
    "Films/Games": "Soundtrack",
    "Films/Games/Film Scores": "Soundtrack",
    "Contemporary Pop": "Pop",
    "Pop & Russian Pop": "Pop",
}
GENRE_SPLIT = re.compile(r"\s*/\s*|\s*,\s*|\s+&\s+")


def clean_genre(g):
    if not g:
        return None
    g = g.strip()
    if g in GENRE_EXACT:
        return GENRE_EXACT[g]
    first = GENRE_SPLIT.split(g)[0].strip()
    return first or None


def safe_name(s, maxlen=120):
    s = re.sub(r'[<>:"/\\|?*]', "_", s or "")
    s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
    return s[:maxlen].strip() or "_"


def parse_pair(raw):
    if not raw:
        return None, None
    m = re.match(r"^(\d+)(?:/(\d+))?", str(raw))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def split_artists(value, protect):
    """Split an artist string into individual names.

    `protect` maps normalised names to names kept whole ('AC/DC').
    """
    if not value:
        return []
    v = value.strip()
    if norm(v) in protect:
        return [protect[norm(v)]]
    out = []
    for chunk in SPLIT_FEAT.split(v):
        if norm(chunk) in protect:
            out.append(protect[norm(chunk)])
            continue
        for p in SPLIT_SLASH.split(chunk):
            if norm(p) in protect or "," not in p:
                out.append(protect.get(norm(p), p))
            else:
                out.extend(x for x in SPLIT_COMMA.split(p))
    out = [x.strip() for x in out if x.strip()]
    return out or [v]


FEAT_GROUP = re.compile(r"\s*\((?:feat|ft|featuring)\.?\s+([^)]*)\)", re.I)


def with_feats(title, feats):
    """'Title' + ['B'] -> 'Title (feat. B)', leaving out names the title already has.

    One group only: names join a '(feat. …)' the title already has, and two such groups
    become one ('Lemonade (feat. NAV) (feat. Gunna)' -> 'Lemonade (feat. NAV, Gunna)').
    """
    groups = FEAT_GROUP.findall(title)
    feats = [f for f in feats if norm(f) not in norm(title)]
    if not groups:
        return title + (f" (feat. {', '.join(feats)})" if feats else "")
    names = ", ".join([g.strip() for g in groups] + feats)
    first = FEAT_GROUP.search(title)
    rest = FEAT_GROUP.sub("", title[first.end():])
    return f"{title[:first.start()]} (feat. {names}){rest}"


def resolve_album_artist(candidates):
    """The album's real artist out of a set of variants.

    'wifiskeleton', 'wifiskeleton, Jaydes' -> 'wifiskeleton'
    The shortest variant that's a prefix of all the others wins.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return next(iter(candidates))
    shortest = min(candidates, key=lambda c: (len(norm(c)), c))
    base = norm(shortest)
    if base and all(norm(c).startswith(base) for c in candidates):
        return shortest
    return None


# ------------------------------------------------------- what's there


def load_library(library_dir):
    """What's already there: albums, tracks, name spellings, (path, length) per track.

    From the library index (library.listing): only new or changed albums are opened. It used
    to read every file of the library on every import — minutes on a slow drive.
    """
    import library
    albums = defaultdict(set)   # (artist, base album) -> track titles
    names = Counter()           # normalised name -> count
    spelling = {}
    lengths = []
    rows = library.listing(library_dir)
    album_of = {rel: rec.get("album") or "" for rel, rec in library.album_info(library_dir).items()}
    for fp, rel, _size, _mtime, t in rows:
        lengths.append((fp, t["length"]))
        aa = t["albumartist"] or t["artist"]
        al = album_of.get(rel, "")
        ti = t["title"]
        if not aa:
            continue
        names[norm(aa)] += 1
        spelling.setdefault(norm(aa), aa)
        ar = t["artist"]
        if ar:
            names[norm(ar)] += 1
            spelling.setdefault(norm(ar), ar)
        if al and ti:
            albums[(norm(aa), norm(strip_edition(al)))].add(norm(strip_feat(ti)))
    return albums, spelling, lengths


# -------------------------------------------------------------- plan


OTHER_AUDIO = (".flac", ".wav", ".ogg", ".opus", ".aac", ".wma", ".alac", ".aiff")


def remux(ffmpeg, src, tmp):
    """A fragmented m4a (SoundCloud, YouTube) rewritten as a plain one, audio and cover
    copied as they are. Returns the new path, or None."""
    out = os.path.join(tmp, f"{len(os.listdir(tmp)):04d}.m4a")
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", src, "-map", "0:a:0", "-map", "0:v?",
                        "-c", "copy", "-disposition:v", "attached_pic", "-movflags", "+faststart", out],
                       capture_output=True)
    return out if r.returncode == 0 and os.path.exists(out) else None


def needs_remux(fp):
    """An m4a mutagen can't read, or one that says it's 0 s long (fragmented)."""
    if not fp.lower().endswith(".m4a"):
        return False
    try:
        return not audiotags.info(fp)["length"]
    except audiotags.Unreadable:
        return True


def read_incoming(src, protect, cfg, tmp):
    """Read every mp3 and m4a in the folder. Returns (files, unreadable).

    Other audio formats are reported as unreadable rather than skipped
    silently: they need converting first (FLAC -> ALAC is in the menu).
    """
    files, broken = [], []
    ffmpeg = settings.ffmpeg(cfg)
    reports = settings.path("reports_dir", cfg)
    pages = soundcloud.load_cache(reports)
    total = sum(len(audiotags.audio_files(fs)) for _r, _s, fs in os.walk(src))
    seen = settings.Progress(_("new tracks read: {n}/{total}"), total=total, every=1)
    for root, _skip, fs in os.walk(src):
        for fn in sorted(f for f in fs if f.lower().endswith(OTHER_AUDIO)):
            broken.append((fn, _("not mp3 or m4a — convert it first")))
        for fn in audiotags.audio_files(fs):
            seen.tick()
            fp = orig = os.path.join(root, fn)
            if needs_remux(fp):
                fp = remux(ffmpeg, fp, tmp) if ffmpeg else None
                if not fp:
                    broken.append((fn, _("fragmented m4a, and ffmpeg can't rewrite it")
                                   if ffmpeg else _("fragmented m4a — ffmpeg is needed to rewrite it")))
                    continue
            try:
                t = audiotags.read(fp)
                info = audiotags.info(fp)
            except audiotags.Unreadable as e:
                broken.append((fn, _("no ID3 tag") if str(e) == "no ID3 tag" else str(e)))
                continue
            tn, tt = parse_pair(t["track"])
            dn, dt = parse_pair(t["disc"])
            f = {
                "src": fp,
                "orig": orig,
                "file": fn,
                "artists": split_artists(t["artist"], protect),
                "aa_raw": t["albumartist"] or t["artist"] or None,
                "album": t["album"] or None,
                "title": t["title"] or os.path.splitext(fn)[0],
                "genre": t["genre"] or None,
                "year": t["year"] or None,
                "composer": t["composer"] or None,
                "track": tn, "track_total": tt, "disc": dn, "disc_total": dt,
                "bitrate": info["bitrate"],
                "length": info["length"],
                "has_art": t["art"],
                "ver": t["version"],
            }
            # Letters that lost their high byte: the file name has them right.
            if soundcloud.mangled(f["title"]) or soundcloud.mangled(t["artist"]):
                a, ti = soundcloud.name_from_file(fn, t["artist"])
                f["title"] = ti
                if soundcloud.mangled(t["artist"]) and a:
                    f["artists"] = split_artists(a, protect)
                    f["aa_raw"] = a
            url = soundcloud.url_of(orig)
            if url:
                soundcloud_tags(f, soundcloud.lookup(url, pages), protect)
                f["soundcloud"] = True
            files.append(f)
    seen.end()
    soundcloud.save_cache(reports, pages)
    return files, broken


def soundcloud_tags(f, page, protect):
    """Artist, features, title and producers of a SoundCloud download.

    From the track page when it can be had; otherwise the downloader's own
    title goes through the same parsing, with its first artist as the uploader.
    """
    if page:
        artist, feats, title, prods = soundcloud.parse(page["title"], page["user"], page["artist"])
        f["year"] = page["year"] or f["year"]
    else:
        artist, feats, title, prods = soundcloud.parse(f["title"], f["artists"][0] if f["artists"] else "")
        feats += [a for a in f["artists"][1:] if norm(a) != norm(artist)]
    names = split_artists(artist, protect) if artist else f["artists"]
    f["artists"] = names[:1] + [n for n in names[1:] + feats if norm(n) not in {norm(x) for x in names[:1]}]
    f["artists"] = list(dict.fromkeys(f["artists"]))
    f["aa_raw"] = f["artists"][0] if f["artists"] else None
    raw = f["title"]
    f["title"] = title
    if prods:
        f["composer"] = ", ".join(prods)
    # a SoundCloud track is its own release: the downloader puts its title in the album
    if not f["album"] or soundcloud.mangled(f["album"]) or norm(f["album"]) in (
            norm(raw), norm(title), norm(page["title"]) if page else "", "unknown"):
        f["album"] = None
    f["year"] = (f["year"] or "")[:4] or None


def speller(spelling, alias, files):
    """name -> its one spelling: the alias, else the library's, else the most common
    one among the new tracks ('ONDA ANDAR' over 'onda andar', 'Onda Andar')."""
    seen = defaultdict(Counter)
    for f in files:
        for i, a in enumerate(f["artists"]):
            seen[norm(a)][a] += 3 if i == 0 else 1     # the main artist's spelling wins
    def spell(name):
        k = norm(name)
        if k in alias:
            return alias[k]
        if k in spelling:
            return spelling[k]
        return seen[k].most_common(1)[0][0] if seen.get(k) else name
    return spell


def build_plan(files, protect, spell, dest_root, lib_albums, merged=False):
    # group by the base album title, so that the three variants of
    # 'wifiskeleton, X' merge into one album; a track with no album is a
    # single: an album of its own
    groups = defaultdict(list)
    for f in files:
        if f["album"] and norm(f["album"]) != norm(SINGLES):
            groups[norm(strip_edition(f["album"]))].append(f)
        else:
            first = f["artists"][0] if f["artists"] else (f["aa_raw"] or "")
            groups[("single", norm(spell(first)), "" if merged else norm(f["title"]))].append(f)

    plan, notes = [], []
    for key, g in groups.items():
        single = isinstance(key, tuple)
        cands = {f["aa_raw"] for f in g if f["aa_raw"]}
        aa = resolve_album_artist({spell(c) for c in cands})
        if aa is None:
            counts = Counter(spell(f["artists"][0]) for f in g if f["artists"])
            aa = counts.most_common(1)[0][0] if counts else "Unknown Artist"
            if not single:
                notes.append({"kind": "compilation?", "album": g[0]["album"], "used": aa,
                              "candidates": sorted(cands)})
        else:
            parts = split_artists(aa, protect)
            if len(parts) > 1:
                notes.append({"kind": "glued", "album": g[0]["album"] or g[0]["title"],
                              "used": parts[0], "candidates": [aa]})
                aa = parts[0]
        aa = spell(aa)

        if single and merged:
            album = SINGLES
        else:
            album = single_album(g[0]["title"], aa, lib_albums) if single else g[0]["album"]
        # merged singles continue the numbering of the artist's 'Singles' already in the library
        have = len(lib_albums.get((norm(aa), norm(SINGLES)), ())) if single and merged else 0
        for n, f in enumerate(sorted(g, key=lambda f: f["title"].lower()) if single else g, 1):
            names = [spell(a) for a in f["artists"]] or [aa]
            title = with_feats(f["title"], [x for x in names[1:] if norm(x) != norm(names[0])])
            track, total = ((have + n, have + len(g)) if merged else (1, 1)) if single \
                else (f["track"], f["track_total"])
            plan.append({
                **f,
                "album_artist": aa,
                "album": album,
                "track": track, "track_total": total,
                "out_artist": names[0],
                "out_title": title,
                "out_genre": clean_genre(f["genre"]),
                "dest": os.path.join(
                    dest_root, safe_name(aa), safe_name(album),
                    f"{track or 0:02d} - {safe_name(title)}{audiotags.extension(f['src'])}"),
            })
    return plan, notes


def same_audio(plan, lib_lengths, cfg):
    """{id(item): what it duplicates} for tracks whose audio is already there.

    Compared with library tracks of a close length and with the new tracks
    before it (the same song downloaded twice under two names).
    """
    prints = duplicates.Prints(cfg)
    if not prints.exe:
        settings.problem(_("fpcalc (Chromaprint) not found: the audio isn't compared, only the tags. "
                           "Install chromaprint (brew / apt / choco) to compare the audio."))
        return {}
    found, earlier = {}, []
    names = {it["src"]: _("{file} (in this folder)").format(file=it["file"]) for it in plan}
    origs = {it["src"]: it["orig"] for it in plan}
    progress = settings.Progress(_("audio compared: {n}/{total}"), total=len(plan), every=1)
    try:
        for it in plan:
            progress.tick()
            mine = prints.get(it["src"], it["orig"])
            if mine is None:
                continue
            pool = [(p, ln) for p, ln in lib_lengths if duplicates.close_length(ln, it["length"])]
            pool += [(e["src"], e["length"]) for e in earlier if duplicates.close_length(e["length"], it["length"])]
            for p, ln in pool:
                other = prints.get(p, origs.get(p))
                if other is not None and duplicates.similarity(
                        mine, other, ln - it["length"]) >= duplicates.SAME_AUDIO:
                    found[id(it)] = names.get(p, p)
                    break
            else:
                earlier.append(it)
    finally:
        progress.end()
        prints.save()
    return found


# what a saved plan keeps of each track: enough to write it without reading anything again
PLAN_KEYS = ("orig", "file", "out_artist", "out_title", "album_artist", "album", "out_genre", "year",
             "composer", "track", "track_total", "disc", "disc_total", "bitrate", "length", "same_as")


def plan_file(cfg):
    return os.path.join(settings.path("reports_dir", cfg), "incoming_plan.json")


def save_plan(cfg, args, plan, dupes, lib_albums):
    """The dry run's plan, for the window's review table and for '--apply --plan'."""
    dupe_ids = {id(it) for it in dupes}
    items = [{**{k: it.get(k) for k in PLAN_KEYS}, "status": "dupe" if id(it) in dupe_ids else "new",
              "skip": id(it) in dupe_ids} for it in plan]
    singles = {it["album_artist"]: len(lib_albums.get((norm(it["album_artist"]), norm(SINGLES)), ()))
               for it in plan if it["album"] == SINGLES}
    path = plan_file(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": os.path.abspath(args.source), "to": args.to, "singles": singles,
                   "items": items}, f, ensure_ascii=False, indent=1)


def add_from_plan(args, cfg, path, dest_root):
    """--apply --plan: add what the (maybe corrected) plan says. Nothing is read again but
    the tracks themselves; a fragmented m4a is rewritten again, the temporary copy is gone."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data["source"] != os.path.abspath(args.source) or data["to"] != args.to:
        sys.exit(_("The plan was made for another folder or target: check again."))
    ffmpeg = settings.ffmpeg(cfg)
    have = {(norm(aa), norm(SINGLES)): n for aa, n in data.get("singles", {}).items()}
    with tempfile.TemporaryDirectory(prefix="podvault-") as tmp:
        todo = []
        for it in data["items"]:
            if it["skip"]:
                continue
            if not os.path.exists(it["orig"]):
                settings.problem(_("gone since the check: {file}").format(file=it["file"]))
                continue
            src = it["orig"]
            if needs_remux(src):
                src = remux(ffmpeg, src, tmp) if ffmpeg else None
                if not src:
                    settings.problem(_("fragmented m4a, and ffmpeg can't rewrite it") + f": {it['file']}")
                    continue
            album = it["album"] or strip_feat(it["out_title"])
            todo.append({**it, "src": src, "album": album, "replace": False,
                         "dest": os.path.join(dest_root, safe_name(it["album_artist"]), safe_name(album),
                                              f"{it['track'] or 0:02d} - {safe_name(it['out_title'])}"
                                              f"{audiotags.extension(src)}")})
        renumber_singles(todo, have)
        todo, renamed, dropped = unique_dests(todo)
        for n, it in enumerate(todo, 1):
            write_track(it)
            if n % 50 == 0:
                print(f"  ... {n}/{len(todo)}")
    print(_("\nTracks added: {n}").format(n=len(todo)))
    skipped = sum(1 for it in data["items"] if it["skip"])
    if skipped:
        print(_("Left out, as the plan says: {n}").format(n=skipped))
    os.remove(path)             # used up: the next import starts from a fresh check
    import genres
    import library
    genres.keep_tags_in_line(cfg, listing=library.listing(settings.library_paths(cfg)[0], quick=True))


def renumber_singles(items, lib_albums):
    """Merged singles that are really added: numbered on from the library's, without gaps.
    lib_albums: load_library's, or {(artist, 'singles'): count} from a saved plan."""
    by_artist = defaultdict(list)
    for it in items:
        if it["album"] == SINGLES:
            by_artist[it["album_artist"]].append(it)
    for aa, group in by_artist.items():
        have = lib_albums.get((norm(aa), norm(SINGLES)), ())
        have = have if isinstance(have, int) else len(have)
        for n, it in enumerate(group, have + 1):
            it["track"], it["track_total"] = n, have + len(group)
            it["dest"] = os.path.join(os.path.dirname(it["dest"]),
                                      f"{n:02d} - {safe_name(it['out_title'])}{audiotags.extension(it['src'])}")


def unique_dests(items):
    """Give every item a destination nothing else uses.

    The same song downloaded twice maps to one path: the second copy is
    dropped (it would only be a duplicate on the iPod). Different tracks
    that happen to map to one path — or onto a file already sitting there —
    get a ' (2)' suffix instead of silently overwriting it.
    Returns (items to write, renamed count, dropped count).
    """
    taken = {}
    keep, renamed, dropped = [], 0, 0
    for it in items:
        ident = (norm(it["album_artist"]), norm(it["album"] or ""), norm(it["out_title"]))
        first = taken.get(os.path.normcase(it["dest"]))
        if first == ident:
            dropped += 1
            continue
        base, ext = os.path.splitext(it["dest"])
        dest, n = it["dest"], 1
        while os.path.normcase(dest) in taken or (os.path.exists(dest) and not it.get("replace")):
            n += 1
            dest = f"{base} ({n}){ext}"
        if dest != it["dest"]:
            renamed += 1
        taken.setdefault(os.path.normcase(it["dest"]), ident)
        taken[os.path.normcase(dest)] = ident
        it["dest"] = dest
        keep.append(it)
    return keep, renamed, dropped


def write_track(item):
    os.makedirs(os.path.dirname(item["dest"]), exist_ok=True)
    if audiotags.JOURNAL:
        audiotags.JOURNAL.added(item["dest"])
    shutil.copy2(item["src"], item["dest"])

    def pair(n, total):
        return (f"{n}/{total}" if total else str(n)) if n else ""

    # Frames the source file brought along (TIPL from its IPLS, sort frames...)
    # would otherwise go into the v2.3 tag as v2.4 ones: tags.write drops them.
    audiotags.write(item["dest"], {
        "artist": item["out_artist"], "albumartist": item["album_artist"],
        "title": item["out_title"], "album": item["album"], "genre": item["out_genre"],
        "composer": item.get("composer") or "",
        "track": pair(item["track"], item["track_total"]),
        "disc": pair(item["disc"], item["disc_total"]), "year": item["year"],
    })

    folder_jpg = os.path.join(os.path.dirname(item["dest"]), "folder.jpg")
    pic = audiotags.cover(item["dest"])
    if pic and not os.path.exists(folder_jpg):
        if audiotags.JOURNAL:
            audiotags.JOURNAL.added(folder_jpg)
        with open(folder_jpg, "wb") as f:
            f.write(pic)


def main():
    cfg = settings.require()
    library, active, archive = settings.library_paths(cfg)

    ap = argparse.ArgumentParser(description=_("fix the tags of new tracks and add them to the library"))
    ap.add_argument("source", nargs="?", default=settings.path("incoming_dir", cfg),
                    help=_("folder with the new tracks (setting: the incoming folder)"))
    ap.add_argument("--apply", action="store_true", help=_("actually add the tracks"))
    ap.add_argument("--replace", action="store_true",
                    help=_("overwrite tracks that are already in the library"))
    ap.add_argument("--to", choices=("archive", "active"), default=cfg["new_tracks_target"],
                    help=_("where to put new tracks (setting: {value})").format(value=cfg['new_tracks_target']))
    ap.add_argument("--plan", metavar="FILE",
                    help=_("with --apply: add what this saved plan says (the window's review)"))
    ap.add_argument("--split-singles", action="store_true",
                    help=_("take the old 'Singles' albums apart: every single an album of its own"))
    ap.add_argument("--fix-library", action="store_true",
                    help=_("split 'A, B' artists already in the library: features into the title"))
    args = ap.parse_args()
    protect, alias = load_artists(cfg)
    if args.apply:
        import undo_tags
        undo_tags.start(cfg, "fix-library" if args.fix_library else
                        "split-singles" if args.split_singles else "import")

    if args.split_singles:
        import fix_artists
        fix_artists.split_singles(library, args.apply, settings.path("genres_file", cfg),
                                  os.path.join(settings.path("reports_dir", cfg), "undone", "singles"))
        if args.apply and cfg.get("singles") != "own":
            # the library is filed the new way now: new singles follow it
            settings.save({**cfg, "singles": "own"})
            print(_("Setting \"Singles go to\" is now: own albums."))
        return

    if args.fix_library:
        import fix_artists
        return fix_artists.run(library, protect, alias, args.apply, settings.path("genres_file", cfg))

    if not args.source or not os.path.isdir(args.source):
        sys.exit(_("Folder not found: {folder}").format(folder=args.source))

    # After the Active/Archive split, writing into the library root would
    # put tracks outside both folders — so the target is always one of them.
    dest_root = archive if args.to == "archive" else active
    if args.apply and args.plan:
        return add_from_plan(args, cfg, args.plan, dest_root)

    print(_("Reading the library (what's there, how names are spelled): {folder}").format(folder=library))
    lib_albums, spelling, lib_lengths = load_library(library)
    print(_("Reading the new tracks: {folder}").format(folder=args.source))
    with tempfile.TemporaryDirectory(prefix="podvault-") as tmp:
        files, broken = read_incoming(args.source, protect, cfg, tmp)
        print(_("  read: {n} tracks").format(n=len(files)))
        print(_("Making the plan: albums, artists, file names..."))
        plan, notes = build_plan(files, protect, speller(spelling, alias, files), dest_root, lib_albums,
                                 merged=cfg.get("singles") == "merged")
        return report_and_add(args, cfg, plan, notes, files, broken, lib_albums, lib_lengths, dest_root)


def report_and_add(args, cfg, plan, notes, files, broken, lib_albums, lib_lengths, dest_root):
    fresh, dupes = [], []
    for it in plan:
        key = (norm(it["album_artist"]), norm(strip_edition(it["album"])))
        have = lib_albums.get(key, set())
        (dupes if norm(strip_feat(it["out_title"])) in have else fresh).append(it)

    print(_("Comparing the audio with the library (the first run takes a while)..."))
    same = same_audio(fresh, lib_lengths, cfg)
    for it in [it for it in fresh if id(it) in same]:
        it["same_as"] = same[id(it)]
        fresh.remove(it)
        dupes.append(it)

    by_album = defaultdict(list)
    for it in plan:
        by_album[(it["album_artist"], it["album"])].append(it)

    print("=" * 74)
    print(_("INCOMING: {tracks} tracks in {albums} albums   ({folder})").format(
        tracks=len(plan), albums=len(by_album), folder=args.source))
    print("=" * 74)
    target = _("ACTIVE") if args.to == "active" else _("ARCHIVE")
    rows = [(_("new to the library"), len(fresh)), (_("already there"), len(dupes)),
            (_("unreadable"), len(broken)), (_("will go into"), f"{target}  ({dest_root})")]
    width = max(len(label) for label, _v in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")
    print(" " * (width + 5) + (_("won't reach the iPod until you mark it [A]") if args.to == "archive"
                               else _("reaches the iPod on the next sync")))

    print("\n--- " + _("ALBUMS") + " ---")
    for (aa, al), items in sorted(by_album.items(), key=lambda x: (-len(x[1]), x[0])):
        n_dup = sum(1 for i in items if i in dupes)
        mark = "  " + _("({n} already there)").format(n=n_dup) if n_dup else ""
        br = sorted({i["bitrate"] for i in items})
        art = sum(1 for i in items if i["has_art"])
        print(_("  {n:3d} tr.").format(n=len(items)) + f"  {aa} — {al}{mark}")
        print("          " + _("{bitrates} kbps, cover on {art}/{n}").format(bitrates=br, art=art, n=len(items)))

    # a SoundCloud track without an album is a single on purpose
    no_album = [f for f in files if not f["album"] and not f.get("soundcloud")]
    if no_album:
        print("\n--- " + _("NO ALBUM IN TAGS ({n})").format(n=len(no_album)) + " ---")
        for f in no_album[:settings.LIST_MAX]:
            print(f"  {f['file']}   artist={f['aa_raw']!r} title={f['title']!r}")

    if notes:
        print("\n--- " + _("UNGLUED ARTIST STRINGS") + " ---")
        for n in notes:
            print(f"  [{n['kind']}] {n['album']} -> {n['used']}")
            print("        " + _("from: {list}").format(list=n['candidates']))

    if dupes:
        print("\n--- " + _("ALREADY IN THE LIBRARY ({n})").format(n=len(dupes)) + " ---")
        for it in dupes[:settings.LIST_MAX]:
            print(f"  {it['album_artist']} — {it['album']} / {it['out_title']}")
            if it.get("same_as"):
                print("        " + _("same audio as: {path}").format(path=it["same_as"]))
        if len(dupes) > settings.LIST_MAX:
            print(_("  ... {n} more").format(n=len(dupes) - settings.LIST_MAX))

    if broken:
        print("\n--- " + _("WON'T BE ADDED: unreadable, or not mp3 or m4a ({n})").format(n=len(broken)) + " ---")
        for fn, err in broken:
            print(f"  {fn}: {err}")

    if files:
        print("\n--- " + _("SUMMARY") + " ---")
        rows = [(_("bitrates"), dict(Counter(f['bitrate'] for f in files).most_common())),
                (_("tag versions"), dict(Counter(f['ver'] for f in files).most_common())),
                (_("with cover art"), f"{sum(1 for f in files if f['has_art'])}/{len(files)}")]
        width = max(len(label) for label, _v in rows)
        for label, value in rows:
            print(f"  {label.ljust(width)} : {value}")

    if not args.apply:
        save_plan(cfg, args, plan, dupes, lib_albums)
        if fresh or (args.replace and plan):
            print(_("\nNothing written. Run with --apply to add the new tracks."))
        else:
            settings.nothing_to_apply()
        return

    # an audio match sits under another name: replacing would add a second copy
    todo = [it for it in plan if not it.get("same_as")] if args.replace else fresh
    for it in todo:
        # --replace overwrites a dupe at its own path, nothing else
        it["replace"] = args.replace and it in dupes
    renumber_singles(todo, lib_albums)
    todo, renamed, dropped = unique_dests(todo)
    if dropped:
        print(_("  {n} tracks were in the folder twice; one copy of each is added").format(n=dropped))
    if renamed:
        print(_("  {n} tracks got a ' (2)' suffix: their file name was taken").format(n=renamed))
    for n, it in enumerate(todo, 1):
        write_track(it)
        if n % 50 == 0:
            print(f"  ... {n}/{len(todo)}")
    print(_("\nTracks added: {n}").format(n=len(todo)))
    if dupes and not args.replace:
        print(_("Skipped as already present: {n} (--replace overwrites)").format(n=len(dupes)))
    # a new track of an album the genres file already knows gets that album's genre
    import genres
    import library
    # quick: only the folders that just changed are read, and the index is warm for the sync
    genres.keep_tags_in_line(cfg, listing=library.listing(settings.library_paths(cfg)[0], quick=True))


if __name__ == "__main__":
    settings.run(main)
