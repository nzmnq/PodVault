#!/bin/sh
# Installs PodVault for this user, from this checkout (macOS / Linux;
# install.ps1 on Windows). Run it again after `git pull` to update.
#
#   ./install.sh              install or update
#   ./install.sh --uninstall  remove the program (settings and data stay)
#
# What goes where:
#   macOS   ~/Applications/PodVault.app      the program, its Python packages inside
#   Linux   ~/.local/lib/podvault            the program and its Python packages
#           ~/.local/bin/podvault            the command
#           ~/.local/share/applications/podvault.desktop, and the icon
#
# The installed copy keeps its settings in the user's folders, not in the
# program (src/settings.py); a checkout's settings.json is taken over once.
#
# Python 3.12+ is needed. On Linux, a system PyQt6 (python-pyqt6 /
# python3-pyqt6 with its QtMultimedia and QtSvg) is used when there is one:
# the same Qt as the rest of the desktop. Otherwise PyQt6 comes from pip.
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)

case "$(uname -s)" in
    Darwin)
        BUNDLE="$HOME/Applications/PodVault.app"
        DEST="$BUNDLE/Contents/Resources" ;;
    *)
        BUNDLE=""
        DEST="$HOME/.local/lib/podvault"
        BIN="$HOME/.local/bin/podvault"
        DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/podvault.desktop"
        ICON="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/podvault.svg" ;;
esac
APP="$DEST/app"
VENV="$DEST/venv"

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    if [ -n "$BUNDLE" ]; then
        rm -rf "$BUNDLE"
    else
        rm -rf "$DEST" "$BIN" "$DESKTOP" "$ICON"
    fi
    say "PodVault removed. Settings and data are kept."
    exit 0
