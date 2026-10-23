#!/usr/bin/env python3
"""playfield 边界程序化测量（视觉基线几何校准用，2026-10-24 战役阶段 0.2）。

背景：视觉审计子代理目测 golden 与 ours 的 playfield 横向错位 ~90px，量级
恰似已修的 96px 原点 bug，但也可能暴露 golden 视频裁剪（crop=1440:1080:0:0）
缺 x 偏移。目测不可靠，本脚本用两种独立方法程序化裁定：

1. 运动带法：同一会话两帧的逐列/逐行平均绝对差。playfield 内精灵/弹幕/
   背景滚动帧间变化大，侧条 HUD 基本静止 → 高运动连续带 = playfield。
2. 静态边缘法：多帧均值图像的列向梯度。front.anm 侧条在 playfield 边界
   （期望 x=128/512）有稳定竖边 → 梯度峰 = 边界。

期望值（th13 原版布局）：playfield x∈[128,512]、y∈[16,464]，宽 384 高 448。

用法（容器内）：
  python3 scripts/measure_playfield.py --motion A.png B.png [--motion A2.png B2.png ...]
  python3 scripts/measure_playfield.py --static F1.png F2.png ...
输出：各方法判定的边界 + 与期望值的偏差，供裁剪参数修正计算。
"""
import argparse
import sys

import numpy as np
from PIL import Image


def load_gray(path: str) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("L"), dtype=np.float32)


def band_bounds(profile: np.ndarray, threshold: float) -> tuple[int, int, int]:
    """返回最长的连续超阈段 (left, right_exclusive, 超阈样本数)。"""
    above = profile > threshold
    best = (0, 0, 0)
    start = None
    for index, flag in enumerate(above):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            run = (start, index, int(above[start:index].sum()))
            if run[2] > best[2]:
                best = run
            start = None
    if start is not None:
        run = (start, len(above), int(above[start:].sum()))
        if run[2] > best[2]:
            best = run
    return best


def motion_measurement(first: str, second: str) -> dict:
    a, b = load_gray(first), load_gray(second)
    if a.shape != b.shape:
        raise SystemExit(f"shape mismatch: {first}{a.shape} vs {second}{b.shape}")
    diff = np.abs(a - b)
    # 变化像素占比剖面：背景滚动使 playfield 内每列/每行都有可观比例像素变化，
    # 侧条仅 HUD 数字行变化（占比极低）。比平均差更锐利、对弹幕稀疏不敏感。
    changed = diff > 15.0
    col = changed.mean(axis=0)
    row = changed.mean(axis=1)
    col_threshold = 0.10
    row_threshold = 0.10
    col_band = band_bounds(col, col_threshold)
    row_band = band_bounds(row, row_threshold)
    return {
        "frames": (first, second),
        "col_threshold": col_threshold,
        "col_band": col_band,
        "col_profile": col,
        "row_threshold": row_threshold,
        "row_band": row_band,
        "row_profile": row,
    }


def static_measurement(paths: list[str]) -> dict:
    images = [load_gray(path) for path in paths]
    mean = np.mean(images, axis=0)
    col_gradient = np.abs(np.diff(mean, axis=1)).mean(axis=0)  # 长度 width-1，样本 i 在列 i/i+1 间
    row_gradient = np.abs(np.diff(mean, axis=0)).mean(axis=1)
    col_peaks = np.argsort(col_gradient)[-8:]
    row_peaks = np.argsort(row_gradient)[-8:]
    return {
        "frames": tuple(paths),
        "col_peaks_sorted_by_strength": [
            (int(i), float(col_gradient[i])) for i in col_peaks[::-1]
        ],
        "row_peaks_sorted_by_strength": [
            (int(i), float(row_gradient[i])) for i in row_peaks[::-1]
        ],
    }


EXPECTED = {"left": 128, "right": 512, "top": 16, "bottom": 464}


def report_motion(result: dict) -> None:
    left, right, _ = result["col_band"]
    top, bottom, _ = result["row_band"]
    print(f"[运动带] {result['frames'][0]} vs {result['frames'][1]}")
    print(f"  列占比阈 {result['col_threshold']:.2f} → x∈[{left},{right}) 宽 {right-left}"
          f"（期望 [{EXPECTED['left']},{EXPECTED['right']}) 偏差左 {left-EXPECTED['left']:+d} 右 {right-EXPECTED['right']:+d}）")
    print(f"  行占比阈 {result['row_threshold']:.2f} → y∈[{top},{bottom}) 高 {bottom-top}"
          f"（期望 [{EXPECTED['top']},{EXPECTED['bottom']}) 偏差上 {top-EXPECTED['top']:+d} 下 {bottom-EXPECTED['bottom']:+d}）")
    profile = result["col_profile"]
    coarse = [float(profile[i:i + 16].mean()) for i in range(0, len(profile), 16)]
    print("  列占比 16px 粗化剖面: " + " ".join(f"{value:.2f}" for value in coarse))


def report_static(result: dict) -> None:
    print(f"[静态边] 均值 {len(result['frames'])} 帧")
    print(f"  列梯度 Top8（位置,强度）: {result['col_peaks_sorted_by_strength']}")
    print(f"  行梯度 Top8（位置,强度）: {result['row_peaks_sorted_by_strength']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--motion", nargs=2, action="append", default=[],
                        metavar=("A.png", "B.png"), help="运动带法帧对（可多次）")
    parser.add_argument("--static", nargs="+", default=[], metavar="F.png",
                        help="静态边缘法帧列表")
    args = parser.parse_args()
    if not args.motion and not args.static:
        parser.error("需要 --motion 或 --static 至少其一")
    for pair in args.motion:
        report_motion(motion_measurement(*pair))
    if args.static:
        report_static(static_measurement(args.static))


if __name__ == "__main__":
    main()
