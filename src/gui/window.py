"""The main window: toolbar with the LCD, the source list (places only), pages,
the status bar with the Active summary, the ☰ menu with the tools, the task sheet."""

import os
import sys
import time

from PyQt6.QtCore import QEvent, QProcess, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                             QMainWindow, QMenu, QPlainTextEdit, QProgressBar, QSplitter,
                             QStackedWidget, QStyle, QStyledItemDelegate, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

import settings
import tags as audiotags
from gui import backend, theme
from i18n import N_, _
from gui.jobs import Jobs
from gui.pages import (IpodPage, LibraryPage, PlaylistPage, SettingsPage, ToolDialog, VibePage)
from gui.theme import C, GLYPHS, UI, icon
from gui.widgets import Lcd, ask, button, covers, fmt_gb, inform, plural, run_async

KeyRole = Qt.ItemDataRole.UserRole + 10


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(C["accent"]))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    f = QFont()
    f.setPixelSize(38)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(C["on_accent"]))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, GLYPHS["app"])
    p.end()
    return QIcon(pm)


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
            self.status.setText(cur.strip() or status or _("working…"))
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
            ok = job.code == 0
            self.bar.setProperty("state", "ok" if ok else "bad")
            took = (job.finished or time.time()) - job.started
            self.status.setText((_("Done") if ok else _("Cancelled") if job.cancelled else
                                 _("Stopped with an error (exit code {code})").format(code=job.code))
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
    """Draws the iPod row: its name, a small capacity bar and an eject button."""

    eject = pyqtSignal()

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
        if index.data(KeyRole) == "ipod" and event.type() == QEvent.Type.MouseButtonRelease \
                and self._eject_rect(option.rect).contains(event.position().toPoint()):
            self.eject.emit()
            return True
        return super().editorEvent(event, model, option, index)


# ------------------------------------------------------------------ the window


# The ☰ menu: tool names from backend.TOOLS; None is a separator; (title, [tools]) a submenu.
MENU = ["covers", "tags", "likes", "export", "marks", None,
        (N_("Audio tools"), ["flac", "download", "tracklist_covers", "spatial"]), None,
        "build", "settings"]
MENU_EXTRA = {"export": N_("Export the list…"), "settings": N_("Settings…")}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.language = (backend.cfg() or {}).get("language")   # the one the texts were built in
        self.setWindowTitle(_("Music Utility"))
        self.setWindowIcon(app_icon())
        self.resize(*UI["window"])
        self.setMinimumSize(*UI["window_min"])
        self.setAcceptDrops(True)
        self.jobs = Jobs()
        self.jobs.started.connect(self._job_started)
        self.jobs.finished.connect(self._job_finished)
        self.albums, self.albums_error, self.scanning = None, None, False
        self.ipods, self.ipod_info, self.polling = [], None, False
        self.playlists, self.genres_missing = [], 0
        self.current_key = None
        self.lcd_note, self.lcd_note_until = None, 0

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
        self.sidebar.setItemDelegate(self.side_delegate)
        self.stack = QStackedWidget()
        split.addWidget(self.sidebar)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([UI["side_w"], UI["window"][0] - UI["side_w"]])
        root.addWidget(split, 1)
        root.addWidget(self._statusbar())
        self.setCentralWidget(central)

        self.library_page = LibraryPage(self)
        self.library_page.marks_changed.connect(self._marks_changed)
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
            self.reload_library()
            self.reload_playlists()
            self.navigate("library")
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
        lay.addWidget(self.lcd, 2)
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
        self.pending = QLabel()
        self.pending.setObjectName("pending")
        self.revert_btn = button(_("Revert"))
        self.save_marks_btn = button(_("Move the files…"), primary=True)
        for b in (self.revert_btn, self.save_marks_btn):
            b.setFixedHeight(UI["status_button_h"])
        self.revert_btn.clicked.connect(lambda: self.library_page.revert())
        self.save_marks_btn.clicked.connect(self.apply_marks)
        lay.addWidget(self.summary)
        lay.addStretch(1)
        lay.addWidget(self.pending)
        lay.addWidget(self.revert_btn)
        lay.addWidget(self.save_marks_btn)
        for w in (self.pending, self.revert_btn, self.save_marks_btn):
            w.hide()
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
        sections.append((N_("PLAYLISTS"), [(f"playlist:{p['file']}", "playlist", p["name"])
                                       for p in self.playlists]
                         + [("vibe", "vibe", _("New vibe playlist…"))]))
        for title, items in sections:
            head = QTreeWidgetItem([_(title)])
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
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
        if self._dropped_folder(e):
            e.acceptProposedAction()

    def dropEvent(self, e):
        folder = self._dropped_folder(e)
        if folder:
            e.acceptProposedAction()
            ToolDialog(self, "incoming", folder).exec()

    # --- library

    def reload_library(self, force=False):
        if self.scanning:
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
        run_async(backend.scan_library, ok=self._scanned, failed=self._scan_failed)

    def _scanned(self, albums):
        self.scanning = False
        self.albums, self.albums_error = albums, None
        self.library_page.set_albums(albums)
        self.update_status()
        self.refresh_attention()

    def _scan_failed(self, msg):
        self.scanning = False
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
        to_a, to_r = self.library_page.summary()
        n = len(to_a) + len(to_r)
        if n:
            parts = []
            if to_r:
                parts.append(_("{n} → Archive").format(n=len(to_r)))
            if to_a:
                parts.append(_("{n} → Active").format(n=len(to_a)))
            self.pending.setText(_("Not saved: {changes}").format(changes=", ".join(parts)))
        for w in (self.pending, self.revert_btn, self.save_marks_btn):
            w.setVisible(bool(n))
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
        refresh = backend.TOOLS[job.tool].get("refresh", set())
        real = job.applying or not backend.TOOLS[job.tool].get("apply")
        if job.tool == "eject":
            # the drive is gone (or going): don't read it again, just look for it
            self.ipod_page.data = None
            self.ipod_info = None
            QTimer.singleShot(UI["eject_repoll_ms"], self._poll_ipod)
        elif real and job.code == 0 or job.tool == "restore":
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
        ok = job.code == 0
        self.note((_("Done — {tool}") if ok else _("Stopped — {tool}")).format(tool=job.base_title), "")
        if not self.sheet.isVisible():
            self.toast((_("Done: {tool}") if ok else _("Didn't finish: {tool}")).format(tool=job.title),
                       good=ok, bad=not ok,
                       action=lambda: self.sheet.open_for(job))

    def _follow_ups(self, job):
        sheet = self.sheet
        if job is not sheet.job or job.code != 0:
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
        self.tb_sync.setEnabled(not self.jobs.busy() and bool(self.ipods))

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
        if roots == self.ipods:
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
        if self.jobs.current is not None:
            self.sheet.open_for(self.jobs.current)

    def update_lcd(self):
        job = self.jobs.current
        if job is not None and job.running:
            status, progress = job.status()
            self.lcd.show_progress(job.title, status, progress)
            return
        if self.lcd_note and time.time() < self.lcd_note_until:
            self.lcd.show_idle(*self.lcd_note)
            return
        self.lcd_note = None
        if self.scanning:
            self.lcd.show_progress(_("Music Utility"), _("Reading the library…"), None)
            return
        info = self.ipod_info if self.ipods else None
        if info:
            self.lcd.show_idle(info["name"], f"{info['model']} · "
                               + _("{free} free").format(free=fmt_gb(info['free_gb'])))
        elif self.ipods:
            self.lcd.show_idle(_("Music Utility"), _("iPod connected — reading it…"))
        else:
            self.lcd.show_idle(_("Music Utility"), _("No iPod connected"))

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
        if info and info.get("capacity_gb"):
            room = info["free_gb"] + info["music_gb"]      # everything but the non-music files
            text += "  ·  " + (_("fits: {size} for music").format(size=fmt_gb(room)) if gb <= room else
                               _("doesn't fit: {size} too much").format(size=fmt_gb(gb - room)))
        self.summary.setText(text)

    # --- settings

    def _settings_saved(self):
        if (backend.cfg() or {}).get("language") != self.language:
            # every text in the window was set when it was built: a new language needs a new window
            if ask(self, _("Settings"), _("The interface language changed. Restart the window now?"),
                   yes=_("Restart")) and self.close():
                QProcess.startDetached(sys.executable, [os.path.join(settings.ROOT, "Main.py"), "--gui"],
                                       settings.ROOT)
                return
        self.reload_library()
        self.reload_playlists()
        self.ipod_page.data = None
        self.genres_page.loaded = False
        self._poll_ipod()
        if self.current_key == "settings" and self.settings_page.first:
            self.navigate("library")

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
        page = self.current_page()
        if self.jobs.busy():
            job = self.jobs.current
            if not job.cancelable:
                inform(self, _("Music Utility"), _("“{tool}” is writing to the iPod. "
                                                   "Wait until it's done before closing.").format(tool=job.base_title))
                e.ignore()
                return
            if not ask(self, _("Music Utility"),
                       _("“{tool}” is still running. Stop it and quit?").format(tool=job.base_title),
                       yes=_("Quit"), danger=True):
                e.ignore()
                return
            job.cancel()
        if self.library_page.marks and not ask(
                self, _("Music Utility"), _("The Active / Archive marks aren't saved. Quit anyway?"),
                yes=_("Quit"), danger=True):
            e.ignore()
            return
        first_run = page is self.settings_page and backend.cfg() is None
        if page is not None and not first_run and not page.can_leave():
            e.ignore()
            return
        e.accept()


def run():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MusicUtility.Window")
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(_("Music Utility"))
    theme.apply(app)
    win = MainWindow()
    win.show()
    return app.exec()
