"""Dense-input, variable-tempo, regression and real-converter acceptance."""
import copy
import http.client
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

import touchchart as tc
from pack_chart import pack, phichain_project, validate_chart
from selftest import CONVERTER, event, BASE

ROOT=Path(__file__).resolve().parent
ART=ROOT/'artifacts'/'review'
ART.mkdir(parents=True,exist_ok=True)
SEGMENTS=[dict(time=0,bpm=120),dict(time=12,bpm=180),dict(time=20,bpm=90)]


class Advanced(unittest.TestCase):
    def test_duplicate_listener_rejected(self):
        with self.assertRaises(OSError):
            duplicate = tc.Server(('127.0.0.1', self.server.server_port))
            duplicate.server_close()

    @classmethod
    def setUpClass(cls):
        cls.server=tc.Server(('127.0.0.1',0))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def request(self,path,body=None,connection=None):
        conn=connection or http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        conn.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'})
        response=conn.getresponse();data=json.loads(response.read())
        if connection is None: conn.close()
        self.assertEqual(response.status,200,data)
        return data

    def test_tempo_integral_inverse(self):
        for seconds,beat in [(0,0),(6,12),(12,24),(16,36),(20,48),(24,54)]:
            self.assertEqual(tc.seconds_to_beat(seconds,120,SEGMENTS),beat)
            self.assertEqual(tc.beat_to_seconds(beat,120,SEGMENTS),seconds)
        for i in range(1000):
            seconds=i/19
            self.assertAlmostEqual(tc.beat_to_seconds(tc.seconds_to_beat(seconds,120,SEGMENTS),120,SEGMENTS),seconds,places=9)

    def test_bpm_boundaries_and_hold(self):
        r=tc.Recorder();r.start(dict(bpm=120,bpmSegments=SEGMENTS,division=192,startEpoch=BASE))
        r.feed(event('down',t=BASE+11500));r.feed(event('up',t=BASE+12500))
        note=r.notes[0];self.assertEqual(tc.beat_value(note['startTime']),23)
        self.assertEqual(tc.beat_value(note['endTime']),25.5)
        self.assertAlmostEqual(r.state()['stats']['duration'],12.5)
        self.assertEqual([n['startTime'] for n in r.chart()['BPMList']],[[0,0,1],[24,0,1],[48,0,1]])
        self.assertEqual(validate_chart(r.chart()),1)

    def test_lcm_grid(self):
        self.assertEqual(tc.combined_division([16,24]),48)
        self.assertEqual(tc.combined_division([32,48]),96)
        self.assertEqual(tc.combined_division([64,96]),192)
        self.assertEqual(self.request('/api/grid',dict(divisions=[16,24]))['division'],48)
        for ds in [[],[10],[0],[True],[128,132]]:
            with self.assertRaises(ValueError):tc.combined_division(ds)
        for d in [32,48,64,96,128,192,384,768]:
            tc.validate_config(dict(division=d))
            self.assertEqual(tc.beat_triple(tc.quantize_beat(.5,120,0,d),d//4),[1,0,d//4])

    def test_offset_preserves_audio_tempo_change_time(self):
        # Offset changes the zero beat, not the physical 12s tempo-change position.
        self.assertAlmostEqual(tc.quantize_beat(12,120,250,192,SEGMENTS),23.5)
        self.assertAlmostEqual(tc.note_seconds(23.5,120,250,SEGMENTS),12)
        exported=tc.export_bpm_list(120,250,SEGMENTS)
        self.assertEqual(exported[1]['startTime'],[23,1,2])
        self.assertAlmostEqual(tc.note_seconds(25,120,250,SEGMENTS),12.5)
        self.assertAlmostEqual(tc.quantize_beat(12.5,120,250,192,SEGMENTS),25)

    def test_audio_change_detection(self):
        values=[0.0]*3200
        peaks=[i*.5 for i in range(24)]+[12+i/3 for i in range(24)]+[20+i*2/3 for i in range(18)]
        for at in peaks:values[round(at*100)]=1
        result=tc.analyze_onsets(values,100,120)
        self.assertEqual(len(result['segments']),3,result)
        for got,want in zip(result['segments'],SEGMENTS):
            self.assertAlmostEqual(got['time'],want['time'],delta=.03)
            self.assertAlmostEqual(got['bpm'],want['bpm'],delta=.5)
        self.assertGreater(result['confidence'],.8)
        self.assertIn('候选',result['warning'])
        (ART/'tempo-analysis-synthetic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

    def test_audio_silence_not_false_positive(self):
        result=tc.analyze_onsets([0.0]*2000)
        self.assertEqual(result['segments'],[])
        self.assertEqual(result['confidence'],0)
        with self.assertRaises(ValueError):tc.analyze_onsets([float('nan')]*1000)

    def test_drag_chain_keeps_curve(self):
        r=tc.Recorder();r.start(dict(bpm=120,division=64,dragMode='chain',startEpoch=BASE))
        r.feed(event('down',x=.2))
        for ms,x in [(250,.8),(500,.2),(750,.8)]:r.feed(event('move',x=x,t=BASE+ms))
        r.feed(event('up',x=.2,t=BASE+1000))
        self.assertEqual(len(r.notes),33)
        self.assertTrue(all(n['type']==4 for n in r.notes))
        self.assertAlmostEqual(r.notes[0]['positionX'],-405)
        self.assertAlmostEqual(r.notes[8]['positionX'],405)
        self.assertAlmostEqual(r.notes[16]['positionX'],-405)
        self.assertEqual(len({tuple(n['startTime']) for n in r.notes}),33)
        self.assertNotIn('path',r.events[-2])

    def test_forced_note_modes(self):
        for kind in tc.NOTE_TYPES:
            r=tc.Recorder();r.start(dict(noteMode=kind,startEpoch=BASE))
            r.feed(event('down'));r.feed(event('up',t=BASE+30))
            self.assertEqual(r.notes[0]['type'],tc.NOTE_TYPES[kind])
            validate_chart(r.chart())

    def test_stop_resume_no_phantom_note(self):
        r=tc.Recorder();r.start(dict(startEpoch=BASE));r.feed(event('down'));r.stop(BASE+300)
        r.start(dict(startEpoch=BASE+500));r.feed(event('up',t=BASE+550))
        self.assertEqual(len(r.notes),1)
        r.feed(event('down',t=BASE+600));r.feed(event('up',t=BASE+630))
        self.assertEqual(len(r.notes),2)

    def test_fractional_offset_rejected(self):
        with self.assertRaises(ValueError):tc.validate_config(dict(offsetMs=1.5))
        tc.validate_config(dict(offsetMs=1.0))
        r=tc.Recorder();r.start(dict(offsetMs=1.0,startEpoch=BASE));r.feed(event('up'))
        self.assertIs(type(r.chart()['META']['offset']),int)

    def test_reject_invalid_tempo_maps(self):
        for points in [[dict(time=1,bpm=120)],[dict(time=0,bpm=0)],[dict(time=0,bpm=120),dict(time=0,bpm=140)], [dict(time=0,bpm=float('nan'))]]:
            with self.assertRaises(ValueError):tc.tempo_map(120,points)

    def test_no_retime_existing_notes(self):
        r=tc.Recorder();r.start(dict(bpm=120,startEpoch=BASE));r.feed(event('up'));r.stop(BASE+50)
        before=copy.deepcopy(r.notes)
        with self.assertRaises(ValueError):r.start(dict(bpm=150,startEpoch=BASE))
        self.assertEqual(before,r.notes)

    def test_http_dedup_and_push_wakeup(self):
        self.request('/api/reset',{});self.request('/api/session/start',dict(startEpoch=BASE))
        data=self.request('/api/events?since=0');received=[]
        def listen():received.append(self.request('/api/events?since='+str(data['next'])+'&revision='+str(data['revision'])+'&wait=1'))
        listener=threading.Thread(target=listen);listener.start();time.sleep(.015)
        body=dict(sid='dedup',batch=1,events=[event('down'),event('up',t=BASE+40)])
        self.request('/api/events',body);duplicate=self.request('/api/events',body);listener.join(timeout=2)
        self.assertTrue(duplicate['duplicate']);self.assertTrue(received)
        self.assertEqual(len(self.request('/api/state')['notes']),1)
        self.assertTrue(any(e.get('note') for e in received[0]['events']))

    def test_dense_50000_notes_roundtrip(self):
        r=tc.Recorder();r.start(dict(bpm=300,division=192,startEpoch=BASE))
        begin=time.perf_counter()
        for i in range(50000):
            t=BASE+i*5
            r.feed(event('down',i%5,x=(i%17)/16,t=t));r.feed(event('up',i%5,x=(i%17)/16,t=t+2))
        elapsed=time.perf_counter()-begin
        self.assertEqual(len(r.notes),50000)
        self.assertEqual(len({n['id'] for n in r.notes}),50000)
        chart=r.chart();self.assertEqual(validate_chart(chart),50000)
        filename=ART/'dense-50000.json';filename.write_text(json.dumps(chart),encoding='utf-8')
        result=subprocess.run([str(CONVERTER),'-i','rpe','-o','phichain',str(filename)],capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        native=json.loads(filename.with_suffix('.phichain.json').read_text(encoding='utf-8'))
        self.assertEqual(len(native['lines'][0]['notes']),50000)
        self.assertLess(elapsed,15)
        (ART/'dense-metrics.json').write_text(json.dumps(dict(notes=50000,inputEvents=100000,elapsedSeconds=elapsed,eventsPerSecond=100000/elapsed,missingNotes=0,converterExit=result.returncode),indent=2),encoding='utf-8')

    def test_video_bug_regressions(self):
        r=tc.Recorder();r.start(dict(startEpoch=BASE));r.feed(event('up'));chart=r.chart()
        validate_chart(chart)
        self.assertEqual(chart['judgeLineList'][0]['eventLayers'][0]['moveYEvents'][0]['start'],-330)
        mutations=[lambda c:c.pop('judgeLineList'),lambda c:c['judgeLineList'][0].update(extended=[]),lambda c:c['judgeLineList'][0]['notes'][0].update(size=-1),lambda c:c['judgeLineList'][0]['notes'][0].update(type=2)]
        for mutate in mutations:
            broken=copy.deepcopy(chart);mutate(broken)
            with self.assertRaises(ValueError):validate_chart(broken)

    def test_pack_manifest_and_variable_tempo_converter(self):
        r=tc.Recorder();r.start(dict(bpm=120,bpmSegments=SEGMENTS,startEpoch=BASE))
        for i in range(4):
            r.feed(event('down',t=BASE+i*7000));r.feed(event('up',t=BASE+i*7000+300))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'chart.json';source.write_text(json.dumps(r.chart()),encoding='utf-8')
            target=pack(source,ROOT/'artifacts/demo-clicks.wav',root/'test.pez')
            with zipfile.ZipFile(target) as z:
                self.assertIsNone(z.testzip());self.assertEqual(set(z.namelist()),{'chart.json','song.wav','background.png','info.yml','info.txt'})
                text=z.read('info.txt').decode();self.assertIn('Chart: chart.json',text);self.assertIn('Picture: background.png',text)
                self.assertIn('format: "rpe"',z.read('info.yml').decode())
            project=phichain_project(source,ROOT/'artifacts/demo-clicks.wav',root/'project')
            parsed=json.loads((project/'chart.json').read_text(encoding='utf-8'))
            self.assertEqual(len(parsed['lines'][0]['notes']),4)
            self.assertEqual(len(parsed['bpm_list']),3)


def benchmark_transport():
    metrics={}
    for label,script in [('before',ART/'baseline/touchchart.py'),('after',ROOT/'touchchart.py')]:
        p=subprocess.Popen([sys.executable,'-u',str(script),'--port','0','--host','127.0.0.1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        try:
            base=p.stdout.readline().strip().split('desktop: ')[1];port=int(base.rsplit(':',1)[1]);conn=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
            def request(path,body):
                conn.request('POST',path,json.dumps(body).encode(),{'Content-Type':'application/json'});res=conn.getresponse();data=json.loads(res.read());assert res.status==200,data;return data
            request('/api/session/start',dict(startEpoch=BASE));samples=[]
            for i in range(120):
                t=time.perf_counter();request('/api/events',dict(sid='latency',events=[event('down',t=BASE+i*100),event('up',t=BASE+i*100+30)]));samples.append((time.perf_counter()-t)*1000)
            samples.sort();metrics[label]=dict(meanMs=statistics.mean(samples),p50Ms=samples[len(samples)//2],p95Ms=samples[int(len(samples)*.95)],maxMs=max(samples),requests=len(samples))
            conn.close()
        finally:p.terminate();p.communicate(timeout=5)
    metrics['scope']='localhost persistent HTTP round-trip; not physical touch-to-photon latency, and not a measurement of the video program'
    (ART/'transport-benchmark.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
    print('TRANSPORT BENCHMARK '+json.dumps(metrics))
    return metrics


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    result=unittest.TextTestRunner(stream=sys.stdout,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Advanced))
    if result.wasSuccessful():
        benchmark_transport()
        print(f'ADVANCED TESTS PASSED ({result.testsRun}/{result.testsRun})')
    sys.exit(0 if result.wasSuccessful() else 1)
