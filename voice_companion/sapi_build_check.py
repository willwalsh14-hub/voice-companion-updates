"""Validate Windows speech synthesis on build servers without speakers."""
import tempfile
import wave
from pathlib import Path


def check_file_speech():
    import win32com.client
    with tempfile.TemporaryDirectory(prefix='VoiceCompanion-SAPI-') as folder:
        target = Path(folder) / 'speech.wav'
        speaker = win32com.client.Dispatch('SAPI.SpVoice')
        stream = win32com.client.Dispatch('SAPI.SpFileStream')
        stream.Open(str(target), 3, False)  # SSFMCreateForWrite
        try:
            speaker.AudioOutputStream = stream
            speaker.Speak('Voice Companion Windows speech synthesis check.', 0)
            speaker.Pause()
            speaker.Resume()
            speaker.Speak('', 3)  # Async purge, as used by interruption.
            if not speaker.WaitUntilDone(5000):
                raise RuntimeError('Windows speech purge did not finish.')
            speaker.Speak('Speech control check completed.', 0)
        finally:
            stream.Close()
        with wave.open(str(target), 'rb') as audio:
            if audio.getnframes() == 0 or not any(audio.readframes(audio.getnframes())):
                raise RuntimeError('Windows speech produced no audio samples.')
    print('Windows speech generated audio; pause, resume, and purge calls completed. Speaker playback requires a local Windows check.', flush=True)
    return 0
