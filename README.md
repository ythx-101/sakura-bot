# 🌸 Sakura Bot

A Telegram bot for searching, browsing, and managing JAV resources — with AI-powered semantic search via Gemini Embedding.

**Fork of [akynazh/tg-search-bot](https://github.com/akynazh/tg-search-bot)**, built on top of [akynazh/jvav](https://github.com/akynazh/jvav). Huge thanks to [@akynazh](https://github.com/akynazh) for the original work 🙏

## What's New (vs upstream)

This fork adds several major features while fixing compatibility with recent DMM/JavLibrary changes:

### 🤖 AI Features (Gemini Embedding 2)
- **Cover Search** — Embed cover images as vectors, search by text description
- **Image Search** — Send a photo, find matching titles via cross-modal embedding
- **Clip Search** (`/clips`) — Semantic video segment retrieval
- **Smart Recommendations** (`/recommend`) — Personalized suggestions based on search history
- **Weekly Reel** (`/weekly`) — Auto-generated highlight compilation with transitions & BGM

### 📁 PikPak Integration
- **One-click Save** — Save magnet links to PikPak cloud storage directly from bot
- **Auto Watcher** — Monitors PikPak mount dirs, auto-scrapes metadata, downloads covers, generates NFO, sends notifications
- **Full Pipeline** (`/auto`) —番号 → magnet → PikPak → metadata → notify

### 🔧 Fixes & Improvements
- **DMM Compatibility Patch** — Handles DMM's layout redesign (grid index change, URL parameter migration)
- **JavLibrary Fallback** — Cloudflare-blocked JavLibrary replaced with JavBus/DMM alternatives for `/nice`, `/new`
- **Live Rankings** (`/rank`) — Real-time actress popularity from JavBus (was hardcoded list)
- **Auto-update** — Daily cron checks for jvav library updates
- **Removed dead sources** — Avgle (shutdown) buttons cleaned up

## Commands

| Command | Description |
|---------|-------------|
| `/id <番号>` | Search by ID (e.g., SONE-758) |
| `/star <name>` | Search actress |
| `/nice` | Random high-rated title |
| `/new` | Random latest release |
| `/rank` | Live actress popularity ranking |
| `/clips <query>` | Semantic clip search |
| `/auto <番号>` | Full auto pipeline |
| `/recommend` | Personalized recommendations |
| `/imgsearch` | Search by image (send photo) |
| `/record` | Export collection records |

## Setup

### 1. Clone & Install

```bash
git clone https://github.com/ythx-101/sakura-bot.git
cd sakura-bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp config.yaml.example jav_config.yaml
# Edit jav_config.yaml with your credentials
```

Required:
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Telegram API ID & Hash (from [my.telegram.org](https://my.telegram.org))
- Your Telegram Chat ID
- (Optional) Gemini API Key for AI features
- (Optional) Proxy for JavBus/DMM access

### 3. Redis

```bash
apt install redis-server
systemctl enable --now redis-server
```

### 4. Run

```bash
python3 bot.py
```

Or with Docker:

```bash
docker-compose up -d
```

### 5. PikPak Watcher (Optional)

Mount PikPak dirs via rclone, then:

```bash
python3 jav_watcher.py
```

## Architecture

```
bot.py                  # Main bot + command routing
├── database.py         # SQLite + Redis caching
├── dmm_patch.py        # DMM layout compatibility fix
├── fallback_sources.py # JavLibrary → JavBus/DMM fallback
├── jav_cover_search.py # Gemini embedding cover search
├── jav_image_search.py # Cross-modal image → title search
├── jav_clip_search.py  # Semantic video clip retrieval
├── jav_recommend.py    # Personalized recommendation engine
├── jav_video_embed.py  # Video frame embedding indexer
├── jav_weekly_reel.py  # Weekly highlight reel generator
├── jav_auto_pipeline.py# Full automation pipeline
├── jav_watcher.py      # PikPak file watcher + auto-processor
└── auto_update.sh      # jvav library auto-updater
```

## Credits

- **[akynazh/tg-search-bot](https://github.com/akynazh/tg-search-bot)** — Original Telegram bot framework
- **[akynazh/jvav](https://github.com/akynazh/jvav)** — Core scraping library
- **[Google Gemini](https://ai.google.dev/)** — Embedding API for semantic search

## Changes from Upstream

This is a derivative work of [tg-search-bot](https://github.com/akynazh/tg-search-bot) (GPLv3).

Major modifications:
1. Added 7 new modules (jav_cover_search, jav_image_search, jav_clip_search, jav_recommend, jav_video_embed, jav_weekly_reel, jav_auto_pipeline)
2. Added PikPak integration and file watcher (jav_watcher.py)
3. Added DMM monkey-patch for layout compatibility (dmm_patch.py)
4. Replaced JavLibrary-dependent features with JavBus/DMM fallbacks (fallback_sources.py)
5. Removed Avgle integration (service shutdown)
6. Added auto-update mechanism for jvav dependency
7. Various bug fixes and UI improvements

## License

This project is licensed under the **GNU General Public License v3.0** — same as the original project.

See [LICENSE](LICENSE) for details.

Copyright (C) 2022-2023 akynazh (original work)
Copyright (C) 2025-2026 ythx-101 (modifications)
