// Read-only snapshots of production title VM state at interaction boundaries.
import {createServer} from 'node:http';
import {readFile, writeFile, mkdir, realpath} from 'node:fs/promises';
import {resolve, join, extname} from 'node:path';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
const root='/work', port=join(root,'eagler-touhou/ports/th13'), runtime=join(port,'web/sdl-runtime');
const out=resolve(process.argv[2]??join(root,'artifacts/th13-longrun-20261029/web-menu-feedback'));
if(!out.startsWith('/work/artifacts/'))throw Error('Output must be in artifacts');
await mkdir(out,{recursive:true});
const sha=b=>createHash('sha256').update(b).digest('hex');
const manifest=await readFile(join(port,'web/artifacts/sdl3/build.json'));
const build=JSON.parse(manifest);
for(const [path,expected] of Object.entries(build.sourceFiles))if(sha(await readFile(path))!==expected)throw Error('Build source changed: '+path);
const server=createServer(async(req,res)=>{try{const path=decodeURIComponent(new URL(req.url,'http://localhost').pathname);const folder=path.startsWith('/artifacts/')?join(port,'web/artifacts'):path.startsWith('/assets/')?join(runtime,'assets'):runtime;const rest=path.replace(/^\/(?:artifacts|assets)\//,'/');const file=await realpath(resolve(folder,'.'+rest));if(!file.startsWith(folder+'/'))throw Error('outside route');res.writeHead(200,{'Content-Type':({'.html':'text/html','.mjs':'text/javascript','.wasm':'application/wasm'})[extname(file)]??'application/octet-stream'}).end(await readFile(file));}catch{res.writeHead(404).end();}});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const require=createRequire(import.meta.url),{chromium}=require(join(process.env.NODE_PATH??'/tmp/th13-browser-tools/node_modules','playwright-core'));
let browser;let result;try{browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader','--autoplay-policy=no-user-gesture-required']});
const page=await browser.newPage({viewport:{width:640,height:480}});const errors=[];page.on('pageerror',e=>errors.push(String(e)));await page.goto(`http://127.0.0.1:${server.address().port}/th13.html`,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>window.mod?._th13_frame()>5||document.getElementById('error').textContent,null,{timeout:180000});
result=await page.evaluate(()=>{const m=window.mod;m._th13_loop_stop();const rows=[];const state=()=>[...m.HEAP32.subarray(m._th13_probe_state()/4,m._th13_probe_state()/4+16)];const frame=()=>{if(!m._th13_debug_frame())throw Error(m.UTF8ToString(m._th13_error()));};const save=label=>rows.push({label,state:state(),report:m.UTF8ToString(m._th13_debug_layer_report())});let count=0;while(!(state()[8]===1&&state()[9]===2)&&count++<800)frame();if(count>=800)throw Error('menu did not become interactive');save('first_interactive');frame();save('interactive_next');const tap=key=>{m._th13_key(key,1);frame();m._th13_key(key,0);frame();};tap(44);for(let i=0;i<140&&!(state()[8]===6&&state()[9]===2);i++)frame();if(state()[8]!==6||state()[9]!==2)throw Error('difficulty did not become interactive');save('difficulty_first');tap(44);for(let i=0;i<140&&!(state()[8]===7&&state()[9]===2);i++)frame();if(state()[8]!==7||state()[9]!==2)throw Error('character did not become interactive');save('character_first');for(let i=0;i<60;i++)frame();save('character_settled');tap(205);save('character_right_first');for(let i=0;i<60;i++)frame();save('character_right_settled');return {rows,error:m.UTF8ToString(m._th13_error())};});result.errors=errors;result.buildSha256=sha(manifest);result.coverage='Production VM state snapshots only; original independent draw comparison pending.';
}catch(e){result={failure:String(e.stack??e)};process.exitCode=1;}finally{await browser?.close();await new Promise(done=>server.close(done));await writeFile(join(out,'menu-feedback.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result.failure??{snapshots:result.rows?.length,errors:result.errors,buildSha256:result.buildSha256}));}
