"""The pages shown next to the source list."""

import os
from collections import Counter

import settings
import i18n
from i18n import N_, _
from PyQt6.QtCore import QEvent, QMimeData, QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QFontMetrics
from PyQt6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QCheckBox, QComboBox, QCompleter,
                             QDialog, QListWidget, QListWidgetItem, QMenu, QPushButton,
                             QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QLineEdit, QListView, QPlainTextEdit,
                             QRadioButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
                             QStackedWidget, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
                             QTabWidget,
                             QVBoxLayout, QWidget)

from gui import backend
from gui.theme import C, COLUMNS, GLYPHS, UI
from musiclib import first_artist, norm
import playlists as saved_lists
import trackstats

DRAG_MIME = "application/x-podvault-paths"     # "album:<folder>" / "file:<path>" lines


class StarClicks(QObject):
    """A table's rating column: a click on the n-th star rates the row n stars; on the
    stars it already has, it clears them. on_rate(row, rating 0-100)."""

    def __init__(self, table, column, on_rate):
        super().__init__(table)
        self.table, self.column, self.on_rate = table, column, on_rate
        table.viewport().installEventFilter(self)
        table.viewport().setMouseTracking(True)

    def eventFilter(self, obj, e):
        if e.type() != QEvent.Type.MouseButtonPress or e.button() != Qt.MouseButton.LeftButton:
            return False
        index = self.table.indexAt(e.position().toPoint())
        if not index.isValid() or index.column() != self.column:
            return False
        rect = self.table.visualRect(index)
        width = max(1, QFontMetrics(self.table.font()).horizontalAdvance("★"))
        n = min(5, max(1, int((e.position().x() - rect.x() - 6) / width) + 1))
        item = self.table.item(index.row(), index.column())
        had = item.data(Qt.ItemDataRole.UserRole) if item else 0
        self.on_rate(index.row(), 0 if had == n * trackstats.STAR else n * trackstats.STAR)
        return True


def star_item(rating):
    it = SortItem(trackstats.stars(rating), rating or 0)
    it.setData(Qt.ItemDataRole.UserRole, rating or 0)
    it.setForeground(QColor(C["accent"] if rating else C["faint"]))
    it.setToolTip(_("Click a star to rate; click the same one again to clear"))
    return it


def rating_menu(menu, current, on_rate):
    sub = menu.addMenu(_("Rating"))
    for n in range(5, -1, -1):
        act = sub.addAction(trackstats.stars(n * trackstats.STAR) if n else _("None"),
                            lambda v=n * trackstats.STAR: on_rate(v))
        act.setCheckable(True)
        act.setChecked(round((current or 0) / trackstats.STAR) == n)
    return sub
from gui.widgets import (AlbumDelegate, AlbumModel, CapacityBar, Card, ElidedLabel, Segmented, ask,
                         button, covers, fmt_gb, fmt_time, fmt_total, inform, muted, placeholder,
                         plural, run_async)


# how the stored values of choice settings read in the window
CHOICE_LABELS = {"active": N_("Active"), "archive": N_("Archive"), "auto": N_("Auto"),
                 "cli": N_("Claude Code (subscription)"), "gemini": N_("Gemini (free key)"),
                 "codex": N_("Codex (subscription)"),
                 "api": N_("Anthropic API (paid)")}


