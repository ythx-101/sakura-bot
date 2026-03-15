#!/usr/bin/env python3
"""
jav_image_search.py — 以图搜番（封面索引）

输入一张图片，embed 后在封面索引中检索，返回 top-N 匹配的番号+标题+女优。

用法:
  python3 jav_image_search.py --image /path/to/query.jpg
  python3 jav_image_search.py --image /path/to/query.jpg --top 10
"""

import argparse
import base64
import json
import math
import os
import sys

import requests
import yaml

# ─── 配置 ──────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "jav_config.yaml")
INDEX_DIR   = os.path.expanduser("~/.openclaw/skills/jav-skill/cover_index")
GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent"
DEFAULT_API_KEY  = "YOUR_GEMINI_API_KEY"


def load_api_key() -> str:
    try:
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        key = cfg.get("gemini_api_key", "")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or DEFAULT_API_KEY


def get_image_embedding(image_path: str, api_key: str) -> list:
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    payload = {
        "model": "models/gemini-embedding-2-preview",
        "content": {"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}]},
        "outputDimensionality": 3072,
    }
    r = requests.post(f"{GEMINI_EMBED_URL}?key={api_key}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]["values"]


def cosine_sim(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def load_index(index_dir: str) -> list:
    entries = []
    if not os.path.exists(index_dir):
        return entries
    for fname in sorted(os.listdir(index_dir)):
        if not fname.endswith(".jsonl"):
            continue
        with open(os.path.join(index_dir, fname)) as f:
            for line in f:
                if line.strip():
                    try:
                        e = json.loads(line)
                        if e.get("embedding"):
                            entries.append(e)
                    except Exception:
                        pass
    return entries


def search_by_image(image_path: str, top_n: int = 5, index_dir: str = None) -> list:
    """
    核心检索函数，可被 bot.py 直接导入调用。
    返回: [{"jav_id": ..., "video_name": ..., "title": ..., "stars": [...], "score": ...}, ...]
    """
    if index_dir is None:
        index_dir = INDEX_DIR
    api_key = load_api_key()

    print(f"🖼️  嵌入查询图片: {os.path.basename(image_path)}")
    q_emb = get_image_embedding(image_path, api_key)

    entries = load_index(index_dir)
    if not entries:
        print(f"📭 封面索引为空: {index_dir}")
        return []

    print(f"🔍 在 {len(entries)} 个番号中检索...")
    scores = []
    for e in entries:
        emb = e.get("embedding", [])
        if len(emb) != len(q_emb):
            continue
        scores.append({
            "score":      cosine_sim(q_emb, emb),
            "jav_id":     e.get("jav_id", ""),
            "video_name": e.get("video_name", "unknown"),
            "title":      e.get("title", ""),
            "stars":      e.get("stars", []),
        })

    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_n]


def format_results(results: list, query_desc: str = "") -> str:
    if not results:
        return "😔 未找到匹配番号"
    header = f"🖼️ 以图搜番结果{' — ' + query_desc if query_desc else ''}:\n"
    lines  = [header]
    for i, r in enumerate(results, 1):
        stars_str = "、".join(r["stars"]) if r["stars"] else "—"
        lines.append(f"  {i}. [{r['score']:.3f}] {r['jav_id']} {stars_str}")
        if r.get("title"):
            lines.append(f"      {r['title'][:50]}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="以图搜番（封面索引）")
    parser.add_argument("--image", "-i", required=True, help="查询图片路径")
    parser.add_argument("--top",   "-n", type=int, default=5, help="返回 top-N 结果（默认5）")
    parser.add_argument("--index-dir", default=INDEX_DIR, help=f"索引目录（默认 {INDEX_DIR}）")
    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(f"❌ 图片不存在: {args.image}")
        sys.exit(1)

    results = search_by_image(args.image, args.top, args.index_dir)
    print(format_results(results))


if __name__ == "__main__":
    main()
