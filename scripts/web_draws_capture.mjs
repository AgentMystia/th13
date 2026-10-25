// Collect real production submissions. This is not an original-framebuffer oracle.
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile, writeFile, mkdir, realpath} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve, dirname, join, extname, sep, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {validateCapture} from './submission_capture_check.mjs';

const workspace = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const port = join(workspace, 'eagler-touhou/ports/th13');
const runtimeDir = join(port, 'web/sdl-runtime');
const buildDir = join(port, 'web/artifacts');
const replaysDir = join(workspace, 'games/replays');
const rawDir = join(workspace, 'artifacts/reference/raw');
const argv = process.argv.slice(2);
const values = new Map();
const names = new Set(['replay', 'sht', 'stage', 'frames', 'out', 'sample-frame', 'points', 'shot-frame']);
for (let i = 0; i < argv.length; i += 2) {
  const name = argv[i]?.startsWith('--') ? argv[i].slice(2) : '';
  if (!names.has(name) || !argv[i + 1] || argv[i + 1].startsWith('--'))
    throw Error(`unknown or incomplete option ${argv[i]}`);
  if (name !== 'points' && values.has(name)) throw Error(`duplicate option --${name}`);
  values.set(name, [...(values.get(name) ?? []), argv[i + 1]]);
}
const option = (name, fallback) => values.get(name)?.[0] ?? fallback;
const integer = (name, fallback, minimum) => {
  const value = Number(option(name, fallback));
  if (!Number.isSafeInteger(value) || value < minimum) throw Error(`invalid --${name}`);
  return value;
};
const replay = option('replay', 'th13_01.rpy');
const sht = option('sht', 'pl00a.sht');
for (const name of [replay, sht])
  if (basename(name) !== name || name.includes('\\')) throw Error('asset basename required');
const stage = integer('stage', '0', 0);
const frames = integer('frames', '300', 1);
const out = resolve(option('out', join(workspace, 'artifacts/tmp-scratch/web-draws.jsonl')));
const artifacts = await realpath(join(workspace, 'artifacts'));
if (!out.startsWith(artifacts + sep)) throw Error('capture output must be inside ignored artifacts/');
const sampleFrame = integer('sample-frame', '-1', -1);
const shotFrame = integer('shot-frame', '-1', -1);
const points = (values.get('points') ?? []).map(point => {
  const pair = point.split(',').map(Number);
  if (pair.length !== 2 || pair.some(v => !Number.isInteger(v)) ||
      pair[0] < 0 || pair[0] >= 640 || pair[1] < 0 || pair[1] >= 480)
    throw Error(`invalid point ${point}`);
  return pair;
});
if (sampleFrame >= 0 && !points.length) throw Error('--sample-frame requires --points');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const buildManifest = await readFile(join(buildDir, 'sdl3-capture/build.json'));
const build = JSON.parse(buildManifest);
const wasm = await readFile(join(buildDir, 'sdl3-capture/th13-sdl.wasm'));
const loader = await readFile(join(buildDir, 'sdl3-capture/th13-sdl.mjs'));
if (build.sha256 !== hash(wasm) || build.loaderSha256 !== hash(loader) || !build.sourceFiles)
  throw Error('capture build proof does not match runtime bytes');
for (const [path, expected] of Object.entries(build.sourceFiles)) {
  const source = await readFile(path);
  if (hash(source) !== expected) throw Error('capture build source is stale: ' + path);
}
const replayBytes = await readFile(join(replaysDir, replay));

const mime = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
  '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.png': 'image/png',
  '.json': 'application/json'};
const routes = [['/assets/', join(runtimeDir, 'assets')], ['/artifacts/', buildDir],
  ['/replays/', replaysDir], ['/', runtimeDir]];
