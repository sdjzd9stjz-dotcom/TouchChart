"""Authenticated one-PC/one-browser relay. Audio and charts stay on the PC."""
import argparse
import json
import secrets
import threading
import time
from collections import deque
from urllib.parse import urlsplit
import touchchart as core

BOOT = r"""
const device=localStorage.tcDevice||(localStorage.tcDevice=crypto.randomUUID());
let credential=JSON.parse(localStorage.tcMobile||'null');
const raw=window.fetch.bind(window);
async function boot(){
 const invite=location.hash.slice(1);
 if(invite){const r=await raw('/join',{method:'POST',headers:{'Content-Type':'application/json','X-Device':device},body:JSON.stringify({invite})});const d=await r.json();if(!r.ok)throw Error(d.error);credential=d;localStorage.tcMobile=JSON.stringify(d);history.replaceState(null,'','/touch')}
 if(!credential)throw Error('请从电脑提供的配对链接进入');
}
try{await boot()}catch(error){document.getElementById('warning').textContent=error.message;document.getElementById('status').textContent='配对失败';throw error}
const fetch=(url,options={})=>raw(url,{...options,headers:{...options.headers,Authorization:'Bearer '+credential.token,'X-Device':device}});
"""

def mobile_page():
    page=core.TOUCH_HTML.replace('<script>','<script type="module">'+BOOT,1)
    return page.replace('</script>','</script><script>window.addEventListener("unhandledrejection",e=>{document.getElementById("warning").textContent=e.reason?.message||"连接失败"})</script>',1)

class Relay(core.Server):
    def __init__(self,address,max_rooms=100):
        super().__init__(address)
        self.RequestHandlerClass=Handler
        self.rooms={};self.max_rooms=max_rooms
        self.capacity=threading.BoundedSemaphore(128)
    def process_request(self,request,address):
        if not self.capacity.acquire(False):self.shutdown_request(request);return
        try:super().process_request(request,address)
        except BaseException:self.capacity.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.capacity.release()

