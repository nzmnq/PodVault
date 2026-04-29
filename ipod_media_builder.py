from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
import yt_dlp


@dataclass(frozen=True)
class TrackMetadata:
    artist: str
    title: str
    album: str = ""
    composer: str = ""
    year: str = ""
    genre: str = ""
    track: str = "1"
    disc: str = "1"
    cover_url: str = ""

    @property
    def source(self) -> str:
        if self.title.lower().startswith(("http://", "https://")):
            return self.title
        return f"ytsearch1:{self.artist} {self.title} audio"

    @property
    def output_name(self) -> str:
        return sanitize_filename(f"{self.artist} - {self.title}.m4a")


class TracklistReader:
    def __init__(self, tracklist_path: Path) -> None:
        self.tracklist_path = tracklist_path

    def read(self) -> Iterable[TrackMetadata]:
        if not self.tracklist_path.exists():
            raise FileNotFoundError(f"Tracklist not found: {self.tracklist_path}")

        with self.tracklist_path.open("r", encoding="utf-8") as file:
            for line_number, raw_line in enumerate(file, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 2:
                    print(f"Skip line {line_number}: expected at least artist and title.")
                    continue

                yield TrackMetadata(
                    artist=parts[0] or "Unknown Artist",
                    title=parts[1] or "Unknown Title",
                    album=get_part(parts, 2),
                    composer=get_part(parts, 3),
                    year=get_part(parts, 4),
                    genre=get_part(parts, 5),
                    track=get_part(parts, 6, "1"),
                    disc=get_part(parts, 7, "1"),
                    cover_url=get_part(parts, 8),
                )


class YouTubeAudioDownloader:
    def __init__(self, temp_dir: Path, cookies_path: Path | None = None) -> None:
        self.temp_dir = temp_dir
        self.cookies_path = cookies_path

    def download(self, track: TrackMetadata) -> Path:
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        temp_stem = sanitize_filename(f"{track.artist} - {track.title}")

        # yt-dlp only downloads the source audio stream here. Final encoding,
        # metadata, and cover embedding are handled by FFmpegIPodAssembler.
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(self.temp_dir / f"{temp_stem}.%(ext)s"),
            "cookiefile": str(self.cookies_path) if self.cookies_path and self.cookies_path.exists() else None,
            "quiet": False,
            "no_warnings": False,
            "noplaylist": True,
        }

        ydl_opts = {key: value for key, value in ydl_opts.items() if value is not None}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(track.source, download=True)

        downloaded_file = self._resolve_downloaded_file(info)
        if not downloaded_file.exists():
            raise FileNotFoundError(f"Downloaded audio file was not found: {downloaded_file}")

        return downloaded_file

    def _resolve_downloaded_file(self, info: dict) -> Path:
        if "entries" in info:
            entries = [entry for entry in info["entries"] if entry]
            if not entries:
                raise RuntimeError("yt-dlp search returned no entries.")
            info = entries[0]

        requested_downloads = info.get("requested_downloads") or []
        for download in requested_downloads:
            filepath = download.get("filepath")
            if filepath:
                return Path(filepath)

        filepath = info.get("filepath") or info.get("_filename")
        if filepath:
            return Path(filepath)

        raise RuntimeError("Could not determine downloaded file path from yt-dlp.")


class CoverDownloader:
    def __init__(self, temp_dir: Path) -> None:
        self.temp_dir = temp_dir

    def download(self, track: TrackMetadata) -> Path | None:
        if not track.cover_url:
            return None

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        cover_path = self.temp_dir / f"{sanitize_filename(f'{track.artist} - {track.title}')}.jpg"

        response = requests.get(track.cover_url, timeout=20)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if content_type and not content_type.lower().startswith("image/"):
            raise ValueError(f"Cover URL did not return an image: {content_type}")

        cover_path.write_bytes(response.content)
        return cover_path


class FFmpegIPodAssembler:
    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    def build(self, audio_path: Path, cover_path: Path | None, output_path: Path, track: TrackMetadata) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i",
            str(audio_path),
        ]

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
                "320k",
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
                f"composer={track.composer}",
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

        subprocess.run(cmd, check=True)


class IPodLibraryBuilder:
    def __init__(
        self,
        tracklist_path: str = "tracklist.txt",
        output_dir: str = "iPod_Music",
        temp_dir: str = "iPod_Music/.tmp",
        cookies_path: str = "cookies.txt",
        ffmpeg_path: str | None = None,
    ) -> None:
        self.tracklist_path = Path(tracklist_path)
        self.output_dir = Path(output_dir)
        self.temp_dir = Path(temp_dir)
        self.cookies_path = Path(cookies_path)
        self.ffmpeg_path = ffmpeg_path or find_ffmpeg()

        self.reader = TracklistReader(self.tracklist_path)
        self.audio_downloader = YouTubeAudioDownloader(self.temp_dir, self.cookies_path)
        self.cover_downloader = CoverDownloader(self.temp_dir)
        self.assembler = FFmpegIPodAssembler(self.ffmpeg_path)

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for track in self.reader.read():
            print(f"\nProcessing: {track.artist} - {track.title}")
            temp_files: list[Path] = []
            final_path = self.output_dir / track.output_name

            try:
                audio_path = self.audio_downloader.download(track)
                temp_files.append(audio_path)

                cover_path = self.cover_downloader.download(track)
                if cover_path:
                    temp_files.append(cover_path)

                self.assembler.build(audio_path, cover_path, final_path, track)
                self._cleanup(temp_files)
                print(f"Done: {final_path}")
            except Exception as error:
                print(f"Failed: {track.artist} - {track.title}: {error}")

    def _cleanup(self, paths: Iterable[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                print(f"Could not remove temporary file {path}: {error}")


def get_part(parts: list[str], index: int, default: str = "") -> str:
    value = parts[index].strip() if len(parts) > index else ""
    return value or default


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:180] or "Unknown"


def find_ffmpeg() -> str:
    local_ffmpeg = Path("ffmpeg.exe")
    if local_ffmpeg.exists():
        return str(local_ffmpeg)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    return "ffmpeg"


if __name__ == "__main__":
    print("iPod media builder started.")
    IPodLibraryBuilder().run()
    print("\nAll tasks done.")
