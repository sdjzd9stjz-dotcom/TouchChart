"""TouchChart Desktop: native Tk controls/canvas with the verified recording core."""
from __future__ import annotations
import argparse
import array
import bisect
import ctypes
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import wave
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = Path(__file__).resolve().parent
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(HERE.parent / 'touchchart'))
import touchchart as core
from cloud_bridge import Bridge
from pack_chart import pack, phichain_project, validate_chart, validate_package, package_path


class NativeHandler(core.Handler):
    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, fmt, *args):
        # Windowed applications have no stderr; protocol errors still return JSON.
        pass

    def dispatch(self):
        route = self.path.split('?')[0]
        if getattr(self.server,'cloud_active',False) and self.command=='POST':
            self.close_connection=True
            return self.reply(dict(error='互联网配对已启用，请使用专属手机链接'),403)
        if route in ('/', '/touch/') and self.command in ('GET', 'HEAD'):
            self.send_response(302)
            self.send_header('Location', '/touch')
            self.send_header('Content-Length', '0')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return
        if route in ('/', '/touch/', '/selftest', '/api/selftest'):
            self.close_connection = True
            return self.reply(dict(error='请使用桌面程序控制录制；手机浏览器入口为 /touch'),404)
        return super().dispatch()


def ffmpeg_path():
    candidates = [HERE / 'ffmpeg.exe', HERE.parent / 'touchchart/.tools/ffmpeg.exe']
    return next((p for p in candidates if p.is_file()), None)


def decode_audio(source, destination):
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise ValueError('缺少音频解码器 ffmpeg.exe，请完整解压软件目录')
    result = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                             '-vn', '-ac', '1', '-ar', '32000', '-c:a', 'pcm_s16le', str(destination)],
                            capture_output=True, timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError(result.stderr.decode('utf-8', errors='replace')[-1000:])
    with wave.open(str(destination)) as w:
        return w.getnframes() / w.getframerate()


def analyze_wav(path, preferred=150):
    envelope, previous = [], 0.0
    with wave.open(str(path)) as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError('分析输入必须为已解码单声道 PCM16')
        step = w.getframerate() // 100
        while raw := w.readframes(step):
            samples = array.array('h', raw)
            rms = math.sqrt(sum(v * v for v in samples) / len(samples)) / 32768
            envelope.append(max(0, rms - previous)); previous = rms
    return core.analyze_onsets(envelope, 100, preferred)


def convert_json_to_phira(chart_path, audio_path, output):
    """Recover an already-exported RPE JSON without changing the current session."""
    chart = json.loads(Path(chart_path).read_text(encoding='utf-8-sig'))
    validate_chart(chart)
    target = package_path(output)
    with tempfile.TemporaryDirectory(prefix='touchchart-json-to-phira-') as folder:
        folder = Path(folder)
        audio = folder / 'song.wav'
        decode_audio(audio_path, audio)
        source = folder / 'chart.json'
        source.write_text(json.dumps(chart, ensure_ascii=False), encoding='utf-8')
        return pack(source, audio, target)


class Audio:
    def __init__(self):
        self.loaded = False
        self.mci = ctypes.windll.winmm.mciSendStringW if sys.platform == 'win32' else None
    def command(self, text, check=True):
        if not self.mci:
            return ''
        out = ctypes.create_unicode_buffer(1024)
        code = self.mci(text, out, len(out), 0)
        if code and check:
            error = ctypes.create_unicode_buffer(1024)
            ctypes.windll.winmm.mciGetErrorStringW(code, error, len(error))
            raise ValueError(error.value)
        return out.value
    def load(self, path):
        self.close()
        self.command(f'open "{path}" type waveaudio alias touchchart_audio')
        self.command('set touchchart_audio time format milliseconds'); self.loaded = True
    def play(self, position):
        if self.loaded:
            self.command(f'play touchchart_audio from {round(position * 1000)}')
    def pause(self):
        if self.loaded: self.command('pause touchchart_audio', False)
    def close(self):
        if self.loaded: self.command('close touchchart_audio', False)
        self.loaded = False