const server = createServer(async (request, response) => {
  try {
    const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    if (path === '/favicon.ico') { response.writeHead(204).end(); return; }
    let dir, rest;
    if (path.startsWith('/assets/') && path.endsWith('.sht')) {
      dir = rawDir; rest = path.slice('/assets/'.length);
    } else {
      const route = routes.find(([prefix]) => path.startsWith(prefix));
      if (route) { dir = route[1]; rest = path.slice(route[0].length); }
    }
    if (dir) {
      const file = await realpath(resolve(dir, rest));
      if (file.startsWith(await realpath(dir) + sep)) {
        response.writeHead(200, {'Content-Type': mime[extname(file)] ?? 'application/octet-stream'});
        response.end(await readFile(file));
        return;
      }
    }
    response.writeHead(404).end('not found');
  } catch { response.writeHead(404).end('not found'); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
try {
  const require = createRequire(import.meta.url);
  let chromium;
  try { ({chromium} = require('playwright-core')); } catch {
    ({chromium} = require(join(process.env.NODE_PATH ?? '/opt/shot/node_modules', 'playwright-core')));
  }
  browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH,
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage', '--use-angle=swiftshader',
      '--enable-unsafe-swiftshader', '--force-color-profile=srgb']});
  const page = await browser.newPage({viewport: {width: 800, height: 560}});
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  const query = new URLSearchParams({artifact: '../artifacts/sdl3-capture/th13-sdl.mjs', replay, sht, stage});
  await page.goto(`${origin}/th13-debug.html?${query}`, {waitUntil: 'domcontentloaded'});
  await page.waitForFunction('!!window.__th13_debug || !!window.__th13_debug_failed', null, {timeout: 180000});
  const failed = await page.evaluate('window.__th13_debug_failed ?? null');
  if (failed) throw Error('startup failed: ' + failed);
  const start = await page.evaluate(() => ({consumed: window.__th13_debug.consumed(),
    result: window.__th13_debug.captureBegin()}));
  if (!start.result?.ok) throw Error('capture activation failed');
  let samples = null, shot = null;
  for (let pumped = 0; pumped < frames; ++pumped) {
    // WebGL's default buffer must be sampled in the same task as the frame pump.
    const observed = await page.evaluate(({sampleFrame, shotFrame, points}) => {
      const debug = window.__th13_debug;
      const consumed = debug.pumpFrames(1);
      const result = {consumed};
      if (consumed === sampleFrame + 1)
        result.samples = points.map(([x, y]) => ({x, y, rgba: Array.from(debug.pixels(x, 479 - y, 1, 1))}));
      if (consumed === shotFrame + 1) result.shot = Array.from(debug.pixels(0, 0, 640, 480));
      return result;
    }, {sampleFrame, shotFrame, points});
    if (observed.consumed !== start.consumed + pumped + 1)
      throw Error(`input did not advance once at pump ${pumped}`);
    if (observed.samples) samples = {consumed: observed.consumed, points: observed.samples};
    if (observed.shot) shot = Buffer.from(observed.shot);
  }
  const result = await page.evaluate(() => ({frames: window.__th13_debug.captureEnd(),
    text: window.__th13_debug.captureDrawList()}));
  const capture = validateCapture(result.text, frames, result.frames);
  if (pageErrors.length || consoleErrors.length)
    throw Error(`browser errors: ${JSON.stringify({pageErrors, consoleErrors})}`);
  if (sampleFrame >= 0 && !samples) throw Error('requested sample frame was not reached');
  if (shotFrame >= 0 && (!shot || shot.length !== 640 * 480 * 4))
    throw Error('requested framebuffer sample was not reached or incomplete');
  await mkdir(dirname(out), {recursive: true});
  await writeFile(out, result.text);
  if (shot) await writeFile(out.replace(/\.jsonl$/, '') + `-f${shotFrame}.raw`, shot);
  const manifest = {schema: 'th13/web-capture/v2', out, replay, sht, stage, requestedFrames: frames,
    capturedFrames: result.frames, startConsumed: start.consumed, ...capture,
    hashes: {capture: hash(result.text), replay: hash(replayBytes), build: hash(buildManifest), wasm: hash(wasm)},
    samples, pageErrors, consoleErrors,
    limitation: 'production submission collection only; original reference and framebuffer fidelity unverified'};
  await writeFile(out + '.manifest.json', JSON.stringify(manifest, null, 2) + '\n');
  console.log(JSON.stringify(manifest, null, 2));
} finally {
  if (browser) await browser.close();
  await new Promise(done => server.close(done));
}
