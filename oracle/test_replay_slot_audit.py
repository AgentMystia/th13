"""Exercise original slot/cursor boundaries without modifying replay input."""
import io
import json
import struct
import unittest
from types import SimpleNamespace

from oracle.battle_oracle import (GAMEINF, INPUT_HELD, INPUT_PRESSED,
                                  INPUT_RELEASED, NativeReplayInputAudit)
from oracle.replayfmt import ReplayStage


class Memory:
    def __init__(self):
        self.bytes = {}

    def write(self, address, data):
        self.bytes.update({address + i: byte for i, byte in enumerate(data)})

    def mem_read(self, address, length):
        return bytes(self.bytes.get(address + i, 0) for i in range(length))


class Oracle:
    def __init__(self):
        self.uc = Memory()
        self.frame = 0
        self.battle_first = 0
        self.replay_edge_mismatches = []
        self._replay_input_log = io.StringIO()
        self.manager = 0x10000
        self.wr32(GAMEINF, 0x20000)
        self.replay = SimpleNamespace(stages=[])
        for number, frames in ((1, [(1, 1, 0), (1, 0, 0)]),
                               (2, [(2, 2, 1), (0, 0, 2)])):
            stage = ReplayStage()
            stage.number = number
            stage.frame_count = len(frames)
            stage.input = b"".join(struct.pack("<HHH", *item) for item in frames)
            self.replay.stages.append(stage)
            base = self.manager + (number * 5 + 0x19) * 8
            start, header = 0x30000 + number * 0x100, 0x40000 + number * 0x100
            self.uc.write(start, stage.input)
            self.wr32(base, start)
            self.wr32(base + 4, start)
            self.wr32(base + 8, start + len(stage.input))
            self.wr32(base + 0x10, header)
            self.wr32(header + 4, stage.frame_count)
        self.audit = NativeReplayInputAudit(self)

    def rd32(self, address):
        return struct.unpack("<I", self.uc.mem_read(address, 4))[0]

    def wr32(self, address, value):
        self.uc.write(address, struct.pack("<I", value & 0xffffffff))

    def consume(self, slot, index, *, advance=True):
        base = self.manager + (slot * 5 + 0x19) * 8
        self.wr32(self.manager + 0x218, slot)
        self.wr32(base + 0x14, index)
        self.wr32(base + 4, self.rd32(base) + 6 * index)
        self.audit.entry(self.manager)
        stage = self.replay.stages[slot - 1]
        held, pressed, released = stage.frame(index)
        for address, value in ((INPUT_HELD, held), (INPUT_PRESSED, pressed),
                               (INPUT_RELEASED, released)):
            self.wr32(address, value)
        if advance:
            self.wr32(base + 4, self.rd32(base + 4) + 6)
            self.wr32(base + 0x14, index + 1)
        self.audit.returned()
        self.frame += 1


class ReplaySlotAuditTest(unittest.TestCase):
    def test_complete_slot_then_new_slot_uses_new_file_frame_zero(self):
        oracle = Oracle()
        oracle.consume(1, 0)
        oracle.consume(1, 1)
        oracle.consume(2, 0)
        self.assertEqual(oracle.replay_edge_mismatches, [])
        self.assertEqual(oracle.audit.input_frames, 3)
        self.assertEqual(oracle.audit.slots, {1: 2, 2: 1})
        rows = [json.loads(line) for line in oracle._replay_input_log.getvalue().splitlines()]
        self.assertEqual([(row["slot"], row["slot_frame"]) for row in rows],
                         [(1, 0), (1, 1), (2, 0)])
        self.assertEqual(rows[-1]["expected"], [2, 2, 1])

    def test_early_switch_and_bad_cursor_remain_errors(self):
        oracle = Oracle()
        oracle.consume(1, 0, advance=False)
        oracle.consume(2, 0)
        reasons = [error[1] for error in oracle.replay_edge_mismatches]
        self.assertIn("replay-cursor-advance", reasons)
        self.assertIn("replay-slot-incomplete", reasons)

    def test_exhaustion_is_not_counted_as_input(self):
        oracle = Oracle()
        oracle.consume(1, 0)
        oracle.consume(1, 1)
        oracle.consume(1, 2, advance=False)
        self.assertEqual(oracle.audit.input_frames, 2)
        self.assertIn("stage-input-exhausted",
                      [error[1] for error in oracle.replay_edge_mismatches])


if __name__ == "__main__":
    unittest.main()
