# Music Utility

A small Python utility collection for preparing audio for Apple/iPod use. It can convert FLAC files to ALAC, download tracks from YouTube, embed metadata and cover art, fetch cover images from iTunes, and apply a simple spatial audio processing step.

## Features

- Convert FLAC files from the input folder to ALAC in the output folder
- Download audio from YouTube and build Apple-friendly `.m4a` files
- Embed metadata such as title, artist, album, composer, year, genre, track, and disc
- Attach cover art to the final audio file when available
- Fill missing cover URLs in the tracklist using the iTunes API
- Apply a basic stereo-to-spatial-style processing pass for richer sound

## Project Structure

- `Main.py` — main menu entry point
- `src/flac_to_alac.py` — FLAC to ALAC conversion
- `src/metadata_download.py` — YouTube download + metadata embedding
- `src/fetch_cover.py` — iTunes cover lookup for tracklist entries
- `src/sur_sound.py` — basic spatial processing for audio files
- `data/tracklist.txt` — source list of tracks
- `data/input_folder/` — input FLAC files for conversion
- `data/ALAC_Output/` — converted ALAC files
- `data/iPod_Music/` — downloaded and tagged `.m4a` files
- `data/Spatial_processed/` — spatial-processed output files
- `cookies.txt` — optional YouTube cookies file for stricter downloads
- `bin/` — local FFmpeg binaries (used automatically if present)

## Requirements

Python 3.8+ is recommended.

Install the required Python packages:

```bash
pip install -r requirements.txt
```

You also need FFmpeg available on your system.

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

If you have FFmpeg installed locally, the scripts will use it automatically. A Windows binary in the `bin` folder is also supported.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
brew install ffmpeg
```

## Using YouTube Cookies

Place a file named `cookies.txt` in the project root. This can help with age-restricted, region-restricted, or login-restricted YouTube downloads.

## Tracklist Format

Each track should be stored as one line in `data/tracklist.txt`:

```text
Artist | Title | Album | Composer | Year | Genre | Track Number | Disc Number | Cover Art URL
```

Example:

```text
Daft Punk | Get Lucky | Random Access Memories | Thomas Bangalter, Guy-Manuel de Homem-Christo | 2013 | Disco | 8 | 1 | https://example.com/cover.jpg
```

If the `Title` field starts with `http`, the downloader treats it as a direct URL instead of a search query.

## Run the Utility

Start the menu-driven script:

```bash
python Main.py
```

You will be prompted to choose one of the following modes:

1. Convert FLAC to ALAC
2. Download audio and embed metadata
3. Process audio for spatial-style output
4. Fetch cover art for the tracklist

## Output Folders

- FLAC conversion output: `data/ALAC_Output`
- Downloaded tagged music: `data/iPod_Music`
- Spatial processed files: `data/Spatial_processed`

## Notes

- The downloader uses `yt-dlp` for audio retrieval and FFmpeg for final file assembly.
- Cover art is downloaded from the provided URL when available.
- The cover fetcher updates the tracklist with iTunes artwork URLs.
- The spatial processing step is a simple experimental transformation and may not be suitable for every audio source.
