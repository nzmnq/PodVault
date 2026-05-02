from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import json
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


AUDIO_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}

REQUEST_HEADERS = {
    "User-Agent": "music_transfer/1.0 (https://github.com/; local music library tool)",
}


@dataclass(frozen=True)
class LocalTrack:
    path: Path
    artist: str
    title: str
    album: str
    album_artist: str
    year: str
    genre: str
    track: str
    disc: str

    @property
    def album_key(self) -> str:
        return f"{self.album_artist}|{self.album}".casefold()

    @property
    def folder_name(self) -> str:
        return sanitize_filename(self.album)

    @property
    def output_name(self) -> str:
        track_number = normalize_number(self.track)
        prefix = f"{track_number:02d} - " if track_number else ""
        return sanitize_filename(f"{prefix}{self.title}.m4a")


class MusicSorterForIPod:
    def __init__(
        self,
        source_dir: Path,
        output_dir: Path,
        cache_dir: Path,
        ffmpeg_path: str,
        ffprobe_path: str | None,
        overwrite: bool = False,
        dry_run: bool = False,
        workers: int = 4,
    ) -> None:
        self.source_dir = source_dir
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.overwrite = overwrite
        self.dry_run = dry_run
        self.workers = max(1, workers)
        self.cover_downloader = AlbumCoverDownloader(cache_dir, ffmpeg_path)
        self.cover_futures: dict[str, Future[Path | None]] = {}
        self.cover_lock = threading.Lock()

    def run(self) -> None:
        audio_files = self.find_source_audio_files()
        tracks = [self.read_track(path) for path in progress_iter(audio_files, desc="Reading tags", unit="file")]
        if not tracks:
            print(f"No audio files found in {self.source_dir}")
            return

        print(f"Found {len(tracks)} audio file(s).")
        jobs = [track for track in tracks if self.should_process(track)]
        skipped = len(tracks) - len(jobs)

        if skipped:
            print(f"Skip existing: {skipped}")

        if self.dry_run:
            for track in jobs:
                print(f"Would write: {track.path} -> {self.output_path_for(track)}")
            return

        if not jobs:
            print("Nothing to do.")
            return

        cover_workers = min(self.workers, 4)
        failures = 0

        with ThreadPoolExecutor(max_workers=self.workers) as track_executor:
            with ThreadPoolExecutor(max_workers=cover_workers) as cover_executor:
                futures = {
                    track_executor.submit(self.process_track, track, cover_executor): track
                    for track in jobs
                }

                for future in progress_as_completed(futures, total=len(futures), desc="Processing"):
                    track = futures[future]
                    try:
                        message = future.result()
                    except Exception as error:
                        failures += 1
                        progress_write(f"Failed: {track.artist} - {track.title}: {error}")
                    else:
                        progress_write(message)

        if failures:
            print(f"Done with {failures} failed track(s).")
        else:
            print("Done.")

    def should_process(self, track: LocalTrack) -> bool:
        return self.overwrite or not self.output_path_for(track).exists()

    def output_path_for(self, track: LocalTrack) -> Path:
        return self.output_dir / track.folder_name / track.output_name

    def process_track(self, track: LocalTrack, cover_executor: ThreadPoolExecutor) -> str:
        output_path = self.output_path_for(track)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cover_path = self.get_album_cover(track, cover_executor)
        self.build_ipod_file(track, cover_path, output_path)
        cover_text = "with cover" if cover_path else "without cover"
        return f"Done: {output_path} ({cover_text})"

    def get_album_cover(self, track: LocalTrack, cover_executor: ThreadPoolExecutor) -> Path | None:
        with self.cover_lock:
            future = self.cover_futures.get(track.album_key)
            if not future:
                future = cover_executor.submit(self.cover_downloader.download, track)
                self.cover_futures[track.album_key] = future

        return future.result()

    def find_source_audio_files(self) -> list[Path]:
        skipped_dirs = [self.output_dir.resolve(), self.cache_dir.resolve()]
        files: list[Path] = []

        for path in self.source_dir.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in AUDIO_EXTENSIONS:
                continue

            resolved = path.resolve()
            if any(is_relative_to(resolved, skipped_dir) for skipped_dir in skipped_dirs):
                continue

            files.append(path)

        return sorted(files)

    def read_track(self, path: Path) -> LocalTrack:
        tags = read_tags_with_mutagen(path)
        if not tags and self.ffprobe_path:
            tags = read_tags_with_ffprobe(path, self.ffprobe_path)

        artist = first_tag(tags, "artist", "album_artist", default="Unknown Artist")
        title = first_tag(tags, "title", default=path.stem)
        album = first_tag(tags, "album", default="Unknown Album")
        album_artist = first_tag(tags, "album_artist", "albumartist", "artist", default=artist)

        return LocalTrack(
            path=path,
            artist=artist,
            title=title,
            album=album,
            album_artist=album_artist,
            year=first_tag(tags, "date", "year"),
            genre=first_tag(tags, "genre"),
            track=first_tag(tags, "track", "tracknumber", default="1"),
            disc=first_tag(tags, "disc", "discnumber", default="1"),
        )

    def build_ipod_file(self, track: LocalTrack, cover_path: Path | None, output_path: Path) -> None:
        cmd = [self.ffmpeg_path, "-y", "-i", str(track.path)]

        if cover_path:
            cmd.extend(["-i", str(cover_path), "-map", "0:a:0", "-map", "1:v:0"])
        else:
            cmd.extend(["-map", "0:a:0"])

        cmd.extend(
            [
                "-map_metadata",
                "-1",
                "-c:a",
                "aac",
                "-b:a",
                "256k",
            ]
        )

        if cover_path:
            cmd.extend(["-c:v", "mjpeg", "-disposition:v", "attached_pic"])

        cmd.extend(
            [
                "-movflags",
                "+faststart",
                "-metadata",
                f"artist={track.artist}",
                "-metadata",
                f"title={track.title}",
                "-metadata",
                f"album={track.album}",
                "-metadata",
                f"album_artist={track.album_artist}",
                "-metadata",
                f"date={track.year}",
                "-metadata",
                f"genre={track.genre}",
                "-metadata",
                f"track={track.track}",
                "-metadata",
                f"disc={track.disc}",
                str(output_path),
            ]
        )

        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


