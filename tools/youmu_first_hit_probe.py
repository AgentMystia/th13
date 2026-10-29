#!/usr/bin/env python3
"""Read-only retail probe: s1 f221 score first-difference (orig 14 vs port 13).

Evidence target: which player shot applies the first fairy's kill damage and
its per-frame trajectory/callback/anchor, via 0x446870 (shot update), the
damage write EIP 0x417a19 (inside 0x418e00), and the 0x419a90 kill chain.
Writes artifacts/youmu-lunatic-20261029/first-hit/.

Usage (container /work):
  python3 tools/youmu_first_hit_probe.py [--frames 235]
"""
import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from oracle.battle_oracle import BattleOracle, capture_exit_code
from oracle.replayfmt import ReplayFile
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.x86_const import (UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_EIP,
                               UC_X86_REG_ESP)

OUT = ROOT / "artifacts/youmu-lunatic-20261029/first-hit"

SHOT_UPDATE = 0x004464D0      # per-shot update (ECX = node, cf. birth probe)
DAMAGE_WRITE = 0x00417A19     # enemy life write inside 0x418e00
KILL_CHAIN = 0x00419A90       # enemy kill chain entry
FAIRY_LIFE = 0x056C9D58       # first fairy life (deterministic heap layout)
OPTION_UPDATE = 0x00445660    # per-option target/pos update (EAX = option obj)
HISTORY_BASE = 0x14738        # player-relative history[0] (00442960 write)
OPTION_TARGET_BASE = 0x14770  # player-relative: 0x14738 + 7*8 (entry 7)


def f32(raw):
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


class HitProbe(BattleOracle):
    def install_hooks(self):
        super().install_hooks()
        self.rows = []
        self.kills = []
        self.life_writes = []
        self._pending_kill = None
        self.capture.write("# schema th13/first-hit-probe/v1\n")

        def bf():
            return self.frame - (self.battle_first or 0)

        def in_window():
            return 0 <= bf() <= 92

        def rd(addr):
            return struct.unpack("<f", bytes(self.uc.mem_read(addr, 4)))[0]

        def snapshot(node):
            record = self.rd32(node + 0x94)
            entry = self.rd32(node + 0x98)
            row = {"node": "%x" % (node & 0xFFFFF), "age": self.rd32(node + 0x1c),
                   "x": round(rd(node + 0x2c), 3), "y": round(rd(node + 0x30), 3),
                   "angle": round(rd(node + 0x48), 4), "speed": round(rd(node + 0x44), 3),
                   "vx": round(rd(node + 0x60), 3), "vy": round(rd(node + 0x64), 3),
                   "damage": self.rd32(node + 0x84), "record": "%x" % (record & 0xFFFFF) if record else "0",
                   "dentry": "%x" % (entry & 0xFFFFF) if entry else "0"}
            if entry:
                row["de"] = {"x": round(rd(entry + 0x2c), 3), "y": round(rd(entry + 0x30), 3),
                             "age": self.rd32(entry + 0x1c), "damage": self.rd32(entry + 0x84),
                             "p10": self.rd32(entry + 0x10), "p14": self.rd32(entry + 0x14),
                             "p18": self.rd32(entry + 0x18), "p20": self.rd32(entry + 0x20)}
            return row

        # per-frame shot table at 0x4464d0 entry (ECX = node)
        def on_shot_update(uc, addr, size, ud):
            if not in_window():
                return
            node = uc.reg_read(UC_X86_REG_ECX)
            row = snapshot(node)
            row["bf"] = bf()
            self.rows.append(row)
        self.uc.hook_add(UC_HOOK_CODE, on_shot_update,
                         begin=SHOT_UPDATE, end=SHOT_UPDATE)

        # 00445660 entry (EAX = option object): the consumed target address is
        # player + 0x14770 + idx*0x40. Log it against the history array at
        # player + 0x14738 (entries of 8 bytes) to pin the index exactly.
        def on_option_update(uc, addr, size, ud):
            if not in_window():
                return
            player = self.rd32(0x004C22C4)
            if not player:
                return
            obj = uc.reg_read(UC_X86_REG_EAX)
            idx = self.rd32(obj + 0xD0)
            tgt = player + OPTION_TARGET_BASE + idx * 0x40

            def entry(k):
                a = player + HISTORY_BASE + k * 8
                return [self.rd32(a), self.rd32(a + 4)]
            emit({"bf": bf(), "kind": "option_target", "idx": idx,
                  "target_addr": "%x" % tgt,
                  "consumed": entry(7 + idx * 8),
                  "hist7": entry(7), "hist8": entry(8), "hist15": entry(15),
                  "option_pos": [self.rd32(obj + 0x54), self.rd32(obj + 0x58)],
                  "player_fixed": [self.rd32(player + 0x5C4),
                                   self.rd32(player + 0x5C8)]})
        self.uc.hook_add(UC_HOOK_CODE, on_option_update,
                         begin=OPTION_UPDATE, end=OPTION_UPDATE)

        # enemy life writes (damage application + kill)
        def on_life_write(uc, access, address, size, value, ud):
            if not in_window():
                return True
            self.life_writes.append({"bf": bf(), "addr": "%x" % address,
                                     "value": value & 0xFFFFFFFF,
                                     "eip": "%x" % uc.reg_read(UC_X86_REG_EIP)})
            return True
        self.uc.hook_add(UC_HOOK_MEM_WRITE, on_life_write,
                         begin=FAIRY_LIFE, end=FAIRY_LIFE + 3)

        # kill chain entry/return (0x419a90)
        def on_kill_entry(uc, addr, size, ud):
            if not in_window():
                return
            esp = uc.reg_read(UC_X86_REG_ESP)
            self._pending_kill = {"bf": bf(), "arg": self.rd32(esp + 4),
                                  "caller": self.rd32(esp)}
        self.uc.hook_add(UC_HOOK_CODE, on_kill_entry, begin=KILL_CHAIN,
                         end=KILL_CHAIN)

        def emit(record):
            self.capture.write(json.dumps(record, separators=(",", ":")) + "\n")

        def frame_end_marker(uc, addr, size, ud):
            if not in_window():
                return
            if self._pending_kill is not None:
                self.kills.append(self._pending_kill)
                emit({"kind": "kill_chain", **self._pending_kill})
                self._pending_kill = None
        # marker: after the kill chain returns — use the next 0x446000 dispatch
        self.uc.hook_add(UC_HOOK_CODE, frame_end_marker, begin=0x00446000,
                         end=0x00446000)


