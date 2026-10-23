import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const portDir = join(root, 'eagler-touhou/ports/th13');
const runtimeDir = join(portDir, 'web/sdl-runtime');
const buildDir = join(portDir, 'web/artifacts');
const rawDir = join(root, 'artifacts/reference/raw');
const replaysDir = join(root, 'games/replays');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.wasm': 'application/wasm',
  '.png': 'image/png',
  '.json': 'application/json',
  '.anm': 'application/octet-stream',
  '.ecl': 'application/octet-stream',
  '.sht': 'application/octet-stream',
  '.std': 'application/octet-stream',
  '.msg': 'application/octet-stream',
  '.rpy': 'application/octet-stream',
};

const server = createServer(async (req, res) => {
  try {
    const urlPath = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
    let filePath;
    if (urlPath.startsWith('/artifacts/')) {
      filePath = join(buildDir, urlPath.slice('/artifacts/'.length));
    } else if (urlPath.startsWith('/assets/') && urlPath.endsWith('.sht')) {
      filePath = join(rawDir, urlPath.slice('/assets/'.length));
    } else if (urlPath.startsWith('/assets/')) {
      filePath = join(runtimeDir, urlPath);
    } else if (urlPath.startsWith('/replays/')) {
      filePath = join(replaysDir, urlPath.slice('/replays/'.length));
    } else {
      filePath = join(runtimeDir, urlPath);
    }
    const ext = '.' + filePath.split('.').pop();
    const data = await readFile(filePath);
    res.writeHead(200, {'Content-Type': MIME[ext] || 'application/octet-stream'}).end(data);
  } catch (err) {
    res.writeHead(404).end('Not found: ' + err.message);
  }
});

await new Promise(r => server.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${server.address().port}/th13-debug.html?replay=th13_01.rpy&sht=pl00a.sht&stage=0`;
console.log('Server running at', url);

const require = createRequire(import.meta.url);
let chromium;
try {
  ({chromium} = require('playwright-core'));
} catch {
  ({chromium} = require(join(process.env.NODE_PATH ?? '/opt/shot/node_modules', 'playwright-core')));
}

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  headless: true,
  args: [
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--use-angle=swiftshader',
    '--enable-unsafe-swiftshader',
    '--force-color-profile=srgb',
  ],
});

const page = await browser.newPage({viewport: {width: 1280, height: 960}});
page.on('console', msg => console.log('[browser]', msg.type(), msg.text()));
page.on('pageerror', err => console.error('[browser error]', err));

console.log('Navigating to', url);
await page.goto(url);

await page.waitForFunction(() => window.__th13_debug && window.__th13_debug.pumpTo, {timeout: 15000});

console.log('Pumping to frame 1242...');
const res = await page.evaluate(async () => {
  window.__th13_debug.pumpTo(1242);
  return {
    consumed: window.__th13_debug.consumed(),
    stageFrame: window.__th13_debug.stageFrame(),
    report: window.__th13_debug.report(),
    stageReport: window.__th13_debug.stageReport(),
  };
});

console.log('Pump result:');
console.log('  consumed:', res.consumed);
console.log('  stageFrame:', res.stageFrame);
console.log('  report:', res.report);
console.log('  stageReport:', res.stageReport);

await browser.close();
server.close();
