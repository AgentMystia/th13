// web 语义绘制清单捕获驱动（确定性渲染门禁 M0）：chromium 无头开
// th13-debug.html（捕获构建 artifact）→ th13_debug_capture_begin → 泵 N 帧
// → （可选）glReadPixels 定点采样 → captureEnd → MEMFS JSONL 落盘。
// 产出：draws JSONL（与 oracle/harness 同 schema）+ 像素采样 JSON。
// 用法：
//   node scripts/web_draws_capture.mjs --frames 300 [--replay th13_01.rpy]
//     [--sht pl00a.sht] [--stage 0] [--out artifacts/tmp-scratch/web-f300.jsonl]
//     [--sample-frame 124 --points 305,393 209,393]   # 游戏坐标（y 下原点）
//     [--shot-frame 124]                              # 整帧 RGBA 落盘 .raw
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {resolve, dirname, join, extname} from 'node:path';
import {fileURLToPath} from 'node:url';

const port = resolve(dirname(fileURLToPath(import.meta.url)), '../eagler-touhou/ports/th13');
const workspace = resolve(port, '../../..');
const runtimeDir = join(port, 'web/sdl-runtime');
const buildDir = join(port, 'web/artifacts');
const replaysDir = join(workspace, 'games/replays');
const rawDir = join(workspace, 'artifacts/reference/raw');

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const at = args.indexOf('--' + name);
  return at >= 0 && args[at + 1] ? args[at + 1] : fallback;
};
const flags = name => args.filter((_, i) => args[i - 1] === '--' + name);
const replay = option('replay', 'th13_01.rpy');
const sht = option('sht', 'pl00a.sht');
const stage = Number(option('stage', '0'));
const frames = Number(option('frames', '300'));
const out = option('out', join(workspace, 'artifacts/tmp-scratch/web-draws.jsonl'));
const sampleFrame = args.includes('--sample-frame') ? Number(option('sample-frame', '0')) : -1;
const points = flags('points').map(p => p.split(',').map(Number));
const shotFrame = args.includes('--shot-frame') ? Number(option('shot-frame', '0')) : -1;

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.wasm': 'application/wasm', '.png': 'image/png', '.json': 'application/json',
  '.anm': 'application/octet-stream', '.ecl': 'application/octet-stream',
  '.rpy': 'application/octet-stream', '.sht': 'application/octet-stream',
};
const routes = [
  ['/assets/', join(runtimeDir, 'assets')],
  ['/artifacts/', buildDir],
  ['/replays/', replaysDir],
  ['/', runtimeDir],
];
const server = createServer(async (request, response) => {
  try {
    const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    let dir = null, rest = path;
    if (path.startsWith('/assets/') && path.endsWith('.sht')) {
      dir = rawDir; rest = path.slice('/assets/'.length);
    } else {
      for (const [prefix, root] of routes) {
        if (!path.startsWith(prefix)) continue;
        dir = root; rest = path.slice(prefix.length); break;
      }
    }
    if (dir) {
      const file = resolve(dir, rest);
      if (file.startsWith(resolve(dir))) {
        const bytes = await readFile(file);
        response.writeHead(200, {'Content-Type': MIME[extname(file)] ?? 'application/octet-stream'});
        response.end(bytes);
        return;
      }
    }
    response.writeHead(404).end('not found');
  } catch { response.writeHead(404).end('not found'); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const origin = `http://127.0.0.1:${server.address().port}`;

const require = createRequire(import.meta.url);
let chromium;
try { ({chromium} = require('playwright-core')); } catch {
  ({chromium} = require(join(process.env.NODE_PATH ?? '/opt/shot/node_modules', 'playwright-core')));
}

const consoleLines = [];
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--use-angle=swiftshader',
         '--enable-unsafe-swiftshader', '--force-color-profile=srgb'],
});
const page = await browser.newPage({viewport: {width: 800, height: 560}});
page.on('console', message => consoleLines.push(message.text()));
page.on('pageerror', error => consoleLines.push('pageerror ' + error.message));
const query = `?artifact=${encodeURIComponent('../artifacts/sdl3-capture/th13-sdl.mjs')}` +
              `&replay=${encodeURIComponent(replay)}&sht=${encodeURIComponent(sht)}&stage=${stage}`;
await page.goto(`${origin}/th13-debug.html${query}`, {waitUntil: 'domcontentloaded'});
await page.waitForFunction('!!window.__th13_debug || !!window.__th13_debug_failed',
                           null, {timeout: 180000});
const failed = await page.evaluate('window.__th13_debug_failed ?? null');
if (failed) throw new Error('启动失败：' + failed);

// 捕获激活 → 泵帧（中途定点采样）→ 收束。
await page.evaluate('window.__th13_debug.captureBegin()');
const samples = {};
let shotSaved = false;
for (let pumped = 0; pumped < frames; pumped++) {
  await page.evaluate('window.__th13_debug.pumpFrames(1)');
  const consumed = await page.evaluate('window.__th13_debug.consumed()');
  if (sampleFrame >= 0 && consumed === sampleFrame + 1 && !samples.consumed) {
    // 游戏坐标（y 下原点）→ GL 坐标（y 上原点）：y_gl = 479 - y。
    samples.consumed = consumed;
    samples.points = await page.evaluate(points => {
      const out = [];
      for (const [x, y] of points) {
        const rgba = window.__th13_debug.pixels(x, 479 - y, 1, 1);
        out.push({x, y, rgba: Array.from(rgba)});
      }
      return out;
    }, points);
  }
  if (shotFrame >= 0 && consumed === shotFrame + 1 && !shotSaved) {
    shotSaved = true;
    const rgba = await page.evaluate('window.__th13_debug.pixels(0, 0, 640, 480)');
    const raw = Buffer.from(rgba);
    const rawPath = out.replace(/\.jsonl$/, '') + `-f${shotFrame}.raw`;
    await mkdir(dirname(rawPath), {recursive: true});
    await writeFile(rawPath, raw);
    console.log('整帧 RGBA 落盘：' + rawPath + '（' + raw.length + ' 字节）');
  }
}
const capturedFrames = await page.evaluate('window.__th13_debug.captureEnd()');
const drawList = await page.evaluate('window.__th13_debug.captureDrawList()');
await browser.close();
server.close();

await mkdir(dirname(out), {recursive: true});
await writeFile(out, drawList);
const lines = drawList.split('\n').filter(Boolean);
console.log(JSON.stringify({
  replay, sht, stage, capturedFrames, jsonlLines: lines.length, out,
  firstLine: lines[0]?.slice(0, 120), lastLine: lines[lines.length - 1]?.slice(0, 80),
  samples: Object.keys(samples).length ? samples : undefined,
}, null, 2));
const errors = consoleLines.filter(l => l.includes('[th13]') && !l.includes('资源'));
for (const line of errors.slice(0, 20)) console.error(line);
