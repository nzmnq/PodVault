"""
What install.sh and install.ps1 can't do in a shell, run with the installed
copy's own Python (its venv):

    python packaging/helper.py icon <out.png|out.ico|out.icns>
        the app icon (packaging/podvault.svg) for the Dock / the Start menu
    python packaging/helper.py settings <checkout>/settings.json
        start the installed copy with a checkout's settings, if it has none
        yet; paths relative to the checkout become absolute, so they keep
        pointing at the same folders
    python packaging/helper.py where
        prints where the installed copy keeps its settings
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import settings  # noqa: E402  (the installed copy's: it knows where its settings go)


def icon(out):
    from PyQt6.QtGui import QGuiApplication, QImage, QPainter
    from PyQt6.QtSvg import QSvgRenderer
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication(sys.argv[:1])  # noqa: F841  (painting needs one)
    svg = QSvgRenderer(os.path.join(HERE, "podvault.svg"))

    def png(size, path):
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        svg.render(p)
        p.end()
        img.save(path)

    ext = os.path.splitext(out)[1].lower()
    if ext == ".png":
        png(512, out)
    elif ext == ".ico":
        from PIL import Image
        tmp = os.path.join(tempfile.mkdtemp(), "icon.png")
        png(256, tmp)
        Image.open(tmp).save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    elif ext == ".icns":
        iconset = os.path.join(tempfile.mkdtemp(), "podvault.iconset")
        os.makedirs(iconset)
        for s in (16, 32, 128, 256, 512):
            png(s, os.path.join(iconset, f"icon_{s}x{s}.png"))
            png(s * 2, os.path.join(iconset, f"icon_{s}x{s}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
        shutil.rmtree(os.path.dirname(iconset))
    else:
        sys.exit(f"unknown icon type: {out}")


def take_settings(src):
    if os.path.isfile(settings.FILE) or not os.path.isfile(src):
        return
    with open(src, encoding="utf-8") as f:
        values = json.load(f)
    base = os.path.dirname(os.path.abspath(src))
    for key, v in values.items():
        field = settings.BY_KEY.get(key)
        if field and field.kind in ("dir", "file") and v and str(v).lower() != "auto" \
                and not os.path.isabs(os.path.expandvars(os.path.expanduser(str(v)))):
            values[key] = os.path.normpath(os.path.join(base, v))
    settings.save(values)
    print(f"settings: {src} -> {settings.FILE}")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what == "icon" and len(sys.argv) == 3:
        icon(sys.argv[2])
    elif what == "settings" and len(sys.argv) == 3:
        take_settings(sys.argv[2])
    elif what == "where":
        print(settings.FILE)
    else:
        sys.exit(__doc__)
