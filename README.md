# music_transfer

Python/OOP скрипт для завантаження аудіо з YouTube і створення Apple/iPod-friendly `.m4a` файлів з обкладинкою та чистими тегами.

## Встановлення

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
brew install ffmpeg
```

### Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
winget install Gyan.FFmpeg
```

У цьому репозиторії також може лежати локальний `ffmpeg.exe`; скрипт використає його автоматично, якщо файл існує.

## Cookies YouTube

Покладіть файл `cookies.txt` поруч зі скриптом. Він використовується `yt-dlp` для обходу вікових, регіональних або login-based обмежень YouTube.

## Формат `tracklist.txt`

Один трек на рядок:

```text
Виконавець | Назва | Альбом | Композитор | Рік | Жанр | Номер треку | Номер диска | Посилання на обкладинку
```

Приклад:

```text
Daft Punk | Get Lucky | Random Access Memories | Thomas Bangalter, Guy-Manuel de Homem-Christo | 2013 | Disco | 8 | 1 | https://example.com/cover.jpg
Daft Punk | https://www.youtube.com/watch?v=5NV6Rdv1a3I | Random Access Memories | Thomas Bangalter | 2013 | Disco | 8 | 1 | https://example.com/cover.jpg
```

Якщо поле `Назва` починається з `http`, скрипт завантажує пряме посилання. Інакше шукає через:

```text
ytsearch1:Виконавець Назва audio
```

## Запуск

```bash
python ipod_media_builder.py
```

Готові файли з'являться в папці `iPod_Music`.

## Що робить скрипт

- `yt-dlp` завантажує лише сирий аудіопотік з `format=bestaudio/best`, `cookies.txt`, `quiet=False`.
- `requests` завантажує обкладинку у тимчасовий `.jpg`.
- `subprocess` запускає FFmpeg для фінальної збірки.
- FFmpeg чистить метадані `-map_metadata -1`, кодує AAC `320k`, додає `-movflags +faststart`, вшиває `artist`, `title`, `album`, `composer`, `date`, `genre`, `track`, `disc`.
- Якщо є обкладинка, вона додається як `attached_pic`.
- Після успішного створення `.m4a` тимчасові файли видаляються.

## Сортування вже завантаженої музики по альбомах

Окремий скрипт `sort_music_for_ipod.py` бере локальні аудіофайли з будь-якої папки, читає теги через `mutagen` або `ffprobe`, шукає доступні обкладинки альбомів без логіна через Deezer, MusicBrainz/Cover Art Archive та iTunes, конвертує обкладинку у baseline JPEG 500x500 і створює iPod-friendly копії з вшитою обкладинкою.

Вхідні файли не змінюються.

Треки обробляються паралельно з автоматично підібраною кількістю потоків і прогресбаром, а обкладинка кешується окремо для кожного `artist + album`, щоб різні альбоми не отримували чужу картинку.

MP3 зберігаються як MP3 без перекодування, щоб не втрачати 320k-якість. M4A/AAC теж копіюються без перекодування. Інші формати автоматично кодуються в M4A AAC з bitrate та sample rate, прочитаними з оригінального файла.

FFmpeg буде знайдено автоматично: локальний `ffmpeg`, системний `ffmpeg`, Windows `ffmpeg.exe` або bundled binary з пакета `imageio-ffmpeg`. Це працює і на Linux після встановлення залежностей з `requirements.txt`.

```bash
python sort_music_for_ipod.py "/path/to/music" -o iPod_Sorted_Music
```

Кількість потоків підбирається автоматично. За потреби її можна обмежити вручну:

```bash
python sort_music_for_ipod.py "/path/to/music" --workers 6
```

Готові файли будуть розкладені так:

```text
iPod_Sorted_Music/
  Album/
    01 - Song.mp3
    02 - Song.m4a
```