fi
[ $# -eq 0 ] || die "unknown option: $1 (only --uninstall)"

# --- Python 3.12+

PY=""
for p in python3.14 python3.13 python3.12 python3; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
        PY=$(command -v "$p")
        break
    fi
done
[ -n "$PY" ] || die "Python 3.12 or newer is needed (macOS: brew install python; Linux: the distribution's python3)."
say "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

[ -f "$SRC/vendor/podsync/podsync/__init__.py" ] || {
    say "Fetching podsync (git submodule)…"
    git -C "$SRC" submodule update --init || die "vendor/podsync is empty; run: git submodule update --init"
}

# --- the program: a clean copy, so files deleted upstream go too

say "Copying the program to $APP"
rm -rf "$APP"
mkdir -p "$APP/vendor/podsync"
cp -R "$SRC/Main.py" "$SRC/src" "$SRC/locale" "$SRC/packaging" "$SRC/requirements.txt" \
      "$SRC/LICENSE" "$SRC/README.md" "$APP/"
cp -R "$SRC/vendor/podsync/podsync" "$APP/vendor/podsync/"
find "$APP" -name __pycache__ -type d -prune -exec rm -rf {} +

# --- its Python packages

SYSTEM_QT=no
if [ -z "$BUNDLE" ] && "$PY" -c 'import PyQt6.QtWidgets, PyQt6.QtMultimedia, PyQt6.QtSvg' 2>/dev/null; then
    SYSTEM_QT=yes
fi
# a venv made by another Python, or with another Qt choice, is made again
WANT="$PY system-qt=$SYSTEM_QT"
if [ "$(cat "$VENV/.podvault" 2>/dev/null)" != "$WANT" ]; then
    rm -rf "$VENV"
    if [ $SYSTEM_QT = yes ]; then
        say "Using the system PyQt6"
        "$PY" -m venv --system-site-packages "$VENV"
    else
        "$PY" -m venv "$VENV"
    fi
    printf '%s\n' "$WANT" > "$VENV/.podvault"
fi

say "Installing Python packages…"
REQ=$(mktemp)
OPT=$(mktemp)
trap 'rm -f "$REQ" "$OPT"' EXIT
if [ $SYSTEM_QT = yes ]; then
    sed -e '/^# AI/,$d' -e '/^PyQt6/d' "$SRC/requirements.txt" > "$REQ"
else
    sed -e '/^# AI/,$d' "$SRC/requirements.txt" > "$REQ"
fi
sed -n '/^# AI/,$p' "$SRC/requirements.txt" > "$OPT"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -r "$REQ" || die "the Python packages could not be installed"
"$VENV/bin/python" -m pip install -q -r "$OPT" ||
    say "!! The AI playlist packages could not be installed; everything else works."

# --- the launcher and the menu entry

if [ -n "$BUNDLE" ]; then
    mkdir -p "$BUNDLE/Contents/MacOS"
    cat > "$BUNDLE/Contents/MacOS/PodVault" <<'EOF'
#!/bin/sh
# Starts PodVault with the Python packages in Resources/venv.
#
# Homebrew's and python.org's Python re-start themselves as their own
# Python.app, and the Dock and the menu bar then say "Python". So the
# interpreter itself is run from inside this bundle, as MacOS/python, and
# pointed at the venv the way the venv's own launcher does it. It is copied
# again whenever the system one changes (a Python update).
M="$(cd "$(dirname "$0")" && pwd)"
R="$(cd "$M/../Resources" && pwd)"
BASE=$(sed -n 's/^home = //p' "$R/venv/pyvenv.cfg")
PY=$(readlink -f "$BASE/python3")
REAL="$(dirname "$PY")/../Resources/Python.app/Contents/MacOS/Python"
[ -x "$REAL" ] || REAL="$PY"
# Which Python the copy came from is kept outside the bundle: the copy itself can't be
# compared with the original, signing changes it (and re-signing at every start is
# what made macOS ask for the same permissions again and again).
CACHE="$HOME/Library/Caches/PodVault"
FROM="$REAL $(stat -f %m:%z "$REAL")"
if [ "$(cat "$CACHE/python-from" 2>/dev/null)" != "$FROM" ] || [ ! -x "$M/python" ]; then
    cp "$REAL" "$M/python"
    # the bundle changed: sign it again (macOS then asks for the permissions once more)
    "$R/app/packaging/sign.sh" "$M/../.." 2>/dev/null || true
    mkdir -p "$CACHE" && printf '%s\n' "$FROM" > "$CACHE/python-from"
fi
# Nothing may be written inside the bundle (that breaks its signature, and
# macOS then asks for the iPod / folder permissions on every start).
export PYTHONPYCACHEPREFIX="$HOME/Library/Caches/PodVault/pycache"
# Started from Finder or the Dock, the PATH is the bare system one: add
# Homebrew's, where ffmpeg and the Claude CLI usually are.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export __PYVENV_LAUNCHER__="$R/venv/bin/python"
exec "$M/python" "$R/app/Main.py" "$@"
EOF
    chmod 755 "$BUNDLE/Contents/MacOS/PodVault"
    VERSION=$(git -C "$SRC" describe --tags --always 2>/dev/null || echo dev)
    cat > "$BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>PodVault</string>
    <key>CFBundleDisplayName</key><string>PodVault</string>
    <key>CFBundleIdentifier</key><string>io.github.nzmnq.podvault</string>
    <key>CFBundleExecutable</key><string>PodVault</string>
    <key>CFBundleIconFile</key><string>podvault</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict>
</plist>
EOF
    "$VENV/bin/python" -B "$APP/packaging/helper.py" icon "$DEST/podvault.icns"
    touch "$BUNDLE"    # Finder picks up the new icon
    # the copy made at the first start comes now, so the bundle is final when signed
    BASE=$(sed -n 's/^home = //p' "$VENV/pyvenv.cfg")
    PYREAL=$(readlink -f "$BASE/python3")
    REAL="$(dirname "$PYREAL")/../Resources/Python.app/Contents/MacOS/Python"
    [ -x "$REAL" ] || REAL="$PYREAL"
    cp "$REAL" "$BUNDLE/Contents/MacOS/python"
else
    mkdir -p "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
    cat > "$BIN" <<EOF
#!/bin/sh
exec "$VENV/bin/python" "$APP/Main.py" "\$@"
EOF
    chmod 755 "$BIN"
    cp "$APP/packaging/podvault.svg" "$ICON"
    cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=PodVault
GenericName=iPod music library
GenericName[uk]=Музична бібліотека для iPod
Comment=Keep a music library in shape for an iPod and sync it without iTunes
Comment[uk]=Лад у музичній бібліотеці та синхронізація iPod без iTunes
Exec=$BIN
Icon=podvault
Terminal=false
Categories=AudioVideo;Audio;
Keywords=ipod;music;library;tags;sync;
StartupWMClass=podvault
EOF
    command -v update-desktop-database >/dev/null 2>&1 &&
        update-desktop-database -q "$(dirname "$DESKTOP")" 2>/dev/null || true
fi

"$VENV/bin/python" -B "$APP/packaging/helper.py" settings "$SRC/settings.json"

if [ -n "$BUNDLE" ]; then
    # An unsigned app gets no lasting identity: macOS asks for the same
    # permissions (the iPod, the music folder) at every start. packaging/sign.sh
    # gives it one that holds across starts and reinstalls (see there).
    find "$APP" -name __pycache__ -type d -prune -exec rm -rf {} +
    "$APP/packaging/sign.sh" "$BUNDLE" ||
        say "!! The app could not be signed; macOS may ask for permissions at every start."
    # the launcher's note of which Python MacOS/python was copied from (see the launcher)
    mkdir -p "$HOME/Library/Caches/PodVault"
    printf '%s\n' "$REAL $(stat -f %m:%z "$REAL")" > "$HOME/Library/Caches/PodVault/python-from"
fi

say ""
say "PodVault is installed."
if [ -n "$BUNDLE" ]; then
    say "  Open it from ~/Applications or Launchpad."
else
    say "  Open it from the applications menu, or run: podvault"
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "  (~/.local/bin isn't in PATH: add it to use the command)";; esac
fi
say "  Settings: $("$VENV/bin/python" -B "$APP/packaging/helper.py" where)"
say "  Update: git pull, then ./install.sh again. Remove: ./install.sh --uninstall"
