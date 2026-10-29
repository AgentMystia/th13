"""0x461830 音频请求设备模型单测（真实 Unicorn 内存，不 mock 内存）。

模型证据：
  0x461830 只入队 {code,value,name}（00461830.c:17-30）；
  worker 0x460dd0 case1 → FUN_00460650(value) 把 name 写入
  0x4e3640 + value*0x400（00460650.c:17,43）；
  case2 → value>=0 取预载表 [value]、value<0 取请求 name，写入
  0x4e13c0（00460dd0.c:60-80 / 反汇编 0x4611ad-0x4611d9）。
本测试直接调用生产方法，读写真实 Unicorn 页；负控证明未预载时模型
不会伪造曲名（0x4e13c0 保持空串并登记诊断）。
"""
import struct
import unittest

from unicorn import Uc, UC_ARCH_X86, UC_MODE_32

from oracle.battle_oracle import (AUDIO_CURRENT_BGM, AUDIO_PRELOAD_SLOTS,
                                  AUDIO_PRELOAD_STRIDE, AUDIO_PRELOAD_TABLE,
                                  BattleOracle)
from oracle.render_oracle import RenderOracle


class _Harness:
    """只借用生产方法 + 真实 Uc 内存的最小宿主。"""

    cstr = RenderOracle.cstr
    _complete_audio_request = BattleOracle._complete_audio_request

    def __init__(self):
        self.uc = Uc(UC_ARCH_X86, UC_MODE_32)
        self.uc.mem_map(0x004E0000, 0x6000)
        self.frame = 7
        self.audio_diagnostics = []

    def cstr_at(self, address):
        return bytes(self.cstr(address))


class AudioRequestModelTest(unittest.TestCase):
    def setUp(self):
        self.harness = _Harness()

    def test_code1_fills_preload_and_code2_selects_table(self):
        self.harness._complete_audio_request(1, 0, b"th13_01.wav")
        self.assertEqual(
            self.harness.cstr_at(AUDIO_PRELOAD_TABLE), b"th13_01.wav")
        # code2 的 EDI 名在 value>=0 时被 worker 忽略：必须取预载表。
        self.harness._complete_audio_request(2, 0, b"dummy")
        self.assertEqual(
            self.harness.cstr_at(AUDIO_CURRENT_BGM), b"th13_01.wav")
        self.assertEqual(self.harness.audio_diagnostics, [])

    def test_preload_stride_is_0x400_and_slot_index_selects(self):
        self.harness._complete_audio_request(1, 0, b"th13_01.wav")
        self.harness._complete_audio_request(1, 1, b"th13_03.wav")
        self.assertEqual(
            self.harness.cstr_at(AUDIO_PRELOAD_TABLE +
                                 AUDIO_PRELOAD_STRIDE), b"th13_03.wav")
        self.harness._complete_audio_request(2, 1, b"dummy")
        self.assertEqual(
            self.harness.cstr_at(AUDIO_CURRENT_BGM), b"th13_03.wav")

    def test_code2_negative_value_uses_request_name(self):
        self.harness._complete_audio_request(2, 0xFFFFFFFF, b"th13_00.wav")
        self.assertEqual(
            self.harness.cstr_at(AUDIO_CURRENT_BGM), b"th13_00.wav")
        self.assertEqual(self.harness.audio_diagnostics, [])

    def test_code2_without_preload_is_empty_and_diagnosed(self):
        # 负控：没有 code1 预载时，worker 会把空串拷到 0x4e13c0；模型
        # 不得用请求名兜底（否则 0x405a20 的 strchr 崩溃被掩盖）。
        self.harness._complete_audio_request(2, 0, b"dummy")
        self.assertEqual(self.harness.cstr_at(AUDIO_CURRENT_BGM), b"")
        self.assertEqual(len(self.harness.audio_diagnostics), 1)
        self.assertIn("empty", self.harness.audio_diagnostics[0][1])

    def test_code1_out_of_range_is_not_written(self):
        self.harness._complete_audio_request(1, AUDIO_PRELOAD_SLOTS,
                                             b"th13_99.wav")
        self.assertEqual(
            self.harness.cstr_at(AUDIO_PRELOAD_TABLE +
                                 (AUDIO_PRELOAD_SLOTS - 1) *
                                 AUDIO_PRELOAD_STRIDE), b"")
        self.assertEqual(len(self.harness.audio_diagnostics), 1)
        self.assertIn("outside", self.harness.audio_diagnostics[0][1])
        self.harness._complete_audio_request(1, 0xFFFFFFFF, b"th13_99.wav")
        self.assertEqual(
            self.harness.cstr_at(AUDIO_PRELOAD_TABLE), b"")
        self.assertEqual(len(self.harness.audio_diagnostics), 2)

    def test_current_bgm_write_is_nul_terminated(self):
        self.harness._complete_audio_request(1, 0, b"th13_01.wav")
        self.harness._complete_audio_request(2, 0, b"dummy")
        raw = bytes(self.harness.uc.mem_read(AUDIO_CURRENT_BGM, 16))
        self.assertEqual(raw, b"th13_01.wav\0\0\0\0\0")
        self.assertEqual(
            struct.unpack("<I", bytes(self.harness.uc.mem_read(
                AUDIO_PRELOAD_TABLE + 12, 4)))[0] & 0xFF, 0)


if __name__ == "__main__":
    unittest.main()
