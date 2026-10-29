#!/usr/bin/env python3
"""原版 s1 f3078 玩家受击取证（只读；0x443240 phase 机 / +0x65c 写点 / 碰撞来源）。

watch 集（th13.exe v1.00c，decomp 00443240.c=玩家 phase 状态机：
  case1 活动 / case2 死亡序列 / case4 deathbomb；+0x65c=phase，+0x668=phase 帧）：
  - capstone 扫 {0x443240,0x4424d0,0x441dd0,0x442960,0x419a90} 内 `[reg+0x65c]`
    写点指令 → 逐点 watch：帧/EIP/写值（死亡入口=写 2 的 PC）。
  - 0x419a90（伤害处理，:117 门 phase==2||0）入口：帧/返回址/栈参/phase。
  - 碰撞源入口 detail（f3040..3130）：0x40db10 敌弹 / 0x418000 0x4183f0
    0x418e00 0x419950 敌本体族 / 0x446870 AABB / 0x441dd0 玩家写入 / 0x442960
    玩家每帧更新——记录 调用方 ret（+源函数判定）与 ECX。
  - 逐帧样本（复用 oracle._state_log 行 + 追加 phase）：x/y/phase/power。
产物落 artifacts/youmu-lunatic-20261029/ignored/contact/（ignored）。

用法（容器内 /work）：
  python3 tools/youmu_contact_probe.py --replay games/replays/th13_udN01.rpy \
      --stage 0 --frames 3130 [--window 3040 3130] [--out DIR]
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EIP, UC_X86_REG_ESP  # noqa: E402
from oracle.battle_oracle import BattleOracle  # noqa: E402
from oracle.replayfmt import ReplayFile        # noqa: E402

OUT_DIR = "artifacts/youmu-lunatic-20261029/ignored/contact"
EXE = "games/th13/th13.exe"
PLAYER_PTR = 0x004C22C4
P_PHASE = 0x65C
P_PHASE_FRAME = 0x668
P_X, P_Y = 0x5B8, 0x5BC

F_DAMAGE = 0x00419A90        # 伤害处理（碰撞命中应用）
F_PLAYER_UPDATE = 0x00442960  # case1 每帧玩家更新
F_PLAYER_WRITER = 0x00441DD0  # 玩家结构逐帧写入
F_BOXES = 0x00443240          # phase 状态机
SCAN_FUNCS = [F_BOXES, 0x00442FD0 - 0xD90, 0x004424D0, F_PLAYER_WRITER,
              F_PLAYER_UPDATE, F_DAMAGE]
SCAN_FUNCS = [f for f in SCAN_FUNCS if f]
SCAN_RANGES = [(0x00443240, 0x00444140), (0x004424D0, 0x00442700),
               (0x00441DD0, 0x00442200), (0x00442960, 0x00442FD0),
               (0x00419A90, 0x00419E00), (0x00444A10, 0x00444CA0),
               (0x00444CA0, 0x00444E00)]
WATCH_SOURCES = {
    0x0040DB10: "enemy-bullet-update",
    0x00418000: "enemy-body-a", 0x004183F0: "enemy-body-b",
    0x00418E00: "enemy-body-c", 0x00419950: "enemy-body-d",
    0x00446870: "aabb-test", 0x00441DD0: "player-writer",
    0x00442960: "player-update",
}
# 0x444140=thiscall 命中测试(EAX=攻击物 xy 指针,栈[1]=半径,ret=来源)；
# 0x444CA0=受击入口(phase=4 deathbomb)；0x444A10=死亡确定(phase=2)。
WATCH_FUNCS = {
    0x00444140: "hit-test", 0x00444CA0: "hit-entry-phase4",
    0x00444A10: "death-commit-phase2",
    0x00444260: "laser-hit-test",
}
FUNC_NAMES = {}


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def rd_u32(uc, addr):
    return struct.unpack("<I", bytes(uc.mem_read(addr, 4)))[0]


def rd_i32(uc, addr):
    return struct.unpack("<i", struct.pack("<I", rd_u32(uc, addr)))[0]


def rd_f32(uc, addr):
    return struct.unpack("<f", bytes(uc.mem_read(addr, 4)))[0]


def scan_65c_writes():
    """capstone 扫描 SCAN_RANGES 内 mov [reg+0x65c] 写点（含 2/4/0/1 立即）。"""
    import capstone
    raw = open(EXE, "rb").read()
    from oracle.pe import PE
    pe = PE(raw)
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    md.detail = True
    sites = []
    for lo, hi in SCAN_RANGES:
        off = pe.rva_to_offset(lo - pe.image_base)
        code = raw[off:off + (hi - lo)]
        for ins in md.disasm(code, lo):
            if ins.mnemonic == "mov" and "ptr [" in ins.op_str and \
                    "0x65c]" in ins.op_str and ins.op_str.split(",")[0].strip().startswith(("dword ptr [", "qword ptr [")):
                sites.append({"va": ins.address, "bytes": ins.mnemonic + " " + ins.op_str,
                              "next": ins.address + ins.size})
    return sites


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", default="games/replays/th13_udN01.rpy")
    ap.add_argument("--stage", type=int, default=0)
    ap.add_argument("--frames", type=int, default=3130)
    ap.add_argument("--window", type=int, nargs=2, default=[3040, 3130])
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    replay = ReplayFile().decode(open(args.replay, "rb").read())
    stage = replay.stages[args.stage]
    tag = "s%d" % (args.stage + 1)
    out_state = os.path.join(args.out, "contact-state-%s.jsonl" % tag)

    oracle = BattleOracle(EXE, "artifacts/reference/raw",
                          os.path.join(args.out, "draws-%s.jsonl" % tag))
    oracle.replay = replay
    oracle.replay_stage = stage
    oracle.replay_stage_index = args.stage
    oracle._replay_file_name = os.path.basename(args.replay)
    oracle.difficulty = replay.rank & 3
    oracle.character = replay.character & 3
    oracle.shot_type = replay.shot_type
    oracle.target_frames = args.frames

    def bf():
        return (oracle.frame - oracle.battle_first
                if oracle.battle_first is not None else None)

    write_sites = scan_65c_writes()
    for site in write_sites:
        # 静态解析写值：源操作数是立即数时直接记录；寄存器源留 None（值经 trace 帧序读出）。
        src = site["bytes"].split(",")[1].strip() if "," in site["bytes"] else ""
        site["value"] = int(src, 16) if src.startswith("0x") else \
            (int(src) if src.isdigit() else None)
        FUNC_NAMES[site["va"]] = "65c-write"
    SITE_BY_VA = {s["va"]: s for s in write_sites}

    phase_writes, damage_calls, source_calls = [], [], []
    hit_tests, entries = [], []
    win_lo, win_hi = args.window

    def on_phase_write(machine):
        uc = machine.uc
        eip = uc.reg_read(UC_X86_REG_EIP)
        player = rd_u32(uc, PLAYER_PTR)
        site = SITE_BY_VA.get(eip, {})
        phase_writes.append({
            "frame": oracle.frame, "bf": bf(), "eip": "%08x" % eip,
            "writes": site.get("str"), "value_static": site.get("value"),
            "phase_pre": rd_i32(uc, player + P_PHASE) if player else None,
            "phase_frame": rd_i32(uc, player + P_PHASE_FRAME) if player else None,
            "x": rd_f32(uc, player + P_X) if player else None,
            "y": rd_f32(uc, player + P_Y) if player else None,
        })

    def on_damage(machine):
        uc = machine.uc
        esp = uc.reg_read(UC_X86_REG_ESP)
        ret = rd_u32(uc, esp)
        player = rd_u32(uc, PLAYER_PTR)
        row = {"frame": oracle.frame, "bf": bf(),
               "ret": "%08x" % ret, "ret_off": "%+d" % (ret - 0x00443240),
               "args": [rd_u32(uc, esp + 4 + 4 * i) for i in range(4)]}
        if player:
            row["phase"] = rd_i32(uc, player + P_PHASE)
        damage_calls.append(row)

    def on_source(name):
        def cb(machine):
            f = bf()
            if f is None or not (win_lo <= f <= win_hi):
                return
            uc = machine.uc
            esp = uc.reg_read(UC_X86_REG_ESP)
            source_calls.append({"frame": oracle.frame, "bf": f, "src": name,
                                 "ret": "%08x" % rd_u32(uc, esp),
                                 "ecx": uc.reg_read(UC_X86_REG_ECX),
                                 "args": [rd_u32(uc, esp + 4 + 4 * i) for i in range(2)]})
        return cb

    def on_hit_test(machine):
        uc = machine.uc
        esp = uc.reg_read(UC_X86_REG_ESP)
        ret = rd_u32(uc, esp)
        obj = uc.reg_read(UC_X86_REG_EAX)  # thiscall: in_EAX=攻击物 xy 指针
        args = [rd_u32(uc, esp + 4 + 4 * i) for i in range(3)]
        player = rd_u32(uc, PLAYER_PTR)
        hit_tests.append({
            "frame": oracle.frame, "bf": bf(), "ret": "%08x" % ret,
            "eax": obj,
            "obj_xy": [rd_f32(uc, obj), rd_f32(uc, obj + 4)] if obj else None,
            "radius_f": f32(args[0]), "radius_bits": args[0],
            "args_f": [f32(a) for a in args],
            "phase": rd_i32(uc, player + P_PHASE) if player else None,
        })

    def on_entry(name):
        def cb(machine):
            uc = machine.uc
            esp = uc.reg_read(UC_X86_REG_ESP)
            player = rd_u32(uc, PLAYER_PTR)
            entries.append({"frame": oracle.frame, "bf": bf(), "fn": name,
                            "ret": "%08x" % rd_u32(uc, esp),
                            "phase": rd_i32(uc, player + P_PHASE) if player else None})
        return cb

    samples = []
    inner = oracle._write_state_sample

    def sampled(battle_f):
        inner(battle_f)
        uc = oracle.uc
        player = rd_u32(uc, PLAYER_PTR)
        samples.append({
            "bf": battle_f,
            "x": rd_f32(uc, player + P_X) if player else None,
            "y": rd_f32(uc, player + P_Y) if player else None,
            "phase": rd_i32(uc, player + P_PHASE) if player else None,
            "phase_frame": rd_i32(uc, player + P_PHASE_FRAME) if player else None,
            "power": rd_u32(uc, 0x004BE7E8),
        })

    oracle._write_state_log = open(out_state, "w")
    oracle._state_log = oracle._write_state_log
    oracle._write_state_sample = sampled

    oracle.m.watch(F_DAMAGE, on_damage)
    for addr, name in WATCH_FUNCS.items():
        oracle.m.watch(addr, on_hit_test if name == 'hit-test' else on_entry(name))
    for site in write_sites:
        oracle.m.watch(site["va"], on_phase_write)
    for addr, name in WATCH_SOURCES.items():
        oracle.m.watch(addr, on_source(name))

    print("CONTACT probe: tag=%s stage=%d frames=%d window=%s 65c_write_sites=%s"
          % (tag, args.stage, args.frames, args.window,
             ["%08x %s" % (s["va"], s["bytes"]) for s in write_sites]))
    oracle.run(frames=args.frames)

    def dump(name, rows):
        path = os.path.join(args.out, name)
        with open(path, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("WROTE", path, len(rows))

    dump("hit-tests-%s.jsonl" % tag, hit_tests)
    dump("death-entries-%s.jsonl" % tag, entries)
    dump("phase-writes-%s.jsonl" % tag, phase_writes)
    dump("damage-calls-%s.jsonl" % tag, damage_calls)
    dump("source-calls-%s.jsonl" % tag, source_calls)
    dump("frame-samples-%s.jsonl" % tag, samples)
    print("CONTACT done: frame=%s battle_first=%s phase_writes=%d damage=%d sources=%d"
          % (oracle.frame, oracle.battle_first, len(phase_writes),
             len(damage_calls), len(source_calls)))


if __name__ == "__main__":
    main()
