#!/usr/bin/env python3
"""Synthetic configuration controls; never open or run the real task graph."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import task_configure as tc
from verify import Blocked, Runner, atomic, digest, load


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/work/artifacts" if Path("/work/artifacts").is_dir() else None)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("artifacts/\n")
        policy = dict(schema=1, environment=dict(root="/work", home="/home/dev",
                      container_marker="/run/.containerenv", required_paths=["/opt/wasi-sdk", "/opt/emsdk"]),
                      result_types=sorted(tc.Runner.__init__.__globals__["TYPES"]), protected=["AGENTS.md", "games"])
        atomic(self.root / "verification/policy.json", policy)
        self.old = dict(id="complete-capture", goal="Original exact acceptance", non_goals=["No weakening"],
                        read=["AGENTS.md"], allowed=[], protected=["verification", "scripts/verify.py"],
                        depends=[], inputs=["games"], checks=dict(fast=[], full=[]), evidence=["Original evidence"],
                        stop="Keep original stop", max_rounds=3, completion="FIDELITY_PASS", blocked_reason="unconfigured")
        self.graph = dict(schema=1, tasks=[self.old])
        atomic(self.root / "verification/tasks.json", self.graph)
        self.proposal = dict(schema=1, task_id=self.old["id"], base_task_sha256=digest(self.old),
                             allowed=["oracle/new_capture.py", tc.PORT + "web/cpp/game/Capture.cpp"],
                             read=["scripts/capture_gate.py", "oracle/test_new_capture.py"],
                             inputs=["scripts/capture_gate.py", "oracle/test_new_capture.py"],
                             checks=dict(fast=[self.tool()], full=[self.fidelity()]),
                             goal=self.old["goal"], non_goals=self.old["non_goals"] + ["More restrictive"],
                             evidence=["Real gate receipts required"], reason="Configure named capture module")
        self.pp = self.root / "artifacts/verification/proposal.json"
        self.rp = self.root / "artifacts/verification/review.json"
        self.write_review()

    @staticmethod
    def tool():
        return dict(id="controls", argv=["python3", tc.WRAPPER, "--", "python3", "-m", "unittest",
                                       "oracle.test_new_capture", "-v"], cwd=".", timeout=30,
                    result_type="TOOL_PASS", window=None, external=False)

    @staticmethod
    def fidelity():
        return dict(id="capture", argv=["python3", "scripts/capture_gate.py"], cwd=".", timeout=30,
                    result_type="FIDELITY_PASS", window=dict(start=0, end=9, roles=["original", "port"]), external=False)

    def write_review(self):
        atomic(self.pp, self.proposal)
        self.review = dict(schema=1, task_id=self.proposal["task_id"],
                           proposal_sha256=hashlib.sha256(self.pp.read_bytes()).hexdigest(),
                           base_task_sha256=self.proposal["base_task_sha256"], approved=True,
                           issues=[], reviewer_role="independent")
        atomic(self.rp, self.review)

    def apply(self):
        return tc.configure(self.root, self.pp, self.rp, check_environment=False)

    def rejected(self, message=None):
        before = (self.root / "verification/tasks.json").read_bytes()
        state_path = self.root / "artifacts/verification/state.json"
        state_before = state_path.read_bytes() if state_path.exists() else None
        with self.assertRaises(Blocked) as result:
            self.apply()
        if message:
            self.assertIn(message, str(result.exception))
        self.assertEqual(before, (self.root / "verification/tasks.json").read_bytes())
        self.assertEqual(state_before, state_path.read_bytes() if state_path.exists() else None)

    def test_success_preserves_acceptance_state_and_hash_audit(self):
        result = self.apply()
        new = load(self.root / "verification/tasks.json")["tasks"][0]
        for key in ("id", "depends", "goal", "completion", "max_rounds", "stop", "protected"):
            self.assertEqual(new[key], self.old[key])
        self.assertEqual(new["blocked_reason"], "")
        self.assertIn("Original evidence", new["evidence"])
        self.assertEqual(new["non_goals"][:1], self.old["non_goals"])
        self.assertFalse((self.root / "artifacts/verification/state.json").exists())
        audit = load(self.root / result["audit"])
        self.assertEqual(audit["old_card_sha256"], digest(self.old))
        self.assertEqual(audit["new_card_sha256"], digest(new))
        self.assertEqual(audit["proposal_sha256"], hashlib.sha256(self.pp.read_bytes()).hexdigest())
        self.assertEqual(audit["review_sha256"], hashlib.sha256(self.rp.read_bytes()).hexdigest())
        self.assertEqual(audit["old_card"], self.old)
        self.assertEqual(audit["new_card"], new)
        self.assertIsNone(audit["check_sources"]["scripts/capture_gate.py"])
        Runner(self.root)  # resulting whole graph remains authoritative Runner schema/DAG valid
        self.rejected("only blocked empty-gate")

    def test_never_started_blocked_state_is_not_rewritten(self):
        state = Runner.empty_state()
        state.update(status="BLOCKED", reason="empty gates")
        atomic(self.root / "artifacts/verification/state.json", dict(schema=1, tasks={self.old["id"]: state}))
        before = (self.root / "artifacts/verification/state.json").read_bytes()
        self.apply()
        self.assertEqual(before, (self.root / "artifacts/verification/state.json").read_bytes())

    def test_past_done_active_failed_stale_or_history_refused(self):
        for status in ("DONE", "ACTIVE", "VERIFYING", "FAILED", "STALE", "BLOCKED", "PENDING"):
            with self.subTest(status=status):
                state = Runner.empty_state()
                state.update(status=status, baseline="artifacts/old.json", baseline_hash="old", rounds=1)
                atomic(self.root / "artifacts/verification/state.json", dict(schema=1, tasks={self.old["id"]: state}))
                self.rejected("task has started")

    def test_partial_or_already_configured_card_refused(self):
        self.old["checks"]["fast"] = [self.tool()]
        atomic(self.root / "verification/tasks.json", self.graph)
        self.proposal["base_task_sha256"] = digest(self.old)
        self.write_review()
        self.rejected("only blocked empty-gate")

    def test_immutable_acceptance_and_unknown_fields(self):
        base = copy.deepcopy(self.proposal)
        for field, value in (("completion", "TOOL_PASS"), ("depends", []), ("max_rounds", 9),
                             ("stop", "weaken"), ("protected", []), ("blocked_reason", ""),
                             ("goal", "Different goal"), ("non_goals", ["remove original"])):
            with self.subTest(field=field):
                self.proposal = dict(copy.deepcopy(base), **{field: value})
                self.write_review()
                self.rejected()

    def test_hash_mismatch_base_proposal_review_task(self):
        for field, value in (("base_task_sha256", "0" * 64), ("proposal_sha256", "0" * 64), ("task_id", "other")):
            with self.subTest(field=field):
                self.write_review()
                self.review[field] = value
                atomic(self.rp, self.review)
                self.rejected("hash")
        self.proposal["base_task_sha256"] = "0" * 64
        self.write_review()
        self.rejected("hash")

    def test_review_denial_issues_role_and_schema(self):
        for field, value in (("approved", False), ("approved", 1), ("issues", ["missing evidence"]),
                             ("issues", {}), ("reviewer_role", "planner"), ("schema", True)):
            with self.subTest(field=field):
                self.write_review()
                self.review[field] = value
                atomic(self.rp, self.review)
                self.rejected()

    def test_proposal_unknown_task_schema_duplicate_json(self):
        self.pp.write_text('{"schema":1,"schema":1}')
        self.rejected("duplicate")
        self.proposal["task_id"] = "unknown"
        self.write_review()
        self.rejected("unknown existing task")

    def test_unbounded_write_and_protected_scopes(self):
        paths = [".", "oracle", "scripts", "oracle/*", "oracle/../verify.py", "/tmp/escape.py",
                 "scripts/verify.py", "verification/tasks.json", "AGENTS.md", "games/replay.py",
                 "scripts/verification_lib/new.py", "oracle/golden.py", "oracle/reset_baseline.py",
                 "oracle/threshold.py", "oracle/draw_compare.py", tc.DELIVERY,
                 tc.PORT + "portable/sdl/Renderer.cpp", tc.PORT + "web/cpp/game/RenderCommands.cpp",
                 tc.PORT + "web/cpp/rebuild/Renderer.cpp", tc.PORT + "web/cpp/sdl/GraphicsDevice.cpp"]
        for path in paths:
            with self.subTest(path=path):
                self.proposal["allowed"] = [path]
                self.write_review()
                self.rejected()

    def test_existing_tests_and_adapters_cannot_be_rewritten(self):
        for name in ("oracle/test_existing.py", "scripts/capture_gate.py", tc.PORT + "tests/old.test.mjs"):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# immutable existing test/adapter\n")
            self.proposal["allowed"] = [name]
            self.write_review()
            self.rejected()

    def test_new_named_tests_and_application_files_allowed(self):
        self.proposal["allowed"] += ["oracle/test_new_capture.py", "scripts/new_capture_adapter.py",
                                     tc.PORT + "tests/new.test.mjs", tc.PORT + "web/cpp/sdl/Application.cpp",
                                     tc.PORT + "web/cpp/game/AnmRenderer.cpp",
                                     tc.PORT + "web/cpp/game/AnmManager.cpp"]
        self.write_review()
        self.apply()

    def test_application_state_header_is_configurable(self):
        name = tc.PORT + "web/cpp/sdl/ApplicationState.hpp"
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#pragma once\n")
        self.proposal["allowed"].append(name)
        self.write_review()
        self.apply()
        new = load(self.root / "verification/tasks.json")["tasks"][0]
        self.assertIn(name, new["allowed"])

    def test_application_state_directory_neighbor_still_rejected(self):
        name = tc.PORT + "web/cpp/sdl/ApplicationTitle.cpp"
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// not an authorized SDL source\n")
        self.proposal["allowed"].append(name)
        self.write_review()
        self.rejected("not business oracle/port C++/named new adapter")

    def test_symlink_and_ignored_path_requirements(self):
        (self.root / "oracle").symlink_to(self.root / "artifacts", target_is_directory=True)
        self.rejected("symlink")

    def test_review_and_proposal_must_be_ignored_and_not_aliased(self):
        other = self.root / "not-ignored.json"
        other.write_bytes(self.pp.read_bytes())
        self.pp = other
        self.rejected("gitignored")
        elsewhere = self.root / "artifacts/elsewhere-proposal.json"
        elsewhere.write_bytes(self.pp.read_bytes())
        self.pp = elsewhere
        self.rejected("artifacts/verification")

    def test_checks_both_tiers_completion_and_window_required(self):
        original = copy.deepcopy(self.proposal)
        for mode in ("fast", "full", "completion", "window"):
            with self.subTest(mode=mode):
                self.proposal = copy.deepcopy(original)
                if mode in {"fast", "full"}:
                    self.proposal["checks"][mode] = []
                elif mode == "completion":
                    self.proposal["checks"]["full"] = [self.tool()]
                else:
                    self.proposal["checks"]["full"][0]["window"] = None
                self.write_review()
                self.rejected()

    def test_shell_nested_container_inline_static_filters_baselines(self):
        commands = [["bash", "-c", "true"], ["python3", tc.WRAPPER, "--", "podman", "run", "image"],
                    ["python3", "-c", "print('pass')"], ["node", "-e", "process.exit(0)"],
                    ["echo", "pass"], ["python3", "README.md"], ["python3", "scripts/verify.py", "doctor"],
                    ["python3", tc.WRAPPER, "--", "env", "sh", "-c", "true"],
                    ["python3", tc.WRAPPER, "--", "test", "-f", "README.md"]]
        commands += [["python3", "scripts/capture_gate.py", option] for option in
                     ("--ignore=texture", "--ulp=1", "--ulp=NaN", "--ulp=Infinity", "--ulp", "--ulp-extra=0",
                      "--update-golden", "--reset-baseline", "--filter=field", "--skip=scene", "--help")]
        commands += [["python3", "scripts/capture_gate.py", "--ulp", "0.01"]]
        for command in commands:
            with self.subTest(command=command):
                self.proposal["checks"]["full"][0]["argv"] = command
                self.write_review()
                self.rejected()

    def test_wrapper_cannot_emit_fidelity(self):
        c = self.tool()
        c.update(result_type="FIDELITY_PASS", window=self.fidelity()["window"])
        self.proposal["checks"]["full"] = [c]
        self.write_review()
        self.rejected("TOOL wrapper cannot issue FIDELITY")

    def test_unreviewed_or_unbound_gate_refused(self):
        self.proposal["inputs"] = ["oracle/test_new_capture.py"]
        self.write_review()
        self.rejected("identity-bound")

    def test_zero_ulp_allowed_no_approximation(self):
        self.proposal["checks"]["full"][0]["argv"] += ["--ulp", "0"]
        self.write_review()
        self.apply()

    def test_final_gate_mandatory_exact_and_direct(self):
        for task_id, scope in tc.FINAL.items():
            with self.subTest(task=task_id):
                self.old["id"] = task_id
                atomic(self.root / "verification/tasks.json", self.graph)
                self.proposal["task_id"] = task_id
                self.proposal["base_task_sha256"] = digest(self.old)
                self.proposal["read"] += [tc.DELIVERY] if tc.DELIVERY not in self.proposal["read"] else []
                self.proposal["inputs"] += [tc.DELIVERY] if tc.DELIVERY not in self.proposal["inputs"] else []
                self.proposal["checks"]["full"] = [self.fidelity()]
                self.write_review()
                self.rejected("mandatory final")
                self.proposal["checks"]["full"][0]["argv"] = ["python3", tc.DELIVERY, "--scope", scope, "--extra"]
                self.write_review()
                self.rejected("mandatory final")
                self.proposal["checks"]["full"][0]["argv"] = ["python3", tc.DELIVERY, "--scope", scope]
                self.write_review()
                self.apply()

    def test_dag_still_validated_by_runner(self):
        self.old["depends"] = [self.old["id"]]
        atomic(self.root / "verification/tasks.json", self.graph)
        self.rejected("dependency")

    def test_audit_failure_never_changes_card(self):
        with patch.object(tc, "atomic", side_effect=OSError("audit write failed")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(load(self.root / "verification/tasks.json"), self.graph)

    def test_atomic_save_failure_keeps_old_card_and_intent_audit(self):
        original = tc.atomic
        def fail_target(path, value):
            if Path(path) == self.root / "verification/tasks.json":
                raise OSError("save failure")
            original(path, value)
        with patch.object(tc, "atomic", side_effect=fail_target):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(load(self.root / "verification/tasks.json"), self.graph)
        self.assertEqual(len(list((self.root / "artifacts/verification/task-configure").glob("*.json"))), 1)

    def test_does_not_execute_checks_or_refresh_or_save_state(self):
        with patch.object(Runner, "execute", side_effect=AssertionError("executed")), \
             patch.object(Runner, "refresh", side_effect=AssertionError("refresh")), \
             patch.object(Runner, "save", side_effect=AssertionError("state mutation")):
            self.apply()

    def test_cli_refuses_outside_container_environment(self):
        with patch.object(tc, "ROOT", self.root):
            self.assertEqual(tc.main(["--proposal", str(self.pp), "--review", str(self.rp)]), 2)
        self.assertEqual(load(self.root / "verification/tasks.json"), self.graph)

    def test_empty_card_without_blocked_reason_is_configurable(self):
        self.old['blocked_reason']=''
        atomic(self.root/'verification/tasks.json',self.graph)
        self.proposal['base_task_sha256']=digest(self.old)
        self.write_review()
        self.apply()

    def test_existing_test_additions_require_bound_hash_and_independent_policy(self):
        name='oracle/test_existing.py'
        path=self.root/name; path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('def test_existing():\n    assert True\n')
        self.proposal['allowed'].append(name)
        self.proposal['read'].append(name); self.proposal['inputs'].append(name)
        self.proposal['additive_tests']={name:hashlib.sha256(path.read_bytes()).hexdigest()}
        self.write_review()
        self.rejected('fields')
        self.review['test_change_policy']='remove old controls'
        atomic(self.rp,self.review); self.rejected('policy')
        self.review['test_change_policy']='additive-controls-only; no acceptance weakening'
        atomic(self.rp,self.review)
        before=path.read_bytes()
        path.write_text('changed before authorization')
        self.rejected('reviewed additive')
        path.write_bytes(before)
        self.apply()

    def bound_owner(self):
        owner = copy.deepcopy(self.old)
        owner.update(id='existing-owner', checks=dict(fast=[self.tool()],full=[self.tool()]),
                     completion='TOOL_PASS', blocked_reason='', inputs=['scripts/owner-input.py'], allowed=['scripts/owner-input.py'])
        (self.root/'scripts').mkdir(exist_ok=True)
        (self.root/'scripts/owner-input.py').write_text('# synthetic input')
        self.graph['tasks'].append(owner)
        atomic(self.root/'verification/tasks.json',self.graph)
        runner=Runner(self.root); runner.refresh(); runner.start(owner['id'])
        self.proposal['owner_binding']=dict(id=owner['id'],baseline_sha256=runner.state['tasks'][owner['id']]['baseline_hash'],card_sha256=digest(owner))
        self.write_review()
        return runner,owner

    def test_owner_bound_configuration_chain_and_negative_controls(self):
        runner,owner=self.bound_owner()
        result=self.apply()
        current=Runner(self.root); current.check_boundary(owner['id'])
        auditpath=self.root/result['audit']; good=load(auditpath)
        for change in ['unreviewed','current-card','broken-chain','old-owner']:
            audit=copy.deepcopy(good)
            if change=='unreviewed': audit['review']['approved']=False
            if change=='current-card': audit['graph_after']['tasks'][1]['goal']='changed live owner'
            if change=='broken-chain': audit['graph_before_record']['sha256']='f'*64
            if change=='old-owner': audit['owner_binding']['baseline_sha256']='old'
            atomic(auditpath,audit)
            with self.subTest(change=change),self.assertRaises(Blocked):
                Runner(self.root).check_boundary(owner['id'])
        atomic(auditpath,good)
        graph=load(self.root/'verification/tasks.json'); graph['tasks'][0]['goal']='manual drift'
        atomic(self.root/'verification/tasks.json',graph)
        with self.assertRaises(Blocked): Runner(self.root).check_boundary(owner['id'])

    def test_new_maintenance_has_fixed_constraints_and_preserves_graph(self):
        runner,owner=self.bound_owner()
        self.proposal.update(task_id='oracle-repair',maintenance=True,base_task_sha256=digest(None),
                             checks=dict(fast=[self.tool()],full=[self.tool()]))
        self.write_review(); self.apply()
        graph=load(self.root/'verification/tasks.json')
        self.assertEqual(graph['tasks'][:2],self.graph['tasks'])
        new=graph['tasks'][-1]
        self.assertEqual((new['completion'],new['max_rounds'],new['depends']),('TOOL_PASS',3,[]))
        self.assertIn('games',new['protected']); self.assertIn('verification',new['protected'])
        Runner(self.root).check_boundary(owner['id'])

    def test_new_maintenance_requires_owner_review_and_cannot_replace_fidelity(self):
        runner,owner=self.bound_owner()
        self.proposal.update(task_id='oracle-repair',maintenance=True,base_task_sha256=digest(None))
        self.write_review(); self.rejected('TOOL_PASS')
        self.proposal['checks']['full']=[self.tool()]
        self.proposal['allowed']=['oracle/draw_compare.py']
        self.write_review(); self.rejected('comparison')
        self.proposal['allowed']=['oracle/new_capture.py']
        del self.proposal['owner_binding']; self.write_review(); self.rejected('owner_binding')

    def test_default_lead_maintenance_configure_repair_and_next_task_end_to_end(self):
        from test_verify import RunnerTests
        helper=RunnerTests('test_success_dirty_baselines_both_repos_and_handoff')
        helper.setUp(); self.addCleanup(helper.doCleanups)
        # Earlier reviewed tool maintenance edited this explicitly owned file;
        # declare it in the synthetic owner scope before the fresh baseline.
        helper.task['allowed'].append('scripts/verify.py')
        # The maintenance card exists in the pre-baseline graph; the owner then
        # exhausts its three real failure rounds without live graph mutation.
        helper.maintenance_fixture(owner_mode='false')
        for _ in range(3): helper.r.run('foundation-runner','fast')
        r=helper.r; root=helper.root; owner='foundation-runner'; tools='autonomy-tooling'
        r.maintenance_start(tools,owner,helper.maintenance_approval())
        def configure_card(task_id,goal,adding,base):
            state=r.state['tasks'][owner]
            check=dict(id='synthetic',argv=['python3','scripts/producer.py','pass'],cwd='.',timeout=10,
                       result_type='TOOL_PASS',window=None,external=False)
            proposal=dict(schema=1,task_id=task_id,base_task_sha256=base,allowed=['oracle/repair.py'] if adding else [],
                          read=['scripts/producer.py'],inputs=['scripts/producer.py'],checks=dict(fast=[check],full=[check]),
                          goal=goal,non_goals=['No game fidelity claim'],evidence=['actual synthetic process receipts'],reason='synthetic lead sequence',
                          owner_binding=dict(id=owner,baseline_sha256=state['baseline_hash'],card_sha256=digest(r.tasks[owner])))
            if adding: proposal['maintenance']=True
            pp=root/f'artifacts/verification/{task_id}-proposal.json'
            rp=root/f'artifacts/verification/{task_id}-review.json'
            atomic(pp,proposal)
            atomic(rp,dict(schema=1,task_id=task_id,base_task_sha256=base,proposal_sha256=hashlib.sha256(pp.read_bytes()).hexdigest(),
                           approved=True,issues=[],reviewer_role='independent'))
            return tc.configure(root,pp,rp,check_environment=False)
        configure_card('oracle-repair','repair synthetic producer input',True,digest(None))
        helper.r=Runner(root); r=helper.r
        # The configuration chain is accepted inside the suspended tools task.
        (root/'scripts/verify.py').write_text('# synthetic reviewed tool revision')
        for tier in ('fast','full'):
            result=r.run(tools,tier)
            self.assertEqual(result['type'],'TOOL_PASS',result)
        self.assertEqual(r.finish(tools)['status'],'DONE')
        r.maintenance_return(tools,helper.maintenance_approval('return'))
        # Same review format, a different named business maintenance card.
        def approval(phase,maintenance):
            review=dict(schema=1,phase=phase,owner=owner,maintenance=maintenance,
                        owner_card_sha256=digest(r.tasks[owner]),maintenance_card_sha256=digest(r.tasks[maintenance]),
                        owner_state_sha256=digest(r.state['tasks'][owner]),workspace_sha256=digest(r.maintenance_workspace(owner)),
                        files=r.tasks[maintenance]['allowed'],approved=True,reviewer_role='independent',issues=[])
            path=f'artifacts/verification/{maintenance}-{phase}.json'; atomic(root/path,review); return path
        r.maintenance_start('oracle-repair',owner,approval('start','oracle-repair'))
        (root/'oracle').mkdir(exist_ok=True); (root/'oracle/repair.py').write_text('# synthetic named repair')
        for tier in ('fast','full'): self.assertEqual(r.run('oracle-repair',tier)['type'],'TOOL_PASS')
        self.assertEqual(r.finish('oracle-repair')['status'],'DONE')
        r.maintenance_return('oracle-repair',approval('return','oracle-repair'))
        # Force the old tools receipt STALE by changing its permitted source;
        # history must remain verifiable without insisting it is current DONE.
        r.refresh()
        state=r.state['tasks'][owner]
        review=dict(schema=1,task_id=owner,card_sha256=digest(r.tasks[owner]),baseline_sha256=state['baseline_hash'],
                    last_run_id=state['run'],new_hypothesis='new explicitly reviewed producer mechanism',experiment='re-run fixed process gate',
                    independent=True,approved=True,issues=[])
        path='artifacts/mechanism.json'; atomic(root/path,review)
        r.mechanism_review(owner,path)
        r.recheck_dependencies(owner)
        execute=r.execute
        def passed(check,run,directory):
            return execute(dict(check,argv=['python3','scripts/producer.py','pass']),run,directory)
        with patch.object(r,'execute',side_effect=passed):
            for tier in ('fast','full'): self.assertEqual(r.run(owner,tier)['type'],'TOOL_PASS')
        self.assertEqual(r.finish(owner)['status'],'DONE')
        # A completed, now stale receipt must not become an active claim.
        r.state['tasks'][tools]['status']='STALE'; r.save()
        r.start('oracle-repair') if False else None
        # New task already configured by the same owner-bound chain can start
        # after owner completion (not an implicit rebaseline of the old owner).
        nextcard=copy.deepcopy(r.tasks['oracle-repair']); nextcard.update(id='next-card',depends=[owner])
        # Synthetic fixed next-card fixture, no real task graph is modified.
        graph=load(root/'verification/tasks.json'); graph['tasks'].append(nextcard); atomic(root/'verification/tasks.json',graph)
        r=Runner(root); r.state['tasks']['next-card']['status']='READY'
        self.assertEqual(r.start('next-card')['status'],'ACTIVE')


if __name__ == "__main__":
    unittest.main()