class AlbumCoverDownloader:
    def __init__(self, cache_dir: Path, ffmpeg_path: str) -> None:
        self.cache_dir = cache_dir
        self.raw_dir = cache_dir / "raw"
        self.ready_dir = cache_dir / "ipod_jpg"
        self.ffmpeg_path = ffmpeg_path
        self.providers: list[CoverProvider] = [
            DeezerCoverProvider(),
            MusicBrainzCoverProvider(),
            ITunesCoverProvider(),
        ]

    def download(self, track: LocalTrack) -> Path | None:
        if track.album == "Unknown Album":
            progress_write(f"No album tag for cover search: {track.path.name}")
            return None

        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.ready_dir.mkdir(parents=True, exist_ok=True)

        cache_name = sanitize_filename(f"{track.album_artist} - {track.album}") or "cover"
        ready_path = self.ready_dir / f"{cache_name}.jpg"
        if ready_path.exists():
            return ready_path

        candidate = self.find_artwork(track)
        if not candidate:
            progress_write(f"Cover not found: {track.album_artist} - {track.album}")
            return None

        raw_path = self.raw_dir / f"{cache_name}{Path(candidate.url).suffix or '.jpg'}"
        import requests

        try:
            response = requests.get(candidate.url, timeout=25, headers=REQUEST_HEADERS)
            response.raise_for_status()
            raw_path.write_bytes(response.content)

            self.convert_to_ipod_jpeg(raw_path, ready_path)
        except Exception as error:
            progress_write(f"Could not prepare cover from {candidate.provider}: {error}")
            return None

        progress_write(f"Cover downloaded from {candidate.provider}: {ready_path}")
        return ready_path

    def find_artwork(self, track: LocalTrack) -> CoverCandidate | None:
        for provider in self.providers:
            try:
                candidate = provider.search(track)
            except Exception as error:
                progress_write(f"{provider.name} cover search failed: {error}")
                continue

            if candidate:
                return candidate

        return None

    def convert_to_ipod_jpeg(self, source_path: Path, output_path: Path) -> None:
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i",
            str(source_path),
            "-vf",
            "scale=600:600:force_original_aspect_ratio=decrease,"
            "pad=600:600:(ow-iw)/2:(oh-ih)/2:color=white,format=yuvj420p",
            "-frames:v",
            "1",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@dataclass(frozen=True)
