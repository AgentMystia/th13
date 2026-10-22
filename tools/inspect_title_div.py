#!/usr/bin/env python3
"""标题窗分歧情报采集：@R 边条几何、sig 半图层级、批次密度概览。"""
import json
import sys


def frame(p, n):
    with open(p) as f:
        for i, line in enumerate(f):
            if i == n:
                return json.loads(line)
    raise SystemExit(f"frame {n} not found in {p}")


def show(o):
    s = json.dumps(o, ensure_ascii=False)
    print("  " + s[:1200])


def brief(o):
    return "%s L%s %s n=%s" % (o["op"], o["layer"], o["tex"][2], o.get("count"))


def main():
    pf = "/work/artifacts/port-draws/title-draws.jsonl"
    of = "/work/artifacts/oracle-draws/title-draws.jsonl"
    keyframes = [0, 180, 300, 360, 400, 450, 469, 470, 480, 500, 550, 599]
    P, O = {}, {}
    for n in keyframes:
        P[n] = frame(pf, n)
        O[n] = frame(of, n)
    for n in keyframes:
        print("== 帧 %d" % n)
        print("  port(%d): %s" % (len(P[n]["ops"]), " | ".join(brief(o) for o in P[n]["ops"])))
        print("  orac(%d): %s" % (len(O[n]["ops"]), " | ".join(brief(o) for o in O[n]["ops"])))

    print("== oracle 帧400 title01 op 完整（菜单字条批次）")
    for o in O[400]["ops"]:
        if o["tex"][2] == "title01.png":
            show(o)
    print("== port 帧400 title01 op 完整")
    for o in P[400]["ops"]:
        if o["tex"][2] == "title01.png":
            show(o)


if __name__ == "__main__":
    main()
