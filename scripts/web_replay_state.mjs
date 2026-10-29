// Read-only production Web replay state collector. No alternate simulation.
import {createServer} from 'node:http';
import {readFile, writeFile, mkdir, realpath} from 'node:fs/promises';
import {resolve, join, extname, basename} from 'node:path';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';

const args = process.argv.slice(2);
const options = new Map();
for (let i = 0; i < args.length; i += 2) {
  if (!['--replay', '--stage', '--frames', '--out', '--menu-tape'].includes(args[i]) || !args[i + 1])
    throw Error('Expected --replay NAME --stage INDEX --frames COUNT --out DIRECTORY');
  options.set(args[i], args[i + 1]);
}
const root = '/work';
const port = join(root, 'eagler-touhou/ports/th13');
const runtime = join(port, 'web/sdl-runtime');
const replay = options.get('--replay') ?? 'th13_01.rpy';
const stage = Number(options.get('--stage') ?? 0);
const frames = Number(options.get('--frames') ?? 120);
const menuTapePath = options.get('--menu-tape');
const menuTape = menuTapePath ? (await readFile(menuTapePath, 'utf8')).trim().split('\n').map(JSON.parse) : null;
const out = resolve(options.get('--out') ?? join(root, 'artifacts/th13-longrun-20261029/web', replay, String(stage)));
if (basename(replay) !== replay || !out.startsWith('/work/artifacts/') ||
    !Number.isInteger(stage) || stage < 0 || !Number.isInteger(frames) || frames < 1)
  throw Error('Invalid replay, stage, frame count or artifact directory');
await mkdir(out, {recursive: true});
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const manifestBytes = await readFile(join(port, 'web/artifacts/sdl3/build.json'));
const build = JSON.parse(manifestBytes);
const wasmBytes = await readFile(join(port, 'web/artifacts/sdl3/th13-sdl.wasm'));
const loaderBytes = await readFile(join(port, 'web/artifacts/sdl3/th13-sdl.mjs'));
if (sha(wasmBytes) !== build.sha256 || sha(loaderBytes) !== build.loaderSha256)
  throw Error('Build manifest does not identify the served Web binary');
for (const [path, expected] of Object.entries(build.sourceFiles))
  if (sha(await readFile(path)) !== expected) throw Error('Build source changed: ' + path);
