"""Native PC relay client; the recording and audio remain local."""
import json
import threading
import time
import uuid
import urllib.request
import urllib.error
from urllib.parse import urlsplit
import touchchart as core

class Bridge:
    def __init__(self,server,url):
        u=urlsplit(url.strip().rstrip('/'))
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
            raise ValueError('请输入服务器根地址，例如 https://touch.example.com')
        if u.scheme!='https' and u.hostname not in ('localhost','127.0.0.1','::1'):raise ValueError('公网服务器必须使用 HTTPS')
        self.url=url.strip().rstrip('/');self.server=server;self.device=server.instance
        self.token='';self.stop_event=threading.Event();self.ack=0;self.message='正在连接';self.offset=0
        result=self.request('/create',{});self.token=result['token'];self.invite=result['invite']
        try:
            best=float('inf')
            for _ in range(5):
                start=core.epoch_ms();r=self.request('/api/sync');end=core.epoch_ms()
                if end-start<best:best=end-start;self.offset=r['now']-(start+end)/2
        except BaseException:
            self.request('/close',{});raise
        self.thread=threading.Thread(target=self.run,daemon=True)
    @property
    def mobile_url(self):return self.url+'/touch#'+self.invite
    def request(self,path,body=None):
        req=urllib.request.Request(self.url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Device':self.device,'Authorization':'Bearer '+self.token})
        try:
            with urllib.request.urlopen(req,timeout=4) as response:return json.load(response)
        except urllib.error.HTTPError as e:
            raise ValueError(json.load(e).get('error','连接失败')) from None
    def release(self):
        with self.server.lock:
            r=self.server.recorder
            for (sid,f),v in list(r.tracker.active.items()):
                if sid=='cloud-mobile':r.feed(dict(k='cancel',f=f,x=v['x'],y=v['y'],t=core.epoch_ms()),sid)
    def run(self):
        while not self.stop_event.is_set():
            try:
                result=self.request('/exchange',dict(ack=self.ack))
                for item in result['events']:
                    if item['seq']<=self.ack:continue
                    if item.get('reset'):self.release()
                    else:
                        e=dict(item['event']);e['t']-=self.offset
                        # Late input cannot be allowed to affect a later recording.
                        if abs(e['t']-core.epoch_ms())<2000:
                            with self.server.lock:
                                self.server.recorder.feed(e,'cloud-mobile')
                                self.server.revision+=1;self.server.changed.notify_all()
                    self.ack=item['seq']
                if not result['online']:self.release()
                self.message='互联网：手机已连接' if result['online'] else '互联网：等待已配对手机' if result['paired'] else '互联网：等待手机配对'
            except Exception as e:self.release();self.message='互联网断线：'+str(e);self.stop_event.wait(1)
            self.stop_event.wait(.02)
    def close(self):
        self.stop_event.set()
        if self.thread.is_alive():self.thread.join(5)
        self.release()
        try:self.request('/close',{})
        except Exception:pass
