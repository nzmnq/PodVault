"""How the window looks: palettes, the symbols used as icons, fonts, sizes, timings.

The layout follows iTunes (a source list, an LCD in the toolbar, views of one
library) but the look is our own: flat light chrome, an indigo accent. Light by
default; dark when the system is dark. Nothing visual is hard-coded elsewhere.
"""

import functools
import os
import tempfile

from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, Qt
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPalette, QPen,
                         QPixmap)
from PyQt6.QtWidgets import QComboBox, QStyledItemDelegate

# Colours are "#RRGGBB" or "#AARRGGBB"; the ones only the stylesheet uses may be rgba().
LIGHT = dict(
    bg="#ffffff", text="#1c1c21", muted="#6b6b76", faint="#a3a3ad", line="#e3e3e8",
    alt="#f6f6f9", hover="#efeff4",
    tb_top="#fbfbfc", tb_bottom="#f1f1f4", tb_line="#d9d9df",
    btn_top="#ffffff", btn_bottom="#f3f3f6", btn_line="#cfcfd7",
    lcd_top="#f4f5f9", lcd_bottom="#eceef5", lcd_line="#d5d8e2", lcd_text="#2b2d3a",
    lcd_track="#1e000000",
    side="#f4f4f7", side_text="#26262e", side_head="#8b8b96", side_hover="#e9e9ef",
    accent="#5b5bd6", accent_soft="#e8e8fb", on_accent="#ffffff",
    active="#2f9e5a", archive="#d9861c", danger="#d0453c", pending="#c2448a",
    sheet="#fafafc",
    attention_bg="#fff8ea", attention_line="#f1dfb8", attention_text="#6b4a10",
    # an opened album
    detail_top="#34374a", detail_bottom="#1f2130", detail_text="#f2f2f6", detail_muted="#b9bccb",
    detail_warn="#ffb0a8", detail_btn="rgba(255,255,255,30)", detail_btn_line="rgba(255,255,255,70)",
    detail_alt="rgba(255,255,255,10)", detail_line="rgba(255,255,255,40)",
    # album tiles
    flag_text="#ffffff", art_initials="#e6ffffff", art_shadow="#2d000000",
    art_dim="#6effffff", selection_fill="#28000000",
    # the iPod picture
    ipod_black_top="#48484b", ipod_black_bottom="#0e0e10",
    ipod_white_top="#ffffff", ipod_white_bottom="#d6d6d6", ipod_outline="#3c000000",
    ipod_screen_top="#b6c3f2", ipod_screen_bottom="#5b66c9", ipod_screen_frame="#96000000",
    ipod_screen_text="#ffffff",
    wheel_black_top="#3f3f42", wheel_black_bottom="#1b1b1d", wheel_black_text="#9a9a9a",
    wheel_white_top="#f0f0f0", wheel_white_bottom="#bdbdbd", wheel_white_text="#7a7a7a",
    wheel_line="#32000000",
    # the capacity bars
    cap_audio_top="#8c8cf0", cap_audio="#5b5bd6", cap_other_top="#ffd27a", cap_other="#e5a53a",
    # notifications
    toast="rgba(38,38,46,235)", toast_good="rgba(40,135,80,240)", toast_bad="rgba(190,55,48,240)",
    toast_text="#ffffff",
    drop_bg="rgba(255,255,255,225)",
)

DARK = dict(
    LIGHT,
    bg="#1b1b1f", text="#e7e7ec", muted="#a2a2ad", faint="#6d6d78", line="#2f2f36",
    alt="#222228", hover="#2a2a31",
    tb_top="#26262c", tb_bottom="#212126", tb_line="#111114",
    btn_top="#34343c", btn_bottom="#2d2d34", btn_line="#41414b",
    lcd_top="#26272f", lcd_bottom="#212229", lcd_line="#3a3b46", lcd_text="#d9dbf0",
    lcd_track="#32ffffff",
    side="#1f1f24", side_text="#dcdce3", side_head="#7c7c88", side_hover="#2a2a31",
    accent="#7b7bf0", accent_soft="#33335a", sheet="#25252b", art_dim="#6e000000",
    attention_bg="#2c2618", attention_line="#4a3d1e", attention_text="#f0d49a",
    drop_bg="rgba(27,27,31,225)",
)

