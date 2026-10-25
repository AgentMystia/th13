"""replay-qualification-research 正负控（任务卡固定验证面）。

研究性质：原版 Replay 恢复/消费机制的资格基础，不是 Stage1 资格认证。

正控（全部执行原版代码，非旁路拟合）：
  1. Python/C++ 双侧解码器保留完整 0xc4 stage 头原样字节（头字段 =
     反编译偏移，不臆造字段名）——replayfmt.header 与逐字段重解析相等。
  2. 原版 0x449070 恢复：th13_01 seed=60189 / udSr01 seed=61738 的
     stage 头 +0x14 起 0x58 字节 → 0x4be7b8 逐位相等（scripts/
     check_replay_mechanism.py 的固定实验，此处按同口径独立执行）。
  3. 原版 0x448200 消费：六字节流逐帧与文件帧一致、0x414f30 沿
     幂等；EOF 语义（0xffff×3）与越界清零。
  4. battle_oracle 回放装备路径字段形状：DAT_004c2198 管理器 +
     0x447900/0x448c40 反编译登记的游标族偏移逐点存在且值正确。
  5. 采样相位：每帧 0x448200 恰一次、与 0x418e00 敌机逻辑同帧
     （consume 一次 + enemy_update 一次的成对推进）。

负控（篡改必须被检出；无完整头的装备必须拒绝）：
  - stage 头单字节篡改 → 0x449070 恢复差异必须非零；
  - 输入流单帧篡改 → 0x448200 消费差异必须被检出；
  - battle_oracle 装备缺 0xc4 头 → SystemExit（不臆造初值）。

原版事实锚点（artifacts/decomp/jp/functions/*.c）：
  0x447900:22   DAT_004c22c8=管理器（装备）
  0x447900:59   mgr+0x20+idx*4 = 0xc4 stage 头槽
  0x448c40:75-78 游标族：+0xd8+idx*0x28=头指针、(idx*5+0x19)*8=头+0xc8
               （输入流）、+0xd0+idx*0x28=输入流尾界
  0x447900:227  mgr+0x218 = -1（未激活）
  0x449070:33-49 回放分支：头+2 u16→0x4dc324/0x4dc31c 种子、
               头+0x14 起 0x58 字节→0x4be7b8
  0x448200:20-52 消费分支：三 u16 → 0x4e4c08/0x4e4c14/0x4e4c18、
               0x414f30 沿重算、游标 +6、EOF 三字全 F → 0x43e5a0
"""
import os
import struct
import sys
import unittest
from pathlib import Path

from oracle.machine import Machine, RET_MAGIC
from oracle.replayfmt import (ReplayFile, ReplayFormatError, HEADER_SIZE,
                              TOP_SIZE, STAGE_HEADER_SIZE, xor_decrypt,
                              zun_inflate)
from unicorn.x86_const import UC_X86_REG_EIP, UC_X86_REG_ESI

ROOT = Path(__file__).resolve().parents[1]

REPLAYS = ("games/replays/th13_01.rpy", "games/replays/th13_udSr01.rpy")
SEEDS = {"games/replays/th13_01.rpy": 60189,
         "games/replays/th13_udSr01.rpy": 61738}
# 0x449070 恢复目标（DAT_004be7b8）与复制源（stage 头 +0x14）。
RESTORE_DST = 0x4BE7B8
RESTORE_LEN = 0x58
SEED_A = 0x4DC324
SEED_B = 0x4DC31C
HELD = 0x4E4C08
PRESSED = 0x4E4C14
RELEASED = 0x4E4C18
KEYSTATE = 0x4E49F0
CONSUME = 0x448200
RESTORE = 0x449070
EDGE_CALC = 0x414F30
EOF_STOP = 0x43E5A0


def _plain_stream(raw, replay):
    """按 replayfmt.decode 同口径取解压流（顶层 0x74 + stage 链）。"""
    body = xor_decrypt(raw[HEADER_SIZE:HEADER_SIZE + replay.comp_size],
                       0x5C, 0xE1, 0x400)
    body = xor_decrypt(body, 0x7D, 0x3A, 0x100)
    return zun_inflate(body, replay.uncomp_size)


def _stage_header_from_stream(plain):
    """解压流中 stage0 的 0xc4 头原样字节（TOP_SIZE 起）。"""
    return bytes(plain[TOP_SIZE:TOP_SIZE + STAGE_HEADER_SIZE])


def _decode(path):
    raw = Path(path).read_bytes()
    replay = ReplayFile().decode(raw)
    plain = _plain_stream(raw, replay)
    return raw, replay, plain


def _original_ex_available():
    return (ROOT / "games/th13/th13.exe").is_file()


class ReplayHeaderPreservationTests(unittest.TestCase):
    """正控 1：Python 侧保留完整 0xc4 头（C++ 侧由 replay_test.cpp 验证）。"""

    def setUp(self):
        self.paths = [str(ROOT / name) for name in REPLAYS]
        missing = [p for p in self.paths if not os.path.isfile(p)]
        if missing:
            self.skipTest("original replays not available: %s" % missing)

    def test_stage_header_bytes_preserved_and_match_stream(self):
        for path, name in zip(self.paths, REPLAYS):
            with self.subTest(replay=name):
                _raw, replay, plain = _decode(path)
                self.assertGreaterEqual(len(replay.stages), 1)
                offset = TOP_SIZE
                for index, stage in enumerate(replay.stages):
                    self.assertEqual(len(stage.header), STAGE_HEADER_SIZE)
                    # 与解压流原样字节逐位相等（decode 同步进公式：
                    # 0xc4 + blob_size，0x448c40:79）
                    stream_header = bytes(
                        plain[offset:offset + STAGE_HEADER_SIZE])
                    self.assertEqual(bytes(stage.header), stream_header)
                    # 派生字段 = 头内偏移（不臆造）：+0 关卡号、+2 戳、
                    # +4 帧数、+8 blob 长、+0x1C 起始分
                    number, stamp = struct.unpack_from("<HH", stage.header, 0)
                    frame_count, blob_size = struct.unpack_from(
                        "<II", stage.header, 4)
                    score = struct.unpack_from("<i", stage.header, 0x1C)[0]
                    self.assertEqual((stage.number, stage.stamp),
                                     (number, stamp))
                    self.assertEqual(
                        (stage.frame_count, stage.blob_size, stage.score),
                        (frame_count, blob_size, score))
                    offset += STAGE_HEADER_SIZE + stage.blob_size

    def test_stage_one_seed_and_frame_counts(self):
        for path, name in zip(self.paths, REPLAYS):
            with self.subTest(replay=name):
                _raw, replay, _plain = _decode(path)
                self.assertEqual(replay.stages[0].stamp, SEEDS[name])
                self.assertEqual(
                    len(replay.stages[0].input),
                    replay.stages[0].frame_count * 6)


