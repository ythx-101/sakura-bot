#!/usr/bin/env python3
"""
jav_watcher.py — PikPak 目录监视 + 元数据整理流水线

检测到新视频后按顺序执行:
  ① 刮削元数据  (jvav → 标题/女优/封面/标签)
  ② 重命名整理  (番号 女优 标题.ext + NFO + poster.jpg，直接操作挂载目录)
  ③ 封面 Embed 索引 (poster.jpg → Gemini Embedding 2 → cover_index JSONL)
  ④ Bot 通知    (含封面图)

不下载视频，不去广告，纯元数据+封面操作。

运行方式:
  python3 /opt/tg-search-bot/jav_watcher.py
  systemctl start jav-watcher
"""

import base64
import json
import os
import re
import shutil
import sys
import tempfile
import time
import logging
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# ── 配置 ──────────────────────────────────────────────────────────────────────
WATCH_DIRS = [
    "/root/pikpak-mypack",
    "/root/pikpak-movies",
    "/root/pikpak-series",
]
POLL_INTERVAL      = 30        # 秒：轮询间隔
STABLE_CHECK_SECS  = 2         # 秒：PikPak 秒存无需长等
STABLE_CHECK_TIMES = 1         # 次：1 次即可（云盘文件瞬间完成）
VIDEO_EXTS         = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".m2ts", ".wmv", ".rmvb"}
JUNK_NAMES         = {"manko.fun", "hhd800.com", "1pon.tv", "caribbeancom", "thz.la",
                       "nyap2p", "hjd2048", "seselah", "sexinsex"}  # 常见广告/水印文件名
MIN_VIDEO_SIZE     = 50 * 1024 * 1024  # 50MB 以下视频跳过（广告）
LOG_FILE           = "/var/log/jav-watcher.log"
STATE_FILE         = os.path.expanduser("~/.jav_watcher_state.json")
NOTIFY_CHAT_ID     = "YOUR_CHAT_ID"

# 封面索引目录
COVER_INDEX_DIR  = os.path.expanduser("~/.openclaw/skills/jav-skill/cover_index")
GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent"

# ── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [watcher] %(message)s",
    handlers=[
        logging.StreamHandler(),
    ]
)
LOG = logging.getLogger("jav_watcher")


# ─────────────────────────────────────────────────────────────────────────────
# 删除同步：文件消失时清理 Redis 缓存 + 封面索引
# ─────────────────────────────────────────────────────────────────────────────

def cleanup_deleted(disappeared_paths: set):
    """文件从 PikPak 删除后，清理对应的 Redis 缓存和封面索引。"""
    for fpath in disappeared_paths:
        fname = os.path.basename(fpath)
        jav_id = extract_jav_id(fname)
        if not jav_id:
            continue

        # 清 Redis 缓存
        try:
            import redis
            r = redis.Redis(host="127.0.0.1", port=6379,
                            password=os.environ.get("REDIS_PASSWORD", ""))
            jid_lower = jav_id.lower()
            deleted_keys = []
            for prefix in ["v-", "sample-", "magnet-", "bt-", "comment-"]:
                key = f"{prefix}{jid_lower}"
                if r.delete(key):
                    deleted_keys.append(key)
            if deleted_keys:
                LOG.info(f"🗑 Redis 已清理: {', '.join(deleted_keys)}")
        except Exception as e:
            LOG.warning(f"Redis 清理失败 {jav_id}: {e}")

        # 清封面索引
        idx_path = os.path.join(COVER_INDEX_DIR, f"{jav_id}.jsonl")
        if os.path.exists(idx_path):
            os.remove(idx_path)
            LOG.info(f"🗑 封面索引已删除: {jav_id}")


# ─────────────────────────────────────────────────────────────────────────────
# 配置 / TG / 状态
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        import yaml
        with open(os.path.join(SCRIPT_DIR, "jav_config.yaml")) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        LOG.warning(f"无法读取配置: {e}")
        return {}


def send_tg_msg(token: str, chat_id: str, text: str):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": text,
                                 "parse_mode": "HTML"}, timeout=15)
    except Exception as e:
        LOG.warning(f"TG 消息异常: {e}")


def send_tg_photo(token: str, chat_id: str, photo_path: str, caption: str):
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        with open(photo_path, "rb") as f:
            resp = requests.post(url, data={"chat_id": chat_id,
                                            "caption": caption,
                                            "parse_mode": "HTML"},
                                 files={"photo": f}, timeout=30)
        if not resp.ok:
            LOG.warning(f"TG 图片发送失败: {resp.text[:100]}")
            send_tg_msg(token, chat_id, caption)
    except Exception as e:
        LOG.warning(f"TG 图片异常: {e}")
        send_tg_msg(token, chat_id, caption)