FONTS = dict(
    mono=["Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"],
    # symbol fonts first, so a symbol is drawn as a plain glyph, never as a colour emoji
    symbols=["Segoe UI Symbol", "Apple Symbols", "Noto Sans Symbols 2", "DejaVu Sans"],
)

# Plain Unicode symbols that have no emoji form, drawn as one-colour icons.
GLYPHS = dict(
    app="♫",               # beamed eighth notes
    library="♫",
    ipod="▯",              # white vertical rectangle
    eject="⏏",             # eject symbol — drawn with the symbol font, as a plain glyph
    vibe="✦",              # black four-pointed star
    playlist="≡",          # identical to
    incoming="+",          # plus sign
    sync="⟳",              # clockwise gapped circle arrow
    menu="☰",              # trigram for heaven
    close="✕",             # multiplication x
    yes="✓",               # check mark
    dot="●",               # black circle
    ring="○",              # white circle
    warn="▲",              # black up-pointing triangle
    on_ipod="✓",           # tile badge: Active, goes to the iPod
    on_disk="–",           # tile badge: Archive, stays on disk (en dash)
    small="▫",             # white small square: the cover-size slider
    large="◻",             # white medium square
)

UI = dict(
    window=(1320, 840), window_min=(1040, 640), sheet=(900, 600), dialog_w=560,
    toolbar_h=64, status_h=28, status_button_h=20,
    lcd_h=50, lcd_w=(260, 440), lcd_bar_w=340,
    side_w=220, side_min=180, side_row=26, side_device_row=44, side_head=28, icon=16,
    side_capacity_h=4, side_eject=22, side_pad=6, side_gap=10,
    tile=150, tile_range=(100, 240), thumb=256, cover_large=640, detail_cover=220,
    artists_w=190, ipod_picture=(112, 184), capacity_h=40, vibe_text_h=80,
    close_button_w=30, form_w=760, form_label_w=190, form_number_w=120, slider_w=110, combo_extra_w=44, genre_box_max_w=260,
    poll_ms=3000, sheet_refresh_ms=150, lcd_refresh_ms=250, eject_repoll_ms=1500,
    note_s=6, toast_ms=5000, toast_bad_ms=8000, toast_w=420, toast_gap=14, list_preview=12,
    cover_threads=3, cache_max=3000, log_lines=10000,
)

# Table column widths, px.
COLUMNS = dict(num=36, state=30, time=56, kbps=44, cover=54, plays=50, count=80,
               artist=170, album=190, genre=140, style=260)

C = dict(LIGHT)   # the colours in use; filled by apply()


def is_dark():
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


