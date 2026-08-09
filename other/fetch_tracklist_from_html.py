import os
import re
try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Error: BeautifulSoup library is not installed.")
    print("Open your terminal and run: pip install beautifulsoup4")
    exit()

html_file = r''
ipod_folder = r'C:\Path\to\data\iPod_Music'
output_tracklist = r'new_tracks_to_download.txt'

def get_existing_songs(folder):
    existing = set()
    if not os.path.exists(folder):
        return existing
    for file in os.listdir(folder):
        if file.endswith('.m4a'):
            name = file[:-4].strip().lower()
            existing.add(name)
    return existing

def parse_apple_music_html(html_path):
    with open(html_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')
        
    songs = []
    rows = soup.find_all('div', role='row')
    if not rows:
        rows = soup.find_all('div', class_=re.compile(r'songs-list-row', re.I))
    
    for row in rows:
        title_elem = row.find(class_=re.compile(r'song-name|track-title', re.I))
        artist_elem = row.find(class_=re.compile(r'by-line|artist', re.I))
        
        if title_elem and artist_elem:
            title = title_elem.get_text(strip=True)
            artist = artist_elem.get_text(strip=True)
            songs.append({"artist": artist, "title": title})
            
    return songs

print("Scanning iPod database...")
existing_songs = get_existing_songs(ipod_folder)

print("Reading Apple Music HTML file...")
apple_songs = parse_apple_music_html(html_file)

missing_songs = []
for song in apple_songs:
    artist = song['artist']
    title = song['title']
    
    raw_name = f"{artist} - {title}"
    safe_name = re.sub(r'[\\/*?:"<>|]', "", raw_name).lower()
    
    if safe_name not in existing_songs:
        missing_songs.append(f"{artist} | {title} | Apple Playlist | {artist} | 2024 | Pop | 1 | 1 | ")

with open(output_tracklist, 'w', encoding='utf-8') as f:
    f.write("# --- NEW SONGS FROM PLAYLIST ---\n")
    for line in missing_songs:
        f.write(line + "\n")

print("\nSYNCHRONIZATION COMPLETE:")
print(f"Total songs in playlist: {len(apple_songs)}")
print(f"Already downloaded to computer: {len(existing_songs)}")
print(f"Found NEW tracks (duplicates skipped): {len(missing_songs)}")
print(f"\nResult saved to: {output_tracklist}")