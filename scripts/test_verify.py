"""Synthetic, container-run negative controls for the verification guardrails.

These tests do not certify original-game qualification or any framebuffer.
"""
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from verify import Blocked, RECONCILE_MAINTENANCE, RECONCILE_OWNER, RECONCILE_PATHS, ROOT, Runner, atomic, digest, load, sha


PRODUCER = r'''
import json, os, sys
from pathlib import Path
mode = sys.argv[1]
if mode == 'exit-none':
    print('RESULT none')
    sys.exit(7)
if mode == 'no-result':
    print('PASS')
    sys.exit(0)
if mode == 'interrupt':
    os.kill(os.getppid(), 2)
    import time
    time.sleep(60)
result = dict(schema=1, run_id=os.environ['VERIFY_RUN_ID'],
              check_id=os.environ['VERIFY_CHECK_ID'], identity=os.environ['VERIFY_IDENTITY'],
              type='TOOL_PASS', passed=True, summary='synthetic test only', ignored=[], evidence=[])
if mode == 'stale': result['identity'] = 'old'
if mode == 'filter': result['ignored'] = ['vertices']
if mode == 'false': result['passed'] = False
if mode == 'none': result['type'] = 'none'
if mode == 'true-exit':
    Path(os.environ['VERIFY_RESULT_PATH']).write_text(json.dumps(result))
    sys.exit(9)
if mode.startswith('capture-'):
    result['type'] = 'FIDELITY_PASS'
    directory = Path(os.environ['VERIFY_RESULT_PATH']).parent
    for role in ['reference', 'port']:
        p = directory / (role + '.jsonl')
        frames = [10, 11, 12]
        if mode == 'capture-short': frames = [10, 11]
        if mode == 'capture-missing': frames = [10, 12]
        if mode == 'capture-duplicate': frames = [10, 11, 11]
        if role == 'port' and mode == 'capture-same': p = directory / 'reference.jsonl'
        elif role == 'port' and mode == 'capture-link': os.link(directory / 'reference.jsonl', p)
        else: p.write_text(''.join(json.dumps(dict(frame=f)) + '\n' for f in frames))
        import hashlib
        result['evidence'].append(dict(role=role, path=str(p.relative_to(Path.cwd())),
                                      sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
if mode == 'mutate-policy':
    p = Path('verification/policy.json')
    p.write_text(p.read_text() + '\n')
if mode == 'mutate-outside': Path('outside.txt').write_text('unowned')
if mode == 'mutate-input': Path('src/input.txt').write_text('changed during run')
Path(os.environ['VERIFY_RESULT_PATH']).write_text(json.dumps(result))
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('verification', 'scripts/verification_lib', 'src', 'eagler-touhou'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / 'verification/policy.json', self.root / 'verification/policy.json')
        shutil.copy(ROOT / 'scripts/verify.py', self.root / 'scripts/verify.py')
        shutil.copy(ROOT / 'scripts/test_verify.py', self.root / 'scripts/test_verify.py')
        shutil.copy(ROOT / 'scripts/verification_lib/check_command.py', self.root / 'scripts/verification_lib/check_command.py')
        (self.root / 'scripts/producer.py').write_text(PRODUCER)
        (self.root / 'src/input.txt').write_text('initial')
        (self.root / 'outside.txt').write_text('existing dirty work')
        (self.root / 'eagler-touhou/dirty.txt').write_text('existing inner work')
        (self.root / '.gitignore').write_text('artifacts/\n__pycache__/\neagler-touhou/\n')
        for repo in (self.root, self.root / 'eagler-touhou'):
            subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True)
        # 内层 ignored 构建产物夹具（在任何 Runner/baseline 之前建立——
        # 基线必须把该状态视为"开工前既存"）：build_out/ 被 .gitignore
        # 声明 ignore；src_real.cpp 为真实跟踪源码。
        inner = self.root / 'eagler-touhou'
        (inner / '.gitignore').write_text('build/\n**/artifacts/\nnode_modules/\n')
        (inner / 'ports').mkdir(parents=True, exist_ok=True)
        (inner / 'ports/build').mkdir(parents=True, exist_ok=True)
        (inner / 'ports/build/game.bin').write_text('built once')
        (inner / 'ports/src_real.cpp').write_text('real source')
        subprocess.run(['git', '-C', str(inner), 'add', '.gitignore',
                        'ports/src_real.cpp'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(inner), 'commit', '-q', '-m', 'init'],
                       check=True, capture_output=True,
                       env={**os.environ,
                            'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
                            'GIT_COMMITTER_NAME': 't',
                            'GIT_COMMITTER_EMAIL': 't@t'})
        self.task = dict(id='foundation-runner', goal='synthetic guardrail test',
                         non_goals=['not fidelity'], read=['src/input.txt'], allowed=['src', 'verification/tasks.json'],
                         protected=['verification/policy.json'], depends=[], inputs=['src/input.txt'],
                         checks={tier: [self.check('pass')] for tier in ('fast', 'full')},
                         evidence=['closed receipts'], stop='three rounds then mechanism review',
                         max_rounds=3, completion='TOOL_PASS', blocked_reason='')
        self.write_tasks([self.task])
        self.r = Runner(self.root)
        self.r.refresh()
        self.r.start(self.task['id'])

    def check(self, mode, fidelity=False):
        return dict(id='synthetic', argv=[sys.executable, 'scripts/producer.py', mode], cwd='.',
                    timeout=10, result_type='FIDELITY_PASS' if fidelity else 'TOOL_PASS',
                    window=dict(start=10, end=12, roles=['reference', 'port']) if fidelity else None,
                    external=False)

    def write_tasks(self, tasks):
        atomic(self.root / 'verification/tasks.json', dict(schema=1, tasks=tasks))

    def reset_graph(self, tasks, owner='foundation-runner'):
        """Rebuild the synthetic run from a graph present before any baseline."""
        shutil.rmtree(self.r.home, ignore_errors=True)
        self.write_tasks(tasks)
        self.r = Runner(self.root)
        self.r.refresh()
        self.r.start(owner)

    def configure(self, mode, fidelity=False):
        self.task['checks'] = {tier: [self.check(mode, fidelity)] for tier in ('fast', 'full')}
        self.task['completion'] = 'FIDELITY_PASS' if fidelity else 'TOOL_PASS'
        self.reset_graph([self.task])

    def pair(self):
        a = self.r.run('foundation-runner', 'fast')
        self.assertEqual(a['type'], 'TOOL_PASS', a)
        b = self.r.run('foundation-runner', 'full')
        self.assertEqual(b['type'], self.task['completion'], b)
        return b

    def test_success_dirty_baselines_both_repos_and_handoff(self):
        (self.root / 'src/input.txt').write_text('authorized change')
        self.pair()
        s = self.r.finish('foundation-runner')
        self.assertEqual(s['status'], 'DONE')
        self.assertEqual(s['rounds'], 1)
        self.r.refresh()
        self.assertEqual(s['status'], 'DONE')
        handoff = self.r.handoff()
        self.assertTrue((self.root / handoff['artifact']).is_file())
        self.assertEqual((self.root / 'outside.txt').read_text(), 'existing dirty work')
        self.assertEqual((self.root / 'eagler-touhou/dirty.txt').read_text(), 'existing inner work')

    def test_full_requires_current_fast_and_finish_requires_full(self):
        with self.assertRaises(Blocked): self.r.run('foundation-runner', 'full')
        self.r.run('foundation-runner', 'fast')
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'BLOCKED')
        self.r.run('foundation-runner', 'full')
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')

    def test_changed_revision_requires_new_fast_and_counts_once(self):
        self.r.run('foundation-runner', 'fast')
        (self.root / 'src/input.txt').write_text('revision two')
        with self.assertRaises(Blocked): self.r.run('foundation-runner', 'full')
        self.pair()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['rounds'], 2)

    def test_three_revision_limit_not_six_tiers(self):
        for revision in range(3):
            (self.root / 'src/input.txt').write_text(str(revision))
            self.pair()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['rounds'], 3)
        (self.root / 'src/input.txt').write_text('four')
        with self.assertRaisesRegex(Blocked, 'budget'): self.r.run('foundation-runner', 'fast')

    def test_failure_reruns_consume_budget(self):
        self.configure('false')
        for _ in range(3): self.assertEqual(self.r.run('foundation-runner', 'fast')['type'], 'BLOCKED')
        with self.assertRaisesRegex(Blocked, 'budget'): self.r.run('foundation-runner', 'fast')

    def test_same_file_hardlink_common_truncation_and_missing_frames(self):
        for mode in ('capture-same', 'capture-link', 'capture-short', 'capture-missing', 'capture-duplicate'):
            with self.subTest(mode=mode):
                self.configure(mode, fidelity=True)
                run = dict(id='capture-test-' + mode, identity='synthetic')
                directory = self.r.home / run['id']
                directory.mkdir()
                with self.assertRaises(Blocked): self.r.execute(self.check(mode, True), run, directory)

    def test_exact_window_accepts_only_integrity_not_game_truth(self):
        self.configure('capture-pass', fidelity=True)
        self.pair()
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')
        # This uses a synthetic authorized adapter: it tests runner mechanics only.

    def test_none_exit_missing_result_filter_stale_and_false(self):
        for mode in ('exit-none', 'no-result', 'filter', 'stale', 'false', 'none', 'true-exit'):
            with self.subTest(mode=mode):
                run = dict(id='negative-' + mode, identity='current')
                directory = self.r.home / run['id']
                directory.mkdir()
                with self.assertRaises(Blocked): self.r.execute(self.check(mode), run, directory)

    def test_scope_protects_outer_and_inner_existing_dirty(self):
        for name in ('outside.txt', 'eagler-touhou/dirty.txt', 'verification/policy.json'):
            with self.subTest(name=name):
                p = self.root / name
                original = p.read_bytes()
                p.write_bytes(original + b'changed')
                with self.assertRaisesRegex(Blocked, 'out-of-scope'): self.r.check_boundary('foundation-runner')
                p.write_bytes(original)

    def test_delete_and_new_unowned_files_detected(self):
        p = self.root / 'new-unowned.txt'
        p.write_text('new')
        with self.assertRaises(Blocked): self.r.check_boundary('foundation-runner')
        p.unlink()
        (self.root / 'outside.txt').unlink()
        with self.assertRaises(Blocked): self.r.check_boundary('foundation-runner')

    def test_changes_during_process_not_certified(self):
        for mode in ('mutate-input', 'mutate-policy', 'mutate-outside'):
            with self.subTest(mode=mode):
                self.configure(mode)
                saved = {n: (self.root / n).read_bytes() for n in ('src/input.txt', 'verification/policy.json', 'outside.txt')}
                self.assertEqual(self.r.run('foundation-runner', 'fast')['type'], 'BLOCKED')
                for n, content in saved.items(): (self.root / n).write_bytes(content)

    def test_expired_input_result_log_and_baseline_rejected(self):
        self.pair()
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')
        original = (self.root / 'src/input.txt').read_bytes()
        (self.root / 'src/input.txt').write_text('new')
        self.r.refresh()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['status'], 'STALE')
        (self.root / 'src/input.txt').write_bytes(original)
        s = self.r.state['tasks']['foundation-runner']
        run = load(self.r.home / s['receipt'] / 'run.json')
        for p in (self.root / run['checks'][0]['result'], self.r.home / run['id'] / 'synthetic.stdout.log', self.root / s['baseline']):
            with self.subTest(path=p):
                data = p.read_bytes()
                p.write_bytes(data + b'\n')
                with self.assertRaises(Blocked): self.r.validate_full('foundation-runner', s['receipt'], boundary=True)
                p.write_bytes(data)

    def test_fast_receipt_revalidated_after_full(self):
        self.pair()
        s = self.r.state['tasks']['foundation-runner']
        p = self.r.home / s['fast_receipt'] / 'synthetic.result.json'
        p.write_text('{}')
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'BLOCKED')

    def test_unrelated_task_configuration_does_not_stale_completed_task(self):
        self.pair()
        self.r.finish('foundation-runner')
        before = self.r.identity('foundation-runner')
        other = copy.deepcopy(self.task)
        other.update(id='future-task', depends=['foundation-runner'], blocked_reason='not configured', checks=dict(fast=[], full=[]))
        self.write_tasks([self.task, other])
        r = Runner(self.root)
        r.refresh()
        self.assertEqual(before, r.identity('foundation-runner'))
        self.assertEqual(r.state['tasks']['foundation-runner']['status'], 'DONE')
        self.assertEqual(r.state['tasks']['future-task']['status'], 'BLOCKED')
        other['goal'] = 'new future scope'
        self.write_tasks([self.task, other])
        r = Runner(self.root)
        r.refresh()
        self.assertEqual(r.state['tasks']['foundation-runner']['status'], 'DONE')
        self.task['goal'] = 'changed actual current task'
        self.write_tasks([self.task, other])
        r = Runner(self.root)
        r.refresh()
        self.assertEqual(r.state['tasks']['foundation-runner']['status'], 'STALE')

    def test_current_config_edit_in_running_instance_rejected(self):
        self.task['goal'] = 'changed on disk'
        self.write_tasks([self.task])
        with self.assertRaises(Blocked): self.r.identity('foundation-runner')

    def test_lock_is_atomic_and_released_after_owner_exit(self):
        with self.r.lock():
            r = Runner(self.root)
            with self.assertRaisesRegex(Blocked, 'locked'):
                with r.lock(): pass
        with Runner(self.root).lock(): pass
        p = subprocess.Popen([sys.executable, '-c',
                              'import fcntl,sys,time; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); time.sleep(60)',
                              str(self.r.home / 'runner.lock')], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), 'locked')
            with self.assertRaises(Blocked):
                with self.r.lock(): pass
        finally:
            p.kill()
            p.wait()
            p.stdout.close()
        with self.r.lock(): pass

    def test_interrupt_preserves_baseline_and_round_budget(self):
        real_execute = self.r.execute
        with patch.object(self.r, 'execute', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.r.run('foundation-runner', 'fast')
        original = self.r.state['tasks']['foundation-runner']['baseline_hash']
        self.r = Runner(self.root)
        self.r.refresh()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['status'], 'BLOCKED')
        self.assertEqual(self.r.state['tasks']['foundation-runner']['rounds'], 1)
        self.r.start('foundation-runner')
        self.assertEqual(original, self.r.state['tasks']['foundation-runner']['baseline_hash'])
        self.pair()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['rounds'], 1)
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')

    def test_empty_checks_document_presence_cannot_finish(self):
        (self.root / 'src/proof.md').write_text('Everything passed')
        # Pre-baseline empty card: start itself must refuse; do not fabricate a
        # baseline for a gate configuration the runner never accepted.
        shutil.rmtree(self.r.home, ignore_errors=True)
        self.task['checks'] = dict(fast=[], full=[])
        self.write_tasks([self.task])
        self.r = Runner(self.root)
        self.r.refresh()
        with self.assertRaisesRegex(Blocked, 'empty/unconfigured checks'):
            self.r.start('foundation-runner')
        self.assertIsNone(self.r.state['tasks']['foundation-runner']['baseline'])
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'BLOCKED')
        # Existing baseline, then the card is emptied: run must hit the real
        # configuration-audit boundary before its try block, and finish stays
        # BLOCKED. Proof documents never substitute for configured checks.
        self.task['checks'] = {tier: [self.check('pass')] for tier in ('fast', 'full')}
        self.reset_graph([self.task])
        self.task['checks'] = dict(fast=[], full=[])
        self.write_tasks([self.task])
        self.r = Runner(self.root)
        with self.assertRaises(Blocked):
            self.r.run('foundation-runner', 'fast')
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'BLOCKED')

    def test_tool_receipt_cannot_promote_fidelity(self):
        self.task['completion'] = 'FIDELITY_PASS'
        self.reset_graph([self.task])
        self.r.run('foundation-runner', 'fast')
        self.assertEqual(self.r.run('foundation-runner', 'full')['type'], 'BLOCKED')
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'BLOCKED')

    def test_external_unconfigured_gate_is_blocked(self):
        c = self.check('pass')
        c['external'] = True
        run = dict(id='external', identity='current')
        p = self.r.home / 'external'
        p.mkdir()
        with self.assertRaisesRegex(Blocked, 'external'): self.r.execute(c, run, p)

    def test_schema_rejects_duplicate_keys_invalid_state_filtered_fidelity(self):
        bad = self.root / 'bad.json'
        bad.write_text('{"schema":1,"schema":1}')
        with self.assertRaises(Blocked): load(bad)
        for edit in ('ignore', 'fidelity-no-window', 'shell', 'unknown-field', 'cycle'):
            t = copy.deepcopy(self.task)
            if edit == 'ignore': t['checks']['full'][0]['argv'].append('--ignore=texture')
            if edit == 'fidelity-no-window': t['checks']['full'][0]['result_type'] = 'FIDELITY_PASS'
            if edit == 'shell': t['checks']['fast'][0]['argv'] = ['bash', '-c', 'true']
            if edit == 'unknown-field': t['skip'] = True
            if edit == 'cycle': t['depends'] = [t['id']]
            self.write_tasks([t])
            with self.subTest(edit=edit), self.assertRaises(Blocked): Runner(self.root)
        self.write_tasks([self.task])
        state = self.r.state
        state['tasks']['foundation-runner']['status'] = 'GREEN'
        atomic(self.r.state_path, state)
        with self.assertRaises(Blocked): Runner(self.root)

    def test_timeout_kills_producer_and_never_closes_successfully(self):
        run = dict(id='timeout', identity='current')
        p = self.r.home / 'timeout'
        p.mkdir()
        c = self.check('pass')
        c['argv'] = [sys.executable, '-c', 'import time; time.sleep(60)']
        c['timeout'] = 1
        with self.assertRaisesRegex(Blocked, 'timeout'): self.r.execute(c, run, p)
        self.assertFalse((p / 'synthetic.result.json').exists())

    def test_real_signal_interrupt_leaves_durable_open_run(self):
        self.configure('interrupt')
        with self.assertRaises(KeyboardInterrupt): self.r.run('foundation-runner', 'fast')
        s = self.r.state['tasks']['foundation-runner']
        self.assertEqual(s['status'], 'VERIFYING')
        self.assertEqual(load(self.r.home / s['run'] / 'run.json')['status'], 'VERIFYING')
        r = Runner(self.root)
        r.refresh()
        self.assertEqual(r.state['tasks']['foundation-runner']['status'], 'BLOCKED')
        self.assertEqual(r.state['tasks']['foundation-runner']['rounds'], 1)

    def test_policy_change_expires_done_and_baseline_corruption_rejected(self):
        self.pair()
        self.r.finish('foundation-runner')
        p = self.root / 'verification/policy.json'
        data = p.read_bytes()
        p.write_bytes(data + b'\n')
        self.r.refresh()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['status'], 'STALE')
        p.write_bytes(data)
        s = self.r.state['tasks']['foundation-runner']
        p = self.root / s['baseline']
        p.write_bytes(p.read_bytes() + b'\n')
        with self.assertRaises(Blocked): self.r.validate_full('foundation-runner', s['receipt'])

    def test_newly_added_task_cannot_steal_active_workspace(self):
        other = copy.deepcopy(self.task)
        other['id'] = 'independent'
        self.write_tasks([self.task, other])
        r = Runner(self.root)
        r.refresh()
        self.assertEqual(r.state['tasks']['independent']['status'], 'READY')
        with self.assertRaisesRegex(Blocked, 'claimed'): r.start('independent')

    def test_symlink_lock_and_evidence_paths_are_rejected(self):
        lock = self.r.home / 'runner.lock'
        lock.symlink_to(self.root / 'outside.txt')
        with self.assertRaisesRegex(Blocked, 'symlink'):
            with self.r.lock(): pass
        escape = self.r.home / 'outside-link'
        escape.symlink_to('/etc/passwd')
        with self.assertRaises(Blocked): self.r.artifact('artifacts/verification/outside-link')
        with self.assertRaises(Blocked): self.r.path('../escape')

    def test_standard_wrapper_does_not_promote_or_hide_failure(self):
        for code in (0, 4):
            directory = self.r.home / ('wrapper-' + str(code))
            directory.mkdir()
            run = dict(id=directory.name, identity='current')
            c = self.check('pass')
            c['argv'] = [sys.executable, 'scripts/verification_lib/check_command.py', '--', sys.executable, '-c', f'raise SystemExit({code})']
            if code:
                with self.assertRaises(Blocked): self.r.execute(c, run, directory)
            else:
                self.r.execute(c, run, directory)
                self.assertEqual(load(directory / 'synthetic.result.json')['type'], 'TOOL_PASS')

    def test_dependency_invalidates_and_global_claim_retained(self):
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        (self.root / 'src/input.txt').write_text('new dependency input')
        self.r.refresh()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['status'], 'STALE')
        self.assertEqual(self.r.state['tasks']['dependent']['status'], 'STALE')
        with self.assertRaises(Blocked): self.r.run('dependent', 'fast')
        old = self.r.state['tasks']['foundation-runner']['receipt']
        result = self.r.recheck_dependencies('dependent')
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertNotEqual(old, self.r.state['tasks']['foundation-runner']['receipt'])
        self.assertEqual(len(result['dependency_runs']), 2)
        for tier in ('fast', 'full'):
            self.assertEqual(self.r.run('dependent', tier)['type'], 'TOOL_PASS')
        self.assertEqual(self.r.finish('dependent')['status'], 'DONE')

    def test_dependency_recheck_cannot_absorb_out_of_scope_changes(self):
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        (self.root / 'outside.txt').write_text('out of scope')
        with self.assertRaisesRegex(Blocked, 'out-of-scope'): self.r.recheck_dependencies('dependent')

    def test_dependency_recheck_rejects_gate_source_mutations(self):
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        (self.root / 'src/input.txt').write_text('owner revision')
        self.r.refresh()
        execute = self.r.execute
        def mutate(c, run, directory):
            receipt = execute(c, run, directory)
            (self.root / 'src/input.txt').write_text('illegal gate write')
            return receipt
        with patch.object(self.r, 'execute', side_effect=mutate):
            with self.assertRaises(Blocked): self.r.recheck_dependencies('dependent')
        self.assertNotEqual(self.r.state['tasks']['foundation-runner']['status'], 'DONE')

    # ---- Reviewed proposal task-configure-20261024-recheck-noround ----

    def test_repeated_rechecks_do_not_consume_dependency_round_budget(self):
        """正控（已批 review 1）：只读依赖重验不再记入依赖任务自身的轮次
        预算——recheck 数次后依赖重验仍可继续且 receipt 更新，依赖的
        rounds 停留在其自身真实修改历史（初始 pair 的一轮）不再增长。"""
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['rounds'], 1)
        self.r.start('dependent')
        for round_index in range(4):
            (self.root / 'src/input.txt').write_text('revision %d' % round_index)
            self.r.refresh()
            self.assertEqual(
                self.r.state['tasks']['foundation-runner']['status'], 'STALE')
            result = self.r.recheck_dependencies('dependent')
            self.assertEqual(result['status'], 'ACTIVE')
            self.assertEqual(
                self.r.state['tasks']['foundation-runner']['status'], 'DONE')
            self.assertEqual(
                self.r.state['tasks']['foundation-runner']['rounds'], 1,
                'read-only re-verification must not consume the dependency '
                'revision round budget')

    def test_owner_round_budget_semantics_unchanged_by_reverify(self):
        """负控（已批 review 1）：owner 任务自身的轮次记账不变——每次
        源 revision 的 fast 仍记一轮，三轮到顶照旧（预算语义未被顺手
        放宽）。"""
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        for round_index in range(3):
            (self.root / 'src/input.txt').write_text('owner %d' % round_index)
            self.r.refresh()
            self.r.recheck_dependencies('dependent')
            for tier in ('fast', 'full'):
                self.assertEqual(self.r.run('dependent', tier)['type'],
                                 'TOOL_PASS')
        self.assertEqual(self.r.state['tasks']['dependent']['rounds'], 3)
        (self.root / 'src/input.txt').write_text('owner four')
        self.r.refresh()
        # 依赖重验通道仍开放（不耗预算）；owner 第四次同源修改的 run
        # 必须被其自身预算阻断（负控：owner 语义未被 reverify 放宽）。
        self.r.recheck_dependencies('dependent')
        self.assertEqual(self.r.state['tasks']['foundation-runner']['status'],
                         'DONE')
        with self.assertRaisesRegex(Blocked, 'budget'):
            self.r.run('dependent', 'fast')

    def test_dependency_run_budget_still_exhausts_via_direct_run(self):
        """负控（已批 review 1）：依赖任务自身的直接 run 预算语义原样
        保留——三轮同源修改到顶（reverify 通道不得放宽它；口径与既有
        test_three_revision_limit_not_six_tiers 相同）。"""
        for revision in range(3):
            (self.root / 'src/input.txt').write_text('dep %d' % revision)
            self.pair()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['rounds'], 3)
        (self.root / 'src/input.txt').write_text('dep four')
        with self.assertRaisesRegex(Blocked, 'budget'):
            self.r.run('foundation-runner', 'fast')

    # ---- Reviewed proposal task-configure-20261024-build-artifact-noise ----
    # 夹具在 setUp 中建立（git init 后、任何 Runner/baseline 前）：
    # eagler-touhou/ports/build/game.bin（两层 git 均 ignore 的构建
    # 产物）与 ports/src_real.cpp（真实跟踪源码）。

    def test_inner_ignored_build_outputs_are_boundary_noise(self):
        """正控（已批 review 2）：两层 git 均声明 ignore 的构建产物重建
        不再构成 out-of-scope（gate 正常输出）。"""
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        # 基线后重建产物（gate 正常输出）
        (self.root / 'eagler-touhou/ports/build/game.bin').write_text(
            'rebuilt by gate')
        result = self.r.recheck_dependencies('dependent')
        self.assertEqual(result['status'], 'ACTIVE')

    def test_inner_real_source_changes_still_block_foreign_cards(self):
        """负控 1（已批 review 2）：不被 ignore 的真实源码改动仍按
        跨卡脏区阻断（全局 claim 语义不放宽）。"""
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        (self.root / 'eagler-touhou/ports/src_real.cpp').write_text(
            'uncommitted cross-card change')
        with self.assertRaisesRegex(Blocked, 'out-of-scope'):
            self.r.recheck_dependencies('dependent')

    def test_inner_ignored_paths_explicitly_declared_still_compare(self):
        """负控 2（已批 review 2）：显式声明进卡 inputs 的 ignored 路径
        仍参与比较（显式声明优先于 ignore 降噪）。"""
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'],
                     inputs=['src/input.txt',
                             'eagler-touhou/ports/build/game.bin'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        (self.root / 'eagler-touhou/ports/build/game.bin').write_text(
            'explicit input drift')
        self.r.refresh()
        # 显式 inputs 由 scoped() 加入快照；变化必须被看见（阻断），
        # 不得被 ignore 降噪吞掉。
        with self.assertRaisesRegex(Blocked, 'out-of-scope'):
            self.r.recheck_dependencies('dependent')

    def test_inner_ignored_protected_paths_still_block(self):
        """负控 3（已批 review 2）：protected 命中的 ignored 路径改动
        仍阻断（不得用 ignore 规则掩护 protected 改动）。"""
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'],
                     protected=['verification', 'eagler-touhou/ports/build'])
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        (self.root / 'eagler-touhou/ports/build/game.bin').write_text(
            'protected touch')
        with self.assertRaisesRegex(Blocked, 'protected'):
            self.r.check_boundary('dependent')

    # ---- Reviewed proposal task-configure-20261024-runner-self-maintenance ----

    def _registry_fixture(self, verify_hash):
        """登记文件在场夹具：runner-baseline.json 记录指定 verify.py 哈希。"""
        import hashlib
        if verify_hash is None:
            verify_hash = hashlib.sha256(
                (self.root / 'scripts/verify.py').read_bytes()).hexdigest()
        atomic(self.root / 'artifacts/verification/runner-baseline.json',
               dict(schema=1, verify_py_sha256=verify_hash,
                    applied_reviews=['synthetic-review'],
                    applied_at='2026-10-24T00:00:00Z'))
        self.r = Runner(self.root)

    def _verify_py_delta(self, text):
        """模拟 runner 自维护：基线后改 scripts/verify.py。"""
        (self.root / 'scripts/verify.py').write_text(text)

    def test_runner_self_maintenance_at_registered_hash_passes_boundary(self):
        """正控（已批 review 3）：verify.py 哈希==登记基准时，其自身
        变化不再阻断卡边界（已授权维护落地通道）。"""
        self._registry_fixture(None)
        self._verify_py_delta('# reviewed maintenance comment\n')
        import hashlib
        current = hashlib.sha256(
            (self.root / 'scripts/verify.py').read_bytes()).hexdigest()
        self._registry_fixture(current)
        # 基线后重写同一内容（哈希不变）——check_boundary 不得因
        # verify.py 进 changed 而阻断。
        changed = self.r.check_boundary(self.task['id'])
        self.assertNotIn('scripts/verify.py', changed)

    def test_runner_deviation_from_registered_hash_still_blocks(self):
        """负控 1（已批 review 3）：登记后再改 verify.py（哈希偏离基准）
        必须 forbidden——不得用登记通道掩护未授权弱化。"""
        import hashlib
        self._registry_fixture(None)
        before = (self.root / 'scripts/verify.py').read_bytes()
        self._verify_py_delta('# unauthorized weakening\n')
        with self.assertRaisesRegex(Blocked, 'verify.py'):
            self.r.check_boundary(self.task['id'])
        # 恢复现场，避免影响后续断言
        (self.root / 'scripts/verify.py').write_bytes(before)

    def test_missing_registry_falls_back_to_full_protection(self):
        """负控 2（已批 review 3）：登记文件缺席时行为回落现状——
        verify.py 一律进 changed、protected 阻断（无静默放行）。"""
        assert not (self.root / 'artifacts/verification/runner-baseline.json').is_file()
        before = (self.root / 'scripts/verify.py').read_bytes()
        self._verify_py_delta('# no registry present\n')
        with self.assertRaisesRegex(Blocked, 'verify.py'):
            self.r.check_boundary(self.task['id'])
        (self.root / 'scripts/verify.py').write_bytes(before)

    def test_registry_channel_covers_only_verify_py(self):
        """负控 3（已批 review 3）：scripts/test_verify.py 不入登记通道
        ——其改动照旧按各卡 scope 判定（protected 命中即阻断）。"""
        import hashlib
        self._registry_fixture(None)
        self._verify_py_delta('# reviewed maintenance\n')
        current = hashlib.sha256(
            (self.root / 'scripts/verify.py').read_bytes()).hexdigest()
        self._registry_fixture(current)
        # test_verify.py 改动不在登记豁免面：本合成卡 protected 不含它，
        # 但它也不在 allowed → 出现在 changed 即 out-of-scope。
        (self.root / 'scripts/test_verify.py').write_text('# out of channel\n')
        with self.assertRaisesRegex(Blocked, 'test_verify.py'):
            self.r.check_boundary(self.task['id'])

    # ---- Reviewed proposal task-configure-20261024-contract-test-registry ----

    def _registry_fixture(self, verify_hash, contract_hash=None):
        """登记文件在场夹具：可选扩展 contract_test_sha256 字段。"""
        import hashlib
        if verify_hash is None:
            verify_hash = hashlib.sha256(
                (self.root / 'scripts/verify.py').read_bytes()).hexdigest()
        record = dict(schema=1, verify_py_sha256=verify_hash,
                      applied_reviews=['synthetic-review'],
                      applied_at='2026-10-24T00:00:00Z')
        if contract_hash is not None:
            record['contract_test_sha256'] = contract_hash
        atomic(self.root / 'artifacts/verification/runner-baseline.json', record)
        self.r = Runner(self.root)

    def test_contract_test_at_registered_hash_passes_boundary(self):
        """正控（已批 review 4）：test_verify.py 哈希==contract_test 登记
        基准时，其自身变化不再阻断卡边界（review 链授权的维护落地）。"""
        import hashlib
        contract = hashlib.sha256(
            (self.root / 'scripts/test_verify.py').read_bytes()).hexdigest()
        self._registry_fixture(None, contract_hash=contract)
        # 基线后重写同哈希内容（模拟已授权维护落地后状态）
        (self.root / 'scripts/test_verify.py').write_text(
            (self.root / 'scripts/test_verify.py').read_text() + '\n# reg\n')
        new_hash = hashlib.sha256(
            (self.root / 'scripts/test_verify.py').read_bytes()).hexdigest()
        self._registry_fixture(None, contract_hash=new_hash)
        changed = self.r.check_boundary(self.task['id'])
        self.assertNotIn('scripts/test_verify.py', changed)

    def test_contract_test_deviation_from_registry_still_blocks(self):
        """负控 1（已批 review 4）：偏离 contract_test 登记基准的字节
        改动必须 forbidden（不得用登记通道掩护未授权弱化）。"""
        import hashlib
        contract = hashlib.sha256(
            (self.root / 'scripts/test_verify.py').read_bytes()).hexdigest()
        self._registry_fixture(None, contract_hash=contract)
        before = (self.root / 'scripts/test_verify.py').read_bytes()
        (self.root / 'scripts/test_verify.py').write_text('# unauthorized\n')
        with self.assertRaisesRegex(Blocked, 'test_verify.py'):
            self.r.check_boundary(self.task['id'])
        (self.root / 'scripts/test_verify.py').write_bytes(before)

    def test_registry_without_contract_field_falls_back(self):
        """负控 2（旧登记兼容）：无 contract_test_sha256 字段的登记文件对
        test_verify.py 回落 review-3 行为（全阻断），verify.py 通道不受
        影响——两文件回落互相独立。"""
        self._registry_fixture(None)   # 无 contract 字段（review-3 形状）
        before = (self.root / 'scripts/test_verify.py').read_bytes()
        (self.root / 'scripts/test_verify.py').write_text('# unregistered\n')
        with self.assertRaisesRegex(Blocked, 'test_verify.py'):
            self.r.check_boundary(self.task['id'])
        (self.root / 'scripts/test_verify.py').write_bytes(before)
        # verify.py 登记基准通道仍有效（字段独立性另一半）
        (self.root / 'scripts/verify.py').write_text(
            '# reviewed maintenance\n')
        import hashlib
        current = hashlib.sha256(
            (self.root / 'scripts/verify.py').read_bytes()).hexdigest()
        self._registry_fixture(current)
        changed = self.r.check_boundary(self.task['id'])
        self.assertNotIn('scripts/verify.py', changed)

    def test_contract_mismatch_does_not_exempt_verify_py_and_vice_versa(self):
        """负控 3（已批 review 4）：字段独立——contract_test 失配不豁免
        verify.py，verify.py 失配不豁免 contract_test。"""
        import hashlib
        contract = hashlib.sha256(
            (self.root / 'scripts/test_verify.py').read_bytes()).hexdigest()
        self._registry_fixture(None, contract_hash=contract)
        # verify.py 失配（登记的是旧哈希）：test_verify.py 豁免不带动
        # verify.py 豁免
        (self.root / 'scripts/verify.py').write_text('# drifted\n')
        with self.assertRaisesRegex(Blocked, 'verify.py'):
            self.r.check_boundary(self.task['id'])

    def test_unrelated_scripts_files_never_exempt(self):
        """负控 4（已批 review 4）：登记面只覆盖两个具名文件——其他
        scripts/ 文件改动照旧按卡 scope 判定。"""
        import hashlib
        contract = hashlib.sha256(
            (self.root / 'scripts/test_verify.py').read_bytes()).hexdigest()
        self._registry_fixture(None, contract_hash=contract)
        (self.root / 'scripts/producer.py').write_text('# producer drift\n')
        with self.assertRaisesRegex(Blocked, 'producer.py'):
            self.r.check_boundary(self.task['id'])

    # ---- Reviewed proposal task-configure-20261025-authorized-oracle-registry ----

    def _registry_fixture(self, verify_hash, contract_hash=None,
                          authorized_files=None):
        """登记文件在场夹具：可选扩展 authorized_files 映射。"""
        import hashlib
        if verify_hash is None:
            verify_hash = hashlib.sha256(
                (self.root / 'scripts/verify.py').read_bytes()).hexdigest()
        record = dict(schema=1, verify_py_sha256=verify_hash,
                      applied_reviews=['synthetic-review'],
                      applied_at='2026-10-25T00:00:00Z')
        if contract_hash is not None:
            record['contract_test_sha256'] = contract_hash
        if authorized_files is not None:
            record['authorized_files'] = authorized_files
        atomic(self.root / 'artifacts/verification/runner-baseline.json', record)
        self.r = Runner(self.root)
        return record

    def test_authorized_files_at_registered_hash_pass_boundary(self):
        """正控（已批 proposal 6）：authorized_files 清单内文件在登记
        哈希下视为未变（已授权修复落地通道）。"""
        import hashlib
        probe = self.root / 'oracle/battle_oracle.py'
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text('# authorized repair\n')
        digest = hashlib.sha256(probe.read_bytes()).hexdigest()
        self._registry_fixture(None,
                               authorized_files={'oracle/battle_oracle.py': digest})
        changed = self.r.check_boundary(self.task['id'])
        self.assertNotIn('oracle/battle_oracle.py', changed)

    def test_authorized_files_deviation_still_blocks(self):
        """负控 1（已批 proposal 6）：偏离登记哈希的清单文件改动必须
        forbidden（不得用登记通道掩护未授权弱化）。"""
        import hashlib
        probe = self.root / 'oracle/battle_oracle.py'
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text('# authorized repair\n')
        digest = hashlib.sha256(probe.read_bytes()).hexdigest()
        self._registry_fixture(None,
                               authorized_files={'oracle/battle_oracle.py': digest})
        probe.write_text('# unauthorized weakening\n')
        with self.assertRaisesRegex(Blocked, 'battle_oracle'):
            self.r.check_boundary(self.task['id'])

    def test_authorized_files_only_named_paths_exempt(self):
        """负控 3（已批 proposal 6）：清单外文件（含同目录其他文件）照旧
        按卡 scope 判定。"""
        import hashlib
        probe = self.root / 'oracle/battle_oracle.py'
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text('# authorized repair\n')
        digest = hashlib.sha256(probe.read_bytes()).hexdigest()
        other = self.root / 'oracle/replayfmt.py'
        other.write_text('# out of list\n')
        self._registry_fixture(None,
                               authorized_files={'oracle/battle_oracle.py': digest})
        with self.assertRaisesRegex(Blocked, 'replayfmt'):
            self.r.check_boundary(self.task['id'])

    def test_registry_without_authorized_files_falls_back(self):
        """负控 2（已批 proposal 6）：无 authorized_files 字段的登记按
        review-3/4 形状回落——清单文件照旧全阻断。"""
        probe = self.root / 'oracle/battle_oracle.py'
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text('# no registry entry\n')
        self._registry_fixture(None)      # 无 authorized_files 字段
        with self.assertRaisesRegex(Blocked, 'battle_oracle'):
            self.r.check_boundary(self.task['id'])

    def test_four_owner_rechecks_preserve_budget_and_failure_rejects_old_evidence(self):
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.reset_graph([self.task, other])
        # Exhaust the upstream implementation budget by legitimate runs, not by
        # editing state; its read-only downstream rechecks must still be legal.
        for revision in range(3):
            (self.root / 'src/input.txt').write_text(f'upstream {revision}')
            self.pair()
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')
        self.r.refresh()
        self.r.start('dependent')
        receipts = {self.r.state['tasks']['foundation-runner']['receipt']}
        baseline = self.r.state['tasks']['dependent']['baseline_hash']
        for revision in range(4):
            (self.root / 'src/input.txt').write_text(f'owner {revision}')
            self.r.refresh()
            with self.assertRaises(Blocked): self.r.run('dependent', 'fast')
            result = self.r.recheck_dependencies('dependent')
            self.assertEqual(len(result['dependency_runs']), 2)
            upstream = self.r.state['tasks']['foundation-runner']
            self.assertEqual(upstream['status'], 'DONE')
            self.assertEqual(upstream['rounds'], 3)
            self.assertNotIn(upstream['receipt'], receipts)
            receipts.add(upstream['receipt'])
            self.assertEqual(self.r.state['tasks']['dependent']['rounds'], 0)
            self.assertEqual(self.r.state['tasks']['dependent']['baseline_hash'], baseline)
        (self.root / 'src/input.txt').write_text('owner failure revision')
        self.r.refresh()
        execute = self.r.execute
        def fail(c, run, directory):
            bad = dict(c, argv=[sys.executable, 'scripts/producer.py', 'exit-none'])
            return execute(bad, run, directory)
        with patch.object(self.r, 'execute', side_effect=fail):
            with self.assertRaises(Blocked): self.r.recheck_dependencies('dependent')
        upstream = self.r.state['tasks']['foundation-runner']
        self.assertEqual(upstream['rounds'], 3)
        self.assertIsNone(upstream['receipt'])
        self.assertNotEqual(upstream['status'], 'DONE')
        with self.assertRaises(Blocked): self.r.run('dependent', 'fast')
        self.assertEqual(self.r.recheck_dependencies('dependent')['status'], 'ACTIVE')
        for tier in ('fast', 'full'):
            self.assertEqual(self.r.run('dependent', tier)['type'], 'TOOL_PASS')
        self.assertEqual(self.r.finish('dependent')['status'], 'DONE')


    def test_games_scoping_never_reads_runtime_registry_or_credentials(self):
        paths = ['games/th13/th13.exe', 'games/th13/th13.dat', 'games/replays/test.rpy',
                 'games/.wineprefix/user.reg', 'games/prefix/drive_c/users/dev/keyrings/secret.dat',
                 'games/th13/system.reg', 'games/credentials/cookie.dat',
                 'games/browser/profile/Cookies', 'games/th13/notes.txt']
        for name in paths:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('synthetic only')
        expected = set(paths[:3])
        real = self.r.file_record
        def guarded(name):
            if name != 'games' and name not in expected:
                self.fail(f'non-asset read attempted: {name}')
            return real(name)
        with patch.object(self.r, 'file_record', side_effect=guarded):
            result = self.r.scoped(['games'])
            self.assertEqual(set(result), expected | {'games'})
            self.assertEqual(self.r.scoped(paths[3:]), {})
        before = self.r.scoped(['games'])
        (self.root / paths[0]).write_text('changed original asset')
        self.assertNotEqual(before, self.r.scoped(['games']))


    def test_legacy_runtime_baseline_is_not_a_deletion_or_read(self):
        private = ['games/.wineprefix/user.reg', 'games/.wineprefix/drive_c/users/dev/secret.dat',
                   'games/th13/system.reg', 'games/credentials/cookie.dat', 'games/th13/notes.txt']
        self.task['protected'].append('games')
        self.write_tasks([self.task])
        self.r = Runner(self.root)
        s = self.r.state['tasks']['foundation-runner']
        p = self.root / s['baseline']
        before = load(p)
        before['verification/tasks.json'] = self.r.file_record('verification/tasks.json')
        (self.root / 'games').mkdir()
        before['games'] = self.r.file_record('games')
        before.update({n: {'sha256': 'synthetic-legacy-private-hash', 'mode': 384} for n in private})
        atomic(p, before)
        s['baseline_hash'] = sha(p)  # Synthetic old baseline fixture, not real state migration.
        real = self.r.file_record
        def guarded(name):
            if name in private or self.r.game_runtime(name):
                self.fail(f'private legacy input read attempted: {name}')
            return real(name)
        with patch.object(self.r, 'file_record', side_effect=guarded):
            self.assertEqual(self.r.check_boundary('foundation-runner'), [])
            asset = self.root / 'games/th13/th13.dat'
            asset.parent.mkdir(parents=True)
            asset.write_text('synthetic original asset')
            with self.assertRaisesRegex(Blocked, 'out-of-scope'):
                self.r.check_boundary('foundation-runner')


    def test_reopen_former_done_archives_baseline_and_requires_new_evidence(self):
        self.pair()
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')
        old = dict(self.r.state['tasks']['foundation-runner'])
        (self.root / 'scripts/verify.py').write_text('# authorized tool maintenance\n')
        (self.root / 'outside.txt').write_text('later completed business work')
        self.r.refresh()
        self.assertEqual(self.r.state['tasks']['foundation-runner']['status'], 'STALE')
        s = self.r.reopen('foundation-runner')
        self.assertEqual(s['status'], 'ACTIVE')
        self.assertNotEqual(old['baseline_hash'], s['baseline_hash'])
        self.assertEqual(s['rounds'], old['rounds'])
        self.assertIsNone(s['receipt'])
        archives = list((self.r.home / 'reopens').glob('*.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(load(archives[0])['previous'], old | dict(status='STALE', reason='stale source/input/tool/contract identity'))
        self.assertTrue((self.root / old['baseline']).is_file())
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'BLOCKED')
        self.pair()
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')
        self.assertEqual(s['rounds'], 2)
        self.assertNotEqual(s['receipt'], old['receipt'])


    def test_reopen_refuses_active_failed_or_other_unfinished_claim(self):
        other = copy.deepcopy(self.task)
        other.update(id='dependent', depends=['foundation-runner'])
        self.task['checks'] = {tier: [self.check('false')] for tier in ('fast', 'full')}
        self.reset_graph([self.task, other])
        with self.assertRaises(Blocked): self.r.reopen('foundation-runner')
        self.r.run('foundation-runner', 'fast')
        with self.assertRaises(Blocked): self.r.reopen('foundation-runner')
        self.task['checks'] = {tier: [self.check('pass')] for tier in ('fast', 'full')}
        self.reset_graph([self.task, other])
        self.pair()
        self.r.finish('foundation-runner')
        self.r.refresh()
        self.r.start('dependent')
        (self.root / 'src/input.txt').write_text('owner still active')
        self.r.refresh()
        before = self.r.state['tasks']['foundation-runner']['baseline_hash']
        with self.assertRaisesRegex(Blocked, 'claim'): self.r.reopen('foundation-runner')
        self.assertEqual(before, self.r.state['tasks']['foundation-runner']['baseline_hash'])


    def exhausted_review(self):
        self.configure('false')
        for _ in range(3):
            self.assertEqual(self.r.run('foundation-runner', 'fast')['type'], 'BLOCKED')
        s = self.r.state['tasks']['foundation-runner']
        review = dict(schema=1, task_id='foundation-runner', card_sha256=digest(self.task),
                      baseline_sha256=s['baseline_hash'], last_run_id=s['run'],
                      new_hypothesis='producer completion is sampled at the wrong phase',
                      experiment='run the fixed gate with producer completion sampled before comparison',
                      independent=True, approved=True, issues=[])
        p = self.r.home / 'review.json'
        atomic(p, review)
        return p.relative_to(self.root).as_posix(), review


    def test_mechanism_review_archives_failure_and_opens_only_new_bounded_revision(self):
        path, review = self.exhausted_review()
        previous = copy.deepcopy(self.r.state['tasks']['foundation-runner'])
        with self.assertRaises(Blocked): self.r.run('foundation-runner', 'fast')
        result = self.r.mechanism_review('foundation-runner', path)
        self.assertEqual(result['status'], 'ACTIVE')
        self.assertTrue(result['revision_id'])
        record = load(self.root / result['artifact'])
        self.assertEqual(record['previous'], previous)
        self.assertEqual(record['review_sha256'], sha(self.root / path))
        self.assertEqual(record['last_run_sha256'], sha(self.r.home / previous['run'] / 'run.json'))
        s = self.r.state['tasks']['foundation-runner']
        self.assertEqual(s['baseline'], previous['baseline'])
        self.assertEqual(s['baseline_hash'], previous['baseline_hash'])
        self.assertEqual(s['run'], previous['run'])
        self.assertEqual(s['rounds'], 0)
        self.assertIsNone(s['receipt'])
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'BLOCKED')
        for _ in range(3):
            self.assertEqual(self.r.run('foundation-runner', 'fast')['type'], 'BLOCKED')
        with self.assertRaises(Blocked): self.r.run('foundation-runner', 'fast')
        review['last_run_id'] = s['run']
        atomic(self.root / path, review)
        with self.assertRaisesRegex(Blocked, 'new hypothesis'):
            self.r.mechanism_review('foundation-runner', path)
        review['new_hypothesis'] = 'producer status is overwritten by a later consumer'
        atomic(self.root / path, review)
        self.r.mechanism_review('foundation-runner', path)
        self.assertEqual(len(list((self.r.home / 'revisions').glob('*.json'))), 2)
        execute = self.r.execute
        def passed(c, run, directory):
            return execute(dict(c, argv=[sys.executable, 'scripts/producer.py', 'pass']), run, directory)
        with patch.object(self.r, 'execute', side_effect=passed):
            self.pair()
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')
        self.assertEqual(s['rounds'], 1)
        self.assertEqual(s['baseline_hash'], previous['baseline_hash'])


    def test_mechanism_review_rejects_wrong_binding_and_cumulative_scope_violation(self):
        path, review = self.exhausted_review()
        before = copy.deepcopy(self.r.state)
        for field, value in [('card_sha256', 'wrong'), ('baseline_sha256', 'wrong'),
                             ('last_run_id', 'foreign-run'), ('independent', False),
                             ('approved', False), ('issues', ['unresolved'])]:
            with self.subTest(field=field):
                invalid = dict(review)
                invalid[field] = value
                atomic(self.root / path, invalid)
                with self.assertRaises(Blocked): self.r.mechanism_review('foundation-runner', path)
                self.assertEqual(self.r.state, before)
        atomic(self.root / path, review)
        (self.root / 'outside.txt').write_text('unowned change must not become baseline')
        with self.assertRaisesRegex(Blocked, 'out-of-scope'):
            self.r.mechanism_review('foundation-runner', path)
        self.assertEqual(self.r.state, before)
        self.assertFalse((self.r.home / 'revisions').exists())


    def maintenance_fixture(self, maintenance_protected=None, owner_mode=None):
        # Owner may have explicit ignored decomp evidence; maintenance must keep
        # that inventory intact without scanning game assets.
        evidence = self.root / 'artifacts/decomp/explicit.c'
        evidence.parent.mkdir(parents=True,exist_ok=True); evidence.write_text('synthetic decomp input')
        if 'artifacts/decomp/explicit.c' not in self.task['inputs']:
            self.task['inputs'].append('artifacts/decomp/explicit.c')
        if owner_mode is not None:
            self.task['checks'] = {tier: [self.check(owner_mode)] for tier in ('fast', 'full')}
            self.task['completion'] = 'TOOL_PASS'
        maintenance = copy.deepcopy(self.task)
        maintenance.update(id='autonomy-tooling', depends=[], allowed=['scripts/verify.py'],
                           protected=['verification/policy.json'],
                           checks={tier: [self.check('pass')] for tier in ('fast', 'full')})
        if maintenance_protected is not None:
            maintenance['protected'] = maintenance_protected
        # Both cards exist before the owner baseline; no live graph mutation is
        # mistaken for a reviewed configuration transition.
        self.reset_graph([self.task, maintenance])
        return maintenance

    def maintenance_approval(self, phase='start'):
        owner, maintenance = 'foundation-runner', 'autonomy-tooling'
        review = dict(schema=1, phase=phase, owner=owner, maintenance=maintenance,
                      owner_card_sha256=digest(self.r.tasks[owner]),
                      maintenance_card_sha256=digest(self.r.tasks[maintenance]),
                      owner_state_sha256=digest(self.r.state['tasks'][owner]),
                      workspace_sha256=digest(self.r.maintenance_workspace()),
                      files=self.r.tasks[maintenance]['allowed'], approved=True,
                      reviewer_role='independent', issues=[])
        path = f'artifacts/verification/{phase}-maintenance-review.json'
        atomic(self.root / path, review)
        return path

    def test_maintenance_boundary_enumerates_owner_scopes_not_protected_directories(self):
        # Regression: a maintenance card whose protected list contains a broad
        # directory (e.g. 'eagler-touhou') must NOT widen boundary enumeration.
        # The baseline is captured with the OWNER's scopes only
        # (maintenance_start -> maintenance_workspace); enumerating the
        # maintenance card's own protected directory re-discovers pre-existing
        # ignored build trees (symlink farms like node_modules/.bin) as
        # unowned new changes and blocks legitimate maintenance runs.
        self.maintenance_fixture(
            maintenance_protected=['verification/policy.json', 'eagler-touhou'])
        # Simulate an ignored symlink farm inside the protected directory that
        # predates maintenance-start (like launcher/node_modules/.bin/*).
        farm = self.root / 'eagler-touhou' / 'node_modules' / '.bin'
        farm.mkdir(parents=True, exist_ok=True)
        target = self.root / 'eagler-touhou' / 'ports' / 'src_real.cpp'
        for tool in ('acorn', 'rollup'):
            link = farm / tool
            if not link.exists():
                link.symlink_to(os.path.relpath(target, farm))
        self.r.maintenance_start('autonomy-tooling', 'foundation-runner', self.maintenance_approval())
        # Boundary for the maintenance card must not report the ignored
        # symlinks as out-of-scope changes...
        self.assertEqual(self.r.check_boundary('autonomy-tooling'), [])
        # ...and an authorized maintenance file still crosses cleanly.
        (self.root / 'scripts/verify.py').write_text('# reviewed repair\n')
        self.assertEqual(self.r.check_boundary('autonomy-tooling'), ['scripts/verify.py'])
        # A real edit inside the protected directory is still forbidden.
        (self.root / 'eagler-touhou' / 'dirty.txt').write_text('unauthorized inner edit')
        with self.assertRaisesRegex(Blocked, 'out-of-scope'):
            self.r.check_boundary('autonomy-tooling')

    def test_reviewed_maintenance_preserves_owner_baseline_history_and_requires_rerun(self):
        self.maintenance_fixture()
        old = copy.deepcopy(self.r.state['tasks']['foundation-runner'])
        self.r.maintenance_start('autonomy-tooling', 'foundation-runner', self.maintenance_approval())
        for action in [lambda: self.r.run('foundation-runner', 'fast'),
                       lambda: self.r.start('foundation-runner'),
                       lambda: self.r.recheck_dependencies('foundation-runner'),
                       lambda: self.r.finish('foundation-runner')]:
            with self.assertRaisesRegex(Blocked, 'suspended'): action()
        (self.root / 'scripts/verify.py').write_text('# independently reviewed repair\n')
        for tier in ('fast', 'full'):
            self.assertEqual(self.r.run('autonomy-tooling', tier)['type'], 'TOOL_PASS')
        self.assertEqual(self.r.finish('autonomy-tooling')['status'], 'DONE')
        self.r.maintenance_return('autonomy-tooling', self.maintenance_approval('return'))
        state = self.r.state['tasks']['foundation-runner']
        for field in ('baseline','baseline_hash','rounds','run'):
            self.assertEqual(state[field], old[field])
        self.assertIsNone(state['fast_receipt'])
        with self.assertRaises(Blocked): self.r.run('foundation-runner','full')
        self.r.check_boundary('foundation-runner')
        self.pair()
        self.assertEqual(self.r.finish('foundation-runner')['status'], 'DONE')
        # Named delta applies only to this owner's original baseline, not to
        # another card or a subsequent unreviewed change of the same file.
        (self.root / 'scripts/verify.py').write_text('# unauthorized later drift\n')
        with self.assertRaisesRegex(Blocked,'verify.py'): self.r.check_boundary('foundation-runner')

    def test_maintenance_rejects_stale_or_nonindependent_review_without_state_write(self):
        self.maintenance_fixture()
        path=self.maintenance_approval()
        good=load(self.root/path)
        before=copy.deepcopy(self.r.state)
        for field, value in [('workspace_sha256','wrong'),('owner_state_sha256','wrong'),
                             ('reviewer_role','implementer'),('approved',False),('issues',['open']),
                             ('files',['outside.txt'])]:
            atomic(self.root/path,dict(good,**{field:value}))
            with self.assertRaises(Blocked): self.r.maintenance_start('autonomy-tooling','foundation-runner',path)
            self.assertEqual(self.r.state,before)
        self.assertIsNone(self.r.maintenance_record())

    def test_maintenance_does_not_absorb_unrelated_delta_or_unfinished_checks(self):
        self.maintenance_fixture()
        (self.root/'outside.txt').write_text('unowned')
        with self.assertRaisesRegex(Blocked,'out-of-scope'):
            self.r.maintenance_start('autonomy-tooling','foundation-runner',self.maintenance_approval())
        (self.root/'outside.txt').write_text('existing dirty work')
        self.r.maintenance_start('autonomy-tooling','foundation-runner',self.maintenance_approval())
        with self.assertRaisesRegex(Blocked,'finish real'):
            self.r.maintenance_return('autonomy-tooling',self.maintenance_approval('return'))
        (self.root/'outside.txt').write_text('illegal while suspended')
        with self.assertRaisesRegex(Blocked,'out-of-scope'):
            self.r.run('autonomy-tooling','fast')
        self.assertEqual(self.r.maintenance_record()['phase'],'ACTIVE')

    def test_maintenance_return_requires_independent_exact_patch_review(self):
        self.maintenance_fixture()
        self.r.maintenance_start('autonomy-tooling','foundation-runner',self.maintenance_approval())
        (self.root/'scripts/verify.py').write_text('# reviewed delta\n')
        for tier in ('fast','full'): self.r.run('autonomy-tooling',tier)
        self.assertEqual(self.r.finish('autonomy-tooling')['status'],'DONE')
        path=self.maintenance_approval('return')
        review=load(self.root/path); review['reviewer_role']='implementer'
        atomic(self.root/path,review)
        with self.assertRaisesRegex(Blocked,'independent'): self.r.maintenance_return('autonomy-tooling',path)
        self.assertEqual(self.r.maintenance_record()['phase'],'ACTIVE')

    def test_game_profile_scopes_are_not_hashed_even_with_asset_suffix(self):
        for name in ['games/profile/private.dat','games/profiles/a/secrets.exe',
                     'games/credential/key.dat']:
            with patch.object(self.r,'file_record',side_effect=AssertionError('private read')):
                self.assertEqual(self.r.scoped([name]),{})

    def test_interrupted_exhausted_run_can_be_reviewed_without_rewriting_it(self):
        self.configure('false')
        for _ in range(2): self.r.run('foundation-runner','fast')
        with patch.object(self.r,'execute',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.r.run('foundation-runner','fast')
        self.r.refresh()
        state=self.r.state['tasks']['foundation-runner']
        runpath=self.r.home/state['run']/'run.json'; before=runpath.read_bytes()
        review=dict(schema=1,task_id='foundation-runner',card_sha256=digest(self.task),
                    baseline_sha256=state['baseline_hash'],last_run_id=state['run'],
                    new_hypothesis='termination prevented producer closure',experiment='run fixed gate with explicit process completion',
                    independent=True,approved=True,issues=[])
        path='artifacts/verification/interrupted-review.json'; atomic(self.root/path,review)
        result=self.r.mechanism_review('foundation-runner',path)
        self.assertEqual(runpath.read_bytes(),before)
        self.assertEqual(load(self.root/result['artifact'])['interruption_evidence']['status'],'INTERRUPTED')
        self.assertEqual(state['rounds'],0)
        self.assertIsNone(state['fast_receipt'])

    def test_bootstrap_migration_is_exact_and_limited_to_two_named_paths(self):
        self.maintenance_fixture()
        original=load(self.root/self.r.state['tasks']['foundation-runner']['baseline'])
        (self.root/'AGENTS.md').write_text('explicitly user-authorized direct GLM lead')
        path=self.maintenance_approval(); review=load(self.root/path)
        with self.assertRaisesRegex(Blocked,'out-of-scope'):
            self.r.maintenance_start('autonomy-tooling','foundation-runner',path)
        delta={'AGENTS.md':dict(before=original.get('AGENTS.md'),after=self.r.file_record('AGENTS.md'))}
        review['preauthorized_changes']=delta
        review['preauthorized_changes']['outside.txt']=dict(before=None,after=None)
        atomic(self.root/path,review)
        with self.assertRaisesRegex(Blocked,'limited'):
            self.r.maintenance_start('autonomy-tooling','foundation-runner',path)
        del review['preauthorized_changes']['outside.txt']
        review['preauthorized_changes']['AGENTS.md']['after']={'sha256':'old'}
        atomic(self.root/path,review)
        with self.assertRaisesRegex(Blocked,'before/after'):
            self.r.maintenance_start('autonomy-tooling','foundation-runner',path)
        review['preauthorized_changes']['AGENTS.md']['after']=self.r.file_record('AGENTS.md')
        atomic(self.root/path,review)
        self.r.maintenance_start('autonomy-tooling','foundation-runner',path)
        (self.root/'AGENTS.md').write_text('unapproved after drift')
        with self.assertRaisesRegex(Blocked,'drifted'):
            self.r.run('autonomy-tooling','fast')

    def reconciliation_fixture(self):
        owner, maintenance = 'replay-qualification-certification', 'autonomy-tooling'
        for name in RECONCILE_PATHS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('before ' + name)
        owner_card = copy.deepcopy(self.task)
        owner_card.update(id=owner, inputs=self.task['inputs'] + list(RECONCILE_PATHS))
        maintenance_card = copy.deepcopy(self.task)
        maintenance_card.update(id=maintenance, depends=[], allowed=['scripts/verify.py'],
                                protected=['verification/policy.json'])
        self.reset_graph([owner_card, maintenance_card], owner)
        path = 'artifacts/verification/reconcile-start-review.json'
        atomic(self.root / path, self.maintenance_review_for('start', owner, maintenance))
        self.r.maintenance_start(maintenance, owner, path)
        for name in RECONCILE_PATHS:
            (self.root / name).write_text('after ' + name)
        return owner, maintenance

    def maintenance_review_for(self, phase, owner, maintenance):
        return dict(schema=1, phase=phase, owner=owner, maintenance=maintenance,
                    owner_card_sha256=digest(self.r.tasks[owner]),
                    maintenance_card_sha256=digest(self.r.tasks[maintenance]),
                    owner_state_sha256=digest(self.r.state['tasks'][owner]),
                    workspace_sha256=digest(self.r.maintenance_workspace()),
                    files=self.r.tasks[maintenance]['allowed'], approved=True,
                    reviewer_role='independent', issues=[])

    def reconcile_review(self, owner, maintenance):
        path = 'artifacts/verification/reconcile-review-%s.json' % uuid.uuid4().hex
        record = dict(schema=1, phase='reconcile', reconcile_id='m0-06-%s' % uuid.uuid4().hex,
                      owner=owner, maintenance=maintenance,
                      owner_card_sha256=digest(self.r.tasks[owner]),
                      maintenance_card_sha256=digest(self.r.tasks[maintenance]),
                      owner_baseline_sha256=self.r.state['tasks'][owner]['baseline_hash'],
                      maintenance_baseline_sha256=self.r.state['tasks'][maintenance]['baseline_hash'],
                      owner_state_sha256=digest(self.r.state['tasks'][owner]),
                      maintenance_state_sha256=digest(self.r.state['tasks'][maintenance]),
                      start_review_sha256=self.r.maintenance_record()['start_review']['sha256'],
                      changes=[dict(boundary='owner', path=name, after=self.r.file_record(name))
                               for name in sorted(RECONCILE_PATHS)],
                      approved=True, reviewer_role='independent', issues=[])
        record['runner_sha256'], record['tool_identity'] = self.r.reconciliation_tools(owner, maintenance)
        record['reviewed_identity'] = self.r.reconciliation_review_identity(record)
        atomic(self.root / path, record)
        return path

    def test_reconcile_exact_paths_allow_fast_full_but_never_auto_done(self):
        owner, maintenance = self.reconciliation_fixture()
        before = copy.deepcopy(self.r.state)
        result = self.r.reconcile_maintenance(maintenance, owner, self.reconcile_review(owner, maintenance))
        self.assertEqual((result['status'], result['paths']), ('SCOPE_RECONCILIATION', 25))
        self.assertEqual(self.r.state, before)
        for tier in ('fast', 'full'):
            self.r.run(maintenance, tier)
            self.assertEqual(self.r.state['tasks'][maintenance]['status'], 'ACTIVE')
        self.assertEqual(self.r.finish(maintenance)['status'], 'DONE')
        path = 'artifacts/verification/reconcile-return-review.json'
        atomic(self.root / path, self.maintenance_review_for('return', owner, maintenance))
        self.r.maintenance_return(maintenance, path)
        self.assertEqual(self.r.maintenance_record()['phase'], 'RETURNED')
        self.assertEqual(self.r.state['tasks'][owner]['baseline_hash'], before['tasks'][owner]['baseline_hash'])

    def test_reconcile_rejects_bad_review_and_extra_paths_without_writing(self):
        for issue in ('missing', 'after-drift', 'foreign-baseline', 'extra-path'):
            with self.subTest(issue=issue):
                owner, maintenance = self.reconciliation_fixture()
                path = self.reconcile_review(owner, maintenance)
                if issue == 'missing':
                    path = 'artifacts/verification/no-such-reconcile-review.json'
                elif issue == 'after-drift':
                    (self.root / RECONCILE_PATHS[0]).write_text('drifted after review')
                elif issue == 'foreign-baseline':
                    review = load(self.root / path)
                    review['owner_baseline_sha256'] = 'f' * 64
                    atomic(self.root / path, review)
                else:
                    (self.root / 'src/reconcile-extra.txt').write_text('unreviewed path')
                with self.assertRaises(Blocked):
                    self.r.reconcile_maintenance(maintenance, owner, path)
                self.assertFalse((self.r.home / 'reconciliations').exists())

    def l1_maintenance_review(self, phase, owner, maintenance):
        review = dict(schema=1, phase=phase, owner=owner, maintenance=maintenance,
                      owner_card_sha256=digest(self.r.tasks[owner]),
                      maintenance_card_sha256=digest(self.r.tasks[maintenance]),
                      owner_state_sha256=digest(self.r.state['tasks'][owner]),
                      workspace_sha256=digest(self.r.maintenance_workspace(owner)),
                      files=self.r.tasks[maintenance]['allowed'], approved=True,
                      reviewer_role='independent', issues=[])
        path = f'artifacts/verification/l1-{phase}-{maintenance}.json'
        atomic(self.root / path, review)
        return path

    def l1_reconciliation_review(self, owner, maintenance):
        owner_state = self.r.state['tasks'][owner]
        maintenance_state = self.r.state['tasks'][maintenance]
        review = dict(schema=1, phase='reconcile', reconcile_id='l1-exact-25-credit',
                      owner=owner, maintenance=maintenance,
                      owner_card_sha256=digest(self.r.tasks[owner]),
                      maintenance_card_sha256=digest(self.r.tasks[maintenance]),
                      owner_baseline_sha256=owner_state['baseline_hash'],
                      maintenance_baseline_sha256=maintenance_state['baseline_hash'],
                      owner_state_sha256=digest(owner_state),
                      maintenance_state_sha256=digest(maintenance_state),
                      start_review_sha256=self.r.maintenance_record()['start_review']['sha256'],
                      changes=[dict(boundary='owner', path=name, after=self.r.file_record(name))
                               for name in sorted(RECONCILE_PATHS)],
                      approved=True, reviewer_role='independent', issues=[])
        review['runner_sha256'], review['tool_identity'] = self.r.reconciliation_tools(owner, maintenance)
        review['reviewed_identity'] = self.r.reconciliation_review_identity(review)
        path = 'artifacts/verification/l1-reconciliation-review.json'
        atomic(self.root / path, review)
        return path

    def test_l1_exact_25_reconciliation_allows_next_maintenance_but_not_drift(self):
        owner, maintenance, next_maintenance = (RECONCILE_OWNER, RECONCILE_MAINTENANCE,
                                                  'l1-next-product-maintenance')
        self.assertEqual(len(RECONCILE_PATHS), 25)
        for name in RECONCILE_PATHS:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('before ' + name)
        owner_card = copy.deepcopy(self.task)
        owner_card.update(id=owner, inputs=self.task['inputs'] + list(RECONCILE_PATHS))
        maintenance_card = copy.deepcopy(self.task)
        maintenance_card.update(id=maintenance, depends=[], allowed=['scripts/verify.py'],
                                protected=['verification/policy.json'])
        next_card = copy.deepcopy(self.task)
        next_card.update(id=next_maintenance, depends=[], allowed=['scripts/verify.py'],
                         protected=['verification/policy.json'])
        self.reset_graph([owner_card, maintenance_card, next_card], owner)

        self.r.maintenance_start(maintenance, owner,
                                 self.l1_maintenance_review('start', owner, maintenance))
        for name in RECONCILE_PATHS:
            (self.root / name).write_text('after ' + name)
        result = self.r.reconcile_maintenance(
            maintenance, owner, self.l1_reconciliation_review(owner, maintenance))
        self.assertEqual((result['status'], result['paths']), ('SCOPE_RECONCILIATION', 25))
        for tier in ('fast', 'full'):
            self.assertEqual(self.r.run(maintenance, tier)['type'], 'TOOL_PASS')
        self.assertEqual(self.r.finish(maintenance)['status'], 'DONE')
        self.r.maintenance_return(maintenance,
                                 self.l1_maintenance_review('return', owner, maintenance))
        self.assertEqual(self.r.maintenance_record()['phase'], 'RETURNED')

        next_review = self.l1_maintenance_review('start', owner, next_maintenance)
        self.assertEqual(self.r.maintenance_start(next_maintenance, owner, next_review)['status'], 'ACTIVE')
        drift = RECONCILE_PATHS[0]
        (self.root / drift).write_text('unauthorized post-reconciliation drift')
        with self.assertRaisesRegex(Blocked, 'reconciliation drift'):
            self.r.check_boundary(next_maintenance)

    def test_reconcile_projects_old_assets_reviewed_and_owner_work_but_rejects_extra(self):
        owner, maintenance = RECONCILE_OWNER, RECONCILE_MAINTENANCE
        asset, reviewed = 'games/th13/th13.dat', 'oracle/reviewed_repair.py'
        for issue in ('pass', 'extra-path', 'owner-late-drift', 'registry-drift'):
            with self.subTest(issue=issue):
                (self.root / '.gitignore').write_text(
                    'artifacts/\n__pycache__/\neagler-touhou/\ngames/\n')
                for path in [asset, reviewed] + list(RECONCILE_PATHS):
                    target = self.root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text('synthetic before ' + path)
                (self.root / 'src/input.txt').write_text('owner before')
                owner_card = copy.deepcopy(self.task)
                owner_card.update(id=owner, allowed=['src/input.txt'],
                                  inputs=self.task['inputs'] + [asset] + list(RECONCILE_PATHS))
                maintenance_card = copy.deepcopy(self.task)
                maintenance_card.update(id=maintenance, depends=[], allowed=['scripts/verify.py'],
                                        protected=['verification/policy.json'])
                self.reset_graph([owner_card, maintenance_card], owner)
                (self.root / reviewed).write_text('synthetic reviewed after')
                self._registry_fixture(None, authorized_files={reviewed: sha(self.root / reviewed)})
                (self.root / 'src/input.txt').write_text('legal owner work before maintenance')
                self.r.maintenance_start(
                    maintenance, owner, self.l1_maintenance_review('start', owner, maintenance))
                baselines = {task: self.r.state['tasks'][task]['baseline'] for task in (owner, maintenance)}
                baseline_hashes = {task: sha(self.root / path) for task, path in baselines.items()}
                self.assertIn(asset, load(self.root / baselines[owner]))
                self.assertNotIn(asset, load(self.root / baselines[maintenance]))
                self.assertNotIn(asset, self.r.maintenance_workspace(owner))
                for path in RECONCILE_PATHS:
                    (self.root / path).write_text('synthetic after ' + path)
                if issue == 'extra-path':
                    (self.root / 'projection-unowned.txt').write_text('unauthorized extra')
                elif issue == 'owner-late-drift':
                    (self.root / 'src/input.txt').write_text('owner path changed during maintenance')
                elif issue == 'registry-drift':
                    (self.root / reviewed).write_text('unreviewed later bytes')
                review_path = self.reconcile_review(owner, maintenance)
                state_before = copy.deepcopy(self.r.state)
                if issue == 'pass':
                    result = self.r.reconcile_maintenance(maintenance, owner, review_path)
                    self.assertEqual((result['status'], result['paths']), ('SCOPE_RECONCILIATION', 25))
                    self.assertEqual(set(self.r.reconciliation_credit(
                        owner, baseline_hashes[owner])), set(RECONCILE_PATHS))
                    (self.root / RECONCILE_PATHS[0]).write_text('unreviewed post-credit drift')
                    with self.assertRaisesRegex(Blocked, 'reconciliation drift'):
                        self.r.reconciliation_credit(owner, baseline_hashes[owner])
                else:
                    with self.assertRaisesRegex(Blocked, 'unreviewed extra reconciliation delta'):
                        self.r.reconcile_maintenance(maintenance, owner, review_path)
                    self.assertFalse((self.r.home / 'reconciliations').exists())
                self.assertEqual(self.r.state, state_before)
                for task, path in baselines.items():
                    self.assertEqual(sha(self.root / path), baseline_hashes[task])

    def test_reconciled_product_successor_can_edit_return_and_reject_future_drift(self):
        owner, tools = RECONCILE_OWNER, RECONCILE_MAINTENANCE
        product, followup = 'l1-product-stagebattle', 'l1-followup-tools'
        target = next(path for path in RECONCILE_PATHS if path.endswith('/StageBattle.cpp'))
        other = next(path for path in RECONCILE_PATHS if path != target)
        for path in RECONCILE_PATHS:
            file = self.root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('synthetic original ' + path)
        owner_card = copy.deepcopy(self.task)
        owner_card.update(id=owner, inputs=self.task['inputs'] + list(RECONCILE_PATHS))
        tool_card = copy.deepcopy(self.task)
        tool_card.update(id=tools, depends=[], allowed=['scripts/verify.py'],
                         protected=['verification/policy.json'])
        product_card = copy.deepcopy(tool_card)
        product_card.update(id=product, allowed=[target])
        followup_card = copy.deepcopy(tool_card)
        followup_card.update(id=followup)
        self.reset_graph([owner_card, tool_card, product_card, followup_card], owner)
        trace = []
        self.r.maintenance_start(tools, owner, self.l1_maintenance_review('start', owner, tools))
        for path in RECONCILE_PATHS:
            (self.root / path).write_text('synthetic reconciled after ' + path)
        result = self.r.reconcile_maintenance(tools, owner, self.reconcile_review(owner, tools))
        self.assertEqual((result['status'], result['paths']), ('SCOPE_RECONCILIATION', 25))
        ledger_path = self.root / result['artifact']
        ledger_before = ledger_path.read_bytes()
        ledger_sha = sha(ledger_path)
        trace.append('exact25 reconciled')
        for tier in ('fast', 'full'):
            self.assertEqual(self.r.run(tools, tier)['type'], 'TOOL_PASS')
        self.assertEqual(self.r.finish(tools)['status'], 'DONE')
        self.r.maintenance_return(tools, self.l1_maintenance_review('return', owner, tools))
        trace.append('tools fast/full/finish/return')

        self.assertEqual(self.r.maintenance_start(
            product, owner, self.l1_maintenance_review('start', owner, product))['status'], 'ACTIVE')
        baselines = {task: self.r.state['tasks'][task]['baseline'] for task in (owner, tools, product)}
        baseline_hashes = {task: sha(self.root / path) for task, path in baselines.items()}
        historical_credit = self.r.reconciliation_credit(owner, baseline_hashes[owner])[target]
        (self.root / target).write_text('synthetic legitimate product revision')
        product_after = self.r.file_record(target)
        self.assertNotEqual(product_after, historical_credit)
        self.assertEqual(self.r.check_boundary(product), [target])
        self.assertEqual(self.r.reconciliation_credit(owner, baseline_hashes[owner])[target],
                         historical_credit)
        trace.append('product started and allowed StageBattle.cpp edit crosses real boundary')
        other_bytes = (self.root / other).read_bytes()
        (self.root / other).write_text('synthetic unauthorized different product file')
        with self.assertRaisesRegex(Blocked, 'reconciliation drift'):
            self.r.check_boundary(product)
        (self.root / other).write_bytes(other_bytes)
        trace.append('different unowned credited path drift rejected while product active')
        for tier in ('fast', 'full'):
            self.assertEqual(self.r.run(product, tier)['type'], 'TOOL_PASS')
            self.assertEqual(self.r.state['tasks'][product]['status'], 'ACTIVE')
        self.assertEqual(self.r.finish(product)['status'], 'DONE')
        self.r.maintenance_return(product, self.l1_maintenance_review('return', owner, product))
        self.assertEqual(self.r.maintenance_record()['phase'], 'RETURNED')
        self.assertEqual(self.r.maintenance_deltas(owner, baseline_hashes[owner])[target], product_after)
        self.assertEqual(self.r.check_boundary(owner), [])
        trace.append('product fast/full/finish/return; original owner boundary passes')
        product_bytes = (self.root / target).read_bytes()
        (self.root / target).write_text('synthetic unauthorized drift after product return')
        with self.assertRaisesRegex(Blocked, 'reconciliation drift'):
            self.r.check_boundary(owner)
        (self.root / target).write_bytes(product_bytes)
        self.assertEqual(self.r.check_boundary(owner), [])
        trace.append('same product path drift after return rejected; exact returned after still passes')
        self.assertEqual(self.r.maintenance_start(
            followup, owner, self.l1_maintenance_review('start', owner, followup))['status'], 'ACTIVE')
        self.assertEqual(self.r.check_boundary(followup), [])
        trace.append('unrelated followup start accepts only exact returned maintenance delta')
        self.assertEqual(ledger_path.read_bytes(), ledger_before)
        self.assertEqual(sha(ledger_path), ledger_sha)
        self.assertEqual(len(list((self.r.home / 'reconciliations').glob('*.json'))), 1)
        for task, path in baselines.items():
            self.assertEqual(sha(self.root / path), baseline_hashes[task])
        print(json.dumps(dict(control='reconciled-product-successor', target=target,
                              trace=trace, ledger_before_sha256=ledger_sha,
                              ledger_after_sha256=sha(ledger_path),
                              original_baselines_unchanged=True, historical_credit=historical_credit,
                              returned_product_after=product_after), sort_keys=True))


    def test_returned_product_after_rejects_exact_historical_credit_reversion(self):
        # Reuse the unchanged real lifecycle from the independent review's
        # control: exact25 -> tools return -> product edit/return -> tools start.
        self.test_reconciled_product_successor_can_edit_return_and_reject_future_drift()
        owner = RECONCILE_OWNER
        active = self.r.maintenance_record()
        followup = active['maintenance']
        target = 'eagler-touhou/ports/th13/web/cpp/game/StageBattle.cpp'
        self.assertEqual(active['phase'], 'ACTIVE')
        self.assertNotIn(target, self.r.tasks[followup]['allowed'])
        ledgers = sorted((self.r.home / 'reconciliations').glob('*.json'))
        self.assertEqual(len(ledgers), 1)
        historical = next(c['after'] for c in load(ledgers[0])['changes'] if c['path'] == target)
        returned = self.r.maintenance_deltas(
            owner, self.r.state['tasks'][owner]['baseline_hash'])[target]
        self.assertNotEqual(returned, historical)
        self.assertEqual(self.r.file_record(target), returned)
        returned_bytes = (self.root / target).read_bytes()
        baseline_hashes = {s['baseline']: sha(self.root / s['baseline'])
                           for s in self.r.state['tasks'].values() if s['baseline']}
        ledger_sha = sha(ledgers[0])
        (self.root / target).write_text('synthetic reconciled after ' + target)
        self.assertEqual(self.r.file_record(target), historical)
        observed = {}
        for task in (owner, followup):
            try:
                result = self.r.check_boundary(task)
            except Blocked as error:
                observed[task] = dict(blocked=True, reason=str(error))
            else:
                observed[task] = dict(blocked=False, boundary_result=result)
        for path, value in baseline_hashes.items():
            self.assertEqual(sha(self.root / path), value)
        self.assertEqual(sha(ledgers[0]), ledger_sha)
        print(json.dumps(dict(control='exact-historical-credit-reversion',
                              historical_credit=historical, returned_product_after=returned,
                              observed=observed, ledger_and_baselines_unchanged=True), sort_keys=True))
        for task, result in observed.items():
            with self.subTest(task=task):
                self.assertTrue(result['blocked'], result)
                self.assertIn('reconciliation drift', result['reason'])
        (self.root / target).write_bytes(returned_bytes)
        self.assertEqual(self.r.file_record(target), returned)
        self.assertEqual(self.r.reconciliation_credit(
            owner, self.r.state['tasks'][owner]['baseline_hash'])[target], returned)
        self.assertEqual(self.r.check_boundary(owner), [])
        self.assertEqual(self.r.check_boundary(followup), [])
        self.assertEqual(sha(ledgers[0]), ledger_sha)
        for path, value in baseline_hashes.items():
            self.assertEqual(sha(self.root / path), value)


if __name__ == '__main__':
    unittest.main()
