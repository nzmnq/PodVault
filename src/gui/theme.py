"""How the window looks: palettes, the symbols used as icons, fonts, sizes, timings.

The look is b1air's "Air": a sidebar with sections, a page with its own header,
an inspector on the right, a full-width player at the bottom; flat surfaces,
one accent for what is chosen or the main action, rounded corners (8 for
controls, 12 for cards, 16 for panels). Dark by default, light when the system
is light. Nothing visual is hard-coded elsewhere.

On the b1air desktop the colours come from its theme instead
(~/.config/b1air/theme.json, written by its Settings → Appearance): the
surfaces, text, lines and the accent follow the desktop.
"""

import functools
import json
import os
import tempfile

from PyQt6.QtCore import QEvent, QObject, QPointF, QRect, Qt
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPalette, QPen,
                         QPixmap)
from PyQt6.QtWidgets import QComboBox, QStyledItemDelegate

# Colours are "#RRGGBB" or "#AARRGGBB"; the ones only the stylesheet uses may be rgba().
DARK = dict(
    ground="#231c33", bg="#2b2340", text="#ebe8f2", muted="#a39fb6", faint="#8a85a0", line="#3a3150",
    alt="#2f2745", hover="#372e4f", fill="#3a3150", fill_hover="#433a5a",
    side="#241d36", side_text="#ebe8f2", side_head="#8a85a0", side_hover="#2f2745", side_line="#30283f",
    inspector="#271f3a", player="#1f1930", player_line="#3a3150",
    accent="#5aa9e6", accent_soft="#2f4565", accent_text="#9fd2f7", on_accent="#0d1b29",
    active="#5cc98a", archive="#e8b35a", danger="#ef7a6f", pending="#e07fb5",
    ok_soft="#2c4a3f", warn_soft="#4a3d2c", warn_text="#f0c77e", ok_text="#7fd9a5",
    sheet="#2f2745",
    # kept for pieces that still read them
    tb_top="#2b2340", tb_bottom="#2b2340", tb_line="#3a3150",
    btn_top="#3a3150", btn_bottom="#3a3150", btn_line="#3a3150",
    lcd_track="#32ffffff",
    attention_bg="#4a3d2c", attention_line="#4a3d2c", attention_text="#f0c77e",
    detail_top="#271f3a", detail_bottom="#271f3a", detail_text="#ebe8f2", detail_muted="#a39fb6",
    detail_warn="#ef7a6f", detail_btn="#3a3150", detail_btn_line="#3a3150",
    detail_alt="rgba(255,255,255,6)", detail_line="#3a3150",
    # album tiles
    flag_text="#ffffff", art_initials="#e6ffffff", art_shadow="#40000000",
    art_dim="#80231c33", selection_fill="#38000000",
    # the iPod picture
    ipod_black_top="#48484b", ipod_black_bottom="#0e0e10",
    ipod_white_top="#ffffff", ipod_white_bottom="#d6d6d6", ipod_outline="#3c000000",
    ipod_screen_top="#b6d6f2", ipod_screen_bottom="#4f8fc9", ipod_screen_frame="#96000000",
    ipod_screen_text="#ffffff",
    wheel_black_top="#3f3f42", wheel_black_bottom="#1b1b1d", wheel_black_text="#9a9a9a",
    wheel_white_top="#f0f0f0", wheel_white_bottom="#bdbdbd", wheel_white_text="#7a7a7a",
    wheel_line="#32000000",
    # the capacity bars
    cap_audio_top="#7cc0f0", cap_audio="#5aa9e6", cap_other_top="#f0c77e", cap_other="#e8b35a",
    # notifications
    toast="rgba(31,25,48,240)", toast_good="rgba(44,110,80,245)", toast_bad="rgba(170,60,55,245)",
    toast_text="#ffffff",
    drop_bg="rgba(35,28,51,225)",
)

