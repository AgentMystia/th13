"""Replay top-header practice-stage field: real encode/decode + retail reference.

旧版单测把 xor_decrypt/zun_inflate 全部 mock 掉，只证明 Python 读了 0x70，
不能发现 XOR/LZ77/字段链回归。本文件改为：
  1. 用真实 xor_encrypt + 合法 MSB 位流 LZ77 编码器构造完整 .rpy，再走
     ReplayFile.decode 全链解码，断言 0x70 字段与 stage 链；
  2. 用仓库内零售 Replay（th13_01/udSr01/udN01）作合法参考，断言
     practice_stage 等于解压流 0x70 的原始字节，且不等于旧偏移 0x68
     （全关录像 0x68=8、0x70=-1，正是本次修订要区分的相邻字段）。
原版依据：0x447900.c:68 把 DAT_004be7d4 写入 +0x70；
C++ ReplayFile.cpp:188 同偏移；0x68 是另一个字段。
"""
import os
import struct
import unittest
from pathlib import Path

from oracle.replayfmt import (HEADER_SIZE, MAGIC, TOP_SIZE, STAGE_HEADER_SIZE,
                              ReplayFile, xor_decrypt, xor_encrypt,
                              zun_inflate)

ROOT = Path(__file__).resolve().parents[1]
RETAIL_REPLAYS = ("games/replays/th13_01.rpy",
                  "games/replays/th13_udSr01.rpy",
                  "games/replays/th13_udN01.rpy")


def _deflate_literals(plain):
    """合法 0x458f30 反方向编码：全字面量 + 地址 0 终止（MSB 位流）。"""
    bits = []

    def put(value, count):
        for shift in range(count - 1, -1, -1):
            bits.append((value >> shift) & 1)

    for byte in plain:
        put(1, 1)
        put(byte, 8)
    put(0, 1)
    put(0, 13)
    out = bytearray((len(bits) + 7) // 8)
    for index, bit in enumerate(bits):
        if bit:
            out[index >> 3] |= 0x80 >> (index & 7)
    return bytes(out)


def _encode_replay(plain):
    """完整 t13r 编码（XOR 0x7d/0x3a/0x100 后 XOR 0x5c/0xe1/0x400）。"""
    compressed = _deflate_literals(bytes(plain))
    body = xor_encrypt(compressed, 0x7D, 0x3A, 0x100)
    raw = xor_encrypt(body, 0x5C, 0xE1, 0x400)
    header = bytearray(HEADER_SIZE)
    header[:4] = MAGIC
    struct.pack_into("<H", header, 4, 2)
    struct.pack_into("<IIIIII", header, 0xC, HEADER_SIZE + len(raw),
                     0, 0, 0, len(raw), len(plain))
    return bytes(header) + raw


def _synthetic_plain(practice_stage, adjacent_0x68=8):
    frame_count = 2
    input_bytes = frame_count * 6
    events = (frame_count + 29) // 30
    blob_size = input_bytes + events
    plain = bytearray(TOP_SIZE + STAGE_HEADER_SIZE + blob_size)
    plain[:5] = b"TEST\0"
    struct.pack_into("<f", plain, 0x54, 1.0)
    plain[0x58] = 1
    struct.pack_into("<iii", plain, 0x5C, 2, 1, 3)
    struct.pack_into("<i", plain, 0x68, adjacent_0x68)
    struct.pack_into("<i", plain, 0x70, practice_stage)
    struct.pack_into("<HHII", plain, TOP_SIZE, 1, 0x1234, frame_count,
                     blob_size)
    struct.pack_into("<i", plain, TOP_SIZE + 0x1C, 777)
    base = TOP_SIZE + STAGE_HEADER_SIZE
    struct.pack_into("<HHH", plain, base, 1, 2, 4)
    plain[base + 6] = 0xAA
    return bytes(plain)


class ReplayPracticeFieldTest(unittest.TestCase):
    def test_practice_field_roundtrips_through_real_xor_and_lz77(self):
        plain = _synthetic_plain(practice_stage=4, adjacent_0x68=8)
        replay = ReplayFile().decode(_encode_replay(plain))
        self.assertEqual(replay.practice_stage, 4)
        self.assertEqual((replay.character, replay.shot_type, replay.rank),
                         (2, 1, 3))
        self.assertEqual(replay.stage_count, 1)
        self.assertEqual(len(replay.stages), 1)
        stage = replay.stages[0]
        self.assertEqual((stage.number, stage.stamp), (1, 0x1234))
        self.assertEqual(stage.frame_count, 2)
        self.assertEqual(stage.blob_size, 13)
        self.assertEqual(stage.frame(0), (1, 2, 4))
        self.assertEqual(stage.events, b"\x00")
        self.assertEqual(stage.score, 777)
        # 字段位置来自原始顶层头，不是测试自己重算的 Python 值。
        self.assertEqual(struct.unpack_from("<i", replay.top_header, 0x70)[0],
                         4)
        self.assertEqual(struct.unpack_from("<i", replay.top_header, 0x68)[0],
                         8)

    def test_practice_field_is_not_the_adjacent_top_header_value(self):
        # 0x68=8 与 0x70=-1 正是零售全关录像的相邻字段形状；若解码器仍读
        # 0x68 会得到 8 而不是 -1。
        plain = _synthetic_plain(practice_stage=-1, adjacent_0x68=8)
        replay = ReplayFile().decode(_encode_replay(plain))
        self.assertEqual(replay.practice_stage, -1)
        self.assertNotEqual(
            replay.practice_stage,
            struct.unpack_from("<i", replay.top_header, 0x68)[0])

    def test_retail_replays_match_raw_top_header_field(self):
        missing = [name for name in RETAIL_REPLAYS
                   if not os.path.isfile(ROOT / name)]
        if missing:
            self.skipTest("retail replays not available: %s" % missing)
        for name in RETAIL_REPLAYS:
            with self.subTest(replay=name):
                raw = (ROOT / name).read_bytes()
                replay = ReplayFile().decode(raw)
                # 独立按 0x448c40/0x472280/0x458f30 口径重解压流。
                body = xor_decrypt(
                    raw[HEADER_SIZE:HEADER_SIZE + replay.comp_size],
                    0x5C, 0xE1, 0x400)
                body = xor_decrypt(body, 0x7D, 0x3A, 0x100)
                plain = zun_inflate(body, replay.uncomp_size)
                self.assertEqual(replay.practice_stage,
                                 struct.unpack_from("<i", plain, 0x70)[0])
                self.assertEqual(replay.top_header, bytes(plain[:TOP_SIZE]))
                # 全关录像：0x68=8、0x70=-1。字段必须取后者。
                self.assertEqual(
                    struct.unpack_from("<i", plain, 0x68)[0], 8)
                self.assertEqual(replay.practice_stage, -1)


if __name__ == "__main__":
    unittest.main()
