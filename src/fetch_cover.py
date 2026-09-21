import os
import sys
import time
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import settings


def get_cover(artist, title):
    try:
        # params= lets requests encode the query: an artist with '&' in the
        # name used to cut the search string short
        res = requests.get(
            "https://itunes.apple.com/search",
            params={"term": f"{artist} {title}", "entity": "song", "limit": 1},
            timeout=5,
        )
        data = res.json()

        if data.get("resultCount", 0) > 0:
            artwork_url = data["results"][0]["artworkUrl100"]
            return artwork_url.replace("100x100bb.jpg", "600x600bb.jpg")
    except Exception:
        pass
    return ""


def cover_processing():
    tracklist_file = settings.path("tracklist_file")
    print("Starting cover finding by iTunes API...\n")

    if not os.path.exists(tracklist_file):
        os.makedirs(os.path.dirname(tracklist_file), exist_ok=True)
        with open(tracklist_file, 'w', encoding='utf-8') as test_track:
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

    print(f"\nAll task done, open: {tracklist_file}")


if __name__ == "__main__":
    cover_processing()