class Handler(core.Handler):
    def setup(self):
        super().setup();self.connection.settimeout(10)
    def log_message(self,*args):pass
    def end_headers(self):
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        super().end_headers()
    def dispatch(self):
        self.close_connection=True
        path=urlsplit(self.path).path
        try:
            if self.command=='GET' and path=='/':
                return self.reply('<meta charset="utf-8"><h1>TouchChart 互联网中继</h1><p>在 Windows 软件中填写本站地址并创建配对；手机打开电脑生成的专属链接。</p>',content_type='text/html; charset=utf-8')
            if self.command=='GET' and path=='/touch':return self.reply(mobile_page(),content_type='text/html; charset=utf-8')
            if self.command=='GET' and path=='/health':return self.reply({'ok':True})
            if self.command not in ('GET','POST'):return self.reply({'error':'方法不支持'},405)
            origin=self.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host'):return self.reply({'error':'拒绝跨站请求'},403)
            n=int(self.headers.get('Content-Length','0'))
            if n<0 or n>262144:raise ValueError('请求过大')
            body=json.loads(self.rfile.read(n) or b'{}') if self.command=='POST' else {}
            if not isinstance(body,dict):raise ValueError('请求须为对象')
            device=self.headers.get('X-Device','')
            if not 16<=len(device)<=128:raise ValueError('设备标识无效')
            now=time.monotonic()
            with self.server.lock:
                rooms=self.server.rooms
                for key in list(rooms):
                    if now-rooms[key]['seen']>3600:del rooms[key]
                if path=='/create' and self.command=='POST':
                    # A device cannot claim a second PC or mobile role simultaneously.
                    if any(device in (r['pc'],r['mobile']) for r in rooms.values()):return self.reply({'error':'此设备已有配对，请先断开'},409)
                    if len(rooms)>=self.server.max_rooms:return self.reply({'error':'房间已满，请稍后重试'},429)
                    token=secrets.token_urlsafe(32);invite=secrets.token_urlsafe(24)
                    rooms[token]=dict(pc=device,mobile=None,phone=None,invite=invite,seen=now,pc_seen=now,phone_seen=now,queue=deque(),seq=0,ack=0,receipts={},sid=None)
                    return self.reply(dict(token=token,invite=invite))
                if path=='/join' and self.command=='POST':
                    r=next((r for r in rooms.values() if secrets.compare_digest(r['invite'],str(body.get('invite','')))),None)
                    if not r:return self.reply({'error':'配对链接无效或已过期'},403)
                    if r['mobile'] and r['mobile']!=device:return self.reply({'error':'这台电脑已绑定另一台手机'},409)
                    if any(device in (x['pc'],x['mobile']) for x in rooms.values() if x is not r) or r['pc']==device:return self.reply({'error':'此设备已用于其他配对'},409)
                    r['mobile']=device;r['phone']=r['phone'] or secrets.token_urlsafe(32);r['seen']=now;r['phone_seen']=now
                    return self.reply(dict(token=r['phone']))
                token=self.headers.get('Authorization','').removeprefix('Bearer ')
                r=rooms.get(token);owner=r is not None
                if not r:r=next((x for x in rooms.values() if x['phone'] and secrets.compare_digest(x['phone'],token)),None)
                if not r or device!=r['pc' if owner else 'mobile']:return self.reply({'error':'配对已失效，请重新连接'},403)
                r['seen']=now
                if owner:r['pc_seen']=now
                else:
                    if now-r['pc_seen']>10:return self.reply({'error':'电脑已离线，等待电脑重连'},409)
                    r['phone_seen']=now
                if path=='/api/sync' and self.command=='GET':return self.reply(dict(now=core.epoch_ms()))
                if owner:
                    if self.command!='POST':return self.reply({'error':'方法不支持'},405)
                    if path=='/close':del rooms[token];return self.reply({'ok':True})
                    if path=='/unpair':
                        r.update(mobile=None,phone=None,invite=secrets.token_urlsafe(24),sid=None,receipts={})
                        r['queue'].clear();r['seq']+=1;r['queue'].append(dict(seq=r['seq'],reset=True))
                        return self.reply(dict(invite=r['invite']))
                    if path=='/exchange':
                        ack=body.get('ack',0)
                        if type(ack)!=int or not r['ack']<=ack<=r['seq']:raise ValueError('确认序号无效')
                        r['ack']=ack
                        while r['queue'] and r['queue'][0]['seq']<=ack:r['queue'].popleft()
                        return self.reply(dict(events=list(r['queue']),paired=bool(r['phone']),online=bool(r['phone']) and now-r['phone_seen']<6,now=core.epoch_ms()))
                    return self.reply({'error':'路径不存在'},404)
                if path=='/api/client/reset' and self.command=='POST':
                    sid=body.get('sid')
                    if not isinstance(sid,str) or not 1<=len(sid)<=128:raise ValueError('sid 无效')
                    r['sid']=sid;r['receipts'].clear();r['queue'].clear()
                    r['seq']+=1;r['queue'].append(dict(seq=r['seq'],reset=True))
                    return self.reply({'ok':True})
                if path=='/api/events' and self.command=='POST':
                    sid,events=core.validate_events(body)
                    if sid!=r['sid']:return self.reply({'error':'触控页面已被新页面替代，请刷新'},409)
                    batch=body.get('batch')
                    if type(batch)!=int or batch<0:raise ValueError('batch 无效')
                    if batch in r['receipts']:return self.reply(dict(ok=True,duplicate=True))
                    if any(abs(e['t']-core.epoch_ms())>10000 for e in events):raise ValueError('事件已过期，请重新同步')
                    if len(r['queue'])+len(events)>5000:return self.reply({'error':'电脑接收积压，请暂停后重连'},429)
                    for e in events:r['seq']+=1;r['queue'].append(dict(seq=r['seq'],event=e))
                    r['receipts'][batch]=True
                    if len(r['receipts'])>4096:del r['receipts'][next(iter(r['receipts']))]
                    return self.reply(dict(ok=True))
                return self.reply({'error':'此设备无权执行该操作'},403)
        except (ValueError,TypeError,KeyError) as e:self.reply({'error':str(e)},400)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8791);a=p.parse_args()
    server=Relay((a.host,a.port));print(f'TouchChart relay: http://{a.host}:{server.server_port}',flush=True)
    server.serve_forever()
