"""Windows-user-protected AI voice configuration, separate from shared source."""
import json
import os
from pathlib import Path

VOICES = ('alloy','ash','ballad','coral','echo','fable','nova','onyx','sage','shimmer','verse','marin','cedar')


def load(folder):
    path = Path(folder) / 'ai-voice.account'
    if path.exists():
        import win32crypt
        data = json.loads(win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1])
        return data
    key = os.getenv('VOICE_COMPANION_OPENAI_KEY', '')
    return {'key':key, 'voice':os.getenv('VOICE_COMPANION_AI_VOICE', 'coral')}


def save(folder, key, voice, consent):
    key = key.strip()
    if not key: raise ValueError('Enter an API key.')
    if voice not in VOICES: raise ValueError('Choose a supported voice.')
    if not consent: raise ValueError('Confirm permission to send narration text to OpenAI.')
    import win32crypt
    sealed = win32crypt.CryptProtectData(
        json.dumps({'key':key, 'voice':voice}).encode(), 'Voice Companion AI voice', None, None, None, 0)
    path = Path(folder) / 'ai-voice.account'
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_bytes(sealed)
    temp.replace(path)


def remove(folder):
    (Path(folder) / 'ai-voice.account').unlink(missing_ok=True)
