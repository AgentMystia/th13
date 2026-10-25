"""Streaming sparse event comparison. Window is explicit --expected-frames or
unknown, never inferred from the last sparse event. Expected window is a caller
assertion, NOT evidence of capture completion. Exit codes match draw_compare.
"""
import itertools
import os
import sys
from oracle.draw_compare import cmp_value, emit, parser, records, ulp_diff, f32_bits


def ulp_distance(a, b):
    return ulp_diff(a, b)


def compare_event(a, b, ulp):
    # Unlike draw-contract's explicit NaN equality, event NaNs never match.
    diff = cmp_value("event", a, b, ulp, nan_equal=False)
    return [] if diff is None else [("event", a, b, diff)]


def main(argv):
    a = parser(__doc__).parse_args(argv[1:])
    first = None
    count = 0
    try:
        if a.ulp < 0 or (a.expected_frames is not None and a.expected_frames <= 0):
            raise ValueError("ULP must be nonnegative; expected frames must be positive")
        if os.path.samefile(a.port, a.oracle):
            raise ValueError("input aliases: port and oracle must be distinct files")
        for port, oracle in itertools.zip_longest(records(a.port, False), records(a.oracle, False)):
            count += 1
            for obj in (port, oracle):
                if obj is not None and a.expected_frames is not None and obj["frame"] >= a.expected_frames:
                    raise ValueError("event outside expected window")
            if first is None and (port is None or oracle is None or compare_event(port, oracle, a.ulp)):
                first = min(obj["frame"] for obj in (port, oracle) if obj is not None)
                print(f"first divergence at frame {first}, event {count}")
        code = 1 if first is not None else 3 if a.ulp or a.expected_frames is None else 0
        return emit(first, a.expected_frames, count, code, "events", expected=a.expected_frames, ulp=a.ulp)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        print(str(exc), file=sys.stderr)
        return emit(first, a.expected_frames, count, 2, "events", error=str(exc), expected=a.expected_frames, ulp=a.ulp)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
