"""Run independent replay/stage captures, keeping each result and log separate.

Examples: python3 tools/batch_replay_oracle.py --out artifacts/replay-run --jobs 4
Outputs are diagnostics, not a fidelity or final-rendering certification.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import os
import re
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oracle.replayfmt import ReplayFile

SOURCE_FILES = tuple(Path("oracle") / name for name in (
    "__init__.py", "battle_oracle.py", "title_oracle.py", "render_oracle.py",
    "machine.py", "stage1_oracle.py", "pe.py", "replayfmt.py",
    "replay_model.py", "replay_state.py"))


def source_identity():
    paths = SOURCE_FILES
    result = {}
    for path in paths:
        try:
            result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            result[str(path)] = "unreadable:%s" % error.errno
    return result


def replay_version_tags(replay):
    """Version strings supplied by replay USER metadata, not EXE identity."""
    pattern = rb"(?:^|\r\n)Version ([0-9]+\.[0-9]+[a-z]?)\r\n"
    return sorted({match.decode("ascii") for block in replay.user_blocks
                   for match in re.findall(pattern, block)})


def run_one(replay_path, stage_index, stage, root, cap, sources):
    directory = root / replay_path.stem / ("stage-%02d" % stage.number)
    directory.mkdir(parents=True, exist_ok=False)
    frames = min(stage.frame_count, cap) if cap is not None else stage.frame_count
    cmd = [sys.executable, "-m", "oracle.battle_oracle", "--out-dir", str(directory),
           "--state-v2", "--semantic-state", "--entity-events", "--draw-log", "off", "--replay", str(replay_path),
           "--replay-stage", str(stage_index), "--frames", str(frames)]
    started = time.time()
    replay_bytes = replay_path.read_bytes()
    replay_hash = hashlib.sha256(replay_bytes).hexdigest()
    version_tags = replay_version_tags(ReplayFile().decode(replay_bytes))
    exe_path = Path("games/th13/th13.exe")
    exe_hash = hashlib.sha256(exe_path.read_bytes()).hexdigest()
    snapshot = root / "tool-source"
    env = dict(os.environ, PYTHONPATH=str(snapshot.resolve()))
    with (directory / "run.log").open("w", encoding="utf8") as log:
        code = subprocess.run(cmd, cwd=snapshot, env=env, stdout=log,
                              stderr=subprocess.STDOUT).returncode
    source_unchanged = all(hashlib.sha256((snapshot / name).read_bytes()).hexdigest() == digest
                           for name, digest in sources.items())
    replay_unchanged = hashlib.sha256(replay_path.read_bytes()).hexdigest() == replay_hash
    exe_unchanged = hashlib.sha256(exe_path.read_bytes()).hexdigest() == exe_hash
    if not source_unchanged or not replay_unchanged or not exe_unchanged:
        code = -1
    result = {"replay": str(replay_path), "replay_sha256": replay_hash,
        "replay_unchanged": replay_unchanged,
        "replay_metadata_version_tags": version_tags, "stage": stage.number,
        "exe_sha256": exe_hash, "exe_unchanged": exe_unchanged,
        "stage_index": stage_index, "requested_frames": frames,
        "total_frames": stage.frame_count, "exit_code": code,
        "source_hashes": sources, "source_unchanged": source_unchanged,
        "elapsed_seconds": round(time.time() - started, 3), "boot_seed": 0,
        "resource_identity": str(directory / "resource-identity.json"),
        "reference_qualified": False, "replay_fidelity": "unverified",
        "coverage": "core and partial raw observations; entities, events, ANM incomplete"}
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replays", nargs="+", type=Path,
                        default=sorted(Path("games/replays").glob("*.rpy")))
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--frames", type=int)
    parser.add_argument("--skip-complete", type=Path,
                        help="reuse completed identities from a previous batch")
    args = parser.parse_args()
    if args.jobs < 1 or (args.frames is not None and args.frames < 1):
        parser.error("jobs and frames must be positive")
    args.out = args.out.resolve()
    args.replays = [path.resolve() for path in args.replays]
    args.out.mkdir(parents=True, exist_ok=False)
    sources = source_identity()
    snapshot = args.out / "tool-source"
    (snapshot / "oracle").mkdir(parents=True)
    for path in SOURCE_FILES:
        shutil.copyfile(path, snapshot / path)
    # The Python module resolves to the snapshot. Runtime resources retain
    # the workspace names expected by the unmodified product commands.
    for link in ("games", "artifacts", "eagler-touhou"):
        (snapshot / link).symlink_to(Path.cwd() / link, target_is_directory=True)
    if any(hashlib.sha256((snapshot / name).read_bytes()).hexdigest() != digest
           for name, digest in sources.items()):
        raise RuntimeError("source changed while creating snapshot")
    (args.out / "source-identity.json").write_text(json.dumps(sources, indent=2) + "\n")
    jobs = []
    completed = set()
    if args.skip_complete:
        for result_path in args.skip_complete.glob("*/stage-*/result.json"):
            result = json.loads(result_path.read_text())
            if (result.get("exit_code") == 0 and result.get("source_unchanged")
                    and result.get("exe_unchanged") and result.get("replay_unchanged")):
                completed.add((Path(result["replay"]).name, result["stage"]))
        (args.out / "prior-completed.json").write_text(json.dumps(
            sorted(completed), indent=2) + "\n")
    for path in args.replays:
        replay = ReplayFile().decode(path.read_bytes())
        jobs.extend((path, i, stage, args.out, args.frames, sources)
                    for i, stage in enumerate(replay.stages)
                    if (path.name, stage.number) not in completed)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        pending = {executor.submit(run_one, *job): job for job in jobs}
        for future in as_completed(pending):
            job = pending[future]
            try:
                result = future.result()
            except Exception as error:
                result = {"replay": str(job[0]), "stage": job[2].number,
                          "exit_code": -1, "error": repr(error)}
            results.append(result)
            print(json.dumps(result, sort_keys=True), flush=True)
    (args.out / "manifest.json").write_text(json.dumps(
        sorted(results, key=lambda item: (item["replay"], item["stage"])),
        indent=2) + "\n")
    return int(any(item["exit_code"] != 0 for item in results))


if __name__ == "__main__":
    sys.exit(main())
