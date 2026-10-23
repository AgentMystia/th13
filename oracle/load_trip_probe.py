#!/usr/bin/env python3
"""装载趟驱动序列取证（f0 tfactor 残留建模）。

包装 RenderOracle.drive：按调用序打印驱动地址/参数/当时帧号，配合
TH13_TFACTOR_PROBE 的 W/O 行还原「装载期每次驱动各自做了什么」——
定位 pre-f0 的 8 次 TFACTOR 写入发生在哪个驱动、装载趟共 tick 几次池。

用法（容器内 /work）：
  TH13_TFACTOR_PROBE=1 python3 -m oracle.load_trip_probe [帧数=3]
"""
import os
import sys

from . import render_oracle as _RO
from .render_oracle import RenderOracle

NAMES = {
    0x418E00: "MGR_UPDATE",
    0x46E510: "ANM_LOAD_TICK",
}


def main(argv):
    frames = int(argv[1]) if len(argv) > 1 else 3
    seq = {"n": 0}
    orig_drive = RenderOracle.drive

    def logged_drive(self, address, args=(), regs=None, timeout_ms=120_000):
        seq["n"] += 1
        sys.stderr.write("DRIVE #%d addr=%#x %s frame=%d args=%s\n" % (
            seq["n"], address, NAMES.get(address, ""), self.frame,
            [hex(a) for a in args]))
        sys.stderr.flush()
        return orig_drive(self, address, args=args, regs=regs,
                          timeout_ms=timeout_ms)

    RenderOracle.drive = logged_drive
    ro = RenderOracle("games/th13/th13.exe", "artifacts/reference/raw",
                      "/tmp/load-trip-draws.jsonl")
    RenderOracle.drive = orig_drive  # run() 内部经 self.drive 走包装
    ro.drive = logged_drive.__get__(ro)
    return ro.run(frames=frames)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
