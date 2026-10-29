import copy
import json
import tempfile
import unittest
from pathlib import Path

from oracle.replay_core_compare import GLOBAL_KEYS, compare


class CoreCompareTest(unittest.TestCase):
    def row(self):
        return {'schema': 'th13-replay-core-v2', 'frame': 0, 'stage': 1,
                'input': dict(held=1, pressed=1, released=0),
                'globals': dict.fromkeys(GLOBAL_KEYS, 0),
                'player': dict(x_bits=0, y_bits=0),
                'rng': dict(game_seed=0, game_index=0, visual_seed=0, visual_index=0)}

    def run_pair(self, left, right, frames=1):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('original', 'web')]
            for path, rows in zip(paths, (left, right)):
                path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            return compare(*paths, frames, 1)

    def test_equal_core_does_not_certify_all_state(self):
        result = self.run_pair([self.row()], [self.row()])
        self.assertTrue(result['coreEqual'])
        self.assertFalse(result['completeStateCertified'])

    def test_one_bit_coordinate_difference_fails(self):
        a = self.row(); b = copy.deepcopy(a); b['player']['x_bits'] = 0x80000000
        self.assertEqual(self.run_pair([a], [b])['firstDifference']['field'], 'player.x_bits')

    def test_missing_or_unknown_or_null_field_fails(self):
        for transform in (lambda r: r.pop('rng'), lambda r: r.update(extra=0),
                          lambda r: r.update(player=None)):
            b = self.row(); transform(b)
            with self.assertRaises(ValueError): self.run_pair([self.row()], [b])

    def test_common_truncation_and_common_missing_frame_fail(self):
        with self.assertRaises(ValueError): self.run_pair([self.row()], [self.row()], 2)
        a = self.row(); a['frame'] = 1
        with self.assertRaises(ValueError): self.run_pair([a], [a])

    def test_duplicate_or_wrong_stage_fails(self):
        with self.assertRaises(ValueError): self.run_pair([self.row()] * 2, [self.row()] * 2, 2)
        a = self.row(); a['stage'] = 2
        with self.assertRaises(ValueError): self.run_pair([a], [a])

    def test_same_file_rejected(self):
        with tempfile.NamedTemporaryFile() as file:
            with self.assertRaises(ValueError): compare(file.name, file.name, 1, 1)


if __name__ == '__main__': unittest.main()
