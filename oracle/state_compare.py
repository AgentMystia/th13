"""对局状态流（th13-state-raw-v1）逐帧严格比较器（r3 lockstep 门禁）。

比较对象：oracle/battle_oracle.py（TH13_STATE_LOG=1，真实 th13.exe Unicorn 执行）
与 port harness replay 模式（tests/harness/harness.cpp，TH13_STATE_LOG=<guest 路径>）
各自产出的 JSONL 状态行。本文件只新增，不改 oracle/ 既有文件。

契约（本比较器即版本化契约正文，无容差）：
  - 行 = {"schema":"th13-state-raw-v1","frame":int,...}；schema 必须逐字节同名，
    frame 为对局帧号（oracle 相位：-1 = scene7 首帧/装载趟对应物，0 起每帧一行）。
  - 帧序列两侧必须完全一致：缺失/多余/错位一律判失败（首差帧 = 第一个失配处）。
  - float 字段（player_position）按 IEEE binary32 位型比较（含 ±0 区分；
    NaN 按位型比较，载荷不同即分歧）。整数字段按 int 精确比较；keys 按列表
    逐项精确比较。全部无容差、无 --ulp、无 --ignore。
  - PORT_UNMAPPED 字段（port 无公开状态来源，port 侧必须输出 null）：
      spell_id（原版 DAT_004be7d4 符卡练习选择）、
      raw_enemy_manager（原版敌机管理器 0x004C2188 +0x24/+0x28/+0x2C，身份未证）、
      raw_globals["004be814"]（hi-score 续关数，port SessionState 无对应字段）。
    这些字段不进入数值比较（单侧 null ≠ 分歧，登记于 RESULT port_null_fields）；
    port 侧若输出非 null 则按精确比较执行（port 补齐来源后自动纳入裁决）。
    除上述外，行内出现契约外字段、或映射字段缺失，一律 schema 错误（exit 2）。

输出：RESULT_JSON v1 行 + RESULT 行。退出码：0=严格一致，1=分歧（含帧序列
失配，field=frame-sequence），2=输入/schema 无效。stdin 无输入、文件相同
（samefile）、空文件均 exit 2。
"""
import itertools
import json
import os
import struct
import sys

SCHEMA = "th13-state-raw-v1"

# 映射字段（进入精确比较）：字段路径 -> 类型标记。
INT_FIELDS = ("power", "power_step")
RAW_GLOBAL_MAPPED = ("004be7c0", "004be7c8", "004be810")
# port 侧无公开状态来源、必须为 null 的字段（见模块 docstring；port 补齐
# 来源后把对应项移出本表即自动纳入严格比较）。
PORT_UNMAPPED = ("spell_id", "raw_enemy_manager", "raw_globals.004be814")


def f32_bits(value):
    return struct.unpack("<I", struct.pack("<f", float(value)))[0]


def cmp_field(path, a, b):
    """精确比较；返回 None（一致）或分歧描述串。"""
    if path == "player_position":
        if not isinstance(a, list) or not isinstance(b, list) or len(a) != 2 or len(b) != 2:
            return f"{path}: shape {a!r} != {b!r}"
        # 逐分量位型比较（±0/NaN 载荷区分）。
        for i, (x, y) in enumerate(zip(a, b)):
            if f32_bits(x) != f32_bits(y):
                return (f"{path}[{i}]: f32 bits {f32_bits(x):08x} != "
                        f"{f32_bits(y):08x} ({x!r} != {y!r})")
        return None
    if path == "keys":
        if not isinstance(a, list) or not isinstance(b, list):
            return f"{path}: {a!r} != {b!r}"
        norm_a = [int(v) for v in a]
        norm_b = [int(v) for v in b]
        return None if norm_a == norm_b else f"{path}: {norm_a} != {norm_b}"
    if path in INT_FIELDS or (path.startswith("raw_globals.") and
                              path.split(".", 1)[1] in RAW_GLOBAL_MAPPED):
        if isinstance(a, bool) or isinstance(b, bool) or \
                not isinstance(a, int) or not isinstance(b, int):
            return f"{path}: {a!r} != {b!r}"
        return None if a == b else f"{path}: {a!r} != {b!r}"
    # 通用严格分支（PORT_UNMAPPED 字段在 port 输出非 null 时的精确比较）：
    # 类型 + 值全等，dict 按键集与逐值递归。
    if type(a) is not type(b):
        return f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict):
        if set(a) != set(b):
            return f"{path}: keys {sorted(a)} != {sorted(b)}"
        for key in a:
            diff = cmp_field(f"{path}.{key}", a[key], b[key])
            if diff is not None:
                return diff
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            diff = cmp_field(f"{path}[{i}]", x, y)
            if diff is not None:
                return diff
        return None
    return None if a == b else f"{path}: {a!r} != {b!r}"


