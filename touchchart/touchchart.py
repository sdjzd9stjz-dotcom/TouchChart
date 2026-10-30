"""TouchChart: LAN touch input -> quantized RPE chart. Python 3.10+, stdlib only."""
from __future__ import annotations

import argparse
import copy
import json
import math
import mimetypes
import socket
import subprocess
import sys
import threading
import time
import uuid
import bisect
import statistics
from fractions import Fraction
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DEFAULTS = dict(bpm=150, offsetMs=0, division=16, lanes=0, comp=0)
EXTRA_DEFAULTS = dict(dragMode="single", noteMode="auto", bpmSegments=[], laneSpan=1.0)
GESTURE_DEFAULTS = dict(holdMs=200, dragDistance=.05, flickDistance=.10, flickMs=300)
# Phira v0.8.2: parse/rpe.rs SPEED_RATIO = 10/45/HEIGHT_RATIO;
# core/note.rs divides by aspect ratio, camera restores it, screen scales by H/2.
# Thus RPE speed s moves s/(9*0.83175) viewport heights per second.
# Keep a stable 1x baseline: 180 px/s in a 300 px-high reference preview.
PHIRA_HEIGHT_RATIO = .83175
SCROLL_HEIGHTS_PER_SECOND = .6
RPE_SPEED_AT_1X = SCROLL_HEIGHTS_PER_SECOND * 9 * PHIRA_HEIGHT_RATIO
JUDGE_LINE_Y = -330.0
JUDGE_LINE_FRACTION = .5 - JUDGE_LINE_Y / 900


def rpe_scroll_speed(multiplier):
    validate_scroll_speed(multiplier)
    return multiplier * RPE_SPEED_AT_1X


def preview_scroll_speed(multiplier, height):
    return rpe_scroll_speed(multiplier) / (9 * PHIRA_HEIGHT_RATIO) * height

def validate_gestures(settings):
    if not isinstance(settings, dict) or set(settings) != set(GESTURE_DEFAULTS):
        raise ValueError('手势参数不完整')
    for key,low,high in [('holdMs',50,2000),('dragDistance',.005,.3),('flickDistance',.005,.8),('flickMs',50,1000)]:
        if not finite_number(settings[key]) or not low<=settings[key]<=high:
            raise ValueError(f'{key} 超出范围 {low}～{high}')
    if settings['flickDistance']<settings['dragDistance']:
        raise ValueError('Flick 最小位移不能小于 Drag 起始位移')
MAX_NOTES = 100000
NOTE_TYPES = {"tap": 1, "hold": 2, "flick": 3, "drag": 4}
LEGACY_TYPES = {"tap": 1, "drag": 2, "hold": 3, "flick": 4}
NAMES = {v: k for k, v in NOTE_TYPES.items()}
ROOT = Path(__file__).resolve().parent


def epoch_ms():
    return int(time.time() * 1000)


def direction_of(dx, dy, d=None):
    if (math.hypot(dx, dy) if d is None else d) < .03:
        return "中心"
    return ["右", "右下", "下", "左下", "左", "左上", "上", "右上"][
        math.floor(math.atan2(dy, dx) / (math.pi / 4) + .5) % 8]


def classify(dt, d, dx=0, dy=0, settings=None, excursion=None):
    if settings is not None:
        motion=d if excursion is None else excursion
        if d+1e-12>=settings['flickDistance'] and dt<=settings['flickMs']:
            kind='flick'
        elif motion+1e-12>=settings['dragDistance']:
            kind='drag'
        else:
            kind='hold' if dt>=settings['holdMs'] else 'tap'
        return kind,direction_of(dx,dy,d)
    if dt < 200 and d < .02:
        kind = "tap"
    elif dt >= 200 and d < .03:
        kind = "hold"
    elif d >= .10 and dt <= 300:
        kind = "flick"
    else:
        kind = "drag"
    return kind, direction_of(dx, dy, d)


def round_half_up(value):
    return math.floor(value + .5)


def tempo_map(bpm, segments=None):
    points = [dict(time=float(p["time"]), bpm=float(p["bpm"])) for p in (segments or [dict(time=0, bpm=bpm)])]
    if not points or points[0]["time"] != 0:
        raise ValueError("BPM 分段必须从 0 秒开始")
    previous = -1
    beat = 0
    for i, point in enumerate(points):
        if not math.isfinite(point["time"]) or point["time"] <= previous or not 30 <= point["bpm"] <= 600:
            raise ValueError("BPM 分段时间需递增，速度需在 30~600")
        if i:
            beat += (point["time"] - previous) * points[i - 1]["bpm"] / 60
        point["beat"] = beat
        previous = point["time"]
    return points


def seconds_to_beat(seconds, bpm, segments=None):
    points = tempo_map(bpm, segments)
    point = points[max(0, bisect.bisect_right([p["time"] for p in points], seconds) - 1)]
    return point["beat"] + (seconds - point["time"]) * point["bpm"] / 60


def beat_to_seconds(beat, bpm, segments=None):
    points = tempo_map(bpm, segments)
    point = points[max(0, bisect.bisect_right([p["beat"] for p in points], beat) - 1)]
    return point["time"] + (beat - point["beat"]) * 60 / point["bpm"]


