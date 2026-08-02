import os
import re
try:
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ Помилка: Бібліотека BeautifulSoup не встановлена.")
    print("Відкрий термінал і введи: pip install beautifulsoup4")
    exit()

# ==========================================
# --- ТВОЇ ШЛЯХИ ---
# ==========================================
# 1. Шлях до завантаженого тобою HTML-файлу
html_file = r'C:\Users\nazar\New folder\_Favourite Songs - Playlist - Apple Music.htm'

# 2. Шлях до папки з уже завантаженою музикою (куди качає yt-dlp)
ipod_folder = r'C:\Шлях\до\data\iPod_Music'

# 3. Як назвати файл із готовим результатом
output_tracklist = r'new_tracks_to_download.txt'
# ==========================================

def get_existing_songs(folder):
    existing = set()
    if not os.path.exists(folder):
        return existing
    for file in os.listdir(folder):
        if file.endswith('.m4a'):
            name = file[:-4].strip().lower() # Відрізаємо розширення
            existing.add(name)
    return existing

def parse_apple_music_html(html_path):
    with open(html_path, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')
        
    songs = []
    # Шукаємо всі можливі блоки, які Apple Music використовує для рядків
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

print("🔍 Сканування бази даних iPod...")
existing_songs = get_existing_songs(ipod_folder)

print("🎵 Читання HTML-файлу Apple Music...")
apple_songs = parse_apple_music_html(html_file)

missing_songs = []
for song in apple_songs:
    artist = song['artist']
    title = song['title']
    
    # Генеруємо назву файлу за тим самим алгоритмом, що й основний скрипт
    raw_name = f"{artist} - {title}"
    safe_name = re.sub(r'[\\/*?:"<>|]', "", raw_name).lower()
    
    # Перевіряємо, чи є вже такий файл у папці
    if safe_name not in existing_songs:
        # Форматуємо рядок ідеально для нашого завантажувача
        missing_songs.append(f"{artist} | {title} | Apple Playlist | {artist} | 2024 | Pop | 1 | 1 | ")

# Записуємо результат у файл
with open(output_tracklist, 'w', encoding='utf-8') as f:
    f.write("# --- НОВІ ПІСНІ З ПЛЕЙЛИСТА ---\n")
    for line in missing_songs:
        f.write(line + "\n")

print("\n✅ СИНХРОНІЗАЦІЮ ЗАВЕРШЕНО:")
print(f"Всього пісень у плейлисті: {len(apple_songs)}")
print(f"Вже завантажено на комп'ютер: {len(existing_songs)}")
print(f"Знайдено НОВИХ треків (дублікати пропущено): {len(missing_songs)}")
print(f"\nГотовий файл збережено як: {output_tracklist}")