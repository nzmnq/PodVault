import requests
import time

input_file = 'new_tracks_to_download.txt'
output_file = 'tracklist_with_covers.txt'

def get_cover(artist, title):
    # Формуємо запит до бази iTunes
    query = f"{artist} {title}"
    url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"
    
    try:
        res = requests.get(url, timeout=5)
        data = res.json()
        
        if data['resultCount'] > 0:
            # Apple видає прев'ю 100x100. Ми хакаємо лінк, щоб отримати 600x600!
            artwork_url = data['results'][0]['artworkUrl100']
            return artwork_url.replace('100x100bb.jpg', '600x600bb.jpg')
    except Exception as e:
        pass
    return ""

print("🎨 Починаємо масовий пошук обкладинок через iTunes API...\n")

with open(input_file, 'r', encoding='utf-8') as f:
    lines = f.readlines()

with open(output_file, 'w', encoding='utf-8') as out:
    for line in lines:
        line = line.strip()
        
        # Пропускаємо порожні рядки та коментарі
        if not line or line.startswith('#'):
            out.write(line + '\n')
            continue
        
        # Розбиваємо рядок на частини
        parts = [p.strip() for p in line.split('|')]
        
        if len(parts) >= 2:
            artist = parts[0]
            title = parts[1]
            print(f"Шукаю: {artist} - {title}...", end=" ")
            
            cover_url = get_cover(artist, title)
            
            # Переконуємось, що масив має достатньо елементів для обкладинки (9-та позиція)
            while len(parts) < 9:
                parts.append("")
                
            if cover_url:
                parts[8] = cover_url
                print("✅ Знайдено!")
            else:
                print("❌ Не знайдено")
            
            # Збираємо рядок назад і записуємо
            new_line = " | ".join(parts)
            if not new_line.endswith("|"):
                new_line += " |"
                
            out.write(new_line + '\n')
            
            # Маленька пауза, щоб сервери Apple не заблокували нас за спам
            time.sleep(0.5)

print(f"\n🎉 Магію завершено! Відкрий файл: {output_file}")