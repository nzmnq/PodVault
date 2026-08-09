import os
import time
import requests

tracklist_file = r".\data\tracklist.txt"


def get_cover(artist, title):
    query = f"{artist} {title}"
    url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=1"

    try:
        res = requests.get(url, timeout=5)
        data = res.json()

        if data.get("resultCount", 0) > 0:
            artwork_url = data["results"][0]["artworkUrl100"]
            return artwork_url.replace("100x100bb.jpg", "600x600bb.jpg")
    except Exception:
        pass
    return ""

def cover_processing():
    print("Starting cover finding by iTunes API...\n")

    if not os.path.exists(tracklist_file):
        with open(os.path.join(tracklist_file), 'w') as test_track:
            test_track.write("#Here is exapmle:\n Rick Astley | Never Gonna Give You Up | Whenever You Need Somebody | Rick Astley | 1987 | Pop/Dance-Pop | 1 | 1 |  | https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    with open(tracklist_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    processed_lines = []

    for line in lines:
        raw_line = line.strip()

        if not raw_line or raw_line.startswith("#"):
            processed_lines.append(line)
            continue

        parts = [p.strip() for p in raw_line.split("|")]

        if len(parts) >= 2:
            artist = parts[0]
            title = parts[1]
            print(f"Looking for: {artist} - {title}...", end=" ")

            cover_url = get_cover(artist, title)
            while len(parts) < 9:
                parts.append("")

            if cover_url:
                parts[8] = cover_url
                print("✓ Found!")
            else:
                print("✕ Not found")

            new_line = " | ".join(parts)
            if not new_line.endswith("|"):
                new_line += " |"

            processed_lines.append(new_line + "\n")

            time.sleep(0.5)
        else:
            processed_lines.append(line)

    with open(tracklist_file, "w", encoding="utf-8") as out:
        out.writelines(processed_lines)

if __name__ == "__main__":
    cover_processing()
    print(f"\nAll task done, open: {tracklist_file}")