"""Exercise the real runner's refusal and resume paths without changing source."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    verify = ROOT / "scripts/verify.py"
    spec = importlib.util.spec_from_file_location("th13_verification", verify)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runner = module.Runner(ROOT)
    state = runner.state
    prerequisite = state["tasks"]["submission-boundary"]
    if prerequisite["status"] != "DONE":
        raise RuntimeError("production submission prerequisite is not certified")
    runner.validate_full("submission-boundary", prerequisite["receipt"])
    if state["tasks"]["stage1-full-chain"]["status"] == "DONE":
        runner.validate_full("stage1-full-chain", state["tasks"]["stage1-full-chain"]["receipt"])
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "scripts",
                            "-p", "test_verify.py"], cwd=ROOT)
    if tests.returncode:
        return tests.returncode
    if os.environ.get("VERIFY_RESULT_PATH"):
        record = dict(schema=1, run_id=os.environ["VERIFY_RUN_ID"],
                      check_id=os.environ["VERIFY_CHECK_ID"], identity=os.environ["VERIFY_IDENTITY"],
                      type="TOOL_PASS", passed=True,
                      summary="Resume paths and refusal controls; actual model execution is verified by the workflow runtime, not by this receipt",
                      ignored=[], evidence=[])
        with Path(os.environ["VERIFY_RESULT_PATH"]).open("x") as out:
            json.dump(record, out)
    print(json.dumps({"schema": "th13/handoff-drill/v1", "refusal_controls_exercised_by_tests": True,
                      "tool_tests_exit": tests.returncode, "framework_effectiveness": "UNVERIFIED"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