def rational_beat(beat):
    f = Fraction(max(0, beat)).limit_denominator(1000000)
    return [f.numerator // f.denominator, f.numerator % f.denominator, f.denominator]


def combined_division(divisions):
    if not divisions or any(type(d) is not int or d < 4 or d % 4 or d > 768 for d in divisions):
        raise ValueError("细分必须是 4 的正整数倍，最大 768")
    value = math.lcm(*divisions)
    if value > 768:
        raise ValueError("最小公倍数超过 768，请减少互质细分，避免不必要的超密网格")
    return value


def quantize_beat(seconds, bpm, offset_ms, division, segments=None):
    steps = division // 4
    absolute = seconds_to_beat(seconds, bpm, segments) - seconds_to_beat(offset_ms / 1000, bpm, segments)
    return max(0, round_half_up(absolute * steps) / steps)


def note_seconds(beat, bpm, offset_ms=0, segments=None):
    return beat_to_seconds(beat + seconds_to_beat(offset_ms / 1000, bpm, segments), bpm, segments)


def export_bpm_list(bpm, offset_ms=0, segments=None):
    points = tempo_map(bpm, segments)
    offset = offset_ms / 1000
    base = seconds_to_beat(offset, bpm, segments)
    initial = points[max(0, bisect.bisect_right([p['time'] for p in points], offset) - 1)]
    return [dict(bpm=initial['bpm'], startTime=[0, 0, 1])] + [
        dict(bpm=p['bpm'], startTime=rational_beat(p['beat'] - base)) for p in points[1:] if p['time'] > offset]


def analyze_onsets(envelope, rate=100, preferred_bpm=150):
    """Estimate piecewise tempo from a local energy-onset envelope; expose uncertainty.

    No audio leaves the local service. Percussive onsets are evidence, not guaranteed beats.
    Ambiguous half/double tempo is reported instead of silently claiming exact timing.
    """
    if not 50 <= rate <= 200 or not 100 <= len(envelope) <= 360000:
        raise ValueError("分析数据需至少 1 秒、最长 1 小时，采样率 50~200Hz")
    if any(not finite_number(v) or v < 0 for v in envelope):
        raise ValueError("音频包络无效")
    peaks = []
    radius = max(1, round(rate * .025))
    for i in range(radius, len(envelope) - radius):
        value = envelope[i]
        if value <= 0 or value != max(envelope[i - radius:i + radius + 1]):
            continue
        baseline = statistics.mean(envelope[max(0, i - rate // 2):min(len(envelope), i + rate // 2)])
        if value > baseline * 1.6 and (not peaks or (i / rate - peaks[-1]) >= .09):
            peaks.append(i / rate)
    if len(peaks) < 8:
        return dict(segments=[], confidence=0, onsets=len(peaks), warning="有效起音不足，无法可靠推断 BPM，请手动设置")
    # Estimate the prevailing inter-onset grid in overlapping windows, then merge stable runs.
    intervals = [b - a for a, b in zip(peaks, peaks[1:])]
    windows = []
    for begin in range(0, len(peaks) - 5, 4):
        chunk = intervals[begin:begin + 12]
        valid = [v for v in chunk if .1 <= v <= 2]
        if len(valid) < 5:
            continue
        buckets = Counter(round(v * rate) for v in valid)
        center = max(buckets, key=lambda k: sum(n for b, n in buckets.items() if abs(b - k) <= 2)) / rate
        near = [v for v in valid if abs(v - center) < .035]
        step = statistics.mean(near)
        raw = 60 / step
        candidates = [raw * factor for factor in (.25, .5, 1, 2) if 40 <= raw * factor <= 300]
        target = windows[-1]["bpm"] if windows else preferred_bpm
        chosen = raw if 60 <= raw <= 210 else min(candidates, key=lambda v: abs(math.log2(v / target)))
        confidence = len(near) / len(valid)
        windows.append(dict(time=peaks[begin], bpm=round(chosen, 2), confidence=round(confidence, 3), interval=step))
    segments = []
    for item in windows:
        if not segments:
            segments.append(dict(time=0, bpm=item["bpm"], confidence=item["confidence"]))
        elif abs(item["bpm"] / segments[-1]["bpm"] - 1) > .06 and item["confidence"] >= .65:
            # Snap the transition to the first onset in a stable new interval run.
            start_index = bisect.bisect_left(peaks, item["time"])
            while start_index + 2 < len(intervals) and not all(abs(v - item["interval"]) <= .035 for v in intervals[start_index:start_index + 3]):
                start_index += 1
            while start_index > 0 and abs(intervals[start_index - 1] - item["interval"]) <= .035:
                start_index -= 1
            change_time = round(peaks[start_index], 4)
            if change_time > segments[-1]["time"] + .5:
                segments.append(dict(time=change_time, bpm=item["bpm"], confidence=item["confidence"]))
    confidence = round(statistics.mean(w["confidence"] for w in windows), 3) if windows else 0
    return dict(segments=segments, confidence=confidence, onsets=len(peaks),
                warning="BPM 为候选值；切分、半拍/倍拍、弱鼓点及渐变速度需试听校正。应用后再录制。",
                alternatives=[.5, 1, 2], resolutionMs=1000 / rate)


def beat_triple(beat_q, steps_per_beat):
    ticks = max(0, round_half_up(beat_q * steps_per_beat))
    return [ticks // steps_per_beat, ticks % steps_per_beat, steps_per_beat]


def beat_value(triple):
    return triple[0] + triple[1] / triple[2]


def lane_positions(lanes, span=1.0):
    return [(.5 + (i / (lanes - 1) - .5) * span) for i in range(lanes)] if lanes > 1 else []


def position_x(x, lanes=0, span=1.0):
    x = min(1, max(0, x))
    if lanes > 1:
        index = min(lanes-1, max(0, round_half_up(((x-.5)/span+.5)*(lanes-1))))
        x = lane_positions(lanes, span)[index]
    return min(675, max(-675, (x - .5) * 1350))


def build_chart(notes, bpm, division, meta):
    """Pure: caller supplies identity; no clock, RNG, mutation or network."""
    scroll_speed = meta.get("scrollSpeed", 1.0)
    validate_scroll_speed(scroll_speed)
    normalized = []
    for note in notes:
        n = {k: copy.deepcopy(note[k]) for k in ("type", "startTime", "endTime", "positionX")}
        n.update(above=1, alpha=255, isFake=0, size=1.0, speed=1.0, visibleTime=999.0, yOffset=0.0)
        normalized.append(n)
    normalized.sort(key=lambda n: (beat_value(n["startTime"]), n["positionX"]))
    end = beat_triple(max((beat_value(n["endTime"]) for n in normalized), default=0) + 4, division // 4)

    def event(value, speed=False):
        e = dict(startTime=[0, 0, 1], endTime=end[:], start=value, end=value, linkgroup=0)
        if not speed:
            e.update(easingType=1, easingLeft=0.0, easingRight=1.0, bezier=0, bezierPoints=[0.0] * 4)
        return [e]

    metadata = dict(RPEVersion=150, background="background.png", charter="TouchChart", composer="",
                    id=meta.get("id", "touchchart-export"), illustration="", level="IN Lv.1",
                    name=meta.get("name", "Untitled"), offset=int(meta.get("offsetMs", 0)),
                    song=meta.get("song", "song.mp3"))
    return dict(formatVersion=3, offset=0, META=metadata,
                BPMList=export_bpm_list(bpm, meta.get("offsetMs", 0), meta.get("bpmSegments")), chartTime=0.0,
                judgeLineGroup=["Default"], multiLineString="", multiScale=1.0,
                judgeLineList=[dict(Group=0, Name="Untitled", Texture="line.png", anchor=[.5, .5],
                                    bpmfactor=1.0, father=-1, isCover=1, zOrder=0,
                                    numOfNotes=len(normalized), notes=normalized,
                                    eventLayers=[dict(moveXEvents=event(0.0), moveYEvents=event(JUDGE_LINE_Y),
                                                      rotateEvents=event(0.0), alphaEvents=event(255),
                                                      speedEvents=event(rpe_scroll_speed(scroll_speed), True))], extended={})])


class FingerTracker:
    def __init__(self):
        self.active = {}
        self.last = {}
        self.ignored = set()
        self.suppressed = set()
        self.slots = [dict(slot=i + 1, status="空闲", x=None, y=None, until=0) for i in range(5)]
        self.dropped = 0

    def snapshot(self, now=None):
        now = epoch_ms() if now is None else now
        for s in self.slots:
            if s["status"] == "离开" and now >= s["until"]:
                s.update(status="空闲", x=None, y=None)
        for finger in self.active.values():
            s = self.slots[finger["slot"] - 1]
            if s["status"] in ("按下", "活动") and now - finger["motion"] >= 100:
                s["status"] = "中心"
        return copy.deepcopy(self.slots)

    def feed(self, event, sid="local", settings=None):
        e = dict(event)
        key = (sid, e["f"])
        t, k, x, y = e["t"], e["k"], e["x"], e["y"]
        if t < self.last.get(key, t) - 500:
            self.dropped += 1
            return []
        # Small reordering is tolerated, but cannot make a negative gesture duration.
        t = max(t, self.last.get(key, t))
        self.last[key] = t
        out = []
        if key in self.suppressed:
            if k == "down":
                self.suppressed.discard(key)
            elif k in ("up", "cancel"):
                self.suppressed.discard(key)
                return []
            else:
                return []
        if key in self.ignored:
            if k in ("up", "cancel"):
                self.ignored.discard(key)
            return []
        if k == "down" and key in self.active:
            old = self.active[key]
            out += self.feed(dict(k="up", f=e["f"], x=old["x"], y=old["y"], t=t), sid, settings)
        if k == "down" or (k in ("up", "cancel") and key not in self.active):
            occupied = {f["slot"] for f in self.active.values()}
            slot = next((i for i in range(1, 6) if i not in occupied), None)
            if slot is None:
                if k == "down":
                    self.ignored.add(key)
                return [dict(kind="log", t=t, message="忽略第6指")]
            self.active[key] = dict(slot=slot, x=x, y=y, sx=x, sy=y, start=t, motion=t, path=[(t, x, y)])
            self.active[key]['settings']=copy.deepcopy(settings)
            self.active[key]['excursion']=0.0
            self.slots[slot - 1].update(status="按下", x=x, y=y, until=0)
            if k == "down":
                zone = ["左上", "上", "右上", "左", "中心", "右", "左下", "下", "右下"][min(2, int(y * 3)) * 3 + min(2, int(x * 3))]
                out.append(dict(kind="down", slot=slot, x=x, y=y, t=t, message=f"down {slot} → {zone}"))
        finger = self.active.get(key)
        if finger is None:
            return out
        finger['excursion']=max(finger['excursion'],math.hypot(x-finger['sx'],y-finger['sy']))
        if k == "move":
            delta = math.hypot(x - finger["x"], y - finger["y"])
            if delta > .01:
                finger["motion"] = t
            finger.update(x=x, y=y)
            finger["path"].append((t, x, y))
            if len(finger["path"]) > 4096:
                finger["path"] = finger["path"][::2]
            self.slots[finger["slot"] - 1].update(x=x, y=y, status="活动" if delta > .01 else ("中心" if t - finger["motion"] >= 100 else "活动"))
        if k in ("up", "cancel"):
            dt = max(0, t - finger["start"])
            dx, dy = x - finger["sx"], y - finger["sy"]
            gesture, direction = classify(dt, math.hypot(dx, dy), dx, dy, finger['settings'], finger['excursion'])
            slot = finger["slot"]
            message = f"{gesture} {slot}" + (f" → {direction}" if gesture == "flick" else (f" {dt}ms" if gesture in ("tap", "hold") else ""))
            out.append(dict(kind="gesture", gesture=gesture, slot=slot, x=finger["sx"], y=finger["sy"],
                            endX=x, endY=y, start=finger["start"], t=t, dt=dt, dir=direction, message=message,
                            path=finger["path"] + [(t, x, y)]))
            out.append(dict(kind="up", slot=slot, t=t, message=f"up {slot} {dt}ms"))
            self.slots[slot - 1].update(status="离开", x=x, y=y, until=epoch_ms() + 300)
            del self.active[key]
        return out


class Recorder:
    def __init__(self):
        self.config = DEFAULTS.copy()
        self.config.update(copy.deepcopy(EXTRA_DEFAULTS))
        self.gestures = GESTURE_DEFAULTS.copy()
        self.tracker = FingerTracker()
        self.notes = []
        self.events = deque(maxlen=5000)
        self.messages = deque(maxlen=12)
        self.seq = 0
        self.note_seq = 0
        self.recording = False
        self.paused = False
        self.sending = True
        self.anchor = epoch_ms()
        self.meta = dict(id="touchchart-" + str(uuid.uuid4()), name="Untitled", song="song.mp3")
        self.taps = deque()
        self.multi_event = None
        self.max_multi = 0
        self.counts = Counter()
        self.duration = 0
        self.transport = dict(position=0, playing=False)

    def emit(self, event):
        self.seq += 1
        e = dict(event, id=self.seq)
        self.events.append(e)
        if e.get("message"):
            self.messages.appendleft(e["message"])
        return e

    def start(self, config):
        proposed = {k: copy.deepcopy(config.get(k, v)) for k, v in dict(DEFAULTS, **EXTRA_DEFAULTS).items()}
        if self.notes and proposed != self.config:
            raise ValueError("已有音符时不能更改节奏参数；请先导出并清空")
        self.config = proposed
        self.config["offsetMs"] = int(self.config["offsetMs"])
        self.anchor = config.get("startEpoch", epoch_ms())
        self.meta.update(name=config.get("name", "Untitled"), song=config.get("song", "song.mp3"))
        self.recording, self.paused = True, False
        # A new take starts at song zero and appends; existing holds cannot leak into it.
        suppressed = self.tracker.suppressed | set(self.tracker.active)
        self.tracker = FingerTracker()
        self.tracker.suppressed = suppressed
        self.taps.clear()
        self.multi_event = None

    def settle(self, t):
        for (sid, f), finger in list(self.tracker.active.items()):
            self.feed(dict(k="up", f=f, x=finger["x"], y=finger["y"], t=max(t, finger["start"])), sid)
            self.tracker.suppressed.add((sid, f))

    def stop(self, t=None):
        self.settle(epoch_ms() if t is None else t)
        self.recording = False
        self.transport["playing"] = False

    def feed(self, event, sid="local"):
        if not self.sending:
            return
        for gesture in self.tracker.feed(event, sid, self.gestures):
            if gesture["kind"] == "gesture":
                if self.recording and not self.paused:
                    c = self.config
                    secs = (gesture["start"] - self.anchor - c["comp"]) / 1000
                    start = quantize_beat(secs, c["bpm"], c["offsetMs"], c["division"], c["bpmSegments"])
                    kind = c["noteMode"] if c["noteMode"] != "auto" else gesture["gesture"]
                    # Tracker uses peak displacement, including closed curves, with the
                    # same configurable tolerance for single-note and chain modes.
                    end = start
                    if kind == "hold":
                        end = max(start + 4 / c["division"], quantize_beat(secs + gesture["dt"] / 1000, c["bpm"], c["offsetMs"], c["division"], c["bpmSegments"]))
                    if kind == "drag" and c["dragMode"] == "chain":
                        last = quantize_beat(secs + gesture["dt"] / 1000, c["bpm"], c["offsetMs"], c["division"], c["bpmSegments"])
                        den = c["division"] // 4
                        count = round((last - start) * den) + 1
                        if len(self.notes) + count > MAX_NOTES:
                            raise ValueError("谱面达到 100000 音符上限，请分段录制")
                        trajectory = gesture["path"]
                        index = 0
                        for tick in range(round(start * den), round(last * den) + 1):
                            at = tick / den
                            t = self.anchor + c["comp"] + 1000 * note_seconds(at, c["bpm"], c["offsetMs"], c["bpmSegments"])
                            while index + 1 < len(trajectory) - 1 and trajectory[index + 1][0] < t:
                                index += 1
                            a, b = trajectory[index], trajectory[min(index + 1, len(trajectory) - 1)]
                            fraction = min(1, max(0, (t - a[0]) / max(1, b[0] - a[0])))
                            note = self.add_note(kind, at, at, a[1] + (b[1] - a[1]) * fraction)
                            self.emit(dict(kind="note", t=gesture["t"], note=note))
                    else:
                        gesture["note"] = self.add_note(kind, start, end, gesture["x"])
                if gesture["gesture"] == "tap":
                    t = gesture["t"]
                    # A chord is a cluster spanning <=100ms, centered within +/-50ms.
                    while self.taps and t - self.taps[0][0] > 100:
                        self.taps.popleft()
                        self.multi_event = None
                    self.taps.append((t, gesture["slot"]))
                    count = len({s for _, s in self.taps})
                    self.max_multi = max(self.max_multi, count)
                    if count >= 3:
                        msg = f"multi {count}（多押 ×{count}）"
                        if self.multi_event and self.events and self.multi_event["id"] >= self.events[0]["id"]:
                            old = self.multi_event["message"]
                            self.multi_event["message"] = msg
                            try:
                                self.messages.remove(old)
                            except ValueError:
                                pass
                            self.messages.appendleft(msg)
                        else:
                            self.multi_event = self.emit(dict(kind="multi", t=t, message=msg))
            gesture.pop("path", None)
            self.emit(gesture)

    def add_note(self, kind, start, end, x):
        if len(self.notes) >= MAX_NOTES:
            raise ValueError("谱面达到 100000 音符上限，请分段录制")
        self.note_seq += 1
        c = self.config
        note = dict(id=self.note_seq, type=NOTE_TYPES[kind], startTime=beat_triple(start, c["division"] // 4),
                    endTime=beat_triple(end, c["division"] // 4), positionX=position_x(x, c["lanes"], c['laneSpan']))
        self.notes.append(note)
        self.counts[kind] += 1
        self.duration = max(self.duration, note_seconds(end, c["bpm"], c["offsetMs"], c["bpmSegments"]))
        return note

    def recount(self):
        self.counts = Counter(NAMES[n["type"]] for n in self.notes)
        self.duration = max((note_seconds(beat_value(n["endTime"]), self.config["bpm"], self.config["offsetMs"], self.config["bpmSegments"]) for n in self.notes), default=0)

    def stats(self):
        return dict(total=len(self.notes), **{k: self.counts[k] for k in NOTE_TYPES}, maxMulti=self.max_multi,
                    duration=self.duration, dropped=self.tracker.dropped)

    def chart(self, legacy=False):
        notes = copy.deepcopy(self.notes)
        if legacy:
            for n in notes:
                n["type"] = LEGACY_TYPES[NAMES[n["type"]]]
        return build_chart(notes, self.config["bpm"], self.config["division"], dict(self.meta, offsetMs=self.config["offsetMs"], bpmSegments=self.config["bpmSegments"]))

    def state(self):
        return dict(slots=self.tracker.snapshot(), messages=list(self.messages), notes=self.notes,
                    stats=self.stats(), config=self.config, gestures=self.gestures, scrollSpeed=self.meta.get("scrollSpeed",1.0),
                    recording=self.recording, paused=self.paused, sending=self.sending, next=self.seq,
                    transport=self.transport)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_scroll_speed(value):
    if not finite_number(value) or not .25 <= value <= 5:
        raise ValueError("音符下落速度须为 0.25～5 倍")


def validate_config(body):
    if not finite_number(body.get('laneSpan',1.0)) or not .01 <= body.get('laneSpan',1.0) <= 1:
        raise ValueError('轨道间距比例必须为 0.01～1')
    for k, low, high in (("bpm", 30, 600), ("offsetMs", -2000, 2000), ("comp", -200, 200)):
        v = body.get(k, DEFAULTS[k])
        if not finite_number(v) or not low <= v <= high:
            raise ValueError(f"{k} 超出范围 {low}~{high}")
    if int(body.get("offsetMs", 0)) != body.get("offsetMs", 0):
        raise ValueError("偏移必须是整数毫秒，保证 Phira 可以解析")
    combined_division([body.get("division", 16)])
    for k, options in (("lanes", (0, 4, 8, 16, 32, 64)),):
        if type(body.get(k, DEFAULTS[k])) is not int or body.get(k, DEFAULTS[k]) not in options:
            raise ValueError(f"{k} 无效")
    if body.get("dragMode", "single") not in ("single", "chain") or body.get("noteMode", "auto") not in ("auto", *NOTE_TYPES):
        raise ValueError("录制模式无效")
    if not isinstance(body.get("bpmSegments", []), list) or len(body.get("bpmSegments", [])) > 2000:
        raise ValueError("BPM 分段无效或超过 2000 段")
    tempo_map(body.get("bpm", 150), body.get("bpmSegments"))
    if "startEpoch" in body and not finite_number(body["startEpoch"]):
        raise ValueError("startEpoch 无效")
    for k in ("name", "song"):
        if k in body and (not isinstance(body[k], str) or len(body[k]) > 300):
            raise ValueError(f"{k} 无效")


def validate_events(body):
    sid, events = body.get("sid"), body.get("events")
    if not isinstance(sid, str) or not 1 <= len(sid) <= 128 or not isinstance(events, list) or len(events) > 1000:
        raise ValueError("sid/events 无效")
    for e in events:
        if not isinstance(e, dict) or e.get("k") not in ("down", "move", "up", "cancel"):
            raise ValueError("事件 k 无效")
        # PointerEvent.pointerId is signed; old pages may send it directly.
        # Preserve the sign (abs() would merge two independent contacts).
        if type(e.get("f")) is not int or not -(2 ** 53 - 1) <= e["f"] <= 2 ** 53 - 1:
            raise ValueError("触控 id 无效")
        if any(not finite_number(e.get(k)) for k in ("x", "y", "t")) or not (0 <= e["x"] <= 1 and 0 <= e["y"] <= 1):
            raise ValueError("坐标/时间无效")
    return sid, events


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self):
        # Windows SO_REUSEADDR can let two listeners share one live port.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, legacy=False):
        super().__init__(address, Handler)
        self.recorder = Recorder()
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.revision = 0
        self.instance = str(uuid.uuid4())
        self.receipts = {}
        self.legacy = legacy
        self.test_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    routes = {"/": "GET", "/touch": "GET", "/selftest": "GET", "/api/sync": "GET", "/api/events": "GET POST",
              "/api/state": "GET", "/api/chart": "GET", "/api/session/start": "POST", "/api/session/stop": "POST",
              "/api/reset": "POST", "/api/control": "POST", "/api/notes/delete": "POST", "/api/selftest": "POST",
              "/api/client/reset": "POST", "/api/analyze": "POST", "/api/grid": "POST"}

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, fmt, *args):
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            super().log_message(fmt, *args)

    def reply(self, body, code=200, content_type="application/json; charset=utf-8", attachment=False):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if attachment:
            self.send_header("Content-Disposition", 'attachment; filename="chart.json"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = do_PUT

    def dispatch(self):
        path = urlsplit(self.path).path
        try:
            if path not in self.routes:
                return self.reply(dict(ok=False, error="路径不存在"), 404)
            if self.command not in self.routes[path].split():
                return self.reply(dict(ok=False, error="请求方法不支持"), 405)
            body = {}
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= (8 if path == "/api/analyze" else 1) * 1024 * 1024:
                    self.close_connection = True
                    raise ValueError("请求过大")
                # Browser writes must originate on this service; no CORS enabled.
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    return self.reply(dict(ok=False, error="拒绝跨站写入"), 403)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("请求必须是 JSON 对象")
            if path in ("/", "/touch", "/selftest"):
                page = {"/": DESKTOP_HTML, "/touch": TOUCH_HTML, "/selftest": SELFTEST_HTML}[path]
                return self.reply(page.replace("__PHONE__", f"http://{lan_ip()}:{self.server.server_port}/touch"), content_type="text/html; charset=utf-8")
            if path == "/api/sync":
                return self.reply(dict(now=epoch_ms()))
            if path == "/api/analyze":
                envelope = body.get("envelope")
                if not isinstance(envelope, list):
                    raise ValueError("缺少音频分析包络")
                return self.reply(analyze_onsets(envelope, body.get("rate", 100), body.get("preferredBpm", 150)))
            if path == "/api/grid":
                return self.reply(dict(division=combined_division(body.get("divisions", []))))
            if path == "/api/selftest":
                if not self.server.test_lock.acquire(blocking=False):
                    return self.reply(dict(ok=False, error="自检正在运行"), 409)
                try:
                    result = subprocess.run([sys.executable, str(ROOT / "selftest.py")], cwd=ROOT, capture_output=True,
                                            text=True, encoding="utf-8", errors="replace", timeout=120)
                    return self.reply(dict(ok=result.returncode == 0, exitCode=result.returncode, output=result.stdout + result.stderr))
                finally:
                    self.server.test_lock.release()
            with self.server.lock:
                r = self.server.recorder
                if path == "/api/events" and self.command == "POST":
                    sid, events = validate_events(body)
                    batch = body.get("batch")
                    if batch is not None and (type(batch) is not int or batch < 0):
                        raise ValueError("batch 无效")
                    receipt = (sid, batch)
                    if batch is not None and receipt in self.server.receipts:
                        return self.reply(dict(ok=True, next=r.seq, duplicate=True))
                    for event in events:
                        r.feed(event, sid)
                    if batch is not None:
                        self.server.receipts[receipt] = True
                        if len(self.server.receipts) > 4096:
                            del self.server.receipts[next(iter(self.server.receipts))]
                    result = dict(ok=True, next=r.seq)
                elif path == "/api/events":
                    query = parse_qs(urlsplit(self.path).query)
                    since = int(query.get("since", ["0"])[0])
                    revision = int(query.get("revision", ["-1"])[0])
                    if query.get("wait") == ["1"] and revision == self.server.revision:
                        self.server.changed.wait(timeout=.12)
                    r = self.server.recorder
                    # Include live analyzer data to avoid a second poll; notes use an incremental stream.
                    result = dict(events=[e for e in r.events if e["id"] > since], next=r.seq,
                                  slots=r.tracker.snapshot(), messages=list(r.messages),
                                  resync=bool(r.events and since < r.events[0]["id"] - 1),
                                  revision=self.server.revision, instance=self.server.instance,
                                  stats=r.stats(), recording=r.recording, paused=r.paused, sending=r.sending)
                elif path == "/api/state":
                    result = r.state()
                elif path == "/api/session/start":
                    validate_config(body)
                    r.start(body)
                    result = dict(ok=True)
                elif path == "/api/session/stop":
                    r.stop()
                    result = dict(ok=True, notes=len(r.notes))
                elif path == "/api/chart":
                    if not r.notes:
                        return self.reply(dict(ok=False, error="尚无音符，请先录制"), 400)
                    return self.reply(r.chart(self.server.legacy), attachment=True)
                elif path == "/api/notes/delete":
                    nid = body.get("id", r.notes[-1]["id"] if r.notes else None)
                    r.notes = [n for n in r.notes if n["id"] != nid]
                    r.recount()
                    r.emit(dict(kind="changed"))
                    result = dict(ok=True)
                elif path == "/api/reset":
                    self.server.recorder = Recorder()
                    self.server.recorder.seq = r.seq
                    self.server.recorder.emit(dict(kind="reset"))
                    result = dict(ok=True)
                elif path == "/api/client/reset":
                    sid = body.get("sid")
                    if not isinstance(sid, str) or not 1 <= len(sid) <= 128:
                        raise ValueError("sid 无效")
                    for (owner, f), finger in list(r.tracker.active.items()):
                        if owner == sid:
                            r.feed(dict(k="cancel", f=f, x=finger["x"], y=finger["y"], t=epoch_ms()), sid)
                    r.tracker.ignored = {key for key in r.tracker.ignored if key[0] != sid}
                    result = dict(ok=True)
                elif path == "/api/control":
                    action = body.get("action")
                    if action == "sending":
                        if type(body.get("enabled")) is not bool:
                            raise ValueError("enabled 必须是布尔值")
                        r.sending = body["enabled"]
                        r.tracker = FingerTracker()
                    elif action == "transport":
                        if not finite_number(body.get("position")) or body["position"] < 0 or type(body.get("playing")) is not bool:
                            raise ValueError("播放位置无效")
                        r.settle(epoch_ms())
                        r.anchor = epoch_ms() - body["position"] * 1000
                        r.paused = not body["playing"]
                        r.transport = dict(position=body["position"], playing=body["playing"])
                    elif action == "clear":
                        r.notes.clear()
                        r.recount()
                        r.max_multi = 0
                        r.emit(dict(kind="changed"))
                    elif action == "gestures":
                        validate_gestures(body.get('settings'))
                        r.gestures = dict(body['settings'])
                    elif action == "scrollSpeed":
                        validate_scroll_speed(body.get("value"))
                        r.meta['scrollSpeed'] = body['value']
                    elif action == "exit":
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                    else:
                        raise ValueError("未知控制动作")
                    result = dict(ok=True)
                else:
                    raise ValueError("未知请求")
                if self.command == "POST":
                    self.server.revision += 1
                    self.server.changed.notify_all()
                self.reply(result)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.reply(dict(ok=False, error=str(exc)), 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except subprocess.TimeoutExpired:
            self.reply(dict(ok=False, error="自检超时"), 500)


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))  # Routing lookup only; no packets sent.
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


# Embedded frontends are defined below. No external assets, packages or CDNs.
COMMON_JS = r"""
const $=id=>document.getElementById(id);
let syncOffset=0, synced=false, rtt=0;
const epochNow=()=>Math.round(performance.now()+syncOffset);
async function api(path,body,signal){
 const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal});
 const data=await response.json();if(!response.ok)throw Error(data.error||response.statusText);return data;
}
async function synchronize(){
 synced=false;let best=Infinity,offset=0;
 for(let i=0;i<5;i++){const c0=performance.now();const r=await api('/api/sync',undefined,AbortSignal.timeout(3000));const c1=performance.now(),dt=c1-c0;if(dt<best){best=dt;offset=r.now+dt/2-c1;}}
 syncOffset=offset;rtt=Math.round(best);synced=true;return rtt;
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const sid=globalThis.crypto?.randomUUID?.()||String(Date.now())+'-'+Math.random();
let contactSequence=0;
function allocateContactId(){if(contactSequence>=Number.MAX_SAFE_INTEGER)throw Error('触控会话已用尽，请刷新页面');return ++contactSequence}
let sendChain=Promise.resolve(),outgoing=[],pumping=false,batchSeq=0,resolveSend=null;
function sendEvents(events){
 if(!synced||!events.length)return sendChain;
 if(outgoing.length+events.length>10000){networkError(Error('发送积压超过 10000 事件，请停止录制并检查网络'));return sendChain}
 outgoing.push(...events);
 if(!pumping){pumping=true;sendChain=new Promise(resolve=>resolveSend=resolve);queueMicrotask(pumpEvents)}return sendChain;
}
async function pumpEvents(){
 try{while(outgoing.length&&synced){const events=outgoing.splice(0,1000),batch=++batchSeq;let failure;for(let retry=0;retry<2;retry++){try{await api('/api/events',{sid,batch,events},AbortSignal.timeout(3000));failure=null;break}catch(err){failure=err}}if(failure)throw failure}}
 catch(err){outgoing=[];networkError(err)}finally{pumping=false;resolveSend?.();resolveSend=null}
}
"""

CSS = r"""
:root{color-scheme:dark;--bg:#0e1116;--panel:#171b23;--border:#2a3140;--cyan:#35e0d0;--yellow:#ffd23f;--red:#ff4d6d;--text:#e6ebf2;--muted:#8b97a8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,'Microsoft YaHei UI',sans-serif}button,input,select{font:inherit}button{cursor:pointer;border:1px solid var(--border);background:#242c39;color:var(--text);border-radius:8px;padding:10px 13px}button:hover{border-color:var(--cyan)}button.primary{background:var(--cyan);color:#081915;font-weight:700}button.recording{background:var(--red);color:white}button:disabled{opacity:.5;cursor:wait}input,select{background:#10141c;color:var(--text);border:1px solid var(--border);border-radius:6px;padding:7px;width:85px}input[type=file]{width:100%;padding:12px}h1{font-size:19px;margin:0}h2{font-size:16px;margin:0 0 18px}small,.muted{color:var(--muted)}.mono,pre,td{font-family:ui-monospace,Consolas,monospace}.badge{font-size:11px;letter-spacing:2px;color:var(--cyan)}a{color:var(--cyan)}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--yellow);margin-right:8px}.error{color:var(--red)}header{padding:20px 28px;border-bottom:1px solid var(--border);display:flex;gap:24px;align-items:center;flex-wrap:wrap}header .phone{margin-left:auto;display:flex;gap:12px;align-items:center}main{display:grid;grid-template-columns:360px minmax(0,1fr);gap:20px;padding:24px;max-width:1800px;margin:auto}.panel{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:20px}.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.controls{margin:16px 0}.control{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:12px}.control input,.control select{color:var(--text)}canvas{display:block}#zones{width:100%;aspect-ratio:1}#chart{width:100%;height:440px;touch-action:none;border:1px solid var(--border);border-radius:10px}table{border-collapse:collapse;width:100%;margin:18px 0}th,td{text-align:left;padding:9px 5px;border-bottom:1px solid var(--border)}th{font-size:12px;color:var(--muted)}#messages{height:206px;overflow:hidden;line-height:1.7;font-size:12px;margin:0}.section-label{margin:22px 0 10px;color:var(--muted);font-size:12px}.statusbar{border:1px solid var(--border);border-radius:8px;padding:13px;margin:14px 0;color:var(--muted);font-size:12px}.note{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding:5px 8px;font:12px ui-monospace,Consolas,monospace}.note button{padding:3px 9px;background:transparent}.note-list{height:180px;overflow:auto}.empty{text-align:center;padding:40px 10px;color:var(--muted)}#drop{border:1px dashed #455269;border-radius:8px;padding:12px;background:#121720}audio{height:32px;width:100%;margin-top:10px}.footer{margin-top:18px;font-size:11px;color:var(--muted)}#playhead{flex:1;min-width:160px;width:auto;accent-color:var(--cyan)}@media(max-width:850px){main{grid-template-columns:1fr;padding:12px}#zones{max-width:320px;margin:auto}header{padding:16px}header .phone{margin-left:0}#chart{height:420px}}
"""

DESKTOP_HTML = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TouchChart 手打谱机</title><style>""" + CSS + r"""</style>
<header><div><div class="badge">RHYTHM CAPTURE / 01</div><h1>TouchChart 手打谱机</h1></div><span id="connection"><i class="dot"></i>同步中</span><div class="phone"><small>手机触控板</small><a id="phone" href="__PHONE__">__PHONE__</a><button id="copy">复制</button></div></header>
<div id="errorBanner" role="alert" hidden style="margin:12px 24px;padding:12px;border:1px solid #ff4d6d;border-radius:8px;color:#ff4d6d"><span id="errorText"></span><button onclick="this.parentElement.hidden=true" style="margin-left:12px">关闭提示</button></div><main><aside class="panel"><div class="badge">INPUT MONITOR</div><h2>手势分析器</h2><canvas id="zones" width="320" height="320" aria-label="触控区域 3×3 区域"></canvas><div class="section-label">触控区域 3×3 区域</div><table><thead><tr><th>槽位</th><th>状态</th><th>x</th><th>y</th></tr></thead><tbody id="slots"></tbody></table><div class="section-label">最近消息 · 实时手势</div><pre id="messages">等待触控输入…</pre><div class="row controls"><button id="sending">暂停发送</button><button id="reset">重置</button><button id="port">端口 8787</button><button id="exit">退出</button></div><a href="/selftest" target="_blank">运行自动自检 ↗</a></aside>
<section class="panel"><div class="row" style="justify-content:space-between"><div><div class="badge">CHART WORKSPACE</div><h2>打谱台</h2></div><span id="timer" class="mono muted">00:00.000</span></div>
<div id="drop"><label for="file">＋ 载入音频 <small>点击选择或拖入 MP3 / WAV / OGG / M4A</small></label><input id="file" type="file" accept=".mp3,.wav,.ogg,.m4a"><small id="audioInfo">未载入音频 · 也可以直接录制节奏</small><audio id="audio" controls preload="auto"></audio></div>
<div class="row controls"><label class="control">BPM<input id="bpm" type="number" min="30" max="600" value="150"></label><label class="control">偏移 ms<input id="offsetMs" type="number" min="-2000" max="2000" value="0"></label><label class="control">补偿 ms<input id="comp" type="number" min="-200" max="200" value="0"></label><label class="control">量化<select id="division"><option value="4">1/4</option><option value="8">1/8</option><option value="16" selected>1/16</option><option value="24">1/24</option><option value="32">1/32</option><option value="48">1/48</option><option value="64">1/64</option><option value="96">1/96</option><option value="128">1/128</option><option value="192">1/192</option><option value="384">1/384</option><option value="768">1/768</option></select></label><label class="control">轨道吸附<select id="lanes"><option value="0">无</option><option>4</option><option>8</option><option>16</option><option>32</option><option>64</option></select></label><label class="control">Phira 同步下落 ×<input id="scrollSpeed" type="number" value="1" min="0.25" max="5" step="any"></label></div>
<div class="row controls"><label class="control">手势模式<select id="noteMode"><option value="auto">自动识别</option><option value="tap">固定 Tap</option><option value="hold">固定 Hold</option><option value="flick">固定 Flick</option><option value="drag">固定 Drag</option></select></label><label class="control">滑动处理<select id="dragMode"><option value="single">单音符</option><option value="chain">沿轨迹成串</option></select></label><button id="densePreset">高密度预设</button><label class="control">复合细分<input id="gridMix" value="16,24" style="width:100px"></label><button id="gridLcm">LCM 合并网格</button></div>
<details id="tempoPanel" style="margin:12px 0"><summary>变速 BPM · 本地音频分析与分段编辑</summary><p class="muted">每行填写「时间秒,BPM」，第一行从 0 秒开始。自动分析是候选结果，请检查半速/倍速与变化点。</p><div class="row"><button id="analyze">分析音频 BPM</button><button id="halfTempo">候选 ×0.5</button><button id="doubleTempo">候选 ×2</button><button id="applyTempo">应用分段</button><button id="constantTempo">恒定 BPM</button></div><textarea id="tempoText" rows="5" style="width:100%;margin-top:10px;background:#10141c;color:#e6ebf2;border:1px solid #2a3140;border-radius:6px;padding:10px" placeholder="0,150&#10;30,180"></textarea><p id="tempoStatus" class="muted">当前：恒定 BPM；分析仅在本机进行。</p></details>
<div class="row controls"><button id="record" class="primary">● 开始录制</button><button id="preview">播放试听</button><button id="clear">清空</button><button id="undo">撤销上一个</button><button id="export">导出 chart.json ↗</button></div>
<canvas id="chart" aria-label="音符预览和鼠标触控输入"></canvas><div class="row controls"><input id="playhead" type="range" min="0" max="60" step=".01" value="0" aria-label="播放进度"><span id="beats" class="mono muted">0.00 / 0.00 拍</span></div>
<div id="stats" class="statusbar">音符 0 · Tap 0 / Hold 0 / Flick 0 / Drag 0 · 最大多押 0 · 时长 0.0s</div><div id="notes" class="note-list"><div class="empty">按 R 开始录制，在预览区按住、拖动或轻点。</div></div><div class="footer">SPACE 播放/暂停　R 录制　CTRL+Z 撤销　CTRL+E 导出　ESC 停止播放</div></section></main>
<script>""" + COMMON_JS + r"""
const audio=$('audio'), chart=$('chart'), zones=$('zones'), ctx=chart.getContext('2d'), zctx=zones.getContext('2d');
const colors={1:'#a8dcff',2:'#4ba8f5',3:'#ff4d6d',4:'#ffd23f'}, labels={1:'Tap',2:'Hold',3:'Flick',4:'Drag'};
let state={slots:[],notes:[],messages:[],stats:{},recording:false,sending:true}, cursor=0, dirty=true, playing=false, position=0, playOrigin=0, fileName='', objectURL='', internalAudio=false, busy=false, reconnects=0;
const flashes=Array(9).fill(0), previousZones=new Map();let lastText='',lastSlots='',lastMessages='',lastFrame=0;
for(let i=1;i<=5;i++)$('slots').insertAdjacentHTML('beforeend',`<tr><td>${i}</td><td id="s${i}">空闲</td><td id="x${i}">—</td><td id="y${i}">—</td></tr>`);
function setConnection(text,color){$('connection').textContent=text;$('connection').style.color=color;}
function networkError(err){console.error(err);setConnection('连接异常：'+err.message,'#ff4d6d');$('errorText').textContent=err.message;$('errorBanner').hidden=false;}
function protect(fn){return async(...args)=>{try{return await fn(...args)}catch(err){networkError(err)}}}
const beat=n=>n[0]+n[1]/n[2];

let bpmSegments=[],audioFile=null,tempoCandidate=null,revision=-1,serverInstance=null;
const settings=()=>({...Object.fromEntries(['bpm','offsetMs','division','lanes','comp'].map(k=>[k,Number($(k).value)])),dragMode:$('dragMode').value,noteMode:$('noteMode').value,bpmSegments});
function tempoPoints(c=state.config||settings()){let points=c.bpmSegments?.length?c.bpmSegments:[{time:0,bpm:c.bpm}];let beat=0;return points.map((p,i)=>{if(i)beat+=(p.time-points[i-1].time)*points[i-1].bpm/60;return {...p,beat}})}
function timeBeat(t,c=state.config||settings()){const points=tempoPoints(c);let p=points[0];for(const next of points){if(next.time>t)break;p=next}return p.beat+(t-p.time)*p.bpm/60}
function beatTime(b,c=state.config||settings()){const points=tempoPoints(c);let p=points[0];for(const next of points){if(next.beat>b)break;p=next}return p.time+(b-p.beat)*60/p.bpm}
const renderBuckets=new Map(),longNotes=[];let knownIds=new Set();
function indexNote(n){if(knownIds.has(n.id))return false;knownIds.add(n.id);n._start=secondsOf(n);n._end=secondsOf(n,true);const a=Math.floor(n._start),b=Math.floor(n._end);if(b-a>60)longNotes.push(n);else for(let t=a;t<=b;t++){if(!renderBuckets.has(t))renderBuckets.set(t,[]);renderBuckets.get(t).push(n)}return true}
function rebuildIndex(){renderBuckets.clear();longNotes.length=0;knownIds=new Set();for(const n of state.notes)indexNote(n)}
function visibleNotes(time,future){const found=new Set(longNotes.filter(n=>n._end>=time-.3&&n._start<=time+future));for(let t=Math.floor(time-.3);t<=Math.ceil(time+future);t++)for(const n of renderBuckets.get(t)||[])found.add(n);return found}

function secondsOf(n,end=false){const c=state.config||settings();return beatTime(beat(n[end?'endTime':'startTime'])+timeBeat(c.offsetMs/1000,c),c);}
function currentTime(){return playing?(objectURL?audio.currentTime:Math.max(0,(performance.now()-playOrigin)/1000)):position;}
async function refresh(){state=await api('/api/state');cursor=state.next;if(document.activeElement!==$('scrollSpeed'))$('scrollSpeed').value=String(state.scrollSpeed??1);rebuildIndex();dirty=true;renderText();return state;}
function renderText(){
 const s=state.stats||{},msg=`音符 ${s.total||0} · Tap ${s.tap||0} / Hold ${s.hold||0} / Flick ${s.flick||0} / Drag ${s.drag||0} · 最大多押 ${s.maxMulti||0} · 时长 ${(s.duration||0).toFixed(1)}s`;
 $('stats').textContent=msg;$('record').textContent=state.recording?'■ 停止录制':'● 开始录制';$('record').classList.toggle('recording',state.recording);$('sending').textContent=state.sending?'暂停发送':'恢复发送';
 for(const k of ['bpm','offsetMs','division','lanes','comp','dragMode','noteMode','densePreset','applyTempo','constantTempo','gridLcm'])$(k).disabled=state.recording||state.notes.length>0;
 const signature=state.notes.length+':'+state.notes.slice(-30).map(n=>n.id).join(',');if(signature!==lastText){lastText=signature;$('notes').replaceChildren();if(!state.notes.length)$('notes').innerHTML='<div class="empty">按 R 开始录制，在预览区按住、拖动或轻点。</div>';for(const n of state.notes.slice(-30).reverse()){const row=document.createElement('div');row.className='note';const label=document.createElement('span');label.textContent=`#${n.id} ${labels[n.type]} ${secondsOf(n).toFixed(3)}s X=${Math.round(n.positionX)}`;label.style.color=colors[n.type];const del=document.createElement('button');del.textContent='×';del.setAttribute('aria-label','删除音符 '+n.id);del.onclick=protect(async()=>{await api('/api/notes/delete',{id:n.id});await refresh()});row.append(label,del);$('notes').append(row)}}
 updateAnalyzer();
}
function updateAnalyzer(){const signature=JSON.stringify(state.slots);if(signature!==lastSlots){lastSlots=signature;dirty=true;for(const s of state.slots){$('s'+s.slot).textContent=s.status;$('s'+s.slot).style.color=({'空闲':'#8b97a8','按下':'#35e0d0','活动':'#6be494','中心':'#65a5ff','离开':'#ffa45e'})[s.status];for(const k of ['x','y'])$(k+s.slot).textContent=s[k]==null?'—':(s[k]*2).toFixed(2);if(s.x!=null&&s.status!=='离开'){const zone=Math.min(2,Math.floor(s.y*3))*3+Math.min(2,Math.floor(s.x*3));if(previousZones.get(s.slot)!==zone){flashes[zone]=performance.now()+120;previousZones.set(s.slot,zone)}}else previousZones.delete(s.slot)}}
 const messages=state.messages.join('\n');if(messages!==lastMessages){$('messages').textContent=messages||'等待触控输入…';lastMessages=messages;}
}
function drawZones(now){zctx.clearRect(0,0,320,320);zctx.font='12px ui-monospace';for(let i=0;i<9;i++){const x=i%3*104+4,y=Math.floor(i/3)*104+4;zctx.fillStyle='#10151d';zctx.fillRect(x,y,100,100);zctx.strokeStyle=flashes[i]>now?'#35e0d0':'#2a3140';zctx.strokeRect(x+.5,y+.5,100,100);zctx.fillStyle='#66758b';zctx.fillText(String(i+1),x+10,y+20)}for(const s of state.slots){if(s.x==null||s.status==='离开')continue;const x=12+s.x*296,y=12+s.y*296;zctx.beginPath();zctx.arc(x,y,16,0,Math.PI*2);zctx.fillStyle='#35e0d033';zctx.fill();zctx.strokeStyle='#35e0d0';zctx.stroke();zctx.textAlign='center';zctx.fillStyle='#b5fff7';zctx.fillText(String(s.slot),x,y+4);zctx.textAlign='left'}}
function drawChart(time, notes=state.notes){
 const W=chart.clientWidth||900,H=chart.clientHeight||440,dpr=devicePixelRatio||1;if(chart.width!==Math.round(W*dpr)||chart.height!==Math.round(H*dpr)){chart.width=Math.round(W*dpr);chart.height=Math.round(H*dpr)}ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#12141a';ctx.fillRect(0,0,W,H);
 const c=state.config||settings(),total=Math.max(4,(timeBeat(state.stats.duration||0,c)-timeBeat(c.offsetMs/1000,c))),line=H*__LINE_FRACTION__,speed=H*__SCROLL_RATE__*Math.max(.25,Math.min(5,Number($('scrollSpeed').value)||1))/1000;
 const visibleBeats=Math.min(64,Math.max(16,total));for(let i=0;i<=visibleBeats;i++){const x=40+i/visibleBeats*(W-80);ctx.strokeStyle=i%4===0?'#3a4250':'#2a2f3a';ctx.lineWidth=i%4===0?2:1;ctx.beginPath();ctx.moveTo(x,18);ctx.lineTo(x,line);ctx.stroke()}
 for(const n of notes===state.notes?visibleNotes(time,H/(speed*1000)+.1):notes){const start=n._start??secondsOf(n),end=n._end??secondsOf(n,true),x=40+(n.positionX/1350+.5)*(W-80),y=line-(start-time)*1000*speed,tail=line-(end-time)*1000*speed;if((y< -30&&tail< -30)||(tail>H+30))continue;ctx.fillStyle=colors[n.type];if(n.type===2){const len=Math.max(20,(end-start)*1000*speed);ctx.globalAlpha=.7;ctx.fillRect(x-9,y-len,18,len);ctx.globalAlpha=1;ctx.fillStyle='#b3e7ff';ctx.fillRect(x-13,y-5,26,10)}else if(n.type===3){ctx.beginPath();ctx.moveTo(x,y-12);ctx.lineTo(x+17,y+7);ctx.lineTo(x,y);ctx.lineTo(x-17,y+7);ctx.closePath();ctx.fill()}else ctx.fillRect(x-13,y-(n.type===4?3:5),26,n.type===4?6:10)}
 ctx.strokeStyle='#ffd23f';ctx.lineWidth=3;ctx.beginPath();ctx.moveTo(40,line);ctx.lineTo(W-40,line);ctx.stroke();const redX=40+Math.min(1,Math.max(0,(timeBeat(time,c)-timeBeat(c.offsetMs/1000,c))/Math.max(total,4)))*(W-80);ctx.strokeStyle='#ff4d6d';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(redX,18);ctx.lineTo(redX,H-22);ctx.stroke();
 for(const s of state.slots){if(s.x==null||s.status==='离开')continue;ctx.fillStyle='#35e0d0';ctx.shadowColor='#35e0d0';ctx.shadowBlur=18;ctx.beginPath();ctx.arc(40+s.x*(W-80),line,8,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0}
 ctx.fillStyle='#8b97a8';ctx.font='11px system-ui';ctx.fillText('JUDGEMENT LINE  /  TOUCH TO RECORD',40,H-25);
}
function frame(now){for(let i=0;i<9;i++)if(flashes[i]>0&&flashes[i]<=now){flashes[i]=0;dirty=true}const animating=flashes.some(t=>t>now);if(dirty||playing||animating){drawZones(now);drawChart(currentTime());dirty=false}if(now-lastFrame>60){lastFrame=now;const t=currentTime(),c=state.config||settings();$('timer').textContent=`${String(Math.floor(t/60)).padStart(2,'0')}:${(t%60).toFixed(3).padStart(6,'0')}`;$('playhead').max=String(Math.max(60,audio.duration||0,state.stats.duration||0,t));$('playhead').value=String(t);$('beats').textContent=`${Math.max(0,(timeBeat(t,c)-timeBeat(c.offsetMs/1000,c))).toFixed(2)} / ${Math.max(0,(timeBeat(state.stats.duration||0,c)-timeBeat(c.offsetMs/1000,c))).toFixed(2)} 拍`;if(!objectURL&&!state.recording&&t>Math.max(1,state.stats.duration||0)+1)protect(()=>setPlaying(false))()}requestAnimationFrame(frame)}requestAnimationFrame(frame);
async function poll(){while(true){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),3000);try{
 const data=await api('/api/events?since='+cursor+'&revision='+revision+'&wait=1',undefined,controller.signal);clearTimeout(timeout);
 if(!synced){await synchronize();await refresh()}reconnects=0;setConnection('● 已连接 '+location.host+' · RTT '+rtt+'ms','#35e0d0');
 const restarted=serverInstance!==null&&serverInstance!==data.instance;serverInstance=data.instance;revision=data.revision;cursor=data.next;
 state.slots=data.slots;state.messages=data.messages;state.stats=data.stats;state.recording=data.recording;state.paused=data.paused;state.sending=data.sending;updateAnalyzer();
 if(restarted||data.resync||data.events.some(e=>e.kind==='changed'||e.kind==='reset'))await refresh();
 else{let added=false;for(const e of data.events)if(e.note&&indexNote(e.note)){state.notes.push(e.note);added=true}if(added){dirty=true;renderText()}}
 }catch(err){clearTimeout(timeout);synced=false;networkError(err);if(++reconnects>10){setConnection('连接失败超过 10 次，请刷新页面','#ff4d6d');break}await sleep(Math.min(8000,500*2**(reconnects-1)))}}}

async function setPlaying(on){position=currentTime();if(on){playOrigin=performance.now()-position*1000;if(objectURL){internalAudio=true;try{audio.currentTime=position;await audio.play()}finally{internalAudio=false}}playing=true}else{playing=false;if(objectURL){internalAudio=true;audio.pause();internalAudio=false}}await api('/api/control',{action:'transport',position:currentTime(),playing});dirty=true;$('preview').textContent=playing?'暂停试听':'播放试听'}
async function toggleRecord(){if(busy)return;busy=true;try{if(state.recording){flushMoves();await sendChain;await api('/api/session/stop',{});await setPlaying(false);position=0;if(objectURL){internalAudio=true;audio.currentTime=0;internalAudio=false}await refresh()}else{if(!synced)throw Error('时间尚未同步');position=0;if(playing)await setPlaying(false);position=0;const config=state.notes.length?state.config:settings();for(const k of ['bpm','offsetMs','comp'])if(!$(k).checkValidity())throw Error(k+' 超出范围');if(objectURL){internalAudio=true;try{audio.currentTime=0;await audio.play()}finally{internalAudio=false}}playing=true;playOrigin=performance.now();await api('/api/session/start',{...config,startEpoch:epochNow()-(objectURL?audio.currentTime*1000:0),name:fileName?fileName.replace(/\.[^.]+$/,''):'Untitled',song:fileName||'song.mp3'});await refresh()}}finally{busy=false}}
async function loadFile(file){if(!file)return;if(!/\.(mp3|wav|ogg|m4a)$/i.test(file.name))throw Error('请选择 mp3 / wav / ogg / m4a');if(state.recording)await toggleRecord();else if(playing)await setPlaying(false);if(objectURL)URL.revokeObjectURL(objectURL);audioFile=file;fileName=file.name;objectURL=URL.createObjectURL(file);audio.src=objectURL;audio.load();position=0;$('audioInfo').textContent=fileName+' · 正在载入…';}
audio.onloadedmetadata=()=>{$('audioInfo').textContent=fileName+' · '+audio.duration.toFixed(1)+'s';$('playhead').max=String(audio.duration)};
audio.onerror=()=>networkError(Error('音频解码失败，请换用 WAV / MP3'));
audio.onended=protect(async()=>{if(state.recording)await toggleRecord();else await setPlaying(false)});
audio.onpause=()=>{if(!internalAudio&&playing)protect(()=>setPlaying(false))()};audio.onplay=()=>{if(!internalAudio&&!playing)protect(()=>setPlaying(true))()};
audio.onseeked=()=>{if(!internalAudio){position=audio.currentTime;playOrigin=performance.now()-position*1000;protect(()=>api('/api/control',{action:'transport',position,playing}))();dirty=true}};
$('file').onchange=protect(e=>loadFile(e.target.files[0]));$('drop').ondragover=e=>e.preventDefault();$('drop').ondrop=protect(e=>{e.preventDefault();return loadFile(e.dataTransfer.files[0])});
$('record').onclick=protect(toggleRecord);$('preview').onclick=protect(()=>setPlaying(!playing));
$('undo').onclick=protect(async()=>{await api('/api/notes/delete',{});await refresh()});
$('clear').onclick=protect(async()=>{if(confirm('清空所有音符？')){if(state.recording)await toggleRecord();await api('/api/control',{action:'clear'});await refresh()}});
$('reset').onclick=protect(async()=>{if(state.recording)await toggleRecord();if(playing)await setPlaying(false);await api('/api/reset',{});position=0;await refresh()});
$('sending').onclick=protect(async()=>{await api('/api/control',{action:'sending',enabled:!state.sending});await refresh()});
$('export').onclick=protect(async()=>{await saveScrollSpeed();if(state.recording)await toggleRecord();if(!state.notes.length){alert('尚无音符，请先录制');return}const response=await fetch('/api/chart');if(!response.ok)throw Error((await response.json()).error);const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download='chart.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
$('playhead').oninput=protect(async e=>{position=Number(e.target.value);playOrigin=performance.now()-position*1000;if(objectURL){internalAudio=true;audio.currentTime=position;internalAudio=false}await api('/api/control',{action:'transport',position,playing});dirty=true});
$('scrollSpeed').oninput=()=>dirty=true;
async function saveScrollSpeed(){const input=$('scrollSpeed');if(!input.checkValidity()||!input.value)throw Error('下落速度必须为 0.25～5');await api('/api/control',{action:'scrollSpeed',value:Number(input.value)});dirty=true}
$('scrollSpeed').onchange=protect(saveScrollSpeed);
$('copy').onclick=protect(async()=>{if(navigator.clipboard)await navigator.clipboard.writeText($('phone').href);else{const input=document.createElement('textarea');input.value=$('phone').href;document.body.append(input);input.select();if(!document.execCommand('copy'))throw Error('复制失败，请手动复制手机地址');input.remove()}$('copy').textContent='已复制'});
$('port').textContent='端口 '+location.port;$('port').onclick=()=>alert('当前端口 '+location.port+'；修改端口请以 python touchchart.py --port 端口号 重启。');
$('exit').onclick=protect(async()=>{if(state.recording)await toggleRecord();await api('/api/control',{action:'exit'});setConnection('服务已退出','#8b97a8')});
document.addEventListener('keydown',e=>{if(/INPUT|SELECT|TEXTAREA/.test(e.target.tagName))return;const key=e.key.toLowerCase();if(e.ctrlKey&&key==='z'){e.preventDefault();$('undo').click()}else if(e.ctrlKey&&key==='e'){e.preventDefault();$('export').click()}else if(key==='r')$('record').click();else if(e.code==='Space'){e.preventDefault();$('preview').click()}else if(key==='escape')protect(()=>setPlaying(false))()});
const pointers=new Map(),moves=new Map();let moveTimer=null;
function pointerEvent(e,k){const b=chart.getBoundingClientRect();return {k,f:pointers.get(e.pointerId),x:Math.max(0,Math.min(1,(e.clientX-b.left-40)/(b.width-80))),y:Math.max(0,Math.min(1,(e.clientY-b.top)/b.height)),t:epochNow()}}
function flushMoves(){if(moveTimer)clearTimeout(moveTimer);moveTimer=null;const data=[...moves.values()];moves.clear();return sendEvents(data)}
chart.addEventListener('pointerdown',e=>{e.preventDefault();if(!synced||pointers.has(e.pointerId))return;chart.setPointerCapture(e.pointerId);pointers.set(e.pointerId,allocateContactId());sendEvents([pointerEvent(e,'down')])});
chart.addEventListener('pointermove',e=>{if(!pointers.has(e.pointerId))return;e.preventDefault();moves.set(e.pointerId,pointerEvent(e,'move'));if(!moveTimer)moveTimer=setTimeout(flushMoves,8)},{passive:false});
for(const kind of ['pointerup','pointercancel','lostpointercapture'])chart.addEventListener(kind,e=>{if(!pointers.has(e.pointerId))return;flushMoves();sendEvents([pointerEvent(e,kind==='pointercancel'?'cancel':'up')]);pointers.delete(e.pointerId)});
window.addEventListener('resize',()=>dirty=true);

function requireEditable(){if(state.recording||state.notes.length)throw Error('请先导出并清空现有音符，再修改节奏参数')}
$('densePreset').onclick=protect(()=>{requireEditable();$('division').value='64';$('dragMode').value='chain';$('noteMode').value='auto';$('lanes').value='0';$('tempoStatus').textContent='高密度：1/64 网格 + 8ms 轨迹采样 + 滑动成串。可进一步选 1/192 或复合网格。'});
$('gridLcm').onclick=protect(async()=>{requireEditable();const divisions=$('gridMix').value.split(/[,，\s]+/).filter(Boolean).map(Number);const r=await api('/api/grid',{divisions});if(![...$('division').options].some(o=>Number(o.value)===r.division))$('division').add(new Option('1/'+r.division,String(r.division)));$('division').value=String(r.division);$('tempoStatus').textContent='LCM('+divisions.join(',')+') = '+r.division+'，当前网格 1/'+r.division});
function parseTempoText(){const points=$('tempoText').value.trim().split(/\n+/).filter(Boolean).map(line=>{const v=line.trim().split(/[,，\s]+/).map(Number);if(v.length!==2||!v.every(Number.isFinite))throw Error('每行需填写 时间秒,BPM');return {time:v[0],bpm:v[1]}});if(!points.length||points[0].time!==0||points.some((p,i)=>p.bpm<30||p.bpm>600||p.time<0||(i&&p.time<=points[i-1].time)))throw Error('首段需从 0 秒开始，时间递增，BPM 30~600');return points}
$('applyTempo').onclick=protect(()=>{requireEditable();bpmSegments=parseTempoText();$('bpm').value=bpmSegments[0].bpm;$('tempoStatus').textContent='已应用 '+bpmSegments.length+' 个 BPM 段，录制与导出使用同一时间映射';dirty=true});
$('constantTempo').onclick=protect(()=>{requireEditable();bpmSegments=[];$('tempoText').value='';$('tempoStatus').textContent='当前：恒定 BPM';dirty=true});
for(const [id,factor] of [['halfTempo',.5],['doubleTempo',2]])$(id).onclick=protect(()=>{$('tempoText').value=parseTempoText().map(p=>p.time+','+(p.bpm*factor)).join('\n');$('tempoStatus').textContent='候选值已调整；检查后点击应用分段'});
async function analyzeAudio(){
 if(!audioFile)throw Error('请先载入音频');$('analyze').disabled=true;$('tempoStatus').textContent='正在本地解码与提取起音…';
 const ac=new AudioContext();try{const decoded=await ac.decodeAudioData(await audioFile.arrayBuffer());const rate=100,hop=Math.max(1,Math.round(decoded.sampleRate/rate)),samples=decoded.getChannelData(0),envelope=[];let previousEnergy=0;
 // Positive energy change across short, non-overlapping frames is an onset-strength approximation.
 for(let i=0;i<samples.length;i+=hop){let energy=0;for(let j=i;j<Math.min(samples.length,i+hop);j++)energy+=samples[j]*samples[j];energy=Math.sqrt(energy/hop);envelope.push(Math.round(Math.max(0,energy-previousEnergy)*100000)/100000);previousEnergy=energy;if(envelope.length%3000===0)await sleep(0)}
 $('tempoStatus').textContent='正在估计分段速度…';const result=await api('/api/analyze',{rate,envelope,preferredBpm:Number($('bpm').value)},AbortSignal.timeout(60000));tempoCandidate=result;$('tempoText').value=result.segments.map(p=>p.time+','+p.bpm).join('\n');$('tempoStatus').textContent=`起音 ${result.onsets} · 置信度 ${Math.round(result.confidence*100)}% · ${result.segments.length} 段。${result.warning}`;return result;
 }finally{await ac.close();$('analyze').disabled=false}
}
$('analyze').onclick=protect(analyzeAudio);
window.touchchart={drawChart,refresh,getState:()=>state,loadFile,setPlaying,toggleRecord,secondsOf,analyzeAudio,rebuildIndex,timeBeat,beatTime};

protect(async()=>{await synchronize();await refresh();poll()})().then(()=>{if(!synced)poll()});
</script></html>"""

TOUCH_HTML = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><title>TouchChart 触控板</title><style>""" + CSS + r"""
html,body{width:100%;height:100%;overflow:hidden;touch-action:none;user-select:none;-webkit-user-select:none;overscroll-behavior:none}#pad{position:fixed;inset:58px 12px 12px;touch-action:none}header{height:48px;padding:10px 16px;justify-content:space-between;gap:8px;font-size:12px}#warning{position:fixed;bottom:16px;left:20px;right:20px;color:#ffd23f;text-align:center;font-size:12px;pointer-events:none}#hint{position:fixed;top:45%;width:100%;text-align:center;pointer-events:none;color:#66758b;letter-spacing:3px;font-size:12px}#pad canvas{width:100%;height:100%;pointer-events:none}
</style><header><span id="status"><i class="dot"></i>同步中</span><span id="rtt">RTT —ms</span><span id="count">上报 0 指</span></header><div id="hint">TOUCH / HOLD / SWIPE<br><br>跟着节拍，打出你的谱面。</div><div id="pad"><canvas id="surface"></canvas></div><div id="warning"></div><script>""" + COMMON_JS + r"""
const pad=$('pad'),canvas=$('surface'),ctx=canvas.getContext('2d'), fingers=new Map(),moves=new Map();let moveTimer=null,attempts=0,reconnecting=false,wake=null;
function paint(){const W=pad.clientWidth,H=pad.clientHeight,dpr=devicePixelRatio||1;canvas.width=W*dpr;canvas.height=H*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,W,H);ctx.strokeStyle='#ffffff33';ctx.lineWidth=1;for(let i=1;i<3;i++){ctx.beginPath();ctx.moveTo(i*W/3,0);ctx.lineTo(i*W/3,H);ctx.stroke();ctx.beginPath();ctx.moveTo(0,i*H/3);ctx.lineTo(W,i*H/3);ctx.stroke()}for(const f of fingers.values()){ctx.beginPath();ctx.arc(f.x*W,f.y*H,34,0,Math.PI*2);ctx.fillStyle='#35e0d044';ctx.fill();ctx.strokeStyle='#35e0d0';ctx.stroke();ctx.fillStyle='#e6ebf2';ctx.font='20px ui-monospace';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(String(f.slot),f.x*W,f.y*H)}$('count').textContent='上报 '+fingers.size+' 指';$('hint').style.opacity=fingers.size?'0':'1'}
function networkError(err){console.error(err);synced=false;$('status').textContent='● 断线';$('status').style.color='#ff4d6d';$('warning').textContent=err.message;fingers.clear();moves.clear();paint();if(!reconnecting)connect()}
async function connect(){reconnecting=true;while(attempts<10){try{$('status').textContent='● 同步中';$('status').style.color='#ffd23f';await synchronize();await api('/api/client/reset',{sid},AbortSignal.timeout(3000));attempts=0;$('status').textContent='● 已连接';$('status').style.color='#35e0d0';$('rtt').textContent='RTT '+rtt+'ms';$('warning').textContent=rtt>120?'网络不稳，建议靠近路由器':'';reconnecting=false;return}catch(err){synced=false;console.error(err);$('status').textContent='● 断线';$('status').style.color='#ff4d6d';$('warning').textContent=err.message;await sleep(Math.min(8000,500*2**attempts));attempts++}}reconnecting=false;$('warning').textContent='重连达到 10 次，请刷新页面';}
function point(e){const b=pad.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),y:Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))}}
function makeTouchEvent(e,k,p){return{k,f:fingers.get(e.pointerId).contactId,...p,t:epochNow()}}
function flush(){if(moveTimer)clearTimeout(moveTimer);moveTimer=null;const events=[...moves.values()];moves.clear();sendEvents(events)}
pad.addEventListener('pointerdown',e=>{e.preventDefault();if(!synced||fingers.has(e.pointerId)||fingers.size>=5)return;const used=new Set([...fingers.values()].map(f=>f.slot));let slot=1;while(used.has(slot))slot++;const p=point(e);fingers.set(e.pointerId,{slot,contactId:allocateContactId(),...p});pad.setPointerCapture(e.pointerId);sendEvents([makeTouchEvent(e,'down',p)]);try{navigator.vibrate?.(6)}catch{}paint()});
pad.addEventListener('pointermove',e=>{e.preventDefault();if(!fingers.has(e.pointerId))return;const p=point(e);Object.assign(fingers.get(e.pointerId),p);moves.set(e.pointerId,makeTouchEvent(e,'move',p));if(!moveTimer)moveTimer=setTimeout(flush,8);paint()},{passive:false});
for(const k of ['pointerup','pointercancel','lostpointercapture'])pad.addEventListener(k,e=>{e.preventDefault();if(!fingers.has(e.pointerId))return;flush();sendEvents([makeTouchEvent(e,k==='pointercancel'?'cancel':'up',point(e))]);fingers.delete(e.pointerId);paint()});
async function wakeScreen(){try{wake=await navigator.wakeLock?.request('screen')}catch{}}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'){wakeScreen();if(!synced&&!reconnecting)connect()}else{flush();sendEvents([...fingers.values()].map(p=>({k:'cancel',f:p.contactId,x:p.x,y:p.y,t:epochNow()})));fingers.clear();paint()}});
window.addEventListener('resize',paint);window.addEventListener('offline',()=>networkError(Error('网络已断开')));window.addEventListener('online',()=>{if(!reconnecting){attempts=0;connect()}});
async function heartbeat(){while(true){await sleep(2000);if(synced)try{await api('/api/sync',undefined,AbortSignal.timeout(3000))}catch(err){networkError(err)}}}
paint();wakeScreen();connect();heartbeat();
</script></html>"""

DESKTOP_HTML = DESKTOP_HTML.replace("__LINE_FRACTION__", str(JUDGE_LINE_FRACTION)).replace("__SCROLL_RATE__", str(SCROLL_HEIGHTS_PER_SECOND))

SELFTEST_HTML = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>TouchChart 自动自检</title><style>""" + CSS + r"""body{padding:28px}pre{white-space:pre-wrap;line-height:1.7}iframe{width:1200px;height:900px;border:0;position:absolute;left:-1300px}</style><h1>TouchChart 自动自检</h1><p class="muted">独立临时服务运行 11 项测试；不会清空当前谱面。浏览器测量真实预览渲染函数。</p><button id="run">运行全部自检</button><pre id="result">等待运行</pre><iframe id="desktop" src="/"></iframe><script>""" + COMMON_JS + r"""
window.selftestResult=null;
async function run(){
 $('run').disabled=true;$('result').textContent='正在运行 Python / HTTP / RPE 转换验收…';
 try{const result=await api('/api/selftest',{});const w=$('desktop').contentWindow;for(let i=0;i<100&&!w.touchchart;i++)await sleep(50);if(!w.touchchart)throw Error('预览页面未就绪');
 const notes=Array.from({length:1000},(_,i)=>({type:i%4+1,startTime:[Math.floor(i/4),i%4,4],endTime:[Math.floor(i/4)+1,i%4,4],positionX:(i%16/15-.5)*1350}));
 for(let i=0;i<20;i++)w.touchchart.drawChart(2,notes);const samples=[];for(let i=0;i<120;i++){await new Promise(requestAnimationFrame);const t=performance.now();w.touchchart.drawChart(i/60,notes);samples.push(performance.now()-t)}samples.sort((a,b)=>a-b);const mean=samples.reduce((a,b)=>a+b,0)/samples.length,p95=samples[Math.floor(samples.length*.95)],max=samples.at(-1);const performanceOK=max<4;
 window.selftestResult={...result,meanMs:mean,p95Ms:p95,maxMs:max,performanceOK};$('result').textContent=result.output+`\n1000 notes / 120 frames: mean=${mean.toFixed(3)}ms p95=${p95.toFixed(3)}ms max=${max.toFixed(3)}ms\nBrowser frame budget <4ms: ${performanceOK?'PASS':'FAIL'}\n`;$('result').dataset.ok=String(result.ok&&performanceOK);
 }catch(err){console.error(err);$('result').textContent+='\nFAIL: '+err.message;$('result').dataset.ok='false'}finally{$('run').disabled=false}}
$('run').onclick=run;if(new URLSearchParams(location.search).has('autorun'))run();
</script></html>"""


def serve(port=8787, host="0.0.0.0", legacy=False):
    server = Server((host, port), legacy)
    print(f"TouchChart desktop: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"TouchChart phone:   http://{lan_ip()}:{server.server_port}/touch", flush=True)
    print("Stop: Ctrl+C", flush=True)
    try:
        server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--legacy-types", action="store_true")
    args = parser.parse_args()
    serve(args.port, args.host, args.legacy_types)
