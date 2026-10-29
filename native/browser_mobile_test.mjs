// Real Edge mobile touch events against the native desktop application's server.
import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const [base,artifacts]=process.argv.slice(2),logs=[];
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const report=s=>{logs.push(s);console.log(s)};
const profile=await mkdtemp(path.join(tmpdir(),'touchchart-native-mobile-'));
const edge=spawn(process.env.TOUCHCHART_EDGE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',[
 '--headless=new','--disable-gpu','--no-first-run','--disable-background-timer-throttling',
 '--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'});
let ws;
try{
 let port;for(let i=0;i<200&&!port;i++){try{port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]}catch{}await sleep(40)}
 assert.ok(port,'Edge DevTools available');
 const target=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();
 ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});
 let id=0;const pending=new Map(),errors=[];
 ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);if(p){pending.delete(m.id);clearTimeout(p.timer);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails)};
 const call=(method,params={})=>new Promise((resolve,reject)=>{const key=++id,timer=setTimeout(()=>{pending.delete(key);reject(Error('CDP timeout '+method))},15000);pending.set(key,{resolve,reject,timer});ws.send(JSON.stringify({id:key,method,params}))});
 const js=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true,userGesture:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
 const wait=async predicate=>{const end=Date.now()+20000;while(Date.now()<end){if(await predicate())return;await sleep(30)}throw Error('Condition timed out')};
 const api=async(route,body)=>{const r=await fetch(base+route,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});assert.ok(r.ok,'HTTP '+r.status);return r.json()};
 await call('Runtime.enable');await call('Page.enable');
 await call('Emulation.setDeviceMetricsOverride',{width:412,height:850,deviceScaleFactor:1,mobile:true});
 await call('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5});
 await call('Page.navigate',{url:base+'/'});
 await wait(()=>js('typeof synced!=="undefined" && synced'));
 assert.equal(await js('location.pathname'),'/touch');
 assert.deepEqual(await js('[...document.scripts].filter(s=>s.src).map(s=>s.src)'),[]);
 report('PASS native server root opens the mobile page and synchronizes without external assets');
 await api('/api/reset',{});await api('/api/session/start',{bpm:120,startEpoch:Date.now()});
 const points=Array.from({length:5},(_,i)=>({x:45+78*i,y:350,id:i+1,radiusX:3,radiusY:3,force:1}));
 await call('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:points});await sleep(35);
 await call('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await wait(async()=> (await api('/api/state')).notes.length===5);
 let state=await api('/api/state');assert.equal(state.stats.maxMulti,5);assert.ok(state.notes.every(n=>n.type===1));
 report('PASS five native browser touch points create five Tap notes and multi 5 in the desktop recorder');
 await api('/api/reset',{});await api('/api/session/start',{bpm:120,startEpoch:Date.now()});
 await call('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[points[0]]});
 await api('/api/events',{sid:'apk-compatibility-test',batch:1,events:[{k:'down',f:1,x:.8,y:.5,t:Date.now()}]});
 await sleep(30);await call('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await api('/api/events',{sid:'apk-compatibility-test',batch:2,events:[{k:'up',f:1,x:.8,y:.5,t:Date.now()}]});
 await wait(async()=> (await api('/api/state')).notes.length===2);
 assert.equal((await api('/api/chart')).judgeLineList[0].notes.length,2);
 report('PASS browser and APK-protocol inputs coexist in one exportable recording');
 await call('Network.enable');
 await call('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:-1,uploadThroughput:-1});
 await wait(()=>js('!synced'));
 await call('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});
 await wait(()=>js('synced && document.getElementById("status").textContent.includes("已连接")'));
 await call('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[points[0]]});await sleep(35);
 await call('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 await wait(async()=> (await api('/api/state')).notes.length===3);
 report('PASS browser offline detection, automatic resynchronization and resumed recording');
 await api('/api/reset',{});await api('/api/session/start',{bpm:120,startEpoch:Date.now()});
 const signed=await js(`(async()=>{
   const sent=[],originalFetch=window.fetch,capture=pad.setPointerCapture;
   window.fetch=(url,options)=>{if(url==='/api/events')sent.push(...JSON.parse(options.body).events);return originalFetch(url,options)};
   pad.setPointerCapture=()=>{}; // Synthetic PointerEvents have no OS pointer to capture.
   const ids=[-2147483648,-42,0,42,2147483647];
   try{
     for(const type of ['pointerdown','pointermove','pointerup'])for(const [i,id] of ids.entries())pad.dispatchEvent(new PointerEvent(type,{pointerId:id,pointerType:'touch',clientX:45+i*78,clientY:350,bubbles:true,cancelable:true}));
     await sendChain;return sent;
   }finally{window.fetch=originalFetch;pad.setPointerCapture=capture}
 })()`);
 assert.equal((await api('/api/state')).notes.length,5);
 const downs=signed.filter(e=>e.k==='down').map(e=>e.f);assert.equal(new Set(downs).size,5);
 assert.ok(signed.every(e=>Number.isSafeInteger(e.f)&&e.f>0));
 for(const f of downs)assert.deepEqual(signed.filter(e=>e.f===f).map(e=>e.k),['down','move','up']);
 report('PASS signed browser pointer IDs map to five distinct stable positive contact IDs');
 await api('/api/reset',{});await api('/api/session/start',{bpm:120,startEpoch:Date.now()});
 const released=await js(`(async()=>{
   const sent=[],originalFetch=window.fetch,capture=pad.setPointerCapture,visibility=Object.getOwnPropertyDescriptor(document,'visibilityState');
   window.fetch=(url,options)=>{if(url==='/api/events')sent.push(...JSON.parse(options.body).events);return originalFetch(url,options)};
   pad.setPointerCapture=()=>{};
   const fire=type=>pad.dispatchEvent(new PointerEvent(type,{pointerId:-42,pointerType:'touch',clientX:100,clientY:350,bubbles:true,cancelable:true}));
   try{
     fire('pointerdown');fire('pointercancel');fire('lostpointercapture');
     fire('pointerdown');fire('pointerup');fire('lostpointercapture');
     fire('pointerdown');Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));
     await sendChain;return {sent,fingers:fingers.size};
   }finally{window.fetch=originalFetch;pad.setPointerCapture=capture;if(visibility)Object.defineProperty(document,'visibilityState',visibility);else delete document.visibilityState}
 })()`);
 assert.equal(released.fingers,0);assert.equal((await api('/api/state')).notes.length,3);
 const reused=released.sent.filter(e=>e.k==='down').map(e=>e.f);assert.equal(new Set(reused).size,3);
 for(const [i,f] of reused.entries())assert.deepEqual(released.sent.filter(e=>e.f===f).map(e=>e.k),['down',i===1?'up':'cancel']);
 report('PASS recycled pointer IDs, cancellation and background release use the correct contact IDs');
 await api('/api/session/stop',{});assert.deepEqual(errors,[]);
 const shot=await call('Page.captureScreenshot',{format:'png'});await writeFile(path.join(artifacts,'native-mobile-browser.png'),Buffer.from(shot.data,'base64'));
 report('PASS no uncaught browser JavaScript exceptions');
 report('NATIVE MOBILE BROWSER ACCEPTANCE PASSED (7/7)');
}catch(error){report('FAIL '+error.stack);process.exitCode=1}
finally{if(ws)ws.close();edge.kill();await writeFile(path.join(artifacts,'native-mobile-browser-output.txt'),logs.join('\n')+'\n')}
