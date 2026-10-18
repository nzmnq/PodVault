"""
Undo the last batch of tag changes: what "Add new tracks", "Split glued artists"
or "Genres — write the tags" changed, put back.

While such a tool writes, it keeps a journal (<reports>/undo/<time>-<tool>.json):
the old value of every tag field it changed, every file it moved, every file it
added. Undoing goes through it backwards: files go back where they were, tags
get their old values, and the files an import added are moved out to
<reports>/undone/ — not deleted, in case the originals are gone by now.

  python src/undo_tags.py            # what the last batch changed
  python src/undo_tags.py --apply    # put it back
"""

import argparse
import glob
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings
import tags as audiotags
from i18n import _


class Journal:
    """What one run changes, saved as it goes (an interrupted run is still undoable)."""

    def __init__(self, cfg, tool):
        folder = os.path.join(settings.path("reports_dir", cfg), "undo")
        os.makedirs(folder, exist_ok=True)
        self.path = os.path.join(folder, f"{time.strftime('%Y%m%d-%H%M%S')}-{tool}.json")
        self.tool, self.entries, self.seen, self.unsaved = tool, [], set(), 0

    def tags(self, path, old):
        """Before the first write to a file: the old values of the fields about to change."""
        key = os.path.normcase(os.path.abspath(path))
        if key in self.seen:
            return
        self.seen.add(key)
        self._add({"do": "tags", "path": os.path.abspath(path), "old": old})

    def moved(self, src, dest):
        self.seen.discard(os.path.normcase(os.path.abspath(src)))
        self._add({"do": "move", "from": os.path.abspath(src), "to": os.path.abspath(dest)})

    def added(self, path):
        self.seen.add(os.path.normcase(os.path.abspath(path)))     # a new file's tags aren't undone
        self._add({"do": "add", "path": os.path.abspath(path)})

    def _add(self, entry):
        self.entries.append(entry)
        self.unsaved += 1
        if self.unsaved >= 25:
            self.save()

    def save(self):
        if not self.entries:
            return
        with open(self.path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"tool": self.tool, "made": time.time(), "entries": self.entries}, f,
                      ensure_ascii=False)
        os.replace(self.path + ".tmp", self.path)
        self.unsaved = 0


def start(cfg, tool):
    """Journal every tag write, move and new file of this process from here on (tags.JOURNAL)."""
    import atexit
    j = Journal(cfg, tool)
    audiotags.JOURNAL = j
    atexit.register(j.save)
    return j


def last_journal(cfg):
    found = sorted(glob.glob(os.path.join(settings.path("reports_dir", cfg), "undo", "*.json")))
    return found[-1] if found else None


def undo(path, apply_changes, cfg):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data["entries"]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(data["made"]))
    print(_("Last batch: {tool}, {when} — {n} changes").format(tool=data["tool"], when=when, n=len(entries)))
    counts = {k: sum(1 for e in entries if e["do"] == k) for k in ("tags", "move", "add")}
    print("  " + _("tags put back: {tags}, files moved back: {move}, added files set aside: {add}").format(**counts))
    if not apply_changes:
        print(_("Nothing changed. Run with --apply to undo it."))
        return
    aside = os.path.join(settings.path("reports_dir", cfg), "undone", os.path.basename(path)[:-5])
    left = set()
    for e in reversed(entries):
        try:
            if e["do"] == "move":
                if os.path.exists(e["to"]) and not os.path.exists(e["from"]):
                    os.makedirs(os.path.dirname(e["from"]), exist_ok=True)
                    shutil.move(e["to"], e["from"])
                    left.add(os.path.dirname(e["to"]))
            elif e["do"] == "tags":
                if os.path.exists(e["path"]):
                    audiotags.write(e["path"], e["old"])
            elif e["do"] == "genres_file":          # the whole file as it was before
                with open(e["file"], "w", encoding="utf-8") as f:
                    f.write(e["before"])
            elif e["do"] == "add" and os.path.exists(e["path"]):
                os.makedirs(aside, exist_ok=True)
                # named after its artist and album too: every album brings its own folder.jpg
                d = os.path.dirname(e["path"])
                name = f"{os.path.basename(os.path.dirname(d))} - {os.path.basename(d)} - {os.path.basename(e['path'])}"
                shutil.move(e["path"], os.path.join(aside, name))
                left.add(os.path.dirname(e["path"]))
        except (OSError, audiotags.Unreadable) as err:
            settings.problem(f"{e.get('path') or e.get('to')}: {err}")
    for d in sorted(left, key=len, reverse=True):       # folders the batch left empty
        for folder in (d, os.path.dirname(d)):
            try:
                os.rmdir(folder)
            except OSError:
                break
    os.replace(path, path[:-5] + ".undone")
    print(_("Undone. Files the batch had added are in {folder}").format(folder=aside)
          if counts["add"] else _("Undone."))


def main():
    ap = argparse.ArgumentParser(description=_("undo the last batch of tag changes"))
    ap.add_argument("--apply", action="store_true", help=_("put it back"))
    args = ap.parse_args()
    args.apply = settings.apply_requested(args.apply)
    cfg = settings.require()
    path = last_journal(cfg)
    if not path:
        settings.nothing_to_apply()
        return
    undo(path, args.apply, cfg)


if __name__ == "__main__":
    settings.run(main)
