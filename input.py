from src.flac_to_alac import FlacToAlacConverter
from src.metadata_download import IPodDownloader
print("utility started")

choice = input("Choose utility:\n1. Convert FLAC to ALAC\n2. Download and embed metadata\nEnter choice: ")

if choice == "1":
    print("Starting FLAC to ALAC conversion...")
    FlacToAlacConverter.convert_flac_to_alac("./input_folder", "./ALAC_Output")
elif choice == "2":
    print("Starting file downloading and metadata embedding...")
    IPodDownloader().process_list()
else:
    print("Invalid choice. Exiting.")