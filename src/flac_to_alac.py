import subprocess
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings


class FlacToAlacConverter:
    @staticmethod
    def check_ffmpeg():
        return settings.ffmpeg() is not None

    @staticmethod
    def convert_flac_to_alac(input_folder=None, output_folder=None):
        cfg = settings.require()
        ffmpeg = settings.ffmpeg(cfg)
        if not ffmpeg:
            print("ffmpeg not found. Set its path on the Settings screen.")
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
            print(f"In folder '{input_folder}' FLAC not found.")
            return

        print(f"Found files for converting: {len(flac_files)}\n")

        for flac_file in flac_files:
            output_file = output_path / f"{flac_file.stem}.m4a"

            print(f"Converting: {flac_file.name} ...")
            command = [
                ffmpeg,
                "-y",
                "-loglevel", "error",
                "-i", str(flac_file),
                "-c:a", "alac",
                "-vn",
                str(output_file)
            ]

            try:
                subprocess.run(command, check=True)
                print(f"Ready: {output_file.name}")
            except subprocess.CalledProcessError:
                print(f"Error occured by converting {flac_file.name}.")


if __name__ == "__main__":
    if not FlacToAlacConverter.check_ffmpeg():
        print("Error, FFMPEG not found. Set its path on the Settings screen.")
        sys.exit(1)

    print("Started")
    FlacToAlacConverter.convert_flac_to_alac()
    print("-" * 30)
    print("All task done")
