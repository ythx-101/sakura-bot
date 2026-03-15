#!/usr/bin/env python3
"""
jav_weekly_reel.py — 每周精选视频合成

自动检索本周所有视频中的精华镜头，合成带转场和 BGM 的周精选视频。

用法:
  # 全自动：检索本周视频索引，合成周精选
  python3 jav_weekly_reel.py

  # 指定片段目录（已提取好的 clips）
  python3 jav_weekly_reel.py --clips /tmp/jav_clips/

  # 指定输出路径和 BGM
  python3 jav_weekly_reel.py --output ~/Desktop/weekly_2026W11.mp4 --bgm ~/Music/bgm.mp3

  # 调整每个片段时长和 query
  python3 jav_weekly_reel.py --clip-duration 6 --queries "漂亮镜头,精彩场景,高质量特写"
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_SITE = os.path.join(SKILL_DIR, ".venv/lib/python3.11/site-packages")
if os.path.exists(VENV_SITE) and VENV_SITE not in sys.path:
    sys.path.insert(0, VENV_SITE)

INDEX_DIR = os.path.expanduser("~/.openclaw/skills/jav-skill/video_index")
REEL_OUTPUT_DIR = os.path.expanduser("~/.openclaw/skills/jav-skill/weekly_reels")

# 默认检索 query（可以自定义）
DEFAULT_QUERIES = [
    "高质量精彩镜头",
    "唯美特写画面",
    "精彩精华片段",
    "高清漂亮场景",
]

def get_week_str():
    now = datetime.now()
    year, week, _ = now.isocalendar()
    return f"{year}W{week:02d}"

def get_this_week_range():
    """返回本周一到本周日的日期范围"""
    now = datetime.now()
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    return monday.replace(hour=0, minute=0, second=0), sunday.replace(hour=23, minute=59, second=59)

def get_indexed_videos_this_week() -> list:
    """获取本周有索引的视频列表（按索引文件 mtime 判断）"""
    if not os.path.exists(INDEX_DIR):
        return []
    monday, sunday = get_this_week_range()
    mon_ts = monday.timestamp()
    sun_ts = sunday.timestamp()
    videos = []
    for fname in os.listdir(INDEX_DIR):
        if not fname.endswith(".jsonl"):
            continue
        fpath = os.path.join(INDEX_DIR, fname)
        mtime = os.path.getmtime(fpath)
        if mon_ts <= mtime <= sun_ts:
            videos.append(fname.replace(".jsonl", ""))
    return videos

def search_clips_by_queries(queries: list, top_per_query: int = 3, video_filter: str = "") -> list:
    """多 query 检索，合并去重"""
    # 动态 import（避免循环依赖）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from jav_clip_search import get_text_embedding, load_index, search
    import yaml

    with open(os.path.join(SKILL_DIR, "config.yaml")) as f:
        cfg = yaml.safe_load(f) or {}
    api_key = cfg.get("gemini_api_key", "") or os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        print("❌ 未配置 Gemini API Key")
        sys.exit(1)

    entries = load_index(video_filter)
    if not entries:
        return []

    all_results = {}  # key: (video, start) → result
    for q in queries:
        print(f"  🔍 检索: {q}")
        emb = get_text_embedding(q, api_key)
        results = search(emb, entries, top_per_query * 2)
        for r in results[:top_per_query]:
            key = (r["video"], round(r["start"] / 3))  # 3秒内不重复
            if key not in all_results or r["score"] > all_results[key]["score"]:
                all_results[key] = r

    # 按得分排序
    clips = sorted(all_results.values(), key=lambda x: x["score"], reverse=True)
    return clips

def extract_clips(clips: list, output_dir: str, clip_duration: float = 6.0) -> list:
    """提取片段（原始画质，以关键帧时间戳为中心）"""
    os.makedirs(output_dir, exist_ok=True)
    clip_paths = []
    for i, clip in enumerate(clips, 1):
        # 以关键帧时间戳为中心，前后各取 clip_duration/2 秒
        ts = clip.get("timestamp", clip.get("start", 0))
        start = max(0, ts - clip_duration / 2)
        end = ts + clip_duration / 2
        fname = f"{i:03d}_{clip['video_name']}_{int(ts)}s.mp4"
        out_path = os.path.join(output_dir, fname)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", clip["video"],
            "-t", str(end - start),
            "-c", "copy",
            out_path
        ]

        try:
            subprocess.run(cmd, capture_output=True, check=True)
            clip_paths.append(out_path)
            print(f"  ✅ [{i}/{len(clips)}] {fname}")
        except Exception as e:
            print(f"  ❌ {fname}: {e}")
    return clip_paths

def build_concat_list(clip_paths: list, tmpdir: str) -> str:
    """生成 ffmpeg concat 文件"""
    list_path = os.path.join(tmpdir, "concat.txt")
    with open(list_path, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    return list_path

def add_crossfade(clip_paths: list, tmpdir: str, fade_duration: float = 0.5) -> list:
    """给每个片段加淡入淡出（为合并做准备）"""
    faded = []
    for i, path in enumerate(clip_paths):
        out = os.path.join(tmpdir, f"faded_{i:03d}.mp4")
        # 获取片段时长
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "json", path],
            capture_output=True, text=True
        )
        dur = float(json.loads(result.stdout).get("format", {}).get("duration", 5))
        fade_out_start = max(0, dur - fade_duration)
        cmd = [
            "ffmpeg", "-y", "-i", path,
            "-vf", f"fade=t=in:st=0:d={fade_duration},fade=t=out:st={fade_out_start}:d={fade_duration}",
            "-af", f"afade=t=in:st=0:d={fade_duration},afade=t=out:st={fade_out_start}:d={fade_duration}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac",
            out
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            faded.append(out)
        except Exception:
            faded.append(path)  # fallback 用原始
    return faded

def merge_clips(clip_paths: list, output_path: str, bgm_path: str = "", tmpdir: str = ""):
    """合并所有片段，可选 BGM"""
    if not tmpdir:
        tmpdir = tempfile.mkdtemp()

    concat_path = build_concat_list(clip_paths, tmpdir)

    if bgm_path and os.path.exists(bgm_path):
        # 先 concat，再混 BGM
        raw_path = os.path.join(tmpdir, "raw_concat.mp4")
        cmd1 = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-c", "copy", raw_path
        ]
        subprocess.run(cmd1, capture_output=True, check=True)

        cmd2 = [
            "ffmpeg", "-y",
            "-i", raw_path,
            "-stream_loop", "-1", "-i", bgm_path,
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=first:weights=1 0.3[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            output_path
        ]
        subprocess.run(cmd2, capture_output=True, check=True)
    else:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-c", "copy", output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)

def add_title_card(output_path: str, week_str: str, clip_count: int, final_path: str):
    """在视频开头加周数标题（字幕覆盖）"""
    cmd = [
        "ffmpeg", "-y", "-i", output_path,
        "-vf", (
            f"drawtext=text='Weekly Highlights {week_str}':"
            f"fontsize=48:fontcolor=white:x=(w-text_w)/2:y=h/2-50:"
            f"enable='between(t,0,3)',"
            f"drawtext=text='{clip_count} clips':"
            f"fontsize=28:fontcolor=white@0.8:x=(w-text_w)/2:y=h/2+20:"
            f"enable='between(t,0,3)'"
        ),
        "-c:a", "copy",
        final_path
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except Exception:
        # fontconfig 可能不支持，跳过标题
        import shutil
        shutil.copy(output_path, final_path)
        return False

def main():
    parser = argparse.ArgumentParser(description="每周精选视频合成")
    parser.add_argument("--clips", help="已提取的片段目录（跳过检索步骤）")
    parser.add_argument("--output", "-o", help="输出视频路径")
    parser.add_argument("--bgm", help="背景音乐路径（mp3/aac）")
    parser.add_argument("--clip-duration", type=float, default=6.0, help="每个精选片段时长（秒，默认6）")
    parser.add_argument("--clips-count", type=int, default=15, help="精选片段总数（默认15）")
    parser.add_argument("--queries", help="检索 query，逗号分隔")
    parser.add_argument("--video-filter", help="只检索包含此关键词的视频")
    parser.add_argument("--no-fade", action="store_true", help="不加淡入淡出")
    args = parser.parse_args()

    week_str = get_week_str()
    os.makedirs(REEL_OUTPUT_DIR, exist_ok=True)
    output_path = args.output or os.path.join(REEL_OUTPUT_DIR, f"weekly_{week_str}.mp4")

    queries = DEFAULT_QUERIES
    if args.queries:
        queries = [q.strip() for q in args.queries.split(",")]

    with tempfile.TemporaryDirectory() as tmpdir:
        # Step 1: 获取片段
        if args.clips and os.path.isdir(args.clips):
            clip_files = sorted([
                os.path.join(args.clips, f)
                for f in os.listdir(args.clips)
                if f.endswith(".mp4")
            ])
            print(f"📂 使用现有片段目录: {len(clip_files)} 个片段")
        else:
            print(f"🔍 检索本周精彩镜头（{week_str}）...")
            this_week_videos = get_indexed_videos_this_week()
            if this_week_videos:
                print(f"   本周有 {len(this_week_videos)} 个视频索引: {', '.join(this_week_videos)}")
            else:
                print("   未找到本周视频索引，检索全部...")

            top_per_query = max(2, args.clips_count // len(queries))
            clips = search_clips_by_queries(
                queries,
                top_per_query=top_per_query,
                video_filter=args.video_filter or ""
            )
            clips = clips[:args.clips_count]

            if not clips:
                print("❌ 未找到任何片段，请先运行 jav_video_embed.py 建立索引")
                sys.exit(1)

            print(f"\n✂️  提取 {len(clips)} 个精彩片段（{args.clip_duration}s/个）...")
            clips_dir = os.path.join(tmpdir, "clips")
            clip_files = extract_clips(clips, clips_dir, args.clip_duration)

        if not clip_files:
            print("❌ 没有可用片段")
            sys.exit(1)

        # Step 2: 加转场
        if not args.no_fade:
            print(f"\n🎬 添加淡入淡出转场...")
            clip_files = add_crossfade(clip_files, tmpdir)

        # Step 3: 合并
        print(f"\n🎞️  合并 {len(clip_files)} 个片段...")
        raw_output = os.path.join(tmpdir, "raw.mp4")
        merge_clips(clip_files, raw_output, args.bgm or "", tmpdir)

        # Step 4: 加标题
        print(f"🏷️  添加标题卡...")
        add_title_card(raw_output, week_str, len(clip_files), output_path)

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"\n🎉 周精选合成完成！")
    print(f"   输出: {output_path}")
    print(f"   大小: {size_mb:.1f} MB")
    print(f"   片段: {len(clip_files)} 个")

if __name__ == "__main__":
    main()
