"""Independent, staged offline microphone and keyboard status during Setup."""
import argparse
import json
import queue
import sys
import time
import threading
from pathlib import Path
from audio_resample import PCM16Resampler, stereo_to_mono


def progress(folder):
    try:return max(0,min(100,int((Path(folder)/'install-percent.txt').read_text().strip())))
    except (OSError,ValueError):return 0


def status_request(text):
    return text.strip().lower().rstrip('.!?') in ('status','update status')

class ProgressSpeech:
    """Native speech must never hold the microphone or stop request hostage."""
    def __init__(self,folder):
        self.folder=Path(folder);self.messages=queue.Queue(maxsize=1);self.stopped=threading.Event()
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def say(self,percent):
        try:self.messages.get_nowait()
        except queue.Empty:pass
        try:self.messages.put_nowait(percent)
        except queue.Full:pass
    def _run(self):
        import pythoncom
        pythoncom.CoInitialize()
        voice=None
        try:
            import win32com.client
            voice=win32com.client.Dispatch('SAPI.SpVoice');voice.Volume=100
            (self.folder/'speech-ready').write_text('ready')
            while not self.stopped.wait(.02):
                pythoncom.PumpWaitingMessages()
                try:percent=self.messages.get_nowait()
                except queue.Empty:continue
                voice.Speak('Installing the update. '+str(percent)+' percent.',3)
                (self.folder/'speech-spoken').write_text(str(percent))
            voice.Speak('',3)
            voice.WaitUntilDone(500)
        except Exception as exc:
            (self.folder/'voice-speech-error.txt').write_text(str(exc))
        finally:
            voice=None
            pythoncom.CoUninitialize()
    def close(self):
        self.stopped.set();self.thread.join(timeout=2)

def recognize_file(path):
    import wave
    import sounddevice as sd
    sd.query_devices()  # Load the staged native input library even on a runner without a microphone.
    from vosk import Model,SetLogLevel
    SetLogLevel(-1)
    listener=Microphone(Model(str(Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'model')))
    with wave.open(str(path),'rb') as source:
        if source.getsampwidth()!=2:raise ValueError('Status test needs 16-bit audio.')
        pcm=source.readframes(source.getnframes())
        if source.getnchannels()==2:pcm=stereo_to_mono(pcm)
        listener.recognizer.AcceptWaveform(PCM16Resampler(source.getframerate()).convert(pcm))
    text=json.loads(listener.recognizer.FinalResult()).get('text','')
    if not status_request(text):raise AssertionError('Status was not heard: '+text)
    return 0


class Microphone:
    def __init__(self,model):
        from vosk import KaldiRecognizer
        self.recognizer=KaldiRecognizer(model,16000,'["status", "update status", "[unk]"]')
        self.audio=queue.Queue(maxsize=100)
        self.stream=None

    def open(self):
        import sounddevice as sd
        self.close()
        rate=int(round(sd.query_devices(kind='input')['default_samplerate']))
        for channels in (1,2):
            try:
                sd.check_input_settings(channels=channels,dtype='int16',samplerate=rate)
                converter=PCM16Resampler(rate)
                def capture(data,*args):
                    pcm=stereo_to_mono(bytes(data)) if channels==2 else bytes(data)
                    try:self.audio.put_nowait(converter.convert(pcm))
                    except queue.Full:pass
                self.stream=sd.RawInputStream(samplerate=rate,blocksize=max(1,rate//20),dtype='int16',channels=channels,callback=capture)
                self.stream.start()
                self.recognizer.Reset()
                return
            except Exception:
                self.close()
        raise OSError('The update microphone could not open.')

    def close(self):
        stream=self.stream;self.stream=None
        if stream:
            try:stream.stop()
            except Exception:pass
            try:stream.close()
            except Exception:pass

    def requested(self):
        requested=False
        for _ in range(100):
            try:pcm=self.audio.get_nowait()
            except queue.Empty:break
            if self.recognizer.AcceptWaveform(pcm):
                requested=status_request(json.loads(self.recognizer.Result()).get('text','')) or requested
        return requested


def run(folder,headless=False,no_microphone=False):
    from vosk import Model, SetLogLevel
    folder=Path(folder);SetLogLevel(-1)
    speech=ProgressSpeech(folder)
    microphone=None;window=None;requested=queue.Queue()
    label=None
    if not headless:
        import tkinter as tk
        window=tk.Tk();window.title('Voice Companion update')
        label=tk.StringVar(value='Preparing installation.')
        tk.Label(window,textvariable=label,font=('Arial',14)).pack(padx=24,pady=16)
        button=tk.Button(window,text='Status',command=lambda:requested.put(True));button.pack(pady=12);button.focus_set()
        window.bind('<Return>',lambda event:requested.put(True));window.bind('<Alt-s>',lambda event:requested.put(True))
        window.protocol('WM_DELETE_WINDOW',lambda:window.iconify())
    try:
        if not no_microphone:
            microphone=Microphone(Model(str(Path(getattr(sys,'_MEIPASS',Path(__file__).parent))/'model')))
            try:microphone.open()
            except OSError:pass
        (folder/'voice-ready.json').write_text(json.dumps({'ready':True,'microphone':bool(microphone and microphone.stream)}))
        announced=-5;retry_at=0
        while not (folder/'voice-stop').exists():
            if window:window.update()
            percent=progress(folder)
            if label:label.set('Installing the update. '+str(percent)+' percent.')
            if microphone and (not microphone.stream or not microphone.stream.active) and time.monotonic()>=retry_at:
                retry_at=time.monotonic()+1
                try:microphone.open()
                except OSError:pass
            repeat=bool(microphone and microphone.stream and microphone.requested())
            while not requested.empty():requested.get_nowait();repeat=True
            if repeat or percent>=announced+5:
                speech.say(percent);announced=percent
            time.sleep(.02)
    finally:
        if microphone:microphone.close()
        if window:window.destroy()
        speech.close()
        (folder/'voice-closed').write_text('closed')
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--folder',required=True)
    parser.add_argument('--headless',action='store_true');parser.add_argument('--no-microphone',action='store_true')
    parser.add_argument('--recognize-file')
    args=parser.parse_args()
    # All input and window resources are released above. Native COM finalizers
    # can otherwise delay this isolated helper's exit after async speech.
    import os
    try:
        code=recognize_file(args.recognize_file) if args.recognize_file else run(args.folder,args.headless,args.no_microphone)
        os._exit(code)
    except Exception as exc:
        Path(args.folder,'voice-error.txt').write_text(str(exc));os._exit(1)
