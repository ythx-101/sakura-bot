# 🌸 Sakura Bot

A self-hosted Telegram bot for JAV (Japanese adult video) metadata lookup by ID/actress, magnet search, one-click PikPak saving, and Gemini Embedding semantic search over your own collection.
自部署 Telegram 机器人：番号查询、女优搜索、磁力搜索、一键存 PikPak 网盘，并用 Gemini Embedding 对个人片库做封面/片段语义搜索与推荐。

> **18+ / for personal self-hosted use only.** This project only retrieves metadata and links; it does not host any content.

**Fork of [akynazh/tg-search-bot](https://github.com/akynazh/tg-search-bot)**, built on top of [akynazh/jvav](https://github.com/akynazh/jvav). Huge thanks to [@akynazh](https://github.com/akynazh) for the original work 🙏

## Who is it for / 适用场景

- Individuals who run a bot on their own VPS or server (Python + Redis) / 在自己服务器上自部署的个人用户
- People who already use PikPak (optionally mounted with rclone) and want to save magnets from Telegram / 已经在用 PikPak + rclone 的人
- People who want to find items in their own saved collection by describing them in text / 想用文字描述找回自己收藏的人
- Anyone who wants 番号 (ID) lookup and 磁力 (magnet) search from a Telegram chat / 想在 Telegram 里查番号、搜磁力的人

## Features

- **ID / actress lookup** — search by 番号 or actress name; plain text messages work too
- **Magnet search** — send a keyword to search magnets
- **One-click PikPak save** — save magnets to PikPak; triggers immediate scrape + cover download + Gemini embedding + notification
- **Auto Watcher** — background service monitors PikPak mount dirs as a fallback and filters junk/ad files
- **AI semantic search (Gemini Embedding 2)** — text search over your cover index (`/clips`), personalized recommendations (`/recommend`), weekly highlights poster (`/weekly`); standalone video clip indexing/search scripts (`jav_video_embed.py`, `jav_clip_search.py`)
- **DMM compatibility patch** — handles DMM's layout redesign (grid index change, URL parameter migration)
- **JavLibrary fallback** — Cloudflare-blocked JavLibrary replaced with JavBus/DMM for `/nice`, `/new`
- **Live rankings** (`/rank`) — actress popularity from JavBus
- **Auto-update script** — `auto_update.sh` checks for jvav updates
- **Removed dead sources** — Avgle (shutdown) buttons cleaned up

## Quick Start

**Requirements:** Python 3.10+, Redis, ffmpeg (only for the AI video features), optional rclone.

```bash
git clone https://github.com/ythx-101/sakura-bot.git
cd sakura-bot
pip install -r requirements.txt

# Two config copies (same format, contents can be identical):
mkdir -p ~/.tg_search_bot
cp config.yaml.example ~/.tg_search_bot/config.yaml   # read by bot.py
cp config.yaml.example jav_config.yaml                # read by the AI / watcher modules
# Edit both with your credentials
```

Required values:
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Telegram API ID & Hash (from [my.telegram.org](https://my.telegram.org))
- Your Telegram Chat ID (the bot only responds to this chat)
- Redis host / port / password
- (Optional) Gemini API Key for AI features
- (Optional) `use_proxy: 1` + `proxy_addr` if JavBus/DMM are not reachable from your region

**Redis:** `apt install redis-server && systemctl enable --now redis-server`. With docker-compose, Redis uses the password `REDIS_PASSWORD` (default `changeme_please`); `redis_password` in your config must match it.

**First start:** the bot logs in to Telegram with a Pyrogram *user* session (it calls `send_msg_to_pikpak("AUTH")` in `main()`) and stores a session file in `~/.tg_search_bot/`. This session is used to forward magnets to the PikPak bot, so expect a login prompt on first run.

```bash
python3 bot.py
# or
docker-compose up -d
```

The compose Python image does not include ffmpeg; run the bot on the host if you need the AI video features. Because the first start needs an interactive Pyrogram login, run `python3 bot.py` once on the host to create the session file in `~/.tg_search_bot/` (mounted into the container) before using `docker-compose up -d`.

**PikPak watcher (optional):** mount PikPak dirs via rclone, then `python3 jav_watcher.py`.

**Example deployment scripts:** `auto_update.sh` restarts a systemd service named `tg-search-bot` and writes to `/var/log/`; `jav_watcher.py` mentions `/opt/tg-search-bot` in its docstring. These are the author's example setup — adapt them to your environment.

## Commands

| Command | Description |
|---------|-------------|
| `/help` (`/start`) | Show command help |
| `/id <番号>` | Search by ID (e.g., SONE-758) |
| `/star <name>` | Search actress |
| `/stars` | View saved actresses |
| `/ids` | View saved IDs |
| `/del <番号>` | Clear cached data, cover index and saved record for an ID |
| `/nice` | Random high-rated title |
| `/new` | Random latest release |
| `/rank` | Live actress popularity ranking |
| `/clips <query>` | Semantic text search over the cover index |
| `/weekly` | Weekly highlights from saved collection |
| `/recommend` | Personalized recommendations |
| `/record` | Export collection records |

You can also just send a message: an ID like `abc-123` runs the ID lookup; any other text runs a magnet search.

## Usage examples

```text
/id SONE-758        → metadata for that ID
/star <name>        → search an actress
/clips <描述>        → semantic search over the cover index of saved titles
abc-123             → detected as an ID, same as /id
some keyword        → magnet search
```

## Data & paths

| Path | Purpose |
|------|---------|
| `~/.tg_search_bot/` | config.yaml, log.txt, record.json, Pyrogram session, Redis data (docker-compose) |
| `jav_config.yaml` (repo dir) | config for AI / watcher modules |
| `~/.openclaw/skills/jav-skill/cover_index`, `video_index`, `weekly_reels` | cover embeddings, clip index, weekly reels |
| `~/.jav_user_history.jsonl` | search history for recommendations |
| `~/.jav_watcher_state.json` | watcher state |
| `../config.yaml` (parent of the repo dir) | read by the standalone scripts `jav_clip_search.py`, `jav_weekly_reel.py` (and as a fallback by `jav_video_embed.py`); `GEMINI_API_KEY` / `GOOGLE_API_KEY` env vars also work for the key |

Back these up if you want to keep your index and history.

## FAQ

**How do I search JAV by ID in Telegram? / 怎么在 Telegram 里查番号？**
Send `/id SONE-758`, or just send the ID text. 发送 `/id 番号` 或直接发番号即可。

**Does it need a Gemini API key? / 必须要 Gemini Key 吗？**
No — only the AI features (cover/clip search, recommendations, weekly) need it. 不需要，仅 AI 功能需要。

**Why does the bot fail to start with KeyError / FileNotFoundError? / 启动报错找不到配置？**
`bot.py` reads `~/.tg_search_bot/config.yaml` and needs the `redis_host`, `redis_port`, `redis_password` keys; AI modules read `jav_config.yaml`. Create both. 请创建两份配置并补全 redis 字段。

**How does PikPak saving work? / 存 PikPak 是怎么实现的？**
Via a Pyrogram user session that sends the magnet to the PikPak bot; `tg_api_id`/`tg_api_hash` are required. 通过 Pyrogram 用户会话把磁力发给 PikPak bot。

**Why is JavLibrary not used for /nice /new? / 为什么不用 JavLibrary？**
It is blocked by Cloudflare; JavBus/DMM are used instead. 被 Cloudflare 拦截，改用 JavBus/DMM。

**Do I need a proxy? / 需要代理吗？**
Only if JavBus/DMM are restricted in your region; set `use_proxy: 1` and `proxy_addr`. 访问受限时才需要。

**Is `enable_nsfw` a content filter? / `enable_nsfw` 是什么？**
When set to 0, `/nice` and `/new` are disabled and nsfw-category magnet results are filtered. 为 0 时关闭 /nice /new 并过滤磁力结果中的 nsfw 分类。

## Sakura Bot vs upstream tg-search-bot

| | tg-search-bot (upstream) | Sakura Bot |
|---|---|---|
| AI semantic search (Gemini Embedding) | — | cover, clip, recommend, weekly modules |
| PikPak integration | — | instant processing + file watcher |
| DMM layout compatibility | — | `dmm_patch.py` |
| JavLibrary | used | replaced by JavBus/DMM fallbacks |
| Avgle | integrated | removed (service shutdown) |

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
