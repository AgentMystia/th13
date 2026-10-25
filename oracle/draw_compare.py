"""Streaming draw comparator. Exit 0=strict qualified match, 1=divergence,
2=invalid input, 3=diagnostic match (no expected window, ULP or filtering).
RESULT_JSON v1 precedes the legacy RESULT line; no capture completion is inferred.
"""
import argparse
import itertools
import json
import math
import os
import re
import struct
import sys

FLOAT_ROOTS = {"viewport", "world", "texmat", "view", "proj"}
_VERT_SLOT = re.compile(r"verts\[\d+\]\[(\d+)\]$")


def is_float_path(path):
    m = _VERT_SLOT.search(path)
    return any(s in FLOAT_ROOTS for s in re.split(r"[\[.\]]+", path)) or bool(m and int(m[1]) != 4)


def f32_bits(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def ulp_diff(a, b):
    if math.isnan(a) or math.isnan(b):
        return 0 if math.isnan(a) and math.isnan(b) else math.inf
    if a == b:
        return 0
    if math.isinf(a) or math.isinf(b):
        return math.inf
    def ordered(x):
        bits = f32_bits(x)
        return 0x80000000 - (bits & 0x7fffffff) if bits & 0x80000000 else 0x80000000 + bits
    return abs(ordered(a) - ordered(b))


def cmp_float(a, b, ulp, nan_equal=True):
    fa, fb = float(a), float(b)
    if math.isnan(fa) or math.isnan(fb):
        return nan_equal and math.isnan(fa) and math.isnan(fb)
    return fa == fb if ulp == 0 else ulp_diff(fa, fb) <= ulp


def cmp_value(path, a, b, ulp, nan_equal=True):
    if is_float_path(path) and not isinstance(a, (list, dict)) and not isinstance(b, (list, dict)):
        try:
            equal = cmp_float(a, b, ulp, nan_equal)
        except (ValueError, TypeError, OverflowError):
            equal = False
        return None if equal else f"{path}: {a!r} != {b!r}"
    if isinstance(a, float) and isinstance(b, float):
        return None if cmp_float(a, b, ulp, nan_equal) else f"{path}: {a!r} != {b!r}"
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = cmp_value(f"{path}[{i}]", x, y, ulp, nan_equal)
            if r:
                return r
        return None
    if isinstance(a, dict) and isinstance(b, dict):
        if list(a) != list(b):
            return f"{path}: key order/set differs"
        for k in a:
            r = cmp_value(f"{path}.{k}", a[k], b[k], ulp, nan_equal)
            if r:
                return r
        return None
    return None if type(a) is type(b) and a == b else f"{path}: {a!r} != {b!r}"


def unique_object(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError(f"duplicate JSON key: {k}")
        result[k] = v
    return result


def records(path, draws=True):
    previous = (-1, -1)
    count = 0
    with open(path, encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, 1):
            if not line.strip():
                raise ValueError(f"{path}:{line_number}: blank record")
            obj = json.loads(line, object_pairs_hook=unique_object)
            if not isinstance(obj, dict) or type(obj.get("frame")) is not int or obj["frame"] < 0:
                raise ValueError(f"{path}:{line_number}: invalid frame")
            if draws:
                if obj["frame"] != count:
                    raise ValueError(f"{path}:{line_number}: duplicate/missing/out-of-order frame, expected {count}")
                if not isinstance(obj.get("ops"), list) or not all(isinstance(op, dict) for op in obj["ops"]):
                    raise ValueError(f"{path}:{line_number}: invalid ops")
                if not isinstance(obj.get("viewport"), list):
                    raise ValueError(f"{path}:{line_number}: missing viewport")
            else:
                if type(obj.get("seq")) is not int or obj["seq"] < 0:
                    raise ValueError(f"{path}:{line_number}: invalid seq")
                cur = (obj["frame"], obj["seq"])
                if cur <= previous:
                    raise ValueError(f"{path}:{line_number}: duplicate/out-of-order event")
                previous = cur
            count += 1
            yield obj
    if count == 0:
        raise ValueError(f"{path}: empty input")


def parser(description):
    p = argparse.ArgumentParser(description=description, allow_abbrev=False)
    p.add_argument("port")
    p.add_argument("oracle")
    p.add_argument("--expected-frames", type=int)
    p.add_argument("--ulp", type=int, default=0, help="diagnostic binary32 ULP tolerance")
    return p


def emit(first, window, count, code, kind, ignored=(), error=None, expected=None, ulp=0):
    result = dict(schema="th13/compare/v1", kind=kind, first_divergence_frame=first,
                  window=window, expected_frames=expected, **{kind: count}, exit_code=code,
                  qualified=code == 0, ignored=sorted(ignored), ulp=ulp,
                  capture_completion="not-attested", error=error)
    print("RESULT_JSON " + json.dumps(result, allow_nan=False, sort_keys=True))
    suffix = " ignored=" + ",".join(sorted(ignored)) if ignored else ""
    print(f"RESULT first_divergence_frame={first if first is not None else 'none'} window={window if window is not None else 'unknown'} {kind}={count}{suffix}")
    return code


def main(argv):
    p = parser(__doc__)
    p.add_argument("--ignore", action="append", default=[])
    a = p.parse_args(argv[1:])
    first = None
    count = 0
    window = 0
    try:
        if a.ulp < 0 or (a.expected_frames is not None and a.expected_frames <= 0):
            raise ValueError("ULP must be nonnegative; expected frames must be positive")
        if os.path.samefile(a.port, a.oracle):
            raise ValueError("input aliases: port and oracle must be distinct files")
        totals = [0, 0]
        lengths = [0, 0]
        for pf, of in itertools.zip_longest(records(a.port), records(a.oracle)):
            frame = window
            window += 1
            for i, obj in enumerate((pf, of)):
                if obj is not None:
                    lengths[i] += 1
                    totals[i] += len(obj["ops"])
            if first is not None:
                continue
            r = None
            if pf is None or of is None:
                r = "frame missing on one side"
            else:
                def filtered(obj):
                    return {**obj, "ops": [{k: v for k, v in op.items() if k not in a.ignore} for op in obj["ops"]]}
                r = cmp_value("frame", filtered(pf), filtered(of), a.ulp)
            if r:
                first = frame
                print(f"first divergence at frame {frame}: {r}")
        count = max(totals)
        if a.expected_frames is not None and lengths != [a.expected_frames] * 2:
            raise ValueError(f"expected {a.expected_frames} frames on both sides, got {lengths}")
        code = 1 if first is not None else 3 if a.ignore or a.ulp or a.expected_frames is None else 0
        return emit(first, window, count, code, "ops", a.ignore, expected=a.expected_frames, ulp=a.ulp)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        print(str(exc), file=sys.stderr)
        return emit(first, window, count, 2, "ops", a.ignore, str(exc), a.expected_frames, a.ulp)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
