#!/usr/bin/env python3
"""
jav_auto_pipeline.py — PikPak 自动闭环流水线

搜索番号 → 获取最佳磁力链接（优先有字幕）→ 推送到 PikPak
→ 轮询 /mnt/pikpak-mypack/ 等待视频出现 → 自动建索引 → 返回标签

用法（命令行）:
  python3 jav_auto_pipeline.py SONE-758
  python3 jav_auto_pipeline.py SONE-758 --push-only   # 只推磁力，不等待
  python3 jav_auto_pipeline.py --watch /path/to/dir   # 监视目录，新文件自动索引

Bot 集成:
  from jav_auto_pipeline import auto_pipeline
  result = auto_pipeline(jav_id, send_msg_fn)
"""

import json
import os
import subprocess
import sys
import time
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_LOG = os.path.expanduser("~/.jav_pipeline.jsonl")
WATCH_INTERVAL = 30  # 秒

# PikPak 挂载目录（按优先级顺序）
PIKPAK_DIRS = [
    "/mnt/pikpak-mypack",
    "/mnt/pikpak-movies",
    "/mnt/pikpak-series",
]

# 视频文件扩展名
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".m2ts", ".wmv", ".rmvb"}

# 字幕关键词
SUBTITLE_KEYWORDS = ["中字", "字幕", "chinese sub", "ch sub", "繁字", "c字"]

# 等待视频出现超时（秒）
WAIT_TIMEOUT = 600  # 10 分钟
WAIT_POLL = 15      # 轮询间隔


