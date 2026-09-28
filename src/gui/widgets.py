"""Building blocks: background work, covers, the album grid, the LCD, the iPod picture."""

import hashlib
import traceback

from PyQt6.QtCore import (QAbstractListModel, QEvent, QModelIndex, QObject, QPoint, QRect,
                          QRectF, QRunnable, QSize, Qt, QThreadPool, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient,
                         QPainter, QPainterPath, QPen, QPixmap)
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                             QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QVBoxLayout,
                             QWidget)

from gui import backend
from gui.theme import C, GLYPHS, UI
from i18n import _, n_

# ------------------------------------------------------------ formatting


def fmt_time(seconds):
    s = int(round(seconds or 0))
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_total(seconds):
    s = int(seconds or 0)
    h, m = s // 3600, (s % 3600) // 60
    return _("{h} h {m} min").format(h=h, m=m) if h else _("{m} min").format(m=m)


def fmt_gb(gb):
    return _("{gb} GB").format(gb=f"{gb:.2f}" if gb < 10 else f"{gb:.1f}")


def plural(n, one, many):
    """plural(3, "{n} track", "{n} tracks") -> '3 tracks', in the interface language."""
    return n_(one, many, n).format(n=n)


# ------------------------------------------------------------ background work


class _Signals(QObject):
    ok = pyqtSignal(object)
    failed = pyqtSignal(str)


class _Task(QRunnable):
    def __init__(self, fn, args, signals):
        super().__init__()
        self.fn, self.args, self.signals = fn, args, signals

    def run(self):
        try:
            result = self.fn(*self.args)
        except SystemExit as e:
            self.signals.failed.emit(str(e.code))
        except Exception as e:
            msg = str(e) or type(e).__name__
            if not isinstance(e, (RuntimeError, ValueError, OSError)):
                msg = f"{type(e).__name__}: {msg}\n\n{traceback.format_exc(limit=4)}"
            self.signals.failed.emit(msg)
        else:
            self.signals.ok.emit(result)


_alive = set()   # signal objects must outlive their task


def run_async(fn, *args, ok=None, failed=None):
    """Run fn(*args) on the thread pool; ok(result) / failed(message) run on the UI thread."""
    signals = _Signals()
    _alive.add(signals)

    def finish(cb, value):
        _alive.discard(signals)
        if cb:
            cb(value)

    signals.ok.connect(lambda r: finish(ok, r))
    signals.failed.connect(lambda m: finish(failed, m))
    QThreadPool.globalInstance().start(_Task(fn, args, signals))


# ------------------------------------------------------------ dialogs