class Page(QWidget):
    """A page: a header with a title and actions, and a body."""

    def __init__(self, win, title=""):
        super().__init__()
        self.win = win
        title = _(title)
        self.setObjectName("page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QFrame()
        head.setObjectName("viewHead")
        head.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        head.setMinimumHeight(UI["head_h"])
        self.head = QHBoxLayout(head)
        self.head.setContentsMargins(20, 8, 20, 8)
        self.head.setSpacing(12)
        self.title = QLabel(title)
        self.title.setObjectName("viewTitle")
        self.sub = ElidedLabel()
        self.sub.setProperty("muted", True)
        self.head.addWidget(self.title)
        self.head.addWidget(self.sub)
        self.head.addStretch(1)
        outer.addWidget(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        outer.addLayout(self.body, 1)

    def scroll_body(self):
        """A scrolling column with margins, for form-like pages."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # the column fits the width
        inner = QWidget()
        inner.setObjectName("page")
        col = QVBoxLayout(inner)
        col.setContentsMargins(20, 16, 20, 30)
        col.setSpacing(12)
        area.setWidget(inner)
        self.body.addWidget(area)
        return col

    def set_search(self, text):
        pass

    def shown(self):
        pass

    def can_leave(self):
        return True


def table(headers, stretch=1):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels([_(h) for h in headers])
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(24)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setWordWrap(False)
    h = t.horizontalHeader()
    h.setHighlightSections(False)
    h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    h.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)   # file order until a header is clicked
    if stretch is not None:
        h.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return t


class SortItem(QTableWidgetItem):
    """A cell that sorts by a hidden key (numbers, times) instead of its text."""

    def __init__(self, text, key=None):
        super().__init__(str(text))
        self.key = key if key is not None else str(text).lower()

    def __lt__(self, other):
        try:
            return self.key < other.key
        except TypeError:
            return str(self.key) < str(other.key)


def widths(t, columns):
    """{column index: COLUMNS key} — the widths live in the theme."""
    for c, name in columns.items():
        t.setColumnWidth(c, COLUMNS[name])


def fill(t, rows, sortable=True):
    """rows: [[(text, key, align, color), ...], ...] — key/align/color optional."""
    t.setSortingEnabled(False)
    t.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            cell = cell if isinstance(cell, tuple) else (cell,)
            text, key, align, color = (list(cell) + [None, None, None])[:4]
            it = SortItem(text, key)
            if align == "r":
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            elif align == "c":
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if color:
                it.setForeground(QColor(color))
            t.setItem(r, c, it)
    t.setSortingEnabled(sortable)


def matches(text, *fields):
    if not text:
        return True
    hay = " ".join(str(f or "") for f in fields).lower()
    return all(w in hay for w in text.lower().split())


# ================================================================ albums


SORTS = [(N_("Sort by Artist"), lambda a: (a["artist"].lower(), a["album"].lower())),
         (N_("Sort by Album"), lambda a: (a["album"].lower(), a["artist"].lower())),
         (N_("Sort by Year"), lambda a: (a["year"] or "9999", a["artist"].lower())),
         (N_("Sort by Size"), lambda a: -a["bytes"]),
         (N_("Sort by Tracks"), lambda a: -a["tracks"])]


class Grid(QListView):
    """The album grid, with iTunes-like keys: A / R mark, Space toggles, Enter opens."""

    key_mark = pyqtSignal(str)
    key_open = pyqtSignal()
    key_escape = pyqtSignal()

    def justify(self):
        """Spread the columns over the whole width: no empty strip on the right."""
        m, d = self.model(), self.itemDelegate()
        if m is None or d is None or not m.rowCount():
            return
        hint = d.sizeHint(QStyleOptionViewItem(), m.index(0, 0))
        # the view keeps a spacing-wide margin on both sides
        width = self.viewport().width() - 2 * self.spacing() - 1
        cols = max(1, width // (hint.width() + self.spacing()))
        size = QSize(width // cols, hint.height() + self.spacing())
        if size != self.gridSize():
            self.setGridSize(size)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.justify()

    def updateGeometries(self):
        # also runs when the scroll bar comes or goes and the viewport changes width
        super().updateGeometries()
        QTimer.singleShot(0, self.justify)

    def startDrag(self, actions):
        """Albums dragged onto a playlist in the sidebar go into it."""
        paths = [self.model().album(i)["path"] for i in self.selectionModel().selectedIndexes()]
        if not paths:
            return
        mime = QMimeData()
        mime.setData(DRAG_MIME, "\n".join("album:" + p for p in paths).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = covers().get(paths[0])
        if pm is not None:
            drag.setPixmap(pm.scaled(QSize(64, 64), Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        drag.exec(Qt.DropAction.CopyAction)

    def keyPressEvent(self, e):
        k, t = e.key(), e.text().lower()
        if t in ("a", "ф"):
            self.key_mark.emit("A")
        elif t in ("r", "к"):
            self.key_mark.emit("R")
        elif k == Qt.Key.Key_Space:
            self.key_mark.emit("toggle")
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.key_open.emit()
        elif k == Qt.Key.Key_Escape:
            self.key_escape.emit()
        else:
            super().keyPressEvent(e)


class AlbumDetail(QFrame):
    """An opened album, as the inspector on the right: cover, facts, where it lives,
    the tracks (the playing one marked), Play and the Active / Archive switch."""

    closed = pyqtSignal()
    mark = pyqtSignal(str, str)
    play = pyqtSignal(list, int)     # [{path, title, artist, album, dir}], the index to start from

    def __init__(self):
        super().__init__()
        self.setObjectName("detail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(UI["inspector_w"])
        self.album, self.rows, self.state, self.playing = None, [], "A", None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 14, 14)
        lay.setSpacing(12)
        top = QHBoxLayout()
        self.name = QLabel()
        self.name.setObjectName("albumName")
        self.name.setWordWrap(True)
        close = QPushButton(GLYPHS["close"])
        close.setProperty("flat", True)
        close.setToolTip(_("Close (Esc)"))
        close.setFixedWidth(UI["close_button_w"])
        close.clicked.connect(self.closed)
        top.addWidget(self.name, 1)
        top.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.cover = QLabel()
        self.cover.setFixedSize(UI["detail_cover"], UI["detail_cover"])
        self.cover.setScaledContents(True)
        row.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        facts = QVBoxLayout()
        facts.setSpacing(6)
        self.who = QLabel()
        self.who.setObjectName("albumArtist")
        self.who.setWordWrap(True)
        self.meta = muted("", wrap=True)
        self.pill = QLabel()
        self.pill.setObjectName("statePill")
        facts.addWidget(self.who)
        facts.addWidget(self.meta)
        facts.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignLeft)
        facts.addStretch(1)
        row.addLayout(facts, 1)
        lay.addLayout(row)
        acts = QHBoxLayout()
        acts.setSpacing(6)
        self.play_btn = button(GLYPHS["play"] + "  " + _("Play"), primary=True,
                               tip=_("Or double-click a track"))
        self.play_btn.clicked.connect(lambda: self._play(0))
        self.move = button("")
        self.move.clicked.connect(self._toggle)
        self.folder_btn = button(_("Show in folder"))
        self.folder_btn.clicked.connect(lambda: self.album and self._open())
        for b in (self.play_btn, self.move, self.folder_btn):
            acts.addWidget(b)
        acts.addStretch(1)
        lay.addLayout(acts)
        self.tracks = table(["#", N_("Title"), N_("Plays"), N_("Rating"), N_("Time")])
        self.tracks.horizontalHeader().hide()
        self.tracks.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tracks.customContextMenuRequested.connect(self._track_menu)
        StarClicks(self.tracks, 3, self._rate)
        self.win = None              # set by LibraryPage: plays, ratings, playlists
        self.tracks.verticalHeader().setDefaultSectionSize(UI["side_row"] + 4)
        self.tracks.setAlternatingRowColors(False)
        self.tracks.setSortingEnabled(False)
        self.tracks.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tracks.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tracks.cellDoubleClicked.connect(lambda row, col: self._play(row))
        lay.addWidget(self.tracks, 1)
        covers().ready.connect(self._cover_ready)

    def show_album(self, a, state):
        self.album = a
        self.name.setText(a["album"])
        self.who.setText(a["artist"])
        facts = [plural(a["tracks"], "{n} track", "{n} tracks"),
                 _("{mb} MB").format(mb=f"{a['bytes'] / 1024 / 1024:.0f}")]
        if a["year"]:
            facts.insert(0, str(a["year"]))
        if a["genre"]:
            facts.insert(1 if a["year"] else 0, a["genre"])
        self.meta.setText(" · ".join(facts))
        self.folder_btn.setToolTip(a["path"])
        self.set_state(state, a["path"])
        self._cover_ready(a["path"])
        self.tracks.setRowCount(0)
        self.rows = []
        path = a["path"]
        run_async(backend.album_tracks, path,
                  ok=lambda rows: self._tracks(path, rows),
                  failed=lambda m: self.meta.setText(_("Could not read the tracks: {error}").format(error=m)))

    def set_state(self, state, path):
        if self.album and self.album["path"] == path:
            self.state = state
            moved = state != self.album["state"]
            self.move.setText(_("Move to Archive") if state == "A" else _("Move to Active"))
            self.pill.setText((_("→ Active, not moved yet") if state == "A" else _("→ Archive, not moved yet"))
                              if moved else _("Active · goes to the iPod") if state == "A" else
                              _("Archive · stays on disk"))
            self.pill.setProperty("state", "moved" if moved else state)
            self.pill.style().unpolish(self.pill)
            self.pill.style().polish(self.pill)

    def set_playing(self, path):
        """Mark the track that's playing, if it's one of this album's."""
        self.playing = path
        if self.album:
            self._paint_rows()

    def _toggle(self):
        if self.album:
            self.mark.emit(self.album["path"], "R" if self.state == "A" else "A")

    def _open(self):
        try:
            backend.open_in_explorer(self.album["path"])
        except ValueError as e:
            inform(self, _("Show in folder"), str(e), bad=True)

    def _cover_ready(self, path):
        if not self.album or path != self.album["path"]:
            return
        pm = covers().get(path, UI["cover_large"])
        if pm is None:
            pm = covers().get(path)
        self.cover.setPixmap(pm or placeholder(f"{self.album['artist']} {self.album['album']}",
                                               UI["detail_cover"]))

    def _play(self, at):
        if at < len(self.rows):
            self.play.emit([{"path": os.path.join(self.album["path"], r["file"]), "title": r["title"],
                             "artist": r["artist"] or self.album["artist"], "album": self.album["album"],
                             "dir": self.album["path"]} for r in self.rows], at)

    def _tracks(self, path, rows):
        if not self.album or self.album["path"] != path:
            return
        self.rows = rows
        self._fill()

    def _info(self, r):
        """(plays, rating, last played) of a track row."""
        if self.win is None:
            return 0, 0, 0
        return trackstats.info(self.win.stats, self.win.rated, r["artist"] or self.album["artist"],
                               r["title"], self.album["album"])

    def _fill(self):
        rows = self.rows
        fill(self.tracks, [[
            (r["track"], int(r["track"]) if r["track"].isdigit() else 999, "r", C["faint"]),
            r["title"] if not r["artist"] or r["artist"] == self.album["artist"]
            else f"{r['title']}  ·  {r['artist']}",
            (self._info(r)[0] or "", self._info(r)[0], "r", C["muted"]),
            "",
            (fmt_time(r["length"]), r["length"], "r", C["muted"]),
        ] for r in rows], sortable=False)
        for n, r in enumerate(rows):
            plays, rating, last = self._info(r)
            self.tracks.setItem(n, 3, star_item(rating))
            if last:
                import time as _t
                self.tracks.item(n, 2).setToolTip(_("last played {date}").format(
                    date=_t.strftime("%Y-%m-%d %H:%M", _t.localtime(last))))
        widths(self.tracks, {0: "num", 2: "plays", 3: "stars", 4: "time"})
        self._paint_rows()

    def _rate(self, row, value):
        if self.win is None or row >= len(self.rows):
            return
        r = self.rows[row]
        self.win.set_rating(r["artist"] or self.album["artist"], r["title"], self.album["album"], value)
        self._fill()

    def _track_menu(self, pos):
        if self.win is None or not self.album:
            return
        row = self.tracks.rowAt(pos.y())
        if row < 0 or row >= len(self.rows):
            return
        r = self.rows[row]
        path = os.path.join(self.album["path"], r["file"])
        menu = QMenu(self)
        menu.addAction(_("Play"), lambda: self._play(row))
        self.win.playlist_menu(menu, lambda: [path])
        rating_menu(menu, self._info(r)[1], lambda v: self._rate(row, v))
        menu.exec(self.tracks.viewport().mapToGlobal(pos))

    def _paint_rows(self):
        playing = os.path.basename(self.playing or "") if self.playing and self.album and \
            os.path.dirname(self.playing) == self.album["path"] else None
        for r, row in enumerate(self.rows):
            on = row["file"] == playing
            for c in range(self.tracks.columnCount()):
                it = self.tracks.item(r, c)
                if it is None:
                    continue
                it.setBackground(QColor(C["accent_soft"]) if on else QColor(0, 0, 0, 0))
                if c == 1:
                    it.setForeground(QColor(C["accent_text"] if on else C["text"]))


STATES = [("all", N_("All"), None), ("A", N_("Active"), N_("Goes to the iPod on the next sync")),
          ("R", N_("Archive"), N_("Stays on disk"))]
# with an iPod connected: what's on it, too
STATES_IPOD = [STATES[0], ("ipod", N_("On iPod"), N_("Albums that are on the connected iPod")), *STATES[1:]]
VIEWS = [("albums", N_("Albums")), ("artists", N_("Artists")), ("genres", N_("Genres"))]


def segmented(items, on_pick, checked=0):
    """b1air's segmented control (widgets.Segmented); exactly one is on.
    items: [(key, text, tip)], texts marked N_()."""
    box = Segmented([(k, _(t), _(tip) if tip else "") for k, t, tip in items], checked)
    box.picked.connect(on_pick)
    return box


def chip(text, tip, on_toggle):
    b = QPushButton(text)
    b.setCheckable(True)
    b.setProperty("chip", True)
    b.setToolTip(tip)
    b.toggled.connect(on_toggle)
    return b


class Attention(QWidget):
    """What needs doing, as chips at the end of the filter row: click one to do it."""

    def __init__(self):
        super().__init__()
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)

    def set_items(self, items):
        """items: [(text, button text, callback)]."""
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for text, action, fn in items:
            b = QPushButton(f"{GLYPHS['dot']}  {text}")
            b.setProperty("chip", "warn")
            b.setToolTip(action)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            self.lay.addWidget(b)
        self.setVisible(bool(items))


class LibraryPage(Page):
    """The library in three views — albums, artists, genres — and the Active / Archive markup."""

    marks_changed = pyqtSignal()
    view_changed = pyqtSignal(str)

    def __init__(self, win):
        super().__init__(win, N_("Albums"))
        self.albums, self.view, self.state, self.search, self.sort = None, "albums", "all", "", 0
        self.genre, self.only_noart = None, False
        self.artist, self.ipod_albums = None, None
        self.model = AlbumModel()
        self.marks = self.model.marks

        # the header: what's shown, the filter by place, sort and size, then the actions
        self.head.removeWidget(self.sub)
        self.head.insertWidget(1, self.sub)
        self.states = segmented(STATES, self._state)
        self.head.insertWidget(2, self.states)
        self.sort_box = QComboBox()
        self.sort_box.addItems([_(n) for n, _key in SORTS])
        self.sort_box.currentIndexChanged.connect(self._sort)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(*UI["tile_range"])
        self.size.setValue(UI["tile"])
        self.size.setFixedWidth(UI["slider_w"])
        self.size.setToolTip(_("Cover size"))
        self.size.valueChanged.connect(self._resize)
        self.add_btn = button("+  " + _("Add tracks"), tip=_("Add new tracks — or drop a folder onto the "
                                                            "window (Ctrl+N)"))
        self.sync_btn = button(GLYPHS["sync"] + "  " + _("Sync"), primary=True,
                               tip=_("Sync the iPod with Active (Ctrl+S)"))
        self.head.insertSpacing(2, 8)              # air between the counts and the filter
        for w in (self.sort_box, self.size, self.add_btn, self.sync_btn):
            self.head.addWidget(w)
        for w in (self.sort_box, self.add_btn, self.sync_btn):
            w.setFixedHeight(UI["control_h"])      # one height along the header, as in b1air

        # second row: filter chips and what needs doing (the genres view has its own tools)
        self.bars = QStackedWidget()
        bar = QWidget()
        bar.setObjectName("chips")
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 10, 20, 4)
        row.setSpacing(8)
        self.genre_box = QComboBox()
        self.genre_box.setProperty("chip", True)
        self.genre_box.setToolTip(_("Only albums of this genre"))
        self.genre_box.currentIndexChanged.connect(self._genre)
        self.noart_chip = chip(_("No cover"), _("Albums with tracks that have no cover art"), self._noart)
        self.attention = Attention()
        row.addWidget(self.genre_box)
        row.addWidget(self.noart_chip)
        row.addWidget(self.attention)
        row.addStretch(1)
        for w in (self.states, self.noart_chip, self.sort_box):
            w.setMinimumWidth(w.sizeHint().width())     # never squeezed into "Al | ti | h"
        self.bars.addWidget(bar)
        self.genres = GenresView(win)
        self.bars.addWidget(self.genres.bar)
        self.bars.setFixedHeight(bar.sizeHint().height())
        self.body.addWidget(self.bars)

        # the selection's actions and the marks not moved yet; hidden when there are neither
        self.marks_bar = QFrame()
        self.marks_bar.setObjectName("marksBar")
        self.marks_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        mrow = QHBoxLayout(self.marks_bar)
        mrow.setContentsMargins(20, 6, 20, 6)
        mrow.setSpacing(8)
        self.sel_label = QLabel()
        self.to_a = button(_("→ Active"), tip=_("Goes to the iPod on the next sync (key A)"))
        self.to_r = button(_("→ Archive"), tip=_("Stays on disk, leaves the iPod (key R)"))
        self.to_a.clicked.connect(lambda: self.mark_selected("A"))
        self.to_r.clicked.connect(lambda: self.mark_selected("R"))
        self.pending = QLabel()
        self.revert_btn = button(_("Revert"))
        self.revert_btn.clicked.connect(self.revert)
        self.save_marks_btn = button(_("Move the files…"), primary=True)
        for w in (self.sel_label, self.to_a, self.to_r):
            mrow.addWidget(w)
        mrow.addStretch(1)
        for w in (self.pending, self.revert_btn, self.save_marks_btn):
            mrow.addWidget(w)
        for w in (self.to_a, self.to_r, self.revert_btn, self.save_marks_btn):
            w.setMinimumWidth(w.sizeHint().width())
        self.body.addWidget(self.marks_bar)

        # albums: artist list (Artists view only) | grid over the opened album
        self.artists = QListWidget()
        self.artists.setObjectName("artists")
        self.artists.setFixedWidth(UI["artists_w"])
        self.artists.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.artists.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.artists.currentItemChanged.connect(self._artist_picked)
        self.grid = Grid()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWrapping(True)
        self.grid.setSpacing(8)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.grid.setModel(self.model)
        self.delegate = AlbumDelegate(self.model, self.grid)
        self.grid.setItemDelegate(self.delegate)
        self.grid.doubleClicked.connect(self.open_album)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._grid_menu)
        self.grid.key_mark.connect(self._key_mark)
        self.grid.key_open.connect(lambda: self.open_album(self.grid.currentIndex()))
        self.grid.key_escape.connect(self._escape)
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self._buttons())
        self.delegate.toggled.connect(self.toggle)
        self.detail = AlbumDetail()
        self.detail.win = win
        self.grid.setDragEnabled(True)
        self.grid.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.detail.closed.connect(self.close_album)
        self.detail.mark.connect(lambda p, s: self.set_marks({p: s}))
        self.detail.hide()
        self.split = QWidget()
        sl = QHBoxLayout(self.split)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        sl.addWidget(self.grid, 1)
        sl.addWidget(self.detail)
        albums_area = QWidget()
        h = QHBoxLayout(albums_area)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self.artists)
        h.addWidget(self.split, 1)
        self.artists.hide()

        # reading / an empty library / nothing matches the filters
        self.empty = QWidget()
        self.empty.setObjectName("empty")
        ev = QVBoxLayout(self.empty)
        ev.addStretch(1)
        self.empty_title = QLabel()
        self.empty_title.setObjectName("emptyTitle")
        self.loading = muted(_("Reading the library…"), wrap=True)
        for lab in (self.empty_title, self.loading):
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ev.addWidget(lab)
        acts = QHBoxLayout()
        acts.addStretch(1)
        self.empty_add = button(_("Add tracks…"), primary=True)
        self.empty_add.clicked.connect(lambda: win.open_tool("incoming"))
        self.empty_settings = button(_("Settings…"))
        self.empty_settings.clicked.connect(lambda: win.navigate("settings"))
        self.empty_clear = button(_("Clear the filters"))
        self.empty_clear.clicked.connect(self.clear_filters)
        for b in (self.empty_add, self.empty_settings, self.empty_clear):
            acts.addWidget(b)
        acts.addStretch(1)
        ev.addSpacing(8)
        ev.addLayout(acts)
        ev.addStretch(2)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(albums_area)
        self.stack.addWidget(self.genres)
        self.body.addWidget(self.stack, 1)
        self._buttons()

    # --- views

    def set_view(self, view):
        """Albums, artists or genres; False if the genres view has edits that weren't let go."""
        if view == self.view:
            return True
        if self.view == "genres" and not self.genres.can_leave():
            return False
        self.view = view
        self.title.setText(_(dict(VIEWS)[view]))
        self.view_changed.emit(view)
        for w in (self.states, self.sort_box, self.size):
            w.setVisible(view != "genres")
        self.artists.setVisible(view == "artists")
        self.bars.setCurrentIndex(1 if view == "genres" else 0)
        self._show_body()
        self._buttons()
        if view == "genres":
            self.genres.shown()
        else:
            self.refresh()
        return True

    def set_ipod(self, info):
        """The connected iPod (or None): the place filter offers "On iPod" while there's one.
        An album is on it when one of the iPod's tracks has its artist (or, under old glued
        tags, its first artist) and album."""
        self.ipod_albums = None if info is None else {
            (norm(name), norm(t["album"])) for t in info["tracks"]
            for name in (t["artist"], first_artist(t["artist"]))}
        items = STATES_IPOD if info is not None else STATES
        keys = [k for k, _t, _tip in items]
        if self.state not in keys:
            self.state = "all"
        new = segmented(items, self._state, keys.index(self.state))
        new.setVisible(self.view != "genres")
        self.head.replaceWidget(self.states, new)
        self.states.deleteLater()
        self.states = new
        self.refresh()

    def open_path(self, path):
        """Open the album in this folder, if it's in the grid."""
        row = self.model.row_of.get(path)
        if row is not None:
            index = self.model.index(row)
            self.grid.setCurrentIndex(index)
            self.open_album(index)

    def _show_body(self):
        if self.view == "genres":
            self.stack.setCurrentIndex(2)
            return
        bare = self.albums == []
        nothing = bool(self.albums) and not self.model.rowCount() and self.artist is None
        if bare:
            lib = settings.library_paths(backend.cfg() or {})
            self.empty_title.setText(_("No albums yet"))
            self.loading.setText(_("PodVault looks for albums in {active} (goes to the iPod) and "
                                   "{archive} (stays on disk). Add tracks, or drop a folder "
                                   "onto the window.").format(
                active=lib[1].replace(os.path.expanduser("~"), "~", 1),
                archive=lib[2].replace(os.path.expanduser("~"), "~", 1)))
        elif nothing:
            self.empty_title.setText(_("Nothing matches"))
            self.loading.setText(_("No album passes the search and the filters."))
        elif self.albums is not None:
            self.empty_title.setText("")
        self.empty_title.setVisible(bare or nothing)
        self.empty_add.setVisible(bare)
        self.empty_settings.setVisible(bare)
        self.empty_clear.setVisible(nothing)
        self.stack.setCurrentIndex(0 if self.albums is None or bare or nothing else 1)

    def clear_filters(self):
        self.win.search.clear()
        self.states.select("all")
        self.genre_box.setCurrentIndex(0)
        self.noart_chip.setChecked(False)

    def shown(self):
        if self.view == "genres":
            self.genres.shown()

    def can_leave(self):
        return self.genres.can_leave() if self.view == "genres" else True

    # --- data

    def set_albums(self, albums, error=None):
        self.albums = albums
        if albums is None:
            self.loading.setText(error or _("Reading the library…"))
        else:
            known = {a["path"] for a in albums}
            for p in [p for p in self.marks if p not in known]:
                del self.marks[p]
            if self.detail.album and self.detail.album["path"] not in known:
                self.close_album()
            self._fill_genres(albums)
            self.refresh()
            self.marks_changed.emit()
        if albums is None:
            self.empty_title.hide()
            for b in (self.empty_add, self.empty_settings, self.empty_clear):
                b.hide()
            self.stack.setCurrentIndex(0)
        else:
            self._show_body()

    def set_search(self, text):
        self.search = text.strip()
        self.genres.set_search(text)
        self.refresh()

    def _state(self, key):
        self.state = key
        self.refresh()

    def _fill_genres(self, albums):
        """The genres the library has, most albums first; the current pick survives a re-read."""
        counts = Counter(a["genre"] for a in albums if a["genre"])
        self.genre_box.blockSignals(True)
        self.genre_box.clear()
        self.genre_box.addItem(_("All genres"), None)
        for g in sorted(counts, key=lambda g: (-counts[g], g.lower())):
            self.genre_box.addItem(f"{g}  ({counts[g]})", g)
        # filled after the window is shown, so Qt's own sizing never sees the items:
        # as wide as the longest entry plus the arrow, within reason
        fm = self.genre_box.fontMetrics()
        longest = max(fm.horizontalAdvance(self.genre_box.itemText(i))
                      for i in range(self.genre_box.count()))
        self.genre_box.setFixedWidth(min(longest + UI["combo_extra_w"], UI["genre_box_max_w"]))
        i = self.genre_box.findData(self.genre)
        self.genre_box.setCurrentIndex(max(i, 0))
        self.genre = self.genre_box.currentData()
        self.genre_box.blockSignals(False)

    def _genre(self, i):
        self.genre = self.genre_box.itemData(i)
        self.refresh()

    def _noart(self, on):
        self.only_noart = on
        self.refresh()

    def _filtered(self):
        out = []
        for a in self.albums or []:
            s = self.model.state(a)
            if self.state == "ipod":
                if not self.ipod_albums or (norm(a["artist"]), norm(a["album"])) not in self.ipod_albums:
                    continue
            elif self.state != "all" and s != self.state:
                continue
            if self.genre is not None and a["genre"] != self.genre:
                continue
            if self.only_noart and not a["no_art"]:
                continue
            if not matches(self.search, a["artist"], a["album"], a["genre"], a["year"]):
                continue
            out.append(a)
        return out

    def refresh(self):
        if self.albums is None or self.view == "genres":
            return
        rows = self._filtered()
        if self.view == "artists":
            self._fill_artists(rows)
            if self.artist is not None:
                rows = [a for a in rows if a["artist"] == self.artist]
        rows.sort(key=SORTS[self.sort][1])
        self.model.set_rows(rows)
        if self.detail.album and self.detail.album["path"] not in self.model.row_of:
            self.close_album()          # its album was filtered away
        tracks = sum(a["tracks"] for a in rows)
        size = sum(a["bytes"] for a in rows) / 1024 ** 3
        self.sub.setText(f"{plural(len(rows), '{n} album', '{n} albums')} · "
                         f"{plural(tracks, '{n} track', '{n} tracks')} · {fmt_gb(size)}")
        self._buttons()
        self._show_body()

    def _fill_artists(self, rows):
        counts = {}
        for a in rows:
            counts[a["artist"]] = counts.get(a["artist"], 0) + 1
        self.artists.blockSignals(True)
        self.artists.clear()
        every = QListWidgetItem(_("All artists  ({n})").format(n=len(counts)))
        every.setData(Qt.ItemDataRole.UserRole, None)
        self.artists.addItem(every)
        current = every
        for name in sorted(counts, key=str.lower):
            it = QListWidgetItem(f"{name}  ({counts[name]})")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.artists.addItem(it)
            if name == self.artist:
                current = it
        if current is every:
            self.artist = None
        self.artists.setCurrentItem(current)
        self.artists.blockSignals(False)

    def _artist_picked(self, item, _prev):
        self.artist = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.refresh()

    def _sort(self, i):
        self.sort = i
        self.refresh()

    def _resize(self, v):
        self.delegate.tile = v
        self.model.layoutChanged.emit()

    # --- marking

    def selected(self):
        return [self.model.album(i) for i in self.grid.selectionModel().selectedIndexes()]

    def _buttons(self):
        n = len(self.grid.selectionModel().selectedIndexes()) if self.grid.selectionModel() else 0
        self.sel_label.setText(plural(n, "{n} selected", "{n} selected"))
        for w in (self.sel_label, self.to_a, self.to_r):
            w.setVisible(n > 0)
        self.pending.setText(self.pending_text())
        for w in (self.pending, self.revert_btn, self.save_marks_btn):
            w.setVisible(bool(self.marks))
        self.marks_bar.setVisible(self.view != "genres" and (n > 0 or bool(self.marks)))

    def pending_text(self):
        to_a, to_r = self.summary()
        parts = ([_("{n} → Archive").format(n=len(to_r))] if to_r else []) + \
                ([_("{n} → Active").format(n=len(to_a))] if to_a else [])
        return _("Not moved yet: {changes}").format(changes=", ".join(parts)) if parts else ""

    def set_marks(self, wanted):
        """{path: 'A'|'R'}: pending moves; a mark equal to the album's place is dropped."""
        by_path = {a["path"]: a for a in self.albums or []}
        for path, state in wanted.items():
            a = by_path.get(path)
            if a is None:
                continue
            if state == a["state"]:
                self.marks.pop(path, None)
            else:
                self.marks[path] = state
            self.detail.set_state(state, path)
        self.model.changed(wanted.keys())
        self._buttons()
        self.marks_changed.emit()

    def mark_selected(self, state):
        sel = self.selected()
        if sel:
            self.set_marks({a["path"]: state for a in sel})

    def toggle(self, path):
        a = next((x for x in self.albums or [] if x["path"] == path), None)
        if a:
            self.set_marks({path: "R" if self.model.state(a) == "A" else "A"})

    def _key_mark(self, what):
        sel = self.selected()
        if not sel:
            return
        if what == "toggle":
            self.set_marks({a["path"]: "R" if self.model.state(a) == "A" else "A" for a in sel})
        else:
            self.mark_selected(what)
            i = self.grid.currentIndex()
            if len(sel) == 1 and i.isValid() and i.row() + 1 < self.model.rowCount():
                self.grid.setCurrentIndex(self.model.index(i.row() + 1))

    def _grid_menu(self, pos):
        index = self.grid.indexAt(pos)
        if not index.isValid():
            return
        if not self.grid.selectionModel().isSelected(index):     # a click outside the selection: just this one
            self.grid.setCurrentIndex(index)
        sel = self.selected()
        one = sel[0] if len(sel) == 1 else None
        menu = QMenu(self)
        if one:
            menu.addAction(_("Open"), lambda: self.open_album(index))
            menu.addSeparator()
        states = {self.model.state(a) for a in sel}
        if states != {"A"}:
            menu.addAction(_("→ Active"), lambda: self.mark_selected("A"))
        if states != {"R"}:
            menu.addAction(_("→ Archive"), lambda: self.mark_selected("R"))
        menu.addSeparator()
        self.win.playlist_menu(menu, lambda: [f for a in sel for f in backend.album_files(a["path"])])
        if one:
            menu.addSeparator()
            menu.addAction(_("Show in folder"), lambda: self._show_folder(one))
        if any(a["no_art"] for a in sel):
            menu.addAction(_("Find covers…"), lambda: self.win.open_tool("covers"))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def _show_folder(self, a):
        try:
            backend.open_in_explorer(a["path"])
        except ValueError as e:
            inform(self, _("Show in folder"), str(e), bad=True)

    def revert(self):
        paths = list(self.marks)
        self.marks.clear()
        self.model.changed(paths)
        self._buttons()
        if self.detail.album:
            self.detail.set_state(self.detail.album["state"], self.detail.album["path"])
        self.marks_changed.emit()
        self.refresh()

    def summary(self):
        by_path = {a["path"]: a for a in self.albums or []}
        to_r = [by_path[p] for p, s in self.marks.items() if s == "R" and p in by_path]
        to_a = [by_path[p] for p, s in self.marks.items() if s == "A" and p in by_path]
        return to_a, to_r

    # --- an opened album

    def open_album(self, index):
        if not index.isValid():
            return
        a = self.model.album(index)
        self.detail.show_album(a, self.model.state(a))
        self.detail.set_playing(self.win.queue[self.win.queue_at]["path"] if self.win.queue else None)
        self.detail.show()
        self.grid.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    def close_album(self):
        self.detail.album = None
        self.detail.hide()
        self.grid.setFocus()

    def _escape(self):
        if self.detail.isVisible():
            self.close_album()
        else:
            self.grid.clearSelection()


