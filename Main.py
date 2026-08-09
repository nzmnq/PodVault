from src.flac_to_alac import FlacToAlacConverter
from src.metadata_download import IPodDownloader
from src.fetch_cover import cover_processing
from src.sur_sound import audio_processing
print("utility started")

choice = input("Choose utility:\n1. Convert FLAC to ALAC\n2. Download and embed metadata\n3. Spatial sound processing\n4. Cover fetcher\n Enter choice!: ")

if choice == "1":
    print("Starting FLAC to ALAC conversion...")
    FlacToAlacConverter().convert_flac_to_alac()
elif choice == "2":
    print("Starting file downloading and metadata embedding...")
    IPodDownloader().process_list()
elif choice == "3":
    print("Started spatial audio processing...")
    audio_processing()
elif choice == "4":
    print("Started cover fetch utility")
    cover_processing()
else:
    print("Invalid choice. Exiting.")