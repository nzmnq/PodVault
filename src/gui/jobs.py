"""
Running the tools: one script (or a short chain) at a time, output streamed.

A job runs in a plain thread with subprocess — the same way the command line
runs the tools — and talks to the window through Qt signals, which Qt
delivers on the window's own thread.
"""

import codecs
import json
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
        self.reason, self.was_nothing = "", False     # set when it ends (_finish)
        self.started, self.finished = time.time(), None
        self.lock = threading.Lock()
        self.quick = False          # set by Jobs: an --apply right after its own dry run
        self.expected = None        # seconds the last such run took, for the bar (Jobs)
        self.segment = None         # (time, progress) where the current n/total count began

    # --- state for the window

    @property
    def running(self):
        return self.code is None

    @property
    def ok(self):
        """It did its work — maybe with problems along the way (settings.run)."""
        return self.code in (0, settings.PROBLEMS_EXIT) and not self.cancelled

    @property
    def problems(self):
        return self.code == settings.PROBLEMS_EXIT

    @property
    def can_apply(self):
        return (bool(backend.TOOLS[self.tool].get("apply")) and not self.applying
                and self.ok and not self.nothing)

    @property
    def nothing(self):
        """The dry run found nothing to change (settings.nothing_to_apply): its last line
        before the closing one _finish() adds."""
        return self.was_nothing

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
        progress, left = self._estimate(progress)
        if left is not None:
            last = last[:250] + "  · " + (_("≈ {time} left").format(time=_clock(left)) if left >= 1
                                         else _("taking longer than last time"))
        return last[:300], progress

    def _estimate(self, progress):
        """(progress, seconds left or None). A tool's own 'n/total': the time left from how
        fast this count is going. Without one: how long the same run took last time —
        a rough bar (never past 95 %) beats one that just runs back and forth."""
        now = time.time()
        if progress is not None:
            if self.segment is None or progress < self.segment[1]:
                self.segment = (now, progress)          # a new count (the next phase) began
            t0, p0 = self.segment
            if progress > p0 and now - t0 > 2:
                return progress, (now - t0) * (1 - progress) / (progress - p0)
            return progress, None
        if self.expected:
            spent = now - self.started
            return min(0.95, spent / self.expected), self.expected - spent
        return None, None

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
        env["PODVAULT_APPLY"] = "1" if self.applying else "0"
        if self.quick:
            env["PODVAULT_SCANNED"] = "1"   # library.listing: the dry run has just read the library
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
                    cwd=settings.DATA, env=env, creationflags=flags)
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while True:
                    chunk = self.proc.stdout.read1(65536)
                    if not chunk:
                        break
                    self._feed(decoder.decode(chunk))
                self._feed(decoder.decode(b"", final=True))
                step_code = self.proc.wait()
                if step_code not in (0, settings.PROBLEMS_EXIT):
                    code = step_code
                    break
                code = code or step_code        # problems in one step: the run has them too
        except Exception as e:
            self.note("\n" + _("!! Could not run it: {error}").format(error=e))
            code = -1
        if self.cancelled:
            code = code or -2
        self._finish(code)

    def _finish(self, code):
        """Every run ends with a line saying how it ended, and why when it failed: the tools
        were written for a terminal, where the prompt coming back said it; in the window
        the output just stopped, done and crashed alike."""
        with self.lock:
            if self.cur:
                self._newline()
            said = [ln.strip() for ln in self.lines if ln.strip() and not ln.startswith("$ ")]
        last = said[-1] if said else ""
        self.was_nothing = code == 0 and last == _(settings.NOTHING_TO_APPLY)
        seconds = round(time.time() - self.started)
        if self.cancelled:
            self.reason = _("cancelled")
            self.note("\n" + _("— cancelled after {s} s —").format(s=seconds))
        elif code == 0:
            self.note("\n" + _("OK — finished in {s} s").format(s=seconds))
        elif code == settings.PROBLEMS_EXIT:
            self.reason = last          # settings.run's "Finished, but N things went wrong"
            self.note("\n" + _("!! Finished with problems in {s} s — see the lines marked !").format(s=seconds))
        else:
            # what the tool said last is the reason: sys.exit's message, a traceback's last line
            self.reason = last or _("no message")
            self.note("\n" + _("!! Didn't finish (exit code {code}): {reason}").format(
                code=code, reason=self.reason))
        with self.lock:
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
        self.last = None            # the run before: its dry run makes an --apply quick
        self.times_file = os.path.join(settings.DATA, "cache", "job_times.json")
        try:
            with open(self.times_file, encoding="utf-8") as f:
                self.times = json.load(f)
        except (OSError, ValueError):
            self.times = {}

    def busy(self):
        return self.current is not None and self.current.running

    def start(self, tool, params=None, applying=False):
        """Start a tool; raises ValueError (bad input) or RuntimeError (busy)."""
        if self.busy():
            raise RuntimeError(_("“{tool}” is still running.").format(tool=self.current.base_title))
        params = params or {}
        job = Job(tool, params, applying, backend.tool_steps(tool, params, applying))
        prev = self.last
        job.quick = bool(applying and prev and prev.tool == tool and prev.params == params
                         and not prev.applying and prev.ok and time.time() - prev.finished < 1800)
        job.expected = self.times.get(self._key(job))
        job.done.connect(lambda code, j=job: self._ended(j))
        self.current = job
        self.started.emit(job)
        job.start()
        return job

    @staticmethod
    def _key(job):
        return f"{job.tool}:{'apply' if job.applying else 'dry'}{':quick' if job.quick else ''}"

    def _ended(self, job):
        self.last = job
        if job.ok:
            self.times[self._key(job)] = round(job.finished - job.started, 1)
            try:
                os.makedirs(os.path.dirname(self.times_file), exist_ok=True)
                with open(self.times_file, "w", encoding="utf-8") as f:
                    json.dump(self.times, f)
            except OSError:
                pass
        self.finished.emit(job)


def _clock(seconds):
    """93 -> '1:33'."""
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"
