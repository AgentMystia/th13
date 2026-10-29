"""Live stage and scene observations must follow the original memory."""
import unittest
import struct
from types import SimpleNamespace

from oracle.replay_state import sample, semantic_sample
from tools.batch_replay_oracle import replay_version_tags


class FakeOracle:
    replay_consumed = 12
    replay_stage = type("Stage", (), {"number": 1})()
    uc = None

    def __init__(self):
        self.memory = {0x004BE81C: 2, 0x004DCC20: 7,
                       0x004DCC24: 9, 0x004C2194: 0x500000,
                       0x500060: 0x400}

    def rd32(self, address):
        return self.memory.get(address, 0)


class ReplayLiveStateTest(unittest.TestCase):
    def test_replay_version_is_read_only_from_user_metadata(self):
        replay = SimpleNamespace(user_blocks=[b"info\r\nVersion 1.00a\r\n",
                                              b"comment Version 9.99z"])
        self.assertEqual(replay_version_tags(replay), ["1.00a"])
        self.assertEqual(replay_version_tags(SimpleNamespace(user_blocks=[])), [])

    def test_live_stage_and_scene_are_read_after_transition(self):
        oracle = FakeOracle()
        self.assertEqual(semantic_sample(oracle, 12)["stage"], 2)
        # No entity managers exist in this fixture, so mem_read is unused.
        oracle.uc = type("Memory", (), {"mem_read": lambda *_: b""})()
        raw = sample(oracle, 12)
        self.assertEqual(raw["current_stage"], 2)
        self.assertEqual(raw["scene"], {"current": 7, "next": 9,
                          "gameinf_pointer": 0x500000, "gameinf_flags": 0x400})
        oracle.memory[0x004C2194] = 0
        self.assertIsNone(sample(oracle, 13)["scene"]["gameinf_flags"])

    def test_active_damage_registration_keeps_all_flag_bits(self):
        oracle = FakeOracle()
        player = 0x600000
        oracle.memory[0x004C22C4] = player
        pool = bytearray(256 * 0x9C)
        struct.pack_into("<I", pool, 3 * 0x9C + 0x10, 5)

        class Memory:
            def mem_read(self, address, size):
                if address == player + 0xA9E4:
                    return bytes(pool)
                return bytes(size)

        oracle.uc = Memory()
        entries = sample(oracle, 1)["damage_registrations"]
        self.assertEqual([(entry["slot"], entry["10"]) for entry in entries],
                         [(3, 5)])


if __name__ == "__main__":
    unittest.main()