LIGHT = dict(
    DARK,
    ground="#e9e6f1", bg="#f7f6fb", text="#1d1a26", muted="#615c72", faint="#8e89a0", line="#e2dfea",
    alt="#f1eff7", hover="#e9e6f1", fill="#e9e6f1", fill_hover="#dfdbea",
    side="#efedf5", side_text="#1d1a26", side_head="#7b768c", side_hover="#e4e1ec", side_line="#e2dfea",
    inspector="#f1eff7", player="#ebe9f2", player_line="#dcd8e6",
    accent="#2f80c9", accent_soft="#d6e7f6", accent_text="#1f63a3", on_accent="#ffffff",
    active="#2f9e5a", archive="#c27a14", danger="#c9443b", pending="#c2448a",
    ok_soft="#dcf1e4", warn_soft="#f8ecd3", warn_text="#7a5310", ok_text="#22764a",
    sheet="#ffffff",
    tb_top="#f7f6fb", tb_bottom="#f7f6fb", tb_line="#e2dfea",
    btn_top="#e9e6f1", btn_bottom="#e9e6f1", btn_line="#e2dfea",
    lcd_track="#1e000000",
    attention_bg="#f8ecd3", attention_line="#f8ecd3", attention_text="#7a5310",
    detail_top="#f1eff7", detail_bottom="#f1eff7", detail_text="#1d1a26", detail_muted="#615c72",
    detail_warn="#c9443b", detail_btn="#e9e6f1", detail_btn_line="#e2dfea",
    detail_alt="rgba(0,0,0,6)", detail_line="#e2dfea",
    art_dim="#80f7f6fb", art_shadow="#26000000", selection_fill="#24000000",
    toast="rgba(29,26,38,235)", drop_bg="rgba(247,246,251,225)",
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
    play="▶",              # black right-pointing triangle
    pause="❚❚",            # two heavy vertical bars
    prev="⏮",              # black left-pointing double triangle with vertical bar
    next="⏭",              # black right-pointing double triangle with vertical bar
    volume="🔈︎",            # speaker, the text form (U+FE0E), not the emoji
    albums="▦",            # square with orthogonal crosshatch
    artists="◉",           # fisheye
    genres="≣",            # strictly equivalent to
    tools="⚙︎",             # gear, text form
    settings="☼",          # white sun with rays
)

UI = dict(
    window=(1360, 860), window_min=(1040, 640), sheet=(900, 600), dialog_w=560,
    side_w=224, side_row=36, head_h=56, player_h=72, player_cover=44, inspector_w=420,
    icon=16, gap=8,
    tile=150, tile_range=(100, 240), thumb=256, cover_large=640, detail_cover=148,
    artists_w=190, ipod_picture=(112, 184), capacity_h=40, vibe_text_h=80,
    close_button_w=30, form_w=760, form_label_w=190, form_number_w=120, slider_w=110, combo_extra_w=44, genre_box_max_w=260,
    poll_ms=3000, sheet_refresh_ms=150, player_refresh_ms=250, restart_ms=3000, eject_repoll_ms=1500,
    note_s=6, toast_ms=5000, toast_bad_ms=8000, toast_w=420, toast_gap=14, list_preview=12,
    cover_threads=3, cache_max=3000, log_lines=10000,
)

# Table column widths, px.
COLUMNS = dict(num=36, state=30, time=56, kbps=44, cover=54, plays=50, count=80,
               artist=170, album=190, genre=140, style=260)

C = dict(LIGHT)   # the colours in use; filled by apply()

B1AIR_THEME = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                           "b1air", "theme.json")

# our key <- the b1air palette's key (src/shell/qml/Services/Theme.qml in b1air)
B1AIR = dict(
    ground="lowest", bg="ground", sheet="mid", alt="low", hover="high", fill="high", fill_hover="highest",
    inspector="low", player="lowest",
    text="text", muted="textDim", faint="outline", line="outlineVariant", side_line="outlineVariant",
    player_line="outlineVariant",
    side="low", side_text="text", side_head="textDim", side_hover="high",
    accent="primary", on_accent="primaryText", accent_soft="primaryBox", accent_text="primary",
    cap_audio_top="primary", cap_audio="primary",
    active="green", archive="peach", danger="error", pending="pink",
)


def _luminance(c):
    """Relative luminance, the way b1air-daemon tells a dark palette from a light one."""
    lin = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in (c.redF(), c.greenF(), c.blueF())]
    return .2126 * lin[0] + .7152 * lin[1] + .0722 * lin[2]


