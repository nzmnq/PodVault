"""
Genres per album: the AI suggests, you correct, the tags get written.

When the library was cleaned, genres were cut down to one broad word
('Rap/Hip Hop' -> 'Hip-Hop'), and many were wrong to begin with: hyperpop
sits under 'Alternative', Ukrainian rock under 'Pop'. Two tags now:

  Genre    (TCON) — broad: Rock, Hip-Hop, Electronic... This is what the
                    iPod's Genres menu shows, so it stays short.
  Grouping (TIT1) — the precise style: Hyperpop, Cloud Rap, Post-punk...
                    The AI playlists read it; the iPod menu isn't cluttered.

The genres file (a setting; data\\genres.txt by default) holds one line per
album and is the source of truth:

  Artist folder/Album folder | Genre | Style

  python src/genres.py suggest          # AI fills in albums not in the file yet
  python src/genres.py apply            # show what would change in the tags
  python src/genres.py apply --apply    # write the tags

'suggest' never touches lines already in the file, so your corrections stay.
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai
import settings
import tags as audiotags
from i18n import _

GENRES = ["Rock", "Alternative", "Pop", "Hip-Hop", "Electronic", "Metal", "Punk",
          "Soundtrack", "Jazz", "Folk", "R&B", "Classical", "Reggae", "Blues",
          "Country", "World"]

HEADER = f"""\
# Genre and style per album. Edit freely, then apply (menu: Genres -> Apply).
# Artist folder/Album folder | Genre (iPod Genres menu) | Style (precise, for the AI)
# Suggested genres: {", ".join(GENRES)}
"""


# ----------------------------------------------------------- the library


def albums(cfg):
    """{key: info} for every album folder in Active and Archive."""
    _skip, active, archive = settings.library_paths(cfg)
    out = {}
    for part, root in (("Active", active), ("Archive", archive)):
        if not os.path.isdir(root):
            continue
        for artist in sorted(os.listdir(root)):
            a_dir = os.path.join(root, artist)
            if not os.path.isdir(a_dir):
                continue
            for album in sorted(os.listdir(a_dir)):
                folder = os.path.join(a_dir, album)
                files = [os.path.join(folder, f) for f in audiotags.audio_files(os.listdir(folder))] \
                    if os.path.isdir(folder) else []
                if files:
                    out[f"{artist}/{album}"] = {"part": part, "folder": folder, "files": files}
    return out


def describe(info):
    """What the AI gets to know about an album."""
    tags = audiotags.read(info["files"][0])
    titles = []
    for p in info["files"][:4]:
        try:
            titles.append(audiotags.read(p)["title"])
        except audiotags.Unreadable:
            pass
    return {"artist": tags["albumartist"] or tags["artist"], "album": tags["album"],
            "year": tags["year"], "genre": tags["genre"], "titles": titles}


# ------------------------------------------------------------ the file


def read_file(path):
    """{key: (genre, style)} from the genres file."""
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                print(_("  ! line {n} skipped (expected 'Artist/Album | Genre | Style'): {line}").format(n=n, line=line))
                continue
            out[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")
    return out


# ------------------------------------------------------------- suggest


SYSTEM = f"""You tag one person's music library with genres.

For every album below give:
- genre: exactly one of {", ".join(GENRES)}. It is shown in an iPod's Genres
  menu, so keep it broad.
- style: the precise style in English, 1-3 comma-separated terms, as a fan of
  the artist would say it (e.g. "Hyperpop", "Cloud Rap, Emo Rap",
  "Post-punk, Russian Rock", "Ukrainian Folk Rock", "Dreamcore").

Use what you know about the artist and the album; the current genre tag is
often wrong or too broad, don't trust it. If you don't know the artist, judge
from the titles and the language, and keep the style generic rather than
invented.
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "albums": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"},
                           "genre": {"type": "string", "enum": GENRES},
                           "style": {"type": "string"}},
            "required": ["id", "genre", "style"],
            "additionalProperties": False}},
    },
    "required": ["albums"],
    "additionalProperties": False,
}


