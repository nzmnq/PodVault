# music_transfer

Python/OOP script for downloading audio from YouTube and creating Apple/iPod-friendly `.m4a` files with cover art and clean tags.

## Installation

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
brew install ffmpeg
```

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
winget install Gyan.FFmpeg
```

A local `ffmpeg.exe` may also exist in this repository; the script will use it automatically if the file is present.

## YouTube Cookies

Place `cookies.txt` next to the script. `yt-dlp` uses it to bypass YouTube age, region, or login-based restrictions.

## `tracklist.txt` Format

One track per line:

```text
Artist | Title | Album | Composer | Year | Genre | Track Number | Disc Number | Cover Art URL
```

Example:

```text
Daft Punk | Get Lucky | Random Access Memories | Thomas Bangalter, Guy-Manuel de Homem-Christo | 2013 | Disco | 8 | 1 | https://example.com/cover.jpg
Daft Punk | https://www.youtube.com/watch?v=5NV6Rdv1a3I | Random Access Memories | Thomas Bangalter | 2013 | Disco | 8 | 1 | https://example.com/cover.jpg
```

If the `Title` field starts with `http`, the script downloads the direct link. Otherwise it searches with:

```text
ytsearch1:Artist Title audio
```

## Run

```bash
python ipod_media_builder.py
```

The finished files will appear in the `iPod_Music` folder.

## What the Script Does

- `yt-dlp` downloads only the raw audio stream with `format=bestaudio/best`, `cookies.txt`, and `quiet=False`.
- `requests` downloads the cover art into a temporary `.jpg` file.
- `subprocess` launches FFmpeg for the final build.
- FFmpeg strips metadata with `-map_metadata -1`, encodes AAC at `320k`, adds `-movflags +faststart`, and writes `artist`, `title`, `album`, `composer`, `date`, `genre`, `track`, and `disc` tags.
- If cover art is available, it is embedded as `attached_pic`.
- After a successful `.m4a` build, temporary files are deleted.