const mime = {'.html': 'text/html', '.mjs': 'text/javascript', '.wasm': 'application/wasm'};
const server = createServer(async (request, response) => {
  try {
    const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const folder = path.startsWith('/replays/') ? join(root, 'games/replays')
      : path.startsWith('/artifacts/') ? join(port, 'web/artifacts')
      : path.startsWith('/assets/') ? join(runtime, 'assets') : runtime;
    const rest = path.replace(/^\/(?:replays|artifacts|assets)\//, '/');
    const file = await realpath(resolve(folder, '.' + rest));
    if (!file.startsWith(folder + '/')) throw Error('Outside route');
    response.writeHead(200, {'Content-Type': mime[extname(file)] ?? 'application/octet-stream'});
    response.end(await readFile(file));
  } catch { response.writeHead(404).end(); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const require = createRequire(import.meta.url);
const {chromium} = require(join(process.env.NODE_PATH ?? '/tmp/th13-browser-tools/node_modules', 'playwright-core'));
let browser;
const errors = [];
const rows = [];
const entities = [];
const audio = [];
let menuObservations = [];
const started = performance.now();
let failure = null;
let bootSeed = null;
let replaySha256 = null;
try {
  browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH, headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--use-angle=swiftshader',
      '--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required']});
  const page = await browser.newPage({viewport: {width: 640, height: 480}});
  if (menuTape) await page.addInitScript(() => {
    let module;
    Object.defineProperty(window, 'mod', {configurable: true,
      get() { return module; }, set(value) {
        module = value;
        const startup = value._th13_draw_startup;
        value._th13_draw_startup = count => {
          window.__th13_boot_advanced = count;
          return startup(count);
        };
        // Pause the host RAF before its first callback; all game ticks below
        // still use the production ApplicationState and renderer.
        const start = value._th13_loop_start;
        value._th13_loop_start = () => { start(); value._th13_loop_pause(1); window.__th13_tape_ready = true; };
      }});
  });
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`http://127.0.0.1:${server.address().port}/th13.html`, {waitUntil: 'domcontentloaded'});
  await page.waitForFunction(() => window.__th13_tape_ready || window.mod?._th13_frame() > 5 || document.getElementById('error').textContent,
    null, {timeout: 180000});
  await page.evaluate(() => {
    if (document.getElementById('error').textContent) throw Error(document.getElementById('error').textContent);
    window.mod._th13_loop_stop();
  });
  bootSeed = await page.evaluate(() => window.mod._th13_boot_seed());
  const bytes = await readFile(join(root, 'games/replays', replay));
  replaySha256 = sha(bytes);
  await page.evaluate(({replay, stage, data, menu}) => {
    const m = window.mod;
    m.FS.writeFile('/assets/' + replay, new Uint8Array(data));
    const name = m.allocateUTF8(replay);
    try {
      if (menu) {
        const ptr = m._malloc(data.length);
        try {
          m.HEAPU8.set(data, ptr);
          if (!m._th13_replay_import(ptr, data.length, name)) throw Error(m.UTF8ToString(m._th13_replay_import_error()));
        } finally { m._free(ptr); }
      } else if (!m._th13_debug_replay(name, stage)) throw Error(m.UTF8ToString(m._th13_error()));
    } finally { m._free(name); }
  }, {replay, stage, data: Array.from(bytes), menu: !!menuTape});
  if (menuTape) {
    menuObservations = await page.evaluate(tape => {
      const m = window.mod, records = [], scans = {40:208,90:44};
      let previous = [];
      for (const record of tape) {
        // th13.html already advanced these signature-only branding ticks.
        if (record.global_frame < (window.__th13_boot_advanced ?? 0)) continue;
        const held = record.held_vk;
        for (const vk of previous) if (!held.includes(vk)) m._th13_key(scans[vk],0);
        for (const vk of held) {
          if (!(vk in scans)) throw Error('Unmapped menu-tape VK: ' + vk);
          if (!previous.includes(vk)) m._th13_key(scans[vk],1);
        }
        previous = held;
        if (!m._th13_debug_frame()) throw Error(m.UTF8ToString(m._th13_error()));
        const ptr = m._th13_probe_state() / 4;
        records.push({global_frame:record.global_frame,state:Array.from(m.HEAP32.subarray(ptr,ptr+16)),
          consumed:m._th13_debug_replay_frame()});
      }
      for (const vk of previous) m._th13_key(scans[vk],0);
      if (m._th13_phase() !== 1 || m._th13_debug_replay_frame() !== 0)
        throw Error('Menu tape did not reach pre-input replay state: ' + JSON.stringify(records.at(-1)));
      return records;
    }, menuTape);
  }
  await page.evaluate(() => window.mod._th13_debug_audio_observe(1));
  // Publish immutable run identity before the long frame window, allowing
  // the native collector to start concurrently with this measured boot clock.
  // This is a start receipt, never a completion or fidelity result.
  await writeFile(join(out, 'capture-start.json'), JSON.stringify({
    schema: 'th13/web-replay-capture-start/v1', replay, stage, bootSeed,
    framesRequested: frames, buildSha256: sha(manifestBytes),
    wasmSha256: sha(wasmBytes), loaderSha256: sha(loaderBytes), replaySha256,
    phase: 'loaded-before-frame-window', completionCertified: false,
  }, null, 2) + '\n');
  while (rows.length < frames) {
    const count = Math.min(120, frames - rows.length);
    const batch = await page.evaluate(count => {
      const m = window.mod, output = [], entities = [];
      for (let i = 0; i < count; ++i) {
        const before = m._th13_debug_replay_frame();
        if (!m._th13_debug_frame()) throw Error(m.UTF8ToString(m._th13_error()));
        if (m._th13_debug_replay_frame() !== before + 1)
          throw Error('Replay did not consume once: ' + before + ' -> ' + m._th13_debug_replay_frame());
        output.push(m.UTF8ToString(m._th13_debug_replay_core()));
        entities.push(m.UTF8ToString(m._th13_debug_replay_entities()));
      }
      return {output, entities, audio: m.UTF8ToString(m._th13_debug_audio_take())};
    }, count);
    rows.push(...batch.output);
    entities.push(...batch.entities);
    audio.push(batch.audio);
  }
  if (errors.length) throw Error(errors.join('\n'));
} catch (error) { failure = String(error.stack ?? error); process.exitCode = 1; }
finally {
  if (browser) await browser.close();
  await new Promise(done => server.close(done));
  const state = rows.join('\n') + (rows.length ? '\n' : '');
  await writeFile(join(out, 'state-core-v2.jsonl'), state);
  const entityText = entities.join('\n') + (entities.length ? '\n' : '');
  await writeFile(join(out, 'entities-v1.jsonl'), entityText);
  const audioText = audio.join('');
  await writeFile(join(out, 'audio-requests.jsonl'), audioText);
  if (menuTape) await writeFile(join(out, 'menu-observations.jsonl'), menuObservations.map(JSON.stringify).join('\n')+'\n');
  const result = {schema: 'th13/web-replay-core-capture/v2', replay, stage, framesRequested: frames,
    framesCaptured: rows.length, bootSeed, elapsedSeconds: (performance.now() - started) / 1000,
    buildSha256: sha(manifestBytes), wasmSha256: sha(wasmBytes), loaderSha256: sha(loaderBytes),
    replaySha256, menuTapeSha256: menuTapePath ? sha(await readFile(menuTapePath)) : null,
    stateSha256: sha(state), entitySha256: sha(entityText), audioSha256: sha(audioText), errors, failure,
    coverage: 'core only; entities, damage, ANM and continuous cross-stage qualification pending'};
  await writeFile(join(out, 'capture.json'), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
}
