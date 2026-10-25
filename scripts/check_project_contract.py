"""Check the durable entry points and unchanged shared rendering substrate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "eagler-touhou/ports/th13"


def check(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    port = root / "eagler-touhou/ports/th13"
    required = [
        "AGENTS.md", "HANDOFF.md", "scripts/verify.py",
        "verification/README.md", "verification/policy.json", "verification/tasks.json",
        "docs/plans/autonomous-refactor-2026-10-24.md",
        "eagler-touhou/AGENTS.md", "eagler-touhou/ports/th13/AGENTS.md",
    ]
    for name in required:
        if not (root / name).is_file():
            errors.append(f"missing entry: {name}")
    for name in ["AGENTS.md", "eagler-touhou/ports/th13/AGENTS.md"]:
        path = root / name
        if path.is_file():
            text = path.read_text()
            for token in ["scripts/verify.py", "resume", "BLOCKED"]:
                if token not in text:
                    errors.append(f"{name}: missing entry contract {token}")
    for name in ["Renderer.cpp", "Renderer.hpp", "Shaders.hpp", "GraphicsState.hpp",
                 "RenderCommands.hpp", "LegacyGraphics.hpp"]:
        a = port / "portable/sdl" / name
        b = root / "eagler-touhou/ports/th15/portable/sdl" / name
        if not a.is_file() or not b.is_file():
            errors.append(f"shared renderer file missing: {name}")
        elif a.read_bytes() != b.read_bytes():
            errors.append(f"unregistered shared renderer divergence: {name}")
    # Original game dumps are evidence, not product source assets.
    banned = port / "docs/plans/ins408-oracle-dump.txt"
    if banned.exists():
        errors.append("original diagnostic dump returned to product repository")
    return errors


def main() -> int:
    errors = check()
    print(json.dumps({"schema": "th13/project-contract/v1",
                      "status": "FAIL" if errors else "TOOL_PASS",
                      "errors": errors,
                      "scope": "entry points and shared backend identity; not game fidelity"},
                     ensure_ascii=False))
    if os.environ.get("VERIFY_RESULT_PATH"):
        receipt = {"schema": 1, "run_id": os.environ["VERIFY_RUN_ID"],
                   "check_id": os.environ["VERIFY_CHECK_ID"],
                   "identity": os.environ["VERIFY_IDENTITY"],
                   "type": "TOOL_PASS" if not errors else "FAIL", "passed": not errors,
                   "summary": "entry points and shared backend identity; not game fidelity",
                   "ignored": [], "evidence": []}
        Path(os.environ["VERIFY_RESULT_PATH"]).write_text(json.dumps(receipt) + "\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
