import os
from mutagen.mp4 import MP4
from mutagen.mp3 import MP3
from mutagen.easyid3 import EasyID3

# ВКАЖИ ЛІТЕРУ ДИСКА ТВОГО IPOD (наприклад, 'E:', 'F:', 'G:')
IPOD_DRIVE = r'D:' 

ipod_music_path = os.path.join(IPOD_DRIVE, 'iPod_Control', 'Music')
output_file = 'ipod_tracklist.txt'

if not os.path.exists(ipod_music_path):
    print(f"❌ Не знайдено папку {ipod_music_path}.")
    print("Переконайся, що iPod підключено і вказано правильну літеру диска.")
    exit()

print("🔍 Сканування бази даних iPod (це може зайняти хвилину)...")
songs = []

# Проходимося по всіх підпапках F00, F01 тощо
for folder_name in os.listdir(ipod_music_path):
    folder_path = os.path.join(ipod_music_path, folder_name)
    
    if os.path.isdir(folder_path):
        for file_name in os.listdir(folder_path):
            file_path = os.path.join(folder_path, file_name)
            
            artist = "Unknown Artist"
            title = "Unknown Title"
            
            try:
                # Читаємо m4a (AAC/ALAC)
                if file_name.lower().endswith('.m4a'):
                    audio = MP4(file_path)
                    # Теги в mp4 мають специфічні ключі
                    if '\xa9ART' in audio: artist = audio['\xa9ART'][0]
                    if '\xa9nam' in audio: title = audio['\xa9nam'][0]
                
                # Читаємо mp3 (на випадок, якщо там є такі)
                elif file_name.lower().endswith('.mp3'):
                    audio = EasyID3(file_path)
                    if 'artist' in audio: artist = audio['artist'][0]
                    if 'title' in audio: title = audio['title'][0]
                    
                songs.append(f"{artist} - {title}")
                
            except Exception:
                # Пропускаємо файли, які не читаються або не є аудіо
                continue

# Сортуємо за алфавітом і зберігаємо у файл
songs.sort()

with open(output_file, 'w', encoding='utf-8') as f:
    for song in songs:
        f.write(song + '\n')

print(f"\n✅ Готово! Знайдено треків: {len(songs)}")
print(f"Список збережено у файл: {output_file}")