# MART — Music Archive Rescue Tool

Recover your music library on SoundCloud. Import from VK, Spotify, Apple Music, Yandex Music — MART finds every track on SoundCloud using AI matching.

2,000 tracks takes ~1 hour and costs ~$0.02 in API fees.

## Features

- **Smart matching** — AI picks the best original uncensored version, rejects slowed/nightcore/covers
- **Multi-platform import** — VK, Spotify, Apple Music, Yandex Music, or any CSV/JSON/TXT
- **AI DJ** — chat with AI to curate playlists from your library ("all Russian trap", "chill 90s vibes")
- **SoundCloud playlists** — create playlists on your SoundCloud account directly
- **Resume-safe** — stop anytime, pick up where you left off
- **Web UI** — browser-based interface for everything

## Quick Start

### 1. Install Python

Download from https://www.python.org/downloads/ — **check "Add Python to PATH"** during install.

### 2. Install dependencies

```
pip install -r requirements.txt
```

Requires: `httpx`, `click`, `rich`, `flask`

### 3. Get an AI API key

| Provider | Get key at | Env variable | Cost |
|----------|-----------|--------------|------|
| **Google Gemini (free)** | https://aistudio.google.com/apikeys | `GEMINI_API_KEY` | **Free** |
| OpenRouter | https://openrouter.ai/keys | `OPENROUTER_API_KEY` | ~$0.02 |
| OpenAI | https://platform.openai.com/api-keys | `OPENAI_API_KEY` | ~$0.10 |
| Anthropic | https://console.anthropic.com/ | `ANTHROPIC_API_KEY` | ~$0.05 |

**Recommended: Google Gemini** — completely free, no credit card required. Go to the link, click "Create API Key", done.

### 4. Export your music library

**VK Music** — use a browser extension like "VK Music Export" to get a `.txt` file:
```
1. Artist - Title
2. Another Artist - Another Title
```

**Spotify** — export from https://exportify.net/ as CSV, or use the Spotify API JSON export.

**Apple Music** — export your library as CSV from the Music app (File → Library → Export).

**Yandex Music** — export via third-party tools as JSON or CSV.

Any CSV with `artist` and `title` columns works. JSON arrays with `artist`/`title` fields work too.

### 5. Run — Web UI (recommended)

```
python ui.py
```

Open http://localhost:8000 in your browser. The web UI lets you:

1. **Connect to SoundCloud** — paste your OAuth token (instructions shown in-app)
2. **Set API key** — enter your LLM API key in Settings
3. **Import library** — upload your exported file (auto-detects format and platform)
4. **Scan tracks** — click Rescan to search SoundCloud for all tracks
5. **Review results** — browse matched/unmatched tracks, listen to previews
6. **AI DJ** — open the DJ tab, type natural language queries to build playlists
7. **Create playlists** — generates a script to paste in SoundCloud's browser console

### 5 (alt). Run — Command line

```
set OPENROUTER_API_KEY=sk-or-your-key-here
python mart.py --input playlist.txt
```

Or just drag your file onto `run.bat`.

## Web UI Guide

### Connecting to SoundCloud

1. Open https://soundcloud.com and log in
2. Open browser DevTools (F12) → Console
3. Type `document.cookie` and press Enter
4. Copy the output — paste it into the **SC Cookies** field in MART Settings
5. For the OAuth token: in DevTools → Application → Cookies → find `oauth_token`

### Importing tracks

Click **Import Library** and upload your file. MART auto-detects:
- **Platform** — Spotify, Apple Music, Yandex Music, VK, or generic
- **Format** — CSV, JSON, or TXT
- **Duplicates** — skips tracks already in your library

### AI DJ

Switch to the **DJ** tab and type requests like:
- "give me 20 Russian rap tracks"
- "chill lo-fi for studying"
- "aggressive 2010s metal"
- "all tracks by Кино"
- "summer party mix"

The AI searches your classified library and returns a curated list. Click **Create Playlist** to export it to SoundCloud.

