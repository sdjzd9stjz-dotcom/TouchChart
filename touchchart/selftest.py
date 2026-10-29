"""Eleven deterministic acceptance tests, stdlib only. Exit 0 only if all pass."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

import touchchart as tc

ROOT = Path(__file__).resolve().parent
CONVERTER = Path(os.environ.get('PHICHAIN_CONVERTER', r'C:\Users\terry\Apps\Phichain\app\phichain-v0.5.0-x86_64-pc-windows-msvc\phichain-converter.exe'))
BASE = 1730000000000


def event(k, f=0, x=.5, y=.5, t=BASE):
    return dict(k=k, f=f, x=x, y=y, t=t)


def fixture_recorder():
    fixture = json.loads((ROOT / 'fixtures' / 'gestures.json').read_text(encoding='utf-8'))
    r = tc.Recorder()
    r.start(fixture['session'])
    for e in fixture['events']:
        r.feed(e, 'fixture')
    r.stop(fixture['events'][-1]['t'])
    return r


class Acceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = subprocess.Popen([sys.executable, '-u', str(ROOT / 'touchchart.py'), '--port', '0', '--host', '127.0.0.1'],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
        line = cls.server.stdout.readline().strip()
        if not line.startswith('TouchChart desktop: '):
            cls.server.terminate()
            raise RuntimeError('HTTP server failed to start: ' + line)
        cls.base = line.split('desktop: ', 1)[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.communicate(timeout=10)

    def request(self, path, body=None, method=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'}, method=method)
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            raw = response.read()
            return json.loads(raw), response.headers, raw

    def test_classify(self):
        for args, expected in [((145, .019, 0, 0), ('tap', '中心')), ((199, .019, 0, 0), ('tap', '中心')),
                               ((199, .021, .021, 0), ('drag', '中心')), ((200, .019, 0, 0), ('hold', '中心')),
                               ((420, 0, 0, 0), ('hold', '中心')), ((300, .10, .071, -.071), ('flick', '右上')),
                               ((100, .2, -.14, .14), ('flick', '左下')), ((301, .10, .1, 0), ('drag', '右')),
                               ((200, .03, .03, 0), ('drag', '右'))]:
            with self.subTest(args=args):
                self.assertEqual(tc.classify(*args), expected)
        for dx, dy, expected in [(0, -1, '上'), (1, -1, '右上'), (1, 0, '右'), (1, 1, '右下'),
                                 (0, 1, '下'), (-1, 1, '左下'), (-1, 0, '左'), (-1, -1, '左上')]:
            self.assertEqual(tc.direction_of(dx, dy), expected)

    def test_slots(self):
        tracker = tc.FingerTracker()
        for i in range(5):
            tracker.feed(event('down', i))
        self.assertEqual([v['slot'] for v in tracker.active.values()], [1, 2, 3, 4, 5])
        self.assertEqual(tracker.feed(event('down', 5))[0]['message'], '忽略第6指')
        self.assertEqual(len(tracker.active), 5)
        tracker.feed(event('up', 2, t=BASE + 100))
        tracker.feed(event('down', 6, t=BASE + 120))
        self.assertEqual(tracker.active[('local', 6)]['slot'], 3)
        self.assertEqual(tracker.feed(event('up', 5, t=BASE + 150)), [])
        self.assertEqual(len(tracker.active), 5)
        duplicate = tracker.feed(event('down', 0, t=BASE + 180))
        self.assertEqual(sum(e['kind'] == 'gesture' for e in duplicate), 1)
        self.assertEqual(len(tracker.active), 5)
        self.assertEqual(tracker.feed(event('move', 0, t=BASE - 1000)), [])
        self.assertEqual(tracker.dropped, 1)
        tracker.feed(event('cancel', 0, t=BASE + 210))
        self.assertNotIn(('local', 0), tracker.active)
        tracker = tc.FingerTracker()
        orphan = tracker.feed(event('up', t=BASE))[0]
        self.assertEqual((orphan['gesture'], orphan['dt']), ('tap', 0))
        tracker.feed(event('down'))
        tracker.feed(event('move', x=.6, t=BASE + 30))
        self.assertEqual(tracker.snapshot(BASE + 40)[0]['status'], '活动')
        self.assertEqual(tracker.snapshot(BASE + 131)[0]['status'], '中心')

    def test_multitouch(self):
        r = tc.Recorder()
        r.start(dict(bpm=120, startEpoch=BASE))
        for f in range(3):
            r.feed(event('down', f, t=BASE))
        for f in range(3):
            r.feed(event('up', f, t=BASE + 100 + f * 40))
        self.assertEqual(len(r.notes), 3)
        multis = [e['message'] for e in r.events if e['kind'] == 'multi']
        self.assertEqual(multis, ['multi 3（多押 ×3）'])
        self.assertEqual(r.state()['stats']['maxMulti'], 3)
        r = tc.Recorder()
        r.start(dict(startEpoch=BASE))
        for f in range(5):
            r.feed(event('down', f))
        for f in range(5):
            r.feed(event('up', f, t=BASE + 100 + f * 5))
        self.assertEqual([e['message'] for e in r.events if e['kind'] == 'multi'], ['multi 5（多押 ×5）'])

    def test_quantize(self):
        for division, seconds, expected in [(16, 1, [2, 0, 4]), (16, 1.13, [2, 1, 4]),
                                             (16, -.5, [0, 0, 4]), (24, 1.75, [3, 3, 6]), (8, 1.25, [2, 1, 2])]:
            self.assertEqual(tc.beat_triple(tc.quantize_beat(seconds, 120, 0, division), division // 4), expected)
        self.assertEqual(tc.quantize_beat(1.2, 120, 200, 16), 2)
        self.assertEqual(tc.beat_triple(2.99999999, 4), [3, 0, 4])
        self.assertEqual(tc.quantize_beat(.0625, 120, 0, 16), .25)
        r = tc.Recorder()
        r.start(dict(bpm=120, division=16, startEpoch=BASE, comp=100))
        r.feed(event('down', t=BASE + 1100))
        r.feed(event('up', t=BASE + 1150))
        self.assertEqual(r.notes[0]['startTime'], [2, 0, 4])
        r.feed(event('down', 1, t=BASE + 2000))
        r.stop(BASE + 2420)
        self.assertEqual(r.notes[-1]['type'], 2)
        self.assertEqual(len(r.tracker.active), 0)

    def test_position_x(self):
        self.assertEqual([tc.position_x(x) for x in [0, .5, 1]], [-675, 0, 675])
        for lanes in [4, 8, 16]:
            for i in range(101):
                x = tc.position_x(i / 100, lanes)
                self.assertLessEqual(abs((x / 1350 + .5) * (lanes - 1) - round((x / 1350 + .5) * (lanes - 1))), 1e-10)
        self.assertEqual(tc.position_x(2), 675)
        self.assertEqual(tc.position_x(-1), -675)

    def test_http_lifecycle(self):
        self.request('/api/reset', {})
        now, _, _ = self.request('/api/sync')
        self.assertLess(abs(now['now'] - tc.epoch_ms()), 2000)
        for path in ['/', '/touch', '/selftest']:
            with urllib.request.urlopen(self.base + path) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(b'<!doctype html>', resp.read())
        self.request('/api/events', dict(sid='pre', events=[event('down'), event('up', t=BASE + 50)]))
        self.assertEqual(self.request('/api/state')[0]['notes'], [])
        self.request('/api/session/start', dict(bpm=120, offsetMs=0, division=16, lanes=0, comp=0, startEpoch=BASE))
        events = []
        for i in range(30):
            events.extend([event('down', i, x=(i % 5) / 4, t=BASE + 1000 + i * 500),
                           event('up', i, x=(i % 5) / 4, t=BASE + 1100 + i * 500)])
        self.assertEqual(len(events), 60)
        self.request('/api/events', dict(sid='http', events=events))
        state = self.request('/api/state')[0]
        self.assertEqual(len(state['notes']), 30)
        stream = self.request('/api/events?since=0')[0]
        self.assertGreater(stream['next'], 0)
        self.assertTrue(any(e.get('note') for e in stream['events']))
        self.request('/api/session/stop', {})
        chart, headers, raw = self.request('/api/chart')
        self.assertEqual(len(chart['judgeLineList'][0]['notes']), 30)
        self.assertEqual(headers['Content-Disposition'], 'attachment; filename="chart.json"')
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
        for path, body, method, expected in [('/missing', None, None, 404), ('/api/sync', {}, None, 405),
                                            ('/api/state', None, 'PUT', 405), ('/api/session/start', {'bpm': 0}, None, 400),
                                            ('/api/session/start', {'division': 10}, None, 400),
                                            ('/api/events', {'sid':'bad','events':[event('move', x=2)]}, None, 400),
                                            ('/api/events?since=bad', None, None, 400)]:
            with self.subTest(path=path, method=method):
                with self.assertRaises(urllib.error.HTTPError) as exc:
                    self.request(path, body, method)
                self.assertEqual(exc.exception.code, expected)
                self.assertFalse(json.loads(exc.exception.read())['ok'])
        # Entire batches are validated before any event is applied.
        old = self.request('/api/state')[0]['next']
        with self.assertRaises(urllib.error.HTTPError):
            self.request('/api/events', {'sid':'atomic','events':[event('down'),event('up',x=9)]})
        self.assertEqual(old, self.request('/api/state')[0]['next'])
        self.request('/api/control', dict(action='sending', enabled=False))
        old = self.request('/api/state')[0]
        self.request('/api/events', dict(sid='paused', events=[event('down'), event('up')]))
        after = self.request('/api/state')[0]
        self.assertEqual((old['notes'], old['messages']), (after['notes'], after['messages']))
        self.request('/api/control', dict(action='sending', enabled=True))
        # Reconnecting one client releases only its own contacts, not another phone's.
        current = tc.epoch_ms()
        for sid in ['phone-a', 'phone-b']:
            self.request('/api/events', dict(sid=sid, events=[event('down', t=current)]))
        self.request('/api/client/reset', dict(sid='phone-a'))
        slots = self.request('/api/state')[0]['slots']
        self.assertEqual(sum(s['status'] not in ['离开', '空闲'] for s in slots), 1)
        self.request('/api/client/reset', dict(sid='phone-b'))
        self.request('/api/notes/delete', {})
        self.assertEqual(len(self.request('/api/state')[0]['notes']), 29)
        self.request('/api/control', dict(action='clear'))
        with self.assertRaises(urllib.error.HTTPError) as exc:
            self.request('/api/chart')
        self.assertEqual(exc.exception.code, 400)

    def test_chart_schema(self):
        r = fixture_recorder()
        before = copy.deepcopy(r.notes)
        chart = r.chart()
        self.assertEqual(r.notes, before)
        self.assertEqual(chart, r.chart())
        self.assertEqual(chart['formatVersion'], 3)
        self.assertEqual(len(chart['judgeLineList']), 1)
        line = chart['judgeLineList'][0]
        notes = line['notes']
        self.assertEqual(len(notes), sum(r.state()['stats'][k] for k in tc.NOTE_TYPES))
        self.assertEqual(line['numOfNotes'], len(notes))
        self.assertEqual(type(line['eventLayers'][0]['alphaEvents'][0]['start']), int)
        self.assertEqual(line['eventLayers'][0]['moveYEvents'][0]['start'], -330.0)
        self.assertIsInstance(line['extended'], dict)
        ordering = [(tc.beat_value(n['startTime']), n['positionX']) for n in notes]
        self.assertEqual(ordering, sorted(ordering))
        for n in notes:
            self.assertIn(n['type'], (1, 2, 3, 4))
            self.assertTrue(-675 <= n['positionX'] <= 675)
            for key in ['startTime', 'endTime']:
                triple = n[key]
                self.assertEqual(len(triple), 3)
                self.assertTrue(all(type(x) is int for x in triple))
                self.assertTrue(0 <= triple[1] < triple[2])
            if n['type'] == 2:
                self.assertGreater(tc.beat_value(n['endTime']), tc.beat_value(n['startTime']))
            else:
                self.assertEqual(n['startTime'], n['endTime'])
        last = max(tc.beat_value(n['endTime']) for n in notes)
        self.assertEqual(tc.beat_value(line['eventLayers'][0]['speedEvents'][0]['endTime']), last + 4)

    def test_rpe_parseable(self):
        if not CONVERTER.is_file():
            self.skipTest('phichain-converter.exe not found: ' + str(CONVERTER))
        r = fixture_recorder()
        path = ROOT / 'chart_selftest.json'
        path.write_text(json.dumps(r.chart(), ensure_ascii=False, indent=2), encoding='utf-8')
        target = ROOT / 'chart_selftest.phichain.json'
        if target.exists():
            target.unlink()
        result = subprocess.run([str(CONVERTER), '-i', 'rpe', '-o', 'phichain', str(path)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(target.is_file(), result.stdout + result.stderr)
        parsed = json.loads(target.read_text(encoding='utf-8'))
        notes = parsed['lines'][0]['notes']
        self.assertEqual(len(notes), len(r.notes))
        kinds = json.dumps([n['kind'] for n in notes]).lower()
        for kind in ['tap', 'hold', 'flick', 'drag']:
            self.assertIn(kind, kinds)
        (ROOT / 'converter_output.txt').write_text('COMMAND: phichain-converter -i rpe -o phichain chart_selftest.json\n'
                                                  + result.stdout + result.stderr + f'\nEXIT CODE: {result.returncode}\n'
                                                  + f'Parsed notes: {len(notes)}; tap/hold/flick/drag present\n', encoding='utf-8')

    def test_fixture_regression(self):
        fixture = json.loads((ROOT / 'fixtures' / 'gestures.json').read_text(encoding='utf-8'))
        self.assertEqual(fixture['seed'], 20261027)
        self.assertEqual(len(fixture['events']), 200)
        r = fixture_recorder()
        stats = r.state()['stats']
        summary = {k: stats[k] for k in ['total', 'tap', 'hold', 'flick', 'drag']}
        summary.update(first=r.notes[0]['startTime'], last=r.notes[-1]['startTime'])
        expected = dict(total=100, tap=25, hold=25, flick=25, drag=25, first=[0, 0, 4], last=[198, 0, 4])
        self.assertEqual(summary, expected)
        (ROOT / 'sample_chart.json').write_text(json.dumps(r.chart(), ensure_ascii=False, indent=2), encoding='utf-8')

    def test_no_external_deps(self):
        source = (ROOT / 'touchchart.py').read_text(encoding='utf-8')
        self.assertIsNone(re.search(r'^\s*(?:import|from)\s+(?:requests|flask|fastapi|numpy)\b', source, re.M))
        self.assertIsNone(re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://', source, re.I))
        links = re.findall(r'https?://[^\s"\'<>]+', source)
        self.assertTrue(all(link.startswith(('http://127.0.0.1', 'http://{lan_ip()}')) for link in links), links)
        self.assertNotIn('setInterval(', source)

    def test_bom_and_encoding(self):
        r = fixture_recorder()
        chart = r.chart()
        data = json.dumps(chart, ensure_ascii=False).encode('utf-8')
        self.assertFalse(data.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(json.loads(data), chart)
        self.request('/api/reset', {})
        self.request('/api/session/start', dict(startEpoch=BASE, name='中文曲名'))
        self.request('/api/events', dict(sid='encoding', events=[event('down'),event('up', t=BASE+100)]))
        exported, _, raw = self.request('/api/chart')
        self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(exported['META']['name'], '中文曲名')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance)
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    passed = result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
    if result.wasSuccessful() and not result.skipped:
        print(f'ALL TESTS PASSED ({passed}/{result.testsRun})')
        sys.exit(0)
    print(f'TESTS INCOMPLETE ({passed}/{result.testsRun}); skipped={len(result.skipped)}')
    sys.exit(1)
