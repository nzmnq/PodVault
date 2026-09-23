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

  python src\\genres.py suggest          # AI fills in albums not in the file yet
  python src\\genres.py apply            # show what would change in the tags
  python src\\genres.py apply --apply    # write the tags

'suggest' never touches lines already in the file, so your corrections stay.
"""

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mutagen.id3 import ID3, TCON, TIT1

import ai
import settings
from musiclib import drop_v24_frames

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
    _, active, archive = settings.library_paths(cfg)
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
                files = sorted(os.path.join(folder, f) for f in os.listdir(folder)
                               if f.lower().endswith(".mp3")) if os.path.isdir(folder) else []
                if files:
                    out[f"{artist}/{album}"] = {"part": part, "folder": folder, "files": files}
    return out


def tag(tags, key):
    v = tags.get(key)
    return str(v.text[0]).strip() if v and v.text else ""


def describe(info):
    """What the AI gets to know about an album."""
    tags = ID3(info["files"][0])
    titles = []
    for p in info["files"][:4]:
        try:
            titles.append(tag(ID3(p), "TIT2"))
        except Exception:
            pass
    return {"artist": tag(tags, "TPE2") or tag(tags, "TPE1"), "album": tag(tags, "TALB"),
            "year": (tag(tags, "TYER") or tag(tags, "TDRC"))[:4],
            "genre": tag(tags, "TCON"), "titles": titles}


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
                print(f"  ! line {n} skipped (expected 'Artist/Album | Genre | Style'): {line}")
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
        print(f"  every album is already in {path}")
        return
    print(f"  {len(todo)} albums without a genre line; asking the AI...")
    lines = ["id | artist | album | year | current genre | some titles"]
    for i, k in enumerate(todo):
        d = describe(lib[k])
        lines.append(" | ".join(str(x).replace("|", "/") for x in
                                (i, d["artist"], d["album"], d["year"], d["genre"],
                                 "; ".join(d["titles"]))))
    backend = ai.pick_backend(cfg, backend)
    print(f"  through {ai.backend_name(backend)} (usually under a minute)")
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
    print(f"\n  suggested {len(got)} albums -> {path}")
    for k in list(got)[:15]:
        print(f"    {k}  ->  {got[k][0]} / {got[k][1]}")
    if len(got) > 15:
        print(f"    ... {len(got) - 15} more")
    if missing:
        print(f"  !! no answer for {len(missing)} albums; run suggest again")
    print("\nCheck the file, correct what's wrong, then apply.")


# --------------------------------------------------------------- apply


def apply(cfg, path, write):
    lib = albums(cfg)
    wanted = read_file(path)
    if not wanted:
        sys.exit(f"No genres yet in {path}. Run 'suggest' first.")
    changes = []   # (key, files to change, old genre, new genre, new style)
    for key, (genre, style) in sorted(wanted.items()):
        if key not in lib:
            continue
        todo, old = [], set()
        for p in lib[key]["files"]:
            try:
                tags = ID3(p)
            except Exception:
                continue
            if tag(tags, "TCON") != genre or tag(tags, "TIT1") != style:
                todo.append(p)
                old.add(f"{tag(tags, 'TCON')} / {tag(tags, 'TIT1')}".strip(" /"))
        if todo:
            changes.append((key, todo, ", ".join(sorted(old)) or "-", genre, style))
    gone = [k for k in wanted if k not in lib]
    missing = [k for k in lib if k not in wanted]

    print("=" * 70)
    print(f"GENRES  ({path})")
    print("=" * 70)
    print(f"  albums in the file        : {len(wanted)}")
    print(f"  albums whose tags change  : {len(changes)}  ({sum(len(c[1]) for c in changes)} tracks)")
    print(f"  albums not in the file    : {len(missing)}  (run 'suggest')")
    if gone:
        print(f"  lines for missing albums  : {len(gone)}  (moved or deleted; ignored)")
    for key, files, old, genre, style in changes[:40]:
        print(f"  {key}\n      {old}  ->  {genre} / {style}")
    if len(changes) > 40:
        print(f"  ... {len(changes) - 40} more")

    if not write:
        print("\nNothing written. Add --apply.")
        return
    done = 0
    for key, files, _, genre, style in changes:
        for p in files:
            tags = ID3(p)
            tags.delall("TCON")
            tags.delall("TIT1")
            tags.add(TCON(encoding=1, text=[genre]))
            if style:
                tags.add(TIT1(encoding=1, text=[style]))
            drop_v24_frames(tags)
            tags.save(p, v2_version=3, v1=2)
            done += 1
    print(f"\nTags written: {done} tracks in {len(changes)} albums.")
    print("The iPod gets the new genres on the next sync.")


def main():
    cfg = settings.require()
    ap = argparse.ArgumentParser(description="genres per album")
    ap.add_argument("step", choices=("suggest", "apply"))
    ap.add_argument("--apply", action="store_true", help="apply: actually write the tags")
    ap.add_argument("--backend", choices=ai.BACKENDS, help="suggest: which AI to ask")
    args = ap.parse_args()
    path = settings.path("genres_file", cfg)
    if args.step == "suggest":
        suggest(cfg, path, args.backend)
    else:
        apply(cfg, path, args.apply)


if __name__ == "__main__":
    main()