# ================================================================ iPod


IPOD_FILTERS = [(N_("All"), lambda t: True),
                (N_("Not in the library"), lambda t: t["state"] == ""),
                (N_("Archive (will leave)"), lambda t: t["state"] == "R"),
                (N_("Without a cover"), lambda t: not t["cover"])]


class IpodPage(Page):
    def __init__(self, win):
        super().__init__(win, N_("iPod"))
        self.data, self.search, self.flt = None, "", 0
        # the header: the iPod's own actions; the rare ones under ⋯
        self.refresh_btn = button("⋯", tip=_("More"))
        more = QMenu(self)
        more.addAction(_("Read it again"), lambda: self.load(force=True))
        more.addSeparator()
        more.addAction(_("Save tracks only on the iPod…"), lambda: self.win.run_tool("rescue"))
        more.addAction(_("Mirror to disk…"), self._disk)
        more.addSeparator()
        more.addAction(_("Restore last backup…"), self._restore)
        self.refresh_btn.setMenu(more)
        eject = button(_("Eject"))
        eject.clicked.connect(self.win.eject)
        self.sync = button(GLYPHS["sync"] + "  " + _("Sync now"), primary=True,
                           tip=_("Make the iPod hold exactly what's in Active"))
        self.sync.clicked.connect(lambda: self.win.run_tool("sync"))
        for b in (self.refresh_btn, eject, self.sync):
            self.head.addWidget(b)

        self.stack = QStackedWidget()
        say = QWidget()
        say_col = QVBoxLayout(say)
        self.message = muted("", wrap=True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.identify_btn = button(_("Identify the iPod…"), primary=True)
        self.identify_btn.clicked.connect(self._identify)
        self.identify_btn.hide()
        say_col.addStretch(1)
        say_col.addWidget(self.message)
        say_col.addSpacing(12)
        say_col.addWidget(self.identify_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        say_col.addStretch(1)
        self.stack.addWidget(say)

        content = QWidget()
        content.setObjectName("page")
        col = QVBoxLayout(content)
        col.setContentsMargins(24, 20, 24, 12)
        col.setSpacing(16)
        # how full it is
        space = QFrame()
        space.setObjectName("card")
        space.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sl0 = QVBoxLayout(space)
        sl0.setContentsMargins(18, 14, 18, 14)
        sl0.setSpacing(8)
        top = QHBoxLayout()
        head = QLabel(_("Space"))
        head.setObjectName("h2")
        self.space_text = muted("", wrap=False)
        top.addWidget(head)
        top.addWidget(self.space_text)
        top.addStretch(1)
        sl0.addLayout(top)
        self.cap = CapacityBar()
        sl0.addWidget(self.cap)
        col.addWidget(space)
        # the numbers that matter, each a tile
        tiles = QHBoxLayout()
        tiles.setSpacing(16)
        self.tiles = {}
        for key, text in (("tracks", _("tracks on the iPod")), ("nocover", _("without a cover")),
                          ("missing", _("not in the library")), ("archive", _("from Archive, to leave"))):
            t = QFrame()
            t.setObjectName("card")
            t.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            tl = QVBoxLayout(t)
            tl.setContentsMargins(18, 12, 18, 12)
            tl.setSpacing(2)
            n = QLabel("—")
            n.setObjectName("metric")
            tl.addWidget(n)
            tl.addWidget(muted(text, wrap=False))
            tiles.addWidget(t, 1)
            self.tiles[key] = n
        col.addLayout(tiles)
        self.name = QLabel()            # model, serial, software: one quiet line
        self.name.setProperty("muted", True)
        self.name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.name)

        self.tabs = QTabWidget()
        songs = QWidget()
        sl = QVBoxLayout(songs)
        sl.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self.flt_box = QComboBox()
        self.flt_box.addItems([_(n) for n, _pred in IPOD_FILTERS])
        self.flt_box.currentIndexChanged.connect(self._filter)
        self.count = muted("", wrap=False)
        self.count.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.flt_box)
        row.addWidget(self.count, 1)
        legend = muted("", wrap=False)
        legend.setText(f'<span style="color:{C["active"]}">{GLYPHS["dot"]}</span> {_("in Active")} &nbsp; '
                       f'<span style="color:{C["archive"]}">{GLYPHS["dot"]}</span> {_("in Archive")} &nbsp; '
                       f'{GLYPHS["ring"]} {_("not in the library")}')
        row.addWidget(legend)
        sl.addLayout(row)
        self.songs = table(["", N_("Name"), N_("Artist"), N_("Album"), N_("Genre"), N_("Time"), N_("Plays"),
                            N_("Cover")], stretch=1)
        self.songs.setSortingEnabled(True)
        sl.addWidget(self.songs)
        self.tabs.addTab(songs, _("Music"))
        lists_tab = QWidget()
        ll = QVBoxLayout(lists_tab)
        ll.setContentsMargins(0, 8, 0, 0)
        lrow = QHBoxLayout()
        lrow.addWidget(muted(_("Playlists made on the iPod or in iTunes can be brought into PodVault "
                               "and edited here; from then on every sync keeps the iPod's copy in line."),
                             wrap=True), 1)
        self.bring = button(_("Bring into PodVault"))
        self.bring.clicked.connect(self._bring)
        new_pl = button("+  " + _("New playlist on the iPod…"), primary=True)
        new_pl.clicked.connect(lambda: self.win.new_playlist(keep_on_ipod=True))
        lrow.addWidget(self.bring)
        lrow.addWidget(new_pl)
        ll.addLayout(lrow)
        self.lists = table([N_("Playlist"), N_("Tracks")], stretch=0)
        self.lists.cellDoubleClicked.connect(lambda r, c: self._bring())
        ll.addWidget(self.lists)
        self.tabs.addTab(lists_tab, _("Playlists"))
        col.addWidget(self.tabs, 1)
        self.stack.addWidget(content)
        self.body.addWidget(self.stack)
        self.loading = False

    def shown(self):
        self.load()

    def load(self, force=False):
        if self.loading:
            return
        if not self.win.ipods:
            self.data = None
            self._say(_("No iPod connected.\n\nConnect it with the cable; it shows up here on its own."))
            return
        if self.data is not None and not force:
            return
        if self.win.jobs.busy():
            self._say(_("“{tool}” is running.\n"
                        "The iPod is read again when it's done.").format(tool=self.win.jobs.current.base_title))
            return
        self.loading = True
        self.refresh_btn.setEnabled(False)
        self._say(_("Reading the iPod…"))
        run_async(backend.in_child, "ipod_read", ok=self._loaded, failed=self._failed)

    def invalidate(self):
        self.data = None
        if self.isVisible():
            self.load()

    def _say(self, text, identify=False):
        self.message.setText(text)
        self.identify_btn.setVisible(identify)
        self.stack.setCurrentIndex(0)

    def _failed(self, msg):
        self.loading = False
        self.refresh_btn.setEnabled(True)
        if msg == backend.unidentified_text():
            self._say(msg, identify=True)
        else:
            self._say(_("Could not read the iPod.\n\n{error}").format(error=msg))

    def _identify(self):
        if not self.win.ipods:
            return
        self.loading = True
        self.refresh_btn.setEnabled(False)
        self.identify_btn.setVisible(False)
        # the iPod drops off for a few seconds on purpose: the window mustn't take that for
        # an unplug (leave this page, read the iPod again) — see Window._polled
        self.win.identifying = True
        self._say(_("Identifying the iPod… It disconnects for a few seconds, then PodVault "
                    "writes its model to the iPod. Don't unplug it."))
        run_async(backend.identify_ipod, self.win.ipods[0], ok=self._identified,
                  failed=lambda m: self._identified(m or "?"))

    def _identified(self, error):
        self.loading = False
        self.refresh_btn.setEnabled(True)
        self.win.identifying = False
        if error:
            self._say(backend.unidentified_text() + "\n\n" + error, identify=True)
            self.win._poll_ipod()
        else:
            self.win.ipods = []             # the next poll reads it afresh, now with its model
            self.win._poll_ipod()

    def _loaded(self, d, compare=True):
        self.loading = False
        self.refresh_btn.setEnabled(True)
        self.data = d
        self.win.ipod_read(d)
        self.title.setText(d["name"])
        self.sub.setText(f"{d['model'] or 'iPod'} · {fmt_gb(d['capacity_gb'])} · {d['path']}")
        tracks = d["tracks"]
        self.name.setText("   ·   ".join(f"{k} {v}" for k, v in (
            (_("Model"), d["model_number"] or "—"), (_("Serial number"), d["serial"] or "—"),
            (_("Software"), d["firmware"] or "—"))))
        comparing = any(t["state"] == "?" for t in tracks)
        self.tiles["tracks"].setText(str(len(tracks)))
        self.tiles["tracks"].setToolTip(fmt_total(sum(t["length"] for t in tracks)))
        self.tiles["nocover"].setText(str(sum(1 for t in tracks if not t["cover"])))
        self.tiles["missing"].setText("…" if comparing else str(sum(1 for t in tracks if t["state"] == "")))
        self.tiles["archive"].setText("…" if comparing else str(sum(1 for t in tracks if t["state"] == "R")))
        self.space_text.setText(_("{used} of {total} used · {free} free").format(
            used=fmt_gb(d["capacity_gb"] - d["free_gb"]), total=fmt_gb(d["capacity_gb"]),
            free=fmt_gb(d["free_gb"])))
        self.cap.set_values(d["capacity_gb"], d["music_gb"], d["free_gb"])
        on_device = {p["title"] for p in d["playlists"]}
        waiting = [n for n in saved_lists.lists(backend.cfg())["keep"] if n not in on_device]
        fill(self.lists, [[p["title"] + (_("  (folder)") if p["folder"] else ""),
                           (p["count"], p["count"], "r")] for p in d["playlists"]]
             + [[(n + "  " + _("(PodVault — goes there on the next sync)"), None, None, C["muted"]),
                 ("", 0, "r")] for n in waiting])
        widths(self.lists, {1: "count"})
        self._render()
        self.stack.setCurrentIndex(1)
        if compare:
            self.win.note(_("iPod read"), _("{n} tracks; comparing with the library…").format(n=len(tracks)))
            self._compare(d)

    def _compare(self, d):
        """Which iPod tracks are in Active / Archive: goes by the library index, so it waits
        for the library check that keeps the index up to date."""
        if self.win.scanning:
            self.count.setText(_("Waiting for the library check to finish…"))
            QTimer.singleShot(2000, lambda: self._compare(d) if self.data is d else None)
            return

        def done(states):
            if self.data is not d:       # read again meanwhile: this answer is for the old one
                return
            for t, s in zip(d["tracks"], states):
                t["state"] = s
            self._loaded(d, compare=False)
            missing = sum(1 for s in states if s == "")
            self.win.note(_("iPod compared with the library"),
                          _("{n} tracks aren't in the library").format(n=missing) if missing
                          else _("every track is in the library"))

        def failed(msg):
            if self.data is d:
                self.count.setText(_("Couldn't compare with the library: {error}").format(error=msg))

        self.count.setText(_("Comparing with the library…"))
        run_async(backend.in_child, "ipod_states", d["tracks"], ok=done, failed=failed)

    def _filter(self, i):
        self.flt = i
        self._render()

    def set_search(self, text):
        self.search = text.strip()
        if self.data:
            self._render()

    def _render(self):
        pred = IPOD_FILTERS[self.flt][1]
        rows = [t for t in self.data["tracks"]
                if pred(t) and matches(self.search, t["title"], t["artist"], t["album"], t["genre"])]
        dots = {"A": (GLYPHS["dot"], C["active"]), "R": (GLYPHS["dot"], C["archive"]),
                "": (GLYPHS["ring"], C["faint"]),
                "?": ("", None)}
        fill(self.songs, [[
            (dots[t["state"]][0], t["state"], "c", dots[t["state"]][1]),
            t["title"], t["artist"], t["album"], t["genre"],
            (fmt_time(t["length"]), t["length"], "r"),
            (t["plays"] or "", t["plays"], "r"),
            (GLYPHS["yes"] if t["cover"] else _("none"), t["cover"], "c",
             C["active"] if t["cover"] else C["danger"]),
        ] for t in rows])
        widths(self.songs, {0: "state", 2: "artist", 3: "album", 4: "genre", 5: "time",
                            6: "plays", 7: "cover"})
        self.count.setText(_("{shown} of {total}").format(shown=len(rows), total=len(self.data['tracks'])))

    def _bring(self):
        row = self.lists.currentRow()
        if row < 0 or not self.data:
            inform(self, _("Bring into PodVault"), _("Pick a playlist in the list first."))
            return
        title = self.lists.item(row, 0).text().replace(_("  (folder)"), "")
        self.bring.setEnabled(False)

        def done(result):
            self.bring.setEnabled(True)
            path, found, total = result
            self.win.reload_playlists()
            self.win.toast(_("“{name}”: {found} of {total} tracks found in the library. "
                             "Kept on the iPod from now on.").format(name=title, found=found, total=total),
                           good=found == total, action=lambda: self.win.navigate(f"playlist:{path}"))

        def failed(msg):
            self.bring.setEnabled(True)
            inform(self, _("Bring into PodVault"), msg, bad=True)
        run_async(backend.in_child, "save_ipod_playlist", title, ok=done, failed=failed)

    def _restore(self):
        if ask(self, _("Restore the iPod's database"),
               _("Put back the iPod's database as it was before the last write?\n\n"
                 "Tracks added since then vanish from the list (their files stay until the next "
                 "sync); tracks deleted since then come back only if their files are still on the iPod."),
               yes=_("Restore"), danger=True):
            self.win.run_tool("restore")

    def _disk(self):
        drive, ok = QInputDialog.getText(self, _("Mirror to disk"),
                                         _("The Rockbox / disk-mode device — a drive like E: or a mount "
                                           "point like /Volumes/NAME:"))
        if ok and drive.strip():
            self.win.run_tool("disk", {"drive": drive.strip()})


