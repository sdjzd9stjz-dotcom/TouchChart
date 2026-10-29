"""Real HTTP isolation and native relay acceptance; no external services."""
import concurrent.futures
import json
import sys
import threading
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'touchchart'))
import touchchart as core
from cloud_server import Relay
from cloud_bridge import Bridge

def run():
    hub=Relay(('127.0.0.1',0));threading.Thread(target=hub.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{hub.server_port}';bridges=[];pcs=[];logs=[]
    def passed(s):logs.append('PASS '+s);print(logs[-1])
    def phone(token='',device=None):return dict(token=token,device=device or str(uuid.uuid4()))
    def req(c,path,body=None,expected=200):
        r=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'X-Device':c['device'],'Authorization':'Bearer '+c['token'],'Content-Type':'application/json'})
        try:response=urllib.request.urlopen(r,timeout=5)
        except urllib.error.HTTPError as e:response=e
        with response:result=json.load(response);assert response.status==expected,(path,response.status,result);return result
    try:
        for _ in range(2):
            pc=core.Server(('127.0.0.1',0));pcs.append(pc)
            b=Bridge(pc,base);bridges.append(b)
        a,b=bridges;pa,pb=phone(),phone()
        pa.update(req(pa,'/join',{'invite':a.invite}));pb.update(req(pb,'/join',{'invite':b.invite}))
        req(phone(),'/join',{'invite':a.invite},409)
        req(pa,'/join',{'invite':b.invite},409)
        passed('one mobile per PC, one room per mobile; competing devices rejected')
        req(phone(pa['token']),'/api/sync',expected=403)
        req(pa,'/exchange',{'ack':0},403);req(pa,'/api/control',{'action':'exit'},403)
        passed('copied token with wrong device and mobile control/owner access rejected')
        for p in (pa,pb):req(p,'/api/client/reset',{'sid':'test'})
        for pc in pcs:pc.recorder.start(dict(bpm=120,startEpoch=core.epoch_ms()))
        for bridge in bridges:bridge.thread.start()
        def send(p,x,count):
            for i in range(count):
                t=core.epoch_ms();body=dict(sid='test',batch=i,events=[dict(k=k,f=1,x=x,y=.5,t=t+(20 if k=='up' else 0)) for k in ('down','up')])
                req(p,'/api/events',body);assert req(p,'/api/events',body)['duplicate']
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results=[pool.submit(send,pa,.2,12),pool.submit(send,pb,.8,17)]
            for f in results:f.result()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and [len(pc.recorder.notes) for pc in pcs]!=[12,17]:time.sleep(.02)
        assert [len(pc.recorder.notes) for pc in pcs]==[12,17]
        assert all(n['positionX']<0 for n in pcs[0].recorder.notes)
        assert all(n['positionX']>0 for n in pcs[1].recorder.notes)
        passed('concurrent PC bridges receive only their own 12/17 notes; retries create no duplicates')
        req(pa,'/api/events',dict(sid='test',batch=99,events=[dict(k='down',f=2,x=.3,y=.5,t=core.epoch_ms())]))
        deadline=time.monotonic()+3
        while not pcs[0].recorder.tracker.active and time.monotonic()<deadline:time.sleep(.02)
        assert pcs[0].recorder.tracker.active
        req(pa,'/api/client/reset',{'sid':'new-page'})
        deadline=time.monotonic()+3
        while pcs[0].recorder.tracker.active and time.monotonic()<deadline:time.sleep(.02)
        assert not pcs[0].recorder.tracker.active
        req(pa,'/api/events',dict(sid='test',batch=100,events=[]),409)
        passed('refresh releases held fingers and replaces the previous touch page')
        a.close();req(pa,'/api/sync',expected=403)
        assert req(pb,'/api/sync')['now']>0
        passed('disconnect revokes old mobile credential without affecting other room')
        page=urllib.request.urlopen(base+'/touch').read().decode()
        assert 'type="module"' in page and 'history.replaceState' in page
        passed('browser page uses fragment invitation and authenticated requests; assets embedded')
        print(f'CLOUD SELFTEST PASSED ({len(logs)}/{len(logs)})')
    finally:
        for b in bridges:b.close()
        for pc in pcs:pc.server_close()
        hub.shutdown();hub.server_close()

if __name__=='__main__':run()
