import copy
import json
from pathlib import Path
import tempfile
import unittest

from oracle.replay_continuous_compare import compare
from oracle.replay_core_compare import GLOBAL_KEYS


class ContinuousCompareTests(unittest.TestCase):
    def fixture(self):
        def core(frame):
            return dict(schema='th13-replay-core-v2', frame=frame, stage=2,
                        input=dict(held=1, pressed=0, released=0),
                        globals=dict.fromkeys(GLOBAL_KEYS, 0),
                        player=dict(x_bits=0, y_bits=0),
                        rng=dict(game_seed=0, game_index=0, visual_seed=0, visual_index=0))
        def state(slot, frame):
            return dict(slot_index=slot-1, slot_number=slot, slot_frame=frame,
                        slot_frames=1, consumed=frame, recorded_position=slot-1+frame,
                        current_stage=2)
        native = [core(0), core(1)]
        audit = [dict(frame=i, status='input', slot=i+1, slot_frame=0, slot_frame_after=1,
                      start=100+i*20, cursor=100+i*20, cursor_after=106+i*20,
                      end=106+i*20, input=[1, 0, 0], expected=[1, 0, 0]) for i in range(2)]
        # Current stage2 begins before the Replay slot1 input is exhausted.
        # Activation consumes new slot frame0 in the same host tick.
        web = [dict(schema='th13/web-continuous-state/v1', host_tick=0,
                    event='input', sample_index=0, before=state(1, 0),
                    after=state(1, 1), core=core(0)),
               dict(schema='th13/web-continuous-state/v1', host_tick=1,
                    event='input', sample_index=1, before=state(1, 1),
                    after=state(2, 1), core=core(0))]
        return native, audit, web

    def run_case(self, data, frames=2):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/name for name in ('native', 'audit', 'web')]
            for path, rows in zip(paths, data):
                path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            return compare(*paths, frames)

    def test_cross_slot_keeps_local_frame_and_live_current_stage(self):
        data = self.fixture()
        result = self.run_case(data)
        self.assertTrue(result['coreEqual'])
        self.assertEqual(data[2][1]['core']['frame'], 0)
        self.assertFalse(result['completeStateCertified'])
        self.assertFalse(result['idleHostOutputCertified'])

    def test_numeric_one_bit_and_live_stage_difference_fail(self):
        for change in ('bit', 'stage'):
            data = self.fixture()
            if change == 'bit': data[2][1]['core']['player']['y_bits'] = 1
            else: data[2][1]['core']['stage'] = data[2][1]['after']['current_stage'] = 1
            self.assertFalse(self.run_case(data)['coreEqual'])

    def test_wrong_slot_frame_cursor_and_extent_are_rejected(self):
        for change in ('slot', 'frame', 'cursor', 'extent', 'early'):
            data = self.fixture()
            if change == 'slot': data[2][1]['after']['slot_number'] = 3
            elif change == 'frame': data[2][1]['core']['frame'] = 1
            elif change == 'cursor': data[1][1]['cursor_after'] += 6
            elif change == 'extent': data[2][1]['after']['slot_frames'] = 2
            else: data[1][0]['end'] += 6;data[2][0]['after']['slot_frames'] = 2
            with self.assertRaises(ValueError): self.run_case(data)

    def test_common_truncation_cannot_pass(self):
        data = tuple(rows[:1] for rows in self.fixture())
        with self.assertRaises(ValueError): self.run_case(data)

    def test_missing_host_tick_or_state_continuity_rejected(self):
        for change in ('host', 'state'):
            data = self.fixture()
            if change == 'host': data[2][1]['host_tick'] = 2
            else: data[2][1]['before']['consumed'] = 0
            with self.assertRaises(ValueError): self.run_case(data)

    def test_idle_ticks_preserved_without_certifying_their_output(self):
        data = self.fixture()
        idle = copy.deepcopy(data[2][0]);idle.update(host_tick=1,event='idle',sample_index=None)
        idle['before'] = copy.deepcopy(idle['after'])
        data[2][1]['host_tick'] = 2;data[2].insert(1, idle)
        result = self.run_case(data)
        self.assertTrue(result['coreEqual'])
        self.assertFalse(result['idleHostOutputCertified'])


if __name__ == '__main__': unittest.main()
