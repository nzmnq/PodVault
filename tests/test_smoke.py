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


if __name__ == "__main__":
    unittest.main()
