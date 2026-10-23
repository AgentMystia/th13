#!/usr/bin/env python3
"""video_golden_extract.py —— 视频帧↔游戏帧对齐拟合 + golden 参考帧抽取

前置结论（见 artifacts/reference/golden/README.md「时间轴对齐」＋2026-10-24
几何校准勘误，证据=scripts/measure_playfield.py + 原始帧列梯度三点锚定拟合
tmp/fit_mapping.py，残差 ≤0.2px）：
  * 视频内游戏窗口映射：video_x = 2.2556 × game_x − 219.7 —— **录像源缺失
    游戏左侧 ~97px**（视频 x=0 即 game x≈97.4），游戏窗口右边缘在视频
    x=1224；1224..1440 为速通者叠加信息区、1440..1520 黑分隔、1520 起速通
    面板。旧版「游戏区=全宽 1440」为误判。
  * 抽取几何：crop=1224:1080:0:0 + scale=544:480（缩放比 0.4444 不变）——
    golden 帧 544×480 = 游戏真坐标 x∈[97,640]，逐像素对齐 ours 截图的
    x∈[98,640]（±1px）。游戏 x<97 与比对协议见 golden/README.md。
  * 视频从标题菜单开始：视频帧号 ≠ 游戏帧号。游戏帧号以
    games/replays/th13_01.rpy 一面输入流为基准（帧 0=一面开始），
    关卡累计帧由各 stage.frame_count 累加。
  * 拟合锚点（游戏帧, 视频帧, 检测方式, 权重）见下方 ANCHORS；
    斜率应为 1（CFR 60 无掉帧），截距即全程恒定偏移。

用法（容器内 /work）：
  python3 scripts/video_golden_extract.py            # 拟合 + 抽帧
  python3 scripts/video_golden_extract.py --fit-only # 仅拟合
"""
import subprocess
import sys

import numpy as np

VIDEO = "artifacts/reference/video/th13-speedrun.mp4"
OUT = "artifacts/reference/golden"

# ---- 锚点表：game=游戏帧（rpy 输入流/oracle 事件），video=视频帧 ----
# type: hard=硬切换（±3），fade=淡入首变化（偏差约 -8，见 README），lag=ECL 特效滞后（仅核对）
ANCHORS = [
    # (game, video, 说明, 权重sigma)
    (0,     407,  "一面开始（黑场→内容起点；淡入偏差）",        6.0),
    (2381,  2806, "道中Boss(Kyouko)生成+Ecl_EtBreak；画面变暗起始滞后≈25帧", None),  # lag: 不进拟合
    (5746,  6143, "对话关闭动画起始（输入流 Z 边沿 5746）",      4.0),
    (11675, 12076, "二面开始（结算→二面 硬切尖峰）",             3.0),
    (27823, 28224, "三面开始（硬切尖峰）",                       3.0),
    (47231, 47633, "四面开始（淡入首变化）",                     6.0),
    (65056, 65463, "五面开始（淡入首变化）",                     6.0),
    (85537, 85940, "六面开始（淡入首变化）",                     6.0),
]

# ---- golden 抽取清单：游戏帧 → 场景说明 ----
KEY_FRAMES = [
    (0,     "一面开场第 0 帧（黑场起步，一面输入流起点）"),
    (61,    "首波 GirlRedA01 生成帧（oracle enemy_spawn）"),
    (121,   "首波第四只（GirlRedA01，画面中部可见）"),
    (901,   "GirlRedA02 新波次（oracle enemy_spawn）"),
    (1242,  "GirlBlueA02/GirlRedA02 密集波（oracle enemy_spawn）"),
    (1671,  "GirlB02 大妖精波开始（oracle enemy_spawn）"),
    (2381,  "道中Boss 幽谷响子生成 + Ecl_EtBreak 清弹（oracle）"),
    (2450,  "道中Boss 入场中（画面变暗段）"),
    (5494,  "Boss 战前对话首帧（对话框出现，视频帧 5895）"),
    (5806,  "Boss 战开始：血条滑入（对应 setBoss 视觉）"),
]

