"""Exercise the mobile browser with a real native app instance on a temporary port."""
from pathlib import Path
import subprocess
import tkinter as tk
import time
from desktop import Desktop

root=Path(__file__).resolve().parent
window=tk.Tk();window.withdraw();app=Desktop(window,0)
try:
    with (root/'artifacts/native-mobile-host-output.txt').open('w',encoding='utf-8') as log:
        child=subprocess.Popen(['node',str(root/'browser_mobile_test.mjs'),f'http://127.0.0.1:{app.server.server_port}',str(root/'artifacts')],
                               stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+90
        while child.poll() is None:
            window.update();time.sleep(.01)
            if time.monotonic()>deadline:
                child.kill();child.wait();raise TimeoutError('Browser acceptance exceeded 90 seconds')
    assert child.returncode==0,(root/'artifacts/native-mobile-host-output.txt').read_text(encoding='utf-8')
    app.draw(0)
    assert len(app.recorder.notes)==3 and app.note_list.size()==3
    print((root/'artifacts/native-mobile-browser-output.txt').read_text(encoding='utf-8'))
    print('PASS received notes appear in the native desktop note list')
finally:app.close(True)
