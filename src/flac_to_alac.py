import subprocess
from pathlib import Path
import sys

def check_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        return False

def convert_flac_to_alac(input_folder, output_folder=None):
    input_path = Path(input_folder)
    
    if not input_path.exists() or not input_path.is_dir():
        print(f"Err, folder '{input_folder}' not found.")
        return

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
            "ffmpeg",
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
        except subprocess.CalledProcessError as e:
            print(f"Error occured by converting {flac_file.name}.")

if __name__ == "__main__":
    if not check_ffmpeg():
        print("Error, FFMPEG not founded")
        sys.exit(1)

    INPUT_DIR = "./input_folder" 
    
    OUTPUT_DIR = "./ALAC_Output" 
    
    print("Started")
    convert_flac_to_alac(INPUT_DIR, OUTPUT_DIR)
    print("-" * 30)
    print("All task done")