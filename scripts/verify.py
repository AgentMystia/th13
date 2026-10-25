#!/usr/bin/env python3
"""Container-only, fail-closed task runner. No shell, no business gate substitutions.

State and evidence are disposable records under artifacts/verification. DONE is
revalidated from a closed full run, never accepted merely because state says so.
This is an engineering guardrail, not isolation from a malicious repository writer.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

STATES = {"PENDING", "READY", "ACTIVE", "VERIFYING", "DONE", "FAILED", "BLOCKED", "STALE"}
TYPES = {"TOOL_PASS", "BASELINE_HELD", "FIDELITY_PASS", "BLOCKED", "FAIL"}
ROOT = Path(__file__).resolve().parents[1]
RECONCILE_OWNER = "replay-qualification-certification"
RECONCILE_MAINTENANCE = "autonomy-tooling"
RECONCILE_PATHS = tuple(
    "eagler-touhou/ports/th13/web/cpp/game/" + n for n in (
        "AnmRenderer.cpp", "AsciiText.cpp", "AsciiText.hpp", "EclContext.hpp",
        "GameHud.cpp", "GameHud.hpp", "GameHudDraw.cpp", "RunScene.cpp",
        "RunScene.hpp", "SessionState.hpp", "StageBattle.cpp", "StageBattle.hpp",
        "StageCommands.cpp", "StageSim.cpp", "StageSim.hpp", "TitleMainMenu.cpp",
        "TitleMainMenu.hpp", "TitleSelectionMenu.cpp", "TitleSelectionMenu.hpp")) + tuple(
    "eagler-touhou/ports/th13/web/cpp/sdl/" + n for n in (
        "Application.cpp", "ApplicationGame.cpp", "ApplicationState.hpp",
        "ApplicationTitle.cpp")) + ("tmp/badge114_probe", "tmp/badge114_probe.cpp")


class Blocked(Exception):
    pass


def require(test, reason):
    if not test:
        raise Blocked(reason)


def keys(value, expected, where):
    require(isinstance(value, dict) and set(value) == set(expected.split()),
            f"{where}: unknown or missing fields")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path):
    def pairs(items):
        d = {}
        for k, v in items:
            require(k not in d, f"duplicate JSON key: {k}")
            d[k] = v
        return d
    try:
        return json.loads(Path(path).read_text(), object_pairs_hook=pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(Blocked(f"invalid number {x}")))
    except (OSError, ValueError) as e:
        raise Blocked(f"cannot read JSON {path}: {e}") from e


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x") as f:
            json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temp.unlink(missing_ok=True)


def relative(value):
    require(isinstance(value, str) and value and not value.startswith("/")
            and ".." not in Path(value).parts and "\\" not in value,
            f"unsafe relative path: {value!r}")
    return value.rstrip("/")


def within(path, scopes):
    return any(path == s or path.startswith(s.rstrip("/") + "/") for s in scopes)


def texts(value, where, empty=False):
    require(isinstance(value, list) and (empty or bool(value))
            and all(isinstance(x, str) and x for x in value), f"{where}: expected string list")
    require(len(set(value)) == len(value), f"{where}: duplicate entries")


class Runner:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.home = self.root / "artifacts/verification"
        self.home.mkdir(parents=True, exist_ok=True)
        self.policy = load(self.root / "verification/policy.json")
        keys(self.policy, "schema environment result_types protected", "policy")
        require(type(self.policy["schema"]) is int and self.policy["schema"] == 1, "policy schema")
        keys(self.policy["environment"], "root home container_marker required_paths", "environment")
        texts(self.policy["result_types"], "result_types")
        require(set(self.policy["result_types"]) == TYPES, "result types must not be weakened")
        texts(self.policy["protected"], "protected")
        for p in self.policy["protected"]:
            relative(p)
        env = self.policy["environment"]
        require(env["root"] == "/work" and env["home"] == "/home/dev"
                and env["container_marker"] == "/run/.containerenv", "unsupported container policy")
        texts(env["required_paths"], "required_paths")
        data = load(self.root / "verification/tasks.json")
        keys(data, "schema tasks", "tasks")
        require(type(data["schema"]) is int and data["schema"] == 1 and isinstance(data["tasks"], list)
                and data["tasks"], "task schema or empty graph")
        self.tasks = {}
        for task in data["tasks"]:
            self.validate_task(task)
            require(task["id"] not in self.tasks, "duplicate task id")
            self.tasks[task["id"]] = task
        for t in self.tasks.values():
            require(all(d in self.tasks and d != t["id"] for d in t["depends"]), "unknown/self dependency")
        def visit(name, stack):
            require(name not in stack, "dependency cycle")
            for dep in self.tasks[name]["depends"]:
                visit(dep, stack + [name])
        for name in self.tasks:
            visit(name, [])
        self.state_path = self.home / "state.json"
        require(not self.state_path.is_symlink(), "symlink state")
        self.state = load(self.state_path) if self.state_path.exists() else {"schema": 1, "tasks": {}}
        keys(self.state, "schema tasks", "state")
        require(type(self.state["schema"]) is int and self.state["schema"] == 1
                and isinstance(self.state["tasks"], dict), "invalid state")
        require(set(self.state["tasks"]) <= set(self.tasks), "state contains unknown task")
        for name in self.tasks:
            self.state["tasks"].setdefault(name, self.empty_state())
            s = self.state["tasks"][name]
            keys(s, "status reason rounds baseline baseline_hash start_identity run receipt round_identity fast_receipt", "task state")
            require(s["status"] in STATES and type(s["rounds"]) is int and 0 <= s["rounds"] <= self.tasks[name]["max_rounds"]
                    and isinstance(s["reason"], str), "invalid task state")
            for field in ("baseline", "baseline_hash", "start_identity", "run", "receipt", "round_identity", "fast_receipt"):
                require(s[field] is None or isinstance(s[field], str) and bool(s[field]), "invalid state reference")
            require((s["baseline"] is None) == (s["baseline_hash"] is None), "incomplete baseline state")
            require(s["status"] not in {"ACTIVE", "VERIFYING", "DONE"} or s["baseline"] is not None,
                    "active/completed state has no baseline")

    @staticmethod
    def empty_state():
        return dict(status="PENDING", reason="not started", rounds=0, baseline=None,
                    baseline_hash=None, start_identity=None, run=None, receipt=None,
                    round_identity=None, fast_receipt=None)

    @staticmethod
    def validate_task(t):
        keys(t, "id goal non_goals read allowed protected depends inputs checks evidence stop max_rounds completion blocked_reason", "task")
        require(isinstance(t["id"], str) and t["id"] and all(c.isalnum() or c == "-" for c in t["id"]), "invalid id")
        for field in ("goal", "stop"):
            require(isinstance(t[field], str) and t[field], f"empty {field}")
        for field in ("non_goals", "read", "allowed", "protected", "depends", "inputs", "evidence"):
            texts(t[field], field, empty=field in {"depends", "allowed"})
        for field in ("read", "allowed", "protected", "inputs"):
            for p in t[field]:
                relative(p)
        require(type(t["max_rounds"]) is int and 1 <= t["max_rounds"] <= 3, "max_rounds must be 1..3")
        require(t["completion"] in {"TOOL_PASS", "FIDELITY_PASS"}, "invalid completion class")
        require(isinstance(t["blocked_reason"], str), "invalid blocked_reason")
        keys(t["checks"], "fast full", "checks")
        for tier, checks in t["checks"].items():
            require(isinstance(checks, list), "checks must be lists")
            names = set()
            for c in checks:
                keys(c, "id argv cwd timeout result_type window external", "check")
                require(isinstance(c["id"], str) and c["id"] and all(x.isalnum() or x == "-" for x in c["id"])
                        and c["id"] not in names, "invalid/duplicate check id")
                names.add(c["id"])
                require(isinstance(c["argv"], list) and c["argv"] and all(isinstance(a, str) and a for a in c["argv"]), "invalid argv")
                require(not any(x in {"podman", "docker", "sh", "bash", "zsh"} for x in [Path(c["argv"][0]).name])
                        and not any(x.startswith("--ignore") for x in c["argv"]), "shell/nested container/filtered gate forbidden")
                relative(c["cwd"])
                require(type(c["timeout"]) is int and 0 < c["timeout"] <= 86400, "invalid timeout")
                require(c["result_type"] in TYPES and type(c["external"]) is bool, "invalid check class")
                if c["window"] is not None:
                    w = c["window"]
                    keys(w, "start end roles", "window")
                    require(type(w["start"]) is int and type(w["end"]) is int and 0 <= w["start"] <= w["end"], "invalid window")
                    texts(w["roles"], "roles")
                    require(len(w["roles"]) >= 2, "need independent captures")
                require(c["result_type"] != "FIDELITY_PASS" or c["window"] is not None,
                        "fidelity requires exact capture window, not document presence")

    def environment(self):
        e = self.policy["environment"]
        require(self.root == Path(e["root"]) and Path.cwd().resolve().is_relative_to(self.root)
                and os.environ.get("HOME") == e["home"]
                and Path(e["container_marker"]).is_file(),
                "container required: podman run --rm --userns=keep-id -e HOME=/home/dev "
                "-v /home/mystia/项目/th13:/work:Z -w /work localhost/th13-dev python3 scripts/verify.py <command>")
        for p in e["required_paths"]:
            require(Path(p).exists(), f"missing th13-dev toolchain path: {p}")

    @contextlib.contextmanager
    def lock(self):
        path = self.home / "runner.lock"
        require(not path.is_symlink(), "symlink lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise Blocked("runner locked by another process; do not delete the lock file") from e
            # Reload only while holding the lock: no stale read/modify/write race.
            if self.state_path.exists():
                fresh = Runner(self.root)
                require(self.policy == fresh.policy and self.tasks == fresh.tasks, "configuration changed while acquiring lock; retry")
                self.state = fresh.state
            yield
        finally:
            os.close(fd)

    def save(self):
        atomic(self.state_path, self.state)

    def path(self, name):
        relative(name)
        p = self.root / name
        require(p.resolve().is_relative_to(self.root), f"path escapes workspace: {name}")
        return p

    def artifact(self, name):
        p = self.path(name)
        require(p.resolve().is_relative_to(self.home.resolve()), "not a verification artifact")
        require(not p.is_symlink(), "symlink evidence")
        return p

    def inventory(self):
        """Tracked + nonignored files in both repos; ignored inputs added by scopes.

        Do not run git status's text parser: NUL names preserve all valid paths.
        No git reset, clean, index writes or user work cleanup is performed.
        """
        result = set()
        repos = [self.root]
        inner = self.root / "eagler-touhou"
        if (inner / ".git").exists():
            repos.append(inner)
        for repo in repos:
            p = subprocess.run(["git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                               capture_output=True, timeout=30)
            require(p.returncode == 0, "git inventory failed")
            for raw in p.stdout.split(b"\0"):
                if raw:
                    name = (repo / os.fsdecode(raw)).relative_to(self.root).as_posix()
                    if not within(name, ["artifacts", ".git"]):
                        result.add(name)
        return result

    def file_record(self, name):
        p = self.root / name
        if p.is_symlink():
            return {"symlink": os.readlink(p)}
        if not p.exists():
            return {"missing": True}
        if p.is_dir():
            return {"directory": True}
        info = p.stat()
        require(stat.S_ISREG(info.st_mode), f"not regular file: {name}")
        return {"sha256": sha(p), "mode": stat.S_IMODE(info.st_mode)}

    def snapshot(self):
        return {n: self.file_record(n) for n in sorted(self.inventory())}

    @staticmethod
    def game_runtime(name):
        parts = Path(name).parts
        if not parts or parts[0] != "games":
            return False
        runtime = {"wineprefix", "wine-prefix", "prefix", "drive_c", "dosdevices",
                   "users", "credentials", "secrets", "keyrings", "cookies",
                   "browser", "chromium", "firefox", "runtime", "profile", "profiles", "credential", "keyring"}
        return any(p.startswith(".") or p.casefold() in runtime
                   or p.casefold().endswith(".reg") for p in parts[1:])


    def scoped(self, scopes):
        # Only original game/assets are inputs under games. Never descend into
        # a user's Wine/runtime/profile or hash registry/credential files.
        asset_suffixes = {".exe", ".dat", ".rpy", ".anm", ".ecl", ".std", ".msg",
                          ".wav", ".ogg", ".flac", ".mp3", ".mid", ".png", ".bmp", ".jpg"}
        names = set()
        for scope in scopes:
            if self.game_runtime(scope):
                continue
            p = self.path(scope)
            game = Path(scope).parts[0] == "games"
            if game and not p.is_dir() and p.suffix.casefold() not in asset_suffixes:
                continue
            names.add(scope)
            if p.is_dir():
                for base, dirs, files in os.walk(p, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__", ".cache"}
                                     and not self.game_runtime((Path(base) / d).relative_to(self.root).as_posix()))
                    for f in files:
                        name = (Path(base) / f).relative_to(self.root).as_posix()
                        if not self.game_runtime(name) and (not game or Path(f).suffix.casefold() in asset_suffixes):
                            names.add(name)
        return {n: self.file_record(n) for n in sorted(names)}

    def identity(self, name):
        t = self.tasks[name]
        current = load(self.root / "verification/tasks.json")
        require(next((v for v in current["tasks"] if v["id"] == name), None) == t,
                "current task configuration changed; reload runner")
        tools = {}
        for tier in t["checks"].values():
            for c in tier:
                executable = shutil.which(c["argv"][0])
                require(executable, f"missing executable {c['argv'][0]}")
                tools[executable] = sha(executable)
        global_scopes = ["scripts/task_configure.py", "scripts/verify_delivery.py", "verification/full-game-scenes.json", "scripts/verify.py", "scripts/verification_lib", "verification/policy.json", "verification/schemas"]
        files = self.scoped(t["inputs"] + t["allowed"] + global_scopes)
        # The current card is hashed as structured data below. Unrelated task
        # configuration must not invalidate this task or its already closed runs.
        files.pop("verification/tasks.json", None)
        return digest(dict(task=t, policy=self.policy, files=files, tools=tools, python=sys.version,
                           dependencies={d: self.identity(d) for d in t["depends"]}))

    def set_status(self, name, status, reason):
        self.state["tasks"][name].update(status=status, reason=reason)

    def refresh(self):
        visited = set()
        def update(name):
            if name in visited:
                return
            visited.add(name)
            t, s = self.tasks[name], self.state["tasks"][name]
            for d in t["depends"]:
                update(d)
            deps = all(self.state["tasks"][d]["status"] == "DONE" for d in t["depends"])
            if s["status"] == "VERIFYING":
                self.set_status(name, "BLOCKED", "interrupted run; evidence is not closed; resume same revision without rebaseline")
            elif s["status"] == "DONE":
                try:
                    require(deps, "dependency no longer certified")
                    self.validate_full(name, s["receipt"])
                except (Blocked, OSError, TypeError, KeyError) as e:
                    self.set_status(name, "STALE", str(e))
            elif s["status"] in {"PENDING", "READY"} or s["status"] == "BLOCKED" and s["baseline"] is None:
                configured = not t["blocked_reason"] and all(t["checks"].values())
                status = "READY" if deps and configured else "BLOCKED" if deps else "PENDING"
                self.set_status(name, status, "dependencies certified" if status == "READY" else
                                (t["blocked_reason"] or "empty/unconfigured checks") if deps else "dependencies not certified")
            elif not deps and s["status"] == "ACTIVE":
                self.set_status(name, "STALE", "dependency no longer certified")
        for n in self.tasks:
            update(n)
        self.save()

    def task(self, name):
        require(name in self.tasks, f"unknown task: {name}")
        return self.tasks[name], self.state["tasks"][name]

    def start(self, name):
        self.guard_maintenance(name)
        t, s = self.task(name)
        require(all(self.state["tasks"][d]["status"] == "DONE" for d in t["depends"]), "dependencies not DONE")
        require(s["status"] in {"READY", "STALE", "FAILED", "BLOCKED"}, "task cannot start in this state")
        require(not t["blocked_reason"] and all(t["checks"].values()), t["blocked_reason"] or "empty/unconfigured checks")
        require(s["rounds"] < t["max_rounds"], "three-round budget exhausted; mechanism review required")
        if s["baseline"] is not None:
            self.check_boundary(name)
            self.set_status(name, "ACTIVE", "existing baseline retained; resume without rebaseline")
            self.save()
            return s
        for other, state in self.state["tasks"].items():
            require(other == name or not (state["baseline"] and state["status"] != "DONE" and not (state["status"] == "STALE" and state["receipt"])),
                    f"workspace already claimed by {other}; global claim prevents unowned cross-task changes")
        baseline = self.snapshot()
        # Include protected ignored paths and explicitly scoped inputs in the content baseline.
        baseline.update(self.scoped(t["protected"] + self.policy["protected"] + t["inputs"]))
        p = self.home / "baselines" / (name + "-" + uuid.uuid4().hex + ".json")
        atomic(p, baseline)
        s.update(status="ACTIVE", reason="baseline captured; existing dirty work retained", baseline=str(p.relative_to(self.root)),
                 baseline_hash=sha(p), start_identity=self.identity(name), run=None, receipt=None)
        self.save()
        return s

    def mechanism_review(self, name, review_path):
        """Start another bounded revision after a bound independent review.

        Independence is an explicit reviewed assertion, not an authentication
        boundary against a writer with the same repository permissions.
        """
        self.guard_maintenance(name)
        t, s = self.task(name)
        require(s["baseline"] and s["rounds"] == t["max_rounds"]
                and s["status"] not in {"VERIFYING", "DONE"}, "mechanism review requires an exhausted unfinished owner")
        require(all(other == name or not (v["baseline"] and v["status"] != "DONE" and not (v["status"] == "STALE" and v["receipt"]))
                    for other, v in self.state["tasks"].items()), "mechanism review requires the unique workspace owner")
        require(not t["blocked_reason"] and all(t["checks"].values()), "mechanism review requires configured gates")
        self.check_boundary(name)
        current_identity = self.identity(name)
        p = self.path(review_path)
        require(within(review_path, ["artifacts"]) and not p.is_symlink()
                and p.is_file() and p.stat().st_nlink == 1, "review must be an unaliased ignored artifact")
        ignored = subprocess.run(["git", "-C", str(self.root), "check-ignore", "--quiet", "--", review_path],
                                 capture_output=True, timeout=30)
        require(ignored.returncode == 0, "review path must be gitignored")
        review = load(p)
        keys(review, "schema task_id card_sha256 baseline_sha256 last_run_id new_hypothesis experiment independent approved issues", "mechanism review")
        require(type(review["schema"]) is int and review["schema"] == 1 and review["task_id"] == name
                and review["card_sha256"] == digest(t) and review["baseline_sha256"] == s["baseline_hash"]
                and review["last_run_id"] == s["run"], "review task/card/baseline/last-run mismatch")
        require(review["independent"] is True and review["approved"] is True
                and isinstance(review["issues"], list) and review["issues"] == [], "independent approval without unresolved issues required")
        for field in ("new_hypothesis", "experiment"):
            require(isinstance(review[field], str) and review[field].strip(), f"empty review {field}")
        run_id = s["run"]
        require(isinstance(run_id, str) and run_id and "/" not in run_id and ".." not in run_id, "missing last failed run")
        run_path = self.artifact(f"artifacts/verification/{run_id}/run.json")
        run = load(run_path)
        require(run["id"] == run_id and run["task"] == name and run["schema"] == 1
                and ((run["status"] == "CLOSED" and run["type"] in {"FAIL", "BLOCKED"})
                     or (run["status"] == "VERIFYING" and run["type"] == "BLOCKED"
                         and s["status"] == "BLOCKED"))
                and run["baseline_hash"] == s["baseline_hash"], "review must bind last failed or explicitly interrupted run")
        revisions = self.home / "revisions"
        hypothesis = " ".join(review["new_hypothesis"].split()).casefold()
        for previous in sorted(revisions.glob("*.json")):
            record = load(previous)
            if record["task_id"] == name:
                require(record["hypothesis_key"] != hypothesis, "mechanism review must propose a new hypothesis")
        revision_id = name + "-" + uuid.uuid4().hex
        record = dict(schema=1, revision_id=revision_id, task_id=name, previous=dict(s),
                      card_sha256=digest(t), baseline_sha256=s["baseline_hash"],
                      last_run_id=run_id, last_run_sha256=sha(run_path),
                      interruption_evidence=(dict(status="INTERRUPTED", original_status=run["status"],
                                                  reason=s["reason"]) if run["status"] == "VERIFYING" else None),
                      review_path=review_path, review_sha256=sha(p), review=review,
                      hypothesis_key=hypothesis, identity=current_identity)
        atomic(revisions / (revision_id + ".json"), record)
        s.update(status="ACTIVE", reason=f"mechanism review approved revision {revision_id}; original baseline retained; new fast/full required",
                 rounds=0, round_identity=None, fast_receipt=None, receipt=None)
        self.save()
        return dict(status="ACTIVE", task_id=name, revision_id=revision_id,
                    artifact=str((revisions / (revision_id + ".json")).relative_to(self.root)),
                    reason=s["reason"])


    def reopen(self, name):
        """User-authorized maintenance of a formerly DONE, now STALE task.

        Never use this to clear an active/failed owner's scope violation. Old
        evidence remains archived; the new baseline itself is not a pass.
        """
        self.guard_maintenance(name)
        t, s = self.task(name)
        require(s["status"] == "STALE" and s["receipt"], "reopen requires a formerly DONE stale task")
        require(not t["blocked_reason"] and all(t["checks"].values()), "reopen requires configured checks")
        require(s["rounds"] < t["max_rounds"], "round budget exhausted; mechanism review required")
        require(all(other == name or not (v["baseline"] and v["status"] != "DONE")
                    for other, v in self.state["tasks"].items()), "unfinished workspace claim; finish its owner before reopen")
        def archived_receipt(run_id, tier):
            require(isinstance(run_id, str) and run_id and "/" not in run_id and ".." not in run_id,
                    "invalid archived run id")
            p = self.artifact(f"artifacts/verification/{run_id}/run.json")
            run = load(p)
            require(run["id"] == run_id and run["task"] == name and run["tier"] == tier
                    and run["status"] == "CLOSED" and run["type"] == (t["completion"] if tier == "full" else "TOOL_PASS")
                    and run["baseline_hash"] == s["baseline_hash"] and run["checks"], "not a former successful run")
            for r in run["checks"]:
                result = self.artifact(r["result"])
                require(result == p.parent / (r["id"] + ".result.json") and r["exit_code"] == 0
                        and sha(result) == r["result_sha256"], "archived process/result changed")
                record = load(result)
                require(record["run_id"] == run_id and record["check_id"] == r["id"]
                        and record["identity"] == run["identity"] and record["passed"] is True
                        and record["ignored"] == [] and record["type"] in {"TOOL_PASS", "BASELINE_HELD", "FIDELITY_PASS"},
                        "archived result not successful")
                for stream in ("stdout", "stderr"):
                    require(sha(p.parent / (r["id"] + f".{stream}.log")) == r[stream + "_sha256"],
                            "archived log changed")
            return run
        require(sha(self.artifact(s["baseline"])) == s["baseline_hash"], "archived baseline changed")
        old = archived_receipt(s["receipt"], "full")
        archived_receipt(old["fast_receipt"], "fast")
        archive = self.home / "reopens" / (name + "-" + uuid.uuid4().hex + ".json")
        atomic(archive, dict(schema=1, task=name, previous=dict(s), current_identity=self.identity(name)))
        baseline = self.snapshot()
        baseline.update(self.scoped(t["protected"] + self.policy["protected"] + t["inputs"]))
        p = self.home / "baselines" / (name + "-" + uuid.uuid4().hex + ".json")
        atomic(p, baseline)
        s.update(status="ACTIVE", reason=f"authorized stale maintenance reopened; archived {archive.relative_to(self.root)}; fast/full required",
                 baseline=str(p.relative_to(self.root)), baseline_hash=sha(p), start_identity=self.identity(name),
                 run=None, receipt=None, round_identity=None, fast_receipt=None)
        self.save()
        return s


    def maintenance_record(self):
        path = self.home / "maintenance-active.json"
        if not path.exists():
            return None
        require(not path.is_symlink(), "symlink maintenance record")
        record = load(path)
        require(record.get("schema") == 1 and record.get("phase") in {"ACTIVE", "RETURNED"},
                "invalid maintenance record")
        return record

    def guard_maintenance(self, name):
        record = self.maintenance_record()
        require(not record or record["phase"] != "ACTIVE" or name == record["maintenance"],
                "owner suspended for named maintenance; return maintenance before running other tasks")

    def maintenance_workspace(self, owner=None):
        # Match the owner's original boundary, not every future card's broad
        # inputs. No asset discovery or runtime/profile reading for tool repair.
        if owner is None:
            record = self.maintenance_record()
            candidates = [n for n, state in self.state["tasks"].items()
                          if state["baseline"] and state["status"] != "DONE" and not state["receipt"]]
            owner = record["owner"] if record and record["phase"] == "ACTIVE" else (candidates[0] if len(candidates) == 1 else None)
        require(owner in self.tasks, "explicit maintenance owner required")
        task = self.tasks[owner]
        files = self.snapshot()
        scopes = task["inputs"] + task["protected"] + self.policy["protected"]
        files.update(self.scoped([n for n in scopes if not within(n, ["games"])]))
        return files

    def maintenance_review(self, review_path, phase, owner, maintenance, files, workspace):
        p = self.artifact(review_path)
        require(p.is_file() and p.stat().st_nlink == 1, "missing/aliased maintenance review")
        ignored = subprocess.run(["git", "-C", str(self.root), "check-ignore", "--quiet", "--", review_path],
                                 capture_output=True, timeout=30)
        require(ignored.returncode == 0, "maintenance review must be ignored")
        review = load(p)
        fields = "schema phase owner maintenance owner_card_sha256 maintenance_card_sha256 owner_state_sha256 workspace_sha256 files approved reviewer_role issues"
        if "preauthorized_changes" in review:
            fields += " preauthorized_changes"
        keys(review, fields, "maintenance review")
        require(type(review["schema"]) is int and review["schema"] == 1
                and review["phase"] == phase and review["owner"] == owner and review["maintenance"] == maintenance
                and review["owner_card_sha256"] == digest(self.tasks[owner])
                and review["maintenance_card_sha256"] == digest(self.tasks[maintenance])
                and review["owner_state_sha256"] == digest(self.state["tasks"][owner])
                and review["workspace_sha256"] == digest(workspace) and review["files"] == files,
                "maintenance review state/card/workspace/files mismatch")
        require(review["approved"] is True and review["reviewer_role"] == "independent"
                and isinstance(review["issues"], list) and review["issues"] == [],
                "independent maintenance approval required")
        return dict(path=review_path, sha256=sha(p), review=review)

    def maintenance_start(self, name, owner, review_path):
        t, s = self.task(name)
        ot, os_ = self.task(owner)
        self.guard_maintenance(name)
        require(not self.maintenance_record() or self.maintenance_record()["phase"] == "RETURNED",
                "maintenance already active")
        require(name != owner and not t["depends"] and not t["blocked_reason"]
                and all(t["checks"].values()) and t["completion"] == "TOOL_PASS",
                "maintenance must be an independent configured TOOL_PASS card")
        require(s["baseline"] is None and s["rounds"] == 0 and s["status"] in {"PENDING", "READY", "BLOCKED"},
                "maintenance card already started")
        require(os_["baseline"] and os_["status"] not in {"VERIFYING", "DONE"},
                "maintenance requires an unfinished non-running owner")
        require(all(other in {name, owner} or not v["baseline"] or v["status"] == "DONE" or v["receipt"]
                    for other, v in self.state["tasks"].items()), "another unfinished workspace owner")
        files = list(t["allowed"])
        texts(files, "maintenance files")
        for file in files:
            relative(file)
            require(not self.path(file).is_dir() and not within(file, self.policy["protected"])
                    and not within(file, ["games", "verification/tasks.json"])
                    and not any(c in file for c in "*?[]{}"), "maintenance needs explicit unprotected files")
        baseline = self.artifact(os_["baseline"])
        require(sha(baseline) == os_["baseline_hash"], "owner baseline changed")
        before = load(baseline)
        workspace = self.maintenance_workspace(owner)
        review = self.maintenance_review(review_path, "start", owner, name, files, workspace)
        migration = review["review"].get("preauthorized_changes", {})
        require(isinstance(migration, dict) and set(migration) <= {"AGENTS.md", "verification/tasks.json"},
                "preauthorized migration is limited to this bootstrap AGENTS/task-graph transition")
        for file, delta in migration.items():
            keys(delta, "before after", "preauthorized migration delta")
            require(delta["before"] == before.get(file) and delta["after"] == workspace.get(file)
                    and delta["before"] != delta["after"], "preauthorized migration before/after mismatch")
        changed = [n for n in before.keys() | workspace.keys()
                   if not within(n, ["games"]) and not self.game_runtime(n) and before.get(n) != workspace.get(n)]
        changed = self.reviewed_boundary_changes(changed, before, workspace)
        credits = self.reconciliation_credit(owner, os_["baseline_hash"])
        credits.update(self.maintenance_deltas(owner, os_["baseline_hash"]))
        changed = [n for n in changed if n not in credits or workspace.get(n) != credits[n]]
        if "verification/tasks.json" in changed and "verification/tasks.json" not in migration:
            anchor = self.maintenance_deltas(owner, os_["baseline_hash"]).get("verification/tasks.json", before.get("verification/tasks.json"))
            self.configuration_delta(owner, anchor, workspace.get("verification/tasks.json"))
            changed.remove("verification/tasks.json")
        # Existing owner deltas remain its responsibility. Reviewed maintenance
        # files alone can cross its old scope; unrelated/protected edits cannot.
        forbidden = [n for n in changed if n not in files and n not in migration and
                     (not within(n, ot["allowed"]) or within(n, ot["protected"] + self.policy["protected"]))]
        require(not forbidden, "out-of-scope before maintenance: " + ", ".join(sorted(forbidden)[:30]))
        record = dict(schema=1, phase="ACTIVE", owner=owner, maintenance=name, files=files,
                      previous_owner=dict(os_), before=workspace, owner_baseline_hash=os_["baseline_hash"],
                      owner_card_sha256=digest(ot), maintenance_card_sha256=digest(t), start_review=review, migration=migration)
        archive = self.home / "maintenance" / (name + "-" + uuid.uuid4().hex + ".json")
        record["archive"] = str(archive.relative_to(self.root))
        atomic(archive, record)
        atomic(self.home / "maintenance-active.json", record)
        p = self.home / "baselines" / (name + "-" + uuid.uuid4().hex + ".json")
        atomic(p, workspace)
        s.update(status="ACTIVE", reason=f"reviewed maintenance of suspended {owner}", baseline=str(p.relative_to(self.root)),
                 baseline_hash=sha(p), start_identity=self.identity(name))
        os_.update(status="BLOCKED", reason=f"suspended for reviewed maintenance {name}; original baseline retained")
        self.save()
        return dict(status="ACTIVE", owner=owner, maintenance=name, artifact=record["archive"])

    def maintenance_return(self, name, review_path):
        record = self.maintenance_record()
        require(record and record["phase"] == "ACTIVE" and record["maintenance"] == name,
                "no matching active maintenance")
        owner = record["owner"]
        t, s = self.task(name)
        ot, os_ = self.task(owner)
        require(digest(ot) == record["owner_card_sha256"] and digest(t) == record["maintenance_card_sha256"]
                and os_["baseline_hash"] == record["owner_baseline_hash"], "maintenance card/baseline changed")
        require(s["status"] == "DONE", "maintenance must finish real fast/full checks before return")
        self.validate_full(name, s["receipt"], boundary=True)
        workspace = self.maintenance_workspace(owner)
        changed = sorted(n for n in record["before"].keys() | workspace.keys()
                         if record["before"].get(n) != workspace.get(n))
        credits = self.reconciliation_credit(owner, record["owner_baseline_hash"])
        changed = [n for n in changed if n not in credits or workspace.get(n) != credits[n]]
        if "verification/tasks.json" in changed:
            self.configuration_delta(owner, record["before"].get("verification/tasks.json"), workspace.get("verification/tasks.json"))
            changed.remove("verification/tasks.json")
        require(all(n in record["files"] for n in changed), "unowned workspace change during maintenance")
        review = self.maintenance_review(review_path, "return", owner, name, record["files"], workspace)
        require(review["review"].get("preauthorized_changes", {}) == record["migration"], "return migration review mismatch")
        for file, delta in record["migration"].items():
            if file == "verification/tasks.json" and workspace.get(file) != delta["after"]:
                self.configuration_delta(owner, delta["after"], workspace.get(file))
            else:
                require(workspace.get(file) == delta["after"], "preauthorized migration drifted")
        # A per-owner delta, never a new global hash exemption. The baseline
        # itself stays byte-for-byte intact; a later drift in either direction
        # fails again. The record is retained with the old failures and reviews.
        original = load(self.artifact(os_["baseline"]))
        record.update(phase="RETURNED", return_review=review, receipt=s["receipt"],
                      accepted={n: workspace.get(n) for n in record["files"] + list(record["migration"]) + ["verification/tasks.json"] if original.get(n) != workspace.get(n)})
        atomic(self.artifact(record["archive"]), record)
        atomic(self.home / "maintenance-active.json", record)
        os_.update(status="BLOCKED", reason=f"maintenance {name} returned; recheck dependencies and new fast/full required",
                   fast_receipt=None, receipt=None)
        self.save()
        return dict(status="BLOCKED", owner=owner, maintenance=name,
                    reason=os_["reason"], artifact=record["archive"])

    def maintenance_deltas(self, owner, baseline_hash):
        result = {}
        for path in sorted((self.home / "maintenance").glob("*.json")):
            record = load(path)
            if record.get("phase") == "RETURNED" and record["owner"] == owner and record["owner_baseline_hash"] == baseline_hash:
                for review in (record["start_review"], record["return_review"]):
                    require(sha(self.artifact(review["path"])) == review["sha256"], "maintenance review changed")
                self.validate_maintenance_history(record)
                for name, value in record["accepted"].items():
                    require(name in record["files"] or name in record.get("migration", {}) or name == "verification/tasks.json", "unreviewed maintenance delta")
                    require(name not in result or result[name] == value, "overlapping maintenance requires explicit migration")
                    result[name] = value
        return result

    def validate_maintenance_history(self, record):
        name = record["maintenance"]
        require(digest(self.tasks[name]) == record["maintenance_card_sha256"], "maintenance card changed")
        for run_id, tier in ((record["receipt"], "full"),):
            full = load(self.artifact(f"artifacts/verification/{run_id}/run.json"))
            for rid, expected in ((run_id, "full"), (full["fast_receipt"], "fast")):
                p = self.artifact(f"artifacts/verification/{rid}/run.json")
                run = load(p)
                require(run["id"] == rid and run["task"] == name and run["tier"] == expected
                        and run["status"] == "CLOSED" and run["type"] == "TOOL_PASS", "maintenance historical run not closed")
                require(len(run["checks"]) == len(self.tasks[name]["checks"][expected]), "maintenance historical checks incomplete")
                for check, receipt in zip(self.tasks[name]["checks"][expected], run["checks"]):
                    result = self.artifact(receipt["result"])
                    require(result == p.parent / (check["id"] + ".result.json") and receipt["id"] == check["id"]
                            and receipt["exit_code"] == 0 and sha(result) == receipt["result_sha256"], "maintenance historical result changed")
                    self.validate_result(check, load(result), run)
                    for stream in ("stdout", "stderr"):
                        require(sha(p.parent / (check["id"] + f".{stream}.log")) == receipt[stream + "_sha256"],
                                "maintenance historical log changed")

    def configuration_delta(self, owner, before_record, after_record):
        """Follow only owner-bound, independently reviewed single-card graph edits."""
        if before_record == after_record:
            return True
        _, state = self.task(owner)
        current, seen = before_record, set()
        audits = [load(p) for p in sorted((self.home / "task-configure").glob("*.json"))]
        while current != after_record:
            matches = [a for a in audits if a.get("owner_binding") == dict(id=owner,
                       baseline_sha256=state["baseline_hash"], card_sha256=digest(self.tasks[owner]))
                       and a.get("graph_before_record") == current]
            require(len(matches) == 1, "configuration audit chain missing/ambiguous or foreign owner")
            audit = matches[0]
            require(digest(audit) not in seen, "configuration audit cycle")
            seen.add(digest(audit))
            proposal, review = audit["proposal"], audit["review"]
            require(review["approved"] is True and review["reviewer_role"] == "independent" and review["issues"] == []
                    and review["proposal_sha256"] == audit["proposal_sha256"]
                    and review["base_task_sha256"] == proposal["base_task_sha256"]
                    and review["task_id"] == proposal["task_id"] == audit["task_id"], "configuration audit lacks independent approval")
            # Retain exact proposal/review bytes and hashes; document strings
            # alone or hand-written graph hashes are not accepted transitions.
            for field in ("proposal", "review"):
                source = self.artifact(audit[field + "_path"])
                require(sha(source) == audit[field + "_sha256"] and load(source) == audit[field], "configuration audit source changed")
            oldgraph, newgraph = audit["graph_before"], audit["graph_after"]
            for graph, field in ((oldgraph, "graph_before_record"), (newgraph, "graph_after_record")):
                raw = (json.dumps(graph, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
                # Before may have noncanonical but valid formatting; its exact
                # bytes are retained separately by the configuration tool.
                if field == "graph_before_record":
                    raw = bytes.fromhex(audit["graph_before_bytes_hex"])
                    require(json.loads(raw) == graph, "configuration before graph bytes mismatch")
                require(hashlib.sha256(raw).hexdigest() == audit[field]["sha256"], "configuration graph hash mismatch")
            old = {t["id"]: t for t in oldgraph["tasks"]}
            new = {t["id"]: t for t in newgraph["tasks"]}
            task_id = audit["task_id"]
            require(old.get(owner) == new.get(owner) == self.tasks[owner] and task_id != owner
                    and set(old) <= set(new) and set(new) - set(old) <= {task_id}
                    and all(new[n] == card for n, card in old.items() if n != task_id),
                    "configuration changed existing graph/current owner")
            if task_id in old:
                require(old[task_id] == audit["old_card"] and old[task_id]["checks"] == {"fast": [], "full": []},
                        "configuration changed a live card")
                for key in ("id", "goal", "depends", "completion", "max_rounds", "stop", "protected"):
                    require(old[task_id][key] == new[task_id][key], "configuration weakened original acceptance")
            else:
                require(proposal.get("maintenance") is True and new[task_id]["completion"] == "TOOL_PASS"
                        and new[task_id]["max_rounds"] == 3 and new[task_id]["depends"] == [], "invalid independent maintenance card")
            require(new[task_id] == audit["new_card"], "configuration card/audit mismatch")
            current = audit["graph_after_record"]
        return True

    def inner_ignored(self):
        """Paths the inner monorepo declares ignored AND the outer repo also
        ignores (eagler-touhou/ itself is ignored out here), and which no task
        card explicitly lists as inputs/protected. These are build outputs
        (tests/*/build binaries, web/artifacts incremental objects) rebuilt by
        the gate toolchain itself. Reviewed proposal
        task-configure-20261024-build-artifact-noise (proposal_sha256
        cf94db471c2b5dce8fb9488b8fc83a7ac6e59d3aba27145a8f721d49ca766dba,
        approved): boundary comparison must not treat their rebuild as
        out-of-scope work. Explicit card declarations override the noise rule
        (directory scopes count); protected scope still blocks even when
        ignored.
        """
        cache = getattr(self, "_inner_ignored_cache", None)
        if cache is not None:
            return cache
        inner = self.root / "eagler-touhou"
        declared = []
        for t in self.tasks.values():
            declared.extend(t["inputs"])
            declared.extend(t["protected"])
        result = set()
        if (inner / ".git").exists():
            p = subprocess.run(
                ["git", "-C", str(inner), "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
                capture_output=True, timeout=30)
            if p.returncode == 0:
                for raw in p.stdout.split(b"\0"):
                    if raw:
                        name = (inner / os.fsdecode(raw)).relative_to(self.root).as_posix()
                        if not within(name, declared):
                            result.add(name)
        self._inner_ignored_cache = result
        return result

    def runner_baseline(self):
        """Reviewed runner self-maintenance registry
        (task-configure-20261024-runner-self-maintenance, proposal_sha256
        71387f40c384c46cacea14710563c9a91849895b4be382970c842d1d297f90c0,
        approved): artifacts/verification/runner-baseline.json records the
        post-application hash of scripts/verify.py plus the review chain that
        authorized it. check_boundary treats an exactly-matching verify.py as
        unchanged; any deviation still hits protected scope and blocks. A
        missing registry keeps current behavior (no silent pass-through).

        Reviewed extension (task-configure-20261024-contract-test-registry,
        proposal_sha256 3eebcc5d1f7658f91675b484e72946e863e05c8c99e6706a6d25ea8f11dbe95c,
        approved): an optional contract_test_sha256 field extends the same
        triple semantics to scripts/test_verify.py (the contract-test file's
        authorized maintenance delta; review 3 deliberately deferred it to
        this separately reviewed channel). Field independence: a mismatch in
        one file never exempts the other. Old registries without the field keep
        the review-3 behavior for verify.py and fall back to full blocking for
        test_verify.py.

        Reviewed extension (task-configure-20261025-authorized-oracle-registry,
        proposal_sha256
        69bf4fef2440bbee0d7035691b650db236d3de3a5348215821dcf980416ec375,
        approved): an optional authorized_files map {path: sha256} extends the
        same triple semantics to review-authorized repair deltas (initially
        the oracle-repair review's scope: battle_oracle.py,
        test_replay_qualification.py, plus the capture-integrity regression
        companion whose authorization basis is the registry's applied_reviews
        note). Named-file list semantics: only explicitly registered paths are
        exempt; any other file (including other oracle/ files) is judged by
        card scope as before. Deviations from the registered hash still block.
        """
        path = self.home / "runner-baseline.json"
        if not path.is_file() or path.is_symlink():
            return None
        record = load(path)
        fields = ["schema", "verify_py_sha256", "applied_reviews", "applied_at"]
        if "contract_test_sha256" in record:
            fields.insert(2, "contract_test_sha256")
        if "authorized_files" in record:
            fields.insert(len(fields) - 2, "authorized_files")
        keys(record, " ".join(fields), "runner baseline")
        require(type(record["schema"]) is int and record["schema"] == 1, "runner baseline schema")
        require(isinstance(record["verify_py_sha256"], str) and len(record["verify_py_sha256"]) == 64,
                "runner baseline hash")
        if "contract_test_sha256" in record:
            require(isinstance(record["contract_test_sha256"], str)
                    and len(record["contract_test_sha256"]) == 64,
                    "runner baseline contract-test hash")
        if "authorized_files" in record:
            authorized = record["authorized_files"]
            require(isinstance(authorized, dict) and authorized,
                    "runner baseline authorized_files map")
            for name, value in authorized.items():
                require(isinstance(name, str) and name and not name.startswith("/"),
                        "authorized_files path")
                require(isinstance(value, str) and len(value) == 64,
                        "authorized_files hash")
        require(isinstance(record["applied_reviews"], list) and record["applied_reviews"], "runner baseline reviews")
        return record

    def reviewed_boundary_changes(self, changed, before, after):
        # Reviewed runner self-maintenance rule: scripts/verify.py at exactly
        # the registered baseline hash counts as unchanged (the registry binds
        # the review chain that authorized the maintenance). Deviations keep
        # full protected-scope blocking. Registry absent -> current behavior.
        # Reviewed extension: the same triple semantics covers
        # scripts/test_verify.py via the optional contract_test_sha256 field;
        # the two exemptions are independent (a mismatch in one file never
        # exempts the other).
        # Reviewed extension (authorized-oracle-registry): the optional
        # authorized_files map extends the same triple semantics to named
        # review-authorized repair files; only explicitly registered paths
        # are exempt and each file's hash binds independently.
        registry = self.runner_baseline()
        if registry is not None and "scripts/verify.py" in changed:
            current = after.get("scripts/verify.py") or before.get("scripts/verify.py")
            if current and isinstance(current, dict) and current.get("sha256") == registry["verify_py_sha256"]:
                changed = [n for n in changed if n != "scripts/verify.py"]
        if registry is not None and "scripts/test_verify.py" in changed \
                and "contract_test_sha256" in registry:
            current = after.get("scripts/test_verify.py") or before.get("scripts/test_verify.py")
            if current and isinstance(current, dict) and current.get("sha256") == registry["contract_test_sha256"]:
                changed = [n for n in changed if n != "scripts/test_verify.py"]
        if registry is not None and "authorized_files" in registry:
            for authorized_name, authorized_hash in registry["authorized_files"].items():
                if authorized_name not in changed:
                    continue
                current = after.get(authorized_name) or before.get(authorized_name)
                if current and isinstance(current, dict) and current.get("sha256") == authorized_hash:
                    changed = [n for n in changed if n != authorized_name]
        # Reviewed noise rule (task-configure-20261024-build-artifact-noise):
        # drop inner-ignored build outputs that no card declares. Explicit
        # inputs/protected paths are excluded from the ignored set upstream, so
        # scoped inputs (games/*.exe etc.) still compare; protected scope below
        # still blocks even when the path is ignored.
        noisy = self.inner_ignored()
        changed = [n for n in changed if n not in noisy]
        return changed

    def reconciliation_credit(self, name, baseline_hash):
        """Read-only exact credits from append-only, independently reviewed ledgers."""
        require(name in self.tasks and isinstance(baseline_hash, str) and len(baseline_hash) == 64,
                "invalid reconciliation request")
        def file(rel, review=False):
            parts = Path(rel).parts if isinstance(rel, str) else ()
            require(isinstance(rel, str) and rel and not rel.startswith("/") and "\\" not in rel
                    and Path(rel).as_posix() == rel and ".." not in parts
                    and not any(c in rel for c in "*?[]{}")
                    and not any(p.casefold() in {"games", "policy", "tasks"} for p in parts)
                    and Path(rel).name.casefold() not in {"agents.md", "tasks.json", "policy.json"},
                    f"unsafe reconciliation path: {rel!r}")
            current = self.root
            for part in parts:
                current /= part
                require(not current.is_symlink(), f"reconciliation symlink: {rel}")
            path = self.root / rel
            require(path.is_file() and path.stat().st_nlink == 1
                    and path.resolve().is_relative_to(self.root), f"reconciliation alias: {rel}")
            if review:
                check = subprocess.run(["git", "-C", str(self.root), "check-ignore", "--quiet", "--", rel],
                                       capture_output=True, timeout=30)
                require(check.returncode == 0, f"reconciliation source not ignored: {rel}")
            return path
        result, ids, reviews = {}, set(), set()
        folder = self.home / "reconciliations"
        if not folder.exists():
            return result
        require(folder.is_dir() and not folder.is_symlink(), "unsafe reconciliation ledger")
        for entry in sorted(folder.glob("*.json")):
            rel = entry.relative_to(self.root).as_posix()
            record = load(file(rel, True))
            keys(record, "schema kind reconcile_id owner maintenance owner_card_sha256 maintenance_card_sha256 "
                 "owner_baseline maintenance_baseline owner_state_sha256 maintenance_state_sha256 "
                 "start_review_sha256 reviewed_identity runner_sha256 tool_identity review changes "
                 "no_fidelity_certification applied_at", "reconciliation record")
            runner, tool_identity = self.reconciliation_tools(record["owner"], record["maintenance"])
            require(type(record["schema"]) is int and record["schema"] == 1
                    and record["kind"] == "SCOPE_RECONCILIATION" and record["no_fidelity_certification"] is True
                    and isinstance(record["reconcile_id"], str) and record["reconcile_id"] not in ids
                    and record["owner"] in self.tasks and record["maintenance"] in self.tasks
                    and record["owner"] != record["maintenance"], "invalid reconciliation identity")
            ids.add(record["reconcile_id"])
            require(digest(self.tasks[record["owner"]]) == record["owner_card_sha256"]
                    and digest(self.tasks[record["maintenance"]]) == record["maintenance_card_sha256"],
                    "reconciliation card changed")
            for base in (record["owner_baseline"], record["maintenance_baseline"]):
                keys(base, "path sha256", "reconciliation baseline")
                require(within(base["path"], ["artifacts/verification/baselines"])
                        and sha(file(base["path"])) == base["sha256"], "reconciliation baseline changed")
            keys(record["review"], "path sha256", "reconciliation review reference")
            require(within(record["review"]["path"], ["artifacts/verification"])
                    and sha(file(record["review"]["path"], True)) == record["review"]["sha256"]
                    and record["review"]["sha256"] not in reviews, "reconciliation review changed/replayed")
            reviews.add(record["review"]["sha256"])
            review = load(file(record["review"]["path"], True))
            keys(review, "schema phase reconcile_id owner maintenance owner_card_sha256 maintenance_card_sha256 "
                 "owner_baseline_sha256 maintenance_baseline_sha256 owner_state_sha256 maintenance_state_sha256 "
                 "start_review_sha256 reviewed_identity runner_sha256 tool_identity changes approved "
                 "reviewer_role issues", "reconciliation review")
            require(type(review["schema"]) is int and review["schema"] == 1
                    and review["phase"] == "reconcile" and review["approved"] is True
                    and review["reviewer_role"] == "independent" and review["issues"] == []
                    and all(review[k] == record[k] for k in ("reconcile_id", "owner", "maintenance",
                        "owner_card_sha256", "maintenance_card_sha256", "runner_sha256",
                        "tool_identity", "changes", "owner_state_sha256", "maintenance_state_sha256",
                        "start_review_sha256", "reviewed_identity"))
                    and review["owner_baseline_sha256"] == record["owner_baseline"]["sha256"]
                    and review["maintenance_baseline_sha256"] == record["maintenance_baseline"]["sha256"],
                    "reconciliation review binding/role failed")
            require(record["runner_sha256"] == runner and record["tool_identity"] == tool_identity,
                    "reconciliation tool identity changed")
            bound = dict(record, owner_baseline_sha256=record["owner_baseline"]["sha256"],
                         maintenance_baseline_sha256=record["maintenance_baseline"]["sha256"])
            require(record["reviewed_identity"] == self.reconciliation_review_identity(bound)
                    and isinstance(record["applied_at"], str) and record["applied_at"],
                    "reconciliation reviewed identity changed")
            changes = record["changes"]
            require(isinstance(changes, list) and changes
                    and all(isinstance(c, dict) for c in changes), "invalid reconciliation changes")
            pair = []
            for change in changes:
                keys(change, "boundary path after", "reconciliation change")
                keys(change["after"], "sha256 mode", "reconciliation after")
                require(change["boundary"] in {"owner", "maintenance"} and isinstance(change["path"], str)
                        and isinstance(change["after"]["sha256"], str) and len(change["after"]["sha256"]) == 64
                        and type(change["after"]["mode"]) is int and 0 <= change["after"]["mode"] <= 0o7777,
                        "invalid reconciliation change")
                pair.append((change["boundary"], change["path"]))
            require(pair == sorted(pair) and len(set(pair)) == len(pair),
                    "reconciliation change order/duplicate")
            accepted = self.maintenance_deltas(
                record["owner"], record["owner_baseline"]["sha256"])
            for change in changes:
                after = change["after"]
                if change["boundary"] == "owner":
                    # Returned exact afters supersede historical credits, even
                    # when an unauthorized edit restores the historical bytes.
                    after = accepted.get(change["path"], after)
                target = file(change["path"])
                current = dict(sha256=sha(target), mode=stat.S_IMODE(target.stat().st_mode))
                if current != after:
                    # Further changes need a live, baseline-bound maintenance
                    # card that owns the edit; returned values are not a freeze.
                    active = self.maintenance_record()
                    repairing = False
                    if (active and active["phase"] == "ACTIVE" and active["owner"] == record["owner"]
                            and active["owner_baseline_hash"] == record["owner_baseline"]["sha256"]):
                        task = self.tasks[active["maintenance"]]
                        repairing = (digest(task) == active["maintenance_card_sha256"]
                                     and change["path"] in active["files"]
                                     and within(change["path"], task["allowed"])
                                     and not within(change["path"], task["protected"] + self.policy["protected"]))
                    require(change["boundary"] == "owner" and repairing,
                            f"reconciliation drift: {change['path']}")
                if name in {record["owner"], record["maintenance"]}:
                    boundary = "owner" if name == record["owner"] else "maintenance"
                    if change["boundary"] == boundary and baseline_hash == record[boundary + "_baseline"]["sha256"]:
                        require(change["path"] not in result, "duplicate reconciliation credit")
                        result[change["path"]] = dict(after)
        return result

    def reconciliation_tools(self, owner, maintenance):
        runner = sha(Path(__file__).resolve())
        tools = {}
        for task in (owner, maintenance):
            for tier in self.tasks[task]["checks"].values():
                for check in tier:
                    tool = shutil.which(check["argv"][0])
                    require(tool, f"missing reconciliation tool {check['argv'][0]}")
                    tools[tool] = sha(tool)
        return runner, digest(dict(runner_sha256=runner, python=sys.version, tools=tools))

    @staticmethod
    def reconciliation_review_identity(value):
        fields = ("owner_card_sha256 maintenance_card_sha256 owner_baseline_sha256 "
                  "maintenance_baseline_sha256 owner_state_sha256 maintenance_state_sha256 "
                  "start_review_sha256 changes").split()
        return digest({name: value[name] for name in fields})

    def reconcile_maintenance(self, name, owner, review_path):
        """Append one exact-path scope ledger; no state, run, baseline, or pass edit."""
        require((name, owner) == (RECONCILE_MAINTENANCE, RECONCILE_OWNER),
                "reconciliation pair is fixed")
        active = self.maintenance_record()
        require(active and active["phase"] == "ACTIVE" and active["maintenance"] == name
                and active["owner"] == owner, "exact active maintenance pair required")
        t, s = self.task(name)
        ot, os_ = self.task(owner)
        require(digest(ot) == active["owner_card_sha256"]
                and digest(t) == active["maintenance_card_sha256"]
                and os_["baseline_hash"] == active["owner_baseline_hash"],
                "maintenance cards or owner baseline changed")
        owner_base = dict(path=os_["baseline"], sha256=os_["baseline_hash"])
        maint_base = dict(path=s["baseline"], sha256=s["baseline_hash"])
        for base in (owner_base, maint_base):
            p = self.artifact(base["path"])
            require(p.is_file() and not p.is_symlink() and p.stat().st_nlink == 1
                    and sha(p) == base["sha256"], "reconciliation baseline changed")
        start = active["start_review"]
        require(sha(self.artifact(start["path"])) == start["sha256"], "start review changed")
        p = self.artifact(review_path)
        require(within(review_path, ["artifacts/verification"]) and p.is_file()
                and not p.is_symlink() and p.stat().st_nlink == 1,
                "missing/aliased reconciliation review")
        part = self.root
        for segment in Path(review_path).parts:
            part /= segment
            require(not part.is_symlink(), "reconciliation review symlink")
        ignored = subprocess.run(["git", "-C", str(self.root), "check-ignore", "--quiet", "--", review_path],
                                 capture_output=True, timeout=30)
        require(ignored.returncode == 0, "reconciliation review must be ignored")
        review = load(p)
        keys(review, "schema phase reconcile_id owner maintenance owner_card_sha256 maintenance_card_sha256 "
             "owner_baseline_sha256 maintenance_baseline_sha256 owner_state_sha256 maintenance_state_sha256 "
             "start_review_sha256 reviewed_identity runner_sha256 tool_identity changes approved "
             "reviewer_role issues", "reconciliation review")
        changes = []
        for path in sorted(RECONCILE_PATHS):
            target = self.root / path
            require(target.is_file() and not target.is_symlink()
                    and target.stat().st_nlink == 1, f"reconciliation target missing/aliased: {path}")
            changes.append(dict(boundary="owner", path=path, after=self.file_record(path)))
        require(review["changes"] == changes, "review is not the exact current 25 paths")
        workspace = self.maintenance_workspace(owner)
        for boundary, base in ((owner, owner_base), (name, maint_base)):
            before = load(self.artifact(base["path"]))
            # Compare the same maintenance inventory on both sides; games and
            # runtime entries in the old owner baseline are not deletions.
            changed = [n for n in before.keys() | workspace.keys()
                       if not within(n, ["games"]) and not self.game_runtime(n)
                       and before.get(n) != workspace.get(n)]
            require(set(RECONCILE_PATHS) <= set(changed), "reviewed path is not a real delta")
            accepted = self.maintenance_deltas(boundary, base["sha256"])
            changed = [n for n in changed if n not in accepted or workspace.get(n) != accepted[n]]
            changed = set(self.reviewed_boundary_changes(changed, before, workspace))
            require(set(RECONCILE_PATHS) <= changed, "reviewed path is not a real delta")
            extra = changed - set(RECONCILE_PATHS) - set(active["files"]) - set(active["migration"])
            if boundary == owner:
                # Owner work predating maintenance stays the owner's duty.
                # The maintenance baseline still rejects any later such edit.
                extra = {n for n in extra if not within(n, ot["allowed"])
                         or within(n, ot["protected"] + self.policy["protected"])}
            require(not extra, "unreviewed extra reconciliation delta")
        runner, tools = self.reconciliation_tools(owner, name)
        require(type(review["schema"]) is int and review["schema"] == 1
                and review["phase"] == "reconcile" and review["approved"] is True
                and review["reviewer_role"] == "independent" and review["issues"] == []
                and (review["owner"], review["maintenance"]) == (owner, name)
                and review["owner_card_sha256"] == digest(ot)
                and review["maintenance_card_sha256"] == digest(t)
                and review["owner_baseline_sha256"] == owner_base["sha256"]
                and review["maintenance_baseline_sha256"] == maint_base["sha256"]
                and review["owner_state_sha256"] == digest(os_)
                and review["maintenance_state_sha256"] == digest(s)
                and review["start_review_sha256"] == start["sha256"]
                and review["runner_sha256"] == runner and review["tool_identity"] == tools
                and review["reviewed_identity"] == self.reconciliation_review_identity(review),
                "review does not bind cards, baselines, state, start review, tools, or afters")
        folder = self.home / "reconciliations"
        require(not folder.exists() or folder.is_dir() and not folder.is_symlink(),
                "unsafe reconciliation ledger")
        self.reconciliation_credit(owner, os_["baseline_hash"])
        anchor = (owner, name, owner_base["sha256"], maint_base["sha256"], start["sha256"])
        for entry in sorted(folder.glob("*.json")) if folder.exists() else ():
            old = load(entry)
            require(old["reconcile_id"] != review["reconcile_id"]
                    and old["review"]["sha256"] != sha(p)
                    and (old["owner"], old["maintenance"], old["owner_baseline"]["sha256"],
                         old["maintenance_baseline"]["sha256"], old["start_review_sha256"]) != anchor,
                    "reconciliation already recorded")
        record = dict(schema=1, kind="SCOPE_RECONCILIATION", reconcile_id=review["reconcile_id"],
                      owner=owner, maintenance=name, owner_card_sha256=digest(ot),
                      maintenance_card_sha256=digest(t), owner_baseline=owner_base,
                      maintenance_baseline=maint_base, owner_state_sha256=digest(os_),
                      maintenance_state_sha256=digest(s), start_review_sha256=start["sha256"],
                      reviewed_identity=review["reviewed_identity"], runner_sha256=runner,
                      tool_identity=tools, review=dict(path=review_path, sha256=sha(p)),
                      changes=changes, no_fidelity_certification=True,
                      applied_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        target = folder / (uuid.uuid4().hex + ".json")
        atomic(target, record)
        return dict(status="SCOPE_RECONCILIATION", artifact=str(target.relative_to(self.root)),
                    paths=len(changes), owner=owner, maintenance=name,
                    no_fidelity_certification=True)

    def check_boundary(self, name):
        t, s = self.task(name)
        require(s["baseline"], "missing start baseline")
        p = self.artifact(s["baseline"])
        require(sha(p) == s["baseline_hash"], "baseline record changed")
        before = load(p)
        after = self.snapshot()
        scopes = t["protected"] + self.policy["protected"] + t["inputs"]
        maintenance = self.maintenance_record()
        if maintenance and maintenance["maintenance"] == name:
            # A maintenance card's baseline is the owner workspace captured with
            # the OWNER's scopes (maintenance_start -> maintenance_workspace).
            # Enumerate with exactly those scopes so the two sides of the
            # comparison share one inventory basis. The maintenance card's own
            # broad protected directory scopes (e.g. a whole repo tree) still
            # block edits below via the forbidden check; they must not widen
            # enumeration, otherwise pre-existing ignored build trees inside a
            # protected directory appear as unowned new changes.
            owner_card = self.tasks[maintenance["owner"]]
            scopes = owner_card["inputs"] + owner_card["protected"] + self.policy["protected"]
            scopes = [n for n in scopes if not within(n, ["games"])]
        after.update(self.scoped(scopes))
        # Missing paths from prior inventory are deletions, not invisible changes.
        changed = sorted(n for n in before.keys() | after.keys() if not self.game_runtime(n) and (Path(n).parts[0] != "games" or n == "games" or before.get(n, {}).get("directory") or after.get(n, {}).get("directory") or Path(n).suffix.casefold() in {".exe", ".dat", ".rpy", ".anm", ".ecl", ".std", ".msg", ".wav", ".ogg", ".flac", ".mp3", ".mid", ".png", ".bmp", ".jpg"}) and before.get(n) != after.get(n))
        if maintenance and maintenance["phase"] == "ACTIVE" and maintenance["maintenance"] == name:
            for file, delta in maintenance.get("migration", {}).items():
                if file == "verification/tasks.json" and after.get(file) != delta["after"]:
                    self.configuration_delta(maintenance["owner"], delta["after"], after.get(file))
                else:
                    require(after.get(file) == delta["after"], "preauthorized migration drifted")
        accepted = self.maintenance_deltas(name, s["baseline_hash"])
        graph_anchor = accepted.get("verification/tasks.json", before.get("verification/tasks.json"))
        if "verification/tasks.json" in changed and graph_anchor != after.get("verification/tasks.json"):
            chain_owner = maintenance["owner"] if maintenance and maintenance["phase"] == "ACTIVE" and maintenance["maintenance"] == name else name
            self.configuration_delta(chain_owner, graph_anchor, after.get("verification/tasks.json"))
            changed = [n for n in changed if n != "verification/tasks.json"]
        changed = [n for n in changed if n not in accepted or after.get(n) != accepted[n]]
        credits = self.reconciliation_credit(name, s["baseline_hash"])
        if maintenance and maintenance["maintenance"] == name:
            credits.update(self.reconciliation_credit(
                maintenance["owner"],
                self.state["tasks"][maintenance["owner"]]["baseline_hash"]))
        changed = [n for n in changed if n not in credits or after.get(n) != credits[n]]
        changed = self.reviewed_boundary_changes(changed, before, after)
        forbidden = [n for n in changed if not within(n, t["allowed"]) or within(n, t["protected"] + self.policy["protected"])]
        require(not forbidden, "out-of-scope/protected changes: " + ", ".join(forbidden[:30]))
        return changed

    def validate_result(self, c, record, run):
        keys(record, "schema run_id check_id identity type passed summary ignored evidence", "structured result")
        require(type(record["schema"]) is int and record["schema"] == 1, "result schema")
        require(record["run_id"] == run["id"] and record["check_id"] == c["id"]
                and record["identity"] == run["identity"], "stale/foreign result identity")
        require(record["type"] in TYPES and type(record["passed"]) is bool
                and isinstance(record["summary"], str), "result types")
        require(record["ignored"] == [] and isinstance(record["ignored"], list), "filtered result cannot certify")
        require(record["passed"] and record["type"] == c["result_type"]
                and record["type"] not in {"BLOCKED", "FAIL"}, "gate did not produce required pass type")
        require(isinstance(record["evidence"], list), "invalid evidence")
        w = c["window"]
        if w is None:
            require(record["evidence"] == [], "tool gates cannot promote static evidence to fidelity")
            return
        require(len(record["evidence"]) == len(w["roles"]), "missing window evidence")
        roles, inodes = set(), set()
        for e in record["evidence"]:
            keys(e, "role path sha256", "capture evidence")
            require(e["role"] in w["roles"] and e["role"] not in roles, "unknown/duplicate role")
            roles.add(e["role"])
            p = self.artifact(e["path"])
            run_dir = self.home / run["id"]
            require(p.resolve().is_relative_to(run_dir.resolve()), "capture must belong to this run")
            st = p.stat()
            inode = (st.st_dev, st.st_ino)
            require(inode not in inodes and st.st_nlink == 1, "same-file/hardlink capture alias")
            inodes.add(inode)
            require(sha(p) == e["sha256"], "capture hash mismatch")
            count = 0
            with p.open() as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError as ex:
                        raise Blocked("malformed capture") from ex
                    require(isinstance(row, dict) and type(row.get("frame")) is int
                            and row["frame"] == w["start"] + count, "duplicate/missing/out-of-order frame")
                    count += 1
            require(count == w["end"] - w["start"] + 1, "common short/empty/oversized capture window")

    def execute(self, c, run, directory):
        require(not c["external"], "external shot evidence required: main session must run th13-shot on host; "
                "register a reviewed process/result adapter before retrying; no nested podman or manual PASS import")
        result_path = directory / (c["id"] + ".result.json")
        stdout, stderr = directory / (c["id"] + ".stdout.log"), directory / (c["id"] + ".stderr.log")
        env = os.environ.copy()
        env.update(VERIFY_RESULT_PATH=str(result_path), VERIFY_RUN_ID=run["id"],
                   VERIFY_CHECK_ID=c["id"], VERIFY_IDENTITY=run["identity"])
        started = time.time_ns()
        process = None
        try:
            with stdout.open("xb") as out, stderr.open("xb") as err:
                process = subprocess.Popen(c["argv"], cwd=self.path(c["cwd"]), env=env,
                                           stdout=out, stderr=err, start_new_session=True)
                try:
                    rc = process.wait(timeout=c["timeout"])
                except subprocess.TimeoutExpired as e:
                    raise Blocked(f"timeout: {c['id']}") from e
        finally:
            if process is not None:
                # Kill descendants too, even if the leader already exited. A still
                # writing child must not turn an open producer into closed evidence.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        require(rc == 0, f"nonzero process exit {rc}: {c['id']}; RESULT none is not sufficient")
        require(result_path.exists() and not result_path.is_symlink() and result_path.stat().st_nlink == 1,
                "missing/aliased structured result")
        require(result_path.stat().st_mtime_ns >= started, "preexisting structured result")
        result = load(result_path)
        self.validate_result(c, result, run)
        return dict(id=c["id"], exit_code=rc, result=str(result_path.relative_to(self.root)),
                    result_sha256=sha(result_path), stdout_sha256=sha(stdout), stderr_sha256=sha(stderr))

    def reverify(self, name, tier, boundary_owner):
        """Read-only dependency re-verification without round accounting.

        Reviews task-configure-20261024-recheck-noround (proposal_sha256
        70c0b5452f5a8028431f06bfe99cc2ed98f48bacb2643b7f9aaa63ca44f3f19f,
        approved): recheck_dependencies re-executes unchanged dependency gates;
        unlike run() this channel does not consume the dependency task's own
        revision round budget (that budget guards "same task, same change,
        three tries" mechanism review, not read-only re-verification).
        All evidence checks stay identical: structured result validation,
        exit code, artifact integrity, boundary snapshot immutability.
        """
        self.guard_maintenance(name)
        t, s = self.task(name)
        require(s["baseline"], f"dependency {name} has never started")
        require(not t["blocked_reason"] and t["checks"][tier], f"{tier} gate is not configured")
        scope = boundary_owner or name
        identity = self.identity(name)
        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex
        directory = self.home / run_id
        directory.mkdir()
        workspace_before = self.snapshot() if boundary_owner else None
        run = dict(schema=1, id=run_id, task=name, tier=tier, identity=identity,
                   baseline_hash=s["baseline_hash"], status="VERIFYING", type="BLOCKED",
                   reason="run not closed", checks=[],
                   fast_receipt=s["fast_receipt"] if tier == "full" else None)
        # Marker kept out of the record: validate_closed enforces the exact
        # run schema; re-verification is identified by its directory evidence
        # and the owner's recheck_dependencies result, not extra fields.
        s.update(status="VERIFYING", reason="read-only dependency re-verification", run=run_id, receipt=None)
        if tier == "fast":
            s["fast_receipt"] = None
        self.save()
        atomic(directory / "run.json", run)
        try:
            if tier == "full":
                self.validate_closed(name, run["fast_receipt"], "fast")
                require(any(c["result_type"] == t["completion"] for c in t["checks"][tier]), "tool pass cannot certify fidelity")
            for c in t["checks"][tier]:
                run["checks"].append(self.execute(c, run, directory))
                atomic(directory / "run.json", run)
            require(self.identity(name) == run["identity"], "source/input/tool/contract changed during run")
            changed = self.check_boundary(scope)
            if boundary_owner:
                require(self.snapshot() == workspace_before, "dependency recheck modified workspace; read-only gates required")
            atomic(directory / "changes.json", changed)
            run.update(status="CLOSED", type=t["completion"] if tier == "full" else "TOOL_PASS",
                       reason="configured checks passed (read-only re-verification)")
            s.update(status="ACTIVE", reason="re-verified under owner %s; %s still required"
                     % (boundary_owner, "finish" if tier == "full" else "full"),
                     run=run_id)
            if tier == "fast":
                s["fast_receipt"] = run_id
        except (Blocked, OSError, ValueError, subprocess.SubprocessError) as e:
            run.update(status="CLOSED", type="BLOCKED" if isinstance(e, Blocked) else "FAIL", reason=str(e))
            s.update(status="BLOCKED" if run["type"] == "BLOCKED" else "FAILED", reason=str(e), receipt=None)
            raise
        finally:
            # Round accounting untouched: rounds/round_identity stay on the
            # task's own revision history; only the run receipt advances to
            # the fresh re-verification evidence.
            atomic(directory / "run.json", run)
            self.save()
        return run

    def recheck_dependencies(self, owner):
        """Re-execute configured ancestors under the active owner's delta boundary.

        No rebaseline or source changes: later modules may legitimately change
        earlier modules' inputs. Their old certificates must not be reused.
        Dependency re-verification goes through reverify() (read-only, no
        dependency round budget consumption; reviewed proposal
        task-configure-20261024-recheck-noround). Owner round accounting is
        unchanged.
        """
        self.guard_maintenance(owner)
        _, state = self.task(owner)
        require(state["baseline"] and state["status"] in {"ACTIVE", "STALE", "BLOCKED", "FAILED"},
                "dependency recheck requires an unfinished owner with retained baseline")
        self.check_boundary(owner)
        visited, runs = set(), []
        def recheck(name):
            if name in visited:
                return
            visited.add(name)
            for dep in self.tasks[name]["depends"]:
                recheck(dep)
            if name == owner:
                return
            t, s = self.task(name)
            if s["status"] == "DONE":
                self.validate_full(name, s["receipt"])
                return
            require(s["baseline"], f"dependency {name} has never started; recheck cannot bootstrap it")
            require(not t["blocked_reason"] and all(t["checks"].values()), f"dependency {name} is unconfigured")
            self.check_boundary(owner)
            s.update(status="ACTIVE", reason=f"read-only dependency recheck under owner {owner}")
            for tier in ("fast", "full"):
                run = self.reverify(name, tier, boundary_owner=owner)
                runs.append(run["id"])
                require(run["type"] == (t["completion"] if tier == "full" else "TOOL_PASS"),
                        f"dependency {name} {tier} failed: {run['reason']}")
            require(self.finish(name, boundary_owner=owner)["status"] == "DONE", f"dependency {name} did not finish")
        recheck(owner)
        self.check_boundary(owner)
        state.update(status="ACTIVE", reason="dependencies rechecked with new process evidence; owner fast/full still required")
        self.save()
        return dict(status="ACTIVE", owner=owner, dependency_runs=runs)

    def run(self, name, tier, boundary_owner=None):
        self.guard_maintenance(name)
        t, s = self.task(name)
        require(s["baseline"] and s["status"] in {"ACTIVE", "FAILED", "BLOCKED", "STALE"}, "start task before running")
        require(all(self.state["tasks"][d]["status"] == "DONE" for d in t["depends"]), "uncertified dependencies")
        self.check_boundary(boundary_owner or name)
        identity = self.identity(name)
        if tier == "full":
            self.validate_closed(name, s["fast_receipt"], "fast")
        previous = load(self.artifact(f"artifacts/verification/{s['run']}/run.json")) if s["run"] else None
        interrupted = (previous is not None and previous["status"] == "VERIFYING"
                       and previous["identity"] == identity and previous["tier"] == tier)
        paired = (tier == "full" and previous is not None and previous["id"] == s["fast_receipt"]
                  and s["round_identity"] == identity)
        increment = 0 if interrupted or paired else 1
        require(s["rounds"] + increment <= t["max_rounds"], "round budget exhausted; stop for mechanism review")
        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex
        directory = self.home / run_id
        directory.mkdir()
        workspace_before = self.snapshot() if boundary_owner else None
        run = dict(schema=1, id=run_id, task=name, tier=tier, identity=identity,
                   baseline_hash=s["baseline_hash"], status="VERIFYING", type="BLOCKED",
                   reason="run not closed", checks=[],
                   fast_receipt=s["fast_receipt"] if tier == "full" else None)
        s.update(status="VERIFYING", reason="running", run=run_id, rounds=s["rounds"] + increment,
                 round_identity=identity, receipt=None)
        if tier == "fast":
            s["fast_receipt"] = None
        self.save()
        atomic(directory / "run.json", run)
        try:
            require(not t["blocked_reason"], t["blocked_reason"])
            require(t["checks"][tier], f"empty/unconfigured {tier} gates")
            if tier == "full":
                require(any(c["result_type"] == t["completion"] for c in t["checks"][tier]),
                        "tool/baseline passes cannot certify fidelity task")
            for c in t["checks"][tier]:
                run["checks"].append(self.execute(c, run, directory))
                atomic(directory / "run.json", run)
            require(self.identity(name) == run["identity"], "source/input/tool/contract changed during run")
            changed = self.check_boundary(boundary_owner or name)
            if boundary_owner:
                require(self.snapshot() == workspace_before, "dependency recheck modified workspace; read-only gates required")
            atomic(directory / "changes.json", changed)
            run.update(status="CLOSED", type=t["completion"] if tier == "full" else "TOOL_PASS", reason="configured checks passed")
            s.update(status="ACTIVE", reason="run closed; finish still required")
            if tier == "fast":
                s["fast_receipt"] = run_id
        except (Blocked, OSError, ValueError, subprocess.SubprocessError) as e:
            run.update(status="CLOSED", type="BLOCKED" if isinstance(e, Blocked) else "FAIL", reason=str(e))
            s.update(status="BLOCKED" if run["type"] == "BLOCKED" else "FAILED", reason=str(e))
        except BaseException:
            # Leave VERIFYING durable for resume to explicitly mark interrupted.
            raise
        finally:
            atomic(directory / "run.json", run)
            self.save()
        return run

    def validate_full(self, name, run_id, boundary=False):
        run = self.validate_closed(name, run_id, "full")
        self.validate_closed(name, run["fast_receipt"], "fast")
        if boundary:
            self.check_boundary(name)
        return run

    def validate_closed(self, name, run_id, tier):
        t, s = self.task(name)
        require(isinstance(run_id, str) and run_id and "/" not in run_id and ".." not in run_id, "missing full-run receipt")
        require(not t["blocked_reason"] and t["checks"][tier], f"{tier} gate is not configured")
        p = self.artifact(f"artifacts/verification/{run_id}/run.json")
        run = load(p)
        keys(run, "schema id task tier identity baseline_hash status type reason checks fast_receipt", "run")
        require(type(run["schema"]) is int and run["schema"] == 1 and run["id"] == run_id
                and run["task"] == name and run["tier"] == tier and run["status"] == "CLOSED"
                and run["type"] == (t["completion"] if tier == "full" else "TOOL_PASS"), "no successful closed run")
        require(run["identity"] == self.identity(name), "stale source/input/tool/contract identity")
        require(run["baseline_hash"] == s["baseline_hash"] and s["baseline"]
                and sha(self.artifact(s["baseline"])) == s["baseline_hash"], "run does not match intact start baseline")
        require(isinstance(run["checks"], list) and len(run["checks"]) == len(t["checks"][tier]), "incomplete checks")
        if tier == "full":
            require(any(c["result_type"] == t["completion"] for c in t["checks"][tier]),
                    "tool/baseline passes cannot certify fidelity task")
        for c, r in zip(t["checks"][tier], run["checks"]):
            keys(r, "id exit_code result result_sha256 stdout_sha256 stderr_sha256", "check receipt")
            require(r["id"] == c["id"] and type(r["exit_code"]) is int and r["exit_code"] == 0, "process receipt inconsistent")
            result_path = self.artifact(r["result"])
            require(result_path == p.parent / (c["id"] + ".result.json"), "foreign result path")
            require(sha(result_path) == r["result_sha256"], "result changed after exit")
            for stream in ("stdout", "stderr"):
                require(sha(p.parent / (c["id"] + f".{stream}.log")) == r[stream + "_sha256"], "process log changed after exit")
            self.validate_result(c, load(result_path), run)
        return run

    def finish(self, name, boundary_owner=None):
        self.guard_maintenance(name)
        t, s = self.task(name)
        try:
            require(s["status"] == "ACTIVE", "task must have a closed run, not manual DONE")
            require(all(self.state["tasks"][d]["status"] == "DONE" for d in t["depends"]), "dependencies not DONE")
            self.validate_full(name, s["run"], boundary=boundary_owner is None)
            if boundary_owner:
                self.check_boundary(boundary_owner)
            s.update(status="DONE", receipt=s["run"], reason="current full process evidence and boundary validated")
        except (Blocked, OSError, KeyError, TypeError) as e:
            s.update(status="BLOCKED", reason=str(e), receipt=None)
        self.save()
        return s

    def handoff(self):
        result = dict(schema=1, tasks=self.state["tasks"],
                      next=[n for n, s in self.state["tasks"].items() if s["status"] == "READY"],
                      warning="Artifact only; main session may apply summary to HANDOFF. No business fidelity implied.")
        p = self.home / ("handoff-" + uuid.uuid4().hex + ".json")
        atomic(p, result)
        return {"artifact": str(p.relative_to(self.root)), "next": result["next"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for cmd in ("doctor", "resume", "handoff"):
        sub.add_parser(cmd)
    task = sub.add_parser("task").add_subparsers(dest="action", required=True)
    for cmd in ("show", "start", "finish", "reopen", "recheck-dependencies"):
        task.add_parser(cmd).add_argument("id")
    maintenance = task.add_parser("maintenance-start")
    maintenance.add_argument("id")
    maintenance.add_argument("--owner", required=True)
    maintenance.add_argument("--review", required=True)
    returned = task.add_parser("maintenance-return")
    returned.add_argument("id")
    returned.add_argument("--review", required=True)
    reconciled = task.add_parser("reconcile-maintenance")
    reconciled.add_argument("id")
    reconciled.add_argument("--owner", required=True)
    reconciled.add_argument("--review", required=True)
    review = task.add_parser("mechanism-review")
    review.add_argument("id")
    review.add_argument("--review", required=True)
    run = sub.add_parser("run")
    run.add_argument("id")
    run.add_argument("--tier", choices=("fast", "full"), required=True)
    args = parser.parse_args(argv)
    try:
        runner = Runner(ROOT)
        runner.environment()
        with runner.lock():
            if not (args.command == "task" and args.action in {
                    "maintenance-start", "maintenance-return", "reconcile-maintenance"}):
                runner.refresh()
            if args.command == "doctor":
                result = {"type": "TOOL_PASS", "scope": "runner environment/config only", "tasks": len(runner.tasks),
                          "unconfigured_full": [n for n, t in runner.tasks.items() if not t["checks"]["full"] or t["blocked_reason"]]}
            elif args.command == "resume":
                result = {"tasks": runner.state["tasks"], "next": [n for n, s in runner.state["tasks"].items() if s["status"] == "READY"]}
            elif args.command == "handoff":
                result = runner.handoff()
            elif args.command == "task":
                if args.action == "show":
                    t, s = runner.task(args.id)
                    result = {"task": t, "state": s}
                elif args.action == "maintenance-start":
                    result = runner.maintenance_start(args.id, args.owner, args.review)
                elif args.action == "maintenance-return":
                    result = runner.maintenance_return(args.id, args.review)
                elif args.action == "reconcile-maintenance":
                    result = runner.reconcile_maintenance(args.id, args.owner, args.review)
                elif args.action == "mechanism-review":
                    result = runner.mechanism_review(args.id, args.review)
                else:
                    method = "recheck_dependencies" if args.action == "recheck-dependencies" else args.action
                    result = getattr(runner, method)(args.id)
            else:
                result = runner.run(args.id, args.tier)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if result.get("status") in {"BLOCKED", "FAILED", "STALE"} or result.get("type") in {"BLOCKED", "FAIL"} else 0
    except (Blocked, OSError, ValueError, subprocess.SubprocessError) as e:
        print(json.dumps({"type": "BLOCKED", "reason": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
