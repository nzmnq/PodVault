import os
import subprocess
import yt_dlp
import requests

class IPodDownloader:
    def __init__(self, tracklist_file='tracklist.txt', download_dir='iPod_Music'):
        self.tracklist_file = tracklist_file
        self.download_dir = os.path.expanduser(download_dir)
        self.ffmpeg_path = './ffmpeg.exe'
        
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

    def process_list(self):
        if not os.path.exists(self.tracklist_file):
            print(f"File {self.tracklist_file} Not found")
            return

        with open(self.tracklist_file, 'r', encoding='utf-8') as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                self._handle_track(line)

    def _download_cover(self, url, temp_cover_path):
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                with open(temp_cover_path, 'wb') as f:
                    f.write(res.content)
                return True
        except Exception as e:
            print(f"Can't download artwork: {e}")
        return False

    def _handle_track(self, line):
        parts = [p.strip() for p in line.split('|')]
        
        meta = {
            'artist':   parts[0] if len(parts) > 0 else "Unknown Artist",
            'title':    parts[1] if len(parts) > 1 else "Unknown Title",
            'album':    parts[2] if len(parts) > 2 else "",
            'composer': parts[3] if len(parts) > 3 else "",
            'year':     parts[4] if len(parts) > 4 else "",
            'genre':    parts[5] if len(parts) > 5 else "",
            'track':    parts[6] if len(parts) > 6 else "1",
            'disc':     parts[7] if len(parts) > 7 else "1",
            'cover':    parts[8] if len(parts) > 8 else None
        }

        print(f"\nDownloading: {meta['track']}. {meta['title']} - {meta['artist']}")
        
        if meta['title'].startswith('http'):
            query = meta['title']
        else:
            query = f"ytsearch1:{meta['artist']} {meta['title']} audio"
        
        safe_name = f"{meta['artist']} - {meta['title']}".replace('/', '_').replace('\\', '_')
        
        temp_audio = os.path.join(self.download_dir, f"temp_{safe_name}.m4a")
        temp_cover = os.path.join(self.download_dir, f"temp_{safe_name}.jpg")
        final_audio = os.path.join(self.download_dir, f"{safe_name}.m4a")

        # НАЛАШТУВАННЯ ДЛЯ МАКСИМАЛЬНОЇ ЯКОСТІ
        ydl_opts = {
            # 1. Прибираємо обмеження [ext=m4a]. Тепер yt-dlp стягне найважчий і 
            # найякісніший потік (навіть якщо це WebM/Opus)
            'format': 'bestaudio/best', 
            
            'ffmpeg_location': self.ffmpeg_path,
            'cookiefile': 'cookies.txt',
            'outtmpl': temp_audio,
            'quiet': False,
            'no_warnings': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio', 
                'preferredcodec': 'm4a',
                # 2. Форсуємо максимальний бітрейт при створенні файлу для iPod
                'preferredquality': '320', 
            }],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
 
                error_code = ydl.download([query])
                if error_code != 0:
                    print("Cannot download, skip.")
                    return
        except Exception as e:
            print(f"Critical error yt-dlp: {e}")
            return

        if not os.path.exists(temp_audio):
            print(f"Temp file {temp_audio} not found.")
            return

        has_cover = False
        if meta['cover']:
            has_cover = self._download_cover(meta['cover'], temp_cover)

        print(f"Embedding metadata...")
        self._build_final_file(temp_audio, temp_cover if has_cover else None, final_audio, meta)

        if os.path.exists(temp_audio):
            os.remove(temp_audio)
        if os.path.exists(temp_cover):
            os.remove(temp_cover)

    def _build_final_file(self, temp_audio, temp_cover, final_output, meta):
        cmd = [self.ffmpeg_path, '-y', '-v', 'error', '-i', temp_audio]

        if temp_cover:
            cmd.extend([
                '-i', temp_cover, 
                '-map', '0:a', '-map', '1:v', 
                '-c:a', 'copy', '-c:v', 'mjpeg', 
                '-disposition:v', 'attached_pic'
            ])
        else:
            cmd.extend(['-map', '0:a', '-c:a', 'copy'])

        cmd.extend([
            '-map_metadata', '-1',
            '-movflags', '+faststart',
            
            '-metadata', f"title={meta['title']}",
            '-metadata', f"artist={meta['artist']}",
            '-metadata', f"album={meta['album']}",
            '-metadata', f"composer={meta['composer']}",
            '-metadata', f"date={meta['year']}",
            '-metadata', f"genre={meta['genre']}",
            '-metadata', f"track={meta['track']}",
            '-metadata', f"disc={meta['disc']}",
            final_output
        ])

        try:
            import subprocess
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            print(f"✅ Готово: {os.path.basename(final_output)}")
        except subprocess.CalledProcessError:
            print(f"❌ Сталася помилка FFmpeg під час вшивання тегів.")

if __name__ == "__main__":
    print("Ipod media builder started!")
    downloader = IPodDownloader()
    downloader.process_list()
    print("\nAll task done!")