def verify_option_target():
    """Index evidence: 00445660 consumes player+0x14770+idx*0x40 ==
    history[7+idx*8] (00442960 writes history[0] at +0x14738, 8-byte
    entries) — NOT history[8+idx*8]. Plus capstone of 00445660."""
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32
    from oracle.pe import PE
    pe = PE((ROOT / "games/th13/th13.exe").read_bytes())
    base = pe.image_base

    def va2off(va):
        rva = va - base
        for s in pe.sections:
            if s.va <= rva < s.va + s.raw_size:
                return s.raw_offset + (rva - s.va)
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    lines = ["%08x  %-8s %s" % (i.address, i.mnemonic, i.op_str)
             for i in md.disasm(pe.data[va2off(0x445660):va2off(0x445660) + 122],
                                0x445660)]
    rows = [json.loads(l) for l in
            (OUT / "probe.jsonl").read_text().splitlines() if l.startswith("{")]
    ot = [r for r in rows if r.get("kind") == "option_target"]
    same7 = sum(1 for r in ot if r["consumed"] == r["hist7"])
    diff78 = sum(1 for r in ot if r["hist7"] != r["hist8"])
    result = {"disasm_00445660": lines,
              "option_target_rows": len(ot),
              "consumed_eq_hist7": "%d/%d" % (same7, len(ot)),
              "hist7_ne_hist8": "%d/%d" % (diff78, len(ot)),
              "samples": ot[:3]}
    (OUT / "option-index-verify.json").write_text(
        json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=235)
    parser.add_argument("--replay", default="games/replays/th13_udN01.rpy")
    parser.add_argument("--verify-option-target", action="store_true")
    args = parser.parse_args()
    if args.verify_option_target:
        return verify_option_target()
    OUT.mkdir(parents=True, exist_ok=True)
    probe = HitProbe("games/th13/th13.exe", "artifacts/reference/raw",
                     str(OUT / "probe-draws.jsonl"))
    replay = ReplayFile().decode(Path(args.replay).read_bytes())
    probe.replay = replay
    probe.replay_stage = replay.stages[0]
    probe._replay_file_name = Path(args.replay).name
    probe.difficulty = probe.replay.rank & 3
    probe.character = probe.replay.character & 3
    probe.shot_type = probe.replay.shot_type
    probe.target_frames = args.frames
    with (OUT / "probe.jsonl").open("w") as capture:
        probe.capture = capture
        probe.run(args.frames)
    summary = {
        "replay": args.replay, "frames": args.frames,
        "life_writes": probe.life_writes,
        "kill_chains": probe.kills,
        "shots_per_frame": {},
        "schema": "th13/first-hit-probe/v1",
    }
    for row in probe.rows:
        summary["shots_per_frame"].setdefault(row["bf"], []).append(row)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({"life_writes": probe.life_writes,
                      "kill_chains": probe.kills,
                      "frames_logged": sorted(summary["shots_per_frame"])},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
