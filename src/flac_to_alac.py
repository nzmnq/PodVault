import subprocess
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mutagen import MutagenError
from mutagen.flac import FLAC

import settings
from i18n import _


class FlacToAlacConverter:
    @staticmethod
    def check_ffmpeg():
        return settings.ffmpeg() is not None

    @staticmethod
    def convert_flac_to_alac(input_folder=None, output_folder=None):
        cfg = settings.require()
        ffmpeg = settings.ffmpeg(cfg)
        if not ffmpeg:
            print(_("ffmpeg not found. Set its path on the Settings screen."))
            return

        if input_folder is None:
            input_folder = settings.path("flac_input_dir", cfg)
        if output_folder is None:
            output_folder = settings.path("alac_output_dir", cfg)

        input_path = Path(input_folder)

        if not input_path.exists() or not input_path.is_dir():
            input_path.mkdir(parents=True, exist_ok=True)

        if output_folder:
            output_path = Path(output_folder)
            output_path.mkdir(parents=True, exist_ok=True)
        else:
            output_path = input_path

        flac_files = list(input_path.glob("*.flac"))

        if not flac_files:
            print(_("No FLAC files in the folder '{folder}'.").format(folder=input_folder))
            return

        print(_("Files to convert: {n}\n").format(n=len(flac_files)))

        for flac_file in flac_files:
            output_file = output_path / f"{flac_file.stem}.m4a"

            print(_("Converting: {file} ...").format(file=flac_file.name))
            try:
                rate = 48000 if FLAC(flac_file).info.sample_rate % 48000 == 0 else 44100
            except (MutagenError, OSError):
                rate = 44100
            command = [
                ffmpeg,
                "-y",
                "-loglevel", "error",
                "-i", str(flac_file),
                "-af", "aresample=dither_method=triangular_hp",
                "-ar", str(rate),
                "-sample_fmt", "s16p",      # an old iPod can't play 24-bit / 96 kHz ALAC
                "-c:a", "alac",
                "-vn",
                str(output_file)
            ]

            try:
                subprocess.run(command, check=True)
                print(_("Ready: {file}").format(file=output_file.name))
            except subprocess.CalledProcessError:
                print(_("Could not convert {file}.").format(file=flac_file.name))


if __name__ == "__main__":
    if not FlacToAlacConverter.check_ffmpeg():
        print(_("ffmpeg not found. Set its path on the Settings screen."))
        sys.exit(1)

    print(_("Started"))
    FlacToAlacConverter.convert_flac_to_alac()
    print("-" * 30)
    print(_("All done."))
