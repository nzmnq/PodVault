from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import json
import os
import re
import shutil
import subprocess
import sys
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
    audio_bitrate: int = 0
    sample_rate: int = 0

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
        return sanitize_filename(f"{prefix}{self.title}.{output_extension_for(self.path)}")


@dataclass(frozen=True)
class AudioInfo:
    bitrate: int = 0
    sample_rate: int = 0


class MusicSorterForIPod:
    def __init__(
        self,
        source_dir: Path,
        output_dir: Path,
        cache_dir: Path,
        ffmpeg_path: str,
        ffprobe_path: str | None,
        workers: int | None = None,
    ) -> None:
        self.source_dir = source_dir
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.workers = resolve_worker_count(workers)
        self.cover_downloader = AlbumCoverDownloader(cache_dir, ffmpeg_path)
        self.cover_futures: dict[str, Future[Path | None]] = {}
        self.cover_lock = threading.Lock()

    def run(self) -> None:
        audio_files = self.find_source_audio_files()
        tracks: list[LocalTrack] = []
        unreadable = 0

        for path in progress_iter(audio_files, desc="Reading tags", unit="file"):
            track = self.read_track(path)
            if track:
                tracks.append(track)
            else:
                unreadable += 1

        if not tracks:
            if unreadable:
                print(f"No readable audio files found in {self.source_dir}. Skipped unreadable: {unreadable}")
            else:
                print(f"No audio files found in {self.source_dir}")
            return

        print(f"Found {len(tracks)} audio file(s).")
        if unreadable:
            print(f"Skipped unreadable audio file(s): {unreadable}")

        jobs = [track for track in tracks if self.should_process(track)]
        skipped = len(tracks) - len(jobs)

        if skipped:
            print(f"Skip existing: {skipped}")

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
        return not self.output_path_for(track).exists()

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

    def read_track(self, path: Path) -> LocalTrack | None:
        tags = read_tags_with_mutagen(path)
        if not tags and self.ffprobe_path:
            tags = read_tags_with_ffprobe(path, self.ffprobe_path)

        if tags is None:
            progress_write(f"Skip unreadable: {path}")
            return None

        if not tags and not is_decodable_audio(path, self.ffmpeg_path):
            progress_write(f"Skip unreadable: {path}")
            return None

        artist = first_tag(tags, "artist", "album_artist", default="Unknown Artist")
        title = first_tag(tags, "title", default=path.stem)
        album = first_tag(tags, "album", default="Unknown Album")
        album_artist = first_tag(tags, "album_artist", "albumartist", "artist", default=artist)
        audio_info = read_audio_info(path, self.ffprobe_path)

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
            audio_bitrate=audio_info.bitrate,
            sample_rate=audio_info.sample_rate,
        )

    def build_ipod_file(self, track: LocalTrack, cover_path: Path | None, output_path: Path) -> None:
        temp_output_path = output_path.with_name(
            f".{output_path.stem}.{threading.get_ident()}.tmp{output_path.suffix}"
        )
        temp_output_path.unlink(missing_ok=True)

        cmd = [self.ffmpeg_path, "-y", "-i", str(track.path)]

        if cover_path:
            cmd.extend(["-i", str(cover_path), "-map", "0:a:0", "-map", "1:v:0"])
        else:
            cmd.extend(["-map", "0:a:0"])

        cmd.extend(["-map_metadata", "-1"])

        audio_mode = self.resolve_audio_mode(track)
        if audio_mode == "copy":
            cmd.extend(["-c:a", "copy"])
        else:
            cmd.extend(["-c:a", "aac"])
            if track.audio_bitrate:
                cmd.extend(["-b:a", str(track.audio_bitrate)])
            else:
                cmd.extend(["-b:a", "320k"])

            if track.sample_rate:
                cmd.extend(["-ar", str(track.sample_rate)])

        if cover_path:
            if output_extension_for(track.path) == "mp3":
                cmd.extend(["-c:v", "mjpeg", "-id3v2_version", "3"])
            else:
                cmd.extend(["-c:v", "mjpeg", "-disposition:v", "attached_pic"])

        if output_extension_for(track.path) == "m4a":
            cmd.extend(["-movflags", "+faststart"])

        cmd.extend(metadata_args(track))
        cmd.append(str(temp_output_path))

        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if cover_path and output_extension_for(track.path) == "mp3":
                embed_mp3_cover(temp_output_path, cover_path)
            if not is_decodable_audio(temp_output_path, self.ffmpeg_path):
                raise RuntimeError("FFmpeg produced an unreadable output file.")
            temp_output_path.replace(output_path)
        except Exception:
            temp_output_path.unlink(missing_ok=True)
            raise

    def resolve_audio_mode(self, track: LocalTrack) -> str:
        if track.path.suffix.casefold() == ".mp3":
            return "copy"

        if track.path.suffix.casefold() in {".m4a", ".aac"}:
            return "copy"

        return "encode"


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
            "scale=500:500:force_original_aspect_ratio=decrease,"
            "pad=500:500:(ow-iw)/2:(oh-ih)/2:color=white,format=yuvj420p",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        save_baseline_jpeg(output_path)


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


