#!/usr/bin/env python3
"""
jav_clip_search.py — 语义片段检索

用文字或图片 query，在视频索引中找到最匹配的片段。

用法:
  # 文字检索
  python3 jav_clip_search.py --query "户外泳池镜头"
  python3 jav_clip_search.py --query "高质量特写" --top 10

  # 图片检索（找视觉相似的片段）
  python3 jav_clip_search.py --image /path/to/ref.jpg

  # 只检索特定视频
  python3 jav_clip_search.py --query "开场镜头" --video SONE-758

  # 检索后直接提取片段
  python3 jav_clip_search.py --query "漂亮镜头" --extract --output /tmp/clips/
"""

import argparse
import base64
import json
import math
import os
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_SITE = os.path.join(SKILL_DIR, ".venv/lib/python3.11/site-packages")
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

import requests
import yaml

CONFIG_PATH = os.path.join(SKILL_DIR, "config.yaml")
INDEX_DIR = os.path.expanduser("~/.openclaw/skills/jav-skill/video_index")

GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent"

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}

def get_api_key(cfg):
    key = cfg.get("gemini_api_key", "")
    if not key:
        key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("❌ 未配置 Gemini API Key")
        sys.exit(1)
    return key

def cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

def get_text_embedding(text: str, api_key: str) -> list:
    """文字 → 向量"""
    payload = {
        "model": "models/gemini-embedding-2-preview",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": 3072
    }
    r = requests.post(f"{GEMINI_EMBED_URL}?key={api_key}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]["values"]

def get_image_embedding(image_path: str, api_key: str) -> list:
    """图片 → 向量"""
    with open(image_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(image_path)[1].lower()
    mime = {"jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(ext, "image/jpeg")
    payload = {
        "model": "models/gemini-embedding-2-preview",
        "content": {
            "parts": [{"inline_data": {"mime_type": mime, "data": img_data}}]
        },
        "outputDimensionality": 3072
    }
    r = requests.post(f"{GEMINI_EMBED_URL}?key={api_key}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]["values"]

def load_index(video_filter: str = "") -> list:
    """加载所有视频索引"""
    entries = []
    if not os.path.exists(INDEX_DIR):
        return entries
    for fname in os.listdir(INDEX_DIR):
        if not fname.endswith(".jsonl"):
            continue
        if video_filter and video_filter.lower() not in fname.lower():
            continue
        with open(os.path.join(INDEX_DIR, fname)) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    return entries

def search(query_embedding: list, entries: list, top_k: int = 5) -> list:
    """余弦相似度检索，返回 top_k 结果"""
    scores = []
    for entry in entries:
        emb = entry.get("embedding", [])
        if not emb:
            continue
        score = cosine_similarity(query_embedding, emb)
        # 兼容新格式（timestamp）和旧格式（start/end）
        ts = entry.get("timestamp", entry.get("start", 0))
        scores.append({
            "score": score,
            "video": entry["video"],
            "video_name": entry.get("video_name", ""),
            "timestamp": ts,
            "start": ts,
            "end": entry.get("end", ts + 30)
        })
    scores.sort(key=lambda x: x["score"], reverse=True)

    # 去重：同一视频同一区域不重复（保留最高分，60秒窗口）
    seen = set()
    results = []
    for s in scores:
        key = (s["video"], round(s["timestamp"] / 60))  # 60秒内不重复
        if key not in seen:
            seen.add(key)
            results.append(s)
        if len(results) >= top_k:
            break
    return results

def extract_clip(video_path: str, start: float, end: float, output_path: str):
    """提取原始画质片段"""
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-c", "copy",
        output_path
    ]
    subprocess.run(cmd, capture_output=True, check=True)

def main():
    parser = argparse.ArgumentParser(description="视频语义片段检索")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", "-q", help="文字检索 query")
    group.add_argument("--image", "-i", help="图片检索（找视觉相似片段）")
    parser.add_argument("--video", "-v", help="限定检索的视频名（模糊匹配）")
    parser.add_argument("--top", type=int, default=5, help="返回结果数（默认5）")
    parser.add_argument("--extract", action="store_true", help="直接提取片段到 --output 目录")
    parser.add_argument("--output", "-o", default="/tmp/jav_clips/", help="片段输出目录")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON 输出")
    args = parser.parse_args()

    cfg = load_config()
    api_key = get_api_key(cfg)

    # 获取 query 向量
    if args.query:
        print(f"🔍 获取 query 向量: {args.query}")
        query_emb = get_text_embedding(args.query, api_key)
    else:
        print(f"🖼️  获取图片向量: {args.image}")
        query_emb = get_image_embedding(args.image, api_key)

    # 加载索引
    entries = load_index(args.video or "")
    if not entries:
        print(f"📭 索引为空，请先运行 jav_video_embed.py 建立索引")
        print(f"   索引目录: {INDEX_DIR}")
        sys.exit(1)

    print(f"📚 索引共 {len(entries)} 个片段")

    # 检索
    results = search(query_emb, entries, args.top)

    if args.json_out:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    print(f"\n🎯 Top {len(results)} 匹配片段:\n")
    for i, r in enumerate(results, 1):
        ts = r["timestamp"]
        mins, secs = divmod(int(ts), 60)
        print(f"  {i}. [{r['score']:.4f}] {r['video_name']}  {mins:02d}:{secs:02d}")

    # 提取片段（以关键帧时间戳为中心，取前后各 clip_duration/2 秒）
    clip_duration = 6.0
    if args.extract and results:
        os.makedirs(args.output, exist_ok=True)
        print(f"\n✂️  提取片段到 {args.output} ...")
        for i, r in enumerate(results, 1):
            ts = r["timestamp"]
            start = max(0, ts - clip_duration / 2)
            end = ts + clip_duration / 2
            fname = f"{i:02d}_{r['video_name']}_{int(ts)}s.mp4"
            out_path = os.path.join(args.output, fname)
            try:
                extract_clip(r["video"], start, end, out_path)
                print(f"  ✅ {fname}")
            except Exception as e:
                print(f"  ❌ {fname}: {e}")

        print(f"\n💾 片段已保存到 {args.output}")
        print(f"   合成周精选: python3 jav_weekly_reel.py --clips {args.output}")

if __name__ == "__main__":
    main()
