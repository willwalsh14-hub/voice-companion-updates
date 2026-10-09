"""Optional Azure recognition fed by the app's existing microphone stream.

No credentials are bundled. Set VOICE_COMPANION_AZURE_KEY and
VOICE_COMPANION_AZURE_REGION in the Windows environment at setup time.
"""
import os
import queue


class CloudRecognition:
    def __init__(self):
        import azure.cognitiveservices.speech as speechsdk
        self.sdk = speechsdk
        self.events = queue.Queue()
        self.active = False
        key = os.getenv('VOICE_COMPANION_AZURE_KEY')
        region = os.getenv('VOICE_COMPANION_AZURE_REGION')
        if not key or not region:
            raise ValueError('Cloud speech account is not configured')
        self.config = speechsdk.SpeechConfig(subscription=key, region=region)
        self.config.speech_recognition_language = 'en-US'
        self.stream = None
        self.recognizer = None

    def start(self):
        sdk = self.sdk
        fmt = sdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        self.stream = sdk.audio.PushAudioInputStream(stream_format=fmt)
        audio = sdk.audio.AudioConfig(stream=self.stream)
        self.recognizer = sdk.SpeechRecognizer(speech_config=self.config, audio_config=audio)
        self.recognizer.recognized.connect(self._recognized)
        self.recognizer.canceled.connect(lambda event: self.events.put(('error', str(event.reason))))
        self.recognizer.start_continuous_recognition_async().get()
        self.active = True

    def _recognized(self, event):
        if event.result.reason == self.sdk.ResultReason.RecognizedSpeech and event.result.text.strip():
            self.events.put(('text', event.result.text.strip()))

    def write(self, audio):
        if self.active and self.stream:
            self.stream.write(audio)

    def stop(self):
        self.active = False
        if self.recognizer:
            self.recognizer.stop_continuous_recognition_async().get()
        if self.stream:
            self.stream.close()
        self.recognizer = None
        self.stream = None
