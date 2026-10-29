import {spawn} from 'node:child_process';
import {mkdtemp,readFile,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const sleep=ms=>new Promise(r=>setTimeout(r,ms)), logs=[], sockets=[];
const py=process.env.TOUCHCHART_PYTHON||'C:/Users/terry/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe';
let serverText='';const server=spawn(py,['-u','touchchart/cloud_server.py','--port','0'],{windowsHide:true});server.stdout.on('data',d=>serverText+=d);server.stderr.on('data',d=>process.stderr.write(d));
const profile=await mkdtemp(path.join(tmpdir(),'touchchart-cloud-'));
let edge;
const log=s=>{logs.push(s);console.log(s)};
try{
 for(let i=0;i<100&&!serverText.includes('http');i++)await sleep(50);
 const base=serverText.match(/http:\/\/\S+/)?.[0];assert.ok(base);
 const api=async(c,p,b)=>{const r=await fetch(base+p,{method:b===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Device':c.device,Authorization:'Bearer '+(c.token||'')},body:b===undefined?undefined:JSON.stringify(b)});const d=await r.json();assert.ok(r.ok,JSON.stringify(d));return d};
 const pcs=[];for(let i=0;i<2;i++){const pc={device:crypto.randomUUID()};Object.assign(pc,await api(pc,'/create',{}));pcs.push(pc)}
 edge=spawn('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',['--headless=new','--disable-gpu','--no-first-run','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:'ignore'});
 let port;for(let i=0;i<150&&!port;i++){try{port=(await readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0]}catch{}await sleep(40)}assert.ok(port);
 async function page(invite){
  const t=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:'PUT'})).json();const ws=new WebSocket(t.webSocketDebuggerUrl);sockets.push(ws);await new Promise(r=>ws.onopen=r);
  let id=0;const pending=new Map();ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}};
  const call=(method,params={})=>new Promise((resolve,reject)=>{const key=++id;pending.set(key,{resolve,reject});ws.send(JSON.stringify({id:key,method,params}))});
  const js=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value};
  await call('Page.enable');await call('Runtime.enable');
  await call('Page.addScriptToEvaluateOnNewDocument',{source:`localStorage.tcDevice=${JSON.stringify(crypto.randomUUID())};localStorage.removeItem('tcMobile');`});
  await call('Emulation.setDeviceMetricsOverride',{width:412,height:850,deviceScaleFactor:1,mobile:true});await call('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:5});
  await call('Page.navigate',{url:base+'/touch#'+invite});return {call,js};
 }
 const wait=async f=>{for(let i=0;i<150;i++){if(await f())return;await sleep(40)}throw Error('Browser condition timeout')};
 const pages=[];for(const pc of pcs){const p=await page(pc.invite);await wait(()=>p.js('document.getElementById("status")?.textContent.includes("已连接")'));pages.push(p)}
 log('PASS two real Edge mobile pages pair with independent PC rooms');
 for(const [i,p] of pages.entries()){
  await p.call('Page.bringToFront');
  const points=Array.from({length:i?3:5},(_,j)=>({id:j+1,x:45+j*70,y:350}));
  await p.call('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:points});await sleep(40);await p.call('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 }
 await sleep(300);
 const results=await Promise.all(pcs.map(pc=>api(pc,'/exchange',{ack:0})));
 assert.deepEqual(results.map(r=>r.events.filter(e=>e.event?.k==='down').length),[5,3]);
 assert.deepEqual(results.map(r=>r.events.filter(e=>e.event?.k==='up').length),[5,3]);
 log('PASS actual 5-finger/3-finger browser touches reach only their paired PC queues');
 const rejected=await page(pcs[0].invite);await wait(()=>rejected.js('document.getElementById("warning")?.textContent.includes("另一台手机")'));
 log('PASS third browser device visibly rejected from occupied PC room');
 assert.equal(await pages[0].js('location.hash'),'');
 log('PASS invitation removed from address after successful pairing');
 log('CLOUD BROWSER SELFTEST PASSED (4/4)');
}catch(e){log('FAIL '+e.stack);process.exitCode=1}
finally{for(const s of sockets)s.close();edge?.kill();server.kill();await writeFile('native/artifacts/cloud-browser-selftest.txt',logs.join('\n')+'\n')}


