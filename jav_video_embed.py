#!/usr/bin/env python3
"""
jav_video_embed.py — 视频关键帧向量索引 + 自动标签

每分钟提取1帧 → Gemini Embedding 2（图片模式）→ 3072维向量索引
2小时视频：120次 API 调用，2-3分钟完成。

用法:
  python3 jav_video_embed.py --video /path/to/SONE-758.mp4
  python3 jav_video_embed.py --video /path/to/SONE-758.mp4 --fps 0.5   # 每2分钟1帧
  python3 jav_video_embed.py --video /path/to/SONE-758.mp4 --fps 2     # 每30秒1帧
  python3 jav_video_embed.py --dir ~/Downloads/videos/                  # 批量索引目录
  python3 jav_video_embed.py --video /path/to/SONE-758.mp4 --tags      # 只打标签
"""

import argparse
import base64
import json
import math
import os
import subprocess
import sys
import tempfile
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_SITE = os.path.join(SKILL_DIR, ".venv/lib/python3.11/site-packages")
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

import requests
import yaml

CONFIG_PATH = os.path.join(SKILL_DIR, "config.yaml")
# 兼容 bot.py 用的路径（两个路径都支持）
INDEX_DIR = os.path.expanduser("~/.jav_video_index")
TAGS_DIR = os.path.expanduser("~/.jav_video_tags")

GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2-preview:embedContent"

# ─── 25 个文本探针，覆盖场景/风格/特征 ────────────────────────
TEXT_PROBES = [
    # 场景类型（8个）
    "室内场景，卧室，床",
    "户外场景，自然，公园",
    "泳池边，游泳池，水",
    "浴室，淋浴间，浴缸",
    "办公室，工作环境，桌椅",
    "酒店房间，宾馆",
    "厨房，餐厅，饭桌",
    "汽车内部，车厢",
    # 风格（6个）
    "剧情向，故事情节，对话",
    "写真风，艺术摄影，美感",
    "POV视角，第一人称拍摄",
    "纪录片风格，真实感",
    "专业摄影，高制作水准，灯光精良",
    "素人风格，生活感",
    # 画面特征（6个）
    "特写镜头，面部细节",
    "全身画面，远景",
    "高清画质，清晰锐利",
    "4K超高清，细腻",
    "暖色调，黄色灯光，温馨",
    "冷色调，蓝色，清冷",
    # 内容特征（5个）
    "中文字幕，汉字，字幕条",
    "多人场景，群体",
    "单人主角，独奏",
    "夜间场景，黑暗，灯光",
    "慢动作，柔焦，梦幻感",
]

def load_config():
    # 先找 bot 目录下的 jav_config.yaml
    bot_cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jav_config.yaml")
    if os.path.exists(bot_cfg):
        with open(bot_cfg) as f:
            return yaml.safe_load(f) or {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}

def get_api_key(cfg):
    key = cfg.get("gemini_api_key", "")
    if not key:
        key = os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        # 硬编码兜底（与 bot.py 保持一致）
        key = "YOUR_GEMINI_API_KEY"
    return key

def get_video_duration(video_path: str) -> float:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_format", video_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data.get("format", {}).get("duration", 0))

def extract_frame(video_path: str, timestamp: float, out_path: str):
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale=640:-2",
        "-q:v", "3",
        out_path
    ]
    subprocess.run(cmd, capture_output=True, check=True)

