"""The pages shown next to the source list."""

import os

import settings
from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QCompleter,
                             QDialog, QListWidget, QListWidgetItem, QPushButton,
                             QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
                             QInputDialog, QLabel, QLineEdit, QListView, QPlainTextEdit,
                             QRadioButton, QScrollArea, QSlider, QSpinBox, QSplitter,
                             QStackedWidget, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
                             QTabWidget,
                             QVBoxLayout, QWidget)

from gui import backend
from gui.theme import C, COLUMNS, GLYPHS, UI
from gui.widgets import (AlbumDelegate, AlbumModel, CapacityBar, Card, IpodPicture, ask,
                         button, covers, fmt_gb, fmt_time, fmt_total, inform, muted, placeholder,
                         plural, run_async)


class Page(QWidget):
    """A page: a header with a title and actions, and a body."""

    def __init__(self, win, title=""):
        super().__init__()
        self.win = win
        self.setObjectName("page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QFrame()
        head.setObjectName("viewHead")
        self.head = QHBoxLayout(head)
        self.head.setContentsMargins(20, 10, 20, 10)
        self.head.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("viewTitle")
        self.sub = muted("", wrap=False)
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
    t.setHorizontalHeaderLabels(headers)
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


SORTS = [("Artist", lambda a: (a["artist"].lower(), a["album"].lower())),
         ("Album", lambda a: (a["album"].lower(), a["artist"].lower())),
         ("Year", lambda a: (a["year"] or "9999", a["artist"].lower())),
         ("Size", lambda a: -a["bytes"]),
         ("Tracks", lambda a: -a["tracks"])]


class Grid(QListView):
    """The album grid, with iTunes-like keys: A / R mark, Space toggles, Enter opens."""

    key_mark = pyqtSignal(str)
    key_open = pyqtSignal()
    key_escape = pyqtSignal()

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
    """An opened album: big cover, facts, tracks, and the Active/Archive switch."""

    closed = pyqtSignal()
    mark = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.setObjectName("detail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.album = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(22)
        self.cover = QLabel()
        self.cover.setFixedSize(UI["detail_cover"], UI["detail_cover"])
        self.cover.setScaledContents(True)
        lay.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        right = QVBoxLayout()
        right.setSpacing(3)
        top = QHBoxLayout()
        self.name = QLabel()
        self.name.setObjectName("albumName")
        self.name.setWordWrap(True)
        close = button(GLYPHS["close"], tip="Close (Esc)")
        close.setFixedWidth(UI["close_button_w"])
        close.clicked.connect(self.closed)
        top.addWidget(self.name, 1)
        top.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        right.addLayout(top)
        self.who = QLabel()
        self.who.setObjectName("albumArtist")
        self.meta = muted("", wrap=True)
        right.addWidget(self.who)
        right.addWidget(self.meta)
        acts = QHBoxLayout()
        self.move = button("")
        self.move.clicked.connect(self._toggle)
        folder = button("Show in folder")
        folder.clicked.connect(lambda: self.album and self._open())
        acts.addWidget(self.move)
        acts.addWidget(folder)
        acts.addStretch(1)
        right.addSpacing(6)
        right.addLayout(acts)
        right.addSpacing(6)
        self.tracks = table(["#", "Title", "Artist", "Genre / style", "Time", "kbps", "Cover"])
        self.tracks.setSortingEnabled(False)
        self.tracks.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tracks.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right.addWidget(self.tracks, 1)
        lay.addLayout(right, 1)
        covers().ready.connect(self._cover_ready)

    def show_album(self, a, state):
        self.album = a
        self.name.setText(a["album"])
        self.who.setText(a["artist"])
        facts = [plural(a["tracks"], "track"), f"{a['bytes'] / 1024 / 1024:.0f} MB"]
        if a["year"]:
            facts.append(str(a["year"]))
        if a["genre"]:
            facts.append(a["genre"])
        self.meta.setText(" · ".join(facts) + f"\n{a['path']}")
        self.set_state(state, a["path"])
        self._cover_ready(a["path"])
        self.tracks.setRowCount(0)
        path = a["path"]
        run_async(backend.album_tracks, path,
                  ok=lambda rows: self._tracks(path, rows),
                  failed=lambda m: self.meta.setText(f"Could not read the tracks: {m}"))

    def set_state(self, state, path):
        if self.album and self.album["path"] == path:
            self.state = state
            self.move.setText("Move to Archive" if state == "A" else "Move to Active")

    def _toggle(self):
        if self.album:
            self.mark.emit(self.album["path"], "R" if self.state == "A" else "A")

    def _open(self):
        try:
            backend.open_in_explorer(self.album["path"])
        except ValueError as e:
            inform(self, "Show in folder", str(e), bad=True)

    def _cover_ready(self, path):
        if not self.album or path != self.album["path"]:
            return
        pm = covers().get(path, UI["cover_large"])
        if pm is None:
            pm = covers().get(path)
        self.cover.setPixmap(pm or placeholder(f"{self.album['artist']} {self.album['album']}",
                                               UI["detail_cover"]))

    def _tracks(self, path, rows):
        if not self.album or self.album["path"] != path:
            return
        fill(self.tracks, [[
            (r["track"], int(r["track"]) if r["track"].isdigit() else 999, "r"),
            r["title"], r["artist"],
            " / ".join(x for x in (r["genre"], r["style"]) if x),
            (fmt_time(r["length"]), r["length"], "r"),
            (r["bitrate"] or "", r["bitrate"], "r"),
            (GLYPHS["yes"] if r["art"] else "none", None, "c", None if r["art"] else C["detail_warn"]),
        ] for r in rows], sortable=False)
        widths(self.tracks, {0: "num", 2: "artist", 3: "genre", 4: "time", 5: "kbps", 6: "cover"})


STATES = [("all", "All", None), ("A", "Active", "Goes to the iPod on the next sync"),
          ("R", "Archive", "Stays on disk")]
VIEWS = [("albums", "Albums"), ("artists", "Artists"), ("genres", "Genres")]


def segmented(items, on_pick, checked=0):
    """A row of joined toggle buttons; exactly one is on. items: [(key, text, tip)]."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    group = QButtonGroup(box)
    group.setExclusive(True)
    buttons = {}
    for n, (key, text, tip) in enumerate(items):
        b = QPushButton(text)
        b.setCheckable(True)
        b.setProperty("seg", "first" if n == 0 else "last" if n == len(items) - 1 else "true")
        if tip:
            b.setToolTip(tip)
        b.setChecked(n == checked)
        b.clicked.connect(lambda _, k=key: on_pick(k))
        group.addButton(b)
        lay.addWidget(b)
        buttons[key] = b
    box.buttons = buttons
    return box


def chip(text, tip, on_toggle):
    b = QPushButton(text)
    b.setCheckable(True)
    b.setProperty("chip", True)
    b.setToolTip(tip)
    b.toggled.connect(on_toggle)
    return b


class Attention(QFrame):
    """What needs doing, each with the button that does it. Hidden when nothing does."""

    def __init__(self):
        super().__init__()
        self.setObjectName("attention")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(20, 6, 20, 6)
        self.lay.setSpacing(10)
        self.hide()

    def set_items(self, items):
        """items: [(text, button text, callback)]."""
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for n, (text, action, fn) in enumerate(items):
            if n:
                sep = QLabel(GLYPHS["dot"])
                sep.setProperty("muted", True)
                self.lay.addWidget(sep)
            lab = QLabel(f"{GLYPHS['warn']}  {text}")
            b = button(action)
            b.clicked.connect(fn)
            self.lay.addWidget(lab)
            self.lay.addWidget(b)
        self.lay.addStretch(1)
        self.setVisible(bool(items))


class LibraryPage(Page):
    """The library in three views — albums, artists, genres — and the Active / Archive markup."""

    marks_changed = pyqtSignal()

    def __init__(self, win):
        super().__init__(win, "Library")
        self.albums, self.view, self.state, self.search, self.sort = None, "albums", "all", "", 0
        self.only_ua = self.only_noart = False
        self.artist = None
        self.model = AlbumModel()
        self.marks = self.model.marks

        self.views = segmented([(k, t, None) for k, t in VIEWS], self.set_view)
        self.head.addWidget(self.views)

        # second row: the tools of the current view
        self.bars = QStackedWidget()
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 6, 20, 6)
        row.setSpacing(8)
        self.states = segmented(STATES, self._state)
        self.ua_chip = chip("Ukrainian", "Albums with і ї є ґ in their titles — the language, not "
                            "the genre", self._ua)
        self.noart_chip = chip("No cover", "Albums with tracks that have no cover art", self._noart)
        self.to_a = button("Mark Active", tip="Mark the selected albums Active (A)")
        self.to_r = button("Mark Archive", tip="Mark the selected albums Archive (R)")
        self.to_a.clicked.connect(lambda: self.mark_selected("A"))
        self.to_r.clicked.connect(lambda: self.mark_selected("R"))
        self.sort_box = QComboBox()
        self.sort_box.addItems([f"Sort by {n}" for n, _ in SORTS])
        self.sort_box.currentIndexChanged.connect(self._sort)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(*UI["tile_range"])
        self.size.setValue(UI["tile"])
        self.size.setFixedWidth(UI["slider_w"])
        self.size.setToolTip("Cover size")
        self.size.valueChanged.connect(self._resize)
        for w in (self.states, self.ua_chip, self.noart_chip):
            row.addWidget(w)
        row.addStretch(1)
        for w in (self.to_a, self.to_r, self.sort_box, self.size):
            row.addWidget(w)
        self.bars.addWidget(bar)
        self.genres = GenresView(win)
        self.bars.addWidget(self.genres.bar)
        self.bars.setFixedHeight(bar.sizeHint().height())
        self.body.addWidget(self.bars)

        self.attention = Attention()
        self.body.addWidget(self.attention)

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
        self.grid.key_mark.connect(self._key_mark)
        self.grid.key_open.connect(lambda: self.open_album(self.grid.currentIndex()))
        self.grid.key_escape.connect(self._escape)
        self.grid.selectionModel().selectionChanged.connect(lambda *_: self._buttons())
        self.delegate.toggled.connect(self.toggle)
        self.detail = AlbumDetail()
        self.detail.closed.connect(self.close_album)
        self.detail.mark.connect(lambda p, s: self.set_marks({p: s}))
        self.detail.hide()
        self.split = QSplitter(Qt.Orientation.Vertical)
        self.split.addWidget(self.grid)
        self.split.addWidget(self.detail)
        self.split.setChildrenCollapsible(False)
        albums_area = QWidget()
        h = QHBoxLayout(albums_area)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self.artists)
        h.addWidget(self.split, 1)
        self.artists.hide()

        self.loading = muted("Reading the library…", wrap=True)
        self.loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.loading)
        self.stack.addWidget(albums_area)
        self.stack.addWidget(self.genres)
        self.body.addWidget(self.stack, 1)
        self._buttons()

    # --- views

    def set_view(self, view):
        if view == self.view:
            return
        if self.view == "genres" and not self.genres.can_leave():
            self.views.buttons["genres"].setChecked(True)
            return
        self.view = view
        self.views.buttons[view].setChecked(True)
        self.artists.setVisible(view == "artists")
        self.bars.setCurrentIndex(1 if view == "genres" else 0)
        self._show_body()
        if view == "genres":
            self.genres.shown()
        else:
            self.refresh()

    def _show_body(self):
        if self.view == "genres":
            self.stack.setCurrentIndex(2)
        else:
            self.stack.setCurrentIndex(0 if self.albums is None else 1)

    def shown(self):
        if self.view == "genres":
            self.genres.shown()

    def can_leave(self):
        return self.genres.can_leave() if self.view == "genres" else True

    # --- data

    def set_albums(self, albums, error=None):
        self.albums = albums
        if albums is None:
            self.loading.setText(error or "Reading the library…")
        else:
            known = {a["path"] for a in albums}
            for p in [p for p in self.marks if p not in known]:
                del self.marks[p]
            if self.detail.album and self.detail.album["path"] not in known:
                self.close_album()
            self.refresh()
            self.marks_changed.emit()
        self._show_body()

    def set_search(self, text):
        self.search = text.strip()
        self.genres.set_search(text)
        self.refresh()

    def _state(self, key):
        self.state = key
        self.refresh()

    def _ua(self, on):
        self.only_ua = on
        self.refresh()

    def _noart(self, on):
        self.only_noart = on
        self.refresh()

    def show_filter(self, state="all", noart=False):
        """Used by the attention bar: e.g. jump to the albums without covers."""
        self.set_view("albums")
        self.states.buttons[state].setChecked(True)
        self.state = state
        self.noart_chip.setChecked(noart)
        self.ua_chip.setChecked(False)
        self.refresh()

    def _filtered(self):
        out = []
        for a in self.albums or []:
            s = self.model.state(a)
            if self.state != "all" and s != self.state:
                continue
            if self.only_ua and not a["ua"]:
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
        self.sub.setText(f"{plural(len(rows), 'album')} · {plural(tracks, 'track')} · {fmt_gb(size)}")
        self._buttons()

    def _fill_artists(self, rows):
        counts = {}
        for a in rows:
            counts[a["artist"]] = counts.get(a["artist"], 0) + 1
        self.artists.blockSignals(True)
        self.artists.clear()
        every = QListWidgetItem(f"All artists  ({len(counts)})")
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
        self.to_a.setEnabled(n > 0)
        self.to_r.setEnabled(n > 0)

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

    def revert(self):
        paths = list(self.marks)
        self.marks.clear()
        self.model.changed(paths)
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
        self.detail.show()
        h = self.split.height()
        self.split.setSizes([h - h // 2, h // 2])
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


IPOD_FILTERS = [("All", lambda t: True),
                ("Not in the library", lambda t: t["state"] == ""),
                ("Archive (will leave)", lambda t: t["state"] == "R"),
                ("Without a cover", lambda t: not t["cover"])]


class IpodPage(Page):
    def __init__(self, win):
        super().__init__(win, "iPod")
        self.data, self.search, self.flt = None, "", 0
        self.refresh_btn = button("Refresh", tip="Read the iPod again")
        self.refresh_btn.clicked.connect(lambda: self.load(force=True))
        self.head.addWidget(self.refresh_btn)

        self.stack = QStackedWidget()
        self.message = muted("", wrap=True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.message)

        content = QWidget()
        content.setObjectName("page")
        col = QVBoxLayout(content)
        col.setContentsMargins(20, 16, 20, 12)
        col.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(26)
        self.pic = IpodPicture()
        top.addWidget(self.pic, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(4)
        self.name = QLabel()
        self.name.setObjectName("ipodName")
        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(16)
        self.facts.setVerticalSpacing(2)
        self.cap = CapacityBar()
        info.addWidget(self.name)
        info.addLayout(self.facts)
        info.addSpacing(6)
        info.addWidget(self.cap)
        acts = QHBoxLayout()
        acts.setSpacing(8)
        self.sync = button("Sync…", primary=True, tip="Make the iPod hold exactly what's in Active")
        self.sync.clicked.connect(lambda: self.win.run_tool("sync"))
        eject = button("Eject")
        eject.clicked.connect(self.win.eject)
        rescue = button("Save tracks only on the iPod…",
                        tip="Copy tracks that are in neither Active nor Archive into the incoming folder")
        rescue.clicked.connect(lambda: self.win.run_tool("rescue"))
        restore = button("Restore last backup…", danger=True,
                         tip="Put the iPod's database back as it was before the last sync")
        restore.clicked.connect(self._restore)
        disk = button("Mirror to disk…", tip="Plain folder copy for Rockbox or disk mode")
        disk.clicked.connect(self._disk)
        for b in (self.sync, eject, rescue, disk, restore):
            acts.addWidget(b)
        acts.addStretch(1)
        info.addSpacing(4)
        info.addLayout(acts)
        top.addLayout(info, 1)
        col.addLayout(top)

        self.tabs = QTabWidget()
        songs = QWidget()
        sl = QVBoxLayout(songs)
        sl.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self.flt_box = QComboBox()
        self.flt_box.addItems([n for n, _ in IPOD_FILTERS])
        self.flt_box.currentIndexChanged.connect(self._filter)
        self.count = muted("", wrap=False)
        row.addWidget(self.flt_box)
        row.addWidget(self.count)
        row.addStretch(1)
        legend = muted("", wrap=False)
        legend.setText(f'<span style="color:{C["active"]}">{GLYPHS["dot"]}</span> in Active &nbsp; '
                       f'<span style="color:{C["archive"]}">{GLYPHS["dot"]}</span> in Archive &nbsp; '
                       f'{GLYPHS["ring"]} not in the library')
        row.addWidget(legend)
        sl.addLayout(row)
        self.songs = table(["", "Name", "Artist", "Album", "Genre", "Time", "Plays", "Cover"], stretch=1)
        self.songs.setSortingEnabled(True)
        sl.addWidget(self.songs)
        self.tabs.addTab(songs, "Music")
        self.lists = table(["Playlist", "Tracks"], stretch=0)
        self.tabs.addTab(self.lists, "Playlists")
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
            self._say("No iPod connected.\n\nConnect it with the cable; it shows up here on its own.")
            return
        if self.data is not None and not force:
            return
        if self.win.jobs.busy():
            self._say(f"“{self.win.jobs.current.base_title}” is running.\n"
                      "The iPod is read again when it's done.")
            return
        self.loading = True
        self.refresh_btn.setEnabled(False)
        self._say("Reading the iPod…")
        run_async(backend.ipod_read, ok=self._loaded, failed=self._failed)

    def invalidate(self):
        self.data = None
        if self.isVisible():
            self.load()

    def _say(self, text):
        self.message.setText(text)
        self.stack.setCurrentIndex(0)

    def _failed(self, msg):
        self.loading = False
        self.refresh_btn.setEnabled(True)
        self._say(f"Could not read the iPod.\n\n{msg}")

    def _loaded(self, d):
        self.loading = False
        self.refresh_btn.setEnabled(True)
        self.data = d
        self.win.ipod_read(d)
        self.title.setText(d["name"])
        self.sub.setText(d["path"])
        self.name.setText(d["model"] or "iPod")
        self.pic.set_white("white" in d["color"])
        while self.facts.count():
            w = self.facts.takeAt(0).widget()
            if w:
                w.deleteLater()
        tracks = d["tracks"]
        facts = [("Capacity", fmt_gb(d["capacity_gb"])), ("Model", d["model_number"] or "—"),
                 ("Serial number", d["serial"] or "—"), ("Software", d["firmware"] or "—"),
                 ("Tracks", f"{len(tracks)} · {fmt_total(sum(t['length'] for t in tracks))}"),
                 ("Without a cover", str(sum(1 for t in tracks if not t["cover"]))),
                 ("Not in the library", str(sum(1 for t in tracks if t["state"] == "")))]
        for r, (k, v) in enumerate(facts):
            self.facts.addWidget(muted(k, wrap=False), r, 0)
            val = QLabel(v)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.facts.addWidget(val, r, 1)
        self.cap.set_values(d["capacity_gb"], d["music_gb"], d["free_gb"])
        fill(self.lists, [[p["title"] + ("  (folder)" if p["folder"] else ""),
                           (p["count"], p["count"], "r")] for p in d["playlists"]])
        widths(self.lists, {1: "count"})
        self._render()
        self.stack.setCurrentIndex(1)

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
            (GLYPHS["yes"] if t["cover"] else "none", t["cover"], "c",
             C["active"] if t["cover"] else C["danger"]),
        ] for t in rows])
        widths(self.songs, {0: "state", 2: "artist", 3: "album", 4: "genre", 5: "time",
                            6: "plays", 7: "cover"})
        self.count.setText(f"{len(rows)} of {len(self.data['tracks'])}")

    def _restore(self):
        if ask(self, "Restore the iPod's database",
               "Put back the iPod's database as it was before the last write?\n\n"
               "Tracks added since then vanish from the list (their files stay until the next "
               "sync); tracks deleted since then come back only if their files are still on the iPod.",
               yes="Restore", danger=True):
            self.win.run_tool("restore")

    def _disk(self):
        drive, ok = QInputDialog.getText(self, "Mirror to disk",
                                         "Drive letter of the Rockbox / disk-mode device (e.g. E:):")
        if ok and drive.strip():
            self.win.run_tool("disk", {"drive": drive.strip()})


# ================================================================ playlists


class PlaylistPage(Page):
    def __init__(self, win):
        super().__init__(win, "Playlist")
        self.file, self.rows, self.search = None, [], ""
        self.to_ipod = button("Create on the iPod…", primary=True,
                              tip="Write this playlist into the iPod's database")
        self.to_ipod.clicked.connect(lambda: self.file and self.win.run_tool(
            "playlist_to_ipod", {"file": self.file}))
        delete = button("Delete", danger=True)
        delete.clicked.connect(self._delete)
        self.head.addWidget(self.to_ipod)
        self.head.addWidget(delete)
        self.table = table(["#", "Name", "Artist", "Album", "Style", "Time"], stretch=1)
        self.table.setSortingEnabled(True)
        self.body.addWidget(self.table)

    def show_playlist(self, file):
        self.file = file
        self.title.setText(os.path.splitext(os.path.basename(file))[0])
        self.sub.setText("reading…")
        self.to_ipod.setEnabled(bool(self.win.ipods))
        run_async(backend.playlist_tracks, file, ok=lambda rows: self._loaded(file, rows),
                  failed=lambda m: self.sub.setText(f"Could not read it: {m}"))

    def shown(self):
        self.to_ipod.setEnabled(bool(self.win.ipods) and bool(self.file))

    def _loaded(self, file, rows):
        if file != self.file:
            return
        self.rows = rows
        gone = sum(1 for r in rows if not r["exists"])
        self.sub.setText(f"{plural(len(rows), 'track')} · {fmt_total(sum(r['length'] for r in rows))}"
                         + (f" · {gone} files missing" if gone else ""))
        self._render()

    def set_search(self, text):
        self.search = text.strip()
        self._render()

    def _render(self):
        rows = [(n, r) for n, r in enumerate(self.rows, 1)
                if matches(self.search, r["title"], r["artist"], r["album"], r["style"])]
        fill(self.table, [[(n, n, "r"),
                           (r["title"], None, None, None if r["exists"] else C["danger"]),
                           r["artist"], r["album"], r["style"],
                           (fmt_time(r["length"]), r["length"], "r")] for n, r in rows])
        widths(self.table, {0: "num", 2: "artist", 3: "album", 4: "genre", 5: "time"})

    def _delete(self):
        if not self.file:
            return
        name = os.path.basename(self.file)
        if not ask(self, "Delete playlist", f"Delete the saved playlist “{name}”?\n\n"
                   "Only the .m3u8 file goes; a copy already on the iPod stays there.",
                   yes="Delete", danger=True):
            return
        try:
            os.remove(backend.playlist_file(self.file))
        except (OSError, ValueError) as e:
            inform(self, "Delete playlist", str(e), bad=True)
            return
        self.file = None
        self.win.reload_playlists()
        self.win.navigate("albums:all")


class VibePage(Page):
    EXAMPLES = ["rainy night drive, slow, a bit sad", "loud and fast for the gym",
                "morning coffee, something light", "ночная прогулка по городу"]

    def __init__(self, win):
        super().__init__(win, "New vibe playlist")
        col = self.scroll_body()
        card = Card("Describe the vibe",
                    "In your own words, in any language. The AI picks and orders tracks from Active, "
                    "using what it knows about the songs plus tempo and energy measured from the "
                    "audio. The first run analyses the whole library (a few minutes); after that "
                    "only new tracks.")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("e.g. rainy night drive, slow, a bit sad")
        self.text.setFixedHeight(UI["vibe_text_h"])
        card.lay.addWidget(self.text)
        chips = QHBoxLayout()
        for ex in self.EXAMPLES:
            b = button(ex)
            b.clicked.connect(lambda _, t=ex: self.text.setPlainText(t))
            chips.addWidget(b)
        chips.addStretch(1)
        card.lay.addLayout(chips)
        self.count = QSpinBox()
        self.count.setRange(5, 200)
        go = button("Make the playlist", primary=True)
        go.clicked.connect(self._go)
        card.row(QLabel("About"), self.count, QLabel("tracks"), go)
        col.addWidget(card)
        more = Card("Before the first playlist",
                    "Analysing the library ahead of time makes the first pick quick.")
        analyse = button("Analyse the library")
        analyse.clicked.connect(lambda: self.win.run_tool("vibe_analyze"))
        more.row(analyse)
        col.addWidget(more)
        how = Card("Which AI", "Settings → AI: Claude Code on a Claude subscription, Google Gemini "
                   "with a free key (GEMINI_API_KEY), or the paid Anthropic API.")
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
            inform(self, "Vibe playlist", "Describe the vibe first.")
            return
        self.win.run_tool("vibe", {"vibe": text, "count": str(self.count.value())})


class PathField(QWidget):
    def __init__(self, kind="dir", value=""):
        super().__init__()
        self.kind = kind
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(str(value or ""))
        browse = button("Choose…")
        browse.clicked.connect(self._browse)
        lay.addWidget(self.edit, 1)
        lay.addWidget(browse)

    def _browse(self):
        start = self.edit.text() or os.path.expanduser("~")
        if self.kind == "dir":
            p = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        elif self.kind == "any":
            p, _ = QFileDialog.getOpenFileName(self, "Choose a file (Cancel to pick a folder)", start)
            if not p:
                p = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Choose a file", start)
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
        self.setWindowTitle(spec["title"])
        self.setMinimumWidth(UI["dialog_w"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(8)
        head = QLabel(spec["title"])
        head.setObjectName("h2")
        lay.addWidget(head)
        lay.addWidget(muted(spec.get("about", "")))
        c = backend.cfg() or settings.defaults()
        self.field = None
        if tool == "incoming":
            lay.addWidget(QLabel("Folder with the new tracks"))
            self.field = PathField("dir", value or settings.resolve(c.get("incoming_dir")) or "")
            lay.addWidget(self.field)
            lay.addWidget(QLabel("Put them into"))
            self.to_active = QRadioButton("Active — goes to the iPod on the next sync")
            self.to_archive = QRadioButton("Archive — set aside")
            (self.to_archive if c.get("new_tracks_target") == "archive" else self.to_active).setChecked(True)
            lay.addWidget(self.to_active)
            lay.addWidget(self.to_archive)
        elif tool == "likes":
            lay.addWidget(QLabel("File or folder with your likes"))
            self.field = PathField("any", value or "")
            lay.addWidget(self.field)
        lay.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        go = button("Check…" if spec.get("apply") else "Run", primary=True)
        go.setDefault(True)
        go.clicked.connect(self._go)
        row.addWidget(cancel)
        row.addWidget(go)
        lay.addLayout(row)

    def params(self):
        p = {}
        if self.tool == "incoming":
            p = {"folder": self.field.text(), "to": "archive" if self.to_archive.isChecked() else "active"}
        elif self.tool == "likes":
            p = {"path": self.field.text()}
        return p

    def _go(self):
        if self.win.run_tool(self.tool, self.params()) is not None:
            self.accept()


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
        self.flt_box.addItems(["All albums", "Not in the file yet", "Edited, not saved", "Active only"])
        self.flt_box.currentIndexChanged.connect(self._filter)
        self.count = muted("", wrap=False)
        suggest = button("Suggest with AI", tip=backend.TOOLS["genres_suggest"]["about"])
        suggest.clicked.connect(self._suggest)
        self.save_btn = button("Save", primary=True)
        self.save_btn.clicked.connect(self.save)
        write = button("Write into the tags…", tip="Dry run first, then the tags are written")
        write.clicked.connect(self._write)
        open_file = button("Open the file")
        open_file.clicked.connect(self._open)
        row.addWidget(self.flt_box)
        row.addWidget(self.count)
        row.addStretch(1)
        for w in (suggest, self.save_btn, write, open_file):
            row.addWidget(w)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        info = muted("Genre — broad (Rock, Hip-Hop…): what the iPod's Genres menu shows.   "
                     "Style — precise (Hyperpop, Cloud Rap…): read by the AI playlists.", wrap=True)
        info.setContentsMargins(20, 6, 20, 6)
        lay.addWidget(info)
        self.table = table(["", "Album (folder)", "Genre", "Style"], stretch=1)
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
        if self.edits and not ask(self, "Genres", "Reload the file and drop the unsaved edits?",
                                  yes="Reload", danger=True):
            return
        self.edits.clear()
        self.count.setText("reading…")
        run_async(backend.genres_rows, ok=self._loaded,
                  failed=lambda m: self.count.setText(f"Could not read it: {m}"))

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
        self.count.setText(f"{shown} shown · {missing} not in the file"
                           + (f" · {len(self.edits)} unsaved" if self.edits else ""))
        self.save_btn.setEnabled(bool(self.edits))

    def _render(self):
        keep = {0: lambda r: True, 1: lambda r: not r["in_file"] and r["key"] not in self.edits,
                2: lambda r: r["key"] in self.edits, 3: lambda r: r["part"] == "Active"}[self.flt]
        rows = [r for r in self.rows if keep(r) and matches(self.search, r["key"], *self._value(r))]
        self._filling = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        parts = {"Active": ("A", C["active"]), "Archive": ("R", C["archive"])}
        for n, r in enumerate(rows):
            g, s = self._value(r)
            letter, color = parts.get(r["part"], ("—", C["faint"]))
            part = QTableWidgetItem(letter)
            part.setForeground(QColor(color))
            part.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            part.setToolTip(r["part"] or "not in the library any more")
            key = QTableWidgetItem(r["key"])
            key.setData(Qt.ItemDataRole.UserRole, r["key"])
            for it in (part, key):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            gi, si = QTableWidgetItem(g), QTableWidgetItem(s)
            if r["key"] in self.edits:
                for it in (gi, si):
                    it.setForeground(QColor(C["pending"]))
            elif not r["in_file"]:
                gi.setToolTip("Not in the file yet — Suggest fills it in, or type it")
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
        item.setForeground(QColor(C["pending"] if key in self.edits else C["text"]))
        self._count(self.table.rowCount())

    def save(self):
        if not self.edits:
            return True
        if self.win.jobs.busy():
            inform(self, "Genres", "Wait for the running task to finish.")
            return False
        try:
            backend.genres_save(dict(self.edits))
        except Exception as e:
            inform(self, "Genres", f"Not saved: {e}", bad=True)
            return False
        self.edits.clear()
        self.loaded = False
        self.reload()
        self.win.toast("Genres saved. “Write into the tags” puts them into the files.")
        self.win.refresh_attention()
        return True

    def _suggest(self):
        if self.edits and not self.save():
            return
        self.win.run_tool("genres_suggest")

    def _write(self):
        if self.edits:
            if not ask(self, "Genres", "Save the edits first?", yes="Save"):
                return
            if not self.save():
                return
        self.win.run_tool("genres_apply")

    def _open(self):
        try:
            backend.open_in_explorer(settings.path("genres_file", backend.cfg()))
        except ValueError:
            inform(self, "Genres", "There's no file yet — Suggest creates it.")

    def invalidate(self):
        if not self.edits:
            self.loaded = False
            if self.isVisible():
                self.reload()

    def can_leave(self):
        if not self.edits:
            return True
        if ask(self, "Genres", f"{plural(len(self.edits), 'unsaved edit')}. Save them?", yes="Save"):
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


class SettingsPage(Page):
    saved = pyqtSignal()

    def __init__(self, win):
        super().__init__(win, "Settings")
        self.save_btn = button("Save", primary=True)
        self.save_btn.clicked.connect(self.save)
        self.head.addWidget(self.save_btn)
        self.col = self.scroll_body()
        self.widgets, self.errors = {}, {}
        self.first = False
        self.create = QCheckBox("Create the library folder if it doesn't exist")
        self.warns = QLabel()
        self.warns.setWordWrap(True)
        self.warns.setProperty("warn", True)

    def shown(self):
        self.build()

    def build(self):
        while self.col.count():
            item = self.col.takeAt(0)
            w = item.widget()
            if w is not None and w not in (self.create, self.warns):
                w.deleteLater()
        values = backend.cfg()
        self.first = values is None
        values = values or settings.defaults()
        self.title.setText("Welcome to Music Utility" if self.first else "Settings")
        self.sub.setText("a few basic settings — everything can be changed later" if self.first
                         else settings.FILE)
        self.widgets.clear()
        self.errors.clear()
        section = None
        for f in settings.FIELDS:
            if self.first and not f.first_run:
                continue
            if f.section != section:
                section = f.section
                lab = QLabel(section.upper())
                lab.setObjectName("section")
                self.col.addWidget(lab)
            box = QWidget()
            grid = QGridLayout(box)
            grid.setContentsMargins(0, 4, 0, 4)
            grid.setHorizontalSpacing(16)
            grid.setColumnMinimumWidth(0, 200)
            grid.setColumnStretch(1, 1)
            label = QLabel(f.label)
            label.setObjectName("bold")
            v = values.get(f.key)
            if f.kind == "choice":
                w = QComboBox()
                w.addItems(f.options)
                if v in f.options:
                    w.setCurrentText(v)
            elif f.kind == "int":
                w = QSpinBox()
                w.setRange(1, 2 ** 31 - 1)       # settings.validate: any whole number > 0
                try:
                    w.setValue(int(v))
                except (TypeError, ValueError):
                    w.setValue(int(f.default))
            elif f.kind in ("dir", "file"):
                w = PathField(f.kind, "" if v is None else v)
            else:
                w = QLineEdit("" if v is None else str(v))
            grid.addWidget(label, 0, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(w, 0, 1)
            help_text = f.help + ("  (may be left empty)" if f.optional else "")
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
            self.col.addWidget(box)
        self.create.setVisible(self.first)
        self.create.setChecked(self.first)
        self.col.addWidget(self.create)
        warns = [] if self.first else settings.warnings(values)
        self.warns.setText("\n".join(f"! {w}" for w in warns))
        self.warns.setVisible(bool(warns))
        self.col.addWidget(self.warns)
        self.col.addStretch(1)

    def values(self):
        out = {}
        for key, w in self.widgets.items():
            if isinstance(w, QComboBox):
                out[key] = w.currentText()
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
            self.win.toast("Not saved — see the red notes.", bad=True)
            return False
        self.win.toast("Settings saved.", good=True)
        self.saved.emit()
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
                self.win.toast("Fill in the basic settings and press Save first.", bad=True)
                return False
            return True
        if not self.dirty():
            return True
        if ask(self, "Settings", "Save the changes?", yes="Save"):
            return self.save()
        self.build()
        return True