class Desktop:
    def __init__(self, root, port=8790):
        self.root = root
        self.server = core.Server(('0.0.0.0', port))
        self.server.RequestHandlerClass = NativeHandler
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.audio = Audio(); self.temp = tempfile.TemporaryDirectory(prefix='touchchart-desktop-')
        self.source = None; self.decoded = None; self.duration = 120.0
        self.position = 0.0; self.origin = time.perf_counter(); self.playing = False
        self.closed = False; self.jobs = queue.Queue(); self.busy = False
        self.bridge=None
        self.last_move = 0; self.dragging = False; self.buckets = {}; self.index_key = None
        self.saved_seq = None; self.candidate = None; self.index_count = 0
        self.root.title('TouchChart Desktop · 下落速度控制版')
        self.root.geometry('1260x850'); self.root.minsize(1060, 740)
        self.root.configure(bg='#101521')
        style = ttk.Style(root); style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), background='#172031', foreground='#e7edf7', fieldbackground='#222e42')
        style.configure('TButton', padding=7, background='#2b3a52')
        style.map('TButton', background=[('active', '#375175')])
        style.configure('Accent.TButton', background='#256f8b')
        style.configure('TLabel', padding=3)
        style.configure('TEntry', insertcolor='white')
        head = ttk.Frame(root, padding=14); head.pack(fill='x')
        ttk.Label(head, text='TouchChart', font=('Segoe UI', 23, 'bold')).pack(side='left')
        ttk.Label(head, text='DESKTOP  /  手打 · 变速 · 高密度', foreground='#8ed9e8').pack(side='left', padx=18)
        self.address = f'{core.lan_ip()}:{self.server.server_port}'
        ttk.Label(head, text='APK 连接  '+self.address).pack(side='right')
        self.mobile_url = f'http://{self.address}/touch'
        connection = ttk.Frame(root, padding=(18, 0, 18, 5)); connection.pack(fill='x')
        ttk.Label(connection, text='手机浏览器').pack(side='left')
        self.mobile_url_var = tk.StringVar(value=self.mobile_url)
        ttk.Entry(connection, textvariable=self.mobile_url_var, state='readonly').pack(side='left', fill='x', expand=True, padx=8)
        ttk.Button(connection, text='复制网页地址', command=self.copy_mobile_url).pack(side='left')
        cloud=ttk.Frame(root,padding=(18,0,18,5));cloud.pack(fill='x')
        ttk.Label(cloud,text='互联网服务器').pack(side='left')
        self.cloud_url=tk.StringVar(value='')
        ttk.Entry(cloud,textvariable=self.cloud_url,width=36).pack(side='left',padx=8)
        ttk.Button(cloud,text='创建配对',command=self.guard(self.connect_cloud)).pack(side='left')
        ttk.Button(cloud,text='解除配对 / 返回局域网',command=self.guard(self.disconnect_cloud)).pack(side='left',padx=8)
        ttk.Button(cloud,text='手势灵敏度',command=self.gesture_dialog).pack(side='left',padx=5)
        self.cloud_status=tk.StringVar(value='局域网模式')
        ttk.Label(cloud,textvariable=self.cloud_status).pack(side='left')
        toolbar = ttk.Frame(root, padding=(14, 4)); toolbar.pack(fill='x')
        for title, action in [('载入音频', self.load_dialog), ('开始 / 停止录制', self.toggle_record),
                              ('播放 / 暂停', self.toggle_play), ('导出 Phira 谱包', self.export_dialog),
                              ('Phichain 项目', self.project_dialog)]:
            button = ttk.Button(toolbar, text=title, command=self.guard(action), style='Accent.TButton' if '录制' in title or 'Phira' in title else 'TButton')
            button.pack(side='left', padx=3)
            if 'Phira' in title:self.export_button=button
        more = ttk.Menubutton(toolbar, text='其他导出');more.pack(side='left', padx=3)
        self.export_menu = tk.Menu(more, tearoff=False)
        self.export_menu.add_command(label='已有 JSON 转成 Phira 谱包…', command=self.guard(self.convert_json_dialog))
        self.export_menu.add_command(label='仅导出 JSON（供制谱器编辑）…', command=self.guard(self.export_json_dialog))
        more.configure(menu=self.export_menu)
        self.audio_label = ttk.Label(root, text='先载入音频，或直接开始空白录制', padding=(18, 6)); self.audio_label.pack(fill='x')
        body = ttk.Frame(root, padding=(14, 5)); body.pack(fill='both', expand=True)
        left = ttk.Frame(body, width=300, padding=10); left.pack(side='left', fill='y'); left.pack_propagate(False)
        right = ttk.Frame(body, padding=(12, 0, 0, 0)); right.pack(side='left', fill='both', expand=True)
        self.fields = {}; self.setting_widgets = []
        defaults = [('bpm','恒定 / 首段 BPM','150'),('offsetMs','谱面偏移 ms','0'),('comp','输入补偿 ms','0'),
                    ('division','量化分母','16'),('lanes','轨道吸附（0=无）','0'),('noteMode','类型模式','auto'),('dragMode','滑动模式','single')]
        choices = {'division':['4','8','16','24','32','48','64','96','128','192','384','768'],
                   'lanes':['0','4','8','16','32','64'],'noteMode':['auto','tap','hold','flick','drag'],'dragMode':['single','chain']}
        for key, title, value in defaults:
            line = ttk.Frame(left); line.pack(fill='x', pady=3)
            ttk.Label(line, text=title).pack(side='left')
            var = tk.StringVar(value=value); self.fields[key] = var
            widget = ttk.Combobox(line, textvariable=var, values=choices[key], width=10, state='readonly') if key in choices else ttk.Entry(line, textvariable=var, width=12)
            widget.pack(side='right'); self.setting_widgets.append((widget, 'readonly' if key in choices else 'normal'))
        row = ttk.Frame(left); row.pack(fill='x', pady=7)
        self.grid_var = tk.StringVar(value='16,24')
        ttk.Entry(row, textvariable=self.grid_var, width=11).pack(side='left')
        ttk.Button(row, text='LCM 合并', command=self.guard(self.merge_grid)).pack(side='left', padx=3)
        ttk.Button(left, text='高密度：1/64 + 滑动成串', command=self.guard(self.dense)).pack(fill='x')
        ttk.Label(left, text='变速段：音频秒数,BPM', padding=(0,12,0,3)).pack(anchor='w')
        self.tempo = tk.Text(left, height=5, bg='#0e1523', fg='#dceafa', insertbackground='white', font=('Consolas',11), relief='flat')
        self.tempo.pack(fill='x'); self.tempo.insert('1.0','')
        ttk.Button(left, text='分析当前音频 BPM', command=self.guard(self.analyze)).pack(fill='x', pady=5)
        row = ttk.Frame(left); row.pack(fill='x')
        for text, factor in [('候选 ×0.5',.5),('候选 ×2',2)]:
            ttk.Button(row,text=text,command=self.guard(lambda f=factor:self.scale_tempo(f))).pack(side='left',expand=True,fill='x')
        ttk.Label(left, text='留空使用恒定 BPM。自动分析为候选，\n开始前请试听校正半速/倍速与变化点。', foreground='#99b0c8', padding=(0,8)).pack(anchor='w')
        speed_row = ttk.Frame(right); speed_row.pack(fill='x', pady=(0, 8))
        ttk.Label(speed_row, text='音符下落速度').pack(side='left')
        self.speed_text = tk.StringVar(value='1')
        self.speed_entry = ttk.Entry(speed_row, textvariable=self.speed_text, width=8)
        self.speed_entry.pack(side='left')
        self.speed_entry.bind('<Return>',lambda e:self.guard(lambda:self.set_scroll_speed(self.speed_text.get()))())
        self.speed_entry.bind('<FocusOut>',lambda e:self.commit_speed())
        ttk.Label(speed_row, text='×').pack(side='left')
        self.speed_scale = ttk.Scale(speed_row, from_=.25, to=5, value=1)
        self.speed_scale.pack(side='left', fill='x', expand=True, padx=8)
        self.speed_scale.configure(command=self.set_scroll_speed)
        self.speed_reset = ttk.Button(speed_row, text='恢复 1×', command=lambda:self.set_scroll_speed(1))
        self.speed_reset.pack(side='right')
        ttk.Label(right, text='0.25×～5× 无级调节，可输入任意小数后回车 · 即时预览并保存到谱包', foreground='#99b0c8').pack(anchor='w', pady=(0,5))
        self.canvas = tk.Canvas(right, bg='#0c1220', highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<ButtonPress-1>',lambda e:self.pointer('down',e))
        self.canvas.bind('<B1-Motion>',lambda e:self.pointer('move',e))
        self.canvas.bind('<ButtonRelease-1>',lambda e:self.pointer('up',e))
        bottom = ttk.Frame(right); bottom.pack(fill='x',pady=6)
        self.seekbar = ttk.Scale(bottom,from_=0,to=120,command=self.seek);self.seekbar.pack(side='left',fill='x',expand=True)
        self.clock = ttk.Label(bottom,text='0.00s',width=12);self.clock.pack(side='right')
        self.note_list = tk.Listbox(right,height=5,bg='#152034',fg='#dceafa',selectbackground='#22698b',relief='flat',font=('Consolas',10))
        self.note_list.pack(fill='x');self.note_ids=[];self.list_signature=None
        row=ttk.Frame(right);row.pack(fill='x',pady=4)
        for title,action in [('撤销',self.undo),('删除选中',self.delete_selected),('清空',self.clear)]:
            ttk.Button(row,text=title,command=self.guard(action)).pack(side='left',padx=3)
        self.stats_label=ttk.Label(row,text='0 音符');self.stats_label.pack(side='right')
        self.status=tk.StringVar(value='就绪 · 手机浏览器或 APK 均可连接；R 录制，空格播放，Ctrl+E 导出 Phira 谱包')
        ttk.Label(root,textvariable=self.status,padding=12,foreground='#9edce6').pack(fill='x')
        root.bind('<Control-e>',lambda e:self.guard(self.export_dialog)())
        root.bind('<Control-z>',lambda e:self.guard(self.undo)())
        root.bind('<KeyPress-r>',lambda e:self.key_action(e,self.toggle_record))
        root.bind('<space>',lambda e:self.key_action(e,self.toggle_play))
        root.protocol('WM_DELETE_WINDOW',self.close)
        self.tick()

    @property
    def recorder(self):return self.server.recorder
    def apply_gestures(self,settings):
        core.validate_gestures(settings)
        with self.server.lock:self.recorder.gestures=dict(settings)
        self.status.set('手势灵敏度已应用：从下一次按下生效，已有音符不变')
    def gesture_dialog(self):
        win=tk.Toplevel(self.root);win.title('Hold / Drag / Flick 识别灵敏度');win.resizable(False,False)
        panel=ttk.Frame(win,padding=18);panel.pack(fill='both',expand=True)
        variables={}
        labels=[('holdMs','Hold 最短按住时间（ms）',1),('dragDistance','Drag 起始位移 / 长按容差（%）',100),('flickDistance','Flick 最小位移（%）',100),('flickMs','Flick 最长划动时间（ms）',1)]
        for key,label,factor in labels:
            row=ttk.Frame(panel);row.pack(fill='x',pady=6)
            ttk.Label(row,text=label,width=34).pack(side='left')
            variables[key]=tk.StringVar(value=f'{self.recorder.gestures[key]*factor:g}')
            ttk.Entry(row,textvariable=variables[key],width=12).pack(side='right')
        ttk.Label(panel,text='长按误变 Drag：调高移动容差，例如 5% → 8%。\nFlick 更难触发：提高最小位移，或缩短最长时间。\n移动量按触控区宽高归一化，使用离按下点的最远距离。\n不累加抖动路径；已按住的手势继续使用按下时的设置。',padding=(0,10)).pack(anchor='w')
        def preset(values):
            for key,_,factor in labels:variables[key].set(f'{values[key]*factor:g}')
        def apply():
            self.apply_gestures({key:float(variables[key].get())/factor for key,_,factor in labels});win.destroy()
        row=ttk.Frame(panel);row.pack(fill='x')
        ttk.Button(row,text='默认',command=lambda:preset(core.GESTURE_DEFAULTS)).pack(side='left')
        ttk.Button(row,text='长按防抖',command=lambda:preset(dict(holdMs=200,dragDistance=.08,flickDistance=.12,flickMs=250))).pack(side='left',padx=6)
        ttk.Button(row,text='应用',command=self.guard(apply)).pack(side='right')
    def connect_cloud(self):
        if self.bridge:raise ValueError('已有互联网配对，请先解除')
        if self.recorder.recording:raise ValueError('请先停止录制再切换连接')
        url=self.cloud_url.get()
        def done(bridge):
            self.bridge=bridge;self.server.cloud_active=True
            with self.server.lock:self.recorder.tracker=core.FingerTracker()
            self.mobile_url=bridge.mobile_url;self.mobile_url_var.set(self.mobile_url)
            bridge.thread.start();self.status.set('已创建独立配对，复制上方手机链接给唯一的移动端')
        self.job(lambda:Bridge(self.server,url),done)
    def disconnect_cloud(self):
        if self.busy:raise ValueError('请等待当前连接操作完成')
        if self.recorder.recording:raise ValueError('请先停止录制再解除配对')
        if self.bridge:self.bridge.close();self.bridge=None
        self.server.cloud_active=False;self.mobile_url=f'http://{self.address}/touch'
        self.mobile_url_var.set(self.mobile_url);self.cloud_status.set('局域网模式')
    def set_scroll_speed(self,value):
        value=float(value);core.validate_scroll_speed(value)
        with self.server.lock:
            if self.busy:
                value=self.recorder.meta.get('scrollSpeed',1.0)
            elif value!=self.recorder.meta.get('scrollSpeed',1.0):
                self.recorder.meta['scrollSpeed']=value
                self.saved_seq=None
                self.server.revision+=1;self.server.changed.notify_all()
        if abs(float(self.speed_scale.get())-value)>1e-9:self.speed_scale.set(value)
        self.speed_text.set(f'{value:.12g}')
    def commit_speed(self):
        try:self.set_scroll_speed(self.speed_text.get())
        except ValueError:
            self.speed_text.set(f'{self.recorder.meta.get("scrollSpeed",1.0):.12g}')
            self.status.set('下落速度须为 0.25～5 的有限小数，已恢复原值')
    def copy_mobile_url(self):
        self.root.clipboard_clear();self.root.clipboard_append(self.mobile_url)
        self.status.set('已复制手机网页地址：'+self.mobile_url)
    def guard(self, callback):
        def call(*args):
            try:return callback(*args)
            except Exception as error:
                self.status.set(str(error));messagebox.showerror('TouchChart',str(error),parent=self.root)
        return call
    def key_action(self,event,action):
        if not isinstance(event.widget,(tk.Text,ttk.Entry,ttk.Combobox,tk.Listbox)):self.guard(action)()
    def editable(self):
        if self.recorder.recording or self.recorder.notes:raise ValueError('请先导出并清空，避免改变已有音符时间')
    def config(self):
        result={k:(v.get() if k in ('noteMode','dragMode') else float(v.get()) if k in ('bpm','offsetMs','comp') else int(v.get())) for k,v in self.fields.items()}
        result['bpmSegments']=self.parse_tempo()
        core.validate_config(result);return result
    def parse_tempo(self):
        result=[]
        for line in self.tempo.get('1.0','end').strip().splitlines():
            if not line.strip():continue
            t,b=line.replace('，',',').split(',');result.append(dict(time=float(t),bpm=float(b)))
        core.tempo_map(float(self.fields['bpm'].get()),result);return result
    def merge_grid(self):
        self.editable();v=[int(x.strip()) for x in self.grid_var.get().replace('，',',').split(',')]
        self.fields['division'].set(str(core.combined_division(v)))
    def dense(self):
        self.editable();self.fields['division'].set('64');self.fields['dragMode'].set('chain')
    def scale_tempo(self,factor):
        self.editable();points=self.parse_tempo()
        for p in points:p['bpm']*=factor
        core.tempo_map(float(self.fields['bpm'].get()),points)
        self.set_tempo(points)
    def set_tempo(self,points):
        self.tempo.delete('1.0','end');self.tempo.insert('1.0','\n'.join(f"{p['time']:g},{p['bpm']:g}" for p in points))
    def job(self,work,done):
        if self.busy:raise ValueError('上一项文件操作仍在进行')
        self.busy=True
        def run():
            try:self.jobs.put((done,work(),None))
            except Exception as error:self.jobs.put((done,None,error))
        threading.Thread(target=run,daemon=True).start()
    def load_dialog(self):
        if self.busy or self.recorder.recording:raise ValueError('请先停止录制并等待当前操作完成，再选择音乐')
        p=filedialog.askopenfilename(filetypes=[('音频','*.wav *.mp3 *.ogg *.flac *.m4a'),('所有文件','*.*')])
        if p:self.load_audio(Path(p))
    def load_audio(self,path):
        if self.busy or self.recorder.recording:raise ValueError('请先停止录制并等待当前操作完成，再选择音乐')
        self.set_playing(False);self.status.set('正在解码音频…')
        target=Path(self.temp.name)/'decoded.wav'
        def done(duration):
            self.source=path;self.decoded=target;self.duration=duration;self.audio.load(target)
            with self.server.lock:self.recorder.meta.update(name=path.stem, song=path.name)
            self.position=0;self.seekbar.configure(to=duration);self.audio_label.configure(text=f'{path.name} · {duration:.2f} 秒')
            self.status.set('音乐已载入，已有音符保持不变；可导出 Phira 谱包' if self.recorder.notes else '音频已载入，可以分析 BPM 或开始录制')
        self.audio.close();self.job(lambda:decode_audio(path,target),done)
    def analyze(self):
        self.editable()
        if not self.decoded:raise ValueError('请先载入音频')
        self.status.set('正在分析本地音频…');preferred=float(self.fields['bpm'].get())
        def done(result):
            self.candidate=result;self.set_tempo(result['segments'])
            self.status.set(f"候选 {len(result['segments'])} 段 / 置信度 {result['confidence']:.0%} · 请试听校正")
        self.job(lambda:analyze_wav(self.decoded,preferred),done)
    def current(self):return self.position+(time.perf_counter()-self.origin if self.playing else 0)
    def transport(self):
        with self.server.lock:
            r=self.recorder;now=core.epoch_ms();r.settle(now)
            r.anchor=now-self.position*1000;r.paused=not self.playing
            r.transport=dict(position=self.position,playing=self.playing)
            self.server.revision+=1;self.server.changed.notify_all()
    def set_playing(self,value):
        self.position=self.current();self.origin=time.perf_counter();self.playing=value
        if value:self.audio.play(self.position)
        else:self.audio.pause()
        self.transport()
    def toggle_play(self):
        if self.busy:raise ValueError('请等待音频操作完成')
        self.set_playing(not self.playing)
    def toggle_record(self):
        if self.busy:raise ValueError('请等待音频操作完成')
        with self.server.lock:
            r=self.recorder
            if r.recording:
                r.stop();self.set_playing(False);self.status.set('已停止 · 请导出保存')
            else:
                config=self.config() if not r.notes else dict(r.config)
                self.position=0;self.origin=time.perf_counter();self.playing=True
                self.audio.play(0)
                r.start(dict(config,startEpoch=core.epoch_ms(),name=self.source.stem if self.source else 'Untitled',song=self.source.name if self.source else 'song.wav'))
                r.transport=dict(position=0,playing=True);self.status.set('录制中 · 支持电脑鼠标、手机网页和 APK 输入')
            self.server.revision+=1;self.server.changed.notify_all()
    def seek(self,value):
        self.position=float(value);self.origin=time.perf_counter()
        if self.playing:self.audio.play(self.position)
        self.transport()
    def pointer(self,kind,event):
        now=core.epoch_ms()
        if kind=='move' and now-self.last_move<8:return
        self.last_move=now
        if kind=='down':self.dragging=True
        if kind=='up' and not self.dragging:return
        if kind=='up':self.dragging=False
        item=dict(k=kind,f=0,x=min(1,max(0,event.x/max(1,self.canvas.winfo_width()))),y=min(1,max(0,event.y/max(1,self.canvas.winfo_height()))),t=now)
        try:
            with self.server.lock:
                self.recorder.feed(item,'desktop-native');self.server.revision+=1;self.server.changed.notify_all()
        except ValueError as error:self.status.set(str(error))
    def undo(self):
        with self.server.lock:
            if self.recorder.notes:self.recorder.notes.pop();self.recorder.recount();self.index_key=None
    def delete_selected(self):
        indexes=self.note_list.curselection()
        if not indexes:return
        nid=self.note_ids[indexes[0]]
        with self.server.lock:
            r=self.recorder;r.notes=[n for n in r.notes if n['id']!=nid];r.recount();self.index_key=None
    def clear(self,confirm=True):
        if confirm and not messagebox.askyesno('清空','确认清空所有音符？',parent=self.root):return
        self.set_playing(False)
        with self.server.lock:
            speed=self.recorder.meta.get('scrollSpeed',1.0);gestures=dict(self.recorder.gestures)
            self.server.recorder=core.Recorder();self.recorder.meta['scrollSpeed']=speed;self.index_key=None
            self.recorder.gestures=gestures
        self.position=0;self.saved_seq=None
    def chart(self):
        with self.server.lock:
            if self.recorder.recording:self.toggle_record()
            if not self.recorder.notes:raise ValueError('尚无音符，请先录制')
            chart=self.recorder.chart();validate_chart(chart);return chart
    def export_to(self,path):
        chart=self.chart();Path(path).write_text(json.dumps(chart,ensure_ascii=False,indent=2),encoding='utf-8')
        self.saved_seq=tuple(n['id'] for n in self.recorder.notes);self.status.set('已保存 '+str(path));return chart
    def export_dialog(self):
        self.pack_dialog()
    def export_json_dialog(self):
        self.chart();p=filedialog.asksaveasfilename(title='导出制谱器 JSON（不是 Phira 游玩包）',defaultextension='.json',initialfile='chart.json',filetypes=[('RPE JSON，仅供编辑','*.json')])
        if p:self.export_to(p)
    def package_to(self,path,project=False):
        if not self.decoded:raise ValueError('请先载入音频，完整包需要音乐')
        chart=self.chart();saved=tuple(n['id'] for n in self.recorder.notes)
        source=Path(self.temp.name)/'chart.json';source.write_text(json.dumps(chart,ensure_ascii=False),encoding='utf-8')
        result=(phichain_project if project else pack)(source,self.decoded,Path(path))
        self.saved_seq=saved;return result
    def pack_dialog(self):
        if self.busy:raise ValueError('请等待当前文件操作完成')
        self.chart()
        if not self.decoded:raise ValueError('Phira 游玩需要音乐。请先点「载入音频」选择对应歌曲，已有音符会保留')
        p=filedialog.asksaveasfilename(title='导出可供 Phira 导入的完整谱包',defaultextension='.pez',initialfile=(self.source.stem if self.source else 'TouchChart')+'.pez',filetypes=[('Phira 游玩谱包','*.pez'),('ZIP 谱包','*.zip')])
        if p:
            p=package_path(p)
            self.job(lambda:self.package_to(p),self.package_saved)
    def package_saved(self,path):
        self.status.set('Phira 谱包已保存并校验：'+str(path))
        messagebox.showinfo('Phira 谱包已生成','已包含谱面、音乐、背景和元数据，并通过 ZIP 完整性校验。\n\n在 Phira 的本地谱面导入中选择此文件：\n'+str(path),parent=self.root)
    def convert_json_dialog(self):
        if self.busy:raise ValueError('请等待当前文件操作完成')
        chart=filedialog.askopenfilename(title='选择已经导出的 RPE JSON',filetypes=[('RPE JSON','*.json')])
        if not chart:return
        audio=filedialog.askopenfilename(title='选择这份谱面对应的音乐',filetypes=[('音频','*.wav *.mp3 *.ogg *.flac *.m4a')])
        if not audio:return
        output=filedialog.asksaveasfilename(title='保存完整 Phira 谱包',defaultextension='.pez',initialfile=Path(chart).stem+'.pez',filetypes=[('Phira 游玩谱包','*.pez'),('ZIP 谱包','*.zip')])
        if output:
            output=package_path(output)
            self.job(lambda:convert_json_to_phira(chart,audio,output),self.package_saved)
    def project_dialog(self):
        self.chart();parent=filedialog.askdirectory(title='选择父目录，将创建 TouchChart-日期 项目')
        if parent:
            p=Path(parent)/time.strftime('TouchChart-%Y%m%d-%H%M%S')
            self.job(lambda:self.package_to(p,True),lambda p:self.status.set('原生项目已保存 '+str(p)))
    def draw(self,position):
        c=self.canvas;w=max(1,c.winfo_width());h=max(1,c.winfo_height());line=h*.8657
        c.delete('all')
        for x in range(1,8):c.create_line(w*x/8,0,w*x/8,h,fill='#1c2940')
        c.create_line(0,line,w,line,fill='#f3d885',width=3)
        c.create_text(14,16,anchor='nw',text='TOUCH / 鼠标或手机输入',fill='#6c89a9',font=('Segoe UI',10))
        with self.server.lock:
            r=self.recorder;key=(id(r),len(r.notes),r.note_seq)
            if key!=self.index_key:
                append=self.index_key is not None and self.index_key[0]==id(r) and len(r.notes)>=self.index_count
                if not append:self.buckets={};self.index_count=0
                for n in r.notes[self.index_count:]:
                    a=core.note_seconds(core.beat_value(n['startTime']),r.config['bpm'],r.config['offsetMs'],r.config['bpmSegments'])
                    b=core.note_seconds(core.beat_value(n['endTime']),r.config['bpm'],r.config['offsetMs'],r.config['bpmSegments'])
                    for bucket in range(math.floor(a),math.floor(b)+1):self.buckets.setdefault(bucket,[]).append((n,a,b))
                self.index_key=key;self.index_count=len(r.notes)
            slots=r.tracker.snapshot();stats=r.stats();recording=r.recording
            speed=180*r.meta.get('scrollSpeed',1.0)
            last=r.notes[-30:];signature=tuple(n['id'] for n in last)
        seen=set()
        for bucket in range(math.floor(position)-1,math.ceil(position+(line+20)/speed)+1):
            for n,a,b in self.buckets.get(bucket,[]):
                if n['id'] in seen:continue
                seen.add(n['id']);x=(n['positionX']/1350+.5)*w;y=line-(a-position)*speed;end=line-(b-position)*speed
                if y< -20 or end>h+20:continue
                color={1:'#61c7f2',2:'#53d8bd',3:'#f28ab8',4:'#f4ce60'}[n['type']]
                if n['type']==2:c.create_rectangle(x-7,end,x+7,y,fill='#247966',outline=color,width=2,tags=('note',f'note-{n["id"]}','hold-body'))
                c.create_rectangle(x-22,y-5,x+22,y+5,fill=color,outline='',tags=('note',f'note-{n["id"]}','note-head'))
        for slot in slots:
            if slot.get('x') is not None and slot['status'] not in ('空闲','离开'):
                x=slot['x']*w;y=slot['y']*h;c.create_oval(x-25,y-25,x+25,y+25,outline='#8eeafa',width=3)
                c.create_text(x,y,text=str(slot['slot']),fill='white')
        if signature!=self.list_signature:
            self.note_list.delete(0,'end');self.note_ids=[]
            for n in reversed(last):self.note_list.insert('end',f"#{n['id']:5}  {core.NAMES[n['type']]:5}  拍 {core.beat_value(n['startTime']):8.3f}  X {n['positionX']:7.1f}");self.note_ids.append(n['id'])
            self.list_signature=signature
        self.stats_label.configure(text=f"{'● 录制中  ' if recording else ''}{stats['total']} 音符 · Tap {stats['tap']} / Hold {stats['hold']} / Flick {stats['flick']} / Drag {stats['drag']}")
        locked=recording or stats['total']>0
        for widget,normal in self.setting_widgets:widget.configure(state='disabled' if locked else normal)
        self.tempo.configure(state='disabled' if locked else 'normal')
        self.speed_scale.configure(state='disabled' if self.busy else 'normal')
        self.speed_reset.configure(state='disabled' if self.busy else 'normal')
        self.speed_entry.configure(state='disabled' if self.busy else 'normal')
    def tick(self):
        if self.closed:return
        if self.bridge:self.cloud_status.set(self.bridge.message)
        try:
            while True:
                done,value,error=self.jobs.get_nowait();self.busy=False
                if error:self.status.set(str(error));messagebox.showerror('TouchChart',str(error),parent=self.root)
                else:self.guard(done)(value)
        except queue.Empty:pass
        position=self.current()
        if self.decoded and self.playing and position>=self.duration:
            if self.recorder.recording:self.toggle_record()
            else:self.set_playing(False)
            position=self.current()
        self.clock.configure(text=f'{position:.2f}s');self.draw(position)
        self.root.after(16,self.tick)
    def close(self,force=False):
        if not force and self.recorder.notes and self.saved_seq!=tuple(n['id'] for n in self.recorder.notes):
            if not messagebox.askyesno('退出','还有未导出的更改，确认退出？',parent=self.root):return
        self.closed=True
        if self.bridge:self.bridge.close()
        self.audio.close();self.server.shutdown();self.server.server_close()
        self.temp.cleanup();self.root.destroy()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8790)
    parser.add_argument('--selftest',type=Path)
    args=parser.parse_args()
    if args.selftest:
        from native_selftest import run
        raise SystemExit(run(args.selftest))
    root=tk.Tk()
    try:Desktop(root,args.port)
    except Exception as error:
        root.withdraw();messagebox.showerror('TouchChart 启动失败',str(error));root.destroy();return
    root.mainloop()


if __name__=='__main__':main()
