import io
import json
import os
import struct
import tempfile
import unittest

from .battle_oracle import (BattleOracle, SCENE_CUR, SCENE_NEXT, DAT_SPELL,
                            DAT_SHOT_TYPE, capture_exit_code, main)
from .battle_startup_check import inspect_startup
from .render_oracle import RenderOracle
from .machine import Machine, HEAP_BASE, HEAP_SIZE
from unicorn.x86_const import UC_X86_REG_EDX


class CaptureIntegrityTests(unittest.TestCase):
    def test_success_requires_the_requested_window_without_a_crash(self):
        stats = dict(frames=1200, requested_frames=1200, crash=None,
                     enemy_update_anomalies=[])
        self.assertEqual(capture_exit_code(stats), 0)
        for change in (dict(frames=1199), dict(crash=1213),
                       dict(enemy_update_anomalies=[(42, 2)])):
            self.assertEqual(capture_exit_code({**stats, **change}), 1)

    def test_normal_battle_clears_spell_practice_before_requesting_scene(self):
        oracle = BattleOracle.__new__(BattleOracle)
        oracle.battle_requested = False
        oracle.difficulty = 1
        oracle.character = 2
        oracle.shot_type = 1
        oracle.frame = 2
        writes = []
        oracle.rd32 = lambda address: 4 if address == SCENE_CUR else 0
        oracle.wr32 = lambda address, value: writes.append((address, value))
        oracle.maybe_request_battle()
        self.assertIn((DAT_SPELL, 0xFFFFFFFF), writes)
        self.assertIn((DAT_SHOT_TYPE, 1), writes)
        self.assertLess(writes.index((DAT_SPELL, 0xFFFFFFFF)),
                        writes.index((SCENE_NEXT, 7)))
        oracle.maybe_request_battle()
        self.assertEqual(writes.count((SCENE_NEXT, 7)), 1)

    def test_raw_state_does_not_claim_unverified_lives_or_score_fields(self):
        oracle = BattleOracle.__new__(BattleOracle)
        oracle._state_log = io.StringIO()
        oracle.rd32 = lambda address: 0xFFFFFFFF if address == DAT_SPELL else 0
        oracle.frame_keys = frozenset((90,))
        class Memory:
            def mem_read(self, address, size):
                return struct.pack('<I', 0xFFFFFFFF)
        oracle.uc = Memory()
        oracle._write_state_sample(0)
        row = json.loads(oracle._state_log.getvalue())
        self.assertEqual(row['frame'], 0)
        self.assertEqual(row['spell_id'], -1)
        self.assertEqual(row['schema'], 'th13-state-raw-v1')
        self.assertNotIn('lives', row)
        self.assertNotIn('score', row)
        self.assertIn('004be810', row['raw_globals'])

    def oracle(self, directory, rows, requested=3, crash=None):
        path = os.path.join(directory, 'draws.jsonl')
        with open(path, 'w') as output:
            for row in rows:
                output.write(json.dumps(row) + '\n')
        oracle = BattleOracle.__new__(BattleOracle)
        oracle.out_path = path
        # 授权修复（task-configure-20261025-oracle-replay-window-repair）：
        # cut 窗起点=检测帧（battle_first-1，scene7 首帧本体）。夹具
        # battle_first=3 → 窗口自帧 2 起。
        oracle.battle_first = 3
        oracle.target_frames = requested
        oracle.crash_frame = crash
        oracle.family_ops = {}
        oracle.enemy_update_audit = []
        oracle.frame = 9
        return oracle

    def rows(self):
        return [dict(frame=f, viewport=[], ops=[dict(tex=[4, 0, 'fixture'])])
                for f in range(6)]

    def test_complete_cut_is_contiguous_and_renumbered(self):
        with tempfile.TemporaryDirectory() as directory:
            oracle = self.oracle(directory, self.rows())
            stats = oracle.cut_battle_window()
            self.assertEqual(capture_exit_code(stats), 0)
            with open(oracle.out_path) as source:
                self.assertEqual([json.loads(row)['frame'] for row in source], [0, 1, 2])
            self.assertEqual(oracle.family_ops, {'fixture': 0})

    def test_crashed_frame_is_not_published_as_a_complete_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            oracle = self.oracle(directory, self.rows(), crash=5)
            stats = oracle.cut_battle_window()
            # 授权修复后的窗起点=检测帧（battle_first-1=2）：窗口行
            # 2,3,4（crash=5 截断），3 帧且 exit 1——崩溃帧不得发表。
            self.assertEqual(stats['frames'], 3)
            self.assertEqual(capture_exit_code(stats), 1)

    def test_a_gap_is_an_error_and_preserves_the_raw_file(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [row for row in self.rows() if row['frame'] != 4]
            oracle = self.oracle(directory, rows)
            with self.assertRaises(SystemExit):
                oracle.cut_battle_window()
            with open(oracle.out_path) as source:
                self.assertEqual(len(source.readlines()), len(rows))
            self.assertEqual(os.listdir(directory), ['draws.jsonl'])

    def test_startup_gate_rejects_the_spell_practice_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'startup.jsonl')
            def write(boss):
                with open(path, 'w') as output:
                    for frame in range(600):
                        ops = []
                        if boss and frame == 74:
                            ops = [dict(tex=[11, 0, 'stgenm/stage01/cardbg0.png'])]
                        elif not boss and frame == 61:
                            ops = [dict(tex=[10, 0, 'enemy/enemy.png'])]
                        output.write(json.dumps(dict(frame=frame, ops=ops)) + '\n')
            write(False)
            self.assertEqual(inspect_startup(path)['errors'], [])
            write(True)
            self.assertEqual(inspect_startup(path)['premature_boss_frame'], 74)
            self.assertEqual(len(inspect_startup(path)['errors']), 2)

    def test_allocator_never_returns_unmapped_or_unaligned_memory(self):
        oracle = RenderOracle.__new__(RenderOracle)
        oracle.bump = HEAP_BASE + HEAP_SIZE - 16
        oracle.stats = {'mallocs': 0, 'malloc_bytes': 0}
        oracle.frame = 0
        self.assertEqual(oracle.game_malloc(1) % 16, 0)
        with self.assertRaises(SystemExit):
            oracle.game_malloc(1)
        self.assertEqual(oracle.bump, HEAP_BASE + HEAP_SIZE)

    def test_win32_clock_drives_the_real_time64_conversion(self):
        exe = 'games/th13/th13.exe'
        if not os.path.isfile(exe):
            self.skipTest('original executable is not available')
        oracle = RenderOracle.__new__(RenderOracle)
        oracle.m = Machine(exe)
        oracle.uc = oracle.m.uc
        oracle.m.stub('GetSystemTimeAsFileTime', oracle._system_filetime, args_pop=4)
        output = oracle.m.malloc(8)
        oracle.uc.mem_write(output, bytes(8))
        low = oracle.m.call(0x0047AE73, args=[output])
        high = oracle.uc.reg_read(UC_X86_REG_EDX)
        self.assertEqual(low, 1577836800)
        self.assertEqual(high, 0)
        self.assertEqual(struct.unpack('<q', bytes(oracle.uc.mem_read(output, 8)))[0],
                         1577836800)

    def test_device_output_is_rejected_before_loading_an_executable(self):
        self.assertEqual(main(['battle-oracle', 'missing.exe', '.', '/dev/null']), 2)


if __name__ == '__main__':
    unittest.main()
