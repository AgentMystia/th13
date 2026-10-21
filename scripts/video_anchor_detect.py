#!/usr/bin/env python3
"""video_anchor_detect.py —— 在指定窗口内逐帧精确检测锚点帧号

用法：python3 scripts/video_anchor_detect.py <窗口起>,<窗口止> <模式> [参数]
模式：
  dev   —— 离开暗平台：play 区均值首次越过 暗平台均值+lead（开局淡入）
  spike —— 全帧差尖峰：取窗口内 diff 最大帧（对话框消失之类的大切换）
  leave —— 离开静态平台：整幅 diff 首次持续 > th（静态段→下一场景第一变化帧）
  jump  —— 区域均值跳变：区域名 topbar/face_r/dlgbox 首次越过 base+lead
输出：检测帧号与局部曲线（供人工复核）。
"""
import subprocess
import sys

import numpy as np

W, H = 160, 120
REG = {
    "play": (slice(0, 120), slice(0, 96)),
    "hud": (slice(0, 120), slice(96, 160)),
    "topbar": (slice(0, 12), slice(0, 96)),
    "face_r": (slice(24, 96), slice(64, 96)),
    "dlgbox": (slice(96, 120), slice(0, 96)),
}


def decode(a, b):
    """解码 [a,b] 帧为 160x120 灰度堆（-ss 快速 seek，已验证与 trim 全解码逐字节一致）。"""
    n = b - a + 1
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{a / 60.0:.6f}",
           "-i", "artifacts/reference/video/th13-speedrun.mp4",
           "-vf", f"crop=1440:1080:0:0,scale={W}:{H},format=gray",
           "-frames:v", str(n),
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    assert len(raw) >= n * W * H, f"decode short: {len(raw)} < {n*W*H}"
    return np.frombuffer(raw[: n * W * H], np.uint8).reshape(n, H, W).astype(np.float32), n


def main():
    a, b = (int(x) for x in sys.argv[1].split(","))
    mode = sys.argv[2]
    fs, n = decode(a, b)
    diff = np.abs(np.diff(fs, axis=0)).mean(axis=(1, 2))
    idx = np.arange(a, b + 1)

    if mode == "spike":
        k = int(np.argmax(diff))
        print(f"spike: 帧号 {a + k + 1}（diff={diff[k]:.2f}，跳变发生在该帧）")
        for i in range(max(0, k - 6), min(n - 1, k + 6)):
            print(f"  {idx[i]:6d} diff={diff[i]:6.2f}")
        return

    if mode == "dev":   # 离开暗平台
        reg = REG[sys.argv[3]]
        lead = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
        curve = fs[:, reg[0], reg[1]].mean(axis=(1, 2))
        base = curve[:8].mean()
        hit = None
        for i in range(len(curve)):
            if curve[i] > base + lead:
                hit = idx[i]
                break
        print(f"dev({sys.argv[3]}): 暗平台={base:.2f} +{lead} → 首越帧 {hit}")
        for i in range(0, min(len(curve), 60)):
            print(f"  {idx[i]:6d} {curve[i]:6.2f}")
        return

    if mode == "leave":  # 离开静态平台
        th = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
        hold = int(sys.argv[4]) if len(sys.argv) > 4 else 3
        hit = None
        for i in range(len(diff) - hold):
            if all(diff[i + j] > th for j in range(hold)):
                hit = idx[i + 1]
                break
        print(f"leave(th={th},hold={hold}): 首变化帧 {hit}")
        for i in range(0, min(len(diff), 80)):
            print(f"  {idx[i]:6d} diff={diff[i]:6.2f}")
        return

    if mode == "jump":
        reg = REG[sys.argv[3]]
        lead = float(sys.argv[4]) if len(sys.argv) > 4 else 3.0
        curve = fs[:, reg[0], reg[1]].mean(axis=(1, 2))
        base = curve[:8].mean()
        hit = None
        for i in range(len(curve)):
            if abs(curve[i] - base) > lead:
                hit = idx[i]
                break
        print(f"jump({sys.argv[3]}): 平台={base:.2f} ±{lead} → 首越帧 {hit}")
        for i in range(0, min(len(curve), 70)):
            print(f"  {idx[i]:6d} {curve[i]:6.2f}")
        return


if __name__ == "__main__":
    main()