def read_tags_with_mutagen(path: Path) -> dict[str, str] | None:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return {}

    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        return {}

    if not audio or not audio.tags:
        return {}

    return normalize_tags(dict(audio.tags))


def read_tags_with_ffprobe(path: Path, ffprobe_path: str) -> dict[str, str] | None:
    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        data = json.loads(result.stdout or "{}")
    except Exception:
        return None

    return normalize_tags(data.get("format", {}).get("tags", {}))


def read_audio_info(path: Path, ffprobe_path: str | None) -> AudioInfo:
    info = read_audio_info_with_mutagen(path)
    if (info.bitrate and info.sample_rate) or not ffprobe_path:
        return info

    ffprobe_info = read_audio_info_with_ffprobe(path, ffprobe_path)
    return AudioInfo(
        bitrate=info.bitrate or ffprobe_info.bitrate,
        sample_rate=info.sample_rate or ffprobe_info.sample_rate,
    )


def read_audio_info_with_mutagen(path: Path) -> AudioInfo:
    try:
        from mutagen import File as MutagenFile
    except ImportError:
        return AudioInfo()

    try:
        audio = MutagenFile(path, easy=False)
    except Exception:
        return AudioInfo()

    if not audio or not audio.info:
        return AudioInfo()

    return AudioInfo(
        bitrate=safe_int(getattr(audio.info, "bitrate", 0)),
        sample_rate=safe_int(getattr(audio.info, "sample_rate", 0)),
    )


def read_audio_info_with_ffprobe(path: Path, ffprobe_path: str) -> AudioInfo:
    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-select_streams",
        "a:0",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        data = json.loads(result.stdout or "{}")
    except Exception:
        return AudioInfo()

    streams = data.get("streams") or []
    stream = streams[0] if streams else {}
    return AudioInfo(
        bitrate=safe_int(stream.get("bit_rate")),
        sample_rate=safe_int(stream.get("sample_rate")),
    )


def is_decodable_audio(path: Path, ffmpeg_path: str) -> bool:
    cmd = [
        ffmpeg_path,
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False

    return result.returncode == 0


def embed_mp3_cover(mp3_path: Path, cover_path: Path) -> None:
    try:
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError
    except ImportError:
        return

    try:
        tags = ID3(mp3_path)
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("APIC")
    tags.add(
        APIC(
            encoding=0,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=cover_path.read_bytes(),
        )
    )
    tags.save(mp3_path, v2_version=3)


def save_baseline_jpeg(path: Path) -> None:
    try:
        from PIL import Image
    except ImportError:
        return

    with Image.open(path) as image:
        image = image.convert("RGB")
        image.save(
            path,
            format="JPEG",
            quality=90,
            optimize=False,
            progressive=False,
            subsampling=2,
        )


def normalize_tags(tags: dict) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in tags.items():
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""

        text = str(value).strip()
        if text:
            normalized[key.casefold().replace(" ", "_")] = text

    return normalized


def metadata_args(track: LocalTrack) -> list[str]:
    values = {
        "artist": track.artist,
        "title": track.title,
        "album": track.album,
        "album_artist": track.album_artist,
        "date": track.year,
        "genre": track.genre,
        "track": track.track,
        "disc": track.disc,
    }

    args: list[str] = []
    for key, value in values.items():
        args.extend(["-metadata", f"{key}={value}"])
    return args


def first_tag(tags: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = tags.get(name.casefold().replace(" ", "_"))
        if value:
            return value
    return default


def normalize_number(value: str) -> int:
    match = re.search(r"\d+", value or "")
    return int(match.group(0)) if match else 0


def safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def output_extension_for(path: Path) -> str:
    if path.suffix.casefold() == ".mp3":
        return "mp3"
    return "m4a"


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

    local_path = Path("ffmpeg")
    if local_path.exists():
        return str(local_path.resolve())

    windows_local_path = Path("ffmpeg.exe")
    if sys.platform.startswith("win") and windows_local_path.exists():
        return str(windows_local_path.resolve())

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
            return str(local_path.resolve())

    found = shutil.which(name)
    if found:
        return found

    return None


def resolve_worker_count(workers: int | None) -> int:
    if workers:
        return max(1, workers)

    cpu_count = os.cpu_count() or 4
    return max(2, min(cpu_count, 8))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sort local music by albums and add iPod-friendly embedded covers."
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
        "-j",
        "--workers",
        type=int,
        default=None,
        help="Override automatic parallel worker count.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ffmpeg_path = find_ffmpeg()
    local_ffprobe = "ffprobe.exe" if sys.platform.startswith("win") else None
    ffprobe_path = find_tool("ffprobe", local_ffprobe)

    sorter = MusicSorterForIPod(
        source_dir=args.source_dir.expanduser(),
        output_dir=args.output_dir.expanduser(),
        cache_dir=(args.output_dir.expanduser() / ".covers"),
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        workers=args.workers,
    )
    sorter.run()


if __name__ == "__main__":
    main()