def suggest(cfg, path, backend):
    lib = albums(cfg)
    known = read_file(path)
    todo = [k for k in lib if k not in known]
    if not todo:
        print(_("  every album is already in {file}").format(file=path))
        return
    print(_("  {n} albums without a genre line; asking the AI...").format(n=len(todo)))
    lines = ["id | artist | album | year | current genre | some titles"]
    for i, k in enumerate(todo):
        d = describe(lib[k])
        lines.append(" | ".join(str(x).replace("|", "/") for x in
                                (i, d["artist"], d["album"], d["year"], d["genre"],
                                 "; ".join(d["titles"]))))
    backend = ai.pick_backend(cfg, backend)
    print(_("  through {ai} (usually under a minute)").format(ai=ai.backend_name(backend)))
    answer = ai.ask_json(cfg, SYSTEM, "\n".join(lines), SCHEMA, backend)

    got = {}
    for a in answer.get("albums", []):
        if 0 <= a.get("id", -1) < len(todo):
            got[todo[a["id"]]] = (a["genre"], a.get("style", "").replace("|", "/").strip())
    new = not os.path.isfile(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write(HEADER)
        f.write(f"\n# suggested {datetime.date.today()} — check these\n")
        for k in todo:
            if k in got:
                f.write(f"{k} | {got[k][0]} | {got[k][1]}\n")
    missing = [k for k in todo if k not in got]
    print(_("\n  suggested {n} albums -> {file}").format(n=len(got), file=path))
    for k in list(got)[:15]:
        print(f"    {k}  ->  {got[k][0]} / {got[k][1]}")
    if len(got) > 15:
        print("  " + _("  ... {n} more").format(n=len(got) - 15))
    if missing:
        print(_("  !! no answer for {n} albums; run suggest again").format(n=len(missing)))
    print(_("\nCheck the file, correct what's wrong, then apply."))


# --------------------------------------------------------------- apply


def differences(lib, wanted):
    """[(key, files to change, old genre, new genre, new style)]: tags that don't match the file."""
    changes = []
    for key, (genre, style) in sorted(wanted.items()):
        if key not in lib:
            continue
        todo, old = [], set()
        for p in lib[key]["files"]:
            try:
                tags = audiotags.read(p)
            except audiotags.Unreadable:
                continue
            if tags["genre"] != genre or tags["grouping"] != style:
                todo.append(p)
                old.add(f"{tags['genre']} / {tags['grouping']}".strip(" /"))
        if todo:
            changes.append((key, todo, ", ".join(sorted(old)) or "-", genre, style))
    return changes


def write_tags(changes):
    """Write the file's genre and style into the tags; returns the number of tracks."""
    done = 0
    for key, files, _skip, genre, style in changes:
        for p in files:
            audiotags.write(p, {"genre": genre, "grouping": style})
            done += 1
    return done


def keep_tags_in_line(cfg, write=True):
    """Bring tags that differ from the genres file back in line — quietly, one line of output.

    Run after new tracks are added and before a sync, so neither a new track
    nor the iPod ends up with a genre the file already corrected. Without a
    genres file there's nothing to do. Returns the number of albums concerned.
    """
    wanted = read_file(settings.path("genres_file", cfg))
    if not wanted:
        return 0
    changes = differences(albums(cfg), wanted)
    if not changes:
        return 0
    if write:
        done = write_tags(changes)
        print(_("  genres from the genres file written: {tracks} tracks in {albums} albums").format(
            tracks=done, albums=len(changes)))
    else:
        print(_("  genres from the genres file to write first: {albums} albums").format(albums=len(changes)))
    return len(changes)


def apply(cfg, path, write):
    lib = albums(cfg)
    wanted = read_file(path)
    if not wanted:
        sys.exit(_("No genres yet in {file}. Run 'suggest' first.").format(file=path))
    changes = differences(lib, wanted)
    gone = [k for k in wanted if k not in lib]
    missing = [k for k in lib if k not in wanted]

    print("=" * 70)
    print(_("GENRES  ({file})").format(file=path))
    print("=" * 70)
    rows = [(_("albums in the file"), len(wanted)),
            (_("albums whose tags change"), _("{n}  ({tracks} tracks)").format(
                n=len(changes), tracks=sum(len(c[1]) for c in changes))),
            (_("albums not in the file"), _("{n}  (run 'suggest')").format(n=len(missing)))]
    if gone:
        rows.append((_("lines for missing albums"), _("{n}  (moved or deleted; ignored)").format(n=len(gone))))
    width = max(len(label) for label, _v in rows)
    for label, value in rows:
        print(f"  {label.ljust(width)} : {value}")
    for key, files, old, genre, style in changes[:40]:
        print(f"  {key}\n      {old}  ->  {genre} / {style}")
    if len(changes) > 40:
        print(_("  ... {n} more").format(n=len(changes) - 40))

    if not write:
        print(_("\nNothing written. Add --apply."))
        return
    done = write_tags(changes)
    print(_("\nTags written: {tracks} tracks in {albums} albums.").format(tracks=done, albums=len(changes)))
    print(_("The iPod gets the new genres on the next sync."))


def main():
    cfg = settings.require()
    ap = argparse.ArgumentParser(description=_("genres per album"))
    ap.add_argument("step", choices=("suggest", "apply"),
                    help=_("suggest: the AI fills in the file; apply: write it into the tags"))
    ap.add_argument("--apply", action="store_true", help=_("apply: actually write the tags"))
    ap.add_argument("--backend", choices=ai.BACKENDS, help=_("suggest: which AI to ask"))
    args = ap.parse_args()
    path = settings.path("genres_file", cfg)
    if args.step == "suggest":
        suggest(cfg, path, args.backend)
    else:
        apply(cfg, path, args.apply)


if __name__ == "__main__":
    main()