### Creating SoundCloud Playlists

MART generates a JavaScript snippet you paste into SoundCloud's browser console. This is necessary because SoundCloud's anti-bot protection blocks server-side playlist creation.

1. Click **Create Playlists** (or create from AI DJ)
2. Copy the generated script
3. Go to https://soundcloud.com, open DevTools Console (F12)
4. Paste the script and press Enter
5. Wait for it to finish — playlists appear in your SC library

### Classifying your library

In Settings, click **Classify Library** to tag all tracks with genre, mood, era, language, energy, and vibe. This powers the AI DJ feature. Classification costs ~$0.03 for 2,000 tracks.

## CLI Options

```
python mart.py --input playlist.txt                     # basic usage
python mart.py --input playlist.txt --output my.json    # custom output file
python mart.py --input playlist.txt --model gpt-4o-mini # different AI model
python mart.py --input playlist.txt --retry-failed      # re-process failed tracks
```

## Troubleshooting

**"No LLM API key found"** — Set an API key. See the table above.

**"Could not extract SoundCloud client_id"** — SoundCloud changed something. Open an issue.

**Tracks showing as "failed"** — Run the same command again. MART retries failed tracks but skips matched ones.

**AI DJ says "classify your library first"** — Go to Settings → Classify Library.

**Playlist script does nothing** — Make sure you're logged into SoundCloud and pasting in the console on soundcloud.com.

## Project Structure

```
mart.py          — CLI entry point + orchestrator
ui.py            — Web UI server (Flask)
sc_search.py     — SoundCloud search API
matcher.py       — LLM track matching
classifier.py    — Batch genre/mood/era classification
importer.py      — Multi-platform import (Spotify, Apple, Yandex, VK)
query_clean.py   — Query cleaning + transliteration
vk_import.py     — VK export parser
static/index.html — Web UI frontend
results.json     — Match results (auto-generated)
tags.json        — Track classifications (auto-generated)
```

## License

MIT

---

# MART — Инструмент восстановления музыкальной библиотеки

Восстановите свою музыкальную библиотеку на SoundCloud. Импорт из VK, Spotify, Apple Music, Яндекс Музыки — MART находит каждый трек на SoundCloud с помощью ИИ.

2 000 треков — ~1 час, стоимость ~$0.02 за API.

## Возможности

- **Умный подбор** — ИИ выбирает лучшую оригинальную нецензурированную версию, отсеивает slowed/nightcore/каверы
- **Мультиплатформенный импорт** — VK, Spotify, Apple Music, Яндекс Музыка или любой CSV/JSON/TXT
- **AI DJ** — чат с ИИ для составления плейлистов ("весь русский трэп", "спокойное из 90-х")
- **Плейлисты SoundCloud** — создание плейлистов прямо в вашем аккаунте
- **Возобновление** — можно остановить в любой момент и продолжить позже
- **Веб-интерфейс** — всё управление через браузер

## Быстрый старт

### 1. Установите Python

Скачайте с https://www.python.org/downloads/ — **поставьте галочку "Add Python to PATH"**.

### 2. Установите зависимости

```
pip install -r requirements.txt
```

### 3. Получите API-ключ

| Провайдер | Получить ключ | Переменная окружения | Стоимость |
|-----------|--------------|----------------------|-----------|
| **Google Gemini (бесплатно)** | https://aistudio.google.com/apikeys | `GEMINI_API_KEY` | **Бесплатно** |
| OpenRouter | https://openrouter.ai/keys | `OPENROUTER_API_KEY` | ~$0.02 |
| OpenAI | https://platform.openai.com/api-keys | `OPENAI_API_KEY` | ~$0.10 |
| Anthropic | https://console.anthropic.com/ | `ANTHROPIC_API_KEY` | ~$0.05 |

**Рекомендуется: Google Gemini** — полностью бесплатно, без кредитной карты. Перейдите по ссылке, нажмите "Create API Key", готово.

### 4. Экспортируйте музыку

