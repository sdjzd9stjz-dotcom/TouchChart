// Optional browser acceptance runner: Node 22+ and installed Edge; no npm packages.
// touchchart.py and selftest.py themselves only need Python standard library.
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, writeFile, mkdir, unlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root=path.dirname(fileURLToPath(import.meta.url));
const python=process.env.TOUCHCHART_PYTHON||'C:\\Users\\terry\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\dependencies\\python\\python.exe';
const edge=process.env.TOUCHCHART_EDGE||'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const artifacts=path.join(root,'artifacts');await mkdir(artifacts,{recursive:true});
const profile=await mkdtemp(path.join(tmpdir(),'touchchart-edge-'));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const output=[];const report=s=>{output.push(s);console.log(s)};
let browser,server;const clients=[];let serverText='';

class CDP{
 constructor(url){this.ws=new WebSocket(url);this.id=0;this.pending=new Map();this.errors=[];this.dialogs=[];this.ready=new Promise((resolve,reject)=>{this.ws.onopen=resolve;this.ws.onerror=reject});this.ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=this.pending.get(m.id);if(p){this.pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}}else if(m.method==='Page.javascriptDialogOpening'){this.dialogs.push(m.params);this.send('Page.handleJavaScriptDialog',{accept:true})}else if(m.method==='Runtime.exceptionThrown')this.errors.push(m.params.exceptionDetails.exception?.description||m.params.exceptionDetails.text)}}
 async send(method,params={}){await this.ready;return new Promise((resolve,reject)=>{const id=++this.id;this.pending.set(id,{resolve,reject});this.ws.send(JSON.stringify({id,method,params}))})}
 async js(expression){const r=await this.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description||r.exceptionDetails.text);return r.result.value}
 async wait(expression,timeout=15000){const deadline=Date.now()+timeout;let last;while(Date.now()<deadline){try{last=await this.js(expression);if(last)return last}catch{}await sleep(50)}throw Error('Timed out: '+expression+'; last='+JSON.stringify(last))}
 async screenshot(name){const r=await this.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await writeFile(path.join(artifacts,name),Buffer.from(r.data,'base64'))}
 close(){this.ws.close()}
}

