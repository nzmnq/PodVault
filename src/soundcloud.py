"""
SoundCloud downloads: what their tags get wrong and how it's repaired.

Every uploader writes the title their own way ('Kill [prod. X]', '+ guest
Title', 'Artist + guest - Title #2016 ✋'), and the downloader glues pieces of
that into the artist tag. So the tag is not trusted. The comment holds the
track's URL; its page gives the real title, the uploader and, if the
uploader filled it in, the declared artist. The title is then taken apart:

  '[prod. X]' / '(prod. X)'   -> producer (the composer field), not an artist
  'Title + X', '+ X Title'    -> feat. X
  'Artist + X - Title'        -> artist, feat. X
  #hashtags, emoji, '(2025)'  -> dropped

Some downloads also keep only the low byte of every Cyrillic letter
('Интернет' -> '\\x18=B5@=5B'); the file name has it right, so the title
comes from there when the page can't be had.

Pages are cached in <reports>/soundcloud.json: a deleted track stays None.
"""

import json
import os
import re
import unicodedata
import urllib.request

URL = re.compile(rb"https?://(?:www\.|m\.)?soundcloud\.com/[^\x00\s\"'<>]+")
HYDRATION = re.compile(r"window\.__sc_hydration\s*=\s*(\[.*?\]);</script>", re.S)

PROD = re.compile(r"[\[(]\s*prod(?:uced)?\.?\s*(?:by\s+)?([^\])]*)[\])]?", re.I)
FEAT = re.compile(r"[\[(]?\s*\b(?:feat|ft|featuring)\.?\s+([^\])]+)[\])]?", re.I)
# a hashtag anywhere but at the very start: '#2016GreenWorld' is the whole title
HASHTAG = re.compile(r"(?<=.)#[^\s#{}()\[\]]+")
TAIL_TAGS = re.compile(r"(?:\s*#\S+){2,}\s*$")
YEAR = re.compile(r"[\[(]\s*(?:19|20)\d\d\s*[\])]")
EMPTY_BRACKETS = re.compile(r"[\[({]\s*[\])}]")
NAMES = re.compile(r"\s*(?:,|&|\s+x\s+|\s+\+\s+)\s*", re.I)


def url_of(path):
    """The SoundCloud URL the downloader left in the comment, or None.

    Read from the raw bytes: mutagen gives up on some of these files.
    """
    with open(path, "rb") as f:
        m = URL.search(f.read())
    return m.group(0).decode("utf-8", "replace") if m else None


def mangled(s):
    """Text that lost the high byte of its letters ('\\x18=B5@=5B')."""
    return any(ord(c) < 0x20 for c in s or "")


def name_from_file(fn, artist_tag):
    """(artist, title) out of the downloader's 'Artist - Title.m4a' name."""
    base = re.sub(r"\(\d+\)$", "", os.path.splitext(fn)[0]).strip()
    if artist_tag and not mangled(artist_tag) and base.startswith(artist_tag + " - "):
        return artist_tag, base[len(artist_tag) + 3:]
    a, sep, t = base.partition(" - ")
    return (a, t) if sep else (None, base)


# ------------------------------------------------------------ the page


def _cache_path(reports):
    return os.path.join(reports, "soundcloud.json")


def load_cache(reports):
    try:
        with open(_cache_path(reports), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(reports, cache):
    os.makedirs(reports, exist_ok=True)
    tmp = _cache_path(reports) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _cache_path(reports))


def fetch(url):
    """{'title', 'user', 'artist', 'year'} from the track page; None if the track is gone.

    Network errors are raised: a failed fetch must not be cached as 'gone'.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", "replace")
    m = HYDRATION.search(html)
    sound = next((x["data"] for x in json.loads(m.group(1)) if x.get("hydratable") == "sound"),
                 None) if m else None
    if not sound:
        return None
    date = sound.get("release_date") or sound.get("display_date") or sound.get("created_at") or ""
    return {"title": sound.get("title") or "", "user": (sound.get("user") or {}).get("username") or "",
            "artist": (sound.get("publisher_metadata") or {}).get("artist") or "",
            "year": date[:4]}


def lookup(url, cache):
    """fetch() through the cache. A network error gives None and isn't cached."""
    if url not in cache:
        try:
            cache[url] = fetch(url)
        except Exception:
            return None
    return cache[url]


# ------------------------------------------------------- the title


def _decor(s):
    """Emoji and symbols out: an old iPod shows them as boxes."""
    return "".join(c for c in s if unicodedata.category(c) not in ("So", "Sk", "Cf", "Co")
                   and c not in "️")