def ask(parent, title, text, yes=None, danger=False, details=None):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    box.setText(text)
    if details:
        box.setDetailedText(details)
    ok = box.addButton(yes or _("OK"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(_("Cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(ok)
    box.exec()
    return box.clickedButton() is ok


def inform(parent, title, text, bad=False):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Critical if bad else QMessageBox.Icon.Information)
    box.setText(text)
    box.exec()


def button(text, primary=False, danger=False, tip=None):
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    if tip:
        b.setToolTip(tip)
    return b


class ElidedLabel(QLabel):
    """One line that fits: a long text (a path) is cut in the middle, whole in the tooltip."""

    def __init__(self, text=""):
        super().__init__()
        self._full = ""
        self.setText(text)

    def setText(self, text):
        self._full = str(text)
        self._fit()
        self.updateGeometry()

    def text(self):
        return self._full

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _fit(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width())
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


def muted(text="", wrap=True):
    lab = QLabel(text)
    lab.setProperty("muted", True)
    lab.setWordWrap(wrap)
    return lab


# ------------------------------------------------------------ covers


_placeholders = {}


def placeholder(text, size):
    """A coloured square with initials, for albums without a cover."""
    key = (text, size)
    if key not in _placeholders:
        if len(_placeholders) > UI["cache_max"]:
            _placeholders.clear()
        _placeholders[key] = _draw_placeholder(text, size)
    return _placeholders[key]


def _draw_placeholder(text, size):
    h = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:6], 16)
    base = QColor.fromHsv(h % 360, 90, 170)
    pm = QPixmap(size, size)
    p = QPainter(pm)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, base.lighter(125))
    g.setColorAt(1, base.darker(135))
    p.fillRect(0, 0, size, size, QBrush(g))
    words = [w for w in text.replace("—", " ").split() if w[:1].isalnum()]
    initials = "".join(w[0] for w in words[:2]).upper() or GLYPHS["library"]
    f = QFont()
    f.setBold(True)
    f.setPixelSize(max(10, size // 4))
    p.setFont(f)
    p.setPen(QColor(C["art_initials"]))
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, initials)
    p.end()
    return pm


class _CoverSignals(QObject):
    loaded = pyqtSignal(str, int, QImage)


class _CoverTask(QRunnable):
    def __init__(self, path, size, signals):
        super().__init__()
        self.path, self.size, self.signals = path, size, signals

    def run(self):
        img = QImage()
        try:
            data = backend.thumbnail(self.path, self.size)
            if data:
                img.loadFromData(data)
        except Exception:
            pass
        self.signals.loaded.emit(self.path, self.size, img)


class Covers(QObject):
    """Album covers, loaded in the background the first time they're painted."""

    ready = pyqtSignal(str)
    def __init__(self):
        super().__init__()
        self.cache, self.pending = {}, set()
        self.signals = _CoverSignals()
        self.signals.loaded.connect(self._loaded)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(UI["cover_threads"])

    def get(self, path, size=None):
        """A QPixmap, False (no cover), or None (loading — ready(path) follows)."""
        size = size or UI["thumb"]
        key = (path, size)
        if key in self.cache:
            return self.cache[key]
        if key not in self.pending:
            self.pending.add(key)
            self.pool.start(_CoverTask(path, size, self.signals))
        return None

    def _loaded(self, path, size, img):
        self.pending.discard((path, size))
        self.cache[(path, size)] = QPixmap.fromImage(img) if not img.isNull() else False
        self.ready.emit(path)

    def clear(self):
        self.cache.clear()


COVERS = None


def covers():
    global COVERS
    if COVERS is None:
        COVERS = Covers()
    return COVERS


# ------------------------------------------------------------ album grid

PathRole = Qt.ItemDataRole.UserRole + 1


class AlbumModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.rows, self.marks = [], {}
        self.row_of = {}
        covers().ready.connect(self._cover_ready)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.row_of = {a["path"]: i for i, a in enumerate(rows)}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        a = self.rows[index.row()]
        if role == PathRole:
            return a["path"]
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{a['album']}\n{a['artist']}"
        if role == Qt.ItemDataRole.ToolTipRole:
            state = self.marks.get(a["path"], a["state"])
            tip = (f"{a['artist']} — {a['album']}\n{plural(a['tracks'], '{n} track', '{n} tracks')}, "
                   + _("{mb} MB").format(mb=f"{a['bytes'] / 1024 / 1024:.0f}")
                   + (f", {a['year']}" if a["year"] else "") + (f", {a['genre']}" if a["genre"] else "")
                   + "\n" + (_("Active — goes to the iPod") if state == 'A' else _("Archive — stays on disk")))
            if a["path"] in self.marks:
                tip += _("  (not saved yet)")
            if a["no_art"]:
                tip += "\n" + n_("{n} track without cover art", "{n} tracks without cover art",
                                  a['no_art']).format(n=a['no_art'])
            return tip
        return None

    def album(self, index):
        return self.rows[index.row()]

    def state(self, a):
        return self.marks.get(a["path"], a["state"])

    def changed(self, paths):
        for p in paths:
            r = self.row_of.get(p)
            if r is not None:
                i = self.index(r)
                self.dataChanged.emit(i, i)

    def _cover_ready(self, path):
        self.changed([path])


class AlbumDelegate(QStyledItemDelegate):
    """Paints an album tile: cover, A/R badge, UA / no-art flags, two lines of text."""

    toggled = pyqtSignal(str)      # the badge was clicked

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.tile = UI["tile"]
        self.scaled = {}

    def _scaled(self, a):
        """The cover at tile size, scaled once: smooth scaling on every paint makes scrolling stutter."""
        pm = covers().get(a["path"])
        key = (a["path"], self.tile, bool(pm))
        hit = self.scaled.get(key)
        if hit is None:
            src = pm or placeholder(f"{a['artist']} {a['album']}", self.tile)
            hit = src.scaled(QSize(self.tile, self.tile), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            if pm is not None:          # don't cache "still loading"
                if len(self.scaled) > UI["cache_max"]:
                    self.scaled.clear()
                self.scaled[key] = hit
        return hit

    def sizeHint(self, option, index):
        fm = QFontMetrics(option.font)
        return QSize(self.tile + 16, self.tile + 16 + fm.height() * 2 + 8)

    def _art_rect(self, rect):
        return QRect(rect.x() + 8, rect.y() + 8, self.tile, self.tile)

    def _badge_rect(self, art):
        return QRect(art.right() - 30, art.bottom() - 24, 26, 19)

    def paint(self, p, option, index):
        a = self.model.album(index)
        state = self.model.state(a)
        moved = a["path"] in self.model.marks
        rect = option.rect
        art = self._art_rect(rect)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            bg = QColor(C["accent"])
            bg.setAlpha(QColor(C["selection_fill"]).alpha())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(QRectF(rect.adjusted(2, 2, -2, -2)), 7, 7)

        scaled = self._scaled(a)
        # shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["art_shadow"]))
        p.drawRoundedRect(QRectF(art.adjusted(1, 2, 1, 2)), 3, 3)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(art), 3, 3)
        p.setClipPath(clip)
        sx = (scaled.width() - art.width()) // 2
        sy = (scaled.height() - art.height()) // 2
        p.drawPixmap(art, scaled, QRect(sx, sy, art.width(), art.height()))
        if state == "R":
            p.fillRect(art, QColor(C["art_dim"]))
        p.setClipping(False)
        if selected:
            p.setPen(QPen(QColor(C["accent"]), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(art.adjusted(-1, -1, 1, 1)), 4, 4)

        # flags
        x = art.x() + 5
        f = QFont(option.font)
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        for text, color, on in (("UA", C["ua"], a["ua"]), ("!art", C["danger"], a["no_art"])):
            if not on:
                continue
            w = QFontMetrics(f).horizontalAdvance(text) + 10
            r = QRect(x, art.y() + 5, w, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(r), 3, 3)
            p.setPen(QColor(C["flag_text"]))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
            x += w + 4

        # A / R badge
        b = self._badge_rect(art)
        color = C["pending"] if moved else (C["active"] if state == "A" else C["archive"])
        p.setPen(QPen(QColor(C["flag_text"]), 1.5) if moved else Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(b), 9, 9)
        p.setPen(QColor(C["flag_text"]))
        p.drawText(b, Qt.AlignmentFlag.AlignCenter, state)

        # text
        fm = QFontMetrics(option.font)
        t1 = QRect(art.x(), art.bottom() + 5, self.tile, fm.height())
        t2 = QRect(art.x(), t1.bottom() + 1, self.tile, fm.height())
        bold = QFont(option.font)
        bold.setBold(True)
        p.setFont(bold)
        p.setPen(QColor(C["text"]))
        p.drawText(t1, Qt.AlignmentFlag.AlignLeft, QFontMetrics(bold).elidedText(
            a["album"], Qt.TextElideMode.ElideRight, self.tile))
        p.setFont(option.font)
        p.setPen(QColor(C["muted"]))
        p.drawText(t2, Qt.AlignmentFlag.AlignLeft, fm.elidedText(
            a["artist"], Qt.TextElideMode.ElideRight, self.tile))
        p.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseButtonRelease and \
                event.button() == Qt.MouseButton.LeftButton:
            if self._badge_rect(self._art_rect(option.rect)).contains(event.position().toPoint()):
                self.toggled.emit(self.model.album(index)["path"])
                return True
        return super().editorEvent(event, model, option, index)


# ------------------------------------------------------------ the LCD


class Lcd(QFrame):
    """The display in the middle of the toolbar: what's going on right now."""

    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("lcd")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(UI["lcd_w"][0])
        self.setMaximumWidth(UI["lcd_w"][1])
        self.setFixedHeight(UI["lcd_h"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 5, 14, 5)
        lay.setSpacing(1)
        self.title = QLabel(_("Music Utility"))
        self.title.setObjectName("lcdTitle")
        self.sub = QLabel("")
        self.sub.setObjectName("lcdSub")
        for lab in (self.title, self.sub):
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumWidth(UI["lcd_bar_w"])
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        lay.addWidget(self.bar, 0, Qt.AlignmentFlag.AlignHCenter)
        self.show_idle(_("Music Utility"), "")

    def show_idle(self, title, sub):
        self.title.setText(title)
        self.sub.setText(sub)
        self.bar.setVisible(False)

    def show_progress(self, title, sub, progress):
        self.title.setText(title)
        self.sub.setText(sub)
        self.bar.setVisible(True)
        if progress is None:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(progress * 1000))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


# ------------------------------------------------------------ iPod pieces


class IpodPicture(QWidget):
    """A classic iPod, drawn: black or white, screen and click wheel."""

    def __init__(self):
        super().__init__()
        self.white = False
        self.setFixedSize(*UI["ipod_picture"])

    def set_white(self, white):
        self.white = white
        self.update()

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        tone = "white" if self.white else "black"
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        def gradient(rect, top, bottom, diagonal=False):
            g = QLinearGradient(rect.left(), rect.top(),
                                rect.right() if diagonal else rect.left(), rect.bottom())
            g.setColorAt(0, QColor(C[top]))
            g.setColorAt(1, QColor(C[bottom]))
            return QBrush(g)

        body = QRectF(w * .04, h * .01, w * .92, h * .96)
        p.setPen(QPen(QColor(C["ipod_outline"]), 1))
        p.setBrush(gradient(body, f"ipod_{tone}_top", f"ipod_{tone}_bottom", diagonal=True))
        p.drawRoundedRect(body, w * .125, w * .125)

        screen = QRectF(w * .135, h * .075, w * .73, h * .34)
        p.setPen(QPen(QColor(C["ipod_screen_frame"]), 2))
        p.setBrush(gradient(screen, "ipod_screen_top", "ipod_screen_bottom"))
        p.drawRoundedRect(screen, 3, 3)
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(6, int(h * .05)))
        p.setFont(f)
        p.setPen(QColor(C["ipod_screen_text"]))
        p.drawText(screen.adjusted(4, 3, -4, 0),
                   Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, "iPod")

        d = w * .59
        wheel = QRectF((w - d) / 2, h * .51, d, d)
        p.setPen(QPen(QColor(C["wheel_line"]), 1))
        p.setBrush(gradient(wheel, f"wheel_{tone}_top", f"wheel_{tone}_bottom"))
        p.drawEllipse(wheel)
        c = d * .36
        p.drawEllipse(QRectF(wheel.center().x() - c / 2, wheel.center().y() - c / 2, c, c))
        f.setPixelSize(max(5, int(h * .038)))
        p.setFont(f)
        p.setPen(QColor(C[f"wheel_{tone}_text"]))
        p.drawText(QRectF(wheel.left(), wheel.top() + 2, d, d * .18), Qt.AlignmentFlag.AlignCenter, "MENU")
        p.end()


class CapacityBar(QWidget):
    """Audio / Other / Free, like iTunes' capacity bar."""

    def __init__(self):
        super().__init__()
        self.total = self.audio = self.free = 0
        self.setFixedHeight(UI["capacity_h"])

    def set_values(self, total, audio, free):
        self.total, self.audio, self.free = max(total, 0), max(audio, 0), max(free, 0)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0, 2, self.width() - 1, 16)
        path = QPainterPath()
        path.addRoundedRect(bar, 8, 8)
        p.setClipPath(path)
        p.fillRect(bar, QColor(C["alt"]))
        if self.total > 0:
            other = max(self.total - self.audio - self.free, 0)
            x = bar.x()
            for value, top, bottom in ((self.audio, C["cap_audio_top"], C["cap_audio"]),
                                       (other, C["cap_other_top"], C["cap_other"])):
                w = bar.width() * value / self.total
                g = QLinearGradient(0, bar.top(), 0, bar.bottom())
                g.setColorAt(0, QColor(top))
                g.setColorAt(1, QColor(bottom))
                p.fillRect(QRectF(x, bar.top(), w, bar.height()), QBrush(g))
                x += w
        p.setClipping(False)
        p.setPen(QPen(QColor(C["line"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(bar, 8, 8)
        f = QFont()
        f.setPointSizeF(8.5)
        p.setFont(f)
        x = 0
        other = max(self.total - self.audio - self.free, 0)
        for label, value, color in ((_("Audio"), self.audio, C["cap_audio"]),
                                    (_("Other"), other, C["cap_other"]),
                                    (_("Free"), self.free, C["line"])):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x, 26, 9, 9), 2, 2)
            p.setPen(QColor(C["muted"]))
            text = f"{label}  {fmt_gb(value)}"
            p.drawText(QPoint(x + 14, 35), text)
            x += 14 + QFontMetrics(f).horizontalAdvance(text) + 18
        p.end()


class Card(QFrame):
    def __init__(self, title=None, text=None):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 14, 18, 16)
        self.lay.setSpacing(6)
        if title:
            t = QLabel(title)
            t.setObjectName("h2")
            self.lay.addWidget(t)
        if text:
            self.lay.addWidget(muted(text))

    def row(self, *widgets, stretch=True):
        h = QHBoxLayout()
        h.setSpacing(8)
        for w in widgets:
            h.addWidget(w) if isinstance(w, QWidget) else h.addLayout(w)
        if stretch:
            h.addStretch(1)
        self.lay.addLayout(h)
        return h