def log_pipeline(jav_id: str, status: str, extra: dict = None):
    entry = {"jav_id": jav_id, "status": status, "ts": time.time()}
    if extra:
        entry.update(extra)
    with open(PIPELINE_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def has_subtitle(name: str) -> bool:
    name_low = name.lower()
    return any(kw in name_low for kw in SUBTITLE_KEYWORDS)


def pick_best_magnet(magnets: list) -> dict:
    """
    从磁力列表中选最佳：
    1. 优先有字幕
    2. 同等条件下取最大尺寸
    """
    if not magnets:
        return None

    def size_mb(size_str: str) -> float:
        try:
            s = size_str.strip().upper()
            if "GB" in s:
                return float(re.sub(r"[^\d.]", "", s)) * 1024
            if "MB" in s:
                return float(re.sub(r"[^\d.]", "", s))
        except Exception:
            pass
        return 0.0

    subtitled = [m for m in magnets if has_subtitle(m.get("name", ""))]
    pool = subtitled if subtitled else magnets
    pool.sort(key=lambda m: size_mb(m.get("size", "0")), reverse=True)
    best = pool[0]
    best["_has_subtitle"] = bool(subtitled)
    return best


def search_magnets(jav_id: str) -> list:
    """
    调用 jvav 库搜索磁力（复用 bot.py 已有逻辑）。
    返回 list of {"name": ..., "magnet": ..., "size": ...}
    """
    try:
        sys.path.insert(0, SCRIPT_DIR)
        import jvav as jv
        import yaml

        cfg_path = os.path.join(SCRIPT_DIR, "jav_config.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        proxy = cfg.get("proxy_addr", "") if cfg.get("use_proxy") else ""

        bus = jv.JavBusUtil(proxy)
        db = jv.JavDbUtil(proxy)
        suke = jv.SukebeiUtil(proxy)

        results = []
        for util in [bus, db, suke]:
            try:
                r = util.get_v_by_id(jav_id)
                if r and r.get("magnets"):
                    results.extend(r["magnets"])
                    if len(results) >= 10:
                        break
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"⚠️ jvav 搜索失败: {e}")
        return []


def push_to_pikpak(magnet: str, jav_id: str, send_msg_fn=None) -> bool:
    """
    推磁力到 PikPak Bot（通过 Telegram 消息）。
    send_msg_fn: callable(msg) 用于 bot 集成；None 时走 CLI 模拟。
    """
    msg = f"[JAV-AUTO] {jav_id}\n{magnet}"
    if send_msg_fn:
        try:
            send_msg_fn(msg)
            return True
        except Exception as e:
            print(f"❌ 推送失败: {e}")
            return False
    else:
        # CLI 模式：打印磁力供手动复制
        print(f"\n📋 磁力链接（请手动发送给 PikPak Bot）:\n{magnet}\n")
        return True


def _jav_id_in_filename(jav_id: str, filename: str) -> bool:
    """判断文件名是否包含该番号（忽略大小写，允许有/无连字符）。"""
    name_up = filename.upper()
    jav_up = jav_id.upper()
    # 直接匹配：SONE-758
    if jav_up in name_up:
        return True
    # 无连字符匹配：SONE758
    jav_no_dash = jav_up.replace("-", "")
    name_no_dash = name_up.replace("-", "")
    return jav_no_dash in name_no_dash


def find_video_in_pikpak(jav_id: str) -> str | None:
    """
    在所有 PikPak 挂载目录中递归搜索匹配番号的视频文件。
    返回第一个匹配的绝对路径，未找到返回 None。
    """
    for root_dir in PIKPAK_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for dirpath, _dirs, files in os.walk(root_dir):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in VIDEO_EXTS and _jav_id_in_filename(jav_id, fname):
                    return os.path.join(dirpath, fname)
    return None


def wait_for_video(jav_id: str, send_msg_fn=None,
                   timeout: int = WAIT_TIMEOUT,
                   poll: int = WAIT_POLL) -> str | None:
    """
    轮询 PikPak 挂载目录，等待匹配番号的视频文件出现。
    返回视频路径，超时返回 None。
    """
    deadline = time.time() + timeout
    elapsed = 0
    while time.time() < deadline:
        path = find_video_in_pikpak(jav_id)
        if path:
            return path
        if send_msg_fn and elapsed % 60 == 0 and elapsed > 0:
            remaining = int(deadline - time.time())
            send_msg_fn(f"⏳ {jav_id}: 等待 PikPak 下载... （剩余 {remaining//60} 分钟）")
        time.sleep(poll)
        elapsed += poll
    return None


def index_video_with_tags(video_path: str, send_msg_fn=None) -> list:
    """
    调用 jav_video_embed.index_video() 建立索引，并返回标签列表。
    """
    try:
        sys.path.insert(0, SCRIPT_DIR)
        import jav_video_embed as embed
        import yaml

        cfg_path = os.path.join(SCRIPT_DIR, "jav_config.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}
        api_key = cfg.get("gemini_api_key", "")

        if send_msg_fn:
            fname = os.path.basename(video_path)
            send_msg_fn(f"📹 开始建立视频索引: {fname}\n（每分钟1帧 + Gemini Embedding，约需数分钟）")

        index_path = embed.index_video(video_path, auto_tag=True)
        tags = embed.compute_tags(index_path, api_key)
        if tags:
            embed.save_tags(os.path.splitext(os.path.basename(video_path))[0], tags, index_path)
        return tags
    except Exception as e:
        print(f"❌ 索引失败: {e}")
        if send_msg_fn:
            send_msg_fn(f"⚠️ 视频索引出错: {e}")
        return []


def watch_directory(watch_dir: str, interval: int = WATCH_INTERVAL):
    """
    监视目录，新增视频文件时自动调用 jav_video_embed.py 索引。
    持续运行直到 Ctrl+C。
    """
    embed_script = os.path.join(SCRIPT_DIR, "jav_video_embed.py")

    print(f"👁️  监视目录: {watch_dir}")
    print(f"   检测间隔: {interval}s | Ctrl+C 停止\n")

    known = set()
    # 初始化已知文件
    for f in os.listdir(watch_dir):
        if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
            known.add(f)

    while True:
        time.sleep(interval)
        try:
            current = set()
            for f in os.listdir(watch_dir):
                if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                    current.add(f)

            new_files = current - known
            for fname in new_files:
                fpath = os.path.join(watch_dir, fname)
                print(f"🆕 检测到新文件: {fname}")
                # 等文件写完（文件大小稳定）
                prev_size = -1
                for _ in range(30):
                    size = os.path.getsize(fpath)
                    if size == prev_size:
                        break
                    prev_size = size
                    time.sleep(5)
                # 自动索引
                print(f"📹 开始自动索引: {fname}")
                ret = subprocess.run(
                    [sys.executable, embed_script, "--video", fpath, "--no-tags"],
                    capture_output=False
                )
                if ret.returncode == 0:
                    print(f"✅ 自动索引完成: {fname}")
                    log_pipeline(fname, "indexed", {"path": fpath})
                else:
                    print(f"❌ 自动索引失败: {fname}")
                known.add(fname)

            known = current
        except KeyboardInterrupt:
            print("\n👋 监视停止")
            break
        except Exception as e:
            print(f"⚠️ 监视错误: {e}")


def auto_pipeline(jav_id: str, send_msg_fn=None, push_only: bool = False) -> dict:
    """
    完整自动流水线（供 bot.py 调用）。
    流程：搜索磁力 → 推送 PikPak → 轮询等待视频 → 建索引 → 返回标签
    返回: {"ok": bool, "jav_id": str, "magnet": str, "has_subtitle": bool,
           "video_path": str, "tags": list, "msg": str}
    """
    jav_id = jav_id.strip().upper()
    log_pipeline(jav_id, "started")

    if send_msg_fn:
        send_msg_fn(f"🔍 /auto {jav_id}: 搜索磁力链接...")

    magnets = search_magnets(jav_id)
    if not magnets:
        msg = f"❌ {jav_id}: 未找到磁力链接"
        log_pipeline(jav_id, "no_magnet")
        if send_msg_fn:
            send_msg_fn(msg)
        return {"ok": False, "jav_id": jav_id, "msg": msg, "tags": []}

    best = pick_best_magnet(magnets)
    if not best or not best.get("magnet"):
        msg = f"❌ {jav_id}: 磁力链接为空"
        log_pipeline(jav_id, "empty_magnet")
        if send_msg_fn:
            send_msg_fn(msg)
        return {"ok": False, "jav_id": jav_id, "msg": msg, "tags": []}

    name = best.get("name", jav_id)
    size = best.get("size", "未知")
    has_sub = best.get("_has_subtitle", False)
    sub_tag = "✅ 有字幕" if has_sub else "⚠️ 无字幕"

    if send_msg_fn:
        send_msg_fn(
            f"📡 找到 {len(magnets)} 条磁力，最佳:\n"
            f"   {name[:60]}\n"
            f"   📦 {size} | {sub_tag}\n"
            f"⬆️  推送到 PikPak..."
        )

    ok = push_to_pikpak(best["magnet"], jav_id, send_msg_fn)
    status = "pushed" if ok else "push_failed"
    log_pipeline(jav_id, status, {"name": name, "size": size, "has_subtitle": has_sub})

    if not ok:
        msg = f"❌ {jav_id}: 推送 PikPak 失败，请检查网络"
        if send_msg_fn:
            send_msg_fn(msg)
        return {"ok": False, "jav_id": jav_id, "msg": msg, "tags": []}

    if push_only:
        msg = (
            f"✅ {jav_id} 已推送到 PikPak！\n"
            f"   {sub_tag} | {size}\n"
            f"💡 下载完成后用 /embed <本地路径> 建立检索索引"
        )
        if send_msg_fn:
            send_msg_fn(msg)
        return {"ok": True, "jav_id": jav_id, "magnet": best["magnet"],
                "has_subtitle": has_sub, "video_path": None, "tags": [], "msg": msg}

    # ── 等待视频出现 ──
    if send_msg_fn:
        send_msg_fn(
            f"✅ 已推送 PikPak！开始监控下载...\n"
            f"   监视目录: {', '.join(PIKPAK_DIRS)}\n"
            f"   超时: 10 分钟"
        )

    video_path = wait_for_video(jav_id, send_msg_fn)
    if not video_path:
        msg = (
            f"⏰ {jav_id}: 等待超时（10分钟），未检测到视频文件\n"
            f"   下载完成后请手动运行: /embed <视频路径>"
        )
        log_pipeline(jav_id, "wait_timeout")
        if send_msg_fn:
            send_msg_fn(msg)
        return {"ok": True, "jav_id": jav_id, "magnet": best["magnet"],
                "has_subtitle": has_sub, "video_path": None, "tags": [], "msg": msg}

    log_pipeline(jav_id, "video_found", {"path": video_path})
    if send_msg_fn:
        send_msg_fn(f"🎬 检测到视频文件: {os.path.basename(video_path)}\n开始建立索引...")

    # ── 建立索引 + 计算标签 ──
    tags = index_video_with_tags(video_path, send_msg_fn)
    log_pipeline(jav_id, "indexed", {"path": video_path, "tags": tags})

    if tags:
        tags_str = " | ".join(tags)
        msg = (
            f"✅ {jav_id} 索引完成！\n"
            f"   🎬 {os.path.basename(video_path)}\n"
            f"   🏷️  标签: {tags_str}\n"
            f"   💡 现在可以用 /clips <描述> 搜索片段"
        )
    else:
        msg = (
            f"✅ {jav_id} 下载完成，索引建立中\n"
            f"   🎬 {os.path.basename(video_path)}\n"
            f"   💡 用 /clips <描述> 搜索片段"
        )

    if send_msg_fn:
        send_msg_fn(msg)

    return {"ok": True, "jav_id": jav_id, "magnet": best["magnet"],
            "has_subtitle": has_sub, "video_path": video_path, "tags": tags, "msg": msg}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="JAV 自动流水线: 搜索 → 磁力 → PikPak → 索引")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("jav_id", nargs="?", help="番号，如 SONE-758")
    group.add_argument("--watch", metavar="DIR", help="监视目录，新文件自动索引")
    parser.add_argument("--push-only", action="store_true", help="只推磁力，不等待下载")
    parser.add_argument("--interval", type=int, default=WATCH_INTERVAL,
                        help=f"监视间隔秒数（默认 {WATCH_INTERVAL}）")
    args = parser.parse_args()

    if args.watch:
        if not os.path.isdir(args.watch):
            print(f"❌ 目录不存在: {args.watch}")
            sys.exit(1)
        watch_directory(args.watch, args.interval)
    else:
        result = auto_pipeline(args.jav_id, push_only=args.push_only)
        print(result["msg"])
        sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
