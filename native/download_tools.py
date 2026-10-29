"""Fetch official portable build tools in verified byte ranges; no system installation."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parent/'.tools'
def fetch(item):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request=urllib.request.Request(item['url'],headers={'Range':'bytes=0-0'})
    with opener.open(request,timeout=30) as r:
        url=r.url;size=int(r.headers['Content-Range'].split('/')[-1])
    folder=ROOT/'chunks'/item['package'].replace(';','-');folder.mkdir(parents=True,exist_ok=True)
    unit=1024*1024
    def chunk(start):
        end=min(size,start+unit)-1;p=folder/str(start)
        if p.exists() and p.stat().st_size==end-start+1:return p
        for attempt in range(4):
            try:
                op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with op.open(urllib.request.Request(url,headers={'Range':f'bytes={start}-{end}'}),timeout=30) as r:
                    assert r.status==206 and r.headers['Content-Range'].startswith(f'bytes {start}-')
                    data=r.read()
                assert len(data)==end-start+1;p.write_bytes(data);return p
            except Exception:
                if attempt==3:raise
                time.sleep(1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:parts=list(pool.map(chunk,range(0,size,unit)))
    output=ROOT/(item['package'].replace(';','-')+'-complete.zip')
    with output.open('wb') as f:
        for part in parts:f.write(part.read_bytes())
    if item.get('sha1'):assert hashlib.sha1(output.read_bytes()).hexdigest()==item['sha1']
    if item.get('sha256'):assert hashlib.sha256(output.read_bytes()).hexdigest()==item['sha256']
    target=ROOT/'ready'/item['package'].replace(';','-');target.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output) as z:
        assert z.testzip() is None;z.extractall(target)
    print(item['package'],size,'VERIFIED AND EXTRACTED',flush=True)

if __name__=='__main__':
    ROOT.mkdir(parents=True,exist_ok=True)
    items=json.loads((ROOT.parent/'build-tools.json').read_text())
    for item in items:fetch(item)