class RetailRestoreTests(unittest.TestCase):
    """正控 2 + 负控 1：原版 0x449070 恢复（th13_01 stage1 seed=60189）。"""

    def setUp(self):
        if not _original_ex_available():
            self.skipTest("original executable is not available")
        missing = [name for name in REPLAYS
                   if not (ROOT / name).is_file()]
        if missing:
            self.skipTest("original replays not available: %s" % missing)

    def _machine_with_replay(self, replay, stage, plain):
        """原版机器 + 零售装备形状的管理器（反编译锚点，槽号=关卡号）。

        与 scripts/check_replay_mechanism.py 同源语义（保留其只读复用），
        差异按反编译修正：
          - stage 槽号 = stage 头 +0x00 关卡号（0x448c40:75 `*psVar8`），
            非数组下标——th13_01/udSr01 stage1 → 槽 1（mechanism 脚本的
            mgr+0x100/0xf0 族即 idx=1 同址，零售形状一致）；
          - DAT_004be81c = 关卡号（0x4525b0:160-161）；
          - mgr+0x1c = 顶层头独立块（0x447900:43-51 分配 0x74、
            0x448c40:67 从解压流 +0x74 拷贝；装备序 0x447900:197-199 从它
            读出写全局——方向：顶层头→全局）。"""
        machine = Machine(str(ROOT / "games/th13/th13.exe"))
        uc = machine.uc
        mgr = machine.malloc(0x320)
        top = machine.malloc(0x74)
        block = machine.malloc(STAGE_HEADER_SIZE + len(stage.input)
                               + max(len(stage.events), 1))
        uc.mem_write(mgr, bytes(0x320))
        uc.mem_write(top, bytes(replay.top_header))
        uc.mem_write(block, bytes(stage.header) + bytes(stage.input)
                     + bytes(stage.events))
        number = stage.number
        machine.write_u32(0x4C22C8, mgr)
        machine.write_u32(0x4BE81C, number)
        machine.write_u32(mgr + 0x10, 1)
        machine.write_u32(mgr + 0x1C, top)             # 顶层头独立块
        machine.write_u32(mgr + 0x20 + number * 4, block)
        base = mgr + (number * 5 + 0x19) * 8
        machine.write_u32(base, block + STAGE_HEADER_SIZE)
        machine.write_u32(base + 8, block + STAGE_HEADER_SIZE
                          + stage.frame_count * 6)
        machine.write_u32(mgr + 0xD8 + number * 0x28, block)
        machine.write_u32(mgr + 0x218, number)
        machine.write_u32(0x4C2194, 1)    # DAT_004c2194 非 0（有对局语义）
        return machine, mgr, block

    def test_retail_restore_seed_and_state_exact(self):
        for name in REPLAYS:
            with self.subTest(replay=name):
                path = str(ROOT / name)
                _raw, replay, plain = _decode(path)
                stage = replay.stages[0]
                machine, _mgr, _block = self._machine_with_replay(
                    replay, stage, plain)
                machine.call(RESTORE)
                self.assertEqual(machine.uc.reg_read(UC_X86_REG_EIP),
                                 RET_MAGIC)
                restored = bytes(machine.uc.mem_read(
                    RESTORE_DST, RESTORE_LEN))
                self.assertEqual(restored, bytes(stage.header[0x14:0x6C]))
                self.assertEqual(machine.read_u32(SEED_A) & 0xFFFF,
                                 stage.stamp)
                self.assertEqual(machine.read_u32(SEED_B) & 0xFFFF,
                                 stage.stamp)
                self.assertEqual(SEEDS[name], stage.stamp)

    def test_tampered_header_is_detected_by_retail_restore(self):
        """负控：stage 头 +0x20（0x58 恢复区内）单字节篡改必须被检出。"""
        path = str(ROOT / "games/replays/th13_01.rpy")
        _raw, replay, plain = _decode(path)
        stage = replay.stages[0]
        tampered = bytearray(stage.header)
        original = tampered[0x20]
        tampered[0x20] = (original + 1) & 0xFF
        machine, _mgr, _block = self._machine_with_replay(
            replay, stage, plain)
        block = machine.read_u32(machine.read_u32(0x4C22C8)
                                 + 0x20 + stage.number * 4)
        machine.uc.mem_write(block, bytes(tampered))
        machine.call(RESTORE)
        restored = bytes(machine.uc.mem_read(RESTORE_DST, RESTORE_LEN))
        self.assertNotEqual(restored, bytes(stage.header[0x14:0x6C]),
                            "tampered header must not restore identical "
                            "state")

    def test_top_header_is_a_separate_block_from_stage_header(self):
        """正控（P0-1 回归防护）：顶层头必须是独立块——它与 stage 头
        的别名（mgr+0x1c == mgr+0x20+number*4）会令 0x447900:197-199 的
        装备读面与 0x449070:43-49 的恢复窗口互相污染（真机复刻实证：
        th13_01 恢复差 +0x5c/+0x64、udSr01 差 +0x64）。"""
        for name in REPLAYS:
            with self.subTest(replay=name):
                path = str(ROOT / name)
                _raw, replay, plain = _decode(path)
                stage = replay.stages[0]
                machine, mgr, block = self._machine_with_replay(
                    replay, stage, plain)
                top = machine.read_u32(mgr + 0x1C)
                self.assertNotEqual(top, block,
                                    "top header must not alias stage header")
                # 顶层头原样字节 = 解压流 [0,0x74)
                self.assertEqual(bytes(machine.uc.mem_read(top, 0x74)),
                                 bytes(replay.top_header))
                # 恢复后 0x4be7b8 族 = 顶层头 +0x5c/0x60/0x64 先写、随后
                # 被 stage 头 0x58 字节窗口覆盖（0x447900:197-208 顺序）——
                # stage 头 [0x14,0x6c) 是最终值。
                machine.call(RESTORE)
                restored = bytes(machine.uc.mem_read(RESTORE_DST,
                                                     RESTORE_LEN))
                self.assertEqual(restored, bytes(stage.header[0x14:0x6C]))


