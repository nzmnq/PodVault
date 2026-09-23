# Music Utility

Tools for keeping a music library in shape for an old iPod: fixing tags so
albums don't fall apart, splitting the library into what goes on the iPod and
what stays on disk, syncing the device, plus a few audio utilities (FLAC →
ALAC, a tracklist downloader, iTunes cover art, spatial sound).

## Quick start

```
run.bat
```

or `python Main.py`. On the first run a short wizard asks for the basic
settings — where the library lives, where new tracks come from, how to sync the
iPod. Everything can be changed later on the **Settings** screen.

Settings are stored in `settings.json` in the project folder. The file is
git-ignored: it holds personal paths and must not end up in the repository.

## Installation

Python 3.10+.

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

`run.bat` picks the interpreter in this order: `.python\python.exe` (a portable
Python next to the project), `.venv\Scripts\python.exe`, then `py -3` / `python`.

FFmpeg is needed by the FLAC → ALAC converter, the downloader and the spatial
sound tool. Set its path on the Settings screen, or leave it on `auto` to use
the one in `PATH` or in `bin\`. The library tools don't need it.

iPod sync through iTunes works on Windows only (it drives iTunes over COM).

## The main screen

**Library**

| | |
|---|---|
| Active / Archive markup | A list of albums: `A` — goes to the iPod, `R` — stays on disk. `s` saves and moves the folders. |
| Add new tracks | Takes a folder of new mp3s, fixes their tags, skips what's already in the library. |
| Sync the iPod | Makes the device hold exactly what's in `Active`. |
| Find missing cover art | Deezer, then MusicBrainz. Only applied when the artist matches. |
| Check tags | Album Artist, ID3v2.3 / UTF-16, no v2.4 frames; can repair the latter. |
| AI vibe playlist | Describe a mood in words; Claude picks and orders tracks from `Active`, helped by tempo/energy measured from the audio. Saved as `.m3u8`. |
| Genres | The AI suggests a genre and a precise style per album, you correct the file, then they're written into the tags. |
| What's missing from my likes | Compares a Spotify data export, an Apple Music playlist page or a text list with the library. |
| Export the list to a file | The markup as a text file, for editing elsewhere. |

**Audio tools**

| | |
|---|---|
| Convert FLAC to ALAC | FLAC files from the input folder → `.m4a` (ALAC) in the output folder. |
| Download from the tracklist | Downloads the tracks listed in the tracklist and tags them. |
| Fetch covers for the tracklist | Fills cover URLs in the tracklist from the iTunes API. |
| Spatial sound processing | An experimental stereo-to-spatial pass. |
| Initial build from an old collection | One-time: builds the library from an unsorted collection. |

Everything that changes files runs as a dry run first and asks before applying.

## Project structure

```
Main.py                 the program (menu, first-run wizard, settings screen)
run.bat                 launcher
src/
  settings.py           settings: defaults, load/save, validation
  library.py            library contents, Active/Archive moves
  add_incoming.py       adding new tracks
  ipod_sync.py          iPod sync
  ipoddb.py             reads the iPod's own databases (covers check)
  fetch_covers.py       missing cover art for the library
  verify_clean.py       tag checks (--fix)
  import_likes.py       "what I listen to" lists -> one format
  find_missing.py       comparison with the library
  vibe.py               AI vibe playlists
  genres.py             genre and style per album
  ai.py                 one question to an AI model (Claude Code, Gemini, API)
  build_clean.py        one-time initial build
  musiclib.py, tui.py   shared helpers
  flac_to_alac.py       FLAC -> ALAC
  metadata_download.py  tracklist downloader
  fetch_cover.py        iTunes covers for the tracklist
  sur_sound.py          spatial sound
data/
  tracklist.txt         the tracklist
