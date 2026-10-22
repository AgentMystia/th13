#!/usr/bin/env python3
"""P0 弹迹对拍探针（python 孪生侧）：跑 udSr01 前 130 帧，打印每次出膛与敌 2 命中。"""
import sys
sys.path.insert(0, "/work/oracle")
from replayfmt import ReplayFile
from replay_model import PlayerModel

rpy = ReplayFile().decode(open("/work/games/replays/th13_udSr01.rpy", "rb").read())
stage = rpy.stages[0]
sht = open("/work/artifacts/reference/raw/pl02a.sht", "rb").read()
model = PlayerModel(sht)
print("块表：", [[(r["interval"], r["phase"], r["x"], r["y"], r["damage"]) for r in b] for b in model.blocks[:2]])

for frame in range(1, 131):
    state = stage.frame(frame - 1)[0]
    x, y = model.step_move(state)
    before = len(model.shots)
    model.step_fire(state, frame)
    fired = model.shots[before:]
    if fired and frame >= 95:
        print(f"f={frame} state={state:#04x} pos=({x:.2f},{y:.2f}) 出膛={[(round(s[0],2), round(s[1],2), s[2]) for s in fired]}")
    # 弹步进（step_fire 内部已做）；敌 2 轨迹近似由事件文件提供，这里只打印弹位
    if frame in (118, 121, 124):
        print(f"f={frame} 活弹数={len(model.shots)} 前5弹={[(round(s[0],2), round(s[1],2)) for s in model.shots[:5]]}")
