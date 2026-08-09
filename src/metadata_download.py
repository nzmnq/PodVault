import os
import subprocess
import yt_dlp
import requests
import re
from pathlib import Path

DEFAULT_TRACKLIST = r'.\data\tracklist.txt'
DEFAULT_DOWNLOAD_DIR = r'.\data\iPod_Music'

class IPodDownloader:
    def __init__(self, tracklist_file=None, download_dir=None):
        self.tracklist_file = tracklist_file if tracklist_file else DEFAULT_TRACKLIST
        self.download_dir = os.path.expanduser(download_dir if download_dir else DEFAULT_DOWNLOAD_DIR)
        
        self.ffmpeg_path = str(Path('./bin/ffmpeg.exe').resolve())
        
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir, exist_ok=True)

    def process_list(self):
        if not os.path.exists(self.tracklist_file):
            tracklist_dir = os.path.dirname(self.tracklist_file)
            if tracklist_dir and not os.path.exists(tracklist_dir):
                os.makedirs(tracklist_dir, exist_ok=True)
                
            with open(self.tracklist_file, 'w', encoding='utf-8') as test_track:
                test_track.write("#Here is example:\n Rick Astley | Never Gonna Give You Up | Whenever You Need Somebody | Rick Astley | 1987 | Pop/Dance-Pop | 1 | 1 | https://is1-ssl.mzstatic.com/image/thumb/Music115/v4/a4/82/10/a482103f-7389-4977-83d8-574360e227a8/078221852424.jpg/600x600bb.jpg | https://www.youtube.com/watch?v=dQw4w9WgXcQ")

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
            print(f"Cannot download artwork: {e}")
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
        
        raw_name = f"{meta['artist']} - {meta['title']}"
        safe_name = re.sub(r'[\\/*?:"<>|]', "", raw_name)
        
        temp_base = os.path.join(self.download_dir, f"temp_{safe_name}")
        
        temp_audio = f"{temp_base}.m4a" 
        temp_cover = os.path.join(self.download_dir, f"temp_{safe_name}.jpg")
        final_audio = os.path.join(self.download_dir, f"{safe_name}.m4a")

        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio', 
            'ffmpeg_location': self.ffmpeg_path,
            'cookiefile': 'cookies.txt',
            'outtmpl': f"{temp_base}.%(ext)s",
            'quiet': False,
            'no_warnings': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio', 
                'preferredcodec': 'm4a',
            }],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                error_code = ydl.download([query])
                if error_code != 0:
                    print("Cannot download, skipping.")
                    return
        except Exception as e:
            print(f"Critical yt-dlp error: {e}")
            return

        if not os.path.exists(temp_audio):
            print(f"Temp file {temp_audio} not found.")
            return

        has_cover = False
        if meta['cover']:
            has_cover = self._download_cover(meta['cover'], temp_cover)

        print("Embedding metadata...")
        self._build_final_file(temp_audio, temp_cover if has_cover else None, final_audio, meta)

        try:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
            if os.path.exists(temp_cover):
                os.remove(temp_cover)
        except Exception as e:
            print(f"Warning: Could not remove temp files: {e}")

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
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
            print(f"Done: {os.path.basename(final_output)}")
        except subprocess.CalledProcessError:
            print("FFmpeg error occurred during metadata embedding.")

if __name__ == "__main__":
    print("iPod Media Builder started!")
    
    downloader = IPodDownloader()
    downloader.process_list()

    print("\nAll tasks done!")