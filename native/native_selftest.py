"""Run real native widgets, audio, HTTP input, exports and conversion in isolation."""
import json
import math
from pathlib import Path
import tempfile
import time
import traceback
from types import SimpleNamespace
import urllib.request
import zipfile
import tkinter as tk
from unittest import mock

def run(output):
    from desktop import Desktop, HERE, decode_audio, analyze_wav, core, validate_package, convert_json_to_phira, pack
    logs=[];app=None;started=time.perf_counter()
    def passed(text):logs.append('PASS '+text)
    try:
        root=tk.Tk();root.withdraw();app=Desktop(root,0);root.update_idletasks()
        assert isinstance(app.canvas,tk.Canvas) and len(app.fields)==7
        passed('native Tk window, Canvas, configuration controls; no browser process')
        app.fields['lanes'].set('4');app.set_half_snap();app.set_lane_span(.6)
        with mock.patch.object(app.canvas,'winfo_width',return_value=1000),mock.patch.object(app.canvas,'winfo_height',return_value=600):
            app.draw(0)
            xs=[app.canvas.coords(i)[0] for i in app.canvas.find_withtag('lane-guide')]
            assert all(math.isclose(a,b) for a,b in zip(xs,[200,400,600,800])) and len(xs)==4
            assert len(app.canvas.find_withtag('beat-guide'))>2
            app.set_lane_span(.33333333);app.draw(0)
            xs=[app.canvas.coords(i)[0] for i in app.canvas.find_withtag('lane-guide')]
            assert math.isclose(xs[1]-xs[0],111.11111)
        app.set_lane_span(.6)
        r=app.recorder
        r.start(dict(app.config(),bpm=120,bpmSegments=[dict(time=0,bpm=120),dict(time=12,bpm=180),dict(time=20,bpm=90)],startEpoch=1000))
        for i,seconds in enumerate((.26,12.17,20.32)):
            r.feed(dict(k='down',f=i,x=.1,y=.5,t=1000+seconds*1000))
            r.feed(dict(k='up',f=i,x=.1,y=.5,t=1050+seconds*1000))
        r.stop(23000)
        assert [core.beat_value(n['startTime']) for n in r.notes]==[.5,24.5,48.5]
        assert all(math.isclose(n['positionX'],-405) for n in r.notes)
        before=[n['startTime'][:] for n in r.notes]
        app.saved_seq=tuple(n['id'] for n in r.notes);app.set_lane_span(.3)
        assert all(math.isclose(n['positionX'],-202.5) for n in r.notes) and app.saved_seq is None
        assert [n['startTime'] for n in r.notes]==before
        app.set_lane_span(.6);assert all(math.isclose(n['positionX'],-405) for n in r.notes)
        passed('continuous lane spacing redraws exact snap targets; existing snapped notes rescale reversibly and mark unsaved')
        for seconds,expected in ((.26,.5),(12.17,24.5),(20.32,48.5)):
            beat=core.quantize_beat(seconds,120,0,8,r.config['bpmSegments'])
            assert beat==expected
        for bad in (0,-1,1.01,float('nan'),float('inf'),True):
            try:core.validate_config(dict(laneSpan=bad));raise AssertionError('invalid lane spacing accepted')
            except ValueError:pass
        assert core.position_x(0,4,.6)==-405
        assert math.isclose(core.position_x(1,4,.6),405)
        passed('half-beat snap follows 120/180/90 BPM changes (250/166.667/333.333 ms); invalid spacing rejected')
        app.clear(False);app.fields['lanes'].set('0');app.fields['division'].set('16');app.set_lane_span(1)
        def gesture(settings,points,mode='auto',chain='single'):
            r=core.Recorder();r.gestures=dict(settings);r.start(dict(bpm=120,startEpoch=1000,noteMode=mode,dragMode=chain))
            for k,t,x in points:r.feed(dict(k=k,t=1000+t,f=1,x=x,y=.5))
            return r
        jitter=[('down',0,.5)]+[('move',i*10,.535 if i%2 else .465) for i in range(1,80)]+[('up',800,.535)]
        for chain in ('single','chain'):
            r=gesture(core.GESTURE_DEFAULTS,jitter,chain=chain)
            assert len(r.notes)==1 and r.notes[0]['type']==2 and core.beat_value(r.notes[0]['endTime'])>core.beat_value(r.notes[0]['startTime'])
        deliberate=[('down',0,.5),('move',300,.6),('up',600,.5)]
        assert all(n['type']==4 for n in gesture(core.GESTURE_DEFAULTS,deliberate,chain='chain').notes)
        slow_flick=[('down',0,.5),('up',250,.65)]
        assert gesture(core.GESTURE_DEFAULTS,slow_flick).notes[0]['type']==3
        assert gesture(dict(core.GESTURE_DEFAULTS,flickMs=200),slow_flick).notes[0]['type']==4
        assert gesture(dict(core.GESTURE_DEFAULTS,flickDistance=.2),slow_flick).notes[0]['type']==4
        assert gesture(dict(core.GESTURE_DEFAULTS,holdMs=1000),jitter).notes[0]['type']==1
        assert gesture(dict(core.GESTURE_DEFAULTS,dragDistance=.02),jitter).notes[0]['type']==4
        assert gesture(core.GESTURE_DEFAULTS,deliberate,mode='hold').notes[0]['type']==2
        passed('Hold jitter stays Hold in single/chain modes; intentional curves and configurable Flick/Hold/Drag thresholds work')
        with app.server.lock:
            r=app.recorder;r.start(dict(bpm=120,startEpoch=1000))
            r.feed(dict(k='down',t=1000,f=1,x=.5,y=.5))
            app.apply_gestures(dict(core.GESTURE_DEFAULTS,dragDistance=.08))
            r.feed(dict(k='up',t=1500,f=1,x=.56,y=.5))
            r.feed(dict(k='down',t=2000,f=1,x=.5,y=.5))
            r.feed(dict(k='up',t=2500,f=1,x=.56,y=.5))
            assert [n['type'] for n in r.notes]==[4,2]
            before=dict(r.gestures)
            for key,value in [('holdMs',float('nan')),('dragDistance',-.1),('flickMs',True),('flickDistance',.01)]:
                try:app.apply_gestures(dict(before,**{key:value}));raise AssertionError('Invalid gestures accepted')
                except ValueError:pass
                assert r.gestures==before
        app.clear(False);assert app.recorder.gestures==before
        app.apply_gestures(core.GESTURE_DEFAULTS)
        passed('live sensitivity snapshots each press, preserves existing notes, rejects invalid values and survives clear')
        op=urllib.request.build_opener(urllib.request.ProxyHandler({}));base=f'http://127.0.0.1:{app.server.server_port}'
        def request(route,body=None):
            req=urllib.request.Request(base+route,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
            with op.open(req,timeout=5) as r:return json.loads(r.read())
        assert abs(request('/api/sync')['now']-core.epoch_ms())<1000
        passed('embedded server reachable over real HTTP')
        for route in ('/', '/touch', '/touch/'):
            with op.open(base+route) as response:
                page=response.read().decode('utf-8')
                assert response.status==200 and response.geturl()==base+'/touch'
                assert 'id="pad"' in page and 'pointerdown' in page and 'id="record"' not in page
        assert app.mobile_url==f'http://{app.address}/touch'
        passed('mobile browser page and root redirect served by the native application')
        app.merge_grid();assert app.fields['division'].get()=='48'
        app.set_tempo([dict(time=0,bpm=120),dict(time=12,bpm=180),dict(time=20,bpm=90)])
        assert len(app.config()['bpmSegments'])==3
        passed('native LCM(16,24)=48 and BPM segment editor')
        fixture=HERE/'test-fixtures/variable-tempo-120-180-90.wav'
        if not fixture.exists():fixture=HERE.parent/'touchchart/artifacts/review/variable-tempo-120-180-90.wav'
        wav=Path(app.temp.name)/'test.wav';duration=decode_audio(fixture,wav)
        assert 31.9<duration<32.1
        app.audio.load(wav);app.audio.play(0);time.sleep(.04);app.audio.pause()
        passed('bundled FFmpeg PCM decoding and real Windows MCI playback/pause')
        result=analyze_wav(wav,120)
        assert [(p['time'],p['bpm']) for p in result['segments']]==[(0,120),(12,180),(20,90)],result
        passed('native audio analysis: 120 -> 180 -> 90 BPM at 0/12/20 seconds')
        app.source=fixture;app.decoded=wav;app.duration=duration
        app.toggle_record();assert app.recorder.recording
        app.pointer('down',SimpleNamespace(x=0,y=0));time.sleep(.04);app.pointer('up',SimpleNamespace(x=0,y=0))
        assert len(app.recorder.notes)==1
        app.toggle_record();assert not app.recorder.recording
        passed('native mouse callback -> recorded Tap -> stop')
        app.clear(False);app.set_tempo([]);app.fields['division'].set('16')
        stamp=core.epoch_ms();request('/api/session/start',{'bpm':120,'startEpoch':stamp})
        events=[dict(k=k,f=f,x=.1+f*.18,y=.5,t=stamp+100+(50 if k=='up' else 0)) for k in ['down','up'] for f in range(5)]
        payload={'sid':'apk-native-selftest','batch':1,'events':events}
        request('/api/events',payload);assert request('/api/events',payload)['duplicate']
        assert len(app.recorder.notes)==5 and app.recorder.max_multi==5
        request('/api/session/stop',{})
        passed('five-finger real HTTP input, multi 5 and duplicate-batch protection')
        app.draw(0);assert app.note_list.size()==5
        app.note_list.selection_set(0);app.delete_selected();assert len(app.recorder.notes)==4
        app.undo();assert len(app.recorder.notes)==3
        passed('native preview index, selected-note delete and undo')
        baseline=app.recorder.chart()
        before=json.dumps(app.recorder.notes,sort_keys=True)
        app.saved_seq=tuple(n['id'] for n in app.recorder.notes)
        app.speed_scale.set(2)
        assert app.speed_text.get()=='2' and app.recorder.meta['scrollSpeed']==2
        assert app.saved_seq is None
        changed=app.recorder.chart()
        assert changed['BPMList']==baseline['BPMList']
        assert changed['judgeLineList'][0]['notes']==baseline['judgeLineList'][0]['notes']
        assert math.isclose(changed['judgeLineList'][0]['eventLayers'][0]['speedEvents'][0]['start'],8.9829)
        app.toggle_record();anchor=app.recorder.anchor
        app.set_scroll_speed(3)
        assert app.recorder.recording and app.playing and app.recorder.anchor==anchor
        app.toggle_record()
        assert json.dumps(app.recorder.notes,sort_keys=True)==before
        app.set_scroll_speed(2)
        passed('live fall-speed slider changes export speed without altering notes, BPM or recording transport; marks unsaved')
        with tempfile.TemporaryDirectory(prefix='touchchart-native-export-') as folder:
            chart=Path(folder)/'chart.json';app.export_to(chart)
            assert len(json.loads(chart.read_text(encoding='utf-8'))['judgeLineList'][0]['notes'])==3
            package=Path(folder)/'demo.pez';app.package_to(package)
            with zipfile.ZipFile(package) as z:assert z.testzip() is None and len(z.namelist())==5
            passed('native RPE export and complete five-file .pez with valid CRC')
            project=Path(folder)/'project';app.package_to(project,True)
            native=json.loads((project/'chart.json').read_text())
            assert len(native['lines'][0]['notes'])==3
            passed('native Phichain project export through installed real converter')
            with zipfile.ZipFile(package) as archive:
                packed=json.loads(archive.read('chart.json'))
            assert math.isclose(packed['judgeLineList'][0]['eventLayers'][0]['speedEvents'][0]['start'],8.9829)
            speed_events=[e for e in native['lines'][0]['events'] if e['kind']=='speed']
            assert speed_events and all(math.isclose(e['value']['transition']['start'],8.9829,rel_tol=1e-6) and math.isclose(e['value']['transition']['end'],8.9829,rel_tol=1e-6) for e in speed_events),speed_events
            passed('2x fall speed survives complete PEZ and real Phichain conversion')
            default=Path(folder)/'default-export.pez'
            finished=[]
            original_saved=app.package_saved
            app.package_saved=lambda path:finished.append(path)
            try:
                with mock.patch('desktop.filedialog.asksaveasfilename',return_value=str(default)) as chooser:
                    app.export_dialog()
                    deadline=time.monotonic()+15
                    while app.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
                assert chooser.call_args.kwargs['defaultextension']=='.pez'
                assert not app.busy and finished==[default]
                assert app.export_button.cget('text')=='导出 Phira 谱包'
                report=validate_package(default)
                assert report['eocd'] and report['crc'] and report['notes']==3
            finally:app.package_saved=original_saved
            passed('primary export creates a complete Phira .pez, with EOCD, CRC, audio and background')
            converted=Path(folder)/'existing-json.pez'
            before=chart.read_bytes()
            convert_json_to_phira(chart,fixture,converted)
            assert validate_package(converted)['notes']==3 and chart.read_bytes()==before
            passed('existing RPE JSON plus selected music converts to a complete Phira package')
            bad=Path(folder)/'bad.pez'
            for content in (before,default.read_bytes()[:-22]):
                bad.write_bytes(content)
                try:validate_package(bad);raise AssertionError('Invalid archive accepted')
                except ValueError:pass
            try:pack(chart,wav,Path(folder)/'wrong.json');raise AssertionError('Wrong extension accepted')
            except ValueError:pass
            interrupted=Path(folder)/'interrupted.pez'
            with mock.patch('pack_chart.zipfile.ZipFile.write',side_effect=OSError('simulated interrupted write')):
                try:pack(chart,wav,interrupted);raise AssertionError('Interrupted write succeeded')
                except OSError:pass
            assert not interrupted.exists() and not list(Path(folder).glob('.touchchart-*.part'))
            passed('raw JSON, truncated EOCD and wrong extensions rejected; interrupted export leaves no partial package')
            preserved=json.dumps(app.recorder.notes,sort_keys=True)
            app.decoded=None
            with mock.patch('desktop.filedialog.asksaveasfilename') as chooser:
                try:app.export_dialog();raise AssertionError('Export without music accepted')
                except ValueError:pass
                chooser.assert_not_called()
            app.load_audio(fixture)
            deadline=time.monotonic()+15
            while app.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
            assert not app.busy and app.decoded.is_file()
            assert json.dumps(app.recorder.notes,sort_keys=True)==preserved
            passed('missing music is explained; attaching music later preserves recorded notes')
        original=app.server.recorder
        try:
            r=core.Recorder();r.start(dict(bpm=120,division=16,bpmSegments=[dict(time=0,bpm=120),dict(time=12,bpm=180)],startEpoch=1000))
            for i,(kind,a,b) in enumerate([(1,.5,.5),(2,.5,1),(3,.5,.5),(4,.5,.5),(1,5.5,5.5),(2,0,20)]):
                r.notes.append(dict(id=i+1,type=kind,startTime=core.beat_triple(core.seconds_to_beat(a,120,r.config['bpmSegments']),4),endTime=core.beat_triple(core.seconds_to_beat(b,120,r.config['bpmSegments']),4),positionX=(i-2)*180))
            r.recount();app.server.recorder=r;app.index_key=None
            coords={}
            with mock.patch.object(app.canvas,'winfo_width',return_value=800),mock.patch.object(app.canvas,'winfo_height',return_value=600):
                for factor in (.25,1,2,5):
                    app.set_scroll_speed(factor);app.draw(0)
                    heads=app.canvas.find_withtag('note-head')
                    if factor==.25:assert app.canvas.find_withtag('note-5'),'Slow preview dropped visible note at 5.5s'
                    if factor in (1,2):
                        coords[factor]=[app.canvas.coords(app.canvas.find_withtag(f'note-{i}')[0]) for i in range(1,5)]
                    if factor==5:assert not app.canvas.find_withtag('note-1'),'Offscreen note was not culled'
                    app.draw(15)
                    assert app.canvas.find_withtag('note-6'),'Crossing Hold body vanished'
                line=600*(.5+330/900)
                for i in (0,2,3):
                    y1=(coords[1][i][1]+coords[1][i][3])/2;y2=(coords[2][i][1]+coords[2][i][3])/2
                    assert abs((line-y2)-2*(line-y1))<.001
                assert abs((coords[2][1][3]-coords[2][1][1])-2*(coords[1][1][3]-coords[1][1][1]))<.001
            passed('Canvas speed scales all four note types; slow horizon beyond 5s and crossing Hold remain visible')
            # Independent reference: Phira v0.8.2 RPE parser + note renderer + camera.
            # Integrate exported speed in seconds, divide by aspect, project with camera.
            samples=0;max_error=0
            for w,h in [(920,290),(800,600),(1920,1080),(1080,1920)]:
                with mock.patch.object(app.canvas,'winfo_width',return_value=w),mock.patch.object(app.canvas,'winfo_height',return_value=h):
                    for factor in (.25,.5,1,1.23456789,2,5):
                        app.set_scroll_speed(factor)
                        chart=r.chart();layer=chart['judgeLineList'][0]['eventLayers'][0]
                        speed_event=layer['speedEvents'][0]
                        assert speed_event['start']==speed_event['end']
                        assert math.isclose(speed_event['start'],4.49145*factor)
                        phira_speed=speed_event['start']*(10/45/.83175)
                        line=h*(.5-layer['moveYEvents'][0]['start']/900)
                        # Four note heads, before and after BPM change (120 -> 180).
                        for moment in (.4,12.125):
                            app.draw(moment)
                            for n in r.notes:
                                a=core.note_seconds(core.beat_value(n['startTime']),120,0,r.config['bpmSegments'])
                                b=core.note_seconds(core.beat_value(n['endTime']),120,0,r.config['bpmSegments'])
                                shapes=app.canvas.find_withtag(f'note-{n["id"]}')
                                if not shapes:continue
                                head=app.canvas.coords(shapes[-1]);actual=(head[1]+head[3])/2
                                expected=line-(a-moment)*phira_speed/(w/h)*(w/h)*h/2
                                error=abs(actual-expected);max_error=max(max_error,error)
                                assert error<1e-7,(w,h,factor,n,actual,expected)
                                if n['type']==2:
                                    body=app.canvas.coords(shapes[0])
                                    assert math.isclose(body[1],line-(b-moment)*phira_speed*h/2,abs_tol=1e-7)
                                samples+=1
            passed(f'Phira v0.8.2 formula parity: {samples} visible notes, four viewport sizes, six multipliers, BPM change; max error {max_error:.9f}px (formula check, not Phira GUI)')
            for invalid in (0,-1,5.01,float('nan'),float('inf'),True,'2'):
                try:core.build_chart(r.notes,120,16,dict(scrollSpeed=invalid));raise AssertionError('Invalid speed accepted')
                except ValueError:pass
            app.set_scroll_speed(1.23456789);assert r.meta['scrollSpeed']==1.23456789
            app.speed_reset.invoke();assert r.meta['scrollSpeed']==1 and app.speed_text.get()=='1'
            app.set_scroll_speed(.25);app.clear(False)
            assert app.recorder.meta['scrollSpeed']==.25 and not app.recorder.notes
            passed('speed bounds reject malformed values; reset restores 1x and clearing retains chosen speed')
        finally:
            app.server.recorder=original;app.index_key=None;app.set_scroll_speed(1)
        app.clear(False);stamp=core.epoch_ms()
        request('/api/session/start',{'bpm':120,'startEpoch':stamp})
        for sid in ('mobile-browser','android-apk'):
            request('/api/events',{'sid':sid,'events':[dict(k='down',f=1,x=.5,y=.5,t=stamp+10)]})
        assert len(app.recorder.tracker.active)==2
        request('/api/client/reset',{'sid':'mobile-browser'})
        assert list(app.recorder.tracker.active)==[('android-apk',1)]
        assert len(app.recorder.notes)==1  # Reset settles this client's held gesture.
        request('/api/events',{'sid':'android-apk','events':[dict(k='up',f=1,x=.5,y=.5,t=stamp+60)]})
        assert len(app.recorder.notes)==2 and not app.recorder.tracker.active
        passed('browser and APK session isolation, shared recording and independent reset')
        app.clear(False);stamp=core.epoch_ms()
        request('/api/session/start',{'bpm':120,'startEpoch':stamp})
        signed_ids=[-2147483648,-7,0,7,2147483647]
        for kind in ('down','up'):
            request('/api/events',{'sid':'legacy-mobile','events':[dict(k=kind,f=f,x=.1+i*.18,y=.5,t=stamp+(10 if kind=='down' else 60)) for i,f in enumerate(signed_ids)]})
        assert len(app.recorder.notes)==5 and app.recorder.max_multi==5
        before=app.recorder.seq
        for invalid in (None,True,1.5,'1',2**53,-2**53):
            try:
                request('/api/events',{'sid':'invalid','events':[dict(k='down',f=invalid,x=.5,y=.5,t=stamp)]})
                raise AssertionError('Malformed touch id accepted: '+repr(invalid))
            except urllib.error.HTTPError as error:
                assert error.code==400
        assert app.recorder.seq==before
        passed('signed legacy touch IDs stay distinct; invalid and unsafe IDs rejected without mutation')
        logs.append(f'NATIVE DESKTOP SELFTEST PASSED ({len(logs)}/{len(logs)})')
        code=0
    except Exception:
        logs.append(traceback.format_exc());logs.append('NATIVE DESKTOP SELFTEST FAILED');code=1
    finally:
        if app:
            try:app.close(True)
            except Exception:logs.append(traceback.format_exc());code=1
    logs.append(f'Elapsed seconds: {time.perf_counter()-started:.3f}')
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_text('\n'.join(logs)+'\n',encoding='utf-8')
    return code
