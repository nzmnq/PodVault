import os
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
from mutagen.easyid3 import EasyID3

IPOD_DRIVE = r'D:' 

ipod_music_path = os.path.join(IPOD_DRIVE, 'iPod_Control', 'Music')
output_file = 'ipod_tracklist.txt'

if not os.path.exists(ipod_music_path):
    print(f"Directory {ipod_music_path} not found.")
    print("Make sure your iPod is connected and the correct drive letter is set.")
    exit()

print("Scanning iPod database (this might take a minute)...")
songs = []

for folder_name in os.listdir(ipod_music_path):
    folder_path = os.path.join(ipod_music_path, folder_name)
    
    if os.path.isdir(folder_path):
        for file_name in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file_name)
            
            artist = "Unknown Artist"
            title = "Unknown Title"
            
            try:
                if file_name.lower().endswith('.m4a'):
                    audio = MP4(file_path)
                    if '\xa9ART' in audio: artist = audio['\xa9ART'][0]
                    if '\xa9nam' in audio: title = audio['\xa9nam'][0]
                
                elif file_name.lower().endswith('.mp3'):
                    audio = EasyID3(file_path)
                    if 'artist' in audio: artist = audio['artist'][0]
                    if 'title' in audio: title = audio['title'][0]
                    
                songs.append(f"{artist} - {title}")
                
            except Exception:
                continue

songs.sort()

with open(output_file, 'w', encoding='utf-8') as f:
    for song in songs:
        f.write(song + '\n')

print(f"\nDone! Tracks found: {len(songs)}")
print(f"Tracklist saved to file: {output_file}")