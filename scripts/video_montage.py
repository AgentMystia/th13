#!/usr/bin/env python3
"""video_montage.py —— 从速通视频按帧号抽取小图并拼成审查拼图

用法：python3 scripts/video_montage.py <帧号逗号列表> <输出png>
每帧：游戏区裁剪(0,0,1440,1080) → 213×120，横向拼一行（每 8 张换行）。
"""
import subprocess
import sys
import tempfile

from PIL import Image

W, H = 213, 120
COLS = 8


def grab(vid, frame, tmpdir):
    t = frame / 60.0
    out = f"{tmpdir}/f{frame:06d}.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", vid,
         "-frames:v", "1", "-vf", f"crop=1440:1080:0:0,scale={W}:{H}", "-y", out],
        check=True)
    return out


def main():
    vid = "artifacts/reference/video/th13-speedrun.mp4"
    frames = [int(x) for x in sys.argv[1].split(",")]
    out_path = sys.argv[2]
    with tempfile.TemporaryDirectory() as td:
        paths = [grab(vid, f, td) for f in frames]
        imgs = [Image.open(p) for p in paths]
    rows = (len(imgs) + COLS - 1) // COLS
    sheet = Image.new("RGB", (COLS * W, rows * H), (20, 20, 20))
    for i, im in enumerate(imgs):
        sheet.paste(im, ((i % COLS) * W, (i // COLS) * H))
    sheet.save(out_path)
    print(" ->", out_path, " 帧号:", frames)


if __name__ == "__main__":
    main()