def load_state() -> set:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return set(json.load(f).get("known", []))
        except Exception:
            pass
    return set()


def save_state(known: set):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"known": sorted(known)}, f)
    except Exception as e:
        LOG.warning(f"状态保存失败: {e}")


def scan_dirs() -> dict:
    result = {}
    for root_dir in WATCH_DIRS:
        if not os.path.isdir(root_dir):
            continue
        try:
            for dirpath, _dirs, files in os.walk(root_dir):
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in VIDEO_EXTS:
                        continue
                    # 跳过无番号的文件（广告/水印文件通常没有合法番号）
                    if not extract_jav_id(fname):
                        continue
                    # 跳过广告/水印小视频
                    stem = os.path.splitext(fname)[0].lower()
                    if any(j in stem for j in JUNK_NAMES):
                        continue
                    fpath_full = os.path.join(dirpath, fname)
                    try:
                        if os.path.getsize(fpath_full) < MIN_VIDEO_SIZE:
                            continue
                    except OSError:
                        continue
                    result[fpath_full] = fname
        except Exception as e:
            LOG.warning(f"扫描 {root_dir} 出错: {e}")
    return result


def wait_for_stable(fpath: str) -> bool:
    LOG.info(f"等待文件稳定: {os.path.basename(fpath)}")
    prev_size, stable_count = -1, 0
    for _ in range(60):
        try:
            size = os.path.getsize(fpath)
        except FileNotFoundError:
            return False
        if size == prev_size:
            stable_count += 1
            if stable_count >= STABLE_CHECK_TIMES:
                LOG.info(f"文件稳定: {os.path.basename(fpath)} ({size // 1024 // 1024} MB)")
                return True
        else:
            stable_count = 0
        prev_size = size
        time.sleep(STABLE_CHECK_SECS)
    LOG.warning(f"等待稳定超时: {os.path.basename(fpath)}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step ①  刮削元数据
# ─────────────────────────────────────────────────────────────────────────────

def extract_jav_id(filename: str) -> str | None:
    """从文件名提取番号（如 SONE-758、MFYD-090），保留至少3位数字。"""
    m = re.search(r'\b([A-Za-z]{2,6})-?(\d{2,5})\b', filename)
    if m:
        num = m.group(2)
        # 只在数字超过3位时剥离前导零（DKRA-01101→1101），3位及以下保留（090→090）
        if len(num) > 3:
            num = num.lstrip('0') or '0'
        return f"{m.group(1).upper()}-{num}"
    return None


def step_scrape_meta(jav_id: str) -> dict:
    """通过 jvav JavBusUtil 刮削元数据。"""
    try:
        import jvav
        import yaml
        cfg_path = os.path.join(SCRIPT_DIR, "jav_config.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        proxy = cfg.get("proxy_addr", "") if cfg.get("use_proxy") else ""

        bus = jvav.JavBusUtil(proxy)
        status, data = bus.get_av_by_id(jav_id, False, False)
        if status != 200 or not data:
            LOG.warning(f"jvav 刮削失败: status={status}")
            return {}

        stars = [s["name"] for s in data.get("stars", [])]
        return {
            "title": data.get("title", ""),
            "stars": stars,
            "img":   data.get("img", ""),
            "tags":  data.get("tags", []),
            "date":  data.get("date", ""),
        }
    except Exception as e:
        LOG.warning(f"刮削元数据出错: {e}")
        return {}


def download_cover(url: str, save_path: str) -> bool:
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.javbus.com/"
        })
        r.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(r.content)
        LOG.info(f"封面已下载: {save_path}")
        return True
    except Exception as e:
        LOG.warning(f"封面下载失败: {e}")
        return False


def write_nfo(jav_id: str, meta: dict, nfo_path: str):
    title = meta.get("title", jav_id)
    date  = meta.get("date", "")
    year  = date[:4] if date else ""
    tags  = meta.get("tags", [])
    stars = meta.get("stars", [])

    tags_xml   = "\n  ".join(f"<tag>{t}</tag>" for t in tags)
    actors_xml = "\n  ".join(
        f"<actor>\n    <name>{s}</name>\n    <type>Actress</type>\n  </actor>"
        for s in stars)

    content = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<movie>
  <title>{title}</title>
  <originaltitle>{jav_id}</originaltitle>
  <sorttitle>{jav_id}</sorttitle>
  <id>{jav_id}</id>
  <plot>{title}</plot>
  <releasedate>{date}</releasedate>
  <year>{year}</year>
  {tags_xml}
  {actors_xml}
