// Validation is separate from collection so malformed captures can be tested without a browser.
export function validateCapture(text, requestedFrames, reportedFrames) {
  if (!Number.isInteger(requestedFrames) || requestedFrames <= 0)
    throw Error('positive requested frame count required');
  if (reportedFrames !== requestedFrames)
    throw Error(`capture length ${reportedFrames} != requested ${requestedFrames}`);
  if (!text.endsWith('\n')) throw Error('incomplete final JSONL record');
  const lines = text.split('\n');
  lines.pop();
  if (!lines.length || lines.some(line => !line.trim())) throw Error('empty capture or blank record');
  const records = lines.map(line => JSON.parse(line));
  const header = records.shift();
  if (header.schema !== 'th13-submission-v2') throw Error('production submission schema required');
  let frame = 0, sequence = 0, completion = null;
  for (const record of records) {
    if (record.kind === 'complete') {
      if (completion) throw Error('duplicate completion record');
      completion = record;
      continue;
    }
    if (completion) throw Error('record after completion');
    if (record.frame !== frame || record.seq !== sequence)
      throw Error(`noncontiguous submission at sequence ${sequence}, frame ${frame}`);
    if (!['draw', 'clear', 'target', 'update', 'prepare', 'release', 'present', 'flush'].includes(record.kind))
      throw Error(`unknown submission kind ${record.kind}`);
    ++sequence;
    if (record.kind === 'present') ++frame;
  }
  if (frame !== requestedFrames) throw Error(`present count ${frame} != requested ${requestedFrames}`);
  const lastEvent = records.filter(record => record.kind !== 'complete').at(-1);
  if (!lastEvent || lastEvent.kind !== 'present') throw Error('unfinished commands after final present');
  if (!completion || completion.complete !== true || completion.frames !== frame || completion.events !== sequence)
    throw Error('missing or inconsistent completion record');
  return {schema: header.schema, frames: frame, events: sequence,
          qualification: header.qualification ?? 'unqualified', fidelity: 'UNVERIFIED'};
}