QSS = """
QWidget {{ color: {text}; font-size: 10pt; }}
QMainWindow, QStackedWidget, #page {{ background: {bg}; }}

#toolbar {{
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {tb_top}, stop:1 {tb_bottom});
  border-bottom: 1px solid {tb_line};
}}
#statusbar {{ background: {tb_bottom}; border-top: 1px solid {tb_line}; }}
#statusbar QLabel {{ color: {muted}; font-size: 8.5pt; }}
#statusbar QLabel#summary {{ color: {text}; }}
#statusbar QLabel#pending {{ color: {pending}; font-weight: 600; }}

QPushButton#tb {{
  min-height: 28px; padding: 0 14px; border-radius: 7px;
  border: 1px solid {btn_line};
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {btn_top}, stop:1 {btn_bottom});
}}
QPushButton#tb:hover {{ border-color: {accent}; }}
QPushButton#tb:pressed {{ background: {hover}; }}
QPushButton#tb:disabled {{ color: {faint}; }}
QPushButton#tb::menu-indicator {{ image: none; width: 0; }}

#lcd {{
  border: 1px solid {lcd_line}; border-radius: 9px;
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {lcd_top}, stop:1 {lcd_bottom});
}}
#lcd QLabel {{ color: {lcd_text}; background: transparent; }}
#lcdTitle {{ font-weight: 600; }}
#lcdSub {{ font-size: 8.5pt; }}
#lcd QProgressBar {{ border: 0; border-radius: 2px; background: {lcd_track}; max-height: 4px; min-height: 4px; }}
#lcd QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}

QLineEdit#search {{
  border: 1px solid {btn_line}; border-radius: 8px; padding: 4px 10px; background: {bg}; min-width: 200px;
}}
QLineEdit#search:focus {{ border-color: {accent}; }}

#sidebar {{ background: {side}; border: 0; border-right: 1px solid {tb_line}; outline: 0; color: {side_text}; }}
#sidebar::item {{ border: 0; margin: 1px 8px; border-radius: 6px; padding: 2px 4px; }}
#sidebar::item:hover {{ background: {side_hover}; }}
#sidebar::item:selected {{ color: {on_accent}; background: {accent}; }}
#sidebar::branch {{ background: {side}; image: none; border: 0; }}
#sideBox {{ background: {side}; }}
#sideBox #sidebar {{ border-right: 0; }}
QPushButton#sideSettings {{ text-align: left; margin: 6px 8px 10px 8px; padding: 4px 10px; border: 0;
  border-radius: 6px; background: transparent; color: {side_text}; }}
QPushButton#sideSettings:hover {{ background: {side_hover}; }}
QPushButton#sideSettings:checked {{ background: {accent}; color: {on_accent}; }}

#viewHead {{ background: {bg}; border-bottom: 1px solid {line}; }}
#viewTitle {{ font-size: 16pt; font-weight: 600; }}
#attention {{ background: {attention_bg}; border-bottom: 1px solid {attention_line}; }}
#attention QLabel {{ color: {attention_text}; }}
#attention QPushButton {{ min-height: 20px; padding: 0 10px; }}
#attention QPushButton#attentionClose {{ border: 0; background: transparent; color: {attention_text}; padding: 0 4px; }}
QLabel#dropHint {{ background: {drop_bg}; border: 2px dashed {accent}; border-radius: 12px;
  color: {accent}; font-size: 15pt; font-weight: 600; }}
#marksBar {{ background: {accent_soft}; border-bottom: 1px solid {accent}; }}
#marksBar QLabel {{ color: {text}; font-weight: 600; }}
#marksBar QPushButton {{ min-height: 20px; padding: 0 10px; }}
#empty QLabel#emptyTitle {{ font-size: 13pt; font-weight: 600; }}
QLabel[muted="true"] {{ color: {muted}; }}
QLabel[warn="true"] {{ color: {danger}; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QLabel#section {{ color: {muted}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; margin-top: 14px; }}

QPushButton {{
  min-height: 22px; padding: 1px 12px; border-radius: 6px;
  border: 1px solid {btn_line};
  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {btn_top}, stop:1 {btn_bottom});
}}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {faint}; border-color: {line}; }}
QPushButton[primary="true"] {{ background: {accent}; color: {on_accent}; border-color: {accent}; }}
QPushButton[primary="true"]:disabled {{ background: {line}; color: {faint}; border-color: {line}; }}
QPushButton[danger="true"] {{ color: {danger}; }}
QPushButton[seg="true"] {{ border-radius: 0; padding: 1px 12px; margin: 0; }}
QPushButton[seg="first"] {{ border-top-right-radius: 0; border-bottom-right-radius: 0; margin: 0; }}
QPushButton[seg="last"] {{ border-top-left-radius: 0; border-bottom-left-radius: 0; margin: 0; }}
QPushButton[seg]:checked {{ background: {accent}; color: {on_accent}; border-color: {accent}; }}
QPushButton[chip="true"] {{ border-radius: 11px; padding: 1px 11px; }}
QPushButton[chip="true"]:checked {{ background: {accent_soft}; color: {accent}; border-color: {accent}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
  border: 1px solid {line}; border-radius: 6px; padding: 3px 6px; background: {bg};
  selection-background-color: {accent};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}

/* drop-down lists: our chevron, and a popup that looks like the ☰ menu */
QComboBox {{ padding: 3px 26px 3px 8px; combobox-popup: 0; }}
QComboBox:hover {{ border-color: {btn_line}; }}
QComboBox:disabled {{ color: {faint}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: 0; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 6px; }}
QComboBox::down-arrow:disabled {{ image: url("{chevron_faint}"); }}
QComboBox QAbstractItemView {{
  background: {sheet}; border: 1px solid {line}; border-radius: 6px; padding: 4px; outline: 0;
  selection-background-color: {accent}; selection-color: {on_accent};
}}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 0 10px; border-radius: 5px; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
  background: {accent}; color: {on_accent};
}}

QTableView, QTreeView, QListView {{
  background: {bg}; alternate-background-color: {alt}; border: 0;
  selection-background-color: {accent}; selection-color: {on_accent}; gridline-color: {line};
}}
QListWidget#artists {{ background: {alt}; border-right: 1px solid {line}; outline: 0; }}
QListWidget#artists {{ padding-top: 6px; }}
QListWidget#artists::item {{ padding: 4px 8px; margin: 1px 8px; border: 0; border-radius: 6px; }}
QListWidget#artists::item:hover {{ background: {side_hover}; }}
QListWidget#artists::item:selected {{ background: {accent}; color: {on_accent}; }}
#settingsNav {{ background: {alt}; border-right: 1px solid {line}; }}
#settingsNav QListWidget#artists {{ border: 0; }}
QPushButton#fileLink {{ border: 0; background: transparent; color: {muted}; text-align: left;
  padding: 2px 12px; font-size: 8.5pt; }}
QPushButton#fileLink:hover {{ color: {accent}; }}
QHeaderView::section {{
  background: {bg}; color: {muted}; border: 0; border-bottom: 1px solid {line};
  padding: 4px 6px; font-weight: 600; font-size: 8.5pt;
}}
QTabWidget::pane {{ border: 0; }}
QTabBar::tab {{ padding: 4px 14px; border: 1px solid {btn_line}; border-bottom: 0; background: {btn_bottom}; }}
QTabBar::tab:selected {{ background: {accent}; color: {on_accent}; border-color: {accent}; }}
QMenu {{ background: {sheet}; border: 1px solid {line}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {accent}; color: {on_accent}; }}
QMenu::item:disabled {{ color: {faint}; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 4px 8px; }}

#card {{ background: {alt}; border: 1px solid {line}; border-radius: 10px; }}
#detail {{
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {detail_top}, stop:1 {detail_bottom});
}}
#detail QLabel {{ color: {detail_text}; background: transparent; }}
#detail QLabel[muted="true"] {{ color: {detail_muted}; }}
#detail QLabel#albumName {{ font-size: 16pt; font-weight: 600; }}
#detail QLabel#albumArtist {{ font-size: 12pt; }}
#detail QPushButton {{ background: {detail_btn}; border: 1px solid {detail_btn_line}; color: {detail_text}; }}
#detail QTableWidget {{ background: transparent; color: {detail_text}; alternate-background-color: {detail_alt}; }}
#detail QHeaderView, #detail QTableCornerButton::section {{ background: transparent; border: 0; }}
#detail QHeaderView::section {{ background: transparent; color: {detail_muted}; border-bottom: 1px solid {detail_line}; }}
QLabel#confirm, QLabel#bold {{ font-weight: 600; }}
QLabel#small {{ font-size: 8pt; }}
QLabel#ipodName {{ font-size: 15pt; font-weight: 600; }}
QLabel#toast {{ color: {toast_text}; background: {toast}; padding: 9px 14px; border-radius: 8px; }}
QLabel#toast[kind="good"] {{ background: {toast_good}; }}
QLabel#toast[kind="bad"] {{ background: {toast_bad}; }}

QPlainTextEdit#log {{ font-family: {mono}; font-size: 9pt; background: {bg}; }}
QProgressBar {{ border: 0; border-radius: 3px; background: {line}; max-height: 6px; min-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
QProgressBar[state="ok"]::chunk {{ background: {active}; }}
QProgressBar[state="bad"]::chunk {{ background: {danger}; }}
QScrollArea {{ border: 0; background: {bg}; }}
QSplitter::handle {{ background: {line}; }}
QToolTip {{ color: {text}; background: {sheet}; border: 1px solid {line}; }}
"""


