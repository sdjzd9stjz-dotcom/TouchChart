"""Build a portable Windows application with Python, Tk and audio decoder included."""
import json
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parent
WEB=ROOT.parent/'touchchart'
parser=argparse.ArgumentParser()
parser.add_argument('--output-root',type=Path,default=ROOT/'dist')
DIST=parser.parse_args().output_root.resolve()
(ROOT/'artifacts').mkdir(exist_ok=True)
(ROOT/'.build').mkdir(exist_ok=True)
args=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir','--name','TouchChart',
      '--distpath',str(DIST),'--workpath',str(ROOT/'.build/desktop'),'--specpath',str(ROOT/'.build'),
      '--paths',str(WEB),'--paths',str(ROOT),'--hidden-import','native_selftest',
      '--add-binary',str(WEB/'.tools/ffmpeg.exe')+';.',
      '--add-data',str(WEB/'artifacts/review/variable-tempo-120-180-90.wav')+';test-fixtures',str(ROOT/'desktop.py')]
with (ROOT/'artifacts/windows-build.txt').open('w',encoding='utf-8') as log:
    result=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT)
if result.returncode:raise SystemExit(result.returncode)
target=DIST/'TouchChart'
runtime=Path(sys.base_prefix)
for source,name in [(runtime/'LICENSE.txt','Python-LICENSE.txt'),(runtime/'tcl/tk8.6/license.terms','Tk-LICENSE.txt')]:
    if source.exists():shutil.copy2(source,target/name)
with zipfile.ZipFile(WEB/'.tools/ffmpeg-essentials.zip') as z:
    for name in z.namelist():
        if name.endswith('/LICENSE'):(target/'FFmpeg-LICENSE.txt').write_bytes(z.read(name))
        elif name.endswith('/README.txt'):(target/'FFmpeg-README.txt').write_bytes(z.read(name))
(target/'THIRD-PARTY.txt').write_text('Python and Tcl/Tk are bundled by PyInstaller. See _internal for runtime licenses.\nFFmpeg is an external executable from https://www.gyan.dev/ffmpeg/builds/ ; see FFmpeg-LICENSE.txt and FFmpeg-README.txt for licensing and source information.\nTouchChart itself does not require a system Python installation.\n',encoding='utf-8')
for name in ['README.md','DESKTOP_GUIDE.md']:
    if (ROOT/name).exists():shutil.copy2(ROOT/name,target/name)
print(json.dumps({'executable':str(target/'TouchChart.exe'),'buildExitCode':result.returncode}))
