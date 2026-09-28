"""Smoke checks that run anywhere without a library or an iPod.

    python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
# a throwaway settings file: nothing here may touch the real one
os.environ["MUSIC_UTILITY_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "settings.json")

from gui import backend  # noqa: E402  (plain Python: no Qt imported)


class ToolArgs(unittest.TestCase):
    def test_tools_that_read_a_field(self):
        # _text was once shadowed by a string: every such tool failed
        self.assertEqual(backend.tool_steps("vibe", {"vibe": "rain", "count": "10"}, False),
                         [["vibe.py", "rain", "--count", "10"]])
        self.assertEqual(backend.tool_steps("disk", {"drive": "/Volumes/X"}, False),
                         [["ipod_sync.py", "--disk", "/Volumes/X"]])
        self.assertEqual(backend.tool_steps("disk", {"drive": "E:"}, False),
                         [["ipod_sync.py", "--disk", "E:"]])

    def test_empty_field_is_a_message_not_a_crash(self):
        with self.assertRaises(ValueError):
            backend.tool_steps("vibe", {}, False)
        with self.assertRaises(ValueError):
            backend.tool_steps("disk", {"drive": "nonsense"}, False)


class DiskMirror(unittest.TestCase):
    def test_missing_device_is_refused(self):
        import ipod_sync
        missing = os.path.join(tempfile.mkdtemp(), "not-mounted")
        with self.assertRaises(SystemExit) as e:
            ipod_sync.sync_disk(missing, False, "Music")
        self.assertIn("Drive not available", str(e.exception))
        self.assertFalse(os.path.exists(missing))


class Compilations(unittest.TestCase):
    def test_compilation_artist_comes_from_the_setting(self):
        import build_clean
        build_clean.VARIOUS = "Various Artists"
        for names in ({"Various Artists", "X"}, {"Разные исполнители", "X"}, {"Abba", "Queen"}):
            self.assertEqual(build_clean.resolve_conflict(names), ("Various Artists", "compilation"))


class Translations(unittest.TestCase):
    """Every string goes through _(): a placeholder that doesn't match its .format() is a
    KeyError at run time, in a message nobody may see until it's needed."""

    @staticmethod
    def fields(s):
        import string
        return {f for _lit, f, _spec, _conv in string.Formatter().parse(s) if f}

    def test_placeholders_match_format_arguments(self):
        import ast
        import glob
        bad = []
        files = [os.path.join(ROOT, "Main.py")] + glob.glob(os.path.join(ROOT, "src", "**", "*.py"),
                                                            recursive=True)
        for path in files:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                fn = getattr(node, "func", None)
                if not (isinstance(fn, ast.Attribute) and fn.attr == "format"
                        and getattr(getattr(fn.value, "func", None), "id", None) in ("_", "n_")):
                    continue
                if any(k.arg is None for k in node.keywords):
                    continue                                   # .format(**meta)
                given = {k.arg for k in node.keywords}
                for a in fn.value.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                            and self.fields(a.value) != given:
                        bad.append(f"{os.path.relpath(path, ROOT)}:{node.lineno} {a.value[:50]!r}")
        self.assertEqual(bad, [])

    def test_english_without_a_catalog(self):
        import i18n
        self.assertEqual(i18n._("Sync the iPod"), "Sync the iPod")
        self.assertEqual(i18n.n_("{n} track", "{n} tracks", 2).format(n=2), "2 tracks")

    def test_json_catalog_and_plural_forms(self):
        import i18n
        c = i18n.Catalog({"_plural": i18n.PLURAL_RULES["uk"], "Save": "Зберегти", "Empty": "",
                          "{n} track": ["{n} трек", "{n} треки", "{n} треків"]})
        self.assertEqual(c.gettext("Save"), "Зберегти")
        self.assertEqual(c.gettext("Empty"), "Empty")          # not translated yet: English
        self.assertEqual([c.ngettext("{n} track", "{n} tracks", n).format(n=n) for n in (1, 3, 5, 21)],
                         ["1 трек", "3 треки", "5 треків", "21 трек"])

    def test_extract_finds_settings_and_plurals(self):
        import i18n
        found = i18n.extract()
        self.assertIn("Interface language", found)             # a Field label
        self.assertIn("Audio tools", found)                    # a Field section
        self.assertEqual(found.get("{n} track"), "{n} tracks")  # plural() / n_()


if __name__ == "__main__":
    unittest.main()
