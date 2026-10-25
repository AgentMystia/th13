"""Run independent producers, then compare complete original/browser outputs.

Producer implementations are reviewed project code, not an adversarial sandbox.
This gate never imports a hand-written PASS receipt or manufactures reference data.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
STAGE1_SCENES = {'loading', 'title', 'difficulty', 'character', 'stage1', 'boss1',
                 'dialogue', 'pause-resume', 'stage-transition', 'stage2-entry'}
PROVIDERS = {'original': 'retail-d3d9-framebuffer', 'candidate': 'browser-webgl-framebuffer'}
ADAPTERS = {'original': ['python3', 'scripts/capture_original_delivery.py'],
            'candidate': ['node', 'scripts/capture_browser_delivery.mjs']}


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            result.update(block)
    return result.hexdigest()


def parse(text):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON number')))


def load(path):
    return parse(path.read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def exact(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(exact(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(exact(x, y) for x, y in zip(a, b))
    return a == b


def safe_file(root, name, expected=None):
    require(isinstance(name, str) and name, 'explicit file path required')
    path = root / name
    require(path.resolve().is_relative_to(root.resolve()), 'evidence path escapes producer directory')
    require(not path.is_symlink() and path.is_file() and path.stat().st_nlink == 1, 'missing/aliased evidence: ' + name)
    if expected is not None:
        require(sha(path) == expected, 'changed evidence: ' + name)
    return path


def inventory(root):
    result = {}
    prefixes = ('oracle/', 'scripts/', 'eagler-touhou/ports/th13/web/',
                'eagler-touhou/ports/th13/portable/', 'eagler-touhou/tools/build/',
                'eagler-touhou/ports/th13/tests/')
    for repo in (root, root / 'eagler-touhou'):
        process = subprocess.run(['git', '-C', str(repo), 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], capture_output=True)
        require(process.returncode == 0, 'source inventory failed')
        for name in process.stdout.split(b'\0'):
            if not name:
                continue
            relative = (repo / os.fsdecode(name)).relative_to(root).as_posix()
            path = root / relative
            if relative.startswith(prefixes) and path.is_file() and path.suffix in {'.py', '.mjs', '.cpp', '.hpp', '.h', '.html'}:
                require(not path.is_symlink(), 'source symlink')
                result[relative] = sha(path)
    return result


def source_input_identity(root, spec):
    sources = inventory(root)
    sources['verification/delivery-scenarios.json'] = sha(root / 'verification/delivery-scenarios.json')
    inputs = {}
    for name, expected in spec['inputs'].items():
        require(name.startswith(('games/th13/', 'games/replays/', 'artifacts/reference/raw/')),
                'not an explicit project asset')
        from verify import Runner
        require(not Runner.game_runtime(name) and '/.' not in name and not name.endswith('.reg'), 'runtime/registry files are not assets')
        inputs[name] = sha(safe_file(root, name, expected))
    require({'games/th13/th13.exe', spec['replay']} <= inputs.keys(), 'original executable/replay identity missing')
    return sources, inputs


def run_process(argv, root, directory, name, env, timeout):
    stdout = directory / (name + '.stdout.log')
    stderr = directory / (name + '.stderr.log')
    with stdout.open('x') as out, stderr.open('x') as err:
        process = subprocess.Popen(argv, cwd=root, env=env, stdout=out, stderr=err, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        finally:
            import signal
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    receipt = {'argv': argv, 'started': True, 'exited': True, 'exit_code': code,
               'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr)}
    (directory / (name + '.process.json')).write_text(json.dumps(receipt, indent=2))
    require(code == 0, 'producer/build failed: ' + name + '; see ' + str(stderr))
    return receipt


def compare_case(root, spec, original_dir, candidate_dir, original, candidate, nonce, source_id, input_id, projection=None):
    count = spec['frames']
    require(type(count) is int and count > 1, 'scenario must have an independently specified complete window')
    require(original['id'] == candidate['id'] == spec['id'], 'scenario identity mismatch')
    paths = [safe_file(base, record['path'], record['sha256']) for base, record in
             [(original_dir, original), (candidate_dir, candidate)]]
    require(not os.path.samefile(*paths), 'same world stream')
    milestones = set()
    with paths[0].open() as a, paths[1].open() as b:
        for stream, role in [(a, 'original'), (b, 'candidate')]:
            header = parse(next(stream))
            require(header.get('schema') == 'th13/delivery-capture/v1' and header.get('provider') == PROVIDERS[role], 'wrong capture provider')
            require(header.get('nonce') == nonce and header.get('source_identity') == source_id and header.get('input_identity') == input_id, 'stale/foreign producer')
            require(header.get('qualified') is True and header.get('qualification_unknown') == [], 'reference/backend not qualified')
            require(header.get('dimensions') == [640, 480] and header.get('format') == 'RGBA8-top-left', 'framebuffer format mismatch')
        for frame in range(count):
            left, right = parse(a.readline()), parse(b.readline())
            for row in (left, right):
                require(type(row.get('frame')) is int and row['frame'] == frame, 'missing/duplicate/out-of-order frame')
                require(row.get('complete') is True and row.get('unknown') == [] and row.get('unqualified') == [], 'incomplete effective draw/state semantics')
                require({'state', 'commands', 'input', 'input_index', 'phase', 'events', 'pixels'} <= row.keys(), 'missing behavior/input/render evidence')
            lx, rx = dict(left), dict(right)
            lp, rp = lx.pop('pixels'), rx.pop('pixels')
            require(exact(lx, rx), 'behavior/input/full draw differs at %s frame %d' % (spec['id'], frame))
            lfile = safe_file(original_dir, lp['path'], lp['sha256'])
            rfile = safe_file(candidate_dir, rp['path'], rp['sha256'])
            require(not os.path.samefile(lfile, rfile), 'same raw framebuffer bytes')
            require(lfile.stat().st_size == rfile.stat().st_size == 640 * 480 * 4, 'short framebuffer')
            with lfile.open('rb') as lbytes, rfile.open('rb') as rbytes:
                while True:
                    x, y = lbytes.read(1048576), rbytes.read(1048576)
                    require(x == y, 'actual framebuffer differs at %s frame %d' % (spec['id'], frame))
                    if not x:
                        break
            milestones.update(left['events'])
            if projection:
                for stream, row in zip(projection, (left, right)):
                    stream.write(json.dumps(row, separators=(',', ':')) + '\n')
        for stream in (a, b):
            footer = parse(stream.readline())
            require(footer.get('kind') == 'complete' and footer.get('frames') == count and footer.get('complete') is True
                    and footer.get('write_errors') == [], 'producer did not close full scenario')
            require(not stream.read().strip(), 'unexpected trailing capture')
    require(set(spec['milestones']) <= milestones, 'natural milestones missing: ' + spec['id'])
    return count


def verify(root, scope):
    # This reviewed specification belongs to the final capture task, not port output.
    configuration = load(root / 'verification/delivery-scenarios.json')
    require(configuration.get('schema') == 'th13/delivery-scenarios/v1', 'scenario contract missing')
    spec = configuration[scope]
    cases = spec['cases']
    names = {case['id'] for case in cases}
    require(len(names) == len(cases) and STAGE1_SCENES <= names and 'mainline' in names, 'required scene/mainline coverage missing')
    if scope == 'full-game':
        required = load(root / 'verification/full-game-scenes.json')['required']
        require(set(required) <= names, 'full-game required branch coverage missing')
    sources, inputs = source_input_identity(root, spec)
    from oracle.replayfmt import ReplayFile
    replay = ReplayFile().decode((root / spec['replay']).read_bytes())
    replay_frames = replay.stages[0].frame_count
    mainline = next(c for c in cases if c['id'] == 'mainline')
    require(mainline['frames'] >= replay_frames, 'mainline shorter than actual Stage1 input')
    require({'boss-defeated', 'stage1-cleared', 'stage2-entered'} <= set(mainline['milestones']), 'mainline completion milestones not required')
    for argv in ADAPTERS.values():
        safe_file(root, argv[1])
    nonce = uuid.uuid4().hex
    directory = root / 'artifacts/verification/delivery-runs' / nonce
    directory.mkdir(parents=True)
    source_id, input_id = digest(sources), digest(inputs)
    context = {'schema': 1, 'nonce': nonce, 'scope': scope, 'source_identity': source_id,
               'input_identity': input_id, 'sources': sources, 'inputs': inputs, 'scenarios': spec}
    context_path = directory / 'context.json'
    context_path.write_text(json.dumps(context, indent=2))
    env = os.environ.copy()
    # The producer cannot write a parent verification receipt.
    for key in list(env):
        if key.startswith('VERIFY_'):
            del env[key]
    receipts = {}
    manifests = {}
    for role, command in ADAPTERS.items():
        output = directory / role
        output.mkdir()
        argv = command + ['--context', str(context_path), '--out', str(output)]
        receipts[role] = run_process(argv, root, directory, role, env, 7200)
        manifest = load(safe_file(output, 'manifest.json'))
        require(manifest.get('nonce') == nonce and manifest.get('provider') == PROVIDERS[role], 'unbound production manifest')
        require(manifest.get('complete') is True and manifest.get('write_errors') == [], 'producer completion failed')
        require(len(manifest['cases']) == len(names) and {c['id'] for c in manifest['cases']} == names, 'producer skipped/duplicated scenarios')
        manifests[role] = {c['id']: c for c in manifest['cases']}
    require(sha(root / 'verification/delivery-scenarios.json') == sources['verification/delivery-scenarios.json'], 'scenario contract changed during capture')
    require(inventory(root) == {k: v for k, v in sources.items() if k != 'verification/delivery-scenarios.json'}, 'source changed during capture')
    for name, expected in inputs.items():
        safe_file(root, name, expected)
    destination = Path(os.environ['VERIFY_RESULT_PATH']) if os.environ.get('VERIFY_RESULT_PATH') else directory / 'verified.json'
    evidence = [destination.parent / 'original.mainline.jsonl', destination.parent / 'candidate.mainline.jsonl']
    with evidence[0].open('x') as a, evidence[1].open('x') as b:
        for case in cases:
            compare_case(root, case, directory / 'original', directory / 'candidate',
                         manifests['original'][case['id']], manifests['candidate'][case['id']], nonce, source_id, input_id,
                         (a, b) if case['id'] == 'mainline' else None)
    result = {'scope': scope, 'passed': True, 'frames': mainline['frames'], 'scenarios': sorted(names),
              'run': str(directory.relative_to(root)), 'limitation': 'Only the declared original/configuration/input coverage is certified'}
    if os.environ.get('VERIFY_RESULT_PATH'):
        receipt = dict(schema=1, run_id=os.environ['VERIFY_RUN_ID'], check_id=os.environ['VERIFY_CHECK_ID'],
                       identity=os.environ['VERIFY_IDENTITY'], type='FIDELITY_PASS', passed=True,
                       summary='Fresh independent original and production browser processes; full state/draw/input and raw RGBA bytes compared exactly',
                       ignored=[], evidence=[{'role': role, 'path': str(path.relative_to(root)), 'sha256': sha(path)}
                                             for role, path in zip(('original', 'candidate'), evidence)])
        with destination.open('x') as output:
            json.dump(receipt, output)
    return result


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--scope', choices=['stage1', 'full-game'], required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(ROOT, args.scope), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration, subprocess.SubprocessError) as error:
        print(json.dumps({'scope': args.scope, 'passed': False, 'status': 'BLOCKED', 'reason': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
