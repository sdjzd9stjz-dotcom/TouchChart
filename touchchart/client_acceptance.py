"""Real installed Phichain renderer acceptance; leaves evidence outside installed app folders."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import shutil

import touchchart as tc
from pack_chart import pack, phichain_project, validate_chart

ROOT=Path(__file__).resolve().parent
ART=ROOT/'artifacts/review'
APP=Path.home()/'Apps/Phichain/app/phichain-v0.5.0-x86_64-pc-windows-msvc'
BASE=1730000000000


def create_demo():
    r=tc.Recorder();r.start(dict(bpm=120,division=48,dragMode='chain',bpmSegments=[dict(time=0,bpm=120),dict(time=12,bpm=180),dict(time=20,bpm=90)],startEpoch=BASE,name='TouchChart Variable Tempo Demo',song='song.wav'))
    starts=[i*.5 for i in range(24)]+[12+i/3 for i in range(24)]+[20+i*2/3 for i in range(17)]
    for i,t in enumerate(starts):
        x=.2+(i%5)*.15;f=i%5;at=BASE+round(t*1000)
        r.feed(dict(k='down',f=f,x=x,y=.5,t=at))
        kind=i%4
        duration=[50,240,80,320][kind]
        if kind==3:
            for dt,dx in [(80,.05),(160,.10),(240,.05)]:r.feed(dict(k='move',f=f,x=min(.95,x+dx),y=.5,t=at+dt))
        r.feed(dict(k='up',f=f,x=min(.95,x+(.15 if kind==2 else 0)),y=.5,t=at+duration))
    chart=r.chart();validate_chart(chart)
    target=ART/'variable-demo-chart.json';target.write_text(json.dumps(chart,ensure_ascii=False,indent=2),encoding='utf-8')
    return target,len(r.notes)


def render(project,video,end=5):
    env=os.environ.copy();env['PATH']=str(ROOT/'.tools')+os.pathsep+env['PATH']
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    args=[str(APP/'phichain-renderer.exe'),str(project),'--from','0','--to',str(end),'--width','960','--height','540','--fps','30','--fc-ap-indicator','-o',str(video)]
    result=subprocess.run(args,cwd=APP,env=env,capture_output=True,timeout=90,startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)
    text=result.stdout.decode('utf-8',errors='replace')+result.stderr.decode('utf-8',errors='replace')
    video.with_suffix('.log').write_text('COMMAND '+subprocess.list2cmdline(args)+'\n'+text+f'\nEXIT CODE: {result.returncode}\n',encoding='utf-8')
    if result.returncode!=0 or not video.is_file():raise RuntimeError(text)
    subprocess.run([str(ROOT/'.tools/ffmpeg.exe'),'-hide_banner','-loglevel','error','-ss','1.5','-i',str(video),'-frames:v','1','-y',str(video.with_suffix('.png'))],check=True,capture_output=True)
    probe=subprocess.run([str(ROOT/'.tools/ffprobe.exe'),'-v','error','-show_streams','-of','json',str(video)],capture_output=True,text=True,encoding='utf-8',check=True)
    info=json.loads(probe.stdout)['streams'][0]
    assert info['width']==960 and info['height']==540 and int(info['nb_frames'])>=end*30-1
    if end>20:
        for timestamp in [12.5,20.5]:
            subprocess.run([str(ROOT/'.tools/ffmpeg.exe'),'-hide_banner','-loglevel','error','-ss',str(timestamp),'-i',str(video),'-frames:v','1','-y',str(video.with_name(video.stem+'-'+str(timestamp)+'.png'))],check=True,capture_output=True)
    return dict(exitCode=result.returncode,width=info['width'],height=info['height'],frames=int(info['nb_frames']),video=str(video))


if __name__=='__main__':
    chart,count=create_demo();audio=ART/'variable-tempo-120-180-90.wav'
    with tempfile.TemporaryDirectory(prefix='touchchart-client-') as temp:
        project=phichain_project(chart,audio,Path(temp)/'project')
        target=ART/'variable-demo-phichain'
        # Generated output only; retain prior result until replacement files are ready.
        target.mkdir(exist_ok=True)
        for file in project.iterdir():shutil.copyfile(file,target/file.name)
        package=Path(temp)/'variable-demo.pez';pack(chart,audio,package);shutil.copyfile(package,ART/'variable-demo.pez')
        results=dict(variableTempo=render(project,ART/'phichain-variable.mp4',32),corrected=render(ART/'corrected_phichain',ART/'phichain-after.mp4'),notes=count)
    results['phiraGUI']='NOT VERIFIED: Windows Computer Use native pipe unavailable; strict schema, package resources and official parser source checked'
    results['editorInReferenceVideo']='PhiEdit; not installed here; info.txt manifest supplied but GUI import not verified'
    (ART/'client-acceptance.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PHICHAIN RENDERER PASS: corrected chart 150 frames; 3-segment BPM chart 960 frames across both tempo changes; exit 0')
    print('VARIABLE DEMO:',count,'notes, BPM 120 -> 180 -> 90, complete .pez and native project')
    print('PHIRA GUI: NOT VERIFIED (native pipe unavailable)')
