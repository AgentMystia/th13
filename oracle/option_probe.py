#!/usr/bin/env python3
"""option（卫星）数组逐帧取证——自机卫星追尾弹重建战役第一步。

每帧 dump player+0xa230..+0xa2b0（4 个 option，0xe4 步长：+0/+4 位、
+0x4c(=0xa27c) 角度域）+ 帧内 SHOT 对拍行。目的：定位 option 轨道
更新模型（谁写 +0xa230/4/+0xa27c——轨道公式）与卫星出膛读值。

用法（容器内 /work）：
  TH13_LIFE_PROBE=1 python3 -m oracle.option_probe <rpy> [帧数] [起始帧]
"""
import struct
import sys

from . import stage1_oracle as _S1
from .stage1_oracle import Stage1Oracle, DAT_PLAYER

OPT_BASE = 0xA230
OPT_STRIDE = 0xE4
OPT_COUNT = 4


def main(argv):
    rpy = argv[1] if len(argv) > 1 else "games/replays/th13_udSr01.rpy"
    frames = int(argv[2]) if len(argv) > 2 else 130
    start = int(argv[3]) if len(argv) > 3 else 100

    orig_apply = Stage1Oracle.apply_shots

    def probe_apply(self):
        if start <= self.frame <= frames:
            uc = self.uc
            player = struct.unpack(
                "<I", bytes(uc.mem_read(DAT_PLAYER, 4)))[0]
            row = []
            for i in range(OPT_COUNT):
                base = player + OPT_BASE + i * OPT_STRIDE
                vals = struct.unpack("<4f", bytes(uc.mem_read(base, 16)))
                ang = struct.unpack(
                    "<f", bytes(uc.mem_read(base + 0x4C, 4)))[0]
                row.append("i%d=(%.6g,%.6g,%.6g)ang=%.6g"
                           % (i, vals[0], vals[1], vals[2], ang))
            print("OPT f=%d player=%#x %s"
                  % (self.frame, player, " | ".join(row)))
        return orig_apply(self)

    Stage1Oracle.apply_shots = probe_apply
    from .replayfmt import ReplayFile
    oracle = Stage1Oracle("games/th13/th13.exe", "artifacts/reference/raw",
                          "/tmp/option-probe-events.jsonl")
    oracle.replay = ReplayFile().decode(open(rpy, "rb").read())
    oracle.replay_stage_index = 0
    return oracle.run(frames)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