def b1air_palette():
    """Our colours with the b1air desktop's over them, or None outside b1air."""
    try:
        with open(B1AIR_THEME, encoding="utf-8") as f:
            theirs = json.load(f)
        ground = QColor(theirs["ground"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not ground.isValid():
        return None
    # ponytail: follows the palette's own darkness; b1air's "apps light/dark" override isn't read
    out = dict(DARK if _luminance(ground) < .4 else LIGHT)
    for ours, key in B1AIR.items():
        v = theirs.get(key)
        if isinstance(v, str) and QColor(v).isValid():
            out[ours] = v
    return out


def is_light():
    """Light only when the system says so: the Air look is dark by default."""
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light
    except AttributeError:
        return False


QSS = """
QWidget {{ color: {text}; font-size: 10pt; }}
QMainWindow, QStackedWidget, #page {{ background: {bg}; }}
#shell {{ background: {bg}; }}

/* the sidebar */
#side {{ background: {side}; border-right: 1px solid {side_line}; }}
QLineEdit#search {{ border: 0; border-radius: 8px; padding: 6px 12px; background: {fill}; min-height: 20px; }}
QLineEdit#search:focus {{ background: {fill_hover}; }}
QLabel#sideHead {{ color: {side_head}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; padding: 0 12px; }}
QPushButton#sideAdd {{ border: 0; background: transparent; color: {side_head}; font-size: 13pt;
  min-height: 18px; max-width: 22px; padding: 0; }}
QPushButton#sideAdd:hover {{ color: {text}; }}
QPushButton#sideRow {{ text-align: left; border: 0; border-radius: 8px; padding: 0 12px; min-height: 36px;
  background: transparent; color: {side_text}; }}
QPushButton#sideRow:hover {{ background: {side_hover}; }}
QPushButton#sideRow:checked {{ background: {accent_soft}; color: {text}; font-weight: 600; }}
QLabel#sideCount {{ color: {muted}; font-size: 8.5pt; background: transparent; }}
#deviceCard {{ background: {alt}; border: 1px solid {side_line}; border-radius: 12px; }}
#deviceCard[current="true"] {{ border-color: {accent}; }}
#deviceCard QLabel {{ background: transparent; }}
QLabel#deviceName {{ font-weight: 600; }}
#deviceCard QProgressBar {{ max-height: 6px; min-height: 6px; border-radius: 3px; background: {fill}; }}
#deviceCard QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
#activity {{ background: {alt}; border-radius: 10px; }}
#activity QLabel {{ background: transparent; }}
#activity QProgressBar {{ max-height: 4px; min-height: 4px; border-radius: 2px; background: {fill}; }}
QLabel#summary {{ color: {muted}; font-size: 8.5pt; padding: 0 12px; }}

/* a page */
#viewHead {{ background: {bg}; border-bottom: 1px solid {line}; }}
#viewTitle {{ font-size: 15pt; font-weight: 600; }}
#chips {{ background: {bg}; }}
QPushButton[chip="warn"] {{ border: 0; border-radius: 12px; padding: 0 12px; min-height: 24px;
  background: {warn_soft}; color: {warn_text}; font-size: 9pt; }}
QPushButton[chip="warn"]:hover {{ background: {attention_line}; }}
QPushButton[chip="true"] {{ border: 0; border-radius: 12px; padding: 0 12px; min-height: 24px;
  background: {fill}; color: {muted}; font-size: 9pt; }}
QPushButton[chip="true"]:hover {{ background: {fill_hover}; color: {text}; }}
QPushButton[chip="true"]:checked {{ background: {accent_soft}; color: {accent_text}; }}
QComboBox[chip="true"] {{ border: 0; border-radius: 12px; min-height: 24px; background: {fill}; color: {muted};
  font-size: 9pt; padding: 0 26px 0 12px; }}
QLabel#dropHint {{ background: {drop_bg}; border: 2px dashed {accent}; border-radius: 16px;
  color: {accent}; font-size: 15pt; font-weight: 600; }}
#marksBar {{ background: {accent_soft}; }}
#marksBar QLabel {{ color: {text}; font-weight: 600; }}
#empty QLabel#emptyTitle {{ font-size: 13pt; font-weight: 600; }}
QLabel[muted="true"] {{ color: {muted}; }}
QLabel[warn="true"] {{ color: {danger}; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QLabel#section {{ color: {side_head}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; margin-top: 14px; }}

/* buttons: flat fills, one accent */
QPushButton {{
  min-height: 24px; padding: 2px 14px; border-radius: 8px; border: 0; background: {fill}; color: {text};
}}
QPushButton:hover {{ background: {fill_hover}; }}
QPushButton:pressed {{ background: {hover}; }}
QPushButton:disabled {{ color: {faint}; background: {alt}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QPushButton[primary="true"] {{ background: {accent}; color: {on_accent}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {accent_text}; }}
QPushButton[primary="true"]:disabled {{ background: {fill}; color: {faint}; }}
QPushButton[danger="true"] {{ color: {danger}; }}
QPushButton[flat="true"] {{ background: transparent; padding: 0 6px; font-size: 11pt; color: {muted}; }}
QPushButton[flat="true"]:hover {{ background: {fill}; }}
#segments {{ background: {fill}; border-radius: 8px; }}
QPushButton[seg] {{ border-radius: 6px; padding: 0 14px; margin: 2px; min-height: 24px; background: transparent;
  color: {muted}; }}
QPushButton[seg]:hover {{ color: {text}; }}
QPushButton[seg]:checked {{ background: {fill_hover}; color: {text}; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
  border: 1px solid {line}; border-radius: 8px; padding: 4px 8px; background: {alt};
  selection-background-color: {accent}; selection-color: {on_accent};
}}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}

/* drop-down lists: our chevron, and a popup that looks like the menus */
QComboBox {{ padding: 4px 26px 4px 10px; combobox-popup: 0; }}
QComboBox:disabled {{ color: {faint}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: 0; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 6px; }}
QComboBox::down-arrow:disabled {{ image: url("{chevron_faint}"); }}
QComboBox QAbstractItemView {{
  background: {sheet}; border: 1px solid {line}; border-radius: 8px; padding: 4px; outline: 0;
  selection-background-color: {accent_soft}; selection-color: {text};
}}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 10px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
  background: {accent_soft}; color: {text};
}}

QTableView, QTreeView, QListView {{
  background: {bg}; alternate-background-color: {alt}; border: 0; outline: 0;
  selection-background-color: {accent_soft}; selection-color: {text}; gridline-color: {line};
}}
QTableView::item {{ padding: 0 6px; }}
QListWidget#artists {{ background: {bg}; border-right: 1px solid {line}; outline: 0; padding-top: 6px; }}
QListWidget#artists::item {{ padding: 6px 10px; margin: 1px 8px; border: 0; border-radius: 8px; }}
QListWidget#artists::item:hover {{ background: {hover}; }}
QListWidget#artists::item:selected {{ background: {accent_soft}; color: {text}; }}
#settingsNav {{ background: {side}; border-right: 1px solid {line}; }}
#settingsNav QListWidget#artists {{ border: 0; background: {side}; }}
QPushButton#fileLink {{ border: 0; background: transparent; color: {muted}; text-align: left;
  padding: 2px 12px; font-size: 8.5pt; }}
QPushButton#fileLink:hover {{ color: {accent}; }}
QHeaderView::section {{
  background: {bg}; color: {faint}; border: 0; border-bottom: 1px solid {line};
  padding: 6px 6px; font-weight: 700; font-size: 8pt;
}}
QTabWidget::pane {{ border: 0; }}
QTabBar::tab {{ padding: 5px 16px; margin-right: 4px; border: 0; border-radius: 8px; background: transparent;
  color: {muted}; }}
QTabBar::tab:selected {{ background: {fill}; color: {text}; font-weight: 600; }}
QMenu {{ background: {sheet}; border: 1px solid {line}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent_soft}; color: {text}; }}
QMenu::item:disabled {{ color: {faint}; font-size: 8pt; font-weight: 700; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 5px 8px; }}

#card {{ background: {alt}; border: 1px solid {line}; border-radius: 12px; }}

/* an opened album: the inspector on the right */
#detail {{ background: {inspector}; border-left: 1px solid {line}; }}
#detail QLabel {{ background: transparent; }}
#detail QLabel#albumName {{ font-size: 14pt; font-weight: 600; }}
#detail QLabel#albumArtist {{ color: {muted}; }}
#detail QTableWidget {{ background: transparent; alternate-background-color: transparent; }}
#detail QHeaderView, #detail QTableCornerButton::section {{ background: transparent; border: 0; }}
#detail QHeaderView::section {{ background: transparent; }}
QLabel#statePill {{ border-radius: 9px; padding: 2px 10px; font-size: 9pt; }}
QLabel#statePill[state="A"] {{ background: {ok_soft}; color: {ok_text}; }}
QLabel#statePill[state="R"] {{ background: {warn_soft}; color: {warn_text}; }}
QLabel#statePill[state="moved"] {{ background: {accent_soft}; color: {accent_text}; }}

/* the player, along the whole bottom */
#player {{ background: {player}; border-top: 1px solid {player_line}; }}
#player QLabel {{ background: transparent; }}
QLabel#playerTitle {{ font-weight: 600; }}
QLabel#playerSub, QLabel#playerTime {{ color: {muted}; font-size: 8.5pt; }}
QPushButton#playerBtn {{ border: 0; border-radius: 8px; background: transparent; min-width: 32px; max-width: 32px;
  min-height: 32px; max-height: 32px; padding: 0; font-size: 12pt; color: {text}; }}
QPushButton#playerBtn:hover {{ background: {fill}; }}
QPushButton#playerPlay {{ border: 0; border-radius: 18px; background: {text}; color: {player};
  min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; padding: 0; font-size: 12pt; }}
QSlider::groove:horizontal {{ height: 4px; border-radius: 2px; background: {fill}; }}
QSlider::sub-page:horizontal {{ height: 4px; border-radius: 2px; background: {accent}; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; background: {text}; }}
QSlider#volume::sub-page:horizontal {{ background: {muted}; }}

QLabel#confirm, QLabel#bold {{ font-weight: 600; }}
QLabel#small {{ font-size: 8pt; }}
QLabel#ipodName {{ font-size: 15pt; font-weight: 600; }}
QLabel#metric {{ font-size: 20pt; font-weight: 600; }}
QLabel#toast {{ color: {toast_text}; background: {toast}; padding: 10px 14px; border-radius: 10px; }}
QLabel#toast[kind="good"] {{ background: {toast_good}; }}
QLabel#toast[kind="bad"] {{ background: {toast_bad}; }}

QPlainTextEdit#log {{ font-family: {mono}; font-size: 9pt; background: {alt}; }}
QProgressBar {{ border: 0; border-radius: 3px; background: {fill}; max-height: 6px; min-height: 6px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
QProgressBar[state="ok"]::chunk {{ background: {active}; }}
QProgressBar[state="bad"]::chunk {{ background: {danger}; }}
QScrollArea {{ border: 0; background: {bg}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {fill_hover}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {fill_hover}; border-radius: 3px; min-width: 30px; }}
QSplitter::handle {{ background: {line}; }}
QToolTip {{ color: {text}; background: {sheet}; border: 1px solid {line}; border-radius: 6px; padding: 4px 6px; }}
QDialog {{ background: {bg}; }}
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
    b1air = b1air_palette()
    C.clear()
    C.update(b1air or (LIGHT if is_light() else DARK))
    if "Inter" in QFontDatabase.families():    # b1air's face, where it's installed
        f = app.font()
        f.setFamily("Inter")
        app.setFont(f)
    if not hasattr(app, "_combo_items"):
        app._combo_items = _ComboItems(app)
        app.installEventFilter(app._combo_items)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.WindowText, "text"),
                      (QPalette.ColorRole.Base, "bg"), (QPalette.ColorRole.AlternateBase, "alt"),
                      (QPalette.ColorRole.Text, "text"), (QPalette.ColorRole.Button, "fill"),
                      (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "accent"),
                      (QPalette.ColorRole.HighlightedText, "on_accent"),
                      (QPalette.ColorRole.PlaceholderText, "faint"),
                      (QPalette.ColorRole.ToolTipBase, "sheet"), (QPalette.ColorRole.ToolTipText, "text"),
                      (QPalette.ColorRole.Link, "accent")):
        pal.setColor(role, QColor(C[key]))
    app.setPalette(pal)
    app.setStyleSheet(QSS.format(**C, chevron=_chevron(C["muted"]), chevron_faint=_chevron(C["faint"]), mono=", ".join(f'"{f}"' for f in _installed("mono")) + ", monospace"))
