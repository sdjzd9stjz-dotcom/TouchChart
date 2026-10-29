// Measured old/new UI latency, queue backpressure, dense rendering and audio-tempo workflow.
import {spawn} from 'node:child_process';
import {readFile,writeFile,mkdir,mkdtemp} from 'node:fs/promises';
import path from 'node:path';
import {tmpdir} from 'node:os';
import assert from 'node:assert/strict';
const root=process.cwd(),art=path.join(root,'artifacts/review');await mkdir(art,{recursive:true});
const python='C:/Users/terry/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const profile=await mkdtemp(path.join(tmpdir(),'touchchart-performance-'));
const edge=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--disable-background-timer-throttling','--disable-renderer-backgrounding','--autoplay-policy=no-user-gesture-required','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'});
const servers=[],clients=[],output=[],metrics={};const report=s=>{console.log(s);output.push(s)};
class Page{
 constructor(url){this.ws=new WebSocket(url);this.id=0;this.pending=new Map();this.errors=[];this.ready=new Promise((r,j)=>{this.ws.onopen=r;this.ws.onerror=j});this.ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=this.pending.get(m.id);if(p){this.pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}}else if(m.method==='Runtime.exceptionThrown')this.errors.push(m.params.exceptionDetails)}}
 async send(method,params={}){await this.ready;return new Promise((resolve,reject)=>{const id=++this.id,timer=setTimeout(()=>{this.pending.delete(id);reject(Error('CDP timeout '+method))},65000);this.pending.set(id,{resolve,reject,timer});this.ws.send(JSON.stringify({id,method,params}))})}
 async js(expression){const r=await this.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
 async wait(expression,timeout=15000){const deadline=Date.now()+timeout;while(Date.now()<deadline){try{if(await this.js(expression))return}catch{}await sleep(20)}throw Error('Wait timeout '+expression)}
}
function summary(values){const v=[...values].sort((a,b)=>a-b);return {samples:v.length,meanMs:v.reduce((a,b)=>a+b,0)/v.length,p50Ms:v[Math.floor(v.length*.5)],p95Ms:v[Math.floor(v.length*.95)],maxMs:v.at(-1)}}
try{
 let port;for(let i=0;i<150&&!port;i++){try{port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]}catch{}await sleep(50)}assert.ok(port);
 async function page(url){const target=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();const p=new Page(target.webSocketDebuggerUrl);clients.push(p);await p.send('Runtime.enable');await p.send('Page.enable');await p.send('Emulation.setDeviceMetricsOverride',{width:1440,height:1300,deviceScaleFactor:1,mobile:false});await p.send('Page.navigate',{url});return p}
 let after;
 for(const label of ['before','after']){
  const script=label==='before'?path.join(art,'baseline/touchchart.py'):path.join(root,'touchchart.py');const server=spawn(python,['-u',script,'--host','127.0.0.1','--port','0'],{cwd:root,windowsHide:true});servers.push(server);let stdout='';server.stdout.on('data',b=>stdout+=b);server.stderr.on('data',()=>{});for(let i=0;i<100&&!stdout.includes('desktop:');i++)await sleep(50);const base=stdout.match(/desktop: (\S+)/)[1];
  const request=async(route,body)=>{const r=await fetch(base+route,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();assert.ok(r.ok,JSON.stringify(d));return d};
  const desktop=await page(base+'/');await desktop.wait('window.touchchart&&synced');const phone=await page(base+'/touch');await phone.wait('synced&&document.getElementById("status").textContent.includes("已连接")');await desktop.send('Page.bringToFront');
  await desktop.js(`window.inputPaints=[];window.seenX=new Set();window.originalDrawZones=drawZones;drawZones=function(now){originalDrawZones(now);const s=state.slots.find(s=>s.x!=null&&s.status!=='离开'&&s.status!=='空闲');if(s&&!seenX.has(s.x)){seenX.add(s.x);inputPaints.push({x:s.x,t:performance.timeOrigin+performance.now()})}}`);
  const latencies=[];
  for(let i=0;i<24;i++){const x=.1+i*.025;const sent=await phone.js(`(()=>{const t=performance.timeOrigin+performance.now();sendEvents([{k:'down',f:0,x:${x},y:.5,t:epochNow()}]);return t})()`);await desktop.wait('inputPaints.length>'+i,3000);const paint=await desktop.js('inputPaints.at(-1).t');latencies.push(Math.max(0,paint-sent));await phone.js(`sendEvents([{k:'up',f:0,x:${x},y:.5,t:epochNow()}])`);await sleep(30)}
  metrics[label]={inputToCanvas:summary(latencies)};report(label+' input callback -> desktop canvas '+JSON.stringify(metrics[label].inputToCanvas));
  await request('/api/reset',{});await request('/api/session/start',{bpm:120,startEpoch:Date.now()});
  await phone.js(`window.originalApi=api;api=async function(...args){if(args[0]==='/api/events')await sleep(40);return originalApi(...args)}`);
  const burst=await phone.js(`(async()=>{const began=performance.now(),t=epochNow();for(let i=0;i<50;i++)sendEvents([{k:'down',f:i%5,x:.5,y:.5,t:t+i*10},{k:'up',f:i%5,x:.5,y:.5,t:t+i*10+5}]);await sendChain;return performance.now()-began})()`);
  const state=await request('/api/state');assert.equal(state.notes.length,50);metrics[label].burstWith40msRequestDelay={notes:50,events:100,drainMs:burst,missing:0};report(label+' 100-event burst / simulated 40ms request delay '+burst.toFixed(2)+'ms; 50/50 notes');
  if(label==='after')after={desktop,phone,request,base};else{desktop.ws.close();phone.ws.close()}
 }
 assert.ok(metrics.after.inputToCanvas.p95Ms<metrics.before.inputToCanvas.p95Ms,'Expected lower p95 canvas latency');
 assert.ok(metrics.after.burstWith40msRequestDelay.drainMs<metrics.before.burstWith40msRequestDelay.drainMs*.4,'Queue batching must prevent per-event RTT accumulation');
 const {desktop,request}=after;await request('/api/reset',{});await desktop.js('touchchart.refresh()');
 await desktop.js(`state.notes=Array.from({length:100000},(_,i)=>({id:i+1,type:i%4+1,startTime:[Math.floor(i/16),i%16,16],endTime:[Math.floor(i/16)+(i%4===1?1:0),i%16,16],positionX:(i%32/31-.5)*1200}));state.stats={duration:3125,total:100000};rebuildIndex();window.frameMeasures=[]`);
 const rendering=await desktop.js(`(async()=>{const samples=[];for(let i=0;i<120;i++){await new Promise(requestAnimationFrame);const start=performance.now();drawChart(i*.05);samples.push(performance.now()-start)}return samples})()`);
 metrics.denseRender={notes:100000,...summary(rendering)};assert.ok(metrics.denseRender.p95Ms<4);report('100000-note indexed rendering '+JSON.stringify(metrics.denseRender));
 await desktop.js('touchchart.refresh()');
 // Real PCM decoding tests the full browser analysis, not just the Python estimator.
 const sr=16000,duration=32,samples=duration*sr,wav=Buffer.alloc(44+samples*2);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(sr,24);wav.writeUInt32LE(sr*2,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(samples*2,40);
 const beats=[...Array.from({length:24},(_,i)=>i*.5),...Array.from({length:24},(_,i)=>12+i/3),...Array.from({length:18},(_,i)=>20+i*2/3)];let bi=0;
 for(let i=0;i<samples;i++){const t=i/sr;while(bi+1<beats.length&&beats[bi+1]<=t)bi++;const dt=t-beats[bi];wav.writeInt16LE(dt<.1?Math.round(Math.sin(t*2*Math.PI*880)*Math.exp(-dt*80)*22000):0,44+i*2)}
 const audioPath=path.join(art,'variable-tempo-120-180-90.wav');await writeFile(audioPath,wav);await desktop.send('Page.bringToFront');
 const doc=await desktop.send('DOM.getDocument');const file=await desktop.send('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'#file'});await desktop.send('DOM.setFileInputFiles',{nodeId:file.nodeId,files:[audioPath]});await desktop.wait('audio.duration===32');
 const analysis=await desktop.js('touchchart.analyzeAudio()');assert.equal(analysis.segments.length,3,JSON.stringify(analysis));for(let i=0;i<3;i++)assert.ok(Math.abs(analysis.segments[i].bpm-[120,180,90][i])<1);metrics.audioAnalysis=analysis;report('PASS real WAV decoding -> BPM segments '+JSON.stringify(analysis.segments));
 await desktop.js('document.getElementById("applyTempo").click();document.getElementById("gridLcm").click()');await desktop.wait('document.getElementById("division").value==="48"');assert.equal(await desktop.js('bpmSegments.length'),3);
 await desktop.js('document.getElementById("record").click()');await desktop.wait('state.recording');await sleep(30);await desktop.js('sendEvents([{k:"down",f:0,x:.5,y:.5,t:epochNow()},{k:"up",f:0,x:.5,y:.5,t:epochNow()+20}])');await desktop.wait('state.notes.length===1');await desktop.js('document.getElementById("record").click()');await desktop.wait('!state.recording');
 const chart=await request('/api/chart');assert.equal(chart.BPMList.length,3);assert.equal(chart.judgeLineList[0].notes[0].startTime[2],12);await writeFile(path.join(art,'browser-variable-chart.json'),JSON.stringify(chart,null,2));
 await desktop.js('document.getElementById("tempoPanel").open=true');const screenshot=await desktop.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});await writeFile(path.join(art,'variable-tempo-ui.png'),Buffer.from(screenshot.data,'base64'));report('PASS apply BPM segments + LCM(16,24)=48 -> recorded chart with 3 BPM segments');
 for(const p of clients)assert.equal(p.errors.length,0,JSON.stringify(p.errors));metrics.scope='Input callback to canvas submission on localhost; synthetic network delay is injected per request; not physical phone latency or a measured comparison against the video software.';
 await writeFile(path.join(art,'browser-performance.json'),JSON.stringify(metrics,null,2));report('PERFORMANCE AND TEMPO BROWSER TESTS PASSED');
}catch(e){report('FAIL '+e.stack);process.exitCode=1}
finally{await writeFile(path.join(art,'browser-performance-output.txt'),output.join('\n')+'\n');for(const p of clients)p.ws.close();for(const s of servers)s.kill();const kill=spawn('taskkill',['/PID',String(edge.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});await new Promise(r=>kill.on('exit',r))}
