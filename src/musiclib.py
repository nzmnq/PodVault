"""
Shared title-parsing helpers. They live in one place because the build,
the missing-album search and the cover search all need them — and
diverging copies of the same regex already caused one silent bug.
"""

import re
import unicodedata

# Words that mark an edition. On its own such a word means nothing: what
# matters is that it sits inside a bracket group or in the title's tail.
# The Cyrillic entries are data, not text: they match Russian/Ukrainian titles.
_EDITION_WORDS = (
    r"deluxe|delux|bonus|expanded|special|remaster(?:ed)?|anniversary|"
    r"complete|extended|intl|international|digital|edition|version|reissue|"
    r"полная\s+версия|переиздание|ремастер"
)

# A bracket group with such a word ANYWHERE inside is removed entirely.
# The key part is '[^)\]]*' before the word: without it '(2009 Remastered
# Version)' is cut only from 'Remastered' on, the opening bracket and '2009'
# stay behind, and 'Vol. 4' turns into the stump 'Vol. 4 (2009'.
_EDITION_BRACKET = re.compile(
    rf"\s*[\(\[][^\)\]]*\b(?:{_EDITION_WORDS})\b[^\)\]]*[\)\]]",
    re.I,
)

# A tail after a dash: 'Nevermind - Remastered 2011'
_EDITION_TAIL = re.compile(
    rf"\s*[-–—]\s*[^-–—]*\b(?:{_EDITION_WORDS})\b.*$",
    re.I,
)

# 'при уч.' is the Russian 'feat.'
_FEAT = re.compile(
    r"\s*[\(\[]\s*(?:feat|ft|featuring|with|при\s+уч)[^\)\]]*[\)\]]",
    re.I,
)


def norm(s):
    """Comparison key: letters and digits only, lower case.

    Absorbs differences in punctuation, case, ё/е, and the filesystem
    replacing forbidden characters with '_'.

    The string is composed (NFC) first. 'й' can be stored as one code point
    or as 'и' + a combining breve; some files use the second form, while
    iTunes keeps the first. Without composing, the breve was stripped as
    punctuation, 'знайде' became 'знаиде', the iPod copy never matched, and
    every sync copied such tracks onto the iPod again.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.lower().replace("ё", "е").replace("’", "'").replace("`", "'")
    return re.sub(r"[^0-9a-zа-яїієґ]+", "", s)


def strip_edition(s):
    """Title without its edition marker.

    'Vol. 4 (2009 Remastered Version)' -> 'Vol. 4'
    'Meteora (Bonus Edition)'          -> 'Meteora'
    'Nevermind - Remastered 2011'      -> 'Nevermind'

    If nothing is left after stripping, the original string is returned:
    an album may have no other title than 'Deluxe'.
    """
    if not s:
        return s
    out = _EDITION_TAIL.sub("", _EDITION_BRACKET.sub(" ", s))
    out = re.sub(r"\s{2,}", " ", out).strip(" -–—_")
    return out or s


def strip_feat(s):
    """Title without '(feat. ...)'."""
    return _FEAT.sub("", s or "").strip()


def first_artist(s):
    """First name out of a comma-glued list of co-artists.

    'prodslam, subbtrahiert, Denizlpsevv' -> 'prodslam'
    Careful: a comma can be part of a real name ('125, Rue Montmartre'),
    so use this only as a fallback, never as the primary split.
    """
    return re.split(r"\s*[,;]\s*", s or "")[0].strip()
