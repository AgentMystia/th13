import test from 'node:test';
import assert from 'node:assert/strict';
import {validateCapture} from './submission_capture_check.mjs';
const header = {schema: 'th13-submission-v2', qualification: 'unqualified'};
const draw = {frame: 0, seq: 0, kind: 'draw'};
const present = {frame: 0, seq: 1, kind: 'present'};
const footer = {kind: 'complete', complete: true, frames: 1, events: 2};
const encode = rows => rows.map(row => JSON.stringify(row)).join('\n') + '\n';

test('collecting complete production submissions does not certify fidelity', () => {
  const result = validateCapture(encode([header, draw, present, footer]), 1, 1);
  assert.equal(result.frames, 1);
  assert.equal(result.fidelity, 'UNVERIFIED');
});
for (const [name, rows, frames, reported] of [
  ['short reported count', [header, draw, present, footer], 2, 1],
  ['missing present', [header, draw, footer], 1, 1],
  ['duplicate sequence', [header, draw, draw, present, footer], 1, 1],
  ['missing sequence', [header, {...draw, seq: 2}, present, footer], 1, 1],
  ['missing frame', [header, {...draw, frame: 1}, present, footer], 1, 1],
  ['unknown operation', [header, {...draw, kind: 'invented'}, present, footer], 1, 1],
  ['legacy schema', [{schema: 'draw-v1'}, draw, present, footer], 1, 1],
  ['no completion', [header, draw, present], 1, 1],
  ['completion false', [header, draw, present, {...footer, complete: false}], 1, 1],
  ['wrong event count', [header, draw, present, {...footer, events: 1}], 1, 1],
  ['trailing event', [header, draw, present, footer, draw], 1, 1],
  ['duplicate completion', [header, draw, present, footer, footer], 1, 1],
  ['unfinished trailing frame', [header, draw, present, {kind: 'draw', frame: 1, seq: 2}, {...footer, events: 3}], 1, 1],
]) test(`reject ${name}`, () => assert.throws(() => validateCapture(encode(rows), frames, reported)));

test('reject partial final record and blank records', () => {
  assert.throws(() => validateCapture(encode([header, draw, present, footer]).trimEnd(), 1, 1));
  assert.throws(() => validateCapture('\n' + encode([header, draw, present, footer]), 1, 1));
});
