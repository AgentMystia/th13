"""Synthetic protocol controls; no game data required."""
import contextlib
import io
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import unittest
from oracle import draw_compare as draw, stage1_compare as event


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Path(self.tmp.name) / 'a.jsonl'
        self.b = Path(self.tmp.name) / 'b.jsonl'

    def run_compare(self, module=draw, left=None, right=None, options=()):
        default = [{'frame': 0, 'viewport': ['0', '0', '640', '480'], 'ops': []}]
        for path, rows in ((self.a, default if left is None else left), (self.b, default if right is None else right)):
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = module.main(['compare', str(self.a), str(self.b), *options])
        lines = out.getvalue().splitlines()
        self.assertEqual(sum(line.startswith('RESULT ') for line in lines), 1)
        result = json.loads(next(line[12:] for line in lines if line.startswith('RESULT_JSON ')))
        self.assertEqual(result['exit_code'], code)
        return code, result

    def test_qualified_and_legacy_diagnostic(self):
        self.assertEqual(self.run_compare(options=['--expected-frames', '1'])[0], 0)
        code, result = self.run_compare()
        self.assertEqual(code, 3)
        self.assertFalse(result['qualified'])
        self.assertEqual(result['capture_completion'], 'not-attested')

    def test_common_truncation_empty_and_holes(self):
        self.assertEqual(self.run_compare(options=['--expected-frames', '2'])[0], 2)
        for rows in ([], [{'frame': 1, 'viewport': [], 'ops': []}],
                     [{'frame': 0, 'viewport': [], 'ops': []}] * 2,
                     [{'frame': True, 'viewport': [], 'ops': []}], [{'frame': 0, 'ops': []}]):
            with self.subTest(rows=rows):
                self.assertEqual(self.run_compare(left=rows, right=rows)[0], 2)

    def test_tail_validated_after_divergence(self):
        left = [{'frame': 0, 'viewport': ['1'], 'ops': []}, {'frame': 0, 'viewport': [], 'ops': []}]
        right = [{'frame': 0, 'viewport': ['2'], 'ops': []}]
        self.assertEqual(self.run_compare(left=left, right=right)[0], 2)

    def test_strict_float_and_ulp_diagnostic(self):
        left = [{'frame': 0, 'viewport': ['1'], 'ops': []}]
        right = [{'frame': 0, 'viewport': ['1.00000001'], 'ops': []}]
        self.assertEqual(self.run_compare(left=left, right=right, options=['--expected-frames', '1'])[0], 1)
        self.assertEqual(self.run_compare(left=left, right=right, options=['--expected-frames', '1', '--ulp', '1'])[0], 3)
        self.assertEqual(self.run_compare(options=['--expected-frames', '1', '--ignore', 'tfactor'])[0], 3)

    def test_unsigned_ulp_and_nan_contract(self):
        tiny = struct.unpack('<f', struct.pack('<I', 1))[0]
        self.assertEqual(draw.ulp_diff(-tiny, tiny), 2)
        self.assertEqual(draw.ulp_diff(-0.0, 0.0), 0)
        self.assertEqual(draw.ulp_diff(-1.0, -1.0000001192092896), 1)
        self.assertEqual(draw.ulp_diff(math.nan, math.nan), 0)
        self.assertEqual(draw.ulp_diff(math.nan, 0), math.inf)
        self.assertFalse(draw.cmp_float(math.inf, 3.4e38, 100000))
        self.assertTrue(draw.cmp_float('nan', 'NaN', 0))

    def test_alias_and_duplicate_json_key(self):
        self.a.write_text('{"frame":0,"frame":0,"viewport":[],"ops":[]}\n')
        self.b.write_text(self.a.read_text())
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(draw.main(['compare', str(self.a), str(self.b)]), 2)
            self.b.unlink()
            os.link(self.a, self.b)
            self.assertEqual(draw.main(['compare', str(self.a), str(self.b)]), 2)

    def test_unknown_options(self):
        for module in (draw, event):
            for option in (['--typo'], ['--expected-f', '1'], ['--ul', '1']):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    module.main(['compare', str(self.a), str(self.b), *option])
                self.assertEqual(error.exception.code, 2)

    def test_sparse_events_do_not_guess_window(self):
        rows = [{'frame': 10, 'seq': 0, 'value': 1.0}]
        code, result = self.run_compare(event, rows, rows)
        self.assertEqual(code, 3)
        self.assertIsNone(result['window'])
        code, result = self.run_compare(event, rows, rows, ['--expected-frames', '100'])
        self.assertEqual(code, 0)
        self.assertEqual(result['window'], 100)
        self.assertEqual(result['capture_completion'], 'not-attested')
        self.assertEqual(self.run_compare(event, rows, rows, ['--expected-frames', '10'])[0], 2)
        self.assertEqual(self.run_compare(event, rows * 2, rows * 2)[0], 2)
        self.assertEqual(self.run_compare(event, [], [])[0], 2)

    def test_event_float_not_rounded_to_binary32(self):
        left = [{'frame': 0, 'seq': 0, 'value': 1.0}]
        right = [{'frame': 0, 'seq': 0, 'value': 1.00000001}]
        self.assertEqual(self.run_compare(event, left, right, ['--expected-frames', '2'])[0], 1)

    def test_event_nan_rule_is_not_draw_nan_rule(self):
        for ulp in (0, 1):
            self.assertTrue(draw.cmp_float(math.nan, math.nan, ulp))
            rows = [{'frame': 0, 'seq': 0, 'args': [math.nan]}]
            self.assertEqual(self.run_compare(event, rows, rows,
                ['--expected-frames', '2', '--ulp', str(ulp)])[0], 1)

    def test_asymmetric_tail_and_malformed_tail_rejected(self):
        rows = [{'frame': i, 'viewport': [], 'ops': []} for i in range(3)]
        self.assertEqual(self.run_compare(left=rows, right=rows[:2], options=['--expected-frames', '3'])[0], 2)
        self.a.write_text('{"frame":0,"viewport":[],"ops":[]}\n\n')
        self.b.write_text('{"frame":0,"viewport":[],"ops":[]}\n')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(draw.main(['compare', str(self.a), str(self.b), '--expected-frames', '1']), 2)

    def test_streaming_generator(self):
        self.a.write_text('{"frame":0,"viewport":[],"ops":[]}\ninvalid\n')
        iterator = draw.records(self.a)
        self.assertEqual(next(iterator)['frame'], 0)
        with self.assertRaises(ValueError):
            next(iterator)


if __name__ == '__main__':
    unittest.main()
