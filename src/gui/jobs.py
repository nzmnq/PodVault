"""
Running the tools: one script (or a short chain) at a time, output streamed.

A job runs in a plain thread with subprocess — the same way the text menu
runs the tools — and talks to the window through Qt signals, which Qt
delivers on the window's own thread.
"""

import codecs
import os
import re
import subprocess
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

import settings
from i18n import _
from gui import backend
from gui.theme import UI

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROGRESS = re.compile(r"(\d+)\s*/\s*(\d+)")


class Job(QObject):
    """One tool run. The output is kept the way a terminal shows it: '\\r'
    returns to the start of the line, so the tools' own progress lines
    ('copied 10/50') overwrite themselves instead of piling up."""

    changed = pyqtSignal()
    done = pyqtSignal(int)

    def __init__(self, tool, params, applying, steps):
        super().__init__()
        spec = backend.TOOLS[tool]
        self.tool, self.params, self.applying, self.steps = tool, params, applying, steps
        self.title = (_("{title} — applying") if applying else
                      _("{title} — dry run") if spec.get("apply") else "{title}").format(title=_(spec["title"]))
        self.base_title = _(spec["title"])
        self.cancelable = spec.get("cancel", True) and not (
            applying and spec.get("cancel_apply") is False)
        self.confirm = spec.get("confirm", "")
        self.lines, self.cur, self.pending_cr, self.dropped = [], "", False, 0
        self.code, self.proc, self.cancelled = None, None, False
        self.started, self.finished = time.time(), None
        self.lock = threading.Lock()

    # --- state for the window

    @property
    def running(self):
        return self.code is None

    @property
    def can_apply(self):
        return (bool(backend.TOOLS[self.tool].get("apply")) and not self.applying
                and self.code == 0 and not self.cancelled)

    def snapshot(self):
        """(dropped, lines, current line) — lines[i] is line dropped + i of the output."""
        with self.lock:
            return self.dropped, list(self.lines), ANSI.sub("", self.cur)

    def status(self):
        """The last meaningful line and the progress it shows (0..1 or None)."""
        with self.lock:
            cur = ANSI.sub("", self.cur).strip()
            last = cur or next((ln.strip() for ln in reversed(self.lines) if ln.strip()), "")
        m = PROGRESS.search(last)
        progress = None
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b and a <= b:
                progress = a / b
        return last[:300], progress

    # --- output

    def _feed(self, text):
        with self.lock:
            if self.pending_cr:
                text, self.pending_cr = "\r" + text, False
            i = 0
            while i < len(text):
                c = text[i]
                if c == "\r":
                    if i + 1 == len(text):
                        self.pending_cr = True
                    elif text[i + 1] == "\n":
                        self._newline()
                        i += 1
                    else:
                        self.cur = ""
                elif c == "\n":
                    self._newline()
                else:
                    self.cur += c
                i += 1
        self.changed.emit()

    def _newline(self):
        self.lines.append(ANSI.sub("", self.cur))
        self.cur = ""
        if len(self.lines) > UI["log_lines"]:
            extra = len(self.lines) - UI["log_lines"]
            del self.lines[:extra]
            self.dropped += extra

    def note(self, text):
        self._feed(text + "\n")

    # --- running

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        code = 0
        try:
            for step in self.steps:
                if self.cancelled:
                    break
                shown = " ".join(a if " " not in a else f'"{a}"' for a in step)
                self.note(f"$ {shown}\n")
                self.proc = subprocess.Popen(
                    [backend.python_exe(), os.path.join(backend.SRC, step[0]), *step[1:]],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=settings.ROOT, env=env, creationflags=flags)
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while True:
                    chunk = self.proc.stdout.read1(65536)
                    if not chunk:
                        break
                    self._feed(decoder.decode(chunk))
                self._feed(decoder.decode(b"", final=True))
                code = self.proc.wait()
                if code:
                    break
        except Exception as e:
            self.note("\n" + _("!! Could not run it: {error}").format(error=e))
            code = -1
        if self.cancelled:
            self.note("\n" + _("— cancelled —"))
            code = code or -2
        with self.lock:
            if self.cur:
                self._newline()
            self.code = code
            self.finished = time.time()
        self.changed.emit()
        self.done.emit(code)

    def cancel(self):
        if not self.cancelable or not self.running:
            return False
        self.cancelled = True
        p = self.proc
        if p and p.poll() is None:
            p.terminate()
        return True


class Jobs(QObject):
    """At most one tool runs at a time: two of them writing the iPod or moving
    library folders at once is exactly what must never happen."""

    started = pyqtSignal(object)
    finished = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.current = None

    def busy(self):
        return self.current is not None and self.current.running

    def start(self, tool, params=None, applying=False):
        """Start a tool; raises ValueError (bad input) or RuntimeError (busy)."""
        if self.busy():
            raise RuntimeError(_("“{tool}” is still running.").format(tool=self.current.base_title))
        params = params or {}
        job = Job(tool, params, applying, backend.tool_steps(tool, params, applying))
        job.done.connect(lambda code, j=job: self.finished.emit(j))
        self.current = job
        self.started.emit(job)
        job.start()
        return job
