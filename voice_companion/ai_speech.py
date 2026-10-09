"""Optional online narrator. No credentials or spoken content are saved to disk."""
import json
import os
import queue
import threading
import time
import urllib.request


def synthesize(text, rate=0, key=None, voice=None):
    key = key if key is not None else os.getenv('VOICE_COMPANION_OPENAI_KEY', '')
    if not key:
        raise ValueError('AI speech is not configured')
    body = json.dumps({'model': 'gpt-4o-mini-tts',
        'voice': voice or os.getenv('VOICE_COMPANION_AI_VOICE', 'coral'),
        'input': text, 'response_format': 'pcm',
        'speed': max(0.25, min(4.0, 1.0 + rate * 0.075))}).encode()
    request = urllib.request.Request('https://api.openai.com/v1/audio/speech',
        data=body, headers={'Authorization': 'Bearer ' + key,
                           'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=8) as response:
        audio = response.read(16 * 1024 * 1024)
    if not audio or len(audio) % 2:
        raise ValueError('Invalid speech audio')
    return audio


class AISpeech:
    def __init__(self, fallback_factory, synthesizer=synthesize, output_factory=None, engine_name="AI voice", error_callback=None):
        self.engine_name = engine_name
        self.error_callback = error_callback
        self.fallback_factory = fallback_factory
        self.synthesizer = synthesizer
        self.output_factory = output_factory
        self.pending = queue.Queue()
        self.lock = threading.RLock()
        self.generation = 0
        self.paused = False
        self.active = False
        self.enabled = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def speak(self, text, rate=0, volume=100):
        with self.lock:
            self.pending.put((self.generation, text, rate, volume))

    def interrupt(self):
        with self.lock:
            self.generation += 1
            self.paused = False
            while not self.pending.empty():
                try: self.pending.get_nowait()
                except queue.Empty: break

    def close(self):
        self.interrupt()
        self.pending.put(None)

    def pause(self):
        with self.lock: self.paused = True

    def resume(self):
        with self.lock: self.paused = False

    def busy(self):
        with self.lock: return self.active or not self.pending.empty()

    def _wait(self, generation):
        while True:
            with self.lock:
                if generation != self.generation: return False
                if not self.paused: return True
            time.sleep(0.02)

    def _play(self, audio, generation, volume):
        import array
        if self.output_factory is None:
            import sounddevice
            factory = sounddevice.RawOutputStream
        else: factory = self.output_factory
        samples = array.array('h', audio)
        if volume != 100:
            samples = array.array('h', (int(v * volume / 100) for v in samples))
        audio = samples.tobytes()
        with factory(samplerate=24000, channels=1, dtype='int16', blocksize=960) as stream:
            for start in range(0, len(audio), 1920):
                if not self._wait(generation):
                    stream.abort()
                    return
                stream.write(audio[start:start + 1920])

    def _run(self):
        fallback = None
        while True:
            job = self.pending.get()
            if job is None: return
            generation, text, rate, volume = job
            with self.lock: self.active = True
            try:
                # Small requests let silence commands take effect between audio blocks.
                for start in range(0, len(text), 1800):
                    part = text[start:start + 1800]
                    if not self._wait(generation): break
                    try:
                        audio = self.synthesizer(part, rate)
                        if self._wait(generation): self._play(audio, generation, volume)
                    except Exception as exc:
                        if self.error_callback is not None:
                            try: self.error_callback(exc)
                            except Exception: pass
                        if not self._wait(generation): break
                        if fallback is None: fallback = self.fallback_factory()
                        fallback.Rate, fallback.Volume = rate, volume
                        fallback.Speak(self.engine_name + ' unavailable. Using Windows speech. ' + part, 1)
                        was_paused = False
                        while not fallback.WaitUntilDone(0):
                            with self.lock:
                                if generation != self.generation:
                                    if was_paused: fallback.Resume()
                                    fallback.Speak('', 3)
                                    break
                                paused = self.paused
                            if paused != was_paused:
                                (fallback.Pause if paused else fallback.Resume)()
                                was_paused = paused
                            time.sleep(0.02)
            except Exception:
                # Keep the worker alive if the audio device or fallback fails.
                pass
            finally:
                with self.lock: self.active = False
