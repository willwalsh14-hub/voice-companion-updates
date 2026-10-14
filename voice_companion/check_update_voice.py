"""Exercise the built offline listener and its shutdown handshake on Windows."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

def check(executable):
    import win32com.client
    with tempfile.TemporaryDirectory(prefix='VC update voice check ') as directory:
        folder=Path(directory);wav=folder/'status.wav'
        voice=win32com.client.Dispatch('SAPI.SpVoice');stream=win32com.client.Dispatch('SAPI.SpFileStream')
        stream.Open(str(wav),3);voice.AudioOutputStream=stream
        voice.Speak('Status',0);stream.Close()
        result=subprocess.run([str(executable),'--folder',str(folder),'--recognize-file',str(wav)],timeout=60)
        if result.returncode:raise AssertionError('Packaged offline Status recognition failed: '+(folder/'voice-error.txt').read_text())
        (folder/'install-percent.txt').write_text('37')
        process=subprocess.Popen([str(executable),'--folder',str(folder),'--headless','--no-microphone','--speech-file',str(folder/'progress.wav')])
        try:
            deadline=time.monotonic()+30
            while not (folder/'voice-ready.json').exists() or not (folder/'speech-spoken').exists():
                if process.poll() is not None or time.monotonic()>deadline:raise AssertionError('Update voice readiness failed: '+repr({p.name:p.read_text(errors='replace') for p in folder.iterdir() if p.suffix=='.txt' or p.name in ('speech-ready','speech-spoken','voice-ready.json')}))
                time.sleep(.1)
            (folder/'install-percent.txt').write_text('100')
            (folder/'install-complete').write_text('complete')
            deadline=time.monotonic()+35
            while not (folder/'completion-spoken').exists():
                if process.poll() is not None or time.monotonic()>deadline:raise AssertionError('Completion speech failed: '+repr({p.name:p.read_text(errors='replace') for p in folder.glob('*.txt')}))
                time.sleep(.1)
            lines=(folder/'speech-log.txt').read_text().splitlines()
            if lines[:-1]!=[str(n)+' percent.' for n in range(5,101,5)]:raise AssertionError('Progress missed a five percent step: '+repr(lines))
            if lines[-1]!='Installation complete, restarting Voice Companion.':raise AssertionError('Completion message changed')
            (folder/'voice-stop').write_text('stop')
            if process.wait(timeout=15)!=0 or not (folder/'voice-closed').exists():raise AssertionError('Update microphone release handshake failed: '+((folder/'voice-error.txt').read_text() if (folder/'voice-error.txt').exists() else 'no closure signal'))
            if not (folder/'progress.wav').exists() or (folder/'progress.wav').stat().st_size<=44:raise AssertionError('Update progress speech produced no audio.')
            if (folder/'voice-speech-error.txt').exists():raise AssertionError((folder/'voice-speech-error.txt').read_text())
        finally:
            if process.poll() is None:process.kill();process.wait()
    print('Packaged offline Status recognition and microphone handoff passed.',flush=True)

if __name__=='__main__':check(Path(sys.argv[1]))
