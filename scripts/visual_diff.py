#!/usr/bin/env python3
"""visual_diff.py —— golden 与移植侧同帧 PNG 的分区像素比对

用法（经 th13-dev 容器，numpy/pillow 已备）：
  podman run --rm --userns=keep-id -v <工作区>:/work:Z -w /work \
      localhost/th13-dev bash -lc \
      'python3 scripts/visual_diff.py --golden artifacts/reference/golden/th13_udSr01-stage1 --ours <移植帧目录>'

比对约定：
  * 双方目录内同名 frame_%06d.png 为同一游戏帧（帧号即取像索引）；
  * 图像按 640×480 的东方原版布局分三区：
      顶栏 hud_top   = y ∈ [0, 32)     （分数/HiScore 一带）
      底栏 hud_bot   = y ∈ [448, 480)  （残机/灵力/Trance 槽一带）
      主视野 field   = 其余区域（自机/敌机/弹幕）
  * 指标 = 各区内逐像素 |a-b| 的平均（按 255 归一化到 [0,1]）；
    hud 为顶栏与底栏合并指标；
  * 末行输出机器可读判定行：
      VISUAL mean=<f> hud=<f> field=<f> pass=(yes|no)
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# ---- 阈值（归一化平均像素差；超任一即 fail）----
TH_MEAN = 0.02   # 全图
TH_HUD = 0.02    # 顶栏+底栏（HUD 文字与图标）
TH_FIELD = 0.02  # 主视野（弹幕/自机/背景）

# 640×480 布局的分区（顶栏 32px、底栏 32px）
TOP_H = 32
BOT_H = 32


def region_diff(a: np.ndarray, b: np.ndarray) -> float:
    """a/b 为同形状 uint8(H,W,3/4) 数组；返回归一化平均绝对差。"""
    if a.shape != b.shape:
        raise ValueError(f"尺寸不一致: {a.shape} vs {b.shape}")
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return float(diff.mean()) / 255.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--golden", required=True, type=Path, help="golden 帧目录")
    ap.add_argument("--ours", required=True, type=Path, help="移植侧帧目录")
    ap.add_argument("--frames", default=None,
                    help="仅比对指定帧号（逗号分隔），默认比对双方交集全部帧")
    args = ap.parse_args()

    def frames_of(d: Path):
        out = {}
        for p in sorted(d.glob("frame_*.png")):
            try:
                out[int(p.stem.split("_")[1])] = p
            except (IndexError, ValueError):
                continue
        return out

    gold = frames_of(args.golden)
    ours = frames_of(args.ours)
    if not gold:
        print(f"!! golden 目录无帧：{args.golden}", file=sys.stderr)
        return 2
    common = sorted(set(gold) & set(ours))
    if args.frames:
        want = [int(x) for x in args.frames.split(",")]
        common = [f for f in want if f in gold and f in ours]
    if not common:
        print("!! 双方无共同帧号", file=sys.stderr)
        return 2
    missing_ours = sorted(set(gold) - set(ours))
    if missing_ours:
        print(f"（提示：{len(missing_ours)} 个 golden 帧在 ours 缺失，"
              f"首尾：{missing_ours[:3]}…{missing_ours[-3:]}）")

    means, huds, fields = [], [], []
    h = None
    for f in common:
        ga = np.asarray(Image.open(gold[f]).convert("RGB"))
        ob = np.asarray(Image.open(ours[f]).convert("RGB"))
        h = ga.shape[0]
        top = slice(0, TOP_H)
        bot = slice(max(0, h - BOT_H), h)
        mid = slice(TOP_H, max(0, h - BOT_H))
        d_top = region_diff(ga[top], ob[top])
        d_bot = region_diff(ga[bot], ob[bot])
        d_mid = region_diff(ga[mid], ob[mid])
        # hud 合并：按两区像素数加权
        n_top = ga[top].size
        n_bot = ga[bot].size
        d_hud = (d_top * n_top + d_bot * n_bot) / (n_top + n_bot)
        means.append((d_top * n_top + d_bot * n_bot + d_mid * ga[mid].size) / ga.size)
        huds.append(d_hud)
        fields.append(d_mid)
        print(f"frame {f:6d}  mean={means[-1]:.5f} hud={d_hud:.5f} field={d_mid:.5f}")

    mean = max(means)
    hud = max(huds)
    field = max(fields)
    ok = mean <= TH_MEAN and hud <= TH_HUD and field <= TH_FIELD
    print(f"frames={len(common)} 阈值 mean/hud/field≤{TH_MEAN}/{TH_HUD}/{TH_FIELD} "
          f"（逐帧取最大值）")
    print(f"VISUAL mean={mean:.5f} hud={hud:.5f} field={field:.5f} "
          f"pass={'yes' if ok else 'no'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
