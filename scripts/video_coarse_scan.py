#!/usr/bin/env python3
"""video_coarse_scan.py —— 速通视频粗扫（场景切换/对话框/血条特征时间序列）

输入：artifacts/reference/video/th13-speedrun.mp4（1920×1080@60 CFR）
处理：ffmpeg 裁掉右侧速通面板（游戏区 x∈[0,1440) 全高）后缩到 160×120 灰度，
      逐帧计算区域特征并落盘 npz，供锚点定位用。
输出：npz 含每帧特征 + 起止帧号；另存若干特征曲线供人工核查。

区域（160×120 坐标，游戏画面 4:3）：
  play   : x∈[0,96)   —— 主视野（640×480 中 x<384）
  hud    : x∈[96,160) —— 右侧 HUD（分数/残机/灵力）
  topbar : y∈[0,12) x∈[0,96) —— Boss 血条出现区（th13 血条在主视野顶部）
  face_r : x∈[64,96) y∈[24,96) —— 对话立绘区（对话框出现时右侧大幅变亮）
  dlgbox : x∈[0,96) y∈[96,120) —— 对话文本框区（底部）
"""
import subprocess
import sys

import numpy as np

W, H = 160, 120
CMD = [
    "ffmpeg", "-v", "error", "-i", sys.argv[1],
    "-vf", f"crop=1440:1080:0:0,scale={W}:{H},format=gray",
    "-f", "rawvideo", "-pix_fmt", "gray", "-",
]

REG = {
    "play": (slice(0, 120), slice(0, 96)),
    "hud": (slice(0, 120), slice(96, 160)),
    "topbar": (slice(0, 12), slice(0, 96)),
    "face_r": (slice(24, 96), slice(64, 96)),
    "dlgbox": (slice(96, 120), slice(0, 96)),
}

def main():
    out_path = sys.argv[2]
    proc = subprocess.Popen(CMD, stdout=subprocess.PIPE, bufsize=10 ** 8)
    feats = {k: [] for k in list(REG) + ["mean", "std", "diff"]}
    prev = None
    n = 0
    while True:
        buf = proc.stdout.read(W * H)
        if len(buf) < W * H:
            break
        f = np.frombuffer(buf, np.uint8).reshape(H, W).astype(np.float32)
        for k, (ys, xs) in REG.items():
            feats[k].append(float(f[ys, xs].mean()))
        feats["mean"].append(float(f.mean()))
        feats["std"].append(float(f.std()))
        feats["diff"].append(float(np.abs(f - prev).mean()) if prev is not None else 0.0)
        prev = f
        n += 1
        if n % 20000 == 0:
            print(f"  ...{n} frames", file=sys.stderr)
    proc.wait()
    arrs = {k: np.asarray(v, np.float32) for k, v in feats.items()}
    np.savez_compressed(out_path, frames=n, **arrs)
    print(f"frames={n} -> {out_path}")

if __name__ == "__main__":
    main()
