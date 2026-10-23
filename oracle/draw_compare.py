"""绘制清单比对：移植侧 vs 原版侧 逐帧逐字段（draw-contract.md）。

用法（容器内 /work）：
    python3 -m oracle.draw_compare <port.jsonl> <oracle.jsonl> [--ulp N]
    python3 -m oracle.draw_compare <port.jsonl> <oracle.jsonl> --ignore <field>

范式对齐 stage1_compare.py：末行
    RESULT first_divergence_frame=<n|none> window=<帧数> ops=<数> [ignored=<字段,...>]
为唯一裁决。浮点字段按 ULP 容差（默认 4），其余字段精确比对。

--ignore 纪律（整改 2026-10-23，draw-contract.md §6）：
  * --ignore 是**诊断模式**开关，仅限隔离工作面用（定位「除 X 外是否还有
    其他分歧」）；绿灯/门禁链路**禁止**带 --ignore 出 RESULT。
  * 带 --ignore 的输出：RESULT 行尾强制追加 ignored=<字段> 且 exit code=3
    （与无过滤 0/1 区分），避免被误当全绿。
  * 每个 --ignore 的字段必须在 HANDOFF/工作面登记销账条件与期限。
"""
import json
import math
import re
import struct
import sys

# 契约 §2/§3：这些路径下的元素为 %.9g 浮点串（按 ULP 容差比）；
# verts[i][4]=argb 为 u32 整数（精确比）。
FLOAT_ROOTS = {"viewport", "world", "texmat", "view", "proj"}
_VERT_SLOT = re.compile(r"verts\[\d+\]\[(\d+)\]$")


def is_float_path(path):
    # 路径任一段命中浮点根（如 "op[3].world[8]" 的 "world"）
    segs = re.split(r"[\[.\]]+", path)
    if any(seg in FLOAT_ROOTS for seg in segs):
        return True
    m = _VERT_SLOT.search(path)
    if m:
        return int(m.group(1)) != 4
    return False


def f32_bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def ulp_diff(a, b):
    """两个 float 的 ULP 差（NaN==NaN 视为相等）。"""
    if math.isnan(a) and math.isnan(b):
        return 0
    if math.isnan(a) or math.isnan(b):
        return 1 << 30
    ia, ib = f32_bits(a), f32_bits(b)
    if ia < 0:
        ia = 0x80000000 - ia   # 有符号序
    if ib < 0:
        ib = 0x80000000 - ib
    return abs(ia - ib)


def cmp_float(a, b, ulp):
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    if math.isnan(fa) or math.isnan(fb):
        return (math.isnan(fa) and math.isnan(fb)) or a == b
    return ulp_diff(fa, fb) <= ulp


def cmp_value(path, a, b, ulp):
    """返回 None（相等）或分歧描述。"""
    if isinstance(a, str) and isinstance(b, str) and is_float_path(path):
        return None if cmp_float(a, b, ulp) else "%s: %r != %r" % (path, a, b)
    if isinstance(a, float) or isinstance(b, float):
        return None if cmp_float(a, b, ulp) else "%s: %r != %r" % (path, a, b)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return "%s: len %d != %d" % (path, len(a), len(b))
        for i, (x, y) in enumerate(zip(a, b)):
            r = cmp_value("%s[%d]" % (path, i), x, y, ulp)
            if r:
                return r
        return None
    if isinstance(a, dict) and isinstance(b, dict):
        ka, kb = list(a.keys()), list(b.keys())
        if ka != kb:
            return "%s: keys %s != %s" % (path, ka, kb)
        for k in ka:
            r = cmp_value("%s.%s" % (path, k), a[k], b[k], ulp)
            if r:
                return r
        return None
    return None if a == b else "%s: %r != %r" % (path, a, b)


def load(path):
    frames = {}
    ops = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            frames[obj["frame"]] = obj
            ops += len(obj.get("ops", []))
    return frames, ops


def main(argv):
    args = []
    ulp = 4
    ignore = set()
    i = 1
    while i < len(argv):
        if argv[i] == "--ulp":
            i += 1
            ulp = int(argv[i])
        elif argv[i] == "--ignore":
            i += 1
            ignore.add(argv[i])  # 诊断模式：跳过字段（纪律见模块 docstring）
        else:
            args.append(argv[i])
        i += 1
    if len(args) != 2:
        sys.stderr.write("usage: draw_compare <port.jsonl> <oracle.jsonl> "
                         "[--ulp N] [--ignore field]\n")
        return 2
    port_frames, port_ops = load(args[0])
    oracle_frames, oracle_ops = load(args[1])
    window = max(max(port_frames), max(oracle_frames)) + 1
    total_ops = max(port_ops, oracle_ops)

    first_div = None
    for frame in range(window):
        pf = port_frames.get(frame)
        of = oracle_frames.get(frame)
        if pf is None and of is None:
            continue
        if pf is None or of is None:
            first_div = (frame, "frame-missing: %s side" %
                         ("port" if pf is None else "oracle"))
            break
        po = pf.get("ops", [])
        oo = of.get("ops", [])
        r = cmp_value("viewport", pf.get("viewport"), of.get("viewport"), ulp)
        if r:
            first_div = (frame, r)
            break
        if len(po) != len(oo):
            first_div = (frame, "ops len %d != %d" % (len(po), len(oo)))
            break
        for k, (a, b) in enumerate(zip(po, oo)):
            if ignore:
                a = {key: value for key, value in a.items() if key not in ignore}
                b = {key: value for key, value in b.items() if key not in ignore}
            r = cmp_value("op[%d]" % k, a, b, ulp)
            if r:
                first_div = (frame, r)
                break
        if first_div:
            break

    if first_div:
        print("first divergence at frame %d: %s" % first_div)
        print("RESULT first_divergence_frame=%d window=%d ops=%d%s"
              % (first_div[0], window, total_ops, ignored_suffix(ignore)))
        return 1
    print("RESULT first_divergence_frame=none window=%d ops=%d%s"
          % (window, total_ops, ignored_suffix(ignore)))
    # 诊断模式（带 --ignore）永远不算绿灯：exit 3 与无过滤的 0 区分，
    # 门禁脚本按 code 判定，杜绝「过滤后全绿」被当真绿（整改纪律）。
    return 3 if ignore else 0


def ignored_suffix(ignore):
    # RESULT 行尾的显式登记：被过滤字段全名列出（整改 2026-10-23）。
    return " ignored=%s" % ",".join(sorted(ignore)) if ignore else ""


if __name__ == "__main__":
    sys.exit(main(sys.argv))
