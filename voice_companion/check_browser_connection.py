"""Exercise the installed native host and real Windows named-pipe transport."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from browser_bridge_host import read_message, write_message
from regular_browser import BrowserConnection


def check(executable):
    with tempfile.TemporaryDirectory() as folder:
        server = BrowserConnection(folder)
        environment = dict(os.environ, VOICE_COMPANION_DATA_DIR=folder)
        host = subprocess.Popen([str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        result = []
        try:
            write_message(host.stdin, {'protocol':1,'command':'poll','connected':True})
            assert read_message(host.stdout) == {}, 'Installed host failed its initial pipe connection.'
            worker = threading.Thread(target=lambda: result.append(server.request('space',timeout=10)))
            worker.start()
            deadline = time.monotonic()+2
            while server.requests.empty() and time.monotonic()<deadline: time.sleep(.01)
            write_message(host.stdin, {'protocol':1,'command':'poll','connected':True})
            assert read_message(host.stdout) == {'action':'space'}, 'Installed host lost or changed the requested action.'
            write_message(host.stdin, {'protocol':1,'command':'poll','connected':True,'result':'Delivery, radio button, checked.'})
            assert read_message(host.stdout) == {}
            worker.join(10)
            assert result == ['Delivery, radio button, checked.'], 'Checked-state feedback did not return to Companion.'
            host.stdin.close()
            assert host.wait(timeout=10) == 0, host.stderr.read().decode(errors='replace')
            print('Installed native host and authenticated Windows pipe checks passed.')
        finally:
            if host.poll() is None: host.kill();host.wait()
            server.listener.close()

if __name__=='__main__':check(Path(sys.argv[1]))