# ================================================================ playlists


class PlaylistPage(Page):
    """A saved playlist: play it, put its tracks in order, take some out, keep it on the iPod."""

    def __init__(self, win):
        super().__init__(win, N_("Playlist"))
        self.file, self.rows, self.search = None, [], ""
        self.play_btn = button(GLYPHS["play"] + "  " + _("Play"), primary=True)
        self.play_btn.clicked.connect(lambda: self._play(0))
        self.keep = button(GLYPHS["ipod"] + "  " + _("On the iPod"),
                           tip=_("Kept on the iPod: every sync makes the iPod's copy match this one"))
        self.keep.setCheckable(True)
        self.keep.clicked.connect(self._keep)
        self.edit_btn = button(GLYPHS.get("edit", "✎") + "  " + _("Edit playlist"))
        self.edit_btn.setCheckable(True)
        self.edit_btn.setToolTip(_("Turn on editing to remove or reorder tracks"))
        self.edit_btn.toggled.connect(self._set_editing)
        more = button("⋯", tip=_("More"))
        menu = QMenu(self)
        menu.addAction(_("Rename…"), self._rename)
        menu.addAction(_("Duplicate"), self._duplicate)
        menu.addSeparator()
        menu.addAction(_("Delete…"), self._delete)
        more.setMenu(menu)
        self.sync_btn = button(GLYPHS["sync"] + "  " + _("Sync"), tip=_("Put it on the iPod now"))
        self.sync_btn.clicked.connect(lambda: self.win.run_tool("sync"))
        for b in (self.play_btn, self.edit_btn, self.keep, self.sync_btn, more):
            self.head.addWidget(b)
        self.empty = muted(_("Empty for now. Drag albums onto this playlist in the sidebar, or right-click an "
                             "album or one of its tracks → Add to playlist."), wrap=True)
        self.empty.setContentsMargins(20, 16, 20, 0)
        self.body.addWidget(self.empty)
        self.table = table(["#", N_("Name"), N_("Artist"), N_("Album"), N_("Plays"), N_("Rating"), N_("Time")],
                           stretch=1)
        self.table.setSortingEnabled(False)          # the order is the playlist's
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.cellDoubleClicked.connect(lambda row, col: col != 5 and self._play(self._index(row)))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        StarClicks(self.table, 5, lambda row, v: self._rate(self._index(row), v))
        self.table.installEventFilter(self)
        self.body.addWidget(self.table)

    # --- showing

    def show_playlist(self, file):
        self.file = file
        self.edit_btn.setChecked(False)
        self.title.setText(os.path.splitext(os.path.basename(file))[0])
        self.sub.setText(_("reading…"))
        self.keep.setChecked(saved_lists.is_kept(backend.cfg(), self.title.text()))
        run_async(backend.playlist_tracks, file, ok=lambda rows: self._loaded(file, rows),
                  failed=lambda m: self.sub.setText(_("Could not read it: {error}").format(error=m)))

    def _loaded(self, file, rows):
        if file != self.file:
            return
        self.rows = rows
        self._render()

    def set_search(self, text):
        self.search = text.strip()
        self._render()

    def _visible(self):
        return [n for n, r in enumerate(self.rows)
                if matches(self.search, r["title"], r["artist"], r["album"], r["style"])]

    def _index(self, table_row):
        shown = self._visible()
        return shown[table_row] if 0 <= table_row < len(shown) else -1

    def _render(self):
        self.empty.setVisible(not self.rows)
        self.sync_btn.setVisible(self.keep.isChecked() and bool(self.win.ipods))
        gone = sum(1 for r in self.rows if not r["exists"])
        self.sub.setText(f"{plural(len(self.rows), '{n} track', '{n} tracks')} · "
                         f"{fmt_total(sum(r['length'] for r in self.rows))}"
                         + (" · " + plural(gone, "{n} file missing", "{n} files missing") if gone else ""))
        shown = self._visible()
        info = [trackstats.info(self.win.stats, self.win.rated, r["artist"], r["title"], r["album"])
                for r in self.rows]
        fill(self.table, [[(n + 1, n, "r", C["faint"]),
                           (self.rows[n]["title"], None, None, None if self.rows[n]["exists"] else C["danger"]),
                           self.rows[n]["artist"], self.rows[n]["album"],
                           (info[n][0] or "", info[n][0], "r", C["muted"]), "",
                           (fmt_time(self.rows[n]["length"]), self.rows[n]["length"], "r", C["muted"])]
                          for n in shown], sortable=False)
        for row, n in enumerate(shown):
            self.table.setItem(row, 5, star_item(info[n][1]))
        widths(self.table, {0: "num", 2: "artist", 3: "album", 4: "plays", 5: "stars", 6: "time"})

    # --- playing and rating

    def _play(self, at):
        rows = [r for r in self.rows if r["exists"]]
        if not rows or at < 0:
            return
        at = sum(1 for r in self.rows[:at] if r["exists"])
        self.win.play([{"path": r["path"], "title": r["title"], "artist": r["artist"], "album": r["album"],
                        "dir": os.path.dirname(r["path"])} for r in rows], min(at, len(rows) - 1))

    def _rate(self, n, value):
        if 0 <= n < len(self.rows):
            r = self.rows[n]
            self.win.set_rating(r["artist"], r["title"], r["album"], value)
            self._render()

    # --- editing: order, removal; saved at once

    def _set_editing(self, on):
        self.edit_btn.setText((GLYPHS.get("edit", "✎") + "  " + _("Done editing")) if on else
                              (GLYPHS.get("edit", "✎") + "  " + _("Edit playlist")))
        self.table.setToolTip(_("Editing is on: select tracks, then use Delete or Alt+↑/↓ to change the list.")
                              if on else _("To change this playlist, click Edit playlist."))
        if not on:
            self.table.clearSelection()

    def _save(self):
        saved_lists.write(self.file, [r["path"] for r in self.rows])
        self._render()

    def _selected(self):
        return sorted({self._index(i.row()) for i in self.table.selectionModel().selectedRows()} - {-1})

    def _remove(self):
        if not self.edit_btn.isChecked():
            return
        picked = set(self._selected())
        if picked:
            self.rows = [r for n, r in enumerate(self.rows) if n not in picked]
            self._save()

    def _move(self, step):
        if not self.edit_btn.isChecked():
            return
        picked = self._selected()
        if not picked or self.search:            # moving within a filtered view would be guesswork
            return
        if (step < 0 and picked[0] == 0) or (step > 0 and picked[-1] == len(self.rows) - 1):
            return
        for n in (picked if step < 0 else reversed(picked)):
            self.rows[n + step], self.rows[n] = self.rows[n], self.rows[n + step]
        self._save()
        self.table.clearSelection()
        for n in picked:
            self.table.selectRow(n + step)

    def eventFilter(self, obj, e):
        if obj is self.table and e.type() == QEvent.Type.KeyPress:
            if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._remove()
                return True
            if e.modifiers() & Qt.KeyboardModifier.AltModifier and e.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self._move(-1 if e.key() == Qt.Key.Key_Up else 1)
                return True
        return False

    def _menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        if not self.table.selectionModel().isRowSelected(row):
            self.table.selectRow(row)
        n = self._index(row)
        menu = QMenu(self)
        menu.addAction(_("Play from here"), lambda: self._play(n))
        menu.addSeparator()
        if self.edit_btn.isChecked():
            menu.addSeparator()
            up = menu.addAction(_("Move up") + "\tAlt+↑", lambda: self._move(-1))
            down = menu.addAction(_("Move down") + "\tAlt+↓", lambda: self._move(1))
            for act in (up, down):
                act.setEnabled(not self.search)
            menu.addAction(_("Remove from the playlist") + "\tDel", self._remove)
        menu.addSeparator()
        r = self.rows[n]
        rating_menu(menu, trackstats.info(self.win.stats, self.win.rated, r["artist"], r["title"], r["album"])[1],
                    lambda v: self._rate(n, v))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # --- the file

    def _keep(self, on):
        saved_lists.keep_on_ipod(backend.cfg(), self.title.text(), on)
        self.sync_btn.setVisible(on and bool(self.win.ipods))
        if on and self.win.ipods:
            self.win.toast(_("Kept on the iPod: the next sync puts it there.\nClick to sync now."),
                           good=True, action=lambda: self.win.run_tool("sync"))
        elif not on:
            self.win.toast(_("No longer kept: the next sync takes it off the iPod."))

    def _rename(self):
        name, ok = QInputDialog.getText(self, _("Rename playlist"), _("Name"), text=self.title.text())
        if not ok or not name.strip() or name.strip() == self.title.text():
            return
        try:
            new = saved_lists.rename(backend.cfg(), backend.playlist_file(self.file), name.strip())
        except (OSError, ValueError) as e:
            inform(self, _("Rename playlist"), str(e), bad=True)
            return
        self.win.reload_playlists()
        self.win.navigate(f"playlist:{new}")

    def _duplicate(self):
        new = saved_lists.new(backend.cfg(), _("{name} copy").format(name=self.title.text()),
                              [r["path"] for r in self.rows])
        self.win.reload_playlists()
        self.win.navigate(f"playlist:{new}")

    def _delete(self):
        if not self.file:
            return
        name = self.title.text()
        if not ask(self, _("Delete playlist"), _("Delete the saved playlist “{name}”?\n\n"
                                                 "If it's kept on the iPod, the next sync takes it off "
                                                 "there too.").format(name=name),
                   yes=_("Delete"), danger=True):
            return
        try:
            os.remove(backend.playlist_file(self.file))
        except (OSError, ValueError) as e:
            inform(self, _("Delete playlist"), str(e), bad=True)
            return
        saved_lists.keep_on_ipod(backend.cfg(), name, False)
        self.file = None
        self.win.reload_playlists()
        self.win.navigate("albums")


