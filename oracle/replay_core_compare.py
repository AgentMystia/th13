"""Strict core-v2 comparison. Passing core never certifies complete state."""
import argparse
import itertools
import json
from pathlib import Path

GLOBAL_KEYS = {f'{address:08x}' for address in (
    0x4be7b8, 0x4be7c0, 0x4be7c4, 0x4be7c8, 0x4be7d0, 0x4be7d4,
    0x4be7dc, 0x4be7e4, 0x4be7e8, 0x4be7ec, 0x4be7f0, 0x4be7f4,
    0x4be7f8, 0x4be7fc, 0x4be800, 0x4be804, 0x4be808, 0x4be810, 0x4be814)}

def validate(row, frame, stage):
    if set(row) != {'schema', 'frame', 'stage', 'input', 'globals', 'player', 'rng'}:
        raise ValueError('missing or unknown top-level fields')
    if row['schema'] != 'th13-replay-core-v2' or type(row['frame']) is not int or row['frame'] != frame:
        raise ValueError(f'frame sequence: expected {frame}, got {row.get("frame")}')
    if type(row['stage']) is not int or row['stage'] != stage:
        raise ValueError('stage mismatch')
    shapes = {'input': {'held', 'pressed', 'released'}, 'globals': GLOBAL_KEYS,
              'player': {'x_bits', 'y_bits'},
              'rng': {'game_seed', 'game_index', 'visual_seed', 'visual_index'}}
    for name, keys in shapes.items():
        if not isinstance(row[name], dict) or set(row[name]) != keys:
            raise ValueError(f'{name}: missing or unknown fields')
        for key, value in row[name].items():
            if type(value) is not int or not 0 <= value <= 0xffffffff:
                raise ValueError(f'{name}.{key}: expected u32')

def compare(original, web, frames, stage):
    if Path(original).samefile(web):
        raise ValueError('independent streams required')
    first = None
    mismatches = 0
    count = 0
    with open(original) as left, open(web) as right:
        for frame, pair in enumerate(itertools.zip_longest(left, right)):
            if None in pair:
                raise ValueError(f'capture length mismatch at {frame}')
            a, b = (json.loads(line) for line in pair)
            validate(a, frame, stage); validate(b, frame, stage)
            for group in ('input', 'globals', 'player', 'rng'):
                for field in sorted(a[group]):
                    if a[group][field] != b[group][field]:
                        mismatches += 1
                        if first is None:
                            first = {'frame': frame, 'field': f'{group}.{field}',
                                     'original': a[group][field], 'web': b[group][field]}
            count += 1
    if count != frames:
        raise ValueError(f'incomplete window: expected {frames}, got {count}')
    return {'schema': 'th13/replay-core-comparison/v2', 'frames': count, 'stage': stage,
            'firstDifference': first, 'mismatches': mismatches, 'coreEqual': first is None,
            'completeStateCertified': False}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('original'); p.add_argument('web')
    p.add_argument('--frames', type=int, required=True); p.add_argument('--stage', type=int, required=True)
    a = p.parse_args()
    try:
        result = compare(a.original, a.web, a.frames, a.stage)
        code = int(not result['coreEqual'])
    except (ValueError, OSError, TypeError) as error:
        result, code = {'error': str(error), 'completeStateCertified': False}, 2
    print(json.dumps(result, sort_keys=True))
    return code

if __name__ == '__main__':
    raise SystemExit(main())
