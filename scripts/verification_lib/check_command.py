#!/usr/bin/env python3
"""TOOL_PASS-only adapter for standard commands; no shell or fidelity promotion.

Usage: python3 scripts/verification_lib/check_command.py -- python3 -m unittest ...
The parent runner captures logs, owns timeout/process groups and checks identity.
"""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        return 2
    argv = sys.argv[2:]
    if Path(argv[0]).name in {"sh", "bash", "zsh", "podman", "docker"}:
        return 2
    code = subprocess.run(argv).returncode
    result = dict(schema=1, run_id=os.environ["VERIFY_RUN_ID"],
                  check_id=os.environ["VERIFY_CHECK_ID"], identity=os.environ["VERIFY_IDENTITY"],
                  type="TOOL_PASS" if code == 0 else "FAIL", passed=code == 0,
                  summary="Process exit only; not game/reference fidelity or independent review",
                  ignored=[], evidence=[])
    path = Path(os.environ["VERIFY_RESULT_PATH"])
    with path.open("x") as f:
        json.dump(result, f)
        f.flush()
        os.fsync(f.fileno())
    return code if 0 <= code <= 255 else 1


if __name__ == "__main__":
    sys.exit(main())
