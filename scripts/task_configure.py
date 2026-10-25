#!/usr/bin/env python3
"""Configure one unstarted empty-gate card; never run checks or change runner state.

Proposal schema 1: schema, task_id, base_task_sha256 (verify.digest(card)),
allowed, read, inputs, checks:{fast:[],full:[]}, goal, non_goals, evidence, reason.
Review schema 1: schema, task_id, proposal_sha256 (file bytes), base_task_sha256,
approved:true, issues:[], reviewer_role:"independent".
Original goal must match exactly; original non_goals must remain an ordered prefix.
Read/inputs/evidence are additive. A missing named adapter can be planned, but
must actually execute and produce runner evidence before anything can pass.
Review is independent-agent evidence, NOT authentication or a sandbox against a
repository writer with equal permissions. Semantic review of business checks is
still mandatory; neither this tool nor a signed-looking JSON proves fidelity.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
from decimal import Decimal, InvalidOperation

from verify import Blocked, ROOT, Runner, atomic, digest, keys, load, relative, require, texts, within

PORT = "eagler-touhou/ports/th13/"
WRAPPER = "scripts/verification_lib/check_command.py"
DELIVERY = "scripts/verify_delivery.py"
FINAL = {"stage1-full-chain": "stage1", "full-game-coverage": "full-game"}
FORBIDDEN_NAMES = re.compile(r"golden|baseline|threshold|tolerance|policy|AGENTS|HANDOFF", re.I)
SHARED_CORE = {"Renderer", "Shaders", "GraphicsState", "RenderCommands", "LegacyGraphics", "ZunGraphics"}
RESERVED = {"scripts/verify.py", "scripts/task_configure.py", "scripts/test_task_configure.py",
            DELIVERY, "scripts/check_project_contract.py", "scripts/audit_baselines.py",
            "scripts/handoff_drill.py"}


def safe_path(root, name):
    relative(name)
    require(name == Path(name).as_posix() and name != "." and
            not any(c in name for c in "*?[]{}\n\r\x00"), f"unbounded/noncanonical scope: {name}")
    path = root / name
    require(path.resolve().is_relative_to(root), f"path escapes repository: {name}")
    require(not any(p.is_symlink() for p in [path, *path.parents] if p != root.parent),
            f"symlink scope: {name}")
    return path


def writable(root, name, protected, reviewed_tests=None):
    path = safe_path(root, name)
    require(not path.is_dir(), f"write scope must be a named file: {name}")
    require(not within(name, protected) and name not in RESERVED and
            not FORBIDDEN_NAMES.search(name), f"protected write scope: {name}")
    require(not name.startswith(("verification/", "scripts/verification_lib/")),
            f"verification write forbidden: {name}")
    parts = Path(name).parts
    require(not name.startswith(PORT + "portable/sdl/") and path.stem not in SHARED_CORE and
            not any(p.lower() in {".git", "shared", "renderer", "shaders"} for p in parts),
            f"shared backend write forbidden: {name}")
    is_test = (Path(name).name.startswith("test_") or ".test." in name or
               "/tests/" in name or "_test." in name)
    reviewed_test = is_test and path.exists() and name in (reviewed_tests or {})
    if is_test and path.exists():
        require(reviewed_test and hashlib.sha256(path.read_bytes()).hexdigest() == reviewed_tests[name],
                f"existing test needs independently reviewed additive controls: {name}")
    oracle = name.startswith("oracle/") and path.suffix == ".py" and len(parts) == 2
    require(not (oracle and "compare" in path.stem.lower()), f"comparison policy write forbidden: {name}")
    cpp = name.startswith((PORT + "web/cpp/game/", PORT + "web/cpp/rebuild/")) and path.suffix in {".cpp", ".hpp", ".h"}
    cpp = cpp or name in {PORT + "web/cpp/sdl/" + f for f in (
        "Application.cpp", "Application.hpp", "ApplicationState.hpp",
        "BrowserRuntime.cpp", "BrowserRuntime.hpp")}
    adapter = (name.startswith("scripts/") and len(parts) == 2 or
               name.startswith(PORT + "tests/")) and path.suffix in {".py", ".mjs", ".cpp", ".hpp"} and not path.exists()
    require(oracle or cpp or adapter or reviewed_test, f"not business oracle/port C++/named new adapter: {name}")


def argv_path(root, cwd, token):
    if token.startswith("/work/"):
        name = token[len("/work/"):]
    else:
        require(not token.startswith("/"), "command path outside /work")
        name = (Path(cwd) / token).as_posix()
    safe_path(root, name)
    return name


def check_command(root, card, check):
    argv = check["argv"]
    # Check every token, including wrapped commands. No shell interpretation is
    # used, and inline code/env launchers cannot disguise a nested command.
    forbidden = {"sh", "bash", "zsh", "dash", "fish", "podman", "docker", "env",
                 "sudo", "eval", "exec", "echo", "printf", "true", "test", "touch"}
    for i, arg in enumerate(argv):
        require(Path(arg).name not in forbidden, f"shell/static/nested command forbidden: {arg}")
        low = arg.lower()
        require(not low.startswith(("--ignore", "--skip", "--exclude", "--filter", "--update", "--accept", "--rebaseline"))
                and not FORBIDDEN_NAMES.search(arg), f"filter/baseline/policy command forbidden: {arg}")
        require(arg not in {"-c", "-e", "--eval", "--shell", "--check", "--help", "--version"},
                f"inline/static command forbidden: {arg}")
        if low.startswith("--ulp"):
            require(low == "--ulp" or low.startswith("--ulp="), "unknown ULP option")
            value = arg.partition("=")[2] if "=" in arg else argv[i + 1] if i + 1 < len(argv) else ""
            try:
                number = Decimal(value)
                require(number.is_finite() and number == 0, "nonzero ULP forbidden")
            except InvalidOperation as e:
                raise Blocked("invalid ULP") from e
    require(check["cwd"] == "." or (safe_path(root, check["cwd"]).is_dir()), "missing check cwd")
    require(not check["external"], "unimplemented external adapter cannot be configured")
    require(check["result_type"] in {"TOOL_PASS", "FIDELITY_PASS"}, "only real tool/fidelity gates allowed")
    wrapped = len(argv) > 1 and argv[0] == "python3" and argv_path(root, check["cwd"], argv[1]) == WRAPPER
    command = argv
    if wrapped:
        require(check["result_type"] == "TOOL_PASS" and check["window"] is None,
                "TOOL wrapper cannot issue FIDELITY")
        require(len(argv) > 4 and argv[2] == "--", "invalid TOOL wrapper")
        command = argv[3:]
    require(command[0] in {"python3", "node"}, "use explicit python3/node business gate")
    require(len(command) >= 2, "missing business check")
    if command[0] == "python3" and command[1:3] == ["-m", "unittest"]:
        require(wrapped and len(command) > 3 and check["cwd"] == ".",
                "unittest needs root cwd, TOOL wrapper and explicit tests")
        require("discover" not in command and all(re.fullmatch(r"(?:oracle|scripts)\.test_[A-Za-z0-9_]+", a)
                or a in {"-v", "-q"} for a in command[3:]), "tests must be named, not discovery/root scope")
        modules = [(a.replace(".", "/") + ".py") for a in command[3:] if not a.startswith("-")]
        require(modules, "at least one explicit test required")
        for name in modules:
            safe_path(root, name)
            require(within(name, card["read"]) and within(name, card["inputs"]),
                    f"test must be reviewed and identity-bound: {name}")
        return modules
    start = 2 if command[0] == "node" and command[1] == "--test" else 1
    require(len(command) > start, "missing script/test path")
    files = command[start:] if start == 2 else [command[start]]
    names = []
    for token in files:
        name = argv_path(root, check["cwd"], token)
        require(name not in RESERVED - {DELIVERY} and not name.startswith("scripts/verification_lib/"),
                "runner/configuration/static tools are not business gates")
        require(name.startswith(("oracle/", "scripts/", PORT + "tests/", PORT + "tools/", PORT + "portable/"))
                and Path(name).suffix in {".py", ".mjs"}, "check must execute a named business script, not a document")
        require(within(name, card["read"]) and within(name, card["inputs"]),
                f"business check must be reviewed and identity-bound in read/inputs: {name}")
        names.append(name)
    require(wrapped or start == 1, "raw test command requires structured TOOL wrapper")
    if check["result_type"] == "FIDELITY_PASS":
        require(not wrapped and start == 1 and not Path(names[0]).name.startswith("test_"),
                "FIDELITY must be a directly reviewed business gate")
    return names


def ignored_file(root, supplied):
    path = Path(supplied)
    path = path if path.is_absolute() else root / path
    name = path.relative_to(root).as_posix() if path.is_relative_to(root) else ""
    require(name, "proposal/review must be ignored files inside repository")
    path = safe_path(root, name)
    require(path.is_file() and path.stat().st_nlink == 1, "missing/aliased proposal/review")
    result = subprocess.run(["git", "-C", str(root), "check-ignore", "--no-index", "-q", "--", name],
                            capture_output=True, timeout=30)
    require(result.returncode == 0, "proposal/review must be gitignored")
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", name],
                             capture_output=True, timeout=30)
    require(tracked.returncode == 1, "proposal/review must not be tracked")
    return path


def configure(root, proposal_path, review_path, *, check_environment=True):
    root = Path(root).resolve()
    runner = Runner(root)
    if check_environment:
        runner.environment()
    with runner.lock():
        proposal_path = ignored_file(root, proposal_path)
        review_path = ignored_file(root, review_path)
        # configuration_delta reads these exact bytes through Runner.artifact,
        # whose evidence boundary is artifacts/verification. Reject paths the
        # long-lived audit chain could never revalidate.
        require(proposal_path.is_relative_to(runner.home) and review_path.is_relative_to(runner.home),
                "proposal/review must be under artifacts/verification")
        # Bind hashes to exactly the bytes decoded below, not a second file read.
        proposal_bytes, review_bytes = proposal_path.read_bytes(), review_path.read_bytes()
        proposal_hash = hashlib.sha256(proposal_bytes).hexdigest()
        review_hash = hashlib.sha256(review_bytes).hexdigest()
        proposal, review = load(proposal_path), load(review_path)
        require(proposal_path.read_bytes() == proposal_bytes and review_path.read_bytes() == review_bytes,
                "proposal/review changed while reading")
        proposal_fields = "schema task_id base_task_sha256 allowed read inputs checks goal evidence reason non_goals"
        review_fields = "schema task_id proposal_sha256 base_task_sha256 approved issues reviewer_role"
        for optional in ("owner_binding", "maintenance"):
            if optional in proposal:
                proposal_fields += " " + optional
        if "additive_tests" in proposal:
            proposal_fields += " additive_tests"
            review_fields += " test_change_policy"
        keys(proposal, proposal_fields, "proposal")
        keys(review, review_fields, "review")
        require(type(proposal["schema"]) is int and proposal["schema"] == 1 and
                type(review["schema"]) is int and review["schema"] == 1, "schema must be 1")
        require(isinstance(proposal["task_id"], str), "task_id must be named")
        adding = proposal.get("maintenance") is True
        if "maintenance" in proposal:
            require(adding and proposal["task_id"] not in runner.tasks, "maintenance adds only a new named card")
        if adding:
            old = dict(id=proposal["task_id"], goal=proposal["goal"], non_goals=proposal["non_goals"],
                       read=["AGENTS.md"], allowed=[], protected=list(dict.fromkeys(runner.policy["protected"] +
                       ["verification", "scripts/verify.py", "scripts/test_verify.py", "scripts/task_configure.py", "scripts/verify_delivery.py"])),
                       depends=[], inputs=["AGENTS.md"], checks=dict(fast=[], full=[]), evidence=["Independent reviewed maintenance receipts"],
                       stop="At most three rounds; independent mechanism review before a new hypothesis", max_rounds=3,
                       completion="TOOL_PASS", blocked_reason="unconfigured")
            state = Runner.empty_state()
        else:
            require(proposal["task_id"] in runner.tasks, "unknown existing task")
            old, state = runner.task(proposal["task_id"])
        require(state["status"] in {"PENDING", "READY", "BLOCKED"} and state["rounds"] == 0 and
                all(state[f] is None for f in ("baseline", "baseline_hash", "start_identity", "run", "receipt", "round_identity", "fast_receipt")),
                "task has started/history: use existing card range, never rebaseline")
        require(old["checks"] == {"fast": [], "full": []}, "only blocked empty-gate cards may be configured")
        binding = proposal.get("owner_binding")
        active = runner.maintenance_record()
        claims = [n for n, v in runner.state["tasks"].items() if v["baseline"] and v["status"] != "DONE"
                  and not (v["status"] == "STALE" and v["receipt"])]
        if binding is not None:
            keys(binding, "id baseline_sha256 card_sha256", "configuration owner binding")
            owner, owner_state = runner.task(binding["id"])
            require(owner_state["baseline"] and owner_state["status"] not in {"DONE", "VERIFYING"}
                    and binding["baseline_sha256"] == owner_state["baseline_hash"]
                    and binding["card_sha256"] == digest(owner), "old/foreign configuration owner")
            require(proposal["task_id"] != binding["id"], "cannot configure current owner")
            require(all(n == binding["id"] or active and active["phase"] == "ACTIVE" and
                        active["owner"] == binding["id"] and n == active["maintenance"] for n in claims),
                    "configuration belongs to another owner")
            runner.check_boundary(active["maintenance"] if active and active["phase"] == "ACTIVE" else binding["id"])
        else:
            require(not claims and not adding, "active owner/new maintenance requires owner_binding")
        require(proposal["base_task_sha256"] == (digest(None) if adding else digest(old)) == review["base_task_sha256"], "base card hash mismatch")
        require(review["task_id"] == old["id"] and review["proposal_sha256"] == proposal_hash, "proposal/review hash or task mismatch")
        require(review["approved"] is True and review["issues"] == [] and isinstance(review["issues"], list)
                and review["reviewer_role"] == "independent", "independent review rejected/has issues")
        require(isinstance(proposal["reason"], str) and proposal["reason"].strip(), "proposal needs reason")
        require(proposal["goal"] == old["goal"], "original acceptance goal must not change")
        texts(proposal["non_goals"], "non_goals")
        require(proposal["non_goals"][:len(old["non_goals"])] == old["non_goals"], "original non_goals must be retained")
        new = copy.deepcopy(old)
        for field in ("allowed", "read", "inputs", "evidence"):
            texts(proposal[field], field, empty=field == "allowed")
            if field in {"read", "inputs", "allowed"}:
                for name in proposal[field]:
                    safe_path(root, name)
            new[field] = list(dict.fromkeys(old[field] + proposal[field]))
        additive = proposal.get("additive_tests", {})
        require(isinstance(additive, dict), "additive_tests must bind named prior test hashes")
        if "additive_tests" in proposal:
            require(bool(additive) and review["test_change_policy"] == "additive-controls-only; no acceptance weakening",
                    "independent additive-test policy required")
        for name, expected in additive.items():
            require(name in new["allowed"] and within(name, new["read"]) and within(name, new["inputs"])
                    and isinstance(expected,str) and len(expected)==64, "additive test must be owned/read/identity-bound")
        for name in new["allowed"]:
            writable(root, name, old["protected"] + runner.policy["protected"], additive)
        new["non_goals"] = proposal["non_goals"]
        new["checks"] = proposal["checks"]
        new["blocked_reason"] = ""
        Runner.validate_task(new)
        require(all(new["checks"].values()), "both fast and full gates required")
        if adding:
            require(all(c["result_type"] == "TOOL_PASS" and c["window"] is None for checks in new["checks"].values() for c in checks),
                    "maintenance gates are TOOL_PASS, not replacement fidelity")
        else:
            require(any(c["result_type"] == old["completion"] for c in new["checks"]["full"]), "full gate cannot weaken completion")
        sources = set()
        for checks in new["checks"].values():
            for check in checks:
                sources.update(check_command(root, new, check))
        if old["id"] in FINAL:
            exact = ["python3", DELIVERY, "--scope", FINAL[old["id"]]]
            require(any(c["argv"] == exact and c["cwd"] == "." and c["result_type"] == "FIDELITY_PASS"
                        for c in new["checks"]["full"]), "mandatory final verify_delivery gate missing")
        target = root / "verification/tasks.json"
        safe_path(root, "verification/tasks.json")
        graph = load(target)
        require({t["id"]: t for t in graph["tasks"]} == runner.tasks, "task graph changed; retry")
        candidate = copy.deepcopy(graph)
        if adding:
            require(new["allowed"], "maintenance needs named business files")
            candidate["tasks"].append(new)
        else:
            candidate["tasks"][next(i for i, t in enumerate(graph["tasks"]) if t["id"] == old["id"])] = new
        # Runner.validate_task is authoritative; replacing only this existing card
        # preserves the DAG already checked by Runner.__init__ (depends immutable).
        for card in candidate["tasks"]:
            Runner.validate_task(card)
        graph_before_record = runner.file_record("verification/tasks.json")
        candidate_bytes = (json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        graph_after_record = dict(sha256=hashlib.sha256(candidate_bytes).hexdigest(), mode=0o644)
        record = dict(schema=1, task_id=old["id"], old_card=old, new_card=new,
                      owner_binding=binding, proposal_path=str(proposal_path.relative_to(root)), review_path=str(review_path.relative_to(root)),
                      graph_before_bytes_hex=target.read_bytes().hex(), graph_before=graph, graph_after=candidate,
                      graph_before_record=graph_before_record, graph_after_record=graph_after_record,
                      old_card_sha256=digest(old), new_card_sha256=digest(new),
                      proposal_sha256=proposal_hash, review_sha256=review_hash,
                      proposal=proposal, review=review, reason=proposal["reason"],
                      check_sources={n: hashlib.sha256((root / n).read_bytes()).hexdigest()
                                     if (root / n).is_file() else None for n in sorted(sources)},
                      warning="Review role is evidence, not authentication. No checks executed; no fidelity certified.")
        audit = runner.home / "task-configure" / (uuid.uuid4().hex + ".json")
        safe_path(root, audit.relative_to(root).as_posix())
        # Durable intent first: a crash may leave an unapplied audit, never an
        # unaudited configuration. Old/new hashes identify whether it applied.
        atomic(audit, record)
        require(load(target) == graph, "task graph changed before save; audit is unapplied")
        atomic(target, candidate)
        return dict(type="TOOL_PASS", task_id=old["id"], audit=str(audit.relative_to(root)),
                    card_sha256=digest(new), warning=record["warning"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proposal", required=True)
    parser.add_argument("--review", required=True)
    args = parser.parse_args(argv)
    try:
        result = configure(ROOT, args.proposal, args.review)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (Blocked, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as e:
        print(json.dumps(dict(type="BLOCKED", reason=str(e)), ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
