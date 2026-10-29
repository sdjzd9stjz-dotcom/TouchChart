"""Test the APK's actual Wire transport on the JVM against the recording service."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'touchchart'))
import touchchart
from desktop import NativeHandler

jdk=next((ROOT/'.tools/ready/jdk').glob('*/bin/javac.exe')).parent
server=touchchart.Server(('127.0.0.1',0))
server.RequestHandlerClass=NativeHandler
worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
try:
    with tempfile.TemporaryDirectory(prefix='touchchart-java-test-') as folder:
        compiled=subprocess.run([str(jdk/'javac.exe'),'-encoding','UTF-8','--release','8','-d',folder,str(ROOT/'android/src/org/touchchart/remote/Wire.java'),str(ROOT/'android/WireTest.java')],capture_output=True)
        assert compiled.returncode==0,compiled.stderr
        result=subprocess.run([str(jdk/'java.exe'),'-cp',folder,'WireTest',f'http://127.0.0.1:{server.server_port}'],capture_output=True)
        (ROOT/'artifacts/android-transport-selftest.txt').write_bytes(result.stdout+result.stderr)
        print(result.stdout.decode('utf-8',errors='replace'))
        assert result.returncode==0 and len(server.recorder.notes)==5
finally:server.shutdown();server.server_close();worker.join()