@functools.cache
def _installed(kind):
    # only the installed ones: a missing family makes Qt rebuild its font aliases
    have = set(QFontDatabase.families())
    return [f for f in FONTS[kind] if f in have] or FONTS[kind][-1:]


def symbol_font(px):
    f = QFont()
    f.setFamilies(_installed("symbols"))
    f.setPixelSize(px)
    return f


def icon(name, color="text", size=None):
    """A one-colour icon drawn from a symbol; white when its row is selected."""
    size = size or UI["icon"]
    out = QIcon()
    for mode, key in ((QIcon.Mode.Normal, color), (QIcon.Mode.Selected, "on_accent"),
                      (QIcon.Mode.Active, color), (QIcon.Mode.Disabled, "faint")):
        pm = QPixmap(size * 2, size * 2)      # drawn at 2x: sharp on HiDPI screens
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(symbol_font(int(size * .9)))
        p.setPen(QColor(C[key]))
        p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, GLYPHS[name])
        p.end()
        out.addPixmap(pm, mode)
    return out


def _chevron(color):
    """A down chevron as a PNG file: a stylesheet can only take an image for the combo arrow."""
    path = os.path.join(tempfile.gettempdir(), f"podvault-chevron-{QColor(color).name()[1:]}.png")
    if not os.path.isfile(path):
        pm = QPixmap(20, 12)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(color), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                      Qt.PenJoinStyle.RoundJoin))
        p.drawPolyline([QPointF(3, 3), QPointF(10, 9), QPointF(17, 3)])
        p.end()
        pm.save(path)
    return path.replace(os.sep, "/")


class _ComboItems(QObject):
    """Qt's own popup delegate ignores the stylesheet's ::item rules; a styled one follows them."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Polish and isinstance(obj, QComboBox) \
                and not isinstance(obj.itemDelegate(), QStyledItemDelegate):
            obj.setItemDelegate(QStyledItemDelegate(obj))
        return False


def apply(app):
    C.clear()
    C.update(DARK if is_dark() else LIGHT)
    if not hasattr(app, "_combo_items"):
        app._combo_items = _ComboItems(app)
        app.installEventFilter(app._combo_items)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Base, "bg"), (QPalette.ColorRole.AlternateBase, "alt"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "btn_bottom"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.HighlightedText, "on_accent"),
                      (QPalette.ColorRole.PlaceholderText, "faint"),
                      (QPalette.ColorRole.ToolTipBase, "sheet"), (QPalette.ColorRole.ToolTipText, "text"),
                      (QPalette.ColorRole.Link, "accent")):
        pal.setColor(role, QColor(C[key]))
    app.setPalette(pal)
    app.setStyleSheet(QSS.format(**C, chevron=_chevron(C["muted"]), chevron_faint=_chevron(C["faint"]), mono=", ".join(f'"{f}"' for f in _installed("mono")) + ", monospace"))
