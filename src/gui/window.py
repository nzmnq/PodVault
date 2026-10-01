"""The main window: toolbar with the LCD, the source list (places only), pages,
the status bar with the Active summary, the ☰ menu with the tools, the task sheet."""

import faulthandler
import os
import sys
import time
import traceback

from PyQt6.QtCore import (QEvent, QFileSystemWatcher, QRect, QRectF, QSize, Qt, QTimer, QUrl,
                          pyqtSignal)
from PyQt6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QShortcut
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                             QStackedWidget, QStyle, QStyledItemDelegate, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

import settings
import tags as audiotags
from gui import backend, theme
import i18n
from i18n import N_, _
from gui.jobs import Jobs
from gui.pages import (IpodPage, LibraryPage, PlaylistPage, SettingsPage, ToolDialog, VibePage)
from gui.theme import C, GLYPHS, UI, icon
from gui.widgets import Lcd, ask, button, covers, fmt_gb, fmt_time, inform, plural, run_async

KeyRole = Qt.ItemDataRole.UserRole + 10
AddRole = Qt.ItemDataRole.UserRole + 11      # a section heading with a "+" on the right


def app_icon():
    """The same picture the installers put in the menu, the Dock and the Start menu."""
    return QIcon(os.path.join(settings.ROOT, "packaging", "podvault.svg"))


# ------------------------------------------------------------------ task sheet