# Boss 第一非符：血条滑入(视频6207)+61帧（udSr01 oracle 512→513 间隔）→ 视频 6268
BOSS_NONSPELL_GAME = 5867
BOSS_RUN = 64          # 连续帧数
BOSS_RUN_START = BOSS_NONSPELL_GAME - 11   # 覆盖起拍前后

TITLE_MENU_VIDEO = 150  # 标题菜单（视频帧号命名，不属游戏帧时间轴）


def fit():
    pts = [(g, v, s) for g, v, _, s in ANCHORS if s is not None]
    g = np.array([p[0] for p in pts], float)
    v = np.array([p[1] for p in pts], float)
    w = 1.0 / np.array([p[2] for p in pts], float) ** 2
    # 加权最小二乘 v = a + b*g
    A = np.vstack([np.ones_like(g), g]).T
    W = np.diag(w)
    cov = np.linalg.inv(A.T @ W @ A)
    (a, b), *_ = np.linalg.lstsq(np.sqrt(W) @ A, np.sqrt(W) @ v, rcond=None)
    pred = a + b * g
    resid = v - pred
    sig_b = np.sqrt(cov[1, 1])
    print("== 拟合：video = a + b × game（加权，含淡入锚点）")
    print(f"   a={a:.2f}  b={b:.6f} ± {sig_b:.6f}")
    game_of = [p[0] for p in pts]
    for gg, vv, note, ss in ANCHORS:
        if ss is None:
            print(f"   [lag] g{gg:6d} v{vv:6d}  残差 {vv - (a + b * gg):+7.1f}  {note}")
        else:
            i = [p[0] for p in pts].index(gg)
            print(f"   g{gg:6d} v{vv:6d}  残差 {resid[i]:+7.1f}  σ={ss}  {note}")
    # 纯硬切锚点对（二/三面尖峰）——斜率应精确为 1
    b_hard = (28224 - 12076) / (27823 - 11675)
    print(f"   硬切对斜率 = {b_hard:.6f}（二面↔三面尖峰，16148/16148）")
    return a, b


def grab(video_frame, out_path):
    """按视频帧号抽 544×480 PNG：游戏窗口裁剪（x∈[0,1224)，右缘=游戏 x=640，
    左缘=game x≈97.4——录像源缺失游戏左侧 ~97px，见文件头几何校准勘误）+
    lanczos 缩放（0.4444，与旧版一致）。产出=游戏真坐标 x∈[97,640]。"""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{video_frame / 60.0:.6f}", "-i", VIDEO,
         "-frames:v", "1",
         "-vf", "crop=1224:1080:0:0,scale=544:480:flags=lanczos",
         "-y", out_path], check=True)


def main():
    fit_only = "--fit-only" in sys.argv
    a, b = fit()
    offset = a + b * 0  # 帧换算用 a（b≈1）
    print(f"\n换算：video = game + {a:.1f}（b={b:.6f}）")
    if fit_only:
        return
    import os
    os.makedirs(OUT, exist_ok=True)
    for gframe, note in KEY_FRAMES:
        v = int(round(a + b * gframe))
        p = f"{OUT}/frame_{gframe:06d}.png"
        grab(v, p)
        print(f"  {p}  ← 视频帧 {v}   {note}")
    # 标题菜单（视频帧号命名）
    grab(TITLE_MENU_VIDEO, f"{OUT}/menu_title_v{TITLE_MENU_VIDEO:06d}.png")
    print(f"  {OUT}/menu_title_v{TITLE_MENU_VIDEO:06d}.png  ← 视频帧 {TITLE_MENU_VIDEO}  标题菜单")
    # Boss 1非连续帧
    for i in range(BOSS_RUN):
        gframe = BOSS_RUN_START + i
        v = int(round(a + b * gframe))
        grab(v, f"{OUT}/frame_{gframe:06d}.png")
    print(f"  Boss 1非连续 {BOSS_RUN} 帧：game {BOSS_RUN_START}..{BOSS_RUN_START + BOSS_RUN - 1}"
          f"（视频 {int(round(a + b * BOSS_RUN_START))}..{int(round(a + b * (BOSS_RUN_START + BOSS_RUN - 1)))}）")


if __name__ == "__main__":
    main()
