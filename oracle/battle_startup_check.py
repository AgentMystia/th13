"""普通 Stage1 冒烟窗的场景断言；捕获成功不等于与移植侧一致。"""
import json
import sys


def inspect_startup(path):
    frames = []
    first_enemy = None
    premature_boss = []
    ops = 0
    with open(path, encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            frame = row["frame"]
            if frame >= 600:
                break
            frames.append(frame)
            ops += len(row["ops"])
            for op in row["ops"]:
                tex = op.get("tex")
                name = tex[2] if tex else ""
                if name == "enemy/enemy.png" and first_enemy is None:
                    first_enemy = frame
                if (name.startswith("face/enemy1/") or
                        name.startswith("stgenm/stage01/cardbg")):
                    premature_boss.append(frame)
    errors = []
    if frames != list(range(600)):
        errors.append("expected contiguous frames 0..599")
    if first_enemy != 61:
        errors.append("first ordinary enemy frame must be 61, got %s" % first_enemy)
    if premature_boss:
        errors.append("unexpected boss assets before frame 600, first=%d"
                      % min(premature_boss))
    return dict(frames=len(frames), ops=ops, first_enemy_frame=first_enemy,
                premature_boss_frame=min(premature_boss) if premature_boss else None,
                errors=errors)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: python3 -m oracle.battle_startup_check draws.jsonl\n")
        return 2
    stats = inspect_startup(argv[1])
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    print("STARTUP status=%s scope=normal-stage1-first600"
          % ("failed" if stats["errors"] else "complete"))
    return int(bool(stats["errors"]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