class VibePage(Page):
    EXAMPLES = [N_("rainy night drive, slow, a bit sad"), N_("loud and fast for the gym"),
                N_("morning coffee, something light"), N_("a late-night walk through the city")]

    def __init__(self, win):
        super().__init__(win, N_("New vibe playlist"))
        col = self.scroll_body()
        card = Card(_("Describe the vibe"),
                    _("In your own words, in any language. The AI picks and orders tracks from Active, "
                      "using what it knows about the songs plus tempo and energy measured from the "
                      "audio. The first run analyses the whole library (a few minutes); after that "
                      "only new tracks."))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText(_("e.g. {example}").format(example=_(self.EXAMPLES[0])))
        self.text.setFixedHeight(UI["vibe_text_h"])
        card.lay.addWidget(self.text)
        chips = QGridLayout()          # two by two: fits a narrow window
        for n, ex in enumerate(map(_, self.EXAMPLES)):
            b = button(ex)
            b.clicked.connect(lambda _, t=ex: self.text.setPlainText(t))
            chips.addWidget(b, n // 2, n % 2)
        chips.setColumnStretch(2, 1)
        card.lay.addLayout(chips)
        self.count = QSpinBox()
        # the styled frame leaves the native up/down arrows as specks: type, wheel or ↑↓
        self.count.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.count.setRange(5, 200)
        go = button(_("Make the playlist"), primary=True)
        go.clicked.connect(self._go)
        card.row(QLabel(_("About")), self.count, QLabel(_("tracks")), go)
        col.addWidget(card)
        more = Card(_("Before the first playlist"),
                    _("Analysing the library ahead of time makes the first pick quick."))
        analyse = button(_("Analyse the library"))
        analyse.clicked.connect(lambda: self.win.run_tool("vibe_analyze"))
        more.row(analyse)
        col.addWidget(more)
        how = Card(_("Which AI"), _("Settings → AI: Claude Code on a Claude subscription, Google Gemini "
                                    "with a free key (GEMINI_API_KEY), Codex on your Codex account, "
                                    "or the paid Anthropic API."))
        col.addWidget(how)
        col.addStretch(1)

    def shown(self):
        c = backend.cfg() or {}
        try:
            self.count.setValue(int(c.get("vibe_count") or 25))
        except (TypeError, ValueError):
            self.count.setValue(25)
        self.text.setFocus()

    def _go(self):
        text = self.text.toPlainText().strip()
        if not text:
            inform(self, _("Vibe playlist"), _("Describe the vibe first."))
            return
        self.win.run_tool("vibe", {"vibe": text, "count": str(self.count.value())})


class PathField(QWidget):
    def __init__(self, kind="dir", value=""):
        super().__init__()
        self.kind = kind
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(str(value or ""))
        browse = button(_("Choose…"))
        browse.clicked.connect(self._browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(browse)

    def _browse(self):
        start = self.edit.text() or os.path.expanduser("~")
        if self.kind == "dir":
            p = QFileDialog.getExistingDirectory(self, _("Choose a folder"), start)
        elif self.kind == "any":
            p, _filter = QFileDialog.getOpenFileName(self, _("Choose a file (Cancel to pick a folder)"), start)
            if not p:
                p = QFileDialog.getExistingDirectory(self, _("Choose a folder"), start)
        else:
            p, _filter = QFileDialog.getOpenFileName(self, _("Choose a file"), start)
        if p:
            self.edit.setText(os.path.normpath(p))

    def text(self):
        return self.edit.text().strip()


class ToolDialog(QDialog):
    """A tool that needs input first (a folder, a file): what it does, the input, Check."""

    def __init__(self, win, tool, value=None):
        super().__init__(win)
        self.win, self.tool = win, tool
        spec = backend.TOOLS[tool]
        self.setWindowTitle(_(spec["title"]))
        self.setMinimumWidth(UI["dialog_w"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(8)
        head = QLabel(_(spec["title"]))
        head.setObjectName("h2")
        lay.addWidget(head)
        lay.addWidget(muted(_(spec.get("about", ""))))
        c = backend.cfg() or settings.defaults()
        self.field = None
        if tool == "incoming":
            lay.addWidget(QLabel(_("Folder with the new tracks")))
            self.field = PathField("dir", value or settings.resolve(c.get("incoming_dir")) or "")
            lay.addWidget(self.field)
            lay.addWidget(QLabel(_("Put them into")))
            self.to_active = QRadioButton(_("Active — goes to the iPod on the next sync"))
            self.to_archive = QRadioButton(_("Archive — set aside"))
            (self.to_archive if c.get("new_tracks_target") == "archive" else self.to_active).setChecked(True)
            lay.addWidget(self.to_active)
            lay.addWidget(self.to_archive)
        elif tool == "likes":
            lay.addWidget(QLabel(_("File or folder with your likes")))
            self.field = PathField("any", value or "")
            lay.addWidget(self.field)
        elif tool == "marks":
            lay.addWidget(QLabel(_("The edited list")))
            self.field = PathField("file", value or backend.marks_file())
            lay.addWidget(self.field)
        lay.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(_("Cancel"))
        cancel.clicked.connect(self.reject)
        direct = None
        if tool in ("incoming", "marks"):
            direct = button(_("Add now") if tool == "incoming" else _("Apply now"), primary=True)
            direct.clicked.connect(self._apply_now)
            row.addWidget(direct)
        go = button(_("Review first") if direct else
                    _("Check…") if spec.get("apply") else _("Run"), primary=direct is None)
        go.setDefault(True)
        go.clicked.connect(self._go)
        row.insertWidget(0, cancel)
        row.addWidget(go)
        lay.addLayout(row)

    def params(self):
        p = {}
        if self.tool == "incoming":
            p = {"folder": self.field.text(), "to": "archive" if self.to_archive.isChecked() else "active"}
        elif self.tool in ("likes", "marks"):
            p = {"path": self.field.text()}
        return p

    def _go(self):
        # Incoming tracks need a dry run to build the review table before writing.
        if self.win.run_tool(self.tool, self.params(), applying=False) is not None:
            self.accept()

    def _apply_now(self):
        # Choosing this explicit action is the confirmation; skip the dry-run/apply pair.
        if self.win.run_tool(self.tool, self.params(), applying=True) is not None:
            self.accept()


class ImportReview(QDialog):
    """The dry run's plan of "Add new tracks" as a table: untick a track to leave it out,
    correct its artist, title or album. Saved back to the plan, which --apply then follows."""

    COLS = (("out_artist", N_("Artist")), ("out_title", N_("Title")), ("album", N_("Album")),
            ("album_artist", N_("Album artist")))

    def __init__(self, win, job):
        import json
        super().__init__(win)
        self.win, self.job = win, job
        self.path = backend.incoming_plan()
        with open(self.path, encoding="utf-8") as f:
            self.data = json.load(f)
        self.setWindowTitle(_("Review the new tracks"))
        self.resize(UI["dialog_w"] * 2, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        head = QLabel(_("Review the new tracks"))
        head.setObjectName("h2")
        lay.addWidget(head)
        lay.addWidget(muted(_("Unticked tracks are left out. Double-click a cell to correct it. "
                              "Tracks already in the library start unticked.")))
        items = self.data["items"]
        self.table = QTableWidget(len(items), len(self.COLS) + 2)
        self.table.setHorizontalHeaderLabels([""] + [_(t) for _k, t in self.COLS] + [_("Status")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for r, it in enumerate(items):
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            box.setCheckState(Qt.CheckState.Unchecked if it["skip"] else Qt.CheckState.Checked)
            self.table.setItem(r, 0, box)
            for c, (key, _t) in enumerate(self.COLS, 1):
                self.table.setItem(r, c, QTableWidgetItem(it.get(key) or ""))
            if it["status"] == "dupe":
                where = it.get("same_as")
                text = (_("same audio as {file}").format(file=os.path.basename(where)) if where
                        else _("already in the library"))
            else:
                text = _("new")
            st = QTableWidgetItem(text)
            st.setFlags(Qt.ItemFlag.ItemIsEnabled)
            st.setToolTip(it.get("same_as") or it["file"])
            self.table.setItem(r, len(self.COLS) + 1, st)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        self.table.resizeColumnsToContents()
        self.table.itemChanged.connect(self._count)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.counter = muted("")
        row.addWidget(self.counter)
        row.addStretch(1)
        cancel = button(_("Cancel"))
        cancel.clicked.connect(self.reject)
        self.go = button("", primary=True)
        self.go.clicked.connect(self._apply)
        row.addWidget(cancel)
        row.addWidget(self.go)
        lay.addLayout(row)
        self._count()

    def _count(self, *_args):
        n = sum(1 for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.CheckState.Checked)
        self.go.setText(plural(n, "Add {n} track", "Add {n} tracks"))
        self.go.setEnabled(n > 0)

    def _apply(self):
        import json
        for r, it in enumerate(self.data["items"]):
            it["skip"] = self.table.item(r, 0).checkState() != Qt.CheckState.Checked
            for c, (key, _t) in enumerate(self.COLS, 1):
                text = self.table.item(r, c).text().strip()
                if text:
                    it[key] = text
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        self.accept()
        self.win.run_tool(self.job.tool, self.job.params, applying=True)


# ================================================================ genres


class GenresView(QWidget):
    """Genre (the iPod's Genres menu) and style (for the AI) per album, edited in a table.

    A view of the library page; its tool row (self.bar) sits where the albums' filters are.
    """

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.rows, self.edits, self.search, self.flt = [], {}, "", 0
        self.choices, self.loaded, self._filling = [], False, False

        self.bar = QWidget()
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(20, 6, 20, 6)
        row.setSpacing(8)
        self.flt_box = QComboBox()
        self.flt_box.addItems([_("All albums"), _("Not in the file yet"), _("Edited, not saved"),
                               _("Active only")])
        self.flt_box.currentIndexChanged.connect(self._filter)
        self.count = muted("", wrap=False)
        suggest = button(_("Suggest with AI"), tip=_(backend.TOOLS["genres_suggest"]["about"]))
        suggest.clicked.connect(self._suggest)
        self.save_btn = button(_("Save the list"), primary=True,
                               tip=_("Save the edits into the genres list; the audio files aren't touched"))
        self.save_btn.clicked.connect(self.save)
        write = button(_("Write into the audio files…"),
                       tip=_("Put genre and style from the list into the tags of the audio files. "
                             "A dry run comes first."))
        write.clicked.connect(self._write)
        open_file = button(_("Open the list file"), tip=_("The genres list is a plain text file"))
        open_file.clicked.connect(self._open)
        self.count.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.flt_box)
        row.addWidget(self.count, 1)
        for w in (suggest, self.save_btn, write):
            w.setMinimumWidth(w.sizeHint().width())
            row.addWidget(w)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        info = muted(_("Genre — broad (Rock, Hip-Hop…): what the iPod's Genres menu shows.   "
                       "Style — precise (Hyperpop, Cloud Rap…): read by the AI playlists.   "
                       "Double-click a cell to edit it."), wrap=True)
        info_row = QHBoxLayout()
        info_row.setContentsMargins(20, 6, 20, 6)
        info_row.addWidget(info, 1)
        info_row.addWidget(open_file, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(info_row)
        self.table = table(["", N_("Album (folder)"), N_("Genre"), N_("Style")], stretch=1)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.itemChanged.connect(self._changed)
        lay.addWidget(self.table)

    def shown(self):
        if not self.loaded:
            self.reload()

    def reload(self):
        if self.edits and not ask(self, _("Genres"), _("Reload the file and drop the unsaved edits?"),
                                  yes=_("Reload"), danger=True):
            return
        self.edits.clear()
        self.count.setText(_("reading…"))
        run_async(backend.genres_rows, ok=self._loaded,
                  failed=lambda m: self.count.setText(_("Could not read it: {error}").format(error=m)))

    def _loaded(self, d):
        self.loaded = True
        self.rows, self.choices = d["rows"], d["choices"]
        self.table.setItemDelegateForColumn(2, _GenreDelegate(self.choices, self.table))
        self._render()

    def _filter(self, i):
        self.flt = i
        self._render()

    def set_search(self, text):
        self.search = text.strip()
        if self.loaded:
            self._render()

    def _value(self, r):
        return self.edits.get(r["key"], (r["genre"], r["style"]))

    def _count(self, shown):
        missing = sum(1 for r in self.rows if not r["in_file"])
        self.count.setText(_("{shown} shown · {missing} not in the file").format(shown=shown, missing=missing)
                           + (" · " + _("{n} unsaved").format(n=len(self.edits)) if self.edits else ""))
        self.count.setToolTip(self.count.text())     # cut short in a narrow window
        self.save_btn.setEnabled(bool(self.edits))

    def _render(self):
        keep = {0: lambda r: True, 1: lambda r: not r["in_file"] and r["key"] not in self.edits,
                2: lambda r: r["key"] in self.edits, 3: lambda r: r["part"] == "Active"}[self.flt]
        rows = [r for r in self.rows if keep(r) and matches(self.search, r["key"], *self._value(r))]
        self._filling = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        parts = {"Active": (GLYPHS["on_ipod"], C["active"]), "Archive": (GLYPHS["on_disk"], C["archive"])}
        for n, r in enumerate(rows):
            g, s = self._value(r)
            letter, color = parts.get(r["part"], ("—", C["faint"]))
            part = QTableWidgetItem(letter)
            part.setForeground(QColor(color))
            part.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            part.setToolTip(_(r["part"]) if r["part"] else _("not in the library any more"))
            key = QTableWidgetItem(r["key"])
            key.setData(Qt.ItemDataRole.UserRole, r["key"])
            for it in (part, key):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            gi, si = QTableWidgetItem(g), QTableWidgetItem(s)
            if r["key"] in self.edits:
                for it in (gi, si):
                    it.setForeground(QColor(C["pending"]))
            elif not r["in_file"]:
                gi.setToolTip(_("Not in the file yet — Suggest fills it in, or type it"))
            if not g:                                   # an empty cell still reads as "fill me in"
                gi.setData(Qt.ItemDataRole.ToolTipRole, _("Not in the file yet — Suggest fills it in, or type it"))
                gi.setBackground(QColor(C["attention_bg"]))
            for c, it in enumerate((part, key, gi, si)):
                self.table.setItem(n, c, it)
        widths(self.table, {0: "state", 2: "genre", 3: "style"})
        self._filling = False
        self._count(len(rows))

    def _changed(self, item):
        if self._filling or item.column() not in (2, 3):
            return
        key = self.table.item(item.row(), 1).data(Qt.ItemDataRole.UserRole)
        r = next(x for x in self.rows if x["key"] == key)
        g = self.table.item(item.row(), 2).text().strip()
        s = self.table.item(item.row(), 3).text().strip()
        if (g, s) == (r["genre"], r["style"]):
            self.edits.pop(key, None)
        else:
            self.edits[key] = (g, s)
        self._filling = True
        item.setForeground(QColor(C["pending"] if key in self.edits else C["text"]))
        if item.column() == 2 and g:
            item.setData(Qt.ItemDataRole.BackgroundRole, None)     # filled in: no longer highlighted
        self._filling = False
        self._count(self.table.rowCount())

    def save(self):
        if not self.edits:
            return True
        if self.win.jobs.busy():
            inform(self, _("Genres"), _("Wait for the running task to finish."))
            return False
        try:
            backend.genres_save(dict(self.edits))
        except Exception as e:
            inform(self, _("Genres"), _("Not saved: {error}").format(error=e), bad=True)
            return False
        self.edits.clear()
        self.loaded = False
        self.reload()
        self.win.toast(_("The list is saved. “Write into the audio files” puts it into the tags."))
        self.win.refresh_attention()
        return True

    def _suggest(self):
        if self.edits and not self.save():
            return
        self.win.run_tool("genres_suggest")

    def _write(self):
        if self.edits and not self.save():
            return
        self.win.run_tool("genres_apply")

    def _open(self):
        try:
            backend.open_in_explorer(settings.path("genres_file", backend.cfg()))
        except ValueError:
            inform(self, _("Genres"), _("There's no file yet — Suggest creates it."))

    def invalidate(self):
        if not self.edits:
            self.loaded = False
            if self.isVisible():
                self.reload()

    def can_leave(self):
        if not self.edits:
            return True
        if ask(self, _("Genres"), plural(len(self.edits), "{n} unsaved edit. Save it?",
                                         "{n} unsaved edits. Save them?"), yes=_("Save")):
            return self.save()
        return True


class _GenreDelegate(QStyledItemDelegate):
    """The genre cell edits with a completer over the suggested genres."""

    def __init__(self, choices, parent):
        super().__init__(parent)
        self.choices = choices

    def createEditor(self, parent, option, index):
        ed = QLineEdit(parent)
        comp = QCompleter(self.choices, ed)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        ed.setCompleter(comp)
        return ed


# ================================================================ settings


class _NoWheel(QObject):
    """A combo or number box inside a scrolling page changes only when it has the focus:
    scrolling the page past it must not change a setting by accident."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Wheel and not obj.hasFocus():
            e.ignore()
            return True
        return False


class SettingsPage(Page):
    """Sections on the left, one section's fields on the right, and a bar at the bottom
    that shows up only when something is changed."""

    saved = pyqtSignal()

    def __init__(self, win):
        super().__init__(win, N_("Settings"))
        self.widgets, self.errors = {}, {}
        self.first, self._filling = False, False
        self.no_wheel = _NoWheel(self)

        self.warns_box = QFrame()                 # the same yellow strip as the library's to-dos
        self.warns_box.setObjectName("attention")
        self.warns_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        wl = QHBoxLayout(self.warns_box)
        wl.setContentsMargins(20, 6, 20, 6)
        self.warns = QLabel()
        self.warns.setWordWrap(True)
        wl.addWidget(self.warns)
        self.body.addWidget(self.warns_box)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        nav_box = QWidget()
        nav_box.setObjectName("settingsNav")
        nav_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        nav_box.setFixedWidth(UI["artists_w"])
        nav_lay = QVBoxLayout(nav_box)
        nav_lay.setContentsMargins(0, 8, 0, 8)
        self.nav = QListWidget()
        self.nav.setObjectName("artists")
        self.nav.currentRowChanged.connect(lambda r: self.pages.setCurrentIndex(max(r, 0)))
        self.file_label = button("")              # where the settings live: shown, not spelled out
        self.file_label.setObjectName("fileLink")
        self.file_label.clicked.connect(lambda: backend.open_in_explorer(os.path.dirname(settings.FILE)))
        nav_lay.addWidget(self.nav, 1)
        nav_lay.addWidget(self.file_label)
        self.nav_box = nav_box
        self.pages = QStackedWidget()
        mid.addWidget(nav_box)
        mid.addWidget(self.pages, 1)
        self.body.addLayout(mid, 1)

        self.bar = QFrame()
        self.bar.setObjectName("marksBar")
        self.bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        brow = QHBoxLayout(self.bar)
        brow.setContentsMargins(20, 8, 20, 8)
        self.bar_label = QLabel()
        self.create = QCheckBox(_("Create the library folder if it doesn't exist"))
        self.revert_btn = button(_("Revert"))
        self.revert_btn.clicked.connect(self.build)
        self.save_btn = button(_("Save"), primary=True)
        self.save_btn.clicked.connect(self.save)
        brow.addWidget(self.bar_label)
        brow.addWidget(self.create)
        brow.addStretch(1)
        brow.addWidget(self.revert_btn)
        brow.addWidget(self.save_btn)
        self.body.addWidget(self.bar)

    def shown(self):
        self.build()

    def _page(self):
        """One section: a scrolling column, not wider than a form needs."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("page")
        outer = QHBoxLayout(inner)
        outer.setContentsMargins(24, 12, 24, 30)
        colw = QWidget()
        colw.setMaximumWidth(UI["form_w"])
        col = QVBoxLayout(colw)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(14)
        outer.addWidget(colw, 1)
        outer.addStretch(0)
        area.setWidget(inner)
        self.pages.addWidget(area)
        self.cols.append(col)
        return col

    def build(self):
        self._filling = True
        keep = max(self.nav.currentRow(), 0)
        while self.pages.count():
            w = self.pages.widget(0)
            self.pages.removeWidget(w)
            w.deleteLater()
        self.nav.blockSignals(True)
        self.nav.clear()
        self.cols = []
        values = backend.cfg()
        self.first = values is None
        values = values or settings.defaults()
        self.title.setText(_("Welcome to PodVault") if self.first else _("Settings"))
        self.sub.setText(_("a few basic settings — everything can be changed later") if self.first else "")
        self.win.set_ready(not self.first)
        self.nav_box.setVisible(not self.first)
        self.file_label.setText(_("Show {file}").format(file=os.path.basename(settings.FILE)))
        self.file_label.setToolTip(settings.FILE)
        self.widgets.clear()
        self.errors.clear()
        section, col = None, None
        for f in settings.FIELDS:
            if self.first and not f.first_run:
                continue
            if f.section != section:
                section = f.section
                if col is None or not self.first:          # the first run is one page
                    col = self._page()
                    self.nav.addItem(_(section))
                if self.first:
                    lab = QLabel(_(section).upper())
                    lab.setObjectName("section")
                    col.addWidget(lab)
            col.addWidget(self._field(f, values))
        for c in self.cols:
            c.addStretch(1)
        self.nav.setCurrentRow(min(keep, self.nav.count() - 1))
        self.pages.setCurrentIndex(min(keep, self.pages.count() - 1) if not self.first else 0)
        self.nav.blockSignals(False)
        self.create.setVisible(self.first)
        self.create.setChecked(self.first)
        warns = [] if self.first else settings.warnings(values)
        self.warns.setText("\n".join(f"{GLYPHS['warn']}  {w}" for w in warns))
        self.warns_box.setVisible(bool(warns))
        self._filling = False
        self._update_bar()

    def _field(self, f, values):
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(3)
        grid.setColumnMinimumWidth(0, UI["form_label_w"])
        grid.setColumnStretch(1, 1)
        label = QLabel(_(f.label))
        label.setObjectName("bold")
        label.setWordWrap(True)
        label.setFixedWidth(UI["form_label_w"])
        v = values.get(f.key)
        if f.kind == "choice":
            w = QComboBox()
            for opt in f.options:
                w.addItem(i18n.LANGUAGE_NAMES.get(opt) or _(CHOICE_LABELS.get(opt, opt)), opt)
            w.setCurrentIndex(max(0, w.findData(v)))
            w.currentIndexChanged.connect(self._update_bar)
        elif f.kind == "int":
            w = QSpinBox()
            w.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            w.setRange(0 if f.default == 0 else 1, 2 ** 31 - 1)
            if f.default == 0:
                w.setSpecialValueText(_("not set"))
            w.setMaximumWidth(UI["form_number_w"])
            try:
                w.setValue(int(v))
            except (TypeError, ValueError):
                w.setValue(int(f.default))
            w.valueChanged.connect(self._update_bar)
        elif f.kind in ("dir", "file") or f.key == "ipod_mount":
            w = PathField("dir" if f.key == "ipod_mount" else f.kind, "" if v is None else v)
            w.edit.textChanged.connect(self._update_bar)
        else:
            w = QLineEdit("" if v is None else str(v))
            w.textChanged.connect(self._update_bar)
        if isinstance(w, (QComboBox, QSpinBox)):
            w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            w.installEventFilter(self.no_wheel)
        grid.addWidget(label, 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(w, 0, 1, Qt.AlignmentFlag.AlignLeft if isinstance(w, QSpinBox) else Qt.AlignmentFlag(0))
        help_text = _(f.help) + (_("  (may be left empty)") if f.optional else "")
        grid.addWidget(muted(help_text), 1, 1)
        if f.kind in ("dir", "file") and v not in (None, "") and str(v).lower() != "auto":
            resolved = settings.resolve(v)
            if resolved != v:
                r = muted(f"→ {resolved}")
                r.setObjectName("small")
                grid.addWidget(r, 2, 1)
        err = QLabel()
        err.setProperty("warn", True)
        err.setWordWrap(True)
        err.hide()
        grid.addWidget(err, 3, 1)
        self.widgets[f.key] = w
        self.errors[f.key] = err
        return box

    def _update_bar(self, *_args):
        if self._filling:
            return
        if self.first:
            self.bar_label.setText("")
            self.save_btn.setText(_("Continue"))
            self.revert_btn.hide()
            self.bar.show()
            return
        dirty = self.dirty()
        self.bar_label.setText(_("Unsaved changes"))
        self.save_btn.setText(_("Save"))
        self.revert_btn.show()
        self.bar.setVisible(dirty)

    def values(self):
        out = {}
        for key, w in self.widgets.items():
            if isinstance(w, QComboBox):
                out[key] = w.currentData()
            elif isinstance(w, QSpinBox):
                out[key] = str(w.value())
            elif isinstance(w, PathField):
                out[key] = w.text()
            else:
                out[key] = w.text().strip()
        return out

    def save(self):
        errors = backend.settings_save(self.values(), self.create.isChecked() and self.first)
        for key, lab in self.errors.items():
            lab.setText(errors.get(key, ""))
            lab.setVisible(key in errors)
        if errors:
            for i in range(self.pages.count()):        # show the first section with a red note
                if any(self.errors[k].isVisible() and self.pages.widget(i).isAncestorOf(self.errors[k])
                       for k in errors):
                    self.nav.setCurrentRow(i)
                    break
            self.win.toast(_("Not saved — see the red notes."), bad=True)
            return False
        self.win.toast(_("Settings saved."), good=True)
        self.saved.emit()
        if not self.win.replaced:
            self.build()
        return True

    def dirty(self):
        values = backend.cfg()
        if values is None:
            return False
        for key, raw in self.values().items():
            val, err = settings.validate(key, raw)
            if err or val != values.get(key):
                return True
        return False

    def can_leave(self):
        if self.first:
            if backend.cfg() is None:
                self.win.toast(_("Fill in the basic settings and press Continue first."), bad=True)
                return False
            return True
        if not self.dirty():
            return True
        if ask(self, _("Settings"), _("Save the changes?"), yes=_("Save")):
            return self.save()
        self.build()
        return True