**VK Музыка** — используйте расширение "VK Music Export", чтобы получить `.txt` файл:
```
1. Исполнитель - Название
2. Другой Исполнитель - Другое Название
```

**Spotify** — экспорт через https://exportify.net/ в CSV или JSON через Spotify API.

**Apple Music** — экспорт библиотеки в CSV (Файл → Медиатека → Экспорт).

**Яндекс Музыка** — экспорт через сторонние инструменты в JSON или CSV.

Любой CSV с колонками `artist` и `title` подойдёт. JSON-массивы с полями `artist`/`title` тоже работают.

### 5. Запуск — Веб-интерфейс (рекомендуется)

```
python ui.py
```

Откройте http://localhost:8000 в браузере. Веб-интерфейс позволяет:

1. **Подключиться к SoundCloud** — вставьте OAuth-токен (инструкция в приложении)
2. **Указать API-ключ** — введите ключ в Настройках
3. **Импортировать библиотеку** — загрузите файл (формат определяется автоматически)
4. **Сканировать треки** — нажмите Rescan для поиска на SoundCloud
5. **Просмотреть результаты** — найденные/ненайденные треки, предпрослушивание
6. **AI DJ** — откройте вкладку DJ, пишите запросы для составления плейлистов
7. **Создать плейлисты** — генерирует скрипт для консоли браузера SoundCloud

### 5 (альт). Запуск — Командная строка

```
set OPENROUTER_API_KEY=sk-or-ваш-ключ
python mart.py --input playlist.txt
```

Или перетащите файл на `run.bat`.

## Руководство по веб-интерфейсу

### Подключение к SoundCloud

1. Откройте https://soundcloud.com и войдите в аккаунт
2. Откройте DevTools (F12) → Console
3. Введите `document.cookie` и нажмите Enter
4. Скопируйте результат — вставьте в поле **SC Cookies** в настройках MART
5. Для OAuth-токена: DevTools → Application → Cookies → найдите `oauth_token`

### Импорт треков

Нажмите **Import Library** и загрузите файл. MART автоматически определяет:
- **Платформу** — Spotify, Apple Music, Яндекс Музыка, VK или другое
- **Формат** — CSV, JSON или TXT
- **Дубликаты** — пропускает треки, которые уже есть в библиотеке

### AI DJ

Перейдите на вкладку **DJ** и пишите запросы:
- "дай 20 русских рэп-треков"
- "спокойный lo-fi для учёбы"
- "агрессивный метал 2010-х"
- "все треки Кино"
- "летний пати-микс"

ИИ ищет по вашей классифицированной библиотеке и возвращает подборку. Нажмите **Create Playlist** для экспорта в SoundCloud.

### Создание плейлистов в SoundCloud

MART генерирует JavaScript-скрипт для вставки в консоль SoundCloud. Это необходимо из-за антибот-защиты SoundCloud.

1. Нажмите **Create Playlists** (или создайте из AI DJ)
2. Скопируйте сгенерированный скрипт
3. Откройте https://soundcloud.com, откройте консоль DevTools (F12)
4. Вставьте скрипт и нажмите Enter
5. Дождитесь завершения — плейлисты появятся в вашей библиотеке SC

### Классификация библиотеки

В Настройках нажмите **Classify Library**, чтобы отметить все треки жанрами, настроением, эпохой, языком, энергией и вайбом. Это нужно для AI DJ. Классификация стоит ~$0.03 за 2 000 треков.

## Решение проблем

**"No LLM API key found"** — Укажите API-ключ (см. таблицу выше).

**"Could not extract SoundCloud client_id"** — SoundCloud что-то изменил. Создайте issue.

**Треки со статусом "failed"** — Запустите снова. MART повторит неудачные, пропустит найденные.

**AI DJ говорит "classify your library first"** — Настройки → Classify Library.

**Скрипт плейлиста не работает** — Убедитесь, что вы залогинены на SoundCloud и вставляете скрипт в консоль на soundcloud.com.

## Лицензия

MIT
