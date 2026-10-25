"""Synthetic protocol controls ONLY: fake adapters are NOT retail qualification.
Run in th13-dev: python3 artifacts/verification/autonomy-pending/test_delivery.py -v
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('delivery_under_review', Path(__file__).with_name('verify_delivery.py'))
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

# Real subprocesses, but entirely synthetic capture providers for protocol tests.
EMITTER = '''import hashlib, json, pathlib, sys
role = sys.argv[1]
args = sys.argv[2:]
context = json.loads(pathlib.Path(args[args.index('--context')+1]).read_text())
out = pathlib.Path(args[args.index('--out')+1])
provider = 'retail-d3d9-framebuffer' if role == 'original' else 'browser-webgl-framebuffer'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
pixels = out / 'frame.rgba'
pixels.write_bytes(bytes(640*480*4))
header = dict(schema='th13/delivery-capture/v1', provider=provider, nonce=context['nonce'],
 source_identity=context['source_identity'], input_identity=context['input_identity'],
 qualified=True, qualification_unknown=[], dimensions=[640,480], format='RGBA8-top-left')
cases = []
for case in context['scenarios']['cases']:
 rows = [header]
 for frame in range(case['frames']):
  rows.append(dict(frame=frame, complete=True, unknown=[], unqualified=[], state={'value':0},
   commands=[], input=[0]*6, input_index=frame, phase='pre-update', events=case['milestones'] if frame == case['frames']-1 else [],
   pixels=dict(path=pixels.name, sha256=sha(pixels))))
 rows.append(dict(kind='complete', frames=case['frames'], complete=True, write_errors=[]))
 path = out / (case['id']+'.jsonl')
 path.write_text(''.join(json.dumps(row)+'\\n' for row in rows))
 cases.append(dict(id=case['id'], path=path.name, sha256=sha(path)))
(out/'manifest.json').write_text(json.dumps(dict(nonce=context['nonce'], provider=provider, complete=True, write_errors=[], cases=cases)))
'''


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(self.root), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        self.environment = mock.patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        for repo in (self.root, self.root / 'eagler-touhou'):
            repo.mkdir(exist_ok=True)
            subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True, capture_output=True)
        self.put('.gitignore', 'artifacts/\ngames/\n')
        self.put('games/th13/th13.exe', b'fake, not retail')
        self.put('games/replays/test.rpy', bytes(12))
        self.put('scripts/fake_emit.py', EMITTER)
        self.put('scripts/capture_original_delivery.py', "import runpy,sys\nsys.argv.insert(1,'original')\nrunpy.run_path('scripts/fake_emit.py',run_name='__main__')\n")
        self.put('scripts/capture_browser_delivery.mjs', "import {spawnSync} from 'node:child_process';\nconst p=spawnSync('python3',['scripts/fake_emit.py','candidate',...process.argv.slice(2)],{stdio:'inherit'});\nprocess.exit(p.error||p.signal||p.status===null?2:p.status);\n")
        cases = [dict(id=name, frames=2, milestones=[]) for name in sorted(gate.STAGE1_SCENES)]
        cases.append(dict(id='mainline', frames=2, milestones=['boss-defeated', 'stage1-cleared', 'stage2-entered']))
        self.spec = dict(replay='games/replays/test.rpy', inputs={name: gate.sha(self.root / name)
                         for name in ['games/th13/th13.exe', 'games/replays/test.rpy']}, cases=cases)
        self.configuration()
        # Replay parsing itself is outside this synthetic protocol test.
        replay_module = types.ModuleType('oracle.replayfmt')
        class FakeReplay:
            def decode(self, data):
                return types.SimpleNamespace(stages=[types.SimpleNamespace(frame_count=2)])
        replay_module.ReplayFile = FakeReplay
        patch = mock.patch.dict('sys.modules', {'oracle.replayfmt': replay_module})
        patch.start()
        self.addCleanup(patch.stop)
        self.base = self.root / 'artifacts/direct'
        self.base.mkdir(parents=True)
        self.case = dict(id='mainline', frames=2, milestones=[])
        self.records = []
        for role in ('original', 'candidate'):
            pix = self.put('artifacts/direct/' + role + '.rgba', bytes(640 * 480 * 4))
            rows = [dict(schema='th13/delivery-capture/v1', provider=gate.PROVIDERS[role], nonce='nonce',
                         source_identity='source', input_identity='input', qualified=True, qualification_unknown=[],
                         dimensions=[640,480], format='RGBA8-top-left')]
            rows += [dict(frame=frame, complete=True, unknown=[], unqualified=[], state={'value':0}, commands=[],
                          input=[0]*6, input_index=frame, phase='pre-update', events=[],
                          pixels=dict(path=Path(pix).name, sha256=gate.sha(self.root / pix))) for frame in range(2)]
            rows.append(dict(kind='complete', frames=2, complete=True, write_errors=[]))
            record = dict(id='mainline', path=role + '.jsonl')
            self.records.append(record)
            self.save(record, rows)

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return name

    def configuration(self):
        self.put('verification/delivery-scenarios.json', json.dumps({'schema':'th13/delivery-scenarios/v1', 'stage1':self.spec}))

    def rows(self, index):
        return [gate.parse(line) for line in (self.base / self.records[index]['path']).read_text().splitlines()]

    def save(self, record, rows):
        path = self.base / record['path']
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        record['sha256'] = gate.sha(path)

    def compare(self):
        return gate.compare_case(self.root, self.case, self.base, self.base, *self.records, 'nonce', 'source', 'input')

    def test_actual_fake_adapter_processes_positive_protocol_only(self):
        self.assertEqual(self.compare(), 2)
        result = gate.verify(self.root, 'stage1')
        self.assertTrue(result['passed'])
        directory = self.root / result['run']
        for role in ('original', 'candidate'):
            receipt = gate.load(directory / (role + '.process.json'))
            self.assertEqual(receipt['exit_code'], 0)
            self.assertIn('--context', receipt['argv'])
            self.assertNotEqual(gate.sha(directory / (role + '.stdout.log')), None)

    def test_receipt_only_without_adapter_rejected(self):
        # A success-looking receipt is not consumed as a substitute producer.
        self.put('scripts/capture_original_delivery.py', '')
        (self.root / 'scripts/capture_original_delivery.py').unlink()
        self.put('artifacts/verification/delivery/stage1.json', json.dumps({'passed':True, 'exit_code':0}))
        with self.assertRaisesRegex(ValueError, 'missing/aliased evidence'):
            gate.verify(self.root, 'stage1')

    def test_same_raw_pixels_rejected(self):
        rows = self.rows(1)
        for frame in (1, 2):
            rows[frame]['pixels'] = copy.deepcopy(self.rows(0)[frame]['pixels'])
        self.save(self.records[1], rows)
        with self.assertRaisesRegex(ValueError, 'same raw framebuffer'):
            self.compare()

    def test_pixel_mutation_rejected_with_updated_hash(self):
        self.put('artifacts/direct/candidate.rgba', b'\x01' + bytes(640*480*4-1))
        rows = self.rows(1)
        for frame in (1, 2):
            rows[frame]['pixels']['sha256'] = gate.sha(self.base / 'candidate.rgba')
        self.save(self.records[1], rows)
        with self.assertRaisesRegex(ValueError, 'actual framebuffer differs'):
            self.compare()

    def test_false_vs_zero_rejected(self):
        rows = self.rows(1)
        rows[1]['state']['value'] = False
        self.save(self.records[1], rows)
        with self.assertRaisesRegex(ValueError, 'behavior/input/full draw differs'):
            self.compare()

    def test_changed_input_hash_is_recomputed(self):
        _, before = gate.source_input_identity(self.root, self.spec)
        self.put(self.spec['replay'], b'changed replay')
        with self.assertRaisesRegex(ValueError, 'changed evidence'):
            gate.source_input_identity(self.root, self.spec)
        self.spec['inputs'][self.spec['replay']] = gate.sha(self.root / self.spec['replay'])
        _, after = gate.source_input_identity(self.root, self.spec)
        self.assertNotEqual(gate.digest(before), gate.digest(after))

    def test_short_window_and_missing_scenario_rejected(self):
        self.case['frames'] = 3
        with self.assertRaises(ValueError):
            self.compare()
        self.spec['cases'][-1]['frames'] = 1
        self.configuration()
        with self.assertRaisesRegex(ValueError, 'mainline shorter'):
            gate.verify(self.root, 'stage1')
        self.spec['cases'].pop(0)
        self.configuration()
        with self.assertRaisesRegex(ValueError, 'coverage missing'):
            gate.verify(self.root, 'stage1')

    def test_duplicate_key_rejected_even_with_updated_hash(self):
        path = self.base / self.records[1]['path']
        path.write_text(path.read_text().replace('"frame": 0', '"frame": 0, "frame": 0', 1))
        self.records[1]['sha256'] = gate.sha(path)
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            self.compare()


if __name__ == '__main__':
    unittest.main()
