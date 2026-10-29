"""Compare actual cross-slot Replay input samples; never relabel local frames.

Idle host ticks are preserved and counted separately. This comparison certifies
only its declared core/input projection; it does not compare idle rendering or
qualify the original device model. Existing fixed-stage comparisons are unchanged.
"""
import argparse
import itertools
import json
from pathlib import Path

from oracle.replay_core_compare import validate


def lines(path):
    with Path(path).open() as stream:
        for number, line in enumerate(stream):
            try:
                yield json.loads(line)
            except (ValueError, TypeError) as error:
                raise ValueError(f'{path}: invalid JSON row {number}') from error


def web_inputs(path):
    samples = 0
    previous = None
    for host_tick, row in enumerate(lines(path)):
        if row.get('schema') != 'th13/web-continuous-state/v1' or row.get('host_tick') != host_tick:
            raise ValueError('Web host-tick sequence/schema mismatch')
        before, after = row['before'], row['after']
        if previous is not None and before != previous:
            raise ValueError('Web cursor state discontinuity between host ticks')
        previous = after
        if row['event'] != 'input':
            if row['event'] != 'idle' or row.get('sample_index') is not None:
                raise ValueError('Web unqualified/eof tick in requested input window')
            if after['recorded_position'] != before['recorded_position']:
                raise ValueError('Web idle tick advanced the input cursor')
            if after['slot_index'] != before['slot_index'] or after['slot_frame'] != before['slot_frame']:
                raise ValueError('Web idle tick changed the active slot')
            continue
        if row.get('sample_index') != samples:
            raise ValueError('Web input sample sequence mismatch')
        if after['recorded_position'] != before['recorded_position'] + 1:
            raise ValueError('Web input cursor did not advance exactly once')
        if after['consumed'] != after['slot_frame'] or after['slot_frame'] < 1:
            raise ValueError('Web consumed/local-frame mismatch')
        yield row
        samples += 1


def compare(native_core, native_audit, web, frames):
    paths = [Path(native_core), Path(native_audit), Path(web)]
    if any(a.samefile(b) for a, b in itertools.combinations(paths, 2)):
        raise ValueError('independent core, input audit and Web streams required')
    first = None
    mismatches = 0
    count = 0
    slots = {}
    slot_sequence = None
    expected_slot_frame = 0
    prior_native = None
    for index, triple in enumerate(itertools.zip_longest(lines(native_core), lines(native_audit), web_inputs(web))):
        if any(row is None for row in triple):
            raise ValueError(f'capture length mismatch at input {index}')
        native, audit, observed = triple
        if audit.get('frame') != index or audit.get('status') != 'input':
            raise ValueError('native input audit frame/status mismatch')
        slot, local = audit['slot'], audit['slot_frame']
        if slot_sequence is None:
            slot_sequence = slot
        if slot != slot_sequence:
            if slot != slot_sequence + 1 or local != 0:
                raise ValueError('native non-adjacent Replay slot transition')
            if prior_native['cursor_after'] != prior_native['end']:
                raise ValueError('native switched slots before consuming the preceding slot')
            slot_sequence, expected_slot_frame = slot, 0
        if local != expected_slot_frame or audit['slot_frame_after'] != local + 1:
            raise ValueError('native slot input sequence mismatch')
        expected_slot_frame += 1
        if audit['cursor_after'] != audit['cursor'] + 6 or audit['input'] != audit['expected']:
            raise ValueError('native input bytes/cursor mismatch')
        after, core = observed['after'], observed['core']
        # A Replay slot's declared stage and the live current stage differ
        # during transitions. Compare both actual identities independently.
        if after['slot_number'] != slot or after['slot_frame'] != local + 1:
            raise ValueError(f'Web/native Replay identity mismatch at input {index}')
        if after['slot_frames'] * 6 != audit['end'] - audit['start']:
            raise ValueError('Web/native Replay slot input extent mismatch')
        if audit['cursor'] != audit['start'] + local * 6:
            raise ValueError('native cursor does not identify the audited slot frame')
        validate(native, index, native['stage'])
        validate(core, local, core['stage'])
        if after['current_stage'] != core['stage']:
            raise ValueError('Web progress/core current-stage mismatch')
        for key, value in zip(('held', 'pressed', 'released'), audit['input']):
            if native['input'][key] != value:
                raise ValueError('native input audit/core differ')
        fields = [('stage', native['stage'], core['stage'])]
        for group in ('input', 'globals', 'player', 'rng'):
            fields.extend((f'{group}.{key}', value, core[group][key])
                          for key, value in native[group].items())
        for field, left, right in fields:
            if left != right:
                mismatches += 1
                if first is None:
                    first = dict(input_index=index, slot=slot, slot_frame=local,
                                 field=field, original=left, web=right)
        slots[slot] = slots.get(slot, 0) + 1
        prior_native = audit
        count += 1
    if count != frames:
        raise ValueError(f'incomplete input window: expected {frames}, got {count}')
    return dict(schema='th13/continuous-core-comparison/v1', frames=count,
                slots=slots, firstDifference=first, mismatches=mismatches,
                coreEqual=first is None, completeStateCertified=False,
                idleHostOutputCertified=False, referenceQualified=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('native_core'); parser.add_argument('native_audit'); parser.add_argument('web')
    parser.add_argument('--frames', type=int, required=True)
    args = parser.parse_args()
    try:
        result = compare(args.native_core, args.native_audit, args.web, args.frames)
        code = int(not result['coreEqual'])
    except (ValueError, KeyError, TypeError, OSError) as error:
        result, code = dict(error=str(error), completeStateCertified=False), 2
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
