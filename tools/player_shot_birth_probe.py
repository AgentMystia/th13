#!/usr/bin/env python3
"""Read-only retail probe for H-BIRTH-ORDER; writes captures under artifacts/."""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oracle.battle_oracle import BattleOracle, capture_exit_code
from oracle.replayfmt import ReplayFile
from unicorn import UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_ECX, UC_X86_REG_ESP


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def quantize(value):
    import math
    return f32(math.floor(f32(value * 100.0)) / 100.0)


class BirthProbe(BattleOracle):
    def install_hooks(self):
        super().install_hooks()
        self.births = {}
        self.pending_spawn = None
        self.pending_update = None
        self.counts = Counter()

        def rd(addr, fmt="f"):
            return struct.unpack("<" + fmt, bytes(self.uc.mem_read(addr, 4)))[0]

        def snapshot(node):
            # 00445c40 stores the SHT record at +0x94 and the independent
            # 004457e0 damage entry at +0x98; these are different objects.
            record = rd(node + 0x94, "I")
            damage_entry = rd(node + 0x98, "I")
            return {
                "node": node, "age": rd(node + 0x1c, "i"),
                "record": record, "damage_entry": damage_entry,
                "x": rd(node + 0x2c), "y": rd(node + 0x30),
                "angle": rd(node + 0x48), "speed": rd(node + 0x44),
                "vx": rd(node + 0x60), "vy": rd(node + 0x64),
                "callback": rd(record + 0x2c, "I"),
                "damage": rd(node + 0x84, "i"),
                "damage_state": {
                    "x": rd(damage_entry + 0x2c), "y": rd(damage_entry + 0x30),
                    "angle": rd(damage_entry + 0x1c),
                    "damage": rd(damage_entry + 0x84, "i"),
                } if damage_entry else None,
                "damage_entry_fields": {
                    "p10": rd(damage_entry + 0x10, "I"),
                    "p14": rd(damage_entry + 0x14, "I"),
                    "p18": rd(damage_entry + 0x18, "I"),
                    "p1c": rd(damage_entry + 0x1c, "I"),
                    "p20": rd(damage_entry + 0x20, "I"),
                    "p24": rd(damage_entry + 0x24, "I"),
                    "p28": rd(damage_entry + 0x28, "I"),
                    "p2c": rd(damage_entry + 0x2c, "I"),
                } if damage_entry else None,
            }

        def frame():
            return self.frame - self.battle_first if self.battle_first is not None else -1

        def emit(record):
            self.capture.write(json.dumps(record, separators=(",", ":")) + "\n")

        def spawn_entry(uc, addr, size, data):
            if not 0 <= frame() < self.target_frames:
                return
            esp = uc.reg_read(UC_X86_REG_ESP)
            record, anchor_ptr = rd(esp + 4, "I"), rd(esp + 12, "I")
            anchor_index = struct.unpack("<b", bytes(uc.mem_read(record + 0x20, 1)))[0]
            if anchor_index:
                player = rd(0x4c22c4, "I")
                anchor = [rd(player + 0xa230 + anchor_index * 0xe4 + axis * 4, "i") / 128.0
                          for axis in range(2)]
            else:
                anchor = [rd(anchor_ptr), rd(anchor_ptr + 4)]
            self.pending_spawn = {
                "frame": frame(), "node": uc.reg_read(UC_X86_REG_EAX),
                "anchor": anchor, "offset": [rd(record + 4), rd(record + 8)],
            }

        def spawn_return(uc, addr, size, data):
            if self.pending_spawn is None:
                return
            entry, self.pending_spawn = self.pending_spawn, None
            born = snapshot(entry["node"])
            expected = [f32(offset - born[velocity] + anchor)
                        for offset, anchor, velocity in zip(entry["offset"], entry["anchor"], ("vx", "vy"))]
            exact = [born["x"], born["y"]] == expected
            self.counts["births"] += 1
            self.counts["birth_exact" if exact else "birth_mismatch"] += 1
            self.births[entry["node"]] = entry
            emit({"kind": "birth", **entry, "state": born, "expected": expected, "exact": exact})

        def update_entry(uc, addr, size, data):
            self.pending_update = None
            if not 0 <= frame() < self.target_frames:
                return
            before = snapshot(uc.reg_read(UC_X86_REG_ECX))
            if before["age"] == 0:
                self.pending_update = {"frame": frame(), "before": before, "phases": []}

        def update_phase(name):
            def capture_phase(uc, addr, size, data):
                if self.pending_update is not None:
                    entry = self.pending_update
                    entry["phases"].append({"phase": name, "state": snapshot(entry["before"]["node"])})
            return capture_phase

        def update_return(uc, addr, size, data):
            if self.pending_update is None:
                return
            entry, self.pending_update = self.pending_update, None
            before = entry["before"]
            after = snapshot(before["node"])
            expected = [quantize(f32(before[axis] + after[velocity]))
                        for axis, velocity in (("x", "vx"), ("y", "vy"))]
            exact = [after["x"], after["y"]] == expected
            birth = self.births.get(before["node"])
            same_frame = birth is not None and birth["frame"] == entry["frame"]
            self.counts["age_zero_updates"] += 1
            self.counts["move_exact" if exact else "move_mismatch"] += 1
            self.counts["same_frame" if same_frame else "unpaired"] += 1
            callback = "straight" if before["callback"] == 0 else "callback"
            self.counts[callback] += 1
            expected_order = (["callback"] if before["callback"] else []) + ["polar", "move", "quantized"]
            order_exact = [phase["phase"] for phase in entry["phases"]] == expected_order
            self.counts["order_exact" if order_exact else "order_mismatch"] += 1
            damage_exact = after["damage_state"] == {
                "x": after["x"], "y": after["y"], "angle": after["angle"], "damage": after["damage"],
            }
            self.counts["damage_exact" if damage_exact else "damage_mismatch"] += 1
            emit({"kind": "age_zero_update", **entry, "after": after,
                  "expected": expected, "exact": exact, "same_frame_birth": same_frame,
                  "order_exact": order_exact, "damage_exact": damage_exact})

        # Retail v1.00c: spawn return after node stores; update caller after CALL.
        self.uc.hook_add(UC_HOOK_CODE, spawn_entry, begin=0x445c40, end=0x445c40)
        self.uc.hook_add(UC_HOOK_CODE, spawn_return, begin=0x445f8f, end=0x445f8f)
        self.uc.hook_add(UC_HOOK_CODE, update_entry, begin=0x4464d0, end=0x4464d0)
        # 004464fd calls SHT+0x2c; 00446507 calls polar; 0044650c calls
        # move/quantize; 00446511 observes its result before damage synchronization.
        for address, name in ((0x4464fd, "callback"), (0x446507, "polar"),
                              (0x44650c, "move"), (0x446511, "quantized")):
            self.uc.hook_add(UC_HOOK_CODE, update_phase(name), begin=address, end=address)
        self.uc.hook_add(UC_HOOK_CODE, update_return, begin=0x4467ad, end=0x4467ad)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", default="games/th13/th13.exe")
    parser.add_argument("--raw", default="artifacts/reference/raw")
    parser.add_argument("--replay", default="games/replays/th13_01.rpy")
    parser.add_argument("--frames", type=int, default=130)
    parser.add_argument("--out", default="artifacts/verification/longrun-20261026/combat/original")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    artifact_root = (Path(__file__).resolve().parents[1] / "artifacts").resolve()
    if artifact_root not in out.parents:
        parser.error("--out must be beneath ignored artifacts/")
    out.mkdir(parents=True, exist_ok=True)
    probe = BirthProbe(args.exe, args.raw, str(out / "oracle-draws.jsonl"))
    probe.replay = ReplayFile().decode(Path(args.replay).read_bytes())
    probe.replay_stage = probe.replay.stages[0]
    probe._replay_file_name = Path(args.replay).name
    probe.difficulty = probe.replay.rank & 3
    probe.character = probe.replay.character & 3
    probe.shot_type = probe.replay.shot_type
    probe.target_frames = args.frames
    with (out / "oracle-birth.jsonl").open("w") as capture:
        probe.capture = capture
        probe.run(args.frames)
    stats = probe.cut_battle_window()
    stats["edge_mismatches"] = probe.replay_qualification["edge_mismatches"]
    summary = {"hypothesis": "H-BIRTH-ORDER", "oracle": stats, "capture": dict(probe.counts)}
    (out / "oracle-birth-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return int(bool(capture_exit_code(stats) or probe.counts["birth_mismatch"]
                    or probe.counts["move_mismatch"] or probe.counts["unpaired"]
                    or probe.counts["order_mismatch"] or probe.counts["damage_mismatch"]
                    or probe.counts["births"] != probe.counts["age_zero_updates"]
                    or not probe.counts["straight"] or not probe.counts["callback"]))


if __name__ == "__main__":
    raise SystemExit(main())
