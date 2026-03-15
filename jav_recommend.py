#!/usr/bin/env python3
"""
jav_recommend.py — 基于历史检索的封面个性化推荐

记录 /clips 检索历史 → 聚合用户偏好向量 → 推荐相似番号

用法（命令行）:
  python3 jav_recommend.py                # 基于历史推荐
  python3 jav_recommend.py --top 10       # 推荐10条
  python3 jav_recommend.py --history      # 查看检索历史
  python3 jav_recommend.py --clear        # 清除历史

Bot 集成:
  from jav_recommend import record_query, recommend
"""

import argparse
import json
import math
import os
import sys
import time

import requests
import yaml

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(SCRIPT_DIR, "jav_config.yaml")
INDEX_DIR    = os.path.expanduser("~/.openclaw/skills/jav-skill/cover_index")
HISTORY_PATH = os.path.expanduser("~/.jav_user_history.jsonl")
GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent"
DEFAULT_API_KEY  = "YOUR_GEMINI_API_KEY"

MAX_HISTORY = 200


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


def get_text_embedding(text: str, api_key: str, dim: int = 3072) -> list:
    payload = {
        "model": "models/gemini-embedding-2-preview",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": dim,
    }
    r = requests.post(f"{GEMINI_EMBED_URL}?key={api_key}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]["values"]


def cosine_sim(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def record_query(query: str, embedding: list):
    """
    记录一次检索历史（query 文本 + 向量）。
    被 bot.py 的 _handle_clips 调用。
    """
    entry = {"query": query, "embedding": embedding, "ts": time.time()}
    history = []
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            for line in f:
                if line.strip():
                    try:
                        history.append(json.loads(line))
                    except Exception:
                        pass
    history.append(entry)
    history = history[-MAX_HISTORY:]
    with open(HISTORY_PATH, "w") as f:
        for h in history:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")


def load_history() -> list:
    if not os.path.exists(HISTORY_PATH):
        return []
    history = []
    with open(HISTORY_PATH) as f:
        for line in f:
            if line.strip():
                try:
                    history.append(json.loads(line))
                except Exception:
                    pass
    return history


def compute_preference_vector(history: list) -> list:
    embeddings = [h["embedding"] for h in history if h.get("embedding")]
    if not embeddings:
        return []
    dim = len(embeddings[0])
    embeddings = [e for e in embeddings if len(e) == dim]
    if not embeddings:
        return []
    return [sum(e[i] for e in embeddings) / len(embeddings) for i in range(dim)]


def load_index(index_dir: str, dim: int) -> list:
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
                        emb = e.get("embedding", [])
                        if emb and len(emb) == dim:
                            entries.append(e)
                    except Exception:
                        pass
    return entries


def recommend(top_n: int = 5, index_dir: str = None) -> list:
    """
    基于历史偏好向量推荐番号。
    返回: [{"jav_id": ..., "video_name": ..., "title": ..., "stars": [...], "score": ...}, ...]
    """
    if index_dir is None:
        index_dir = INDEX_DIR

    history = load_history()
    if not history:
        return []

    pref = compute_preference_vector(history)
    if not pref:
        return []

    dim     = len(pref)
    entries = load_index(index_dir, dim)
    if not entries:
        return []

    scores = []
    for e in entries:
        scores.append({
            "score":      cosine_sim(pref, e["embedding"]),
            "jav_id":     e.get("jav_id", ""),
            "video_name": e.get("video_name", "unknown"),
            "title":      e.get("title", ""),
            "stars":      e.get("stars", []),
        })

    scores.sort(key=lambda x: x["score"], reverse=True)
    return scores[:top_n]


def format_recommend_msg(results: list, history_count: int) -> str:
    if not results:
        return "😔 推荐为空（封面索引可能为空，或历史向量维度不匹配）"
    lines = [f"🎯 基于 {history_count} 条历史记录的个性化推荐:\n"]
    for i, r in enumerate(results, 1):
        stars_str = "、".join(r["stars"]) if r["stars"] else "—"
        lines.append(f"  {i}. [{r['score']:.3f}] {r['jav_id']} {stars_str}")
        if r.get("title"):
            lines.append(f"      {r['title'][:50]}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="JAV 封面个性化推荐")
    parser.add_argument("--top",       "-n",  type=int, default=5,  help="推荐条数（默认5）")
    parser.add_argument("--history",   action="store_true",          help="查看检索历史")
    parser.add_argument("--clear",     action="store_true",          help="清除所有历史")
    parser.add_argument("--index-dir", default=INDEX_DIR,            help="索引目录")
    args = parser.parse_args()

    if args.clear:
        if os.path.exists(HISTORY_PATH):
            os.remove(HISTORY_PATH)
            print("✅ 历史已清除")
        else:
            print("📭 无历史记录")
        return

    if args.history:
        history = load_history()
        if not history:
            print("📭 无历史记录")
            return
        print(f"📚 检索历史（最近 {len(history)} 条）:")
        for i, h in enumerate(history[-20:], 1):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(h["ts"]))
            print(f"  {i:2d}. [{ts}] {h['query']}")
        return

    history = load_history()
    if not history:
        print("📭 还没有搜索历史，先用 /clips 搜索一些番号")
        return

    results = recommend(args.top, args.index_dir)
    print(format_recommend_msg(results, len(history)))


if __name__ == "__main__":
    main()
