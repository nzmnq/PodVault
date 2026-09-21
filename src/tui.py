"""
A small toolkit for the text interface.

No third-party libraries: Windows has no curses, and pulling in a package
just for menus isn't worth it — the portable Python build already let us
down once by shipping without root certificates. Only msvcrt and ANSI
codes, which Windows 10 understands if asked to.
"""

import os
import sys

try:
    import msvcrt
except ImportError:  # in case this ever runs outside Windows
    msvcrt = None

ESC = "\x1b"
RESET = f"{ESC}[0m"
BOLD = f"{ESC}[1m"
DIM = f"{ESC}[2m"
INV = f"{ESC}[7m"

FG = {
    "red": f"{ESC}[31m", "green": f"{ESC}[32m", "yellow": f"{ESC}[33m",
    "blue": f"{ESC}[34m", "magenta": f"{ESC}[35m", "cyan": f"{ESC}[36m",
    "white": f"{ESC}[37m", "grey": f"{ESC}[90m",
}

# codes returned by read_key()
UP, DOWN, LEFT, RIGHT = "UP", "DOWN", "LEFT", "RIGHT"
HOME, END, PGUP, PGDN, DEL = "HOME", "END", "PGUP", "PGDN", "DEL"
ENTER, ESCAPE, BACKSPACE, TAB = "ENTER", "ESCAPE", "BACKSPACE", "TAB"

_SPECIAL = {
    "H": UP, "P": DOWN, "K": LEFT, "M": RIGHT,
    "G": HOME, "O": END, "I": PGUP, "Q": PGDN, "S": DEL,
}


def enable_ansi():
    """Ask the Windows console to interpret ANSI codes.

    Before Windows 10 1511 this mode doesn't exist, and the interface shows
    garbage instead of colours — but it keeps working, so stay quiet.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(mode)):
            return False
        return bool(k.SetConsoleMode(h, mode.value | 0x0004))
    except Exception:
        return False


def size():
    try:
        s = os.get_terminal_size()
        return max(s.columns, 60), max(s.lines, 15)
    except OSError:
        return 100, 30


def clear():
    sys.stdout.write(f"{ESC}[2J{ESC}[H")


def hide_cursor():
    sys.stdout.write(f"{ESC}[?25l")


def show_cursor():
    sys.stdout.write(f"{ESC}[?25h")


def flush():
    sys.stdout.flush()


def read_key():
    """Wait for a key. Arrows and other special keys come back as names."""
    if msvcrt is None:
        return sys.stdin.read(1)
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):          # special-key prefix
        return _SPECIAL.get(msvcrt.getwch(), "")
    if ch == "\r":
        return ENTER
    if ch == "\x1b":
        return ESCAPE
    if ch == "\x08":
        return BACKSPACE
    if ch == "\t":
        return TAB
    if ch == "\x03":
        raise KeyboardInterrupt
    return ch


def fit(text, width):
    """Truncate to width so long titles don't break the layout."""
    if width <= 0:
        return ""
    return text if len(text) <= width else text[: width - 1] + "…"


def pad(text, width):
    return fit(text, width).ljust(width)


def color(text, name):
    return f"{FG.get(name, '')}{text}{RESET}"


def header(title, subtitle="", width=None):
    w = width or size()[0]
    out = [f"{BOLD}{FG['cyan']}{fit(' ' + title, w)}{RESET}"]
    if subtitle:
        out.append(f"{FG['grey']}{fit(' ' + subtitle, w)}{RESET}")
    out.append(f"{FG['grey']}{'─' * w}{RESET}")
    return out


def footer(hints, width=None):
    w = width or size()[0]
    parts = []
    for key, what in hints:
        parts.append(f"{BOLD}{FG['yellow']}{key}{RESET}{FG['grey']} {what}{RESET}")
    line = f"{FG['grey']}  ·  {RESET}".join(parts)
    return [f"{FG['grey']}{'─' * w}{RESET}", " " + line]


def draw(lines):
    """Redraw the whole screen: simpler, and no flicker at these sizes."""
    clear()
    sys.stdout.write("\n".join(lines))
    flush()


def prompt(text):
    """Ask for a line of input, showing the cursor while typing."""
    show_cursor()
    sys.stdout.write(f"\n\n {text}")
    flush()
    line = sys.stdin.readline().strip()
    hide_cursor()
    return line


def drop_typeahead():
    """Discard keys pressed while a tool was running.

    Otherwise a key hit during a long dry run answers the next prompt —
    possibly a confirmation — on its own.
    """
    if msvcrt is None:
        return
    while msvcrt.kbhit():
        msvcrt.getwch()


def confirm(text):
    drop_typeahead()
    sys.stdout.write(f"\n\n {BOLD}{text}{RESET} {FG['grey']}[y/n]{RESET} ")
    flush()
    while True:
        k = read_key()
        # Check Esc BEFORE lowering: 'ESCAPE'.lower() is 'escape', and the
        # old version compared after lowering, so Esc never meant "no".
        if k == ESCAPE:
            answer = False
        else:
            k = k.lower()
            # 'н'/'т' are the y/n keys on Russian and Ukrainian layouts, 'д' is "да"
            if k in ("y", "н", "д"):
                answer = True
            elif k in ("n", "т"):
                answer = False
            else:
                continue
        # show that the key was taken — a long tool may start right after
        sys.stdout.write(f"{BOLD}{'yes' if answer else 'no'}{RESET}\n")
        flush()
        return answer


def pause(text="Press any key"):
    drop_typeahead()
    sys.stdout.write(f"\n {FG['grey']}{text}{RESET}")
    flush()
    read_key()