class JobSheet(QDialog):
    """The output of a running tool, and the button that applies a dry run."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.job = None
        self.shown_upto = 0
        self.setWindowTitle(_("Task"))
        self.setModal(True)
        self.resize(*UI["sheet"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("h2")
        self.status = QLabel()
        self.status.setProperty("muted", True)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.confirm = QLabel()
        self.confirm.setWordWrap(True)
        self.confirm.setObjectName("confirm")
        foot = QHBoxLayout()
        self.follow = QHBoxLayout()
        self.cancel_btn = button(_("Stop"))
        self.cancel_btn.clicked.connect(self._cancel)
        self.hide_btn = button(_("Hide"), tip=_("Keep it running; the LCD shows its progress"))
        self.hide_btn.clicked.connect(self.hide)
        self.apply_btn = button(_("Apply"), primary=True)
        self.apply_btn.clicked.connect(self._apply)
        self.close_btn = button(_("Close"))
        self.close_btn.clicked.connect(self.hide)
        foot.addLayout(self.follow)
        foot.addStretch(1)
        for b in (self.cancel_btn, self.hide_btn, self.close_btn, self.apply_btn):
            foot.addWidget(b)
        for w in (self.title, self.status, self.bar, self.log, self.confirm):
            lay.addWidget(w)
        lay.addLayout(foot)
        self.timer = QTimer(self)
        self.timer.setInterval(UI["sheet_refresh_ms"])
        self.timer.timeout.connect(self.refresh)

    def attach(self, job):
        self.job = job
        self.shown_upto = 0
        self.log.clear()
        self.title.setText(job.title)
        self.setWindowTitle(job.title)
        self._clear_follow()
        self.refresh()
        self.timer.start()

    def open_for(self, job):
        if job is not self.job:
            self.attach(job)
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def _clear_follow(self):
        while self.follow.count():
            w = self.follow.takeAt(0).widget()
            if w:
                w.deleteLater()

    def add_follow(self, text, fn, primary=False):
        b = button(text, primary=primary)
        b.clicked.connect(lambda: (self.hide(), fn()))
        self.follow.addWidget(b)

    def refresh(self):
        job = self.job
        if job is None:
            return
        dropped, lines, cur = job.snapshot()
        start = self.shown_upto - dropped
        if start < 0:                 # old lines were dropped: redraw what's kept
            self.log.setPlainText("\n".join(lines))
        elif start < len(lines):
            sb = self.log.verticalScrollBar()
            at_end = sb.value() >= sb.maximum() - 4
            self.log.appendPlainText("\n".join(lines[start:]))
            if at_end:
                sb.setValue(sb.maximum())
        self.shown_upto = dropped + len(lines)
        status, progress = job.status()
        running = job.running
        if running:
            self.status.setText(status or _("working…"))         # status: the last line + time left
            if progress is None:
                self.bar.setRange(0, 0)
            else:
                self.bar.setRange(0, 1000)
                self.bar.setValue(int(progress * 1000))
            self.bar.setProperty("state", "")
        else:
            self.timer.stop()
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            ok = job.ok and not job.problems
            self.bar.setProperty("state", "ok" if ok else "bad")
            took = (job.finished or time.time()) - job.started
            self.status.setText((_("Done — nothing to change") if ok and job.nothing else
                                 _("Done") if ok else
                                 _("Done, with problems") + ": " + job.reason if job.problems else
                                 _("Cancelled") if job.cancelled else
                                 _("Stopped with an error (exit code {code})").format(code=job.code)
                                 + (": " + job.reason if job.reason else ""))
                                + " · " + _("{s} s").format(s=f"{took:.0f}"))
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)
        self.cancel_btn.setVisible(running and job.cancelable)
        self.hide_btn.setVisible(running)
        self.close_btn.setVisible(not running)
        self.apply_btn.setVisible(job.can_apply)
        self.confirm.setVisible(job.can_apply)
        if job.can_apply:
            self.confirm.setText(_(job.confirm) if job.confirm else _("Apply these changes?"))
            self.apply_btn.setDefault(True)

    def _cancel(self):
        if self.job and ask(self, _("Stop"), _("Stop “{tool}”?").format(tool=self.job.base_title),
                            yes=_("Stop"), danger=True):
            self.job.cancel()

    def _apply(self):
        job = self.job
        if job and job.can_apply:
            self.win.run_tool(job.tool, job.params, applying=True)

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ------------------------------------------------------------------ source list


class SideDelegate(QStyledItemDelegate):
    """Draws the iPod row (its name, a small capacity bar and an eject button)
    and the "+" of the Playlists heading."""

    eject = pyqtSignal()
    add = pyqtSignal()

    def __init__(self, win):
        super().__init__(win.sidebar)
        self.win = win

    def _eject_rect(self, rect):
        size = UI["side_eject"]
        return QRect(rect.right() - size - UI["side_pad"], rect.top() + (rect.height() - size) // 2,
                     size, size)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        key = index.data(KeyRole)
        if key == "ipod":
            return QSize(hint.width(), UI["side_device_row"])
        if key:
            return QSize(hint.width(), UI["side_row"])
        return QSize(hint.width(), UI["side_head"] if index.data() else UI["side_gap"])

    def paint(self, p, option, index):
        super().paint(p, option, index)
        if index.data(AddRole):
            p.save()
            f = QFont(option.font)
            f.setPixelSize(UI["side_eject"] - 2)
            p.setFont(f)
            p.setPen(QColor(C["side_text"]))
            p.drawText(self._eject_rect(option.rect), Qt.AlignmentFlag.AlignCenter, "+")
            p.restore()
            return
        if index.data(KeyRole) != "ipod":
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        fg = QColor(C["on_accent"] if selected else C["side_text"])
        rect = option.rect
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(theme.symbol_font(UI["side_eject"] - UI["side_pad"]))
        p.setPen(fg)
        p.drawText(self._eject_rect(rect), Qt.AlignmentFlag.AlignCenter, GLYPHS["eject"])
        info = self.win.ipod_info
        if info and info.get("capacity_gb"):
            used = 1 - info["free_gb"] / info["capacity_gb"]
            left = rect.left() + UI["icon"] + 3 * UI["side_pad"]
            width = self._eject_rect(rect).left() - UI["side_pad"] - left
            bar = QRectF(left, rect.bottom() - UI["side_pad"] - UI["side_capacity_h"],
                         width, UI["side_capacity_h"])
            track = QColor(fg)
            track.setAlpha(QColor(C["lcd_track"]).alpha())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(bar, 2, 2)
            p.setBrush(fg if selected else QColor(C["accent"]))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * max(0, min(used, 1)),
                                     bar.height()), 2, 2)
        p.restore()

    def editorEvent(self, event, model, option, index):
        if index.data(AddRole) and event.type() == QEvent.Type.MouseButtonRelease \
                and self._eject_rect(option.rect).contains(event.position().toPoint()):
            self.add.emit()
            return True
        if index.data(KeyRole) == "ipod" and event.type() == QEvent.Type.MouseButtonRelease \
                and self._eject_rect(option.rect).contains(event.position().toPoint()):
            self.eject.emit()
            return True
        return super().editorEvent(event, model, option, index)


# ------------------------------------------------------------------ the window


# the ☰ menu: a string is a tool, None a separator, a 1-tuple a section heading
MENU = [(N_("Library"),), "covers", "tags", "duplicates", "artists", "likes",
        (N_("iPod"),), "fit", "soundcheck", "smart",
        (N_("Active / Archive as a text list"),), "export", "marks", "undo",
        (N_("Audio"),), "flac", None,
        "build", "shortcuts", "settings"]
MENU_EXTRA = {"export": N_("Export the list…"), "settings": N_("Settings…"),
              "shortcuts": N_("Keyboard shortcuts")}
SHORTCUTS = [("Ctrl+F", N_("Search")), ("Ctrl+1 / 2 / 3", N_("Albums / Artists / Genres")),
             ("A / R", N_("Selected albums → Active / → Archive")), ("Space", N_("Switch Active ⇄ Archive")),
             ("Enter / Esc", N_("Open / close an album")), ("Ctrl+N", N_("Add new tracks")),
             ("Ctrl+S", N_("Sync the iPod")), ("Ctrl+E", N_("Eject the iPod")),
             ("Ctrl+R", N_("Read the library again")), ("Ctrl+,", N_("Settings"))]


class MainWindow(QMainWindow):
    def __init__(self, key=None):
        super().__init__()
        self.language = (backend.cfg() or {}).get("language")   # the one the texts were built in
        self.setWindowTitle(_("PodVault"))
        self.setWindowIcon(app_icon())
        self.resize(*UI["window"])
        self.setMinimumSize(*UI["window_min"])
        self.setAcceptDrops(True)
        self.jobs = Jobs()
        self.jobs.started.connect(self._job_started)
        self.jobs.finished.connect(self._job_finished)
        self.albums, self.albums_error, self.scanning = None, None, False
        self.scan_progress = None     # (done, total, seconds left), written by the scan's thread
        self.rescan = False
        self.ipods, self.ipod_info, self.polling = [], None, False
        self.identifying = False    # IpodPage._identify: the iPod drops off on purpose
        self.playlists, self.genres_missing = [], 0
        self.current_key = None
        self.lcd_note, self.lcd_note_until = None, 0
        self.relabel_after_job, self.replaced = None, False    # relabel_after_job: the toast to show then
        # the b1air desktop's theme: a new one picked in its Settings recolours the window
        self.b1air = theme.b1air_palette()
        self.theme_watch = QFileSystemWatcher(self)
        if os.path.isdir(os.path.dirname(theme.B1AIR_THEME)):
            self.theme_watch.addPath(os.path.dirname(theme.B1AIR_THEME))   # the file is replaced, not edited
        self.theme_watch.directoryChanged.connect(lambda _p: QTimer.singleShot(200, self._b1air_changed))

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._toolbar())
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(1)
        self.sidebar = QTreeWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setIndentation(0)
        self.sidebar.setRootIsDecorated(False)
        self.sidebar.setIconSize(QSize(UI["icon"], UI["icon"]))
        self.sidebar.setMinimumWidth(UI["side_min"])
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._side_menu)
        self.sidebar.currentItemChanged.connect(self._side_changed)
        self.side_delegate = SideDelegate(self)
        self.side_delegate.eject.connect(self.eject)
        self.side_delegate.add.connect(lambda: self.navigate("vibe"))
        self.sidebar.setItemDelegate(self.side_delegate)
        self.stack = QStackedWidget()
        # the source list, and Settings pinned under it
        side = QWidget()
        side.setObjectName("sideBox")
        side.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(0, 0, 0, 0)
        side_lay.setSpacing(0)
        side_lay.addWidget(self.sidebar, 1)
        self.settings_btn = QPushButton(_("Settings"))
        self.settings_btn.setObjectName("sideSettings")
        self.settings_btn.setCheckable(True)
        self.settings_btn.setToolTip(_("Settings (Ctrl+,)"))
        self.settings_btn.clicked.connect(lambda: (self.navigate("settings"), self.settings_btn.setChecked(
            self.current_key == "settings")))
        side_lay.addWidget(self.settings_btn)
        split.addWidget(side)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([UI["side_w"], UI["window"][0] - UI["side_w"]])
        root.addWidget(split, 1)
        root.addWidget(self._statusbar())
        self.setCentralWidget(central)
        self.drop_hint = QLabel(_("Drop to add these tracks to the library"), self)
        self.drop_hint.setObjectName("dropHint")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drop_hint.hide()

        self.library_page = LibraryPage(self)
        self.library_page.marks_changed.connect(self._marks_changed)
        self.library_page.detail.play.connect(self.play)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(QAudioOutput(self))
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(lambda err, msg: self.note(_("Can't play this track"), msg))
        self.queue, self.queue_at, self.resume_at = [], 0, 0
        self.revert_btn = self.library_page.revert_btn
        self.save_marks_btn = self.library_page.save_marks_btn
        self.save_marks_btn.clicked.connect(self.apply_marks)
        self.ipod_page = IpodPage(self)
        self.playlist_page = PlaylistPage(self)
        self.settings_page = SettingsPage(self)
        self.settings_page.saved.connect(self._settings_saved)
        self.vibe_page = VibePage(self)
        for p in (self.library_page, self.ipod_page, self.playlist_page, self.settings_page,
                  self.vibe_page):
            self.stack.addWidget(p)

        self.sheet = JobSheet(self)
        self._shortcuts()
        self.poll = QTimer(self)
        self.poll.setInterval(UI["poll_ms"])
        self.poll.timeout.connect(self._poll_ipod)
        self.poll.start()
        self.lcd_timer = QTimer(self)
        self.lcd_timer.setInterval(UI["lcd_refresh_ms"])
        self.lcd_timer.timeout.connect(self.update_lcd)
        self.lcd_timer.start()

        self.build_sidebar()
        self._busy_buttons()
        if backend.cfg() is None:
            self.navigate("settings")
        else:
            self.reload_library(quick=True)      # at start: only what came and went (library.scan)
            self.reload_playlists()
            self.navigate(key if key in ("settings", "ipod", "vibe") else "library")
        self._poll_ipod()

    @property
    def genres_page(self):
        return self.library_page.genres

    # --- chrome

    def _toolbar(self):
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(UI["toolbar_h"])
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)
        self.tb_sync = button(_("Sync"), tip=_("Sync the iPod with Active (Ctrl+S)"))
        self.tb_add = button(_("Add"), tip=_("Add new tracks — or drop a folder onto the window (Ctrl+N)"))
        for b, name in ((self.tb_sync, "sync"), (self.tb_add, "incoming")):
            b.setObjectName("tb")
            b.setIcon(icon(name))
        self.tb_sync.clicked.connect(lambda: self.run_tool("sync"))
        self.tb_add.clicked.connect(lambda: self.open_tool("incoming"))
        left = QHBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.tb_sync)
        left.addWidget(self.tb_add)
        left.addStretch(1)
        self.lcd = Lcd()
        self.lcd.clicked.connect(self._lcd_clicked)
        self.lcd.seek.connect(self._lcd_seek)
        self.tb_prev = button(GLYPHS["prev"], tip=_("Previous track"))
        self.tb_next = button(GLYPHS["next"], tip=_("Next track"))
        for b, step in ((self.tb_prev, -1), (self.tb_next, 1)):
            b.setObjectName("tb")
            b.clicked.connect(lambda _c, s=step: self.skip(s))
            sp = b.sizePolicy()
            sp.setRetainSizeWhenHidden(True)      # the display doesn't jump when they show up
            b.setSizePolicy(sp)
            b.hide()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText(_("Search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search)
        self.tb_menu = button("")
        self.tb_menu.setObjectName("tb")
        self.tb_menu.setIcon(icon("menu"))
        self.tb_menu.setToolTip(_("Tools and settings"))
        self.tb_menu.setMenu(self._menu())
        right = QHBoxLayout()
        right.setSpacing(8)
        right.addStretch(1)
        right.addWidget(self.search)
        right.addWidget(self.tb_menu)
        lay.addLayout(left, 1)
        lay.addWidget(self.tb_prev)
        lay.addWidget(self.lcd, 2)
        lay.addWidget(self.tb_next)
        lay.addLayout(right, 1)
        return bar

    def _menu(self):
        def add(menu, name):
            title = _(MENU_EXTRA[name]) if name in MENU_EXTRA else _(backend.TOOLS[name]["title"]) + "…"
            act = menu.addAction(title, lambda n=name: self.open_tool(n))
            if name in backend.TOOLS:
                act.setToolTip(_(backend.TOOLS[name].get("about", "")))

        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for entry in MENU:
            if entry is None:
                menu.addSeparator()
            elif isinstance(entry, tuple) and len(entry) == 1:
                if menu.actions():
                    menu.addSeparator()
                head = menu.addAction(_(entry[0]))
                head.setEnabled(False)          # a heading, not a command
            elif isinstance(entry, tuple):
                sub = menu.addMenu(_(entry[0]))
                sub.setToolTipsVisible(True)
                for name in entry[1]:
                    add(sub, name)
            else:
                add(menu, entry)
        return menu

    def _statusbar(self):
        bar = QFrame()
        bar.setObjectName("statusbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bar.setFixedHeight(UI["status_h"])
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 2, 14, 2)
        lay.setSpacing(10)
        self.summary = QLabel()
        self.summary.setObjectName("summary")
        lay.addWidget(self.summary)
        lay.addStretch(1)
        return bar

    def _shortcuts(self):
        QShortcut(QKeySequence.StandardKey.Find, self, activated=lambda: (
            self.search.setFocus(), self.search.selectAll()))
        QShortcut(QKeySequence("Ctrl+S"), self, activated=lambda: self.run_tool("sync"))
        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.open_tool("incoming"))
        QShortcut(QKeySequence("Ctrl+R"), self, activated=lambda: self.reload_library(force=True))
        QShortcut(QKeySequence("Ctrl+,"), self, activated=lambda: self.navigate("settings"))
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.eject)
        for n, view in enumerate(("albums", "artists", "genres"), 1):
            QShortcut(QKeySequence(f"Ctrl+{n}"), self,
                      activated=lambda v=view: self.navigate("library") and self.library_page.set_view(v))

    # --- source list: places only

    def build_sidebar(self):
        self.sidebar.blockSignals(True)
        self.sidebar.clear()
        head_font = QFont()
        head_font.setPointSizeF(8)
        head_font.setBold(True)
        self.side_items = {}
        sections = [(N_("LIBRARY"), [("library", "library", _("Music"))])]
        if self.ipods:
            name = (self.ipod_info or {}).get("name") or "iPod"
            sections.append((N_("DEVICE"), [("ipod", "ipod", name)]))
        # "+" on the heading makes a new one; the row only while there are none yet
        sections.append((N_("PLAYLISTS"), [(f"playlist:{p['file']}", "playlist", p["name"])
                                           for p in self.playlists]
                         or [("vibe", "vibe", _("New vibe playlist…"))]))
        for title, items in sections:
            head = QTreeWidgetItem([_(title)])
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if title == "PLAYLISTS":
                head.setData(0, AddRole, True)
                head.setToolTip(0, _("New vibe playlist…"))
            head.setFont(0, head_font)
            head.setForeground(0, QColor(C["side_head"]))
            self.sidebar.addTopLevelItem(head)
            for key, glyph_name, text in items:
                it = QTreeWidgetItem([text])
                it.setIcon(0, icon(glyph_name, "side_text"))
                it.setData(0, KeyRole, key)
                if key == "ipod" and self.ipod_info:
                    d = self.ipod_info
                    it.setToolTip(0, f"{d['model']}\n" + _("{free} free of {total}").format(
                        free=fmt_gb(d['free_gb']), total=fmt_gb(d['capacity_gb'])))
                head.addChild(it)
                self.side_items[key] = it
            head.setExpanded(True)
        cur = self.side_items.get(self.current_key)
        if cur:
            self.sidebar.setCurrentItem(cur)
        self.sidebar.blockSignals(False)

    def _side_changed(self, item, previous):
        key = item.data(0, KeyRole) if item else None
        if not key or (key != self.current_key and not self.navigate(key)):
            self.sidebar.blockSignals(True)
            self.sidebar.setCurrentItem(previous)
            self.sidebar.blockSignals(False)

    def _side_menu(self, pos):
        item = self.sidebar.itemAt(pos)
        key = item.data(0, KeyRole) if item else None
        menu = QMenu(self)
        if key == "ipod":
            menu.addAction(_("Sync…"), lambda: self.run_tool("sync"))
            menu.addAction(_("Eject"), self.eject)
            menu.addAction(_("Read it again"), lambda: self.ipod_page.load(force=True))
        elif key and key.startswith("playlist:"):
            f = key.split(":", 1)[1]
            act = menu.addAction(_("Create on the iPod…"), lambda: self.run_tool(
                "playlist_to_ipod", {"file": f}))
            act.setEnabled(bool(self.ipods))
        elif key == "library":
            menu.addAction(_("Read the library again"), lambda: self.reload_library(force=True))
        else:
            return
        menu.exec(self.sidebar.viewport().mapToGlobal(pos))

    # --- navigation

    def current_page(self):
        return self.stack.currentWidget()

    def navigate(self, key):
        """Show a page; False if the current page doesn't want to be left."""
        page = self.current_page()
        if key != self.current_key and page is not None and not page.can_leave():
            return False
        if backend.cfg() is None and key != "settings":
            self.toast(_("Fill in the basic settings first."), bad=True)
            return False
        if key == "library":
            target = self.library_page
        elif key == "ipod":
            target = self.ipod_page
        elif key.startswith("playlist:"):
            target = self.playlist_page
            self.playlist_page.show_playlist(key.split(":", 1)[1])
        elif key == "vibe":
            target = self.vibe_page
        elif key == "settings":
            target = self.settings_page
        else:
            return False
        self.current_key = key
        self.settings_btn.setChecked(key == "settings")
        self.stack.setCurrentWidget(target)
        target.set_search(self.search.text())
        target.shown()
        item = self.side_items.get(key)
        if self.sidebar.currentItem() is not item:
            self.sidebar.blockSignals(True)
            if item:
                self.sidebar.setCurrentItem(item)
            else:
                self.sidebar.setCurrentItem(None)
                self.sidebar.clearSelection()
            self.sidebar.blockSignals(False)
        return True

    def _search(self, text):
        page = self.current_page()
        if page is not None:
            page.set_search(text)

    # --- tools from the menu

    def open_tool(self, name):
        if name == "settings":
            self.navigate("settings")
        elif name == "shortcuts":
            inform(self, _("Keyboard shortcuts"), "\n".join(
                f"{keys}    {_(what)}" for keys, what in SHORTCUTS))
        elif name == "export":
            self.export_list()
        elif name in ("incoming", "likes", "marks"):
            ToolDialog(self, name).exec()
        elif backend.TOOLS[name].get("apply"):
            self.run_tool(name)                 # a dry run first: safe to start right away
        elif ask(self, _(backend.TOOLS[name]["title"]), _(backend.TOOLS[name].get("about", "")),
                 yes=_("Run")):
            self.run_tool(name)

    def export_list(self):
        if self.albums is None:
            inform(self, _("Export the list"), _("The library is still being read."))
            return
        try:
            path = backend.export_list(self.albums)
        except Exception as e:
            inform(self, _("Export the list"), str(e), bad=True)
            return
        self.toast(_("Written: {path}\nClick to show it.").format(path=path), good=True,
                   action=lambda: backend.open_in_explorer(os.path.dirname(path)))

    # --- drag & drop: a folder of new tracks

    def _dropped_folder(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        if len(paths) != 1:
            return None
        p = paths[0]
        return p if os.path.isdir(p) else os.path.dirname(p) if audiotags.is_audio(p) else None

    def dragEnterEvent(self, e):
        if self._dropped_folder(e) and backend.cfg() is not None:
            e.acceptProposedAction()
            self.drop_hint.setGeometry(self.centralWidget().geometry().adjusted(12, 12, -12, -12))
            self.drop_hint.show()
            self.drop_hint.raise_()

    def dragLeaveEvent(self, e):
        self.drop_hint.hide()

    def dropEvent(self, e):
        self.drop_hint.hide()
        folder = self._dropped_folder(e)
        if folder:
            e.acceptProposedAction()
            ToolDialog(self, "incoming", folder).exec()

    # --- library

    def reload_library(self, force=False, quick=False):
        if self.scanning:
            # the cached library can be worked with during a scan: a move made then needs one more
            self.rescan = True
            return
        if force and self.library_page.marks:
            if not ask(self, _("Read the library again"),
                       _("Read the library again and drop the unsaved marks?"), yes=_("Read again"),
                       danger=True):
                return
            self.library_page.revert()
        self.scanning = True
        self.albums = None
        self.library_page.set_albums(None)
        covers().clear()
        self.library_page.delegate.scaled.clear()
        self.scan_progress = None
        # what the index remembers is shown at once; the scan then checks it against the drive
        run_async(backend.cached_library, ok=self._cached)
        run_async(backend.scan_library, lambda *p: setattr(self, "scan_progress", p), quick,
                  ok=self._scanned, failed=self._scan_failed)

    def _cached(self, albums):
        if self.scanning and self.albums is None and albums:
            self.albums = albums
            self.library_page.set_albums(albums)
            self.update_status()

    def _scan_over(self):
        self.scanning = False
        if self.rescan:
            self.rescan = False
            QTimer.singleShot(0, self.reload_library)

    def _scanned(self, albums):
        self._scan_over()
        same = albums == self.albums
        self.albums, self.albums_error = albums, None
        if not same:                            # an unchanged library keeps the grid as it is
            self.library_page.set_albums(albums)
        self.note(_("Library checked"), plural(len(albums), "{n} album", "{n} albums")
                  + (" · " + _("changes found") if not same else " · " + _("nothing changed")))
        self.update_status()
        self.refresh_attention()

    def _scan_failed(self, msg):
        self._scan_over()
        if self.albums:                         # the cached library stays; the drive may be back soon
            self.toast(_("Could not check the library: {error}").format(error=msg), bad=True)
            return
        self.albums, self.albums_error = None, msg
        self.library_page.set_albums(None, msg)
        self.update_status()

    def reload_playlists(self):
        try:
            self.playlists = backend.playlists()
        except Exception:
            self.playlists = []
        self.build_sidebar()

    # --- what needs attention

    def refresh_attention(self):
        """Recount what needs doing; the genre count is read in the background."""
        run_async(backend.genres_missing, ok=self._genres_counted, failed=lambda m: None)
        self._show_attention()

    def _genres_counted(self, n):
        self.genres_missing = n
        self._show_attention()

    def _show_attention(self):
        items = []
        if self.albums:
            bare = [a for a in self.albums if a["no_art"]]
            if bare:
                items.append((plural(len(bare), "{n} album without a cover", "{n} albums without a cover"),
                              _("Find covers…"),
                              lambda: self.open_tool("covers")))
        if self.genres_missing:
            items.append((plural(self.genres_missing, "{n} album without a genre",
                                 "{n} albums without a genre"), _("Suggest…"),
                          lambda: (self.navigate("library"), self.library_page.set_view("genres"),
                                   self.run_tool("genres_suggest"))))
        info = self.ipod_info if self.ipods else None
        if info:
            only = sum(1 for t in info["tracks"] if t["state"] == "")
            archive = sum(1 for t in info["tracks"] if t["state"] == "R")
            if archive:
                items.append((plural(archive, "{n} archive track still on the iPod",
                                     "{n} archive tracks still on the iPod"), _("Sync…"),
                              lambda: self.run_tool("sync")))
            if only:
                items.append((plural(only, "{n} track only on the iPod", "{n} tracks only on the iPod"),
                              _("Save them…"),
                              lambda: self.run_tool("rescue")))
        self.library_page.attention.set_items(items)

    # --- marks

    def _marks_changed(self):
        self.update_status()

    def apply_marks(self):
        if self.jobs.busy():
            inform(self, _("Move albums"), _("Wait for the running task to finish."))
            return
        to_a, to_r = self.library_page.summary()
        if not (to_a or to_r):
            return
        lines = [_("→ Archive   {album}").format(album=f"{a['artist']} — {a['album']}") for a in to_r] + \
                [_("→ Active    {album}").format(album=f"{a['artist']} — {a['album']}") for a in to_a]
        shown = UI["list_preview"]
        text = (plural(len(lines), "Move {n} album between Active and Archive?",
                       "Move {n} albums between Active and Archive?") + "\n\n"
                + "\n".join(lines[:shown])
                + ("\n" + _("… {n} more").format(n=len(lines) - shown) if len(lines) > shown else ""))
        if not ask(self, _("Move albums"), text, yes=_("Move"), details="\n".join(lines)):
            return
        moves = dict(self.library_page.marks)
        self.library_page.setEnabled(False)       # no new marks while files move
        self.save_marks_btn.setEnabled(False)
        self.revert_btn.setEnabled(False)
        self.lcd_note = (_("Moving albums…"), "")
        self.lcd_note_until = float("inf")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        run_async(backend.save_marks, list(self.albums or []), moves,
                  ok=self._marks_saved, failed=self._marks_failed)

    def _marks_done(self):
        QApplication.restoreOverrideCursor()
        self.library_page.setEnabled(True)
        self.save_marks_btn.setEnabled(True)
        self.revert_btn.setEnabled(True)
        self.lcd_note = None

    def _marks_saved(self, result):
        self._marks_done()
        done, errors = result
        self.library_page.marks.clear()
        self._marks_changed()
        self.note(plural(done, "Moved {n} album", "Moved {n} albums"), "")
        if errors:
            inform(self, _("Move albums"), _("Some albums weren't moved:") + "\n\n"
                   + "\n".join(errors[:UI["list_preview"]]), bad=True)
        self.reload_library()
        self.ipod_page.data = None
        if done and self.ipods and ask(self, _("Move albums"),
                                       plural(done, "Moved {n} album. Sync the iPod now?",
                                              "Moved {n} albums. Sync the iPod now?"),
                                       yes=_("Sync…")):
            self.run_tool("sync")

    def _marks_failed(self, msg):
        self._marks_done()
        inform(self, _("Move albums"), msg, bad=True)
        self.reload_library()

    # --- tools

    def run_tool(self, tool, params=None, applying=False):
        title = _(backend.TOOLS[tool]["title"])
        if tool in ("sync", "rescue", "restore", "playlist_to_ipod", "eject") and not self.ipods:
            inform(self, title, _("No iPod connected. Connect it with the cable and try again."))
            return None
        if tool in ("sync", "playlist_to_ipod", "rescue") and self.library_page.marks:
            if not ask(self, title, _("There are unsaved Active / Archive marks. The sync only sees "
                                      "what's saved on disk.\n\nContinue without them?"), yes=_("Continue")):
                return None
        if tool == "genres_apply" and self.genres_page.edits and not self.genres_page.save():
            return None
        try:
            return self.jobs.start(tool, params or {}, applying)
        except ValueError as e:
            inform(self, title, str(e))
        except RuntimeError as e:
            inform(self, title, f"{e}\n" + _("Wait for it to finish."))
            self.sheet.open_for(self.jobs.current)
        return None

    def _job_started(self, job):
        self.sheet.open_for(job)
        self._busy_buttons()

    def _job_finished(self, job):
        if self.relabel_after_job and not self.jobs.busy():
            QTimer.singleShot(0, lambda note=self.relabel_after_job: self.relabel(note))
        refresh = backend.TOOLS[job.tool].get("refresh", set())
        real = job.applying or not backend.TOOLS[job.tool].get("apply")
        if job.tool == "eject":
            # the drive is gone (or going): don't read it again, just look for it
            self.ipod_page.data = None
            self.ipod_info = None
            QTimer.singleShot(UI["eject_repoll_ms"], self._poll_ipod)
        elif real and job.ok or job.tool == "restore":
            if "library" in refresh:
                self.reload_library()
            if "ipod" in refresh:
                self.ipod_page.invalidate()
            if "genres" in refresh:
                self.genres_page.invalidate()
                self.refresh_attention()
            if "playlists" in refresh:
                self.reload_playlists()
        self.sheet.refresh()
        self._follow_ups(job)
        self._busy_buttons()
        if job.ok and not job.problems:
            head, good = _("Done: {tool}").format(tool=job.title), True
        elif job.problems:
            head, good = _("Done, with problems: {tool}").format(tool=job.title), False
        else:
            head, good = _("Didn't finish: {tool}").format(tool=job.title), False
        self.note(head, job.reason)
        # always, the log open or not: its last line can be scrolled out of sight
        self.toast(head + ("\n" + job.reason if job.reason else ""), good=good, bad=not good,
                   action=lambda: self.sheet.open_for(job))

    def _follow_ups(self, job):
        sheet = self.sheet
        if job is not sheet.job or not job.ok:
            return
        if job.tool == "sync" and job.applying:
            sheet.add_follow(_("Eject the iPod"), self.eject)
            sheet.add_follow(_("Show the iPod"), lambda: self.navigate("ipod"))
        elif job.tool == "incoming" and job.applying:
            if job.params.get("to") != "archive" and self.ipods:
                sheet.add_follow(_("Sync the iPod…"), lambda: self.run_tool("sync"), primary=True)
            sheet.add_follow(_("Show the library"), lambda: self.navigate("library"))
        elif job.tool in ("genres_apply", "covers") and job.applying and self.ipods:
            sheet.add_follow(_("Sync the iPod…"), lambda: self.run_tool("sync"), primary=True)
        elif job.tool == "rescue" and job.applying:
            sheet.add_follow(_("Add them to the library…"), lambda: self.open_tool("incoming"),
                             primary=True)
        elif job.tool == "vibe":
            newest = self.playlists[0]["file"] if self.playlists else None
            if newest:
                sheet.add_follow(_("Show the playlist"), lambda: self.navigate(f"playlist:{newest}"))
                if self.ipods:
                    sheet.add_follow(_("Create on the iPod…"), lambda: self.run_tool(
                        "playlist_to_ipod", {"file": newest}), primary=True)
        elif job.tool == "genres_suggest":
            sheet.add_follow(_("Review the genres"), lambda: (
                self.navigate("library"), self.library_page.set_view("genres")), primary=True)

    def _busy_buttons(self):
        ready = backend.cfg() is not None
        self.tb_sync.setEnabled(ready and not self.jobs.busy() and bool(self.ipods))

    def set_ready(self, ready):
        """Before the first settings are saved there's no library: nothing to add, search or open."""
        for w in (self.tb_add, self.search, self.tb_menu, self.sidebar, self.settings_btn):
            w.setEnabled(ready)
        self._busy_buttons()

    def eject(self):
        if self.ipods:
            self.run_tool("eject")

    # --- the iPod: noticed when it's plugged in, read in the background

    def _poll_ipod(self):
        if self.polling:
            return
        self.polling = True
        run_async(backend.ipod_mounted, ok=self._polled,
                  failed=lambda m: setattr(self, "polling", False))

    def _polled(self, roots):
        self.polling = False
        if roots == self.ipods or self.identifying:
            return
        self.ipods = roots
        self.ipod_info = None
        self.ipod_page.data = None
        if roots:
            self.ipod_page.load(force=True)     # fills ipod_info: sidebar bar, attention
        elif self.current_key == "ipod":
            self.navigate("library")
        self.build_sidebar()
        self._busy_buttons()
        self._show_attention()
        self.update_status()
        if self.current_key and self.current_key.startswith("playlist:"):
            self.playlist_page.shown()
        self.note(_("iPod connected") if roots else _("iPod disconnected"), ", ".join(roots))

    def ipod_read(self, info):
        """IpodPage read the iPod: remember it for the sidebar, attention and status."""
        self.ipod_info = info
        self.build_sidebar()
        self._show_attention()
        self.update_status()

    # --- LCD and status

    def note(self, title, sub, seconds=None):
        self.lcd_note = (title, sub)
        self.lcd_note_until = time.time() + (seconds or UI["note_s"])

    def _lcd_clicked(self):
        job = self.jobs.current
        if self.queue and not (job and job.running):
            playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            self.player.pause() if playing else self.player.play()
        elif job is not None:
            self.sheet.open_for(job)

    def _lcd_seek(self, where):
        job = self.jobs.current
        if self.queue and not (job and job.running) and self.player.duration() > 0:
            self.player.setPosition(int(where * self.player.duration()))
        else:
            self._lcd_clicked()

    # --- playing tracks: an album from the chosen track to its end

    def play(self, queue, at, position=0):
        self.queue, self.queue_at, self.resume_at = queue, at, position
        self.player.setSource(QUrl.fromLocalFile(queue[at]["path"]))
        self.player.play()

    def skip(self, step):
        """Next / previous track; 'previous' a few seconds into a track starts it over."""
        if step < 0 and self.player.position() > UI["restart_ms"]:
            self.player.setPosition(0)
            return
        at = self.queue_at + step
        if 0 <= at < len(self.queue):
            self.play(self.queue, at)
        elif step > 0:
            self.player.stop()
            self.queue = []
        else:
            self.player.setPosition(0)

    def _media_status(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self.resume_at:
            self.player.setPosition(self.resume_at)      # can't be set before the file is open
            self.resume_at = 0
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.skip(1)

    def update_lcd(self):
        for b in (self.tb_prev, self.tb_next):
            b.setVisible(bool(self.queue))
        job = self.jobs.current
        if job is not None and job.running:
            status, progress = job.status()
            self.lcd.show_progress(job.title, status, progress)
            return
        if self.lcd_note and time.time() < self.lcd_note_until:
            self.lcd.show_idle(*self.lcd_note)
            return
        self.lcd_note = None
        if self.queue:
            t, p = self.queue[self.queue_at], self.player
            paused = p.playbackState() != QMediaPlayer.PlaybackState.PlayingState
            self.lcd.show_progress((GLYPHS["pause"] if paused else GLYPHS["play"]) + "  " + t["title"],
                                   f"{t['artist']} · {fmt_time(p.position() / 1000)} / "
                                   f"{fmt_time(p.duration() / 1000)}",
                                   p.position() / p.duration() if p.duration() > 0 else 0)
            return
        if self.scanning:
            text, frac = self.scan_text()
            self.lcd.show_progress(_("Reading the library…"), text, frac)
            if self.albums is None:
                self.library_page.loading.setText(_("Reading the library…") + "\n" + text)
            return
        info = self.ipod_info if self.ipods else None
        if info:
            self.lcd.show_idle(info["name"], f"{info['model']} · "
                               + _("{free} free").format(free=fmt_gb(info['free_gb'])))
        elif self.ipods:
            self.lcd.show_idle(_("PodVault"), _("iPod connected — reading it…"))
        else:
            self.lcd.show_idle(_("PodVault"), _("No iPod — connect one to sync"))

    def scan_text(self):
        """What the scan is doing, and how far it is (0..1 or None)."""
        p = self.scan_progress
        if not p:
            return "", None
        done, total, left = p
        if not total:
            return plural(done, "Looking through the folders: {n} album",
                          "Looking through the folders: {n} albums"), None
        text = _("Reading tags: {done}/{total}").format(done=done, total=total)
        if left is not None:
            text += " · " + (_("less than a minute left") if left < 60 else
                             plural(round(left / 60), "about {n} minute left", "about {n} minutes left"))
        return text, done / total

    def update_status(self):
        """The one number that matters: how much Active is, and whether it fits the iPod."""
        if self.albums is None:
            self.summary.setText(self.albums_error or _("Reading the library…"))
            return
        active = [a for a in self.albums if self.library_page.model.state(a) == "A"]
        gb = sum(a["bytes"] for a in active) / 1024 ** 3
        tracks = sum(a["tracks"] for a in active)
        text = _("Active: {albums}, {tracks}, {size} → iPod").format(
            albums=plural(len(active), "{n} album", "{n} albums"),
            tracks=plural(tracks, "{n} track", "{n} tracks"), size=fmt_gb(gb))
        info = self.ipod_info if self.ipods else None
        room = None
        if info and info.get("capacity_gb"):
            room = info["free_gb"] + info["music_gb"]      # everything but the non-music files
        else:
            try:                                           # no iPod: the space set in Settings
                room = int((backend.cfg() or {}).get("ipod_capacity_gb") or 0) or None
            except (TypeError, ValueError):
                pass
        if room:
            text += "  ·  " + (_("fits: {size} for music").format(size=fmt_gb(room)) if gb <= room else
                               _("doesn't fit: {size} too much").format(size=fmt_gb(gb - room)))
        self.summary.setText(text)

    # --- settings

    def _settings_saved(self):
        if (backend.cfg() or {}).get("language") != self.language:
            # every text in the window was set when it was built: a new language gets a new window
            if self.jobs.busy():
                self.relabel_after_job = _("Language changed.")
                self.toast(_("The new language is applied when the running task finishes."))
            else:
                self.relabel(_("Language changed."))
                return
        self.reload_library()
        self.reload_playlists()
        self.ipod_page.data = None
        self.genres_page.loaded = False
        self._poll_ipod()
        if self.current_key == "settings" and self.settings_page.first:
            self.navigate("library")

    def _b1air_changed(self):
        pal = theme.b1air_palette()
        if pal == self.b1air or self.replaced:
            return
        self.b1air = pal
        theme.apply(QApplication.instance())
        # the stylesheet is new already; icons and pictures drawn at build time need a new window
        if self.jobs.busy():
            self.relabel_after_job = self.relabel_after_job or _("Colours changed.")
        else:
            self.relabel(_("Colours changed."))

    def relabel(self, note):
        """A new language or colours, in place: the same window rebuilt, state carried over."""
        i18n.reset()
        QApplication.instance().setApplicationName(_("PodVault"))
        new = MainWindow(key=self.current_key if backend.cfg() is not None else None)
        new.setGeometry(self.geometry())
        if self.isMaximized():
            new.showMaximized()
        else:
            new.show()
        lp, nlp = self.library_page, new.library_page
        nlp.marks.update(lp.marks)
        nlp.set_view(lp.view)
        new.search.setText(self.search.text())
        new._marks_changed()
        if self.queue:
            new.play(self.queue, self.queue_at, self.player.position())
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                new.player.pause()
        self.player.stop()
        new.toast(note, good=True)
        self.replaced = True
        # hidden, not deleted: background reads started by this window may still call back into it
        self.poll.stop()
        self.lcd_timer.stop()
        self.close()

    # --- toasts

    def toast(self, text, good=False, bad=False, action=None):
        t = QLabel(text, self)
        t.setWordWrap(True)
        t.setMaximumWidth(UI["toast_w"])
        stack = [w for w in self.findChildren(QLabel, "toast") if w.isVisible()]
        t.setObjectName("toast")
        t.setProperty("kind", "bad" if bad else "good" if good else "")
        t.style().unpolish(t)
        t.style().polish(t)
        t.adjustSize()
        gap = UI["toast_gap"]
        y = self.height() - UI["status_h"] - gap - t.height() - sum(w.height() + gap for w in stack)
        t.move(self.width() - t.width() - gap, max(UI["toolbar_h"] + gap, y))
        if action:
            t.setCursor(Qt.CursorShape.PointingHandCursor)
            t.mousePressEvent = lambda e: (action(), t.deleteLater())
        t.show()
        t.raise_()
        QTimer.singleShot(UI["toast_bad_ms"] if bad else UI["toast_ms"], t.deleteLater)

    # --- closing

    def closeEvent(self, e):
        if self.replaced:           # a new language: the new window has taken over
            e.accept()
            return
        page = self.current_page()
        if self.jobs.busy():
            job = self.jobs.current
            if not job.cancelable:
                inform(self, _("PodVault"), _("“{tool}” is writing to the iPod. "
                                                   "Wait until it's done before closing.").format(tool=job.base_title))
                e.ignore()
                return
            if not ask(self, _("PodVault"),
                       _("“{tool}” is still running. Stop it and quit?").format(tool=job.base_title),
                       yes=_("Quit"), danger=True):
                e.ignore()
                return
            job.cancel()
        if self.library_page.marks and not ask(
                self, _("PodVault"), _("The Active / Archive marks aren't saved. Quit anyway?"),
                yes=_("Quit"), danger=True):
            e.ignore()
            return
        first_run = page is self.settings_page and backend.cfg() is None
        if page is not None and not first_run and not page.can_leave():
            e.ignore()
            return
        e.accept()


def catch_errors():
    """An exception in a click handler or a timer would end the program: PyQt aborts on one,
    and started from Finder or the Start menu nothing is left to say why. Show it and log it
    instead; faulthandler logs real crashes (in Qt, in a decoder) into the same file."""
    log = None
    try:
        os.makedirs(settings.DATA, exist_ok=True)
        log = open(os.path.join(settings.DATA, "podvault-errors.log"), "a", encoding="utf-8")
        faulthandler.enable(log)
    except OSError:
        pass
    showing = []

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        if sys.stderr:                          # None under pythonw
            sys.stderr.write(text)
        if log:
            log.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
            log.flush()
        if showing:                             # an error while showing one: logged is enough
            return
        showing.append(1)
        try:
            box = QMessageBox(QApplication.activeWindow())
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle(_("PodVault"))
            box.setText(_("Something went wrong: {error}").format(error=str(value) or kind.__name__)
                        + ("\n\n" + _("The details are in {file}").format(file=log.name) if log else ""))
            box.setDetailedText(text)
            box.exec()
        finally:
            showing.clear()

    sys.excepthook = hook
    return log


def run():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PodVault.Window")
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(_("PodVault"))
    app.setDesktopFileName("podvault")      # Wayland finds the menu entry and its icon by this
    app._error_log = catch_errors()         # kept open for faulthandler
    theme.apply(app)
    win = MainWindow()
    win.show()
    return app.exec()