def _tidy(s):
    s = EMPTY_BRACKETS.sub(" ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" -_+|/\\")


def names(s):
    """'A, B & C' -> ['A', 'B', 'C'] with '@' dropped."""
    return [n.strip().lstrip("@").strip() for n in NAMES.split(s or "") if n.strip(" @")]


def _artist(s):
    """A declared artist without the hashtags people put there ('ONDA ANDAR #2016')."""
    return _tidy(re.sub(r"\s+#\S+", "", s or "")).lstrip("@")


def parse(title, uploader="", declared=""):
    """A SoundCloud title -> (artist, [feats], clean title, [producers])."""
    t = _decor(title or "")
    prods = [n for m in PROD.finditer(t) for n in names(m.group(1))]
    t = PROD.sub(" ", t)

    feats = []
    m = FEAT.search(t)
    if m:
        feats += names(m.group(1))
        t = t[:m.start()] + " " + t[m.end():]

    artist = None
    left, sep, right = t.partition(" - ")
    if sep and left.strip():
        parts = names(left)
        artist, feats, t = parts[0], parts[1:] + feats, right
    if t.lstrip().startswith("+ "):
        # '+ guest Title': the first word is the guest
        guest, _sp, rest = t.lstrip()[2:].strip().partition(" ")
        feats.append(guest.lstrip("@"))
        t = rest
    elif " + " in t:
        t, *more = t.split(" + ")
        feats += [n for part in more for n in names(part)]

    # '#2016', '#pharaohseason #2016' go; a lone '#STranger' is a word of the title
    t = TAIL_TAGS.sub(" ", t) if not t.lstrip().startswith("#") else t
    t = HASHTAG.sub(lambda m: " " if re.search(r"\d", m.group(0)) else m.group(0), t)
    t = YEAR.sub(" ", t)
    t = _tidy(t) or _tidy(_decor(title or ""))

    dec = names(_artist(declared))
    if not artist:
        artist = dec[0] if dec else _artist(uploader) or None
    feats += dec[1:]

    def keep(seq, *drop):
        seen = {_key(d) for d in drop if d}
        out = []
        for n in seq:
            if n and _key(n) not in seen:
                seen.add(_key(n))
                out.append(n)
        return out

    return artist, keep(feats, artist), t, keep(prods, artist)


def _key(s):
    return re.sub(r"\W+", "", (s or "").lower())


def selfcheck():
    """Real titles from a SoundCloud download and what they must become (run by the tests)."""
    cases = {
        ("Kill((((Spike))))InMyHead (DLC) [prod. Onda Andar ]", "ONDA ANDAR", ""):
            ("ONDA ANDAR", [], "Kill((((Spike))))InMyHead (DLC)", []),
        ("+ cowboyclicker GhostRunner #2016", "ONDA ANDAR", ""):
            ("ONDA ANDAR", ["cowboyclicker"], "GhostRunner", []),
        ("clouds as guardians + coraline03 [prod. onda andar]", "ONDA ANDAR", ""):
            ("ONDA ANDAR", ["coraline03"], "clouds as guardians", []),
        ("ШИПЫ + @russianmafiaextra - feel my swag {course if u can} #2016 [prod. swaggaholix]", "ONDA ANDAR", ""):
            ("ШИПЫ", ["russianmafiaextra"], "feel my swag {course if u can}", ["swaggaholix"]),
        ("old summer DEMO ✋ (2025)", "#keyoo", "#keyoo, onda andar"):
            ("#keyoo", ["onda andar"], "old summer DEMO", []),
        ("FIRE", "ONDA ANDAR", "ШИПЫ"): ("ШИПЫ", [], "FIRE", []),
        ("#2016GreenWorld*DAMAGE [prod.narcix]", "ONDA ANDAR", ""):
            ("ONDA ANDAR", [], "#2016GreenWorld*DAMAGE", ["narcix"]),
        ("{#2016} Dont txuch me / 過去に戻って自殺したい[prod.lunico]", "ONDA ANDAR", ""):
            ("ONDA ANDAR", [], "Dont txuch me / 過去に戻って自殺したい", ["lunico"]),
        ("L+R [prod. weikoo]", "ONDA ANDAR", ""): ("ONDA ANDAR", [], "L+R", ["weikoo"]),
        ("AMEN 98 #pharaohseason #2016 [prod ptrckparker, @djcomerz]", "ONDA ANDAR", ""):
            ("ONDA ANDAR", [], "AMEN 98", ["ptrckparker", "djcomerz"]),
        ("moR th3 #STranger + onda andar", "#keyoo", ""): ("#keyoo", ["onda andar"], "moR th3 #STranger", []),
        ("i don't know.mp3", "@extra", ""): ("extra", [], "i don't know.mp3", []),
        ("warzone [prod. onda andar]", "ONDA ANDAR", "ONDA ANDAR #2016"): ("ONDA ANDAR", [], "warzone", []),
    }
    for args, want in cases.items():
        got = parse(*args)
        assert got == want, (args, got)
    assert name_from_file("ONDA ANDAR, onda andar - Интернет человек #2016.m4a",
                          "ONDA ANDAR, onda andar") == ("ONDA ANDAR, onda andar", "Интернет человек #2016")
    assert mangled("\x18=B5@=5B") and not mangled("Интернет")


if __name__ == "__main__":
    selfcheck()
    print("ok")