def get_image_embedding(image_path: str, api_key: str) -> list:
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    payload = {
        "model": "models/gemini-embedding-2-preview",
        "content": {
            "parts": [{"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}]
        },
        "outputDimensionality": 3072
    }
    r = requests.post(f"{GEMINI_EMBED_URL}?key={api_key}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]["values"]

def get_text_embedding(text: str, api_key: str) -> list:
    payload = {
        "model": "models/gemini-embedding-2-preview",
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": 3072
    }
    r = requests.post(f"{GEMINI_EMBED_URL}?key={api_key}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["embedding"]["values"]

def cosine_sim(a: list, b: list) -> float:
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(x*x for x in b))
    return dot / (na * nb) if na and nb else 0.0

def compute_tags(index_path: str, api_key: str, top_n: int = 5) -> list:
    """
    读取已有索引 JSONL，计算每个 probe 与所有帧向量的平均余弦相似度，
    取 top_n 作为标签。
    """
    # 加载帧向量
    frame_embeddings = []
    with open(index_path) as f:
        for line in f:
            if line.strip():
                try:
                    e = json.loads(line)
                    if e.get("embedding"):
                        frame_embeddings.append(e["embedding"])
                except Exception:
                    pass

    if not frame_embeddings:
        print("⚠️ 索引为空，无法计算标签")
        return []

    probe_scores = []
    print(f"🏷️  计算 {len(TEXT_PROBES)} 个探针 vs {len(frame_embeddings)} 帧 ...")
    for probe in TEXT_PROBES:
        try:
            probe_emb = get_text_embedding(probe, api_key)
            avg_sim = sum(cosine_sim(probe_emb, fe) for fe in frame_embeddings) / len(frame_embeddings)
            probe_scores.append((probe.split("，")[0], avg_sim))  # 取探针第一段作为标签名
            time.sleep(0.1)
        except Exception as e:
            print(f"  ⚠️ probe '{probe[:10]}' 失败: {e}")

    probe_scores.sort(key=lambda x: x[1], reverse=True)
    tags = [t for t, _ in probe_scores[:top_n]]
    print(f"✅ Top-{top_n} 标签: {tags}")
    return tags

def save_tags(video_name: str, tags: list, index_path: str = None):
    """将标签写入 tags 目录，并可选地更新 JSONL 元数据"""
    os.makedirs(TAGS_DIR, exist_ok=True)
    tag_file = os.path.join(TAGS_DIR, f"{video_name}.tags.json")
    result = {"video": video_name, "tags": tags}
    with open(tag_file, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"💾 标签已保存: {tag_file}")
    print(json.dumps(result, ensure_ascii=False))
    return tag_file

def index_video(video_path: str, fps: float = 1/60, force: bool = False,
                auto_tag: bool = True) -> str:
    cfg = load_config()
    api_key = get_api_key(cfg)

    video_name = os.path.splitext(os.path.basename(video_path))[0]
    os.makedirs(INDEX_DIR, exist_ok=True)
    index_path = os.path.join(INDEX_DIR, f"{video_name}.jsonl")

    if os.path.exists(index_path) and not force:
        with open(index_path) as f:
            lines = [l for l in f if l.strip()]
        if lines:
            print(f"⚡ 索引已存在 ({len(lines)} 帧): {index_path}（用 --force 重建）")
            if auto_tag:
                tags = compute_tags(index_path, api_key)
                if tags:
                    save_tags(video_name, tags, index_path)
            return index_path

    duration = get_video_duration(video_path)
    if duration == 0:
        print(f"❌ 无法读取视频时长: {video_path}")
        sys.exit(1)

    interval = 1.0 / fps
    timestamps = [i * interval for i in range(int(duration / interval) + 1) if i * interval < duration]

    print(f"📹 {video_name}")
    print(f"   时长: {duration/60:.1f} 分钟 | 采样: 每 {interval:.0f} 秒1帧 | 共 {len(timestamps)} 帧")

    done_timestamps = set()
    if os.path.exists(index_path):
        with open(index_path) as f:
            for line in f:
                if line.strip():
                    try:
                        e = json.loads(line)
                        done_timestamps.add(e["timestamp"])
                    except Exception:
                        pass

    remaining = [t for t in timestamps if t not in done_timestamps]
    if done_timestamps:
        print(f"   断点续传: 已完成 {len(done_timestamps)} 帧，剩余 {len(remaining)} 帧")

    with open(index_path, "a") as f_out:
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, ts in enumerate(remaining):
                frame_path = os.path.join(tmpdir, "frame.jpg")
                mins, secs = divmod(int(ts), 60)
                print(f"  [{i+1}/{len(remaining)}] {mins:02d}:{secs:02d} ...", end=" ", flush=True)
                try:
                    extract_frame(video_path, ts, frame_path)
                    embedding = get_image_embedding(frame_path, api_key)
                    entry = {
                        "video": video_path,
                        "video_name": video_name,
                        "timestamp": ts,
                        "embedding": embedding
                    }
                    f_out.write(json.dumps(entry) + "\n")
                    f_out.flush()
                    print("✅")
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 429:
                        print("⏳ 限速，等5秒...")
                        time.sleep(5)
                        try:
                            embedding = get_image_embedding(frame_path, api_key)
                            entry = {"video": video_path, "video_name": video_name,
                                     "timestamp": ts, "embedding": embedding}
                            f_out.write(json.dumps(entry) + "\n")
                            f_out.flush()
                            print("✅ (重试成功)")
                        except Exception as e2:
                            print(f"❌ {e2}")
                    else:
                        print(f"❌ HTTP {e.response.status_code}")
                except Exception as e:
                    print(f"❌ {e}")
                time.sleep(0.2)

    with open(index_path) as f:
        actual = sum(1 for l in f if l.strip())
    print(f"\n📦 索引完成: {actual}/{len(timestamps)} 帧 → {index_path}")

    # 自动打标签
    if auto_tag and actual > 0:
        print("\n🏷️  开始自动打标签...")
        tags = compute_tags(index_path, api_key)
        if tags:
            save_tags(video_name, tags, index_path)

    return index_path

def index_directory(directory: str, fps: float = 1/60, force: bool = False):
    video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.ts', '.m2ts'}
    videos = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if os.path.splitext(f)[1].lower() in video_exts:
                videos.append(os.path.join(root, f))

    if not videos:
        print(f"📁 {directory} 下没有视频文件")
        return

    print(f"📂 找到 {len(videos)} 个视频，开始批量索引...\n")
    for i, v in enumerate(videos, 1):
        print(f"{'='*50}")
        print(f"[{i}/{len(videos)}] {os.path.basename(v)}")
        index_video(v, fps, force)
        print()

def tags_only(video_path: str):
    """只打标签，不做完整索引（要求索引已存在）"""
    cfg = load_config()
    api_key = get_api_key(cfg)
    video_name = os.path.splitext(os.path.basename(video_path))[0]
    index_path = os.path.join(INDEX_DIR, f"{video_name}.jsonl")
    if not os.path.exists(index_path):
        print(f"❌ 索引不存在: {index_path}，请先运行 --video 建立索引")
        sys.exit(1)
    tags = compute_tags(index_path, api_key)
    if tags:
        save_tags(video_name, tags)

def main():
    parser = argparse.ArgumentParser(description="视频关键帧向量索引 + 自动标签（Gemini Embedding 2）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video", "-v", help="单个视频文件")
    group.add_argument("--dir", "-d", help="批量索引目录")
    parser.add_argument("--fps", type=float, default=1/60,
                        help="采样率（帧/秒，默认 1/60 = 每分钟1帧）")
    parser.add_argument("--every", type=int, metavar="SECONDS",
                        help="每N秒1帧（--every 30 等同于 --fps 0.033）")
    parser.add_argument("--force", action="store_true", help="强制重建索引")
    parser.add_argument("--tags", action="store_true",
                        help="只输出标签（要求索引已存在），不做完整索引")
    parser.add_argument("--no-tags", action="store_true", help="索引完成后跳过自动打标签")
    args = parser.parse_args()

    fps = args.fps
    if args.every:
        fps = 1.0 / args.every

    if args.video:
        if not os.path.exists(args.video):
            print(f"❌ 文件不存在: {args.video}")
            sys.exit(1)
        if args.tags:
            tags_only(args.video)
        else:
            index_video(args.video, fps, args.force, auto_tag=not args.no_tags)
    else:
        index_directory(args.dir, fps, args.force)

if __name__ == "__main__":
    main()
