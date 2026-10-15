"""Capital cues for individual letters, without changing displayed text."""
class CharacterSpeech(str):
    def __new__(cls,text,capital=False,pitch_delta=0,sound=False):
        obj=super().__new__(cls,text);obj.capital=capital;obj.pitch_delta=pitch_delta;obj.sound=sound;return obj

def is_capital(text):
    return bool(getattr(text,'capital',False) or len(str(text))==1 and str(text).isalpha() and str(text).isupper())

def preserve_character(original,text):
    return CharacterSpeech(text,True,getattr(original,'pitch_delta',0),getattr(original,'sound',False)) if is_capital(original) else text

def prepare(text,mode='do nothing'):
    mode={'with a higher pitch':'raise pitch','with a lower pitch':'lower pitch','with a sound':'play a sound'}.get(mode,mode)
    capital=is_capital(text)
    delta=(4 if mode=='raise pitch' else -4 if mode=='lower pitch' else 0) if capital else 0
    return CharacterSpeech(str(text),capital,delta,capital and mode=='play a sound')

def pitch_for(text,base=0):return max(-10,min(10,int(base)+getattr(text,'pitch_delta',0)))

def play_sound(text):
    if getattr(text,'sound',False):
        import sys
        if sys.platform=='win32':
            import winsound
            winsound.Beep(1100,45)