</movie>
"""
    with open(nfo_path, "w", encoding="utf-8") as f:
        f.write(content)
    LOG.info(f"NFO 已写入: {nfo_path}")


def sanitize_filename(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '', s).strip()


def build_new_filename(jav_id: str, meta: dict, ext: str) -> str:
    stars_str = " ".join(meta.get("stars", []))
    title     = meta.get("title", "")[:60]
    parts = [p for p in [jav_id, stars_str, title] if p]
    name = " ".join(parts)
    name = sanitize_filename(name)
    return name + ext


# ─────────────────────────────────────────────────────────────────────────────
# Step ③  Embed 封面索引
# ─────────────────────────────────────────────────────────────────────────────

def embed_and_index_cover(video_name: str, jav_id: str, meta: dict,
                          poster_path: str, api_key: str) -> bool:
    """
    把 poster.jpg embed 成 3072 维向量，保存到 cover_index JSONL。
    每个番号一条记录（单行 JSONL，覆盖写入）。
    """
    try:
        with open(poster_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        payload = {
            "model": "models/gemini-embedding-2-preview",
            "content": {"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}]},
            "outputDimensionality": 3072,
        }
        r = requests.post(GEMINI_EMBED_URL, json=payload, timeout=30,
                      headers={"x-goog-api-key": api_key})
        r.raise_for_status()
        embedding = r.json()["embedding"]["values"]

        os.makedirs(COVER_INDEX_DIR, exist_ok=True)
        index_path = os.path.join(COVER_INDEX_DIR, f"{jav_id}.jsonl")
        entry = {
            "video_name": video_name,
            "jav_id":     jav_id,
            "title":      meta.get("title", ""),
            "stars":      meta.get("stars", []),
            "tags":       meta.get("tags", []),
            "embedding":  embedding,
        }
        with open(index_path, "w") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        LOG.info(f"封面索引已保存: {index_path}")
        return True
    except Exception as e:
        LOG.error(f"封面 embed 失败: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 完整流水线
# ─────────────────────────────────────────────────────────────────────────────

def process_video(fpath: str, fname: str, bot_token: str, chat_id: str):
    """
    元数据整理流水线（纯元数据+封面，不下载视频，不去广告）。
    /tmp 只用于存封面临时文件、NFO 临时文件。
    """
    LOG.info(f"{'='*50}")
    LOG.info(f"▶ 开始处理: {fname}")
    LOG.info(f"{'='*50}")

    video_dir   = os.path.dirname(fpath)
    tmp_dir     = tempfile.mkdtemp(prefix="jav_proc_")
    final_path  = fpath
    jav_id      = None
    meta        = {}
    poster_path = None

    try:
        # ── Step ①: 刮削元数据 ──────────────────────────────────────────────
        try:
            jav_id = extract_jav_id(fname)
            if jav_id:
                LOG.info(f"[①刮削] 番号: {jav_id}")
                meta = step_scrape_meta(jav_id)
                if meta:
                    poster_tmp = os.path.join(tmp_dir, "poster.jpg")
                    if meta.get("img") and download_cover(meta["img"], poster_tmp):
                        poster_path = poster_tmp
                    nfo_tmp = os.path.join(tmp_dir, f"{jav_id}.nfo")
                    write_nfo(jav_id, meta, nfo_tmp)
                    LOG.info(f"[①刮削] 完成: {meta.get('title','')[:40]}")
                else:
                    LOG.warning(f"[①刮削] 无结果，跳过")
            else:
                LOG.warning(f"[①刮削] 无法提取番号，跳过")
        except Exception as e:
            LOG.warning(f"[①刮削] 出错（已跳过）: {e}")

        # ── Step ②: 重命名 + 整理 ──────────────────────────────────────────
        try:
            ext = os.path.splitext(fname)[1]
            if jav_id and meta:
                new_fname = build_new_filename(jav_id, meta, ext)
                new_path  = os.path.join(video_dir, new_fname)
                base_name = os.path.splitext(new_fname)[0]

                if new_fname != fname:
                    LOG.info(f"[②整理] 重命名: {fname} → {new_fname}")
                    os.rename(fpath, new_path)

                final_path = new_path

                # 封面写到挂载目录
                if poster_path and os.path.exists(poster_path):
                    dest_poster = os.path.join(video_dir, f"{base_name}-poster.jpg")
                    shutil.copy2(poster_path, dest_poster)
                    poster_path = dest_poster

                # NFO 写到挂载目录
                nfo_tmp = os.path.join(tmp_dir, f"{jav_id}.nfo")
                if os.path.exists(nfo_tmp):
                    dest_nfo = os.path.join(video_dir, f"{base_name}.nfo")
                    shutil.copy2(nfo_tmp, dest_nfo)

                # 清理同目录下的广告/垃圾文件
                for item in os.listdir(video_dir):
                    item_path = os.path.join(video_dir, item)
                    if item_path == final_path:
                        continue
                    # 保留我们生成的 poster 和 nfo
                    if item.endswith(("-poster.jpg", ".nfo")):
                        continue
                    item_stem = os.path.splitext(item)[0].lower()
                    item_ext  = os.path.splitext(item)[1].lower()
                    is_junk = any(j in item_stem for j in JUNK_NAMES)
                    is_small_video = (item_ext in VIDEO_EXTS and
                                     os.path.isfile(item_path) and
                                     os.path.getsize(item_path) < MIN_VIDEO_SIZE)
                    if is_junk or is_small_video:
                        try:
                            os.remove(item_path)
                            LOG.info(f"[②整理] 删除垃圾文件: {item}")
                        except Exception as e2:
                            LOG.warning(f"[②整理] 删除失败: {item}: {e2}")

                # 重命名父文件夹
                parent_dir = video_dir
                # 只重命名直接包含视频的子目录，不动 WATCH_DIRS 根目录
                if parent_dir not in WATCH_DIRS:
                    new_dir_name = sanitize_filename(f"{jav_id} {' '.join(meta.get('stars', []))} {meta.get('title', '')[:40]}")
                    new_dir_path = os.path.join(os.path.dirname(parent_dir), new_dir_name)
                    if new_dir_path != parent_dir and not os.path.exists(new_dir_path):
                        try:
                            os.rename(parent_dir, new_dir_path)
                            LOG.info(f"[②整理] 文件夹重命名: {os.path.basename(parent_dir)} → {new_dir_name}")
                            # 更新路径引用
                            video_dir  = new_dir_path
                            final_path = os.path.join(new_dir_path, os.path.basename(final_path))
                            if poster_path:
                                poster_path = os.path.join(new_dir_path, os.path.basename(poster_path))
                        except Exception as e2:
                            LOG.warning(f"[②整理] 文件夹重命名失败: {e2}")

                LOG.info(f"[②整理] 完成: {new_fname}")
        except Exception as e:
            LOG.warning(f"[②整理] 出错（已跳过）: {e}")

        # ── Step ③: 封面 Embed 索引 ─────────────────────────────────────────
        try:
            if jav_id and meta and poster_path and os.path.exists(poster_path):
                import yaml
                with open(os.path.join(SCRIPT_DIR, "jav_config.yaml")) as f:
                    cfg = yaml.safe_load(f) or {}
                api_key = cfg.get("gemini_api_key", "") or os.environ.get("GEMINI_API_KEY", "")
                video_name = os.path.splitext(os.path.basename(final_path))[0]
                ok = embed_and_index_cover(video_name, jav_id, meta, poster_path, api_key)
                if ok:
                    LOG.info(f"[③索引] 封面已嵌入: {jav_id}")
            else:
                LOG.warning(f"[③索引] 跳过（缺少封面或元数据）")
        except Exception as e:
            LOG.warning(f"[③索引] 出错（已跳过）: {e}")

        # ── Step ④: Bot 通知 ────────────────────────────────────────────────
        try:
            import html as _html
            stars_str = "、".join(meta.get("stars", [])) if meta else ""
            tags_str  = " | ".join(meta.get("tags", [])[:8]) if meta else ""

            caption = (
                f"✅ <b>新视频整理完成</b>\n"
                f"📌 番号：<code>{_html.escape(jav_id or '未识别')}</code>\n"
            )
            if stars_str:
                caption += f"🎭 女优：{_html.escape(stars_str)}\n"
            if meta.get("title"):
                caption += f"📝 标题：{_html.escape(meta['title'][:60])}\n"
            if tags_str:
                caption += f"🏷 标签：{_html.escape(tags_str)}\n"
            caption += f"💡 /clips &lt;描述&gt; 搜封面索引"

            if poster_path and os.path.exists(poster_path):
                send_tg_photo(bot_token, chat_id, poster_path, caption)
            else:
                send_tg_msg(bot_token, chat_id, caption)
        except Exception as e:
            LOG.warning(f"[④通知] 出错（已跳过）: {e}")

    finally:
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass

    LOG.info(f"▶ 处理完成: {os.path.basename(final_path)}")
    return final_path if meta else None


# ─────────────────────────────────────────────────────────────────────────────
# 主循环
# ─────────────────────────────────────────────────────────────────────────────

def main():
    cfg       = load_config()
    bot_token = cfg.get("tg_bot_token", "")
    chat_id   = str(cfg.get("tg_chat_id", NOTIFY_CHAT_ID))

    if not bot_token:
        LOG.error("未配置 tg_bot_token，请检查 jav_config.yaml")
        sys.exit(1)

    LOG.info("=" * 60)
    LOG.info("jav_watcher 启动（纯元数据+封面版）")
    LOG.info(f"监视目录: {WATCH_DIRS}")
    LOG.info(f"轮询间隔: {POLL_INTERVAL}s")
    LOG.info("=" * 60)

    known = load_state()
    fail_count = {}          # fpath → 已失败次数
    MAX_RETRIES = 3

    if not known:
        current = scan_dirs()
        known   = set(current.keys())
        save_state(known)
        LOG.info(f"初始化完成，已知 {len(known)} 个视频文件")

    while True:
        try:
            current       = scan_dirs()
            current_paths = set(current.keys())
            new_paths     = current_paths - known

            for fpath in sorted(new_paths):
                # 重试次数耗尽，放弃
                if fail_count.get(fpath, 0) >= MAX_RETRIES:
                    known.add(fpath)
                    save_state(known)
                    LOG.warning(f"重试 {MAX_RETRIES} 次仍失败，跳过: {os.path.basename(fpath)}")
                    continue

                fname = current[fpath]
                # 如果即时处理已建索引，跳过
                _jid = extract_jav_id(fname)
                if _jid:
                    _idx = os.path.expanduser(f"~/.openclaw/skills/jav-skill/cover_index/{_jid}.jsonl")
                    if os.path.exists(_idx):
                        LOG.info(f"⏭ 跳过（已由即时处理完成）: {fname}")
                        known.add(fpath)
                        save_state(known)
                        continue

                is_retry = fpath in fail_count
                if not is_retry:
                    LOG.info(f"🆕 新文件: {fname}")
                    send_tg_msg(bot_token, chat_id,
                                f"🆕 检测到新视频: <code>{fname}</code>\n正在处理元数据...")
                try:
                    if not is_retry:
                        stable = wait_for_stable(fpath)
                        if not stable:
                            LOG.warning(f"文件路径变化（PikPak可能已重命名）: {fname}")
                    result_path = process_video(fpath, fname, bot_token, chat_id)
                    if result_path:
                        known.add(fpath)
                        # 如果文件被重命名，把新路径也加入 known，避免下轮当新文件
                        if result_path != fpath:
                            known.add(result_path)
                        save_state(known)
                        fail_count.pop(fpath, None)
                    else:
                        fail_count[fpath] = fail_count.get(fpath, 0) + 1
                        LOG.warning(f"处理不完整，第 {fail_count[fpath]}/{MAX_RETRIES} 次: {fname}")
                except Exception as e:
                    fail_count[fpath] = fail_count.get(fpath, 0) + 1
                    LOG.error(f"处理出错 {fname}: {e}")
                    if fail_count[fpath] >= MAX_RETRIES:
                        send_tg_msg(bot_token, chat_id,
                                    f"⚠ 处理失败（已放弃）: <code>{fname}</code>\n{e}")

            # 检测已删除的文件，清理缓存
            disappeared = known - current_paths
            if disappeared:
                # 排除因重命名导致的路径变化：如果番号在当前文件中仍存在，不算删除
                current_jav_ids = set()
                for p in current_paths:
                    _j = extract_jav_id(os.path.basename(p))
                    if _j:
                        current_jav_ids.add(_j)
                truly_deleted = set()
                for p in disappeared:
                    _j = extract_jav_id(os.path.basename(p))
                    if _j and _j not in current_jav_ids:
                        truly_deleted.add(p)
                if truly_deleted:
                    cleanup_deleted(truly_deleted)

            known = (known & current_paths) | {p for p in new_paths if p in known}
            save_state(known)

        except Exception as e:
            LOG.error(f"主循环出错: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