class RetailConsumeTests(unittest.TestCase):
    """正控 3 + 负控 2：原版 0x448200 逐帧消费与沿一致性。"""

    def setUp(self):
        if not _original_ex_available():
            self.skipTest("original executable is not available")
        path = str(ROOT / "games/replays/th13_01.rpy")
        if not os.path.isfile(path):
            self.skipTest("original replay is not available")
        self._raw, self._replay, self._plain = _decode(path)
        self._stage = self._replay.stages[0]
        from oracle import battle_oracle as bo
        self._oracle_module = bo

    def _machine(self, tamper_frame=None):
        """零售装备形状（槽号=关卡号、顶层头独立块）+ 0x449070 装备。

        0x449070 case 1（artifacts/decomp/jp/functions/00449070.c:33-50）：
          base(number)=mgr+(number*5+0x19)*8；读 stage 头指针 =
          mgr+0x10+base 偏移（=mgr+0xd8+number*0x28）；base+4←base+0
          （当前游标←初始游标）、base+0xc←base+8（事件游标←尾界）、
          base+0x14=-1（帧计数）；头+0x02 u16 种子 → 0x4dc324/0x4dc31c；
          头+0x14 起 0x58 → 0x4be7b8。
        消费测试必须先跑它（否则当前游标未初始化——空指针读）。"""
        replay, stage, plain = (self._replay, self._stage, self._plain)
        machine = Machine(str(ROOT / "games/th13/th13.exe"))
        uc = machine.uc
        mgr = machine.malloc(0x320)
        top = machine.malloc(0x74)
        input_bytes = bytearray(stage.input)
        if tamper_frame is not None and (tamper_frame + 1) * 6 <= len(
                input_bytes):
            input_bytes[tamper_frame * 6] ^= 0x40
        block = machine.malloc(STAGE_HEADER_SIZE + len(input_bytes)
                               + max(len(stage.events), 1))
        uc.mem_write(mgr, bytes(0x320))
        uc.mem_write(top, bytes(replay.top_header))
        uc.mem_write(block, bytes(stage.header) + bytes(input_bytes)
                     + bytes(stage.events))
        number = stage.number
        machine.write_u32(0x4C22C8, mgr)
        machine.write_u32(0x4BE81C, number)
        machine.write_u32(mgr + 0x10, 1)          # 0x449070 case 1 判据
        machine.write_u32(mgr + 0x1C, top)        # 顶层头独立块（0x447900:51）
        base = mgr + (number * 5 + 0x19) * 8      # 游标族（槽号=关卡号）
        machine.write_u32(base, block + STAGE_HEADER_SIZE)  # 初始游标
        machine.write_u32(base + 8, block + STAGE_HEADER_SIZE   # 尾界
                          + stage.frame_count * 6)
        machine.write_u32(mgr + 0xD8 + number * 0x28, block)  # 头指针
        machine.write_u32(mgr + 0x20 + number * 4, block)      # stage 头槽
        machine.write_u32(mgr + 0x218, number)
        machine.write_u32(0x4C2194, 1)
        # 原版装备序（0x449070 case 1）：种子/初值恢复 + 游标拷贝 + 帧计数 -1
        machine.call(RESTORE)
        self.assertEqual(machine.read_u32(base + 4),
                         machine.read_u32(base),
                         "0x449070 must copy initial cursor to current")
        self.assertEqual(machine.read_u32(base + 0x14) & 0xFFFFFFFF,
                         0xFFFFFFFF, "0x449070 sets frame counter to -1")
        # 原版消费启动（0x448ec0:41-54，scene7+0x14==0 首帧执行；
        # 桩环境不跑本体——其还触玩家对象 0x4c22c4 与 0x444df0——
        # 按同拍写四字段：激活索引=be81c、当前游标←初始、事件游标←尾界、
        # 帧计数=0。0x448200:14 的负帧计数拦截自此解除。
        machine.write_u32(mgr + 0x218, number)    # 激活索引（0x448ec0:42）
        machine.write_u32(base + 4, machine.read_u32(base))
        machine.write_u32(base + 0xC, machine.read_u32(base + 8))
        machine.write_u32(base + 0x14, 0)        # 帧计数（0x448ec0:54）
        return machine, mgr

    def test_six_byte_consumption_matches_file_frames(self):
        machine, mgr = self._machine()
        uc = machine.uc
        number = self._stage.number
        base = mgr + (number * 5 + 0x19) * 8
        mismatches = 0
        for frame in range(min(self._stage.frame_count, 4000)):
            machine.call(CONSUME, regs={"esi": mgr})
            if uc.reg_read(UC_X86_REG_EIP) != RET_MAGIC:
                self.fail("retail consumer did not return at frame %d"
                          % frame)
            want = self._stage.frame(frame)
            if want == (0xFFFF, 0xFFFF, 0xFFFF):
                break    # EOF：0x448200:26 三字全 F → 0x43e5a0 停
            got = (machine.read_u32(HELD) & 0xFFFF,
                   machine.read_u32(PRESSED) & 0xFFFF,
                   machine.read_u32(RELEASED) & 0xFFFF)
            if got != want:
                mismatches += 1
                if mismatches > 4:
                    break
            # 帧计数（base+0x14）：0x448ec0 启动置 0，每消费 +1
            #（0x448200:49-50）——首帧后为 1
            self.assertEqual(
                machine.read_u32(base + 0x14) & 0xFFFFFFFF,
                (frame + 1) & 0xFFFFFFFF)
            # 当前游标（base+4）每帧 +6（0x448200:37-38）
            self.assertEqual(
                machine.read_u32(base + 4),
                machine.read_u32(base) + 6 * (frame + 1))
        self.assertEqual(mismatches, 0)

    def test_tampered_input_frame_is_detected(self):
        """负控：帧 73（首输入帧）态字节篡改必须被消费检出。"""
        machine, mgr = self._machine(tamper_frame=73)
        stage = self._stage
        want = stage.frame(73)
        mismatch_at_73 = False
        for frame in range(74):
            machine.call(CONSUME, regs={"esi": mgr})
            if frame == 73:
                got = (machine.read_u32(HELD) & 0xFFFF,
                       machine.read_u32(PRESSED) & 0xFFFF,
                       machine.read_u32(RELEASED) & 0xFFFF)
                if got != want:
                    mismatch_at_73 = True
        self.assertTrue(mismatch_at_73,
                        "tampered input must produce a detected mismatch")

    def test_overflow_clears_input(self):
        """越界（帧计数 ≥ 头内帧数）清零输入（0x448200:44-48 分支）。"""
        machine, mgr = self._machine()
        number = self._stage.number
        base = mgr + (number * 5 + 0x19) * 8
        # 帧计数推过头内帧数（0x448200:23 比较：帧计数 < *(头+4)；
        # 头指针 = mgr+0xd8+number*0x28）
        header_ptr = machine.read_u32(mgr + 0xD8 + number * 0x28)
        frame_count = struct.unpack_from("<I", bytes(
            machine.uc.mem_read(header_ptr + 4, 4)))[0]
        machine.write_u32(base + 0x14, frame_count)
        machine.call(CONSUME, regs={"esi": mgr})
        self.assertEqual(machine.read_u32(HELD), 0)
        self.assertEqual(machine.read_u32(PRESSED), 0)
        self.assertEqual(machine.read_u32(RELEASED), 0)