try{
 server=spawn(python,['-u',path.join(root,'touchchart.py'),'--port','0','--host','127.0.0.1'],{cwd:root,windowsHide:true});server.stdout.on('data',b=>serverText+=b);server.stderr.on('data',()=>{});
 for(let i=0;i<100&&!serverText.includes('TouchChart desktop:');i++)await sleep(50);
 const base=serverText.match(/TouchChart desktop: (\S+)/)?.[1];assert.ok(base,'server address');
 const request=async(route,body)=>{const r=await fetch(base+route,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();assert.ok(r.ok,JSON.stringify(d));return d};
 browser=spawn(edge,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-background-timer-throttling','--disable-renderer-backgrounding','--autoplay-policy=no-user-gesture-required','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'});
 let port;for(let i=0;i<200&&!port;i++){try{port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]}catch{}await sleep(50)}assert.ok(port,'Edge DevTools ready');
 async function page(route,width=1440,height=1100,mobile=false){const t=await(await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent("about:blank")}`,{method:'PUT'})).json();const c=new CDP(t.webSocketDebuggerUrl);clients.push(c);await c.send('Runtime.enable');await c.send('Page.enable');await c.send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile});await c.send('Emulation.setTouchEmulationEnabled',{enabled:mobile,maxTouchPoints:5});await c.send('Page.navigate',{url:base+route});await c.send('Page.bringToFront');return c}
 const desktop=await page('/');await desktop.wait('window.touchchart && synced && document.getElementById("connection").textContent.includes("已连接")');
 assert.deepEqual(await desktop.js('[...document.scripts].map(s=>s.src).filter(Boolean)'),[]);
 report('PASS desktop startup, synchronization and inline assets');
 const box=await desktop.js('(()=>{const b=document.getElementById("chart").getBoundingClientRect();return{x:b.x,y:b.y,width:b.width,height:b.height}})()');
 async function mouse(kind,x,y){await desktop.send('Input.dispatchMouseEvent',{type:kind,x:box.x+x*box.width,y:box.y+y*box.height,button:kind==='mouseMoved'?'none':'left',buttons:kind==='mouseReleased'?0:1,clickCount:1})}
 async function gesture(x,dx,dt){await mouse('mousePressed',x,.55);await sleep(dt);if(dx)await mouse('mouseMoved',x+dx,.55);await mouse('mouseReleased',x+dx,.55);await sleep(100)}
 await desktop.js('document.getElementById("record").click()');await desktop.wait('state.recording');
 await gesture(.3,0,80);await gesture(.4,0,260);await gesture(.5,.2,90);await gesture(.6,.05,360);
 await desktop.js('document.getElementById("record").click()');await desktop.wait('!state.recording && state.notes.length===4');
 assert.deepEqual((await request('/api/state')).notes.map(n=>n.type),[1,2,3,4]);
 await desktop.screenshot('desktop-four-gestures.png');report('PASS real mouse events: Tap / Hold / Flick / Drag, recording and stop');
 const downloads=path.join(artifacts,'downloads');await mkdir(downloads,{recursive:true});try{await unlink(path.join(downloads,'chart.json'))}catch(e){if(e.code!=='ENOENT')throw e}await desktop.send('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:downloads});
 await desktop.send('Input.dispatchKeyEvent',{type:'keyDown',key:'e',code:'KeyE',modifiers:2});await desktop.send('Input.dispatchKeyEvent',{type:'keyUp',key:'e',code:'KeyE',modifiers:2});
 let downloaded;for(let i=0;i<100&&!downloaded;i++){try{downloaded=JSON.parse(await readFile(path.join(downloads,'chart.json'),'utf8'))}catch{}await sleep(30)}assert.equal(downloaded.judgeLineList[0].notes.length,4);report('PASS Ctrl+E downloads real chart.json with four notes');
 await desktop.send('Input.dispatchKeyEvent',{type:'keyDown',key:'z',code:'KeyZ',modifiers:2});await desktop.send('Input.dispatchKeyEvent',{type:'keyUp',key:'z',code:'KeyZ',modifiers:2});await desktop.wait('state.notes.length===3');
 await desktop.js('document.querySelector(".note button").click()');await desktop.wait('state.notes.length===2');
 report('PASS Ctrl+Z undo and individual note deletion');
 await desktop.js('document.getElementById("clear").click()');await desktop.wait('state.notes.length===0');assert.ok(desktop.dialogs.some(d=>d.type==='confirm'));await desktop.js('document.getElementById("export").click()');for(let i=0;i<100&&!desktop.dialogs.some(d=>d.type==='alert');i++)await sleep(20);assert.ok(desktop.dialogs.some(d=>d.type==='alert'));report('PASS clear confirmation and empty-chart export rejection');
 await request('/api/reset',{});await desktop.js('touchchart.refresh()');
 const touch=await page('/touch',412,850,true);await touch.wait('synced && document.getElementById("status").textContent.includes("已连接")');
 await desktop.js('document.getElementById("record").click()');await desktop.wait('state.recording');
 const points=Array.from({length:5},(_,i)=>({x:45+i*78,y:350,id:i+1,radiusX:3,radiusY:3,force:1}));
 await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:points});await sleep(75);
 assert.equal((await request('/api/state')).slots.filter(s=>['按下','活动','中心'].includes(s.status)).length,5);
 assert.equal(await touch.js('fingers.size'),5);await touch.screenshot('mobile-five-fingers.png');
 await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await desktop.wait('state.notes.length===5');await desktop.js('document.getElementById("record").click()');await desktop.wait('!state.recording');
 for(const factor of [.25,1,1.23456789,2,5]){
  await desktop.js(`document.getElementById('scrollSpeed').value=${factor};saveScrollSpeed()`);
  const exported=await request('/api/chart');
  assert.ok(Math.abs(exported.judgeLineList[0].eventLayers[0].speedEvents[0].start-4.49145*factor)<1e-9);
  await desktop.js('refresh()');assert.equal(await desktop.js('Number(document.getElementById("scrollSpeed").value)'),factor);
  const drawn=await desktop.js(`(()=>{const old=ctx.fillRect,heads=[];ctx.fillRect=function(x,y,w,h){if(w===26&&h===10)heads.push(y+5);return old.call(this,x,y,w,h)};try{drawChart(0,[{type:1,positionX:0,_start:.1,_end:.1}]);return {head:heads[0],height:chart.clientHeight}}finally{ctx.fillRect=old}})()`);
  assert.ok(Math.abs(drawn.head/drawn.height-(.5+330/900-.1*.6*factor))<1e-9);
 }
 await desktop.js("document.getElementById('scrollSpeed').value=1;saveScrollSpeed()");
 report('PASS web fall speed matches exported Phira geometry at five continuous multipliers; refresh preserves selection');
 await request('/api/reset',{});await desktop.js('touchchart.refresh()');await desktop.js('document.getElementById("record").click()');await desktop.wait('state.recording');
 await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:points});await sleep(35);await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await desktop.wait('state.notes.length===5');assert.equal((await request('/api/state')).stats.maxMulti,5);
 await desktop.js('document.getElementById("record").click()');await desktop.wait('!state.recording');
 report('PASS five native touch points, five slots, five notes and multi 5');
 await touch.send('Network.enable');await touch.send('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:-1,uploadThroughput:-1});
 await touch.wait('!synced',10000);await touch.send('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});await touch.wait('synced',15000);
 report('PASS phone offline detection and automatic resynchronization');
 await request('/api/reset',{});await desktop.js('touchchart.refresh()');
 // A real local PCM WAV exercises the audio element without third-party codecs.
 const samples=44100*5, wav=Buffer.alloc(44+samples*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(44100,24);wav.writeUInt32LE(88200,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(samples*2,40);for(let i=0;i<samples;i++)wav.writeInt16LE(Math.round(Math.sin(i*2*Math.PI*440/44100)*1000),44+i*2);
 await desktop.send('Page.bringToFront');
 const wavPath=path.join(artifacts,'test-tone.wav');await writeFile(wavPath,wav);
 const doc=await desktop.send('DOM.getDocument');const file=await desktop.send('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'#file'});await desktop.send('DOM.setFileInputFiles',{nodeId:file.nodeId,files:[wavPath]});await desktop.wait('audio.duration===5');
 await desktop.js('document.getElementById("record").click()');await desktop.wait('state.recording && !audio.paused');await sleep(230);
 await mouse('mousePressed',.45,.55);await sleep(300);await desktop.js('document.getElementById("record").click()');await desktop.wait('!state.recording && state.notes.length===1');await mouse('mouseReleased',.45,.55);
 let recorded=await request('/api/state');assert.equal(recorded.notes[0].type,2);assert.equal(recorded.slots.filter(s=>s.status!=='空闲'&&s.status!=='离开').length,0);
 assert.equal((await request('/api/chart')).META.name,'test-tone');
 await desktop.js('touchchart.setPlaying(true)');await sleep(100);await desktop.js('touchchart.setPlaying(false)');
 const paused=await desktop.js('audio.currentTime');await sleep(150);assert.ok(Math.abs(await desktop.js('audio.currentTime')-paused)<.03);
 await desktop.js('document.getElementById("playhead").value=2;document.getElementById("playhead").dispatchEvent(new Event("input"))');await desktop.wait('Math.abs(audio.currentTime-2)<.03');
 await desktop.js('touchchart.setPlaying(true)');await sleep(100);assert.ok(await desktop.js('audio.currentTime>2'));await desktop.js('touchchart.setPlaying(false)');
 report('PASS WAV loading/playback, forced Hold settlement, pause, seek, resume and song metadata');
 const selftest=await page('/selftest?autorun',1400,1050);await selftest.wait('window.selftestResult!=null || document.getElementById("result")?.dataset.ok==="false"',60000);const results=await selftest.js('window.selftestResult');
 assert.equal(results.ok,true);assert.equal(results.performanceOK,true,JSON.stringify(results));
 await writeFile(path.join(artifacts,'browser-selftest.json'),JSON.stringify(results,null,2));await selftest.screenshot('browser-selftest.png');
 report(`PASS /selftest: 11/11; 1000 notes, 120 frames, mean=${results.meanMs.toFixed(3)}ms p95=${results.p95Ms.toFixed(3)}ms max=${results.maxMs.toFixed(3)}ms (<4ms)`);
 for(const c of clients)assert.deepEqual(c.errors,[],'Uncaught browser exception');report('PASS no uncaught JavaScript exceptions');
 report('BROWSER ACCEPTANCE PASSED (11/11)');
}catch(error){report('FAIL '+error.stack);for(let i=0;i<clients.length;i++){try{report('DIAGNOSTIC '+i+' '+JSON.stringify(await clients[i].js('({url:location.href,text:document.body.innerText,errors:typeof audio!=="undefined"?{src:audio.src,error:audio.error?.message,duration:audio.duration,ready:audio.readyState}:null})')));await clients[i].screenshot('failure-'+i+'.png')}catch{}}process.exitCode=1}
finally{
 await writeFile(path.join(artifacts,'browser-output.txt'),output.join('\n')+'\n');
 for(const c of clients)c.close();
 if(browser){const killer=spawn('taskkill',['/PID',String(browser.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});await new Promise(r=>killer.on('exit',r))}
 if(server)server.kill();
}
