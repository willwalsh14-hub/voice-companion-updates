"""Dedicated local speech thread so keyboard feedback never waits for recognition."""
import queue
import threading
import time

class KeyboardSpeech:
    def __init__(self):
        self.narration_interrupt=threading.Event()
        self.queue=queue.Queue();self.settings=('',0,100,None,0);self.problem=None;self.active=False;self.paused=False;self.quiet_until=0
        threading.Thread(target=self.run,name='Immediate keyboard speech',daemon=True).start()
    def configure(self,voice_id,rate,volume,espeak=None,pitch=0):self.settings=(voice_id,rate,volume,espeak,pitch)
    def speak(self,text):
        if text:
            self.interrupt()
            self.narration_interrupt.set();self.active=True;self.queue.put(str(text))
    def busy(self):return (self.active and not getattr(self,'paused',False)) or time.monotonic()<self.quiet_until
    def control(self,action):self.queue.put((action,))
    def interrupt(self,notify=True):
        if notify and hasattr(self,'narration_interrupt'):self.narration_interrupt.set()
        while True:
            try:self.queue.get_nowait()
            except queue.Empty:break
        self.control('interrupt')
    def run(self):
        import pythoncom
        from win32com.client import Dispatch
        pythoncom.CoInitialize()
        try:
            voice=Dispatch('SAPI.SpVoice');chosen=None
            while True:
                try:text=self.queue.get(timeout=.02)
                except queue.Empty:
                    self.active=self.active and (not bool(voice.WaitUntilDone(0)) or bool(self.settings[3] and self.settings[3].enabled and self.settings[3].busy()))
                    if self.active and not getattr(self,'paused',False):self.quiet_until=time.monotonic()+.15
                    continue
                if text is None:return
                voice_id,rate,volume,espeak=self.settings[:4];pitch=self.settings[4] if len(self.settings)>4 else 0
                try:
                    if isinstance(text,tuple):
                        action=text[0]
                        if action=='pause':
                            if not getattr(self,'paused',False):voice.Pause()
                            self.paused=True
                            if espeak:espeak.pause()
                        elif action in ('resume','interrupt'):
                            if getattr(self,'paused',False):voice.Resume()
                            self.paused=False
                            if action=='interrupt':
                                voice.Speak('',3);self.active=False;self.quiet_until=time.monotonic()+.15
                                if espeak:espeak.interrupt()
                            elif espeak:espeak.resume()
                        continue
                    if getattr(self,'paused',False):voice.Resume();self.paused=False
                    self.active=True
                    if espeak and espeak.enabled:espeak.interrupt();espeak.speak(text,rate,volume);continue
                    if voice_id and voice_id!=chosen:
                        voices=voice.GetVoices()
                        token=next((voices.Item(i) for i in range(voices.Count) if voices.Item(i).Id==voice_id),None)
                        if token:voice.Voice=token
                        chosen=voice_id
                    voice.Rate=rate;voice.Volume=volume
                    from voice_pitch import sapi_speak
                    sapi_speak(voice,text,pitch)
                except Exception as exc:self.problem=type(exc).__name__
        except Exception as exc:
            self.problem=type(exc).__name__;self.active=False
        finally:pythoncom.CoUninitialize()
    def close(self):self.queue.put(None)