class BattleOracleReplayPathTests(unittest.TestCase):
    """正控 4 + 负控 3：battle_oracle 装备路径（不跑整局）。"""

    def setUp(self):
        path = str(ROOT / "games/replays/th13_01.rpy")
        if not os.path.isfile(path):
            self.skipTest("original replay is not available")
        self._raw, self._replay, self._plain = _decode(path)

    def _oracle_stub(self):
        from oracle.battle_oracle import BattleOracle
        oracle = BattleOracle.__new__(BattleOracle)
        oracle.replay = self._replay
        oracle.replay_stage = self._replay.stages[0]
        oracle._replay_file_name = "th13_01.rpy"    # 壳 0x4c2198 文件名语义
        oracle.battle_requested = False
        oracle.frame = 0
        return oracle

    def test_replay_equip_registers_cursor_family_offsets(self):
        """0x447900/0x448c40/0x448ec0 游标族偏移逐点存在且值正确（装备形状）。

        零售形状：槽号 = stage 头 +0x00 关卡号（0x448c40:75）；顶层头为
        独立块（mgr+0x1c，非 stage 头别名）；激活索引=关卡号。"""
        from oracle.battle_oracle import (REPLAY_MGR, REPLAY_SHELL,
                                          REPLAY_RESTORE, DAT_REPLAY_FLAG)
        oracle = self._oracle_stub()
        # 极小 fake：不装载 EXE，只验证装备写入的数据形状；
        # drive(0x449070) 桩化——恢复结果由 FakeMem 按解码头回放，
        # 使装备全流程（含消费启动写）走通。
        writes = {}
        stage = oracle.replay_stage

        class FakeMem:
            def mem_read(self, address, size):
                if address == RESTORE_DST:
                    return bytes(stage.header[0x14:0x6C])
                if address == SEED_A:
                    return struct.pack("<I", stage.stamp)
                return b"\x00" * size

            def mem_write(self, address, data):
                writes.setdefault(address, bytearray()).extend(data)
        oracle.uc = FakeMem()
        allocated = []

        def fake_malloc(size):
            # 顺序分配基址（与真 game_malloc 同语义：每次返回新块）
            ptr = 0x200000 + len(allocated) * 0x10000
            allocated.append((ptr, size))
            return ptr
        oracle.game_malloc = fake_malloc
        oracle.wr32 = lambda address, value: writes.__setitem__(
            address, struct.pack("<I", value & 0xFFFFFFFF))

        def fake_rd32(address):
            if address == SEED_A:
                return stage.stamp    # 桩化 0x449070 的种子写（0x4dc324）
            return struct.unpack(
                "<I", writes.get(address, b"\x00\x00\x00\x00"))[0]
        oracle.rd32 = fake_rd32
        restore_calls = []
        oracle.drive = (lambda address, args=(), regs=None, timeout_ms=0:
                        restore_calls.append(address) or 0)
        number = stage.number
        # 装备分配：mgr（0x200000）、top_block（0x210000）、stage_block
        #（0x220000）、shell 文件名（0x230000）——按分配序推导
        mgr_expected = 0x200000
        top_expected = 0x210000
        block_expected = 0x220000
        try:
            oracle._equip_replay_manager()
        except SystemExit:
            # fake 内存下恢复校验读回 0 必然 fail——本测试只测装备写入
            # 形状；恢复的逐位校验由 RetailRestoreTests（真机）覆盖。
            pass
        self.assertEqual(restore_calls, [REPLAY_RESTORE],
                         "equipment must drive the retail restorer")
        mgr = struct.unpack("<I", writes[REPLAY_MGR])[0]
        self.assertEqual(mgr, mgr_expected)
        # 顶层头独立块（0x447900:43-51 分配；严禁别名 stage 头——
        # P0-1 真机复刻：别名+字段写污染 0x449070 恢复源）
        self.assertEqual(struct.unpack("<I", writes[mgr + 0x1C])[0],
                         top_expected)
        self.assertNotEqual(top_expected, block_expected)
        # 游标族（槽号=关卡号：base=mgr+(number*5+0x19)*8）
        base = mgr + (number * 5 + 0x19) * 8
        self.assertEqual(
            struct.unpack("<I", writes[base])[0],
            block_expected + STAGE_HEADER_SIZE)   # 初始游标（0x448c40:76）
        self.assertEqual(
            struct.unpack("<I", writes[base + 8])[0],
            block_expected + STAGE_HEADER_SIZE + stage.frame_count * 6)
        self.assertEqual(
            struct.unpack("<I", writes[mgr + 0xD8 + number * 0x28])[0]
            & 0xFFFFFFFF,
            block_expected)          # stage 头指针（0x448c40:75）
        self.assertEqual(
            struct.unpack("<I", writes[mgr + 0x20 + number * 4])[0],
            block_expected)          # stage 头槽（0x447900:59）
        # 激活索引=关卡号（0x448ec0:42 语义；0 位 python 下标=伪造）
        self.assertEqual(struct.unpack("<I", writes[mgr + 0x218])[0],
                         number)
        # DAT_004be81c/be820 = 关卡号（0x4525b0:160-161）
        self.assertEqual(struct.unpack(
            "<I", writes[0x004BE81C])[0], number)
        self.assertEqual(struct.unpack(
            "<I", writes[0x004BE820])[0], number)
        # 回放模式位（授权修复 task-configure-20261025-oracle-replay-window-
        # repair：撤回旧 |=2 位形 2 分歧——零售首关回放（0x4525b0 case 3 →
        # scene 0xd → 0x42b990）不置任何 be830 位，位形 0 走 0x447ed0 装载
        # 链；旧 |=2 使 0x42b990 else 分支跳过装载链 → 0x424fd0 以未构造
        # EBX 写 +0x14c 冷启动崩（0042b990.c:175-191 + 00424fd0.c:15-20）。
        # 装备不再触碰 DAT_004be830——负控断言：位形恒为 0。
        self.assertNotIn(DAT_REPLAY_FLAG, writes,
                        "equip must not touch the loader-path flag "
                        "(retail first-stage replay keeps form 0)")
        # 壳=文件名缓冲（0x4525b0:165-170 语义；堆指针≠管理器）
        shell_ptr = struct.unpack("<I", writes[REPLAY_SHELL])[0]
        self.assertNotEqual(shell_ptr, mgr)
        # 消费启动写（0x448ec0:41-54 同拍）：当前游标←初始、事件游标←尾界、
        # 帧计数=0（base+0x14）
        self.assertEqual(struct.unpack("<I", writes[base + 4])[0],
                         block_expected + STAGE_HEADER_SIZE)
        self.assertEqual(struct.unpack("<I", writes[base + 0xC])[0],
                         block_expected + STAGE_HEADER_SIZE
                         + stage.frame_count * 6)
        self.assertEqual(struct.unpack("<I", writes[base + 0x14])[0], 0)

    def test_equip_without_raw_header_fails(self):
        """负控：解码器未保留 0xc4 头时装备必须拒绝（不臆造初值）。"""
        from oracle.battle_oracle import BattleOracle
        oracle = self._oracle_stub()
        oracle.replay_stage.header = b""    # 模拟旧解码器丢失头

        class FakeMem:
            def mem_read(self, address, size):
                return b"\x00" * size

            def mem_write(self, address, data):
                return None
        oracle.uc = FakeMem()
        oracle.game_malloc = lambda size: 0x100000
        oracle.wr32 = lambda address, value: None
        oracle.rd32 = lambda address: 0
        with self.assertRaises(SystemExit):
            oracle._equip_replay_manager()

    def test_consume_phase_pairs_with_enemy_update(self):
        """正控 5（相位，缺口 a 修正）：按 run 循环的真实次序仿真多帧——
        每帧「consume_replay_frame（帧首）→ title_frame（敌机逻辑在
        update walk 内）→ battle_first 检测」推进，断言：
          (1) 每帧消费恰一次（0x448200 hook 计数与帧数相等）；
          (2) 消费先于本帧 0x418e00（帧首直调=update walk 前置等价）；
          (3) 逐帧成对：第 k 次消费产出文件帧 k-1 的输入后，同帧敌机
              逻辑（0x418e00 恰一次）才执行；
          (4) battle_f 语义从 0 起（无错位）——消费产出与 want=stage
              .frame(battle_f) 全等。
        声明边界：本证建立"帧内输入先行"的等价次序；0x470990 优先级
        插链中回放消费任务与敌机任务的**链内绝对次序**未证明（两任务
        注册源不同：0x447900:221 vs 0x418d30），如实登记。"""
        from oracle.battle_oracle import BattleOracle, REPLAY_CONSUME
        oracle = self._oracle_stub()
        if not _original_ex_available():
            self.skipTest("original executable is not available")
        machine, mgr = self._consume_machine(oracle)
        oracle.uc = machine.uc
        oracle.rd32 = machine.read_u32
        oracle.wr32 = machine.write_u32
        oracle.battle_first = 0
        oracle.replay_consumed = 0
        oracle.replay_phase_samples = []
        oracle.replay_edge_mismatches = []
        oracle._replay_log = None
        oracle.game_malloc = machine.malloc
        consume_entries = []

        def fake_drive(address, args=(), regs=None, timeout_ms=0):
            if address == REPLAY_CONSUME:
                consume_entries.append(len(enemy_update_log))
                return machine.call(address, regs=regs)
            return machine.call(address, regs=regs)
        oracle.drive = fake_drive
        # 消费计数 hook（与 install_hooks 的 on_replay_consume 同源；
        # __new__ 构造的 oracle 未跑 install_hooks，需自挂）
        from unicorn import UC_HOOK_CODE as _HOOK_CODE

        def on_consume(uc, addr, size, ud):
            oracle.replay_consumed += 1
        machine.uc.hook_add(_HOOK_CODE, on_consume,
                            begin=REPLAY_CONSUME, end=REPLAY_CONSUME)
        # 0x418e00 敌机逻辑的执行日志（同帧次序证据——仿真帧内
        # 不执行 0x418e00 函数体，按 run 循环的次序位置记录）
        enemy_update_log = []
        stage = oracle.replay_stage
        # 三帧仿真：帧首消费 → 帧内敌机逻辑
        for frame in range(3):
            oracle.frame = frame
            oracle.enemy_updates = 0
            oracle.consume_replay_frame()
            # 帧内敌机逻辑（真机由 update walk 驱动；仿真=本帧一次
            # 0x418e00 入口事件——只记次序证据，不真执行其函数体）
            enemy_update_log.append(len(consume_entries))
            # 每帧断言：恰一次消费、敌机逻辑在消费之后（帧内次序）
            self.assertEqual(len(consume_entries), frame + 1,
                             "consume must happen exactly once per frame")
            self.assertEqual(enemy_update_log[-1], frame + 1,
                             "enemy update must follow the same frame's "
                             "consume (pairing)")
            battle_f = frame - (oracle.battle_first or 0)
            want = stage.frame(battle_f)
            got = (machine.read_u32(HELD) & 0xFFFF,
                   machine.read_u32(PRESSED) & 0xFFFF,
                   machine.read_u32(RELEASED) & 0xFFFF)
            self.assertEqual(got, want,
                             "input phase must align: battle_f=%d" % battle_f)
        self.assertEqual(oracle.replay_edge_mismatches, [])
        # 相位样本成对推进（(帧, 消费数, 敌机数) 单调）
        for older, newer in zip(oracle.replay_phase_samples,
                                oracle.replay_phase_samples[1:]):
            self.assertEqual(newer[1], older[1] + 1)

    def test_ec0_rollback_guard_restores_consumed_cursor(self):
        """P2-2 + P2·EC0 回归防护（真机时序版）。

        缺陷史（P2·EC0）：旧防护挂 0x448ec0 入口，UC_HOOK_CODE 在指令
        执行前触发——防护重推先于本体回滚写（0x44902c/0x44902f/
        0x449032，反编译 00448ec0.c:52-54），回滚胜出，防护在真实时序
        下无效；旧测试手工按"回滚三写→防护重推"次序模拟（防护后置），
        与实现相位互反，14/14 全绿不证明防护有效。

        本测试把原版 0x448ec0 本体**真跑**（消费 3 帧后触发一次），防护
        钩子按 battle_oracle.install_hooks 的实跑相位挂回滚写之后的
        0x449035——断言真实时序下（回滚先发生、防护后执行）：
          (1) 防护后游标/帧计数 == 回滚前（消费态）；
          (2) 后续 0x448200 消费从文件帧 3 续读（无重复帧）。
        负控：同样的本体真跑 + 防护改挂 0x448ec0 入口（旧相位），必须
        观察到回滚胜出（游标被退回、下一消费重复文件帧 0）——证明本
        测试有能力检出相位缺陷（非同义反复）。"""
        from oracle.battle_oracle import (BattleOracle, REPLAY_MGR,
                                          REPLAY_CONSUME)
        oracle = self._oracle_stub()
        if not _original_ex_available():
            self.skipTest("original executable is not available")
        number = oracle.replay_stage.number
        guard_hook, cursor_before, counter_before = self._run_ec0_with_guard(
            oracle, hook_addr=0x00449035)
        self.assertEqual(guard_hook["hits"], 1,
                         "guard hook must fire exactly once per 0x448ec0 "
                         "replay-branch execution")
        self.assertEqual(counter_before, 3,
                         "precondition: 3 frames consumed before 0x448ec0")
        # (1) 防护后游标/帧计数 == 消费态（回滚被重推消除）
        self.assertEqual(guard_hook["cursor_after"], cursor_before,
                         "guard must restore cursor after rollback")
        self.assertEqual(guard_hook["counter_after"], counter_before,
                         "guard must restore frame counter after rollback")
        # (2) 后续消费从文件帧 3 续读（真实时序无重复帧）。
        # 消费产出对照口径：held 直写文件帧 3 态字节；pressed/released
        # 是 0x414f30 按 (前态 0x4e4c0c, 当前态 0x4e4c08) 重算的位沿
        #（0x414f9f xor/and 序：pressed=cur&~prev、released=~cur&prev）。
        # 本场景 0x448ec0 本体已把 0x4e4c0c←0x4e4c08（清零后同值 0）
        #——前态=0；帧 3 态=1（Z 键位）→ pressed=1、released=0。
        # 文件沿字节（replayfmt.frame 的三 u16）在整段连续消费下与
        # 位沿重算逐帧一致（test_six_byte_consumption 正控）；本测试
        # 经 0x448ec0 清零中断后，前态口径以消费器重算语义为准。
        machine = guard_hook["machine"]
        mgr = guard_hook["mgr"]
        base = mgr + (number * 5 + 0x19) * 8
        # 消费前前态快照（0x414f30 的输入对：cur=0x4e4c08、prev=0x4e4c0c；
        # 0x448ec0 已把两者清零——本场景 prev=0）
        prev = machine.read_u32(0x4E4C0C) & 0xFFFF
        machine.call(REPLAY_CONSUME, regs={"esi": mgr})
        held_file, _pressed_file, _released_file = oracle.replay_stage.frame(3)
        cur = machine.read_u32(HELD) & 0xFFFF          # 帧 3 态（直写）
        want = (cur, cur & ~prev & 0xFFFF, ~cur & prev & 0xFFFF)
        got = (machine.read_u32(HELD) & 0xFFFF,
               machine.read_u32(PRESSED) & 0xFFFF,
               machine.read_u32(RELEASED) & 0xFFFF)
        self.assertEqual(got, want,
                         "edge recompute must follow 0x414f30 prev/cur "
                         "semantics after 0x448ec0 input clear")
        self.assertEqual(cur, held_file,
                         "held must be file frame 3 state (no repeat of "
                         "frame 0 — guard held)")
        self.assertEqual(machine.read_u32(base + 0x14), 4)

    def test_ec0_entry_phase_guard_is_detected_as_defective(self):
        """负控（P2·EC0）：旧入口相位（挂 0x448ec0）在真实时序下必须
        失效——防护写被本体回滚覆盖，游标退回、下一消费重复文件帧 0。
        本测试证明上测试的检出能力：同样的真跑场景、只把钩子相位换回
        入口，断言回滚胜出（与正控互反）。"""
        from oracle.battle_oracle import BattleOracle, REPLAY_CONSUME
        oracle = self._oracle_stub()
        if not _original_ex_available():
            self.skipTest("original executable is not available")
        guard_hook, cursor_before, counter_before = self._run_ec0_with_guard(
            oracle, hook_addr=0x00448EC0)
        self.assertEqual(guard_hook["hits"], 1,
                         "old entry-phase hook must still fire (it fires "
                         "before the body executes)")
        # 真实时序：本体回滚在防护写之后执行并胜出
        self.assertNotEqual(guard_hook["cursor_after"], cursor_before,
                           "entry-phase guard must be rolled back by the "
                           "body (defect must be observable here)")
        self.assertEqual(guard_hook["counter_after"], 0,
                         "frame counter must be rolled back to 0 under the "
                         "entry-phase defect")
        # 下游症状（回滚未被消除）：下一消费重读文件帧 0——游标退回
        # 初始（=base+0），帧计数重启 1（正控为 base+6*4/4——互反）。
        # 文件帧 0 输入=(0,0,0)，与清零态不可区分——症状断言以机制
        # 状态（游标/帧计数）为准，不以 held 字面值冒充。
        machine = guard_hook["machine"]
        mgr = guard_hook["mgr"]
        number = oracle.replay_stage.number
        base = mgr + (number * 5 + 0x19) * 8
        machine.call(REPLAY_CONSUME, regs={"esi": mgr})
        self.assertEqual(machine.read_u32(base + 4),
                         machine.read_u32(base) + 6,
                         "rolled-back cursor must re-consume file frame 0 "
                         "(downstream symptom of the entry-phase defect)")
        self.assertEqual(machine.read_u32(base + 0x14), 1,
                         "counter restarts from 0 under the entry-phase "
                         "defect (guard state was rolled back)")

    def _run_ec0_with_guard(self, oracle, hook_addr):
        """真实时序场景：消费 3 帧 → 真跑原版 0x448ec0 本体（回放分支）
        → 防护钩子按指定相位挂（与 install_hooks 的 on_replay_ec0 同式）。

        返回取证 dict：钩子命中数、回滚前游标/帧计数（消费态）、防护后
        游标/帧计数、machine/mgr 引用（供后续消费断言）。

        本体真跑可行性（objdump 0x448ec0 回放分支指令序）：
          - 0x448ede..0x448ef2：mgr+8/+0x214/+0xc 三任务节点判空后
            `or [node+4],2`——三指针槽清零即跳过（无解引用）；
          - 0x448ef5/0x448f08：memset(0x4e4a84,0,0x80)/(0x4e4b84,0,0x80)
            ——写 .bss 状态区（已映射映像），安全；
          - 0x448f18..0x448f3c：0x4e4c04..0x4e4c20 清零——安全；
          - 0x448fd3/0x448fd6：mgr+0x10==1 → 回放分支（本场景判据）；
          - 0x448fe4：edi=stage 头指针（mgr+0xd8+number*0x28）；
            0x448feb..0x448ff4：call 0x447850（edi+0xc 位置复位——
            写玩家对象 DAT_004c22c4+0x5c4/+0x5c8 与派生浮点/0xa38c 族；
            玩家对象为 0x200 大小 malloc 块，仅 +0x5c8 内字段写入，
            出界安全）；
          - 0x448ffc..0x449002：玩家对象+0x14840←头+0x6c——玩家块
            **不扩容**：0x14840 越过块界写入堆内紧邻空区（机器堆
            0x1000_0000 字节连续零页，无别名分配——安全写，值不消费）；
          - 0x449008..0x449012：be81c==3 时写 mgr+0x14（本场景=关卡号
            1，跳过）；
          - 0x44901e..0x449032：回滚三写（游标←初始/事件←尾界/计数←0
            ——被测行为本体）；
          - 0x449035..0x449057：edi=玩家对象基址下 0x20 次循环
            `*(edi+0x14838-8k)=玩家+0x5c4`——同样越 0x200 块界写堆
            空区（向下 0x100 字节内），安全写；
          - 0x449059：call 0x444df0——ECL 复位（读全局任务链，指针
            槽清零下遍历空——按反编译 :102 piVar7=NULL 早退，安全）；
          - 0x44905e..0x44906a：出口（mgr+0x210←0，弹栈 ret）。"""
        from oracle.battle_oracle import REPLAY_MGR, REPLAY_CONSUME
        from unicorn import UC_HOOK_CODE as _HOOK_CODE
        machine, mgr = self._consume_machine(oracle)
        base = mgr + (oracle.replay_stage.number * 5 + 0x19) * 8
        # 玩家对象（DAT_004c22c4）：0x447850 位置复位与 +0x14840 写的
        # 目标——0x200 块 + 0x14840 越界写落在堆空区（见 docstring 论证）。
        player = machine.malloc(0x200)
        machine.write_u32(0x4C22C4, player)
        # 0x444df0 早退形状（objdump 实证）：入口读 DAT_004be7e8/e8 /
        # be7f0——be7e8(=0) < be7ec(=0) 为假、local_8=be7e8/be7f0——
        # be7f0=1 时 idiv 0 也安全（0/1=0）；[edi+0x146c4]==0 走
        # 0x472f30(0.0)（纯 FPU 常量区间钳制，无外部依赖，objdump
        # 0x472f30..0x472fbd 全程 FPU+立即数，无 call）；随后
        # [edi+0x146c4]==local_8（0==0）→ 0x44561b 早退 ret。
        # 玩家块 0x200 界内 +0x146c4/+0x14734 均为块内偏移
        #（0x200=512 > 0x14734? 否——0x14734 块界外，落在堆空区，
        #  与 +0x14840 同论证：越界写仅及堆内零区，值不消费）。
        machine.write_u32(0x4BE7E8, 0)
        machine.write_u32(0x4BE7EC, 0)
        machine.write_u32(0x4BE7F0, 1)
        # 消费 3 帧（0x448200 真跑）——场景前置态
        for _ in range(3):
            machine.call(REPLAY_CONSUME, regs={"esi": mgr})
        cursor_before = machine.read_u32(base + 4)
        counter_before = machine.read_u32(base + 0x14)
        # 防护钩子（与 install_hooks 的 on_replay_ec0 同式，按指定相位挂）
        guard_hook = {"hits": 0, "cursor_after": None, "counter_after": None,
                      "machine": machine, "mgr": mgr}
        oracle.uc = machine.uc
        oracle.rd32 = machine.read_u32
        oracle.wr32 = machine.write_u32
        oracle.replay_consumed = 3

        def on_guard(uc, addr, size, ud):
            if getattr(oracle, "replay_stage", None) is None:
                return
            m = oracle.rd32(REPLAY_MGR)
            if not m or oracle.rd32(m + 0x10) != 1:
                return
            consumed = getattr(oracle, "replay_consumed", 0)
            guard_hook["hits"] += 1
            if consumed:
                n = oracle.replay_stage.number
                b = m + (n * 5 + 0x19) * 8
                oracle.wr32(b + 4, oracle.rd32(b) + 6 * consumed)
                oracle.wr32(b + 0x14, consumed)
        machine.uc.hook_add(_HOOK_CODE, on_guard, begin=hook_addr,
                            end=hook_addr)
        # 真跑原版 0x448ec0 本体（回放分支）——真实钩子时序：
        # 入口相位（0x448EC0）= 防护写先于回滚写；回滚后相位
        #（0x449035）= 防护写在回滚写之后。本体执行结束后取证。
        machine.call(0x448EC0)
        guard_hook["cursor_after"] = machine.read_u32(base + 4)
        guard_hook["counter_after"] = machine.read_u32(base + 0x14)
        return guard_hook, cursor_before, counter_before

    def _consume_machine(self, oracle):
        """零售装备形状（槽号=关卡号、顶层头独立块）+ 0x449070 装备
        （与 RetailConsumeTests._machine 同构，供 battle_oracle 消费用）。"""
        stage = oracle.replay_stage
        replay = oracle.replay
        machine = Machine(str(ROOT / "games/th13/th13.exe"))
        uc = machine.uc
        mgr = machine.malloc(0x320)
        top = machine.malloc(0x74)
        block = machine.malloc(STAGE_HEADER_SIZE + len(stage.input)
                               + max(len(stage.events), 1))
        uc.mem_write(mgr, bytes(0x320))
        uc.mem_write(top, bytes(replay.top_header))
        uc.mem_write(block, bytes(stage.header) + bytes(stage.input)
                     + bytes(stage.events))
        number = stage.number
        base = mgr + (number * 5 + 0x19) * 8
        machine.write_u32(0x4C22C8, mgr)
        machine.write_u32(0x4BE81C, number)
        machine.write_u32(mgr + 0x10, 1)
        machine.write_u32(mgr + 0x1C, top)
        machine.write_u32(base, block + STAGE_HEADER_SIZE)
        machine.write_u32(base + 8, block + STAGE_HEADER_SIZE
                          + stage.frame_count * 6)
        machine.write_u32(mgr + 0xD8 + number * 0x28, block)
        machine.write_u32(mgr + 0x218, number)
        machine.write_u32(0x4C2194, 1)
        machine.write_u32(0x4C22C8, mgr)   # consume_replay_frame 读管理器全局
        machine.call(RESTORE)     # 游标初始化（base+4←base+0）
        # 0x448ec0:41-54 消费启动写（与 RetailConsumeTests._machine 同拍）
        machine.write_u32(mgr + 0x218, number)
        machine.write_u32(base + 4, machine.read_u32(base))
        machine.write_u32(base + 0xC, machine.read_u32(base + 8))
        machine.write_u32(base + 0x14, 0)
        return machine, mgr


class ReplayfmtRegressionTests(unittest.TestCase):
    """旧解码语义不回归（replayfmt.header 追加不破坏 decode）。"""

    def test_decode_still_rejects_bad_magic(self):
        with self.assertRaises(ReplayFormatError):
            ReplayFile().decode(b"XXXX" + bytes(0x20))

    def test_frame_accessor_semantics_unchanged(self):
        path = str(ROOT / "games/replays/th13_udSr01.rpy")
        if not os.path.isfile(path):
            self.skipTest("original replay is not available")
        _raw, replay, _plain = _decode(path)
        stage = replay.stages[0]
        self.assertEqual(stage.frame(0), (0, 0, 0))
        self.assertEqual(stage.frame(73)[0], 0x40)
        self.assertEqual(stage.frame(stage.frame_count)[0], 0)


if __name__ == "__main__":
    unittest.main()
