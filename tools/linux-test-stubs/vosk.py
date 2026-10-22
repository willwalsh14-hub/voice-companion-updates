"""Linux test stand-in for the Windows speech recognizer package (vosk).

Only lets modules import; recognition always returns empty text.
Never ship this file or put it on the app's path outside tests.
"""
class Model:
    def __init__(self, *args, **kwargs): pass


class KaldiRecognizer:
    def __init__(self, *args, **kwargs): pass
    def SetWords(self, *args): pass
    def AcceptWaveform(self, *args): return False
    def Result(self): return '{"text":""}'
    def PartialResult(self): return '{"partial":""}'
    def FinalResult(self): return '{"text":""}'
    def Reset(self): pass


def SetLogLevel(*args): pass
