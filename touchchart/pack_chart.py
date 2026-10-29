"""Package an exported chart and local audio as a Phira .pez. Standard library only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib
import zipfile
import math
import wave


def validate_chart(chart):
    """Reject the concrete schema and geometry faults seen in the reference video."""
    if not isinstance(chart, dict) or chart.get('formatVersion') != 3 or not isinstance(chart.get('judgeLineList'), list):
        raise ValueError('RPE chart requires formatVersion=3 and judgeLineList; native Phichain JSON is not an RPE chart')
    if type(chart.get('META', {}).get('offset')) is not int:
        raise ValueError('META.offset must be an integer for Phira')
    def value(triple):
        if not isinstance(triple, list) or len(triple)!=3 or not all(type(v) is int for v in triple) or triple[0]<0 or not 0<=triple[1]<triple[2]:
            raise ValueError('Invalid normalized beat triple')
        return triple[0]+triple[1]/triple[2]
    previous=-1
    bpm_list=chart.get('BPMList',[])
    if not bpm_list or value(bpm_list[0]['startTime'])!=0:
        raise ValueError('BPMList must start at beat zero')
    for p in bpm_list:
        b=value(p['startTime'])
        if b<=previous or not isinstance(p.get('bpm'),(int,float)) or not math.isfinite(p['bpm']) or not 30<=p['bpm']<=600:
            raise ValueError('Invalid BPM segment')
        previous=b
    total=0
    for line in chart['judgeLineList']:
        if not isinstance(line.get('extended',{}),dict):
            raise ValueError('extended must be an object, not an array, for Phira')
        notes=line.get('notes',[])
        if line.get('numOfNotes')!=len(notes): raise ValueError('numOfNotes mismatch')
        for n in notes:
            start,end=value(n['startTime']),value(n['endTime'])
            if n.get('type') not in (1,2,3,4) or (n['type']==2 and end<=start) or (n['type']!=2 and end!=start):
                raise ValueError('Invalid note type or Hold duration')
            if not math.isfinite(n['positionX']) or not -675<=n['positionX']<=675 or not 0<n.get('size',0)<=4 or not 0<n.get('speed',0)<=10:
                raise ValueError('Invalid note geometry or speed')
        for layer in line['eventLayers']:
            for e in layer.get('alphaEvents',[]):
                if type(e['start']) is not int or type(e['end']) is not int: raise ValueError('Alpha must be integer')
        total+=len(notes)
    if not total: raise ValueError('Chart contains no notes')
    return total


def background_png():
    width, height = 640, 360
    pixels = b''.join(b'\0' + bytes((14 + y // 24, 17 + y // 18, 22 + y // 12)) * width for y in range(height))

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def package_path(path):
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix('.pez')
    if path.suffix.lower() not in ('.pez', '.zip'):
        raise ValueError('Phira 谱包请保存为 .pez 或 .zip，不能保存为 JSON 或仅修改 JSON 扩展名')
    return path


def validate_package(path):
    """Check the actual ZIP/EOCD, CRC and the resources referenced by our manifest."""
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise ValueError('文件不是完整 ZIP 谱包（缺少 EOCD）；单独 JSON 不能直接导入 Phira')
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('谱包 CRC 校验失败，文件可能不完整')
        names = archive.namelist()
        if len(names) != len(set(names)) or not {'chart.json', 'info.yml', 'info.txt'}.issubset(names):
            raise ValueError('谱包缺少根目录谱面或元数据')
        chart = json.loads(archive.read('chart.json').decode('utf-8'))
        count = validate_chart(chart)
        # The writer emits JSON scalars, a strict YAML subset with no external parser dependency.
        info = {k: json.loads(v.strip()) for k, v in (line.split(':', 1) for line in archive.read('info.yml').decode('utf-8').splitlines() if line.strip())}
        if info.get('format') != 'rpe' or info.get('chart') != 'chart.json':
            raise ValueError('谱包格式或谱面引用错误')
        for key, meta_key in [('music', 'song'), ('illustration', 'background')]:
            name = info.get(key)
            if name not in names or name != chart['META'].get(meta_key) or archive.getinfo(name).file_size == 0:
                raise ValueError('谱包资源缺失或引用不一致：' + key)
        seconds = None
        if info['music'].lower().endswith('.wav'):
            with archive.open(info['music']) as audio, wave.open(audio, 'rb') as wav:
                if wav.getnframes() <= 0 or wav.getframerate() <= 0:
                    raise ValueError('谱包音频为空')
                seconds = wav.getnframes() / wav.getframerate()
        with archive.open(info['illustration']) as image:
            if info['illustration'].lower().endswith('.png') and image.read(8) != b'\x89PNG\r\n\x1a\n':
                raise ValueError('谱包 PNG 背景无效')
    return dict(zip=True, eocd=True, crc=True, files=names, notes=count, audioSeconds=seconds)


def pack(chart_path, audio_path, output, background=None):
    chart_path, audio_path, output = map(Path, (chart_path, audio_path, output))
    output = package_path(output)
    chart = json.loads(chart_path.read_text(encoding='utf-8'))
    validate_chart(chart)
    if not audio_path.is_file():
        raise ValueError('Audio file does not exist')
    if not chart.get('judgeLineList') or not any(line.get('notes') for line in chart['judgeLineList']):
        raise ValueError('Chart contains no notes')
    if output.exists():
        raise FileExistsError('Refusing to overwrite existing output: ' + str(output))
    song_name = 'song' + audio_path.suffix.lower()
    background_name = 'background' + (Path(background).suffix.lower() if background else '.png')
    chart['META'].update(song=song_name, background=background_name)
    info = dict(name=chart['META'].get('name', 'Untitled'), difficulty=1.0, level='IN Lv.1',
                charter='TouchChart', composer=chart['META'].get('composer') or 'Unknown',
                illustrator='TouchChart' if background is None else 'Unknown',
                chart='chart.json', format='rpe', music=song_name, illustration=background_name,
                previewStart=0.0, previewEnd=None, aspectRatio=16 / 9, backgroundDim=.6,
                lineLength=6.0, offset=0.0, tags=[], intro='TouchChart rhythm draft', holdPartialCover=False)
    # JSON scalars are valid YAML scalars; avoid escaping or interpreting filenames as YAML.
    yaml = '\n'.join(k + ': ' + json.dumps(v, ensure_ascii=False) for k, v in info.items()) + '\n'
    editor_info = dict(Name=info['name'], Song=song_name, Picture=background_name, Chart='chart.json',
                       Level=info['level'], Composer=info['composer'], Charter=info['charter'], Illustrator=info['illustrator'])
    editor_text = '#\n' + '\n'.join(k + ': ' + str(v).replace('\n',' ').replace('\r',' ') for k,v in editor_info.items()) + '\n'
    picture = Path(background).read_bytes() if background else background_png()
    # Write and verify the entire archive before publishing its final name.
    # A failed export must not leave a half-written .pez for the user to import.
    fd, pending_name = tempfile.mkstemp(prefix='.touchchart-', suffix='.part', dir=output.parent)
    os.close(fd)
    pending = Path(pending_name)
    try:
        with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('chart.json', json.dumps(chart, ensure_ascii=False).encode('utf-8'))
            archive.writestr('info.yml', yaml.encode('utf-8'))
            archive.writestr('info.txt', editor_text.encode('utf-8'))
            archive.write(audio_path, song_name)
            archive.writestr(background_name, picture)
        validate_package(pending)
        if os.name == 'nt':
            os.rename(pending, output)  # Windows refuses to replace an existing destination.
        else:
            os.link(pending, output)  # Atomic no-clobber publication on POSIX.
    finally:
        pending.unlink(missing_ok=True)
    return output


def phichain_project(chart_path, audio_path, output, background=None):
    """v0.5.0 opens a project directory containing native chart.json and meta.json."""
    chart_path, audio_path, output = map(Path, (chart_path, audio_path, output))
    if output.exists():
        raise FileExistsError('Refusing to overwrite existing project: ' + str(output))
    if audio_path.suffix.lower() not in ('.wav', '.mp3', '.ogg', '.flac'):
        raise ValueError('Phichain v0.5.0 requires WAV/MP3/OGG/FLAC audio')
    converter = os.environ.get('PHICHAIN_CONVERTER', str(Path.home() / 'Apps/Phichain/app/phichain-v0.5.0-x86_64-pc-windows-msvc/phichain-converter.exe'))
    metadata = json.loads(chart_path.read_text(encoding='utf-8'))['META']
    validate_chart(json.loads(chart_path.read_text(encoding='utf-8')))
    with tempfile.TemporaryDirectory(prefix='touchchart-project-') as temporary:
        chart_copy = Path(temporary) / 'chart.json'
        shutil.copyfile(chart_path, chart_copy)
        subprocess.run([converter, '-i', 'rpe', '-o', 'phichain', str(chart_copy)], check=True, timeout=30)
        native = Path(temporary) / 'chart.phichain.json'
        json.loads(native.read_text(encoding='utf-8'))
        output.mkdir(parents=True)
        shutil.copyfile(native, output / 'chart.json')
        shutil.copyfile(audio_path, output / ('music' + audio_path.suffix.lower()))
        image_name = 'illustration' + (Path(background).suffix.lower() if background else '.png')
        (output / image_name).write_bytes(Path(background).read_bytes() if background else background_png())
        project_meta = dict(name=metadata.get('name', 'Untitled'), composer=metadata.get('composer', ''),
                            charter='TouchChart', illustrator='TouchChart', level='IN Lv.1')
        (output / 'meta.json').write_text(json.dumps(project_meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('chart', type=Path)
    parser.add_argument('audio', type=Path)
    parser.add_argument('-o', '--output', type=Path, default=Path('touchchart.pez'))
    parser.add_argument('--background', type=Path)
    parser.add_argument('--phichain', action='store_true', help='Create a native Phichain v0.5.0 project directory instead of .pez')
    args = parser.parse_args()
    if args.phichain and args.output == Path('touchchart.pez'):
        args.output = Path('touchchart-project')
    print((phichain_project if args.phichain else pack)(args.chart, args.audio, args.output, args.background))