def row_fields(row):
    """行 -> {字段路径: 值}；契约外字段/缺字段抛 ValueError。"""
    if not isinstance(row, dict):
        raise ValueError("row is not an object")
    if row.get("schema") != SCHEMA:
        raise ValueError(f"schema {row.get('schema')!r} != {SCHEMA!r}")
    if type(row.get("frame")) is not int:
        raise ValueError("invalid frame")
    fields = {}
    for key in ("player_position", "power", "power_step", "keys"):
        if key not in row:
            raise ValueError(f"missing mapped field {key}")
        fields[key] = row[key]
    raw_globals = row.get("raw_globals")
    if not isinstance(raw_globals, dict):
        raise ValueError("raw_globals missing")
    for address in RAW_GLOBAL_MAPPED:
        if address not in raw_globals:
            raise ValueError(f"raw_globals.{address} missing")
        fields[f"raw_globals.{address}"] = raw_globals[address]
    for path in PORT_UNMAPPED:
        node = row
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        fields[path] = node
    known = {"schema", "frame", "player_position", "power", "power_step",
             "spell_id", "keys", "raw_globals", "raw_enemy_manager"}
    if set(row) - known:
        raise ValueError(f"unknown fields {sorted(set(row) - known)}")
    if set(raw_globals) - {"004be7c0", "004be7c8", "004be810", "004be814"}:
        raise ValueError(f"unknown raw_globals keys {sorted(raw_globals)}")
    return fields


def read_rows(path):
    rows = []
    previous = None
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                raise ValueError(f"{path}:{line_number}: blank record")
            row = json.loads(line)
            fields = row_fields(row)
            frame = row["frame"]
            # 帧序必须严格递增；缺失/多余（跳号）不作 schema 错——留给比较
            # 主循环判 frame-sequence 分歧（首差帧可定位），错位/倒序仍拒收。
            if previous is not None and frame <= previous:
                raise ValueError(f"{path}:{line_number}: frame {frame} not increasing after {previous}")
            previous = frame
            rows.append((frame, fields))
    if not rows:
        raise ValueError(f"{path}: empty input")
    return rows


def emit(first_frame, first_field, count, code, port_null, error=None):
    result = dict(schema="th13/state-compare/v1", kind="state",
                  first_divergence_frame=first_frame, field=first_field,
                  frames=count, port_null_fields=port_null, exit_code=code,
                  error=error)
    print("RESULT_JSON " + json.dumps(result, allow_nan=False, sort_keys=True))
    print(f"RESULT first_divergence_frame="
          f"{first_frame if first_frame is not None else 'none'} "
          f"field={first_field if first_field is not None else 'none'} "
          f"frames={count} port_null_fields={','.join(port_null) or '-'}")
    return code


def main(argv):
    args = argv[1:]
    if len(args) != 2:
        print("usage: python3 -m oracle.state_compare <port.jsonl> <oracle.jsonl>",
              file=sys.stderr)
        return emit(None, None, 0, 2, (), "expected exactly two inputs")
    port_path, oracle_path = args
    try:
        if os.path.samefile(port_path, oracle_path):
            raise ValueError("input aliases: port and oracle must be distinct files")
        port_rows = read_rows(port_path)
        oracle_rows = read_rows(oracle_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return emit(None, None, 0, 2, (), str(exc))

    count = 0
    for (pf, pfields), (of, ofields) in itertools.zip_longest(
            port_rows, oracle_rows):
        if pf is None or of is None:
            missing_side = "port" if pf is None else "oracle"
            frame = pf if pf is not None else of
            print(f"frame sequence mismatch at frame {frame}: {missing_side} "
                  f"row missing", file=sys.stderr)
            return emit(frame, "frame-sequence", count, 1, PORT_UNMAPPED)
        if pf != of:
            print(f"frame misalignment: port {pf} vs oracle {of}", file=sys.stderr)
            return emit(min(pf, of), "frame-sequence", count, 1, PORT_UNMAPPED)
        count += 1
        for path in ("player_position", "power", "power_step", "keys",
                     "raw_globals.004be7c0", "raw_globals.004be7c8",
                     "raw_globals.004be810"):
            diff = cmp_field(path, pfields[path], ofields[path])
            if diff is not None:
                print(f"first divergence at frame {pf}: {diff}", file=sys.stderr)
                return emit(pf, path, count, 1, PORT_UNMAPPED)
        # PORT_UNMAPPED：port 必须 null（否则有值即精确比较——port 补齐来源
        # 后自动纳入裁决；oracle 侧值不进入裁决面，分歧面 = 映射字段）。
        for path in PORT_UNMAPPED:
            if pfields[path] is not None:
                diff = cmp_field(path, pfields[path], ofields[path])
                if diff is not None:
                    print(f"first divergence at frame {pf}: {diff}", file=sys.stderr)
                    return emit(pf, path, count, 1, PORT_UNMAPPED)
    return emit(None, None, count, 0, PORT_UNMAPPED)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