class CoverCandidate:
    provider: str
    url: str


class CoverProvider:
    name = "provider"

    def search(self, track: LocalTrack) -> CoverCandidate | None:
        raise NotImplementedError


class DeezerCoverProvider(CoverProvider):
    name = "Deezer"

    def search(self, track: LocalTrack) -> CoverCandidate | None:
        import requests

        query = quote_plus(f'{track.album_artist} "{track.album}"')
        url = f"https://api.deezer.com/search/album?q={query}&limit=5"
        response = requests.get(url, timeout=20, headers=REQUEST_HEADERS)
        response.raise_for_status()

        results = response.json().get("data", [])
        best = pick_best_album(
            results,
            track,
            album_getter=lambda item: item.get("title", ""),
            artist_getter=lambda item: item.get("artist", {}).get("name", ""),
        )
        if not best:
            return None

        cover_url = best.get("cover_xl") or best.get("cover_big") or best.get("cover_medium")
        return CoverCandidate(self.name, cover_url) if cover_url else None


class MusicBrainzCoverProvider(CoverProvider):
    name = "MusicBrainz/Cover Art Archive"

    def search(self, track: LocalTrack) -> CoverCandidate | None:
        import requests

        release_group_id = self.find_release_group_id(requests, track)
        if not release_group_id:
            return None

        url = f"https://coverartarchive.org/release-group/{release_group_id}/front-500"
        response = requests.get(url, timeout=20, headers=REQUEST_HEADERS, allow_redirects=True)
        if response.status_code == 404:
            return None
        response.raise_for_status()

        return CoverCandidate(self.name, response.url)

    def find_release_group_id(self, requests_module, track: LocalTrack) -> str | None:
        query = quote_plus(f'releasegroup:"{track.album}" AND artist:"{track.album_artist}"')
        url = f"https://musicbrainz.org/ws/2/release-group/?query={query}&fmt=json&limit=5"
        response = requests_module.get(url, timeout=20, headers=REQUEST_HEADERS)
        response.raise_for_status()

        results = response.json().get("release-groups", [])
        best = pick_best_album(
            results,
            track,
            album_getter=lambda item: item.get("title", ""),
            artist_getter=lambda item: artist_credit_name(item.get("artist-credit", [])),
        )
        return best.get("id") if best else None


class ITunesCoverProvider(CoverProvider):
    name = "iTunes"

    def search(self, track: LocalTrack) -> CoverCandidate | None:
        import requests

        query = quote_plus(f"{track.album_artist} {track.album}")
        url = f"https://itunes.apple.com/search?term={query}&entity=album&limit=5"
        response = requests.get(url, timeout=20, headers=REQUEST_HEADERS)
        response.raise_for_status()

        results = response.json().get("results", [])
        best = pick_best_album(
            results,
            track,
            album_getter=lambda item: item.get("collectionName", ""),
            artist_getter=lambda item: item.get("artistName", ""),
        )
        if not best:
            return None

        artwork_url = best.get("artworkUrl100")
        if not artwork_url:
            return None

        return CoverCandidate(self.name, artwork_url.replace("100x100bb", "600x600bb"))


