"""How the window looks: palettes, the symbols used as icons, fonts, sizes, timings.

The look is b1air's "Air", in its own Breeze palettes: a sidebar with sections, a page with its own header,
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
from PyQt6.QtWidgets import QComboBox, QMenu, QStyledItemDelegate

# Colours are "#RRGGBB" or "#AARRGGBB"; the ones only the stylesheet uses may be rgba().
# The two palettes are b1air's own built-ins, Breeze Dark (its default) and Breeze Light
# (src/shell/qml/Ui/Design.qml and Services/Theme.qml in b1air), on the same roles:
# ground -> the page, low -> sidebar and inspector, lowest -> the player, mid -> cards,
# high / highest -> controls, outlineVariant -> lines, primary -> the accent.
DARK = dict(
    # b1air Design: sunken = lowest (fields, tracks), surface = low (the window),
    # raised = mid (sidebar, cards, buttons), hover = high, active = highest,
    # glassBorder = text at 12 %, selection = accent at 22 %, veil = text at 5 %
    ground="#141618", well="#141618", bg="#1b1e20", text="#fcfcfc", muted="#b4bbc2", faint="#6b737a",
    line="#3a3f44", glass="#1ffcfcfc", veil="#0dfcfcfc",
    alt="#292c30", hover="#31363b", fill="#292c30", fill_hover="#31363b", fill_press="#3e434a",
    side="#292c30", side_text="#fcfcfc", side_head="#6b737a", side_hover="#0dfcfcfc", side_line="#1ffcfcfc",
    inspector="#1b1e20", player="#141618", player_line="#3a3f44",
    accent="#3daee9", accent_hover="#52b8ec", accent_soft="#383daee9", accent_line="#593daee9",
    accent_text="#7fcbf1", on_accent="#0d1b24",
    active="#2ecc71", archive="#f67400", danger="#ed4b5b", pending="#e93a9a",
    ok_soft="#1d3b2b", warn_soft="#3f3420", warn_text="#fdbc4b", ok_text="#5fd896",
    sheet="#292c30",
    # kept for pieces that still read them
    tb_top="#202326", tb_bottom="#202326", tb_line="#3a3f44",
    btn_top="#31363b", btn_bottom="#31363b", btn_line="#3a3f44",
    lcd_track="#32ffffff",
    attention_bg="#3f3420", attention_line="#4d3f25", attention_text="#fdbc4b",
    detail_top="#1b1e20", detail_bottom="#1b1e20", detail_text="#fcfcfc", detail_muted="#b4bbc2",
    detail_warn="#ed4b5b", detail_btn="#31363b", detail_btn_line="#3a3f44",
    detail_alt="rgba(255,255,255,6)", detail_line="#3a3f44",
    # album tiles
    flag_text="#ffffff", art_initials="#e6ffffff", art_shadow="#40000000",
    art_dim="#80202326", selection_fill="#38000000",
    # the iPod picture
    ipod_black_top="#48484b", ipod_black_bottom="#0e0e10",
    ipod_white_top="#ffffff", ipod_white_bottom="#d6d6d6", ipod_outline="#3c000000",
    ipod_screen_top="#b6dcf2", ipod_screen_bottom="#3d8fc9", ipod_screen_frame="#96000000",
    ipod_screen_text="#ffffff",
    wheel_black_top="#3f3f42", wheel_black_bottom="#1b1b1d", wheel_black_text="#9a9a9a",
    wheel_white_top="#f0f0f0", wheel_white_bottom="#bdbdbd", wheel_white_text="#7a7a7a",
    wheel_line="#32000000",
    # the capacity bars
    cap_audio_top="#7fcbf1", cap_audio="#3daee9", cap_other_top="#fdd27e", cap_other="#fdbc4b",
    # notifications
    toast="rgba(20,22,24,240)", toast_good="rgba(30,110,65,245)", toast_bad="rgba(180,50,60,245)",
    toast_text="#ffffff",
    drop_bg="rgba(32,35,38,225)",
)

LIGHT = dict(
    DARK,
    ground="#dee0e2", well="#dee0e2", bg="#f7f7f8", text="#232629", muted="#4d5257", faint="#7f868d",
    line="#c8ccd0", glass="#1f232629", veil="#0d232629",
    alt="#ffffff", hover="#e5e7e9", fill="#ffffff", fill_hover="#e5e7e9", fill_press="#d3d6da",
    side="#ffffff", side_text="#232629", side_head="#7f868d", side_hover="#0d232629", side_line="#1f232629",
    inspector="#f7f7f8", player="#eff0f1", player_line="#c8ccd0",
    accent="#1f7ac2", accent_hover="#2a86cf", accent_soft="#381f7ac2", accent_line="#591f7ac2",
    accent_text="#17639e", on_accent="#ffffff",
    active="#1e8a4b", archive="#d35400", danger="#c62b3b", pending="#c2185b",
    ok_soft="#d5eedf", warn_soft="#f5e6c6", warn_text="#7a4f00", ok_text="#1e8a4b",
    sheet="#ffffff",
    tb_top="#eff0f1", tb_bottom="#eff0f1", tb_line="#c8ccd0",
    btn_top="#e5e7e9", btn_bottom="#e5e7e9", btn_line="#c8ccd0",
    lcd_track="#1e000000",
    attention_bg="#f5e6c6", attention_line="#ecd9ad", attention_text="#7a4f00",
    detail_top="#f7f7f8", detail_bottom="#f7f7f8", detail_text="#232629", detail_muted="#4d5257",
    detail_warn="#c62b3b", detail_btn="#e5e7e9", detail_btn_line="#c8ccd0",
    detail_alt="rgba(0,0,0,6)", detail_line="#c8ccd0",
    art_dim="#80eff0f1", art_shadow="#26000000", selection_fill="#24000000",
    cap_audio_top="#4f9ad6", cap_audio="#1f7ac2", cap_other_top="#e0a64a", cap_other="#a86b00",
    toast="rgba(35,38,41,235)", drop_bg="rgba(239,240,241,225)",
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
    side_w=224, side_row=36, head_h=56, control_h=32, seg_h=26, chip_h=26, player_h=72, player_cover=44, inspector_w=420,
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
    ground="lowest", well="lowest", bg="low", sheet="mid", alt="mid", hover="high", fill="mid",
    fill_hover="high", fill_press="highest", side="mid", inspector="low", player="lowest",
    text="text", muted="textDim", faint="outline", line="outlineVariant", side_line="outlineVariant",
    player_line="outlineVariant",
    side_text="text", side_head="outline",
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
#side {{ background: {side}; border: 1px solid {glass}; border-radius: 12px; }}
QLineEdit#search {{ border: 1px solid {line}; border-radius: 8px; padding: 5px 10px; background: {well}; min-height: 20px; }}
QLineEdit#search:focus {{ border-color: {accent}; }}
QLabel#sideHead {{ color: {side_head}; font-size: 8pt; font-weight: 600; letter-spacing: 1px; padding: 0 8px; }}
QPushButton#sideAdd {{ border: 0; background: transparent; color: {side_head}; font-size: 13pt;
  min-height: 18px; max-width: 22px; padding: 0; }}
QPushButton#sideAdd:hover {{ color: {text}; }}
QPushButton#sideRow {{ text-align: left; border: 0; border-radius: 8px; padding: 0 8px; min-height: 32px;
  background: transparent; color: {side_text}; }}
QPushButton#sideRow:hover {{ background: {side_hover}; }}
QPushButton#sideRow:checked {{ background: {accent_soft}; color: {text}; font-weight: 600; }}
QLabel#sideCount {{ color: {muted}; font-size: 8pt; background: {well}; border-radius: 9px; padding: 1px 7px;
  min-height: 16px; }}
#deviceCard {{ background: {well}; border: 1px solid {glass}; border-radius: 12px; }}
#deviceCard[current="true"] {{ border-color: {accent}; }}
#deviceCard QLabel {{ background: transparent; }}
QLabel#deviceName {{ font-weight: 600; }}
#deviceCard QProgressBar {{ max-height: 6px; min-height: 6px; border-radius: 3px; background: {fill}; }}
#deviceCard QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
#activity {{ background: {well}; border: 1px solid {glass}; border-radius: 10px; }}
#activity QLabel {{ background: transparent; }}
#activity QProgressBar {{ max-height: 4px; min-height: 4px; border-radius: 2px; background: {fill}; }}
QLabel#summary {{ color: {muted}; font-size: 8.5pt; padding: 0 12px; }}

/* a page */
#viewHead {{ background: {bg}; border-bottom: 1px solid {line}; }}
#viewTitle {{ font-size: 15pt; font-weight: 600; }}
#chips {{ background: {bg}; }}
QPushButton[chip="warn"] {{ border: 1px solid {attention_line}; border-radius: 13px; padding: 0 12px;
  min-height: 24px; max-height: 24px; background: {warn_soft}; color: {warn_text}; font-size: 9pt; }}
QPushButton[chip="warn"]:hover {{ background: {attention_line}; }}
QPushButton[chip="true"] {{ border: 1px solid {glass}; border-radius: 13px; padding: 0 12px; min-height: 24px;
  max-height: 24px; background: {fill}; color: {text}; font-size: 9pt; }}
QPushButton[chip="true"]:hover {{ background: {fill_hover}; }}
QPushButton[chip="true"]:checked {{ background: {accent_soft}; border-color: {accent_line}; color: {accent_text}; }}
QComboBox[chip="true"] {{ border: 1px solid {glass}; border-radius: 13px; min-height: 24px; max-height: 24px;
  background: {fill}; font-size: 9pt; padding: 0 26px 0 12px; }}
QComboBox[chip="true"]:hover {{ background: {fill_hover}; }}
QLabel#dropHint {{ background: {drop_bg}; border: 2px dashed {accent}; border-radius: 16px;
  color: {accent}; font-size: 15pt; font-weight: 600; }}
#marksBar {{ background: {accent_soft}; }}
#marksBar QLabel {{ color: {text}; font-weight: 600; }}
#empty QLabel#emptyTitle {{ font-size: 13pt; font-weight: 600; }}
QLabel[muted="true"] {{ color: {muted}; }}
QLabel[warn="true"] {{ color: {danger}; }}
QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
QLabel#section {{ color: {side_head}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; margin-top: 14px; }}

/* buttons, as b1air's: a raised pane with a hairline; the primary one is the accent */
QPushButton {{
  min-height: 26px; max-height: 26px; padding: 2px 14px; border-radius: 8px; border: 1px solid {glass};
  background: {fill}; color: {text};
}}
QPushButton:hover {{ background: {fill_hover}; }}
QPushButton:pressed {{ background: {fill_press}; }}
QPushButton:checked {{ background: {accent_soft}; border-color: {accent_line}; }}
QPushButton:disabled {{ color: {faint}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QPushButton[primary="true"] {{ background: {accent}; color: {on_accent}; border: 1px solid {accent};
  font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton[primary="true"]:disabled {{ background: {fill}; color: {faint}; border: 1px solid {glass}; }}
QPushButton[danger="true"] {{ color: {danger}; }}
QPushButton[flat="true"] {{ background: transparent; border: 0; padding: 0 6px; font-size: 11pt; color: {muted}; }}
QPushButton[flat="true"]:hover {{ background: {veil}; color: {text}; }}
/* the segmented control: a recessed track, equal segments, the chosen one a raised pane
   filling its segment (b1air Ui/SegmentedControl) */
#segments {{ background: {well}; border: 1px solid {glass}; border-radius: 8px; }}
QPushButton[seg] {{ border: 1px solid transparent; border-radius: 6px; padding: 0 12px; margin: 0;
  background: transparent; color: {muted}; }}
QPushButton[seg]:hover {{ color: {text}; background: transparent; }}
QPushButton[seg]:checked {{ background: {fill}; border: 1px solid {glass}; color: {text}; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
  border: 1px solid {line}; border-radius: 8px; padding: 4px 8px; background: {well};
  selection-background-color: {accent}; selection-color: {on_accent};
}}
QComboBox:hover {{ background: {hover}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {accent}; }}

/* drop-down lists: our chevron, and a popup that looks like the menus */
QComboBox {{ padding: 4px 26px 4px 10px; combobox-popup: 0; }}
QComboBox:disabled {{ color: {faint}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: 0; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 6px; }}
QComboBox::down-arrow:disabled {{ image: url("{chevron_faint}"); }}
QComboBox QAbstractItemView {{
  background: {sheet}; border: 1px solid {glass}; border-radius: 10px; padding: 4px; outline: 0;
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
QTabBar::tab:selected {{ background: {fill}; border: 1px solid {glass}; color: {text}; font-weight: 600; }}
QMenu {{ background: {sheet}; border: 1px solid {glass}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {accent_soft}; color: {text}; }}
QMenu::item:disabled {{ color: {faint}; font-size: 8pt; font-weight: 700; }}
QMenu::separator {{ height: 1px; background: {line}; margin: 5px 8px; }}

#card {{ background: {alt}; border: 1px solid {glass}; border-radius: 12px; }}

/* an opened album: the inspector on the right */
#detail {{ background: {inspector}; border-left: 1px solid {line}; }}
#chips, #viewHead {{ background: {bg}; }}
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
QToolTip {{ color: {text}; background: {sheet}; border: 1px solid {line}; padding: 4px 6px; }}
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
    for mode, key, state in ((QIcon.Mode.Normal, color, QIcon.State.Off), (QIcon.Mode.Selected, "on_accent",
                             QIcon.State.Off), (QIcon.Mode.Active, color, QIcon.State.Off),
                             (QIcon.Mode.Disabled, "faint", QIcon.State.Off),
                             (QIcon.Mode.Normal, "accent", QIcon.State.On), (QIcon.Mode.Active, "accent", QIcon.State.On)):
        pm = QPixmap(size * 2, size * 2)      # drawn at 2x: sharp on HiDPI screens
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(symbol_font(int(size * .9)))
        p.setPen(QColor(C[key]))
        p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, GLYPHS[name])
        p.end()
        out.addPixmap(pm, mode, state)
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


def _round_popup(window):
    """A menu or a drop-down list is a window of its own: rounded by the stylesheet, its
    square corners still showed the window's colour behind them. See-through and frameless,
    only the rounded panel is drawn."""
    if window is None or window.property("podvaultRound"):
        return
    window.setProperty("podvaultRound", True)
    window.setWindowFlags(window.windowFlags() | Qt.WindowType.FramelessWindowHint
                          | Qt.WindowType.NoDropShadowWindowHint)
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class _ComboItems(QObject):
    """Qt's own popup delegate ignores the stylesheet's ::item rules; a styled one follows them.
    And popups get rounded corners without a square backing (_round_popup)."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Polish:
            if isinstance(obj, QComboBox):
                if not isinstance(obj.itemDelegate(), QStyledItemDelegate):
                    obj.setItemDelegate(QStyledItemDelegate(obj))
                container = obj.view().parentWidget()
                if container is not None:
                    # its own selector: a bare rule would make the list inside see-through too
                    container.setStyleSheet(f"{container.metaObject().className()} "
                                            "{ background: transparent; border: 0; }")
                    _round_popup(container)
            elif isinstance(obj, QMenu):
                _round_popup(obj)
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
