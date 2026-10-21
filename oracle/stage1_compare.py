"""stage1 事件流逐帧逐字段比对（event-contract.md §1：两侧产出按行比对）。

对 <wasi.jsonl>（移植侧）与 <oracle.jsonl>（原版侧）逐事件配对比较：
按键序比字段名，按类型比值（浮点默认 %.9g 位级相等，可选 ULP 容差）。
行数不齐、frame/seq 乱序均计为分歧。

输出：逐条分歧明细（至多 20 条），末行固定
    RESULT first_divergence_frame=<n|none> window=3600 events=<数>
events = 完成比对的事件对数（发现分歧时含该对；无分歧时为公共对数）。
退出码：0 = 全程一致；1 = 存在分歧；2 = 输入无法解析。

用法（容器内 /work）：
    python3 -m oracle.stage1_compare <wasi.jsonl> <oracle.jsonl> [--ulp N]
"""
import json
import struct
import sys

WINDOW = 3600
MAX_REPORT = 20


def f32_bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def ulp_distance(a, b):
    """两个 binary32 的 ULP 距离（符号展开；NaN/Inf 返回 None 表示不可比）。"""
    ia, ib = f32_bits(f32(a)), f32_bits(f32(b))
    if ia >> 31:
        ia = 0x80000000 - (ia & 0x7FFFFFFF) - 1 if ia != 0x80000000 else -0x80000000
    if ib >> 31:
        ib = 0x80000000 - (ib & 0x7FFFFFFF) - 1 if ib != 0x80000000 else -0x80000000
    return abs(ia - ib)


def is_float(value):
    return isinstance(value, float)


def compare_event(a, b, ulp):
    """返回字段差异列表 [(key, a, b, reason)]；空列表即一致。"""
    diffs = []
    keys_a, keys_b = list(a.keys()), list(b.keys())
    if keys_a != keys_b:
        diffs.append(("keys", keys_a, keys_b, "key order/set"))
        return diffs
    for key in keys_a:
        va, vb = a[key], b[key]
        if is_float(va) and is_float(vb):
            if f32(va) == f32(vb):
                continue
            if ulp:
                dist = ulp_distance(va, vb)
                if dist is not None and dist <= ulp:
                    continue
            diffs.append((key, va, vb, "float differ"))
        elif isinstance(va, list) and isinstance(vb, list):
            if len(va) != len(vb):
                diffs.append((key, va, vb, "args length"))
                continue
            for i, (xa, xb) in enumerate(zip(va, vb)):
                if is_float(xa) and is_float(xb):
                    if f32(xa) == f32(xb):
                        continue
                    if ulp:
                        dist = ulp_distance(xa, xb)
                        if dist is not None and dist <= ulp:
                            continue
                    diffs.append((f"{key}[{i}]", xa, xb, "float differ"))
                elif xa != xb or type(xa) is not type(xb):
                    diffs.append((f"{key}[{i}]", xa, xb, "value/type differ"))
        elif va != vb or type(va) is not type(vb):
            diffs.append((key, va, vb, "value/type differ"))
    return diffs


def check_order(events, side, problems):
    last = (-1, -1)
    for i, ev in enumerate(events):
        cur = (ev.get("frame", -1), ev.get("seq", -1))
        if cur <= last:
            problems.append((side, i, "order", str(last), str(cur)))
        last = cur


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    ulp = 0
    if "--ulp" in argv:
        ulp = int(argv[argv.index("--ulp") + 1])
    if len(args) != 2:
        print(__doc__)
        return 2

    streams = []
    for path in args:
        events = []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as exc:
            sys.stderr.write("cannot parse %s: %s\n" % (path, exc))
            return 2
        streams.append(events)
    a_events, b_events = streams
    if not a_events or not b_events:
        sys.stderr.write("empty input: %s=%d events, %s=%d events\n" % (
            args[0], len(a_events), args[1], len(b_events)))
        return 2

    problems = []
    check_order(a_events, "wasi", problems)
    check_order(b_events, "oracle", problems)

    first_divergence = None
    compared = 0
    reports = []
    for i in range(max(len(a_events), len(b_events))):
        a = a_events[i] if i < len(a_events) else None
        b = b_events[i] if i < len(b_events) else None
        if a is None or b is None:
            frame = (a or b).get("frame", "?")
            detail = ("missing on wasi side" if a is None
                      else "missing on oracle side")
            reports.append("line %d frame %s: %s" % (i + 1, frame, detail))
            if first_divergence is None:
                first_divergence = frame
            compared = i + 1
            break
        compared = i + 1
        diffs = compare_event(a, b, ulp)
        if diffs:
            frame = a.get("frame", "?")
            if first_divergence is None:
                first_divergence = frame
            for key, va, vb, reason in diffs[:4]:
                reports.append("line %d frame %s: %s %s: wasi=%r oracle=%r" % (
                    i + 1, frame, reason, key, va, vb))
        if first_divergence is not None and len(reports) >= MAX_REPORT:
            break

    for side, i, reason, xa, xb in problems[:MAX_REPORT]:
        reports.append("%s line %d: %s %s -> %s" % (side, i + 1, reason, xa, xb))
        if first_divergence is None:
            first_divergence = "?"
    for line in reports[:MAX_REPORT]:
        print(line)
    frame_str = ("none" if first_divergence is None else str(first_divergence))
    print("RESULT first_divergence_frame=%s window=%d events=%d" % (
        frame_str, WINDOW, compared))
    return 0 if first_divergence is None else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
