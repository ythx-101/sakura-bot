#!/usr/bin/env python3
"""
jav_cover_search.py — 封面语义搜索

用文字或图片 query，在封面索引中找到最匹配的番号。

用法:
  python3 jav_cover_search.py --query "户外泳池"
  python3 jav_cover_search.py --image /path/to/ref.jpg
  python3 jav_cover_search.py --query "高质量" --top 10
  python3 jav_cover_search.py --query "特写" --json
"""

import argparse
import base64
import json
import math
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

import requests
import yaml

CONFIG_PATH     = os.path.join(SCRIPT_DIR, "jav_config.yaml")
COVER_INDEX_DIR = os.path.expanduser("~/.openclaw/skills/jav-skill/cover_index")
GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent"


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def get_api_key(cfg):
    key = cfg.get("gemini_api_key", "")
    if not key:
        key = (os.environ.get("GOOGLE_API_KEY") or
               os.environ.get("GEMINI_API_KEY") or
               "YOUR_GEMINI_API_KEY")
    return key


def cosine_similarity(a: list, b: list) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def get_text_embedding(text: str, api_key: str) -> list:
    payload = {
        "model": "models/gemini-embedding-2-preview",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": 3072,
    }
    r = requests.post(f"{GEMINI_EMBED_URL}?key={api_key}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]["values"]


def get_image_embedding(image_path: str, api_key: str) -> list:
    with open(image_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode()
    ext  = os.path.splitext(image_path)[1].lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(ext, "image/jpeg")
    payload = {
        "model": "models/gemini-embedding-2-preview",
        "content": {"parts": [{"inline_data": {"mime_type": mime, "data": img_data}}]},
        "outputDimensionality": 3072,
    }
    r = requests.post(f"{GEMINI_EMBED_URL}?key={api_key}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]["values"]


def load_index(index_dir: str = COVER_INDEX_DIR) -> list:
    """加载所有封面索引条目"""
    entries = []
    if not os.path.exists(index_dir):
        return entries
    for fname in os.listdir(index_dir):
        if not fname.endswith(".jsonl"):
            continue
        with open(os.path.join(index_dir, fname)) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    return entries


def search(query_embedding: list, entries: list, top_k: int = 5) -> list:
    """余弦相似度检索，返回 top_k 结果（番号+标题+女优，无时间戳）"""
    scores = []
    for entry in entries:
        emb = entry.get("embedding", [])
        if not emb or len(emb) != len(query_embedding):
            continue
        scores.append({
            "score":      cosine_similarity(query_embedding, emb),
            "jav_id":     entry.get("jav_id", ""),
            "video_name": entry.get("video_name", ""),
            "title":      entry.get("title", ""),
            "stars":      entry.get("stars", []),
            "tags":       entry.get("tags", []),
        })
    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_k]


def format_results(results: list, query_desc: str = "") -> str:
    if not results:
        return "😔 未找到匹配番号"
    header = f"🎯 搜索结果{' — ' + query_desc if query_desc else ''}:\n"
    lines  = [header]
    for i, r in enumerate(results, 1):
        stars_str = "、".join(r["stars"]) if r["stars"] else "—"
        lines.append(f"  {i}. [{r['score']:.3f}] <code>{r['jav_id']}</code> {stars_str}")
        if r.get("title"):
            lines.append(f"      {r['title'][:50]}")
    return "\n".join(lines)


def search_by_text(query: str, top_n: int = 5, index_dir: str = None) -> list:
    """供 bot.py 直接调用的文字搜索接口"""
    if index_dir is None:
        index_dir = COVER_INDEX_DIR
    cfg     = load_config()
    api_key = get_api_key(cfg)
    q_emb   = get_text_embedding(query, api_key)
    entries = load_index(index_dir)
    return search(q_emb, entries, top_n)


def search_by_image(image_path: str, top_n: int = 5, index_dir: str = None) -> list:
    """供 bot.py 直接调用的图片搜索接口"""
    if index_dir is None:
        index_dir = COVER_INDEX_DIR
    cfg     = load_config()
    api_key = get_api_key(cfg)
    q_emb   = get_image_embedding(image_path, api_key)
    entries = load_index(index_dir)
    return search(q_emb, entries, top_n)


def main():
    parser = argparse.ArgumentParser(description="封面语义搜索（Gemini Embedding 2）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", "-q", help="文字检索 query")
    group.add_argument("--image", "-i", help="图片检索")
    parser.add_argument("--top",  type=int, default=5,  help="返回结果数（默认5）")
    parser.add_argument("--json", action="store_true",  dest="json_out", help="JSON 输出")
    args = parser.parse_args()

    cfg     = load_config()
    api_key = get_api_key(cfg)

    if args.query:
        print(f"🔍 获取 query 向量: {args.query}")
        query_emb = get_text_embedding(args.query, api_key)
    else:
        print(f"🖼️  获取图片向量: {args.image}")
        query_emb = get_image_embedding(args.image, api_key)

    entries = load_index()
    if not entries:
        print(f"📭 封面索引为空: {COVER_INDEX_DIR}")
        sys.exit(1)

    print(f"📚 索引共 {len(entries)} 个番号")
    results = search(query_emb, entries, args.top)

    if args.json_out:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    # 去掉 HTML tag 用于 terminal 显示
    import re
    print(re.sub(r'<[^>]+>', '', format_results(results, args.query or args.image)))


if __name__ == "__main__":
    main()