def progress_as_completed(futures: dict[Future[str], LocalTrack], total: int, desc: str):
    try:
        from tqdm import tqdm
    except ImportError:
        yield from as_completed(futures)
        return

    yield from tqdm(as_completed(futures), total=total, desc=desc, unit="track")


def progress_iter(items: list[Path], desc: str, unit: str):
    try:
        from tqdm import tqdm
    except ImportError:
        return items

    return tqdm(items, desc=desc, unit=unit)


def progress_write(message: str) -> None:
    try:
        from tqdm import tqdm
    except ImportError:
        print(message)
        return

    tqdm.write(message)


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def read_tags_with_mutagen(path: Path) -> dict[str, str]:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return {}

    audio = MutagenFile(path, easy=True)
    if not audio or not audio.tags:
        return {}

    return normalize_tags(dict(audio.tags))


def read_tags_with_ffprobe(path: Path, ffprobe_path: str) -> dict[str, str]:
    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    data = json.loads(result.stdout or "{}")
    return normalize_tags(data.get("format", {}).get("tags", {}))


def normalize_tags(tags: dict) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in tags.items():
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""

        text = str(value).strip()
        if text:
            normalized[key.casefold().replace(" ", "_")] = text

    return normalized


def first_tag(tags: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = tags.get(name.casefold().replace(" ", "_"))
        if value:
            return value
    return default


def normalize_number(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:180] or "Unknown"


def simplify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def pick_best_album(
    items: list[dict],
    track: LocalTrack,
    album_getter,
    artist_getter,
) -> dict | None:
    if not items:
        return None

    target_album = simplify(track.album)
    target_artist = simplify(track.album_artist)

    for item in items:
        album = simplify(album_getter(item))
        artist = simplify(artist_getter(item))
        if target_album in album and (target_artist in artist or artist in target_artist):
            return item

    for item in items:
        album = simplify(album_getter(item))
        if target_album in album or album in target_album:
            return item

    return items[0]


def artist_credit_name(artist_credit: list[dict]) -> str:
    return " ".join(credit.get("artist", {}).get("name", "") for credit in artist_credit)


def find_ffmpeg(explicit_path: str | None = None) -> str:
    if explicit_path:
        return explicit_path

    local_path = Path("ffmpeg.exe")
    if local_path.exists():
        return str(local_path)

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg
    except ImportError:
        return "ffmpeg"

    return imageio_ffmpeg.get_ffmpeg_exe()


def find_tool(name: str, local_name: str | None = None) -> str | None:
    if local_name:
        local_path = Path(local_name)
        if local_path.exists():
            return str(local_path)

    found = shutil.which(name)
    if found:
        return found

    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sort local music by albums and create iPod-friendly .m4a files with embedded covers."
    )
    parser.add_argument("source_dir", type=Path, help="Folder with unsorted music.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("iPod_Sorted_Music"),
        help="Folder for sorted iPod-ready files.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("iPod_Sorted_Music/.covers"),
        help="Folder for downloaded and converted covers.",
    )
    parser.add_argument("--ffmpeg", default=None, help="Path to ffmpeg. Uses bundled imageio-ffmpeg if available.")
    parser.add_argument("--ffprobe", default=None, help="Optional path to ffprobe for tag fallback.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output files.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned output without writing files.")
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=4,
        help="Number of parallel track conversion workers.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ffmpeg_path = find_ffmpeg(args.ffmpeg)
    ffprobe_path = args.ffprobe or find_tool("ffprobe", "ffprobe.exe")

    sorter = MusicSorterForIPod(
        source_dir=args.source_dir.expanduser(),
        output_dir=args.output_dir.expanduser(),
        cache_dir=args.cache_dir.expanduser(),
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        workers=args.workers,
    )
    sorter.run()


if __name__ == "__main__":
    main()
