#!/usr/bin/env python3
"""Observe original ANM random variable reads and child births; no state writes.

Run from /work with the existing original assets:
  python3 tools/anm_rng_probe.py --frames 3 --out artifacts/th13-longrun-20261029/anm-rng-probe
The output is a diagnostic Unicorn run, not browser or GPU certification.
"""
import argparse
import json
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_ESI, UC_X86_REG_ESP
from oracle.battle_oracle import BattleOracle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frames', type=int, default=3)
    parser.add_argument('--out', default='artifacts/th13-longrun-20261029/anm-rng-probe')
    parser.add_argument('--replay')
    parser.add_argument('--boot-seed', type=int, default=0)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    oracle = BattleOracle('games/th13/th13.exe', 'artifacts/reference/raw',
                          os.path.join(args.out, 'draws.jsonl'))
    oracle.target_frames = args.frames
    if args.replay:
        from oracle.replayfmt import ReplayFile
        oracle.replay_bytes = Path(args.replay).read_bytes()
        oracle.replay = ReplayFile().decode(oracle.replay_bytes)
        oracle._replay_file_name = os.path.basename(args.replay)
        oracle.replay_stage = oracle.replay.stages[0]
        oracle.native_replay = True
        oracle.retail_loader = True
        oracle.difficulty = oracle.replay.rank & 3
        oracle.character = oracle.replay.character & 3
        oracle.shot_type = oracle.replay.shot_type
        oracle.boot_seed = args.boot_seed
        oracle.draw_log = False
    rows = []
    def slot(file):
        return [n for n in range(26) if oracle.rd32(oracle.anm_mgr + 0xb77b34 + 4*n) == file]
    def u16(addr):
        return struct.unpack('<H', oracle.uc.mem_read(addr, 2))[0]
    def f32(addr):
        return struct.unpack('<f', oracle.uc.mem_read(addr, 4))[0]
    def floating(uc, addr, size, unused):
        sp = uc.reg_read(UC_X86_REG_ESP)
        vm = uc.reg_read(UC_X86_REG_EAX)
        variable = f32(sp + 4)
        if variable not in (10010., 10011., 10012., 10030., 10031., 10032.):
            return
        file = oracle.rd32(vm + 0x30)
        pc = oracle.rd32(vm + 0x28)
        rows.append(dict(frame=oracle.frame, kind='float', variable=variable,
                         vm=vm, script=u16(vm+0x4aa), bank=slot(file),
                         pc=pc, caller=oracle.rd32(sp), instruction=bytes(uc.mem_read(pc, 20)).hex()))
    def integer(uc, addr, size, unused):
        if uc.reg_read(UC_X86_REG_EAX) != 10022:
            return
        vm = uc.reg_read(UC_X86_REG_ECX)
        sp = uc.reg_read(UC_X86_REG_ESP)
        file = oracle.rd32(vm + 0x30)
        pc = oracle.rd32(vm + 0x28)
        rows.append(dict(frame=oracle.frame, kind='integer', variable=10022,
                         vm=vm, script=u16(vm+0x4aa), bank=slot(file),
                         bound=oracle.rd32(vm+0x4dc), pc=pc, caller=oracle.rd32(sp),
                         instruction=bytes(uc.mem_read(pc, 20)).hex()))
    def birth(uc, addr, size, unused):
        sp = uc.reg_read(UC_X86_REG_ESP)
        parent = uc.reg_read(UC_X86_REG_ESI)
        child = oracle.rd32(sp+8)
        if child != 16:
            return
        file = oracle.rd32(parent+0x30)
        rows.append(dict(frame=oracle.frame, kind='child', child=child,
                         parent=parent, parent_script=u16(parent+0x4aa), bank=slot(file),
                         translation=[f32(parent+0x574), f32(parent+0x578)],
                         caller=oracle.rd32(sp)))
    for address, fn in ((0x461e80, floating), (0x4621b0, integer), (0x46f990, birth)):
        oracle.uc.hook_add(UC_HOOK_CODE, fn, begin=address, end=address)
    oracle.run(frames=args.frames)
    with open(os.path.join(args.out, 'anm-rng.jsonl'), 'w') as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(',', ':')) + '\n')
    print('events=%d crash=%s output=%s' % (len(rows), oracle.crash_frame, args.out))
    return 1 if oracle.crash_frame is not None else 0

if __name__ == '__main__':
    sys.exit(main())
