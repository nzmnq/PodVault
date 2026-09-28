"""
Interface translations: plain JSON files, no build step.

The interface is written in English, and the English text is the key.
A translation is locale/<lang>.json in the project root:

    {
      "_plural": "n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2",
      "Sync the iPod": "Синхронізувати iPod",
      "{n} track": ["{n} трек", "{n} треки", "{n} треків"]
    }

An empty value means "not translated yet": the English text is shown.
Placeholders like {n} stay as they are; their order in the phrase may change.
Plural strings take a list, one form per case of the "_plural" rule (the
formula from the gettext Plural-Forms header; missing = English, n != 1).

The language comes from the `language` setting: English ('en') by default;
a code such as 'uk' picks locale/uk.json, 'auto' follows the system.

In code:
    from i18n import _, n_, N_
    print(_("Sync the iPod"))
    print(_("Copied {n} of {total}").format(n=n, total=total))   # never an f-string
    print(n_("{n} track", "{n} tracks", n).format(n=n))           # plural forms
    TITLE = N_("Settings")      # module level: only marks the string...
    label.setText(_(TITLE))     # ...it's translated where it's shown

Keeping the files up to date (plain Python, any system):
    python src/i18n.py update        # rescan the code: locale/template.json and
                                     # every locale/*.json get the new strings
    python src/i18n.py update uk     # the same, and start locale/uk.json

template.json is every English string the interface has. Translations are
never lost: strings the code no longer uses move to "_obsolete".

Not translated on purpose: tag values, file formats the tools read back,
the prompts sent to the AI, CLI flags.
"""

import ast
import gettext
import json
import locale
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALE_DIR = os.path.join(ROOT, "locale")

# plural rules for languages started with `update <lang>`; others get English's
PLURAL_RULES = {
    "uk": "n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2",
    "ru": "n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2",
    "pl": "n==1 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) ? 1 : 2",
    "de": "n != 1", "fr": "n > 1", "es": "n != 1",
}


class Catalog:
    def __init__(self, data=None):
        self.data = data or {}
        # ponytail: gettext's own parser for the Plural-Forms formula (undocumented, stable since 2.x)
        self.plural = gettext.c2py(self.data.get("_plural") or "n != 1")

    def gettext(self, text):
        v = self.data.get(text)
        return v if isinstance(v, str) and v else text

    def ngettext(self, singular, plural, n):
        forms = self.data.get(singular)
        if isinstance(forms, list):
            i = self.plural(n)
            if i < len(forms) and forms[i]:
                return forms[i]
        return singular if n == 1 else plural


_catalog = None


def _system_languages():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(var):
            return os.environ[var].split(":")
    return [locale.getlocale()[0] or "en"]         # Windows sets none of the above


def catalog():
    """The active translation, picked once per process on first use."""
    global _catalog
    if _catalog is None:
        import settings          # here, not at the top: settings uses _() itself
        lang = str((settings.load() or {}).get("language") or "en").strip()
        wanted = _system_languages() if lang.lower() == "auto" else [lang]
        _catalog = Catalog()
        for code in wanted:
            code = code.split(".")[0]                       # uk_UA.UTF-8 -> uk_UA
            for name in (code, code.split("_")[0]):         # uk_UA, then uk
                path = os.path.join(LOCALE_DIR, name + ".json")
                if not os.path.isfile(path):
                    continue
                try:
                    with open(path, encoding="utf-8") as f:
                        _catalog = Catalog(json.load(f))
                except (OSError, ValueError, SyntaxError) as e:
                    # a broken file must not take the whole program down: English it is
                    print(f"! {path} not loaded: {e}", file=sys.stderr)
                return _catalog
    return _catalog


def _(text):
    return catalog().gettext(text) if text else text


def n_(singular, plural, n):
    """The form for n: n_("{n} track", "{n} tracks", n).format(n=n)."""
    return catalog().ngettext(singular, plural, n)


def N_(text):
    """Marks a string for extraction without translating it yet."""
    return text


# ------------------------------------------------------------------ update

# which arguments of which call are interface text
_SINGLE = {"_": (0,), "N_": (0,), "Field": (3, 4, 5)}      # Field: label, help, section
_PLURAL = {"n_": (0, 1), "plural": (1, 2)}               # (singular, plural)


def extract():
    """{english: None (a plain string) or its plural form}, in the order they appear in the code."""
    files = [os.path.join(ROOT, "Main.py")]
    for folder, _dirs, names in os.walk(os.path.join(ROOT, "src")):
        files += [os.path.join(folder, n) for n in sorted(names) if n.endswith(".py")]
    found = {}
    for path in files:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        calls = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call)),
                       key=lambda n: (n.lineno, n.col_offset))
        for node in calls:
            name = getattr(node.func, "id", None)
            args = [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
                    for a in node.args]
            for i in _SINGLE.get(name, ()):
                if i < len(args) and args[i]:
                    found.setdefault(args[i], None)
            if name in _PLURAL:
                one, many = _PLURAL[name]
                if many < len(args) and args[one] and args[many]:
                    found.setdefault(args[one], args[many])
    return found


def update(new_langs=()):
    strings = extract()
    os.makedirs(LOCALE_DIR, exist_ok=True)

    def write(path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    # every English string; a plural one shows both English forms
    write(os.path.join(LOCALE_DIR, "template.json"),
          {k: ([k, v] if v else k) for k, v in strings.items()})
    print(f"template.json: {len(strings)} strings")

    for code in new_langs:
        path = os.path.join(LOCALE_DIR, code + ".json")
        if not os.path.exists(path):
            write(path, {"_plural": PLURAL_RULES.get(code.split("_")[0], "n != 1")})

    for fn in sorted(os.listdir(LOCALE_DIR)):
        if not fn.endswith(".json") or fn == "template.json":
            continue
        path = os.path.join(LOCALE_DIR, fn)
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
        obsolete = dict(old.get("_obsolete") or {})
        out = {"_plural": old.get("_plural") or "n != 1"}
        for k, plural in strings.items():
            v = old.get(k, obsolete.pop(k, None))
            out[k] = v if v not in (None, "", []) else ([] if plural else "")
        obsolete.update({k: v for k, v in old.items()
                         if not k.startswith("_") and k not in strings and v not in ("", [])})
        if obsolete:
            out["_obsolete"] = obsolete
        write(path, out)
        done = sum(1 for k in strings if out[k] not in ("", []))
        print(f"{fn}: {done}/{len(strings)} translated"
              + (f", {len(obsolete)} obsolete kept" if obsolete else ""))


if __name__ == "__main__":
    if sys.argv[1:2] != ["update"]:
        sys.exit("usage: python src/i18n.py update [new language code...]")
    update(sys.argv[2:])
