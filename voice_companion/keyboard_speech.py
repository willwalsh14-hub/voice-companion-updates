"""Dedicated local speech thread so keyboard feedback never waits for recognition."""
import queue
import threading
import time

class KeyboardSpeech:
    def __init__(self):
        self.narration_interrupt=threading.Event()
        self.queue=queue.Queue();self.settings=('',0,100,None);self.problem=None;self.active=False;self.quiet_until=0
        threading.Thread(target=self.run,name='Immediate keyboard speech',daemon=True).start()
    def configure(self,voice_id,rate,volume,espeak=None):self.settings=(voice_id,rate,volume,espeak)
    def speak(self,text):
        if text:self.narration_interrupt.set();self.active=True;self.queue.put(str(text))
    def busy(self):return self.active or time.monotonic()<self.quiet_until
    def run(self):
        import pythoncom
        from win32com.client import Dispatch
        pythoncom.CoInitialize()
        try:
            voice=Dispatch('SAPI.SpVoice');chosen=None
            while True:
                try:text=self.queue.get(timeout=.02)
                except queue.Empty:
                    self.active=not bool(voice.WaitUntilDone(0)) or bool(self.settings[3] and self.settings[3].busy())
                    if self.active:self.quiet_until=time.monotonic()+.15
                    continue
                if text is None:return
                voice_id,rate,volume,espeak=self.settings
                try:
                    if espeak and espeak.enabled:espeak.speak(text,rate,volume);continue
                    if voice_id and voice_id!=chosen:
                        voices=voice.GetVoices()
                        token=next((voices.Item(i) for i in range(voices.Count) if voices.Item(i).Id==voice_id),None)
                        if token:voice.Voice=token
                        chosen=voice_id
                    voice.Rate=rate;voice.Volume=volume;voice.Speak(text,3)
                except Exception as exc:self.problem=type(exc).__name__
        except Exception as exc:
            self.problem=type(exc).__name__;self.active=False
        finally:pythoncom.CoUninitialize()
    def close(self):self.queue.put(None)
