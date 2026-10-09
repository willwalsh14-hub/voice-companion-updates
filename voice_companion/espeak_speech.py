"""Local eSpeak NG audio generation; shared player preserves pause and resume."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave
from audio_resample import PCM16Resampler

# Names and identifiers from the bundled, unmodified eSpeak NG 1.52.0 variants.
VARIANTS = (('Adam', 'adam'), ('Alex', 'Alex'), ('Alicia', 'Alicia'), ('Andrea', 'Andrea'), ('Andy', 'Andy'), ('anika', 'anika'), ('anikaRobot', 'anikaRobot'), ('Annie', 'Annie'), ('Half-LifeAnnouncementSystem', 'announcer'), ('Antonio', 'antonio'), ('anxiousAndy', 'AnxiousAndy'), ('Auntie', 'aunty'), ('Belinda', 'belinda'), ('Benjamin', 'benjamin'), ('Boris', 'boris'), ('Caleb', 'caleb'), ('croak', 'croak'), ('David', 'david'), ('Demonic', 'Demonic'), ('Denis', 'Denis'), ('Diogo', 'Diogo'), ('Ed', 'ed'), ('Edward', 'edward'), ('Edward2', 'edward2'), ('female1', 'f1'), ('female2', 'f2'), ('female3', 'f3'), ('female4', 'f4'), ('female5', 'f5'), ('fast test', 'fast'), ('Gene', 'Gene'), ('Gene2', 'Gene2'), ('grandma', 'grandma'), ('grandpa', 'grandpa'), ('Gustave', 'gustave'), ('Henrique', 'Henrique'), ('Hugo', 'Hugo'), ('Iven', 'iven'), ('Iven2', 'iven2'), ('Iven3', 'iven3'), ('Iven4', 'iven4'), ('Jacky', 'Jacky'), ('John', 'john'), ('Kaukovalta', 'kaukovalta'), ('klatt', 'klatt'), ('klatt2', 'klatt2'), ('klatt3', 'klatt3'), ('klatt4', 'klatt4'), ('klatt5', 'klatt5'), ('klatt6', 'klatt6'), ('Lee', 'Lee'), ('Linda', 'linda'), ('male1', 'm1'), ('male2', 'm2'), ('male3', 'm3'), ('male4', 'm4'), ('male5', 'm5'), ('male6', 'm6'), ('male7', 'm7'), ('male8', 'm8'), ('Marcelo', 'marcelo'), ('Marco', 'Marco'), ('Mario', 'Mario'), ('Max', 'max'), ('Michael', 'Michael'), ('Michel', 'michel'), ('Miguel', 'miguel'), ('Mike', 'Mike'), ('Mr Serious', 'Mr serious'), ('Nguyen', 'Nguyen'), ('norbert', 'norbert'), ('Pablo', 'pablo'), ('Paul', 'paul'), ('Pedro', 'pedro'), ('Quincy', 'quincy'), ('RicishayMax', 'RicishayMax'), ('RicishayMax2', 'RicishayMax2'), ('RicishayMax3', 'RicishayMax3'), ('Rob', 'rob'), ('Robert', 'robert'), ('Robosoft', 'robosoft'), ('Robosoft2', 'robosoft2'), ('Robosoft3', 'robosoft3'), ('Robosoft4', 'robosoft4'), ('Robosoft5', 'robosoft5'), ('Robosoft6', 'robosoft6'), ('Robosoft7', 'robosoft7'), ('Robosoft8', 'robosoft8'), ('sandro', 'sandro'), ('shelby', 'shelby'), ('Steph', 'steph'), ('Steph2', 'steph2'), ('Steph3', 'steph3'), ('Storm', 'Storm'), ('travis', 'travis'), ('Tweaky', 'Tweaky'), ('UniversalRobot', 'UniRobot'), ('victor', 'victor'), ('whisper', 'whisper'), ('female whisper', 'whisperf'), ('Zac', 'zac'))
ALL_VOICES = (('US English', 'en-us'), ('UK English', 'en-gb')) + tuple(
    (language + ' ' + name, code + '+' + variant)
    for language, code in (('US English', 'en-us'), ('UK English', 'en-gb'))
    for name, variant in VARIANTS)
# Keep the public picker small. Existing saved variants remain valid internally.
VOICES = (('US English', 'en-us'), ('UK English', 'en-gb'),
          ('US English Male', 'en-us+m1'), ('UK English Male', 'en-gb+m1'),
          ('US English Female', 'en-us+f1'), ('UK English Female', 'en-gb+f1'))


def executable():
    app = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    candidates = [app / 'espeak' / 'espeak-ng.exe']
    installed = Path(os.getenv('LOCALAPPDATA', '')) / 'Programs' / 'Voice Companion' / 'espeak' / 'espeak-ng.exe'
    candidates.append(installed)
    for base in ('ProgramFiles', 'ProgramFiles(x86)'):
        if os.getenv(base): candidates.append(Path(os.environ[base]) / 'eSpeak NG' / 'espeak-ng.exe')
    for path in candidates:
        if path.is_file(): return path
    found = shutil.which('espeak-ng')
    return Path(found) if found else None

def synthesize(text, rate=0, voice='en-us', program=None):
    if voice not in {code for _, code in ALL_VOICES}: raise ValueError('Unsupported eSpeak voice')
    program = program or executable()
    if not program: raise OSError('eSpeak NG is not installed')
    program = Path(program).resolve()
    speed = max(80, min(450, round(175 * (1 + rate * .075))))
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    result = subprocess.run([str(program), '--path=' + str(program.parent), '--stdout', '-v', voice, '-s', str(speed), '--stdin'],
                            input=text.encode('utf-8'), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=8, check=True, **options)
    with wave.open(io.BytesIO(result.stdout), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError('Invalid eSpeak audio')
        pcm = wav.readframes(wav.getnframes())
        if not pcm: raise ValueError('Empty eSpeak audio')
        return PCM16Resampler(wav.getframerate(), 24000).convert(pcm)
