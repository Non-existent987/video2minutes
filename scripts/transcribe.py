# -*- coding: utf-8 -*-
"""
video2minutes · Step 1: 视频/音频 -> 本地离线转写

用法:
    python transcribe.py 输入.mp4                 # 输出到 ./_transcribe
    python transcribe.py 输入.mp4 -o 输出目录
    python transcribe.py 输入.mp4 --reconv        # 强制重新解码音频

依赖: ffmpeg 在 PATH 中; 见 requirements.txt
流程: ffmpeg 取音轨(16k mono wav, 断点缓存) -> FunASR 本地识别
      -> transcript.json (原始结果) + transcript_ts.txt (带时间戳分段)
全程不联网上传任何音频, 仅首次运行时从 ModelScope 下载模型。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

# 模型缓存目录。Windows 下务必用短路径:
# 深目录 + ModelScope 长快照名会撞 260 字符上限, 报 WinError 206。
DEFAULT_CACHE = os.path.join(os.path.expanduser("~"), "v2m_cache")

MODEL_ASR = "paraformer-zh"   # Paraformer-large 中文 ASR (seaco 热词版)
MODEL_VAD = "fsmn-vad"        # 端点检测
MODEL_PUNC = "ct-punc-c"      # 标点预测


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def extract_audio(src, wav_path, force=False):
    """ffmpeg 取音轨 -> 16kHz 单声道 wav。已存在则跳过(断点续跑)。"""
    if os.path.exists(wav_path) and not force:
        log(f"wav 已存在, 跳过解码 (需要重解码请加 --reconv)")
        return
    if shutil.which("ffmpeg") is None:
        sys.exit("未找到 ffmpeg, 请安装并加入 PATH")
    log("ffmpeg 提取音轨 (16kHz mono)...")
    subprocess.run(
        ["ffmpeg", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav_path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def fmt_ts(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def main():
    ap = argparse.ArgumentParser(description="视频/音频 -> 本地离线转写")
    ap.add_argument("input", help="视频或音频文件 (mp4/mkv/mp3/wav/...)")
    ap.add_argument("-o", "--outdir", default="_transcribe", help="输出目录")
    ap.add_argument("--cache", default=DEFAULT_CACHE, help="模型缓存目录")
    ap.add_argument("--reconv", action="store_true", help="强制重新解码音频")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.cache, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.input))[0]
    wav_path = os.path.join(args.outdir, "meeting_16k.wav")

    extract_audio(args.input, wav_path, force=args.reconv)

    log("加载语音识别模型 (首次运行会下载, 需数分钟)...")
    os.environ["MODELSCOPE_CACHE"] = args.cache
    from funasr import AutoModel

    model = AutoModel(
        model=MODEL_ASR,
        vad_model=MODEL_VAD,
        punc_model=MODEL_PUNC,
        vad_kwargs={"max_single_segment_time": 60000},
        disable_update=True,
        hub="ms",
        device="cpu",
    )

    log("开始转写 (CPU 约为音频时长的 1/8, GPU 更快)...")
    res = model.generate(
        input=wav_path,
        batch_size_s=300,
        hotword="",
        sentence_timestamp=True,
    )
    item = res[0]

    json_path = os.path.join(args.outdir, "transcript.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    # 带时间戳的分段文本: 优先用 sentence_info, 退化用整段
    ts_path = os.path.join(args.outdir, "transcript_ts.txt")
    lines = []
    sentences = item.get("sentence_info") or []
    if sentences:
        for s in sentences:
            text = s.get("text", "").strip()
            if not text or not any(ch.isalnum() for ch in text):
                continue  # 滤掉只剩标点的静默段
            lines.append(
                f"[{fmt_ts(s['start'])} - {fmt_ts(s['end'])}] {text}"
            )
    else:
        text = item.get("text", "").strip()
        if text:
            lines.append(text)

    with open(ts_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    log(f"完成: {json_path}")
    log(f"      {ts_path}  (共 {len(lines)} 段)")


if __name__ == "__main__":
    main()
