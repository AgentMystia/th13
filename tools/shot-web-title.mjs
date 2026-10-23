import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile, writeFile} from 'node:fs/promises';
import {resolve, dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const portDir = join(root, 'eagler-touhou/ports/th13');
const runtimeDir = join(portDir, 'web/sdl-runtime');
const buildDir = join(portDir, 'web/artifacts');
const rawDir = join(root, 'artifacts/reference/raw');

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
    } else if (urlPath === '/' || urlPath === '/th13.html') {
      filePath = join(runtimeDir, 'th13.html');
    } else {
      filePath = join(runtimeDir, urlPath);
    }
    const ext = '.' + filePath.split('.').pop();
    const data = await readFile(filePath);
    res.writeHead(200, {'Content-Type': MIME[ext] || 'application/octet-stream'}).end(data);
  } catch (err) {
    console.log('[server 404]', req.url, 'path:', err.message);
    res.writeHead(404).end('Not found: ' + err.message);
  }
});

await new Promise(r => server.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${server.address().port}/th13.html`;
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

// 循环检查 window.mod 是否就绪
console.log('Waiting for window.mod initialization...');
await page.waitForFunction(() => window.mod && window.mod._th13_frame, {timeout: 15000});

// 等待游戏主循环自己跑到 420 帧以上
console.log('Waiting for game to reach frame 420...');
await page.waitForFunction(() => window.mod && window.mod._th13_frame() >= 420, {timeout: 30000});

const curFrame = await page.evaluate(() => window.mod._th13_frame());
console.log('Current game frame reached:', curFrame);

// 等待 500ms 让画面刷新稳定
await page.waitForTimeout(500);

const canvas = await page.$('#canvas');
if (canvas) {
  const outPath = join(root, 'artifacts/web-title-current.png');
  await canvas.screenshot({path: outPath});
  console.log('Saved screenshot via playwright to', outPath);
} else {
  console.error('Canvas element not found!');
}

await browser.close();
server.close();
