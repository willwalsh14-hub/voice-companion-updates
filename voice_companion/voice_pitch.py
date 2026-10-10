"""Escape narration before adding native SAPI pitch markup."""
from xml.sax.saxutils import escape

def sapi_speak(voice,text,pitch=0,flags=3):
    if pitch:
        voice.Speak('<pitch absmiddle="'+str(max(-10,min(10,int(pitch))))+'">'+escape(str(text))+'</pitch>',flags|8)
    else:voice.Speak(str(text),flags)