```

Each script in `src/` also runs on its own: `python src\library.py`,
`python src\add_incoming.py --apply`, and so on. They read the same
`settings.json`.

## Library layout

```
<library>\Active\<Artist>\<Album>\NN - Title.mp3     goes to the iPod
<library>\Archive\<Artist>\<Album>\NN - Title.mp3    stays on disk
```

An album's state is **which folder it sits in**. Adding tracks wipes nothing,
and the markup can be changed any number of times.

Ukrainian-language albums are highlighted by the letters `і ї є ґ`, which don't
exist in Russian. That's about language, not genre: folk can't be told apart
from rock by tags, so a human decides.

## Why the tags are rewritten

In the original library the **Album Artist field (TPE2) was empty on every
track**. The iPod groups albums by Album Artist and, when it's missing, falls
back to Artist — so a track with a feature (`Arash/ Helena`) became a separate
artist and tore its album apart.

What's written:

- **TPE2** — set everywhere
- **TPE1** — the main artist only; features move into the title as `(feat. X)`
- **TCON** — the first genre before a separator (`Rap/Hip Hop` → `Hip-Hop`);
  see [Genres](#genres) for fixing them
- **APIC + folder.jpg** — cover art
- **ID3v2.3 / UTF-16** — otherwise an old iPod garbles Cyrillic; v2.4 frames
  such as `TDRC` are removed, the year goes into `TYER`
- one-track folders become a `Singles` album per artist

Compilations get the Album Artist `Разные исполнители` ("Various Artists"). It's
a tag value already written into files, so it isn't translated.

## Artist separators

- **Slash** (`Lil Peep/ Lil Tracy`). The one real name with a slash is
  **AC/DC**, protected in `ARTIST_PROTECT`.
- **Comma** (`wifiskeleton, Jaydes`). `125, Rue Montmartre` is a real name, so a
  string isn't split if the whole name already occurs in the library.

## iPod sync

The script **never writes `iTunesDB`**: a bad write there wipes all music on the
device at once. Everything goes through iTunes. The default mode is a setting.

- **device** — cleans the iPod itself. Needed when the iPod is set to
  "Manually manage music and videos": then iTunes removes nothing from the
  device on its own, and archived tracks keep playing in shuffle however much
  the library is cleaned. Matching is by tags and duration; only tracks
  **positively recognised** as archive are deleted, unknown ones are left alone.
- **library** — the iTunes library is brought in line with `Active`, iTunes
  stays in "sync entire library" mode.
- **playlist** — the library isn't touched, a playlist equal to `Active` is
  kept. The iPod must be set once to sync only that playlist, otherwise archived
  tracks still reach it and play in shuffle.
- **disk** — a plain mirror for Rockbox or disk mode; only the managed
  subfolder is touched.

If iTunes has "Copy files to iTunes Media folder when adding to library" on (the
default), adding the library would silently duplicate it. The library and
playlist modes test this on one file first and stop with an explanation.

### Covers on the iPod

The cover an iPod draws is neither the picture inside the mp3 nor what iTunes
reports: it's a small pre-rendered copy in `iPod_Control\Artwork`, linked to the
track in the device's own database. iTunes can say a track has artwork while
the iPod has no such copy — tracks put on the iPod by other programs
(libgpod-based ones) typically look like that. So the device mode reads the
iPod's databases directly (`src/ipoddb.py`, read-only) to find tracks the
screen shows without a cover, and sets their cover again from the file, which
makes iTunes render the copies.

iTunes writes its changes to the iPod only when the iPod is ejected or iTunes
quits. When covers were changed, the sync closes iTunes at the end, reads the
iPod back and reports how many covers really landed. Afterwards unplug the
iPod with "Safely Remove Hardware" (or reopen iTunes and eject).

`python src\ipoddb.py` prints what the iPod really has at any time.

### Tracks only on the iPod

The sync never deletes tracks it can't find in `Active` or `Archive` — the
iPod may hold the only copy. `ipod_sync.py --rescue` (Sync → 5 in the menu)
copies them into `<incoming>\From iPod`, reading the iPod's own database and
files directly, without iTunes; "Add new tracks" then brings them into the
library like any other new track. Already saved tracks aren't copied again.

## Tracklist format

Used by the downloader and the iTunes cover fetcher. One track per line in
`data/tracklist.txt` (the path is a setting):

```text
Artist | Title | Album | Composer | Year | Genre | Track Number | Disc Number | Cover Art URL
```

Example:

```text
Daft Punk | Get Lucky | Random Access Memories | Thomas Bangalter, Guy-Manuel de Homem-Christo | 2013 | Disco | 8 | 1 | https://example.com/cover.jpg
```

If the `Title` field starts with `http`, the downloader treats it as a direct
URL instead of a search query. An optional cookies file (a setting) can help
with restricted downloads.

## "What I listen to" lists

`import_likes.py` understands:

1. **Spotify data export** — spotify.com → Privacy Settings → Download your
   data. No Premium needed. `YourLibrary.json` and `Playlist*.json`.
2. **An Apple Music playlist** saved from the browser as `.html`.
3. **A text list** — `Artist — Album` or `Artist — Album — Track` per line.

`find_missing.py` then reports what's complete, partial or missing, and what is
present but sitting in the archive (no need to download — just mark it `[A]`).

## Genres

Genres were cut down to one broad word when the library was cleaned, and many
were wrong to begin with (hyperpop under `Alternative`). Two tags now:

- **Genre** (`TCON`) — broad: Rock, Hip-Hop, Electronic… It's what the iPod's
  Genres menu shows, so it stays short.
- **Grouping** (`TIT1`) — the precise style: Hyperpop, Cloud Rap, Post-punk…
  The AI playlists read it; the iPod menu isn't cluttered.

`data\genres.txt` (a setting, git-ignored) holds one line per album —
`Artist folder/Album folder | Genre | Style` — and is the source of truth:

```powershell
python src\genres.py suggest          # the AI fills in albums not in the file yet
python src\genres.py apply            # what would change in the tags
python src\genres.py apply --apply    # write them
```

`suggest` never touches lines already in the file, so corrections stay; new
albums get their line the next time it runs. The device sync then sets the
genre on the iPod copies too, through iTunes.

## AI vibe playlists

```powershell
python src\vibe.py "rainy night drive, slow, a bit sad"
python src\vibe.py "gym, loud and fast" --count 40
```

Every `Active` track is analysed once from a 45-second excerpt — tempo,
loudness, how busy, bright, bass-heavy and dynamic it is — and cached in
`reports\vibe_features.json`; later runs analyse only new tracks. Claude gets
the whole catalogue with those numbers and the vibe (roughly 50k tokens),
picks and orders the tracks and names the playlist. The result is saved to
`reports\playlists\<name>.m3u8`.

Settings → AI holds the mode, the default number of tracks and the
model for each mode. Where the pick comes from (`--backend` overrides it):

- **auto** (default) — `cli` if Claude Code is installed, else `gemini` if
  `GEMINI_API_KEY` is set, else `api`. The same project works for everyone.
- **gemini** — Google Gemini through its OpenAI-compatible endpoint, for
  anyone without a Claude subscription. The key is free (aistudio.google.com →
  Get API key, no card); set it once with `setx GEMINI_API_KEY "..."`. The
  free tier's limits (a few requests a minute, some hundreds a day) are plenty
  for playlists. On the free tier Google may use requests to improve its
  models — here that's the list of track names. The model is a setting.
- **cli** — the Claude Code command line, on a Claude subscription, no API
  costs. `auto` finds `claude` in `PATH` or the copy
  bundled with the Claude desktop app. Log in once: run `claude`, type
  `/login`, then `/exit`. Tools, MCP servers and project settings are switched
  off for the call, and nothing is saved to the session history.
- **api** — the Anthropic API (`claude-opus-5`), paid per use; needs
  `ANTHROPIC_API_KEY` (console.anthropic.com → API keys). The library is sent
  as a cached prompt, so a second vibe within the hour costs a fraction of the
  first.

`--apply` / `--push-last` try to create the playlist on the iPod through
iTunes. With iTunes 12.13 and a manually managed iPod Video this was refused
("The source is not modifiable"), although the same iPod accepts deletions
and covers — so for now the playlist has to be assembled on the iPod by hand.

## Pitfalls already hit

- Windows Explorer doesn't show `TPOS` and draws the artist separator `/` as
  `;`. Look at tags through mutagen.
- When reading v2.3, mutagen substitutes v2.4 frames (`TYER` → `TDRC`,
  `IPLS` → `TIPL`) and writes them back into a v2.3 tag on save. Every writer
  must call `musiclib.drop_v24_frames` before saving; `verify_clean.py --fix`
  cleans up files written without it.
- Stripping an edition suffix must remove the whole bracket group, or
  `Vol. 4 (2009 Remastered Version)` becomes `Vol. 4 (2009`.
- A portable Python build ships without root certificates; HTTPS goes through
  `certifi`. HTTP headers are latin-1, so no Cyrillic in the `User-Agent`.
- The iPod holds tracks with the tags they had when copied. Don't match the
  device by album, and delete only what's positively identified.
- iTunes COM invalidates references to device tracks after the first deletion:
  fetch each track fresh, walking from the end.
- Right after iTunes starts, the iPod can take minutes to appear among its
  sources, and iTunes rejects calls as busy meanwhile. The sync waits for it.
- `comtypes` exposes the iPod library as `IITPlaylist` without `AddFile`;
  `QueryInterface(IITLibraryPlaylist)` is needed.
- The same song exists as album, compilation and live versions; they're told
  apart by duration.
- iTunes' `Artwork.Count` says nothing about what the iPod screen shows, and
  iTunes writes the iPod's databases only on eject or quit — check covers with
  `ipoddb.py` after that, not before.
- PowerShell 5.1 prepends a BOM to piped input.
- The Spotify Web API refuses every request (403) unless the app owner has
  Premium, and doesn't accept `localhost` as a redirect URI.
- After the Active/Archive split, anything writing into the library root writes
  past both folders. `build_clean.py` refuses to; use `--dest` for a rebuild.

## License

MIT, see `LICENSE`.
