"""Shared settings schema and staged edits for voice and native keyboard controls."""
from dataclasses import dataclass
import copy
import re

@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = 'choice'
    choices: tuple = ()

CATEGORIES = ('Verbosity', 'Speech', 'Synthesizer', 'Input', 'Email', 'Documents', 'Web browsing', 'Radio', 'Podcasts', 'Updates', 'Startup options')
DEFAULTS = {'verbosity':'high','typing_echo':'characters','phonetic_enabled':True,'phonetic_delay':'0.5','duck_audio':True,'check_updates':True,'start_with_windows':False,'document_font':'Calibri','document_size':'11','document_spacing':'single','document_alignment':'left'}


def fields(category, context):
    definitions = {
        'Verbosity':[Field('verbosity','Verbosity','choice',('high','medium','low')),Field('punctuation','Spoken punctuation','choice',('none','some','most','all')),Field('typing_echo','Typing echo','choice',('words','characters','characters and words','none')),Field('phonetic_enabled','Delayed phonetic pronunciation','check'),Field('phonetic_delay','Phonetic delay in seconds','choice',('0.5','1','2'))],
        'Speech':[Field('rate','Speech rate','choice',tuple(str(x) for x in range(-10,11))),Field('volume','Speech volume','choice',tuple(str(x) for x in range(101))),Field('duck_audio','Lower media volume while Companion speaks','check')],
        'Synthesizer':[Field('engine','Synthesizer','choice',tuple(context.get('engines',('windows',)))),Field('windows_voice','Windows voice','choice',tuple(context.get('windows_voices',()))),Field('espeak_voice','eSpeak voice','choice',tuple(context.get('espeak_voices',()))),Field('ai_voice','AI voice','choice',tuple(context.get('ai_voices',()))),Field('api_key','AI API key; blank keeps the saved key','password'),Field('ai_consent','Allow narration text to be sent to OpenAI','check'),Field('remove_ai','Remove saved AI key','check')],
        'Input':[Field('input_mode','Command and dictation mode','choice',('mixed','commands','dictation'))],
        'Email':[Field('email_account','Sending account','choice',tuple(context.get('accounts',()))),Field('email_list_size','Messages per list','choice',('10','20','30','40','50','100','1000','all')),Field('add_account','Add an email account','action')],
        'Documents':[Field('document_font','Font for new documents','text'),Field('document_size','Font size for new documents','choice',tuple(str(x) for x in range(6,73))),Field('document_spacing','Line spacing for new documents','choice',('single','one and a half','double')),Field('document_alignment','Alignment for new documents','choice',('left','center','right','justify')),Field('open_documents','Open Documents folder','action')],
        'Web browsing':[Field('browser','Guided browser','choice',('edge','chrome','brave','firefox')),Field('web_favorites','List website favorites','action')],
        'Radio':[Field('radio_source','Station database','choice',('all','radio browser','iprd','radio sure')),Field('radio_presets','List radio presets','action'),Field('radio_recordings','List scheduled recordings','action')],
        'Podcasts':[Field('podcast_feed','Podcast subscription','choice',tuple(context.get('podcasts',()))),Field('podcast_limit','Automatic downloads for this subscription','combo',('manual','all','1','2','3','5','10','20','50','100','1000')),Field('podcast_subscriptions','List subscriptions','action')],
        'Startup options':[Field('start_with_windows','Start with Windows','check')],
        'Updates':[Field('check_updates','Check automatically at startup','check'),Field('check_now','Check for updates now','action')],
    }
    return definitions[category]

class SettingsSession:
    def __init__(self, values, context):
        self.original = copy.deepcopy(values)
        self.values = copy.deepcopy(values)
        self.context = context
        self.values.setdefault('podcast_limits',copy.deepcopy(context.get('podcast_limits',{})))
        self.category = 0
        self.closed = False
    def category_name(self): return CATEGORIES[self.category]
    def current_fields(self): return fields(self.category_name(),self.context)
    def set(self,key,value):
        if key=='phonetic_delay':
            value=str(value).lower().replace(' seconds','').replace(' second','').strip()
            value={'half':'0.5','half a':'0.5','point five':'0.5','one':'1','two':'2','.5':'0.5'}.get(value,value)
        field=next((f for category in CATEGORIES for f in fields(category,self.context) if f.key==key),None)
        if field is None or field.kind=='action': raise ValueError('Unknown setting.')
        if field.kind=='check':
            if isinstance(value,str):
                if value.lower() not in ('on','off','yes','no','true','false'): raise ValueError('Say on or off.')
                value=value.lower() in ('on','yes','true')
            value=bool(value)
        else:
            value=str(value).strip()
            if field.kind=='choice':
                found=next((c for c in field.choices if c.casefold()==value.casefold()),None)
                if found is None: raise ValueError('Choose one of the available '+field.label+' options.')
                value=found
            if key=='podcast_limit' and value not in ('manual','all') and not (value.isdigit() and 1<=int(value)<=10000): raise ValueError('Choose manual, all, or a number from 1 to 10000.')
            if key=='document_font' and (not value or len(value)>100): raise ValueError('Enter a font name.')
        self.values[key]=value
        if key=='podcast_feed': self.values['podcast_limit']=str(self.values.get('podcast_limits',{}).get(value,'manual'))
        if key=='podcast_limit' and self.values.get('podcast_feed'):self.values['podcast_limits'][self.values['podcast_feed']]=value
        return field.label + (' hidden' if field.kind=='password' else ' '+('on' if value is True else 'off' if value is False else value))
    def voice_setting(self,command):
        command=command.strip().rstrip('.!?')
        aliases={'windows startup':'start with windows','start automatically with windows':'start with windows','phonetic pronunciation':'phonetic enabled','phonetics':'phonetic enabled','audio ducking':'duck audio','automatic updates':'check updates','default font size':'document size','default font':'document font','default line spacing':'document spacing','default alignment':'document alignment'}
        for alias,name in aliases.items():command=re.sub(r'^(?:set )?'+re.escape(alias)+r'\b',name,command,flags=re.I)
        engine=re.fullmatch(r'(?:use|select|choose) (?:synthesizer )?(windows|windows speech|microsoft|espeak|espeak ng|ai voice)',command,re.I)
        if engine:return 'engine',{'windows speech':'windows','microsoft':'windows','espeak ng':'espeak','ai voice':'ai'}.get(engine[1].lower(),engine[1].lower())
        from speech_controls import request
        speech=request(command.lower())
        if speech and speech[2] is not None:
            key,operation,amount=speech
            value=int(self.values.get(key,0))
            value=amount if operation=='set' else value+amount
            return key,str(max(-10 if key=='rate' else 0,min(10 if key=='rate' else 100,value)))
        download=re.fullmatch(r'(?:auto download|automatically download) (manual|all|\d+)',command,re.I)
        if download:return 'podcast_limit',download[1].lower()
        match=re.fullmatch(r'(?:set )?(?:verbosity|punctuation)(?: to)? (.+)',command,re.I)
        if match: return ('verbosity' if 'verbosity' in command.lower() else 'punctuation',match[1].lower())
        for category in CATEGORIES:
            for field in fields(category,self.context):
                if field.kind=='action': continue
                for label in (field.label,field.key.replace('_',' ')):
                    match=re.fullmatch(r'(?:(?:set|use|choose|select) )?'+re.escape(label)+r'(?: to)? (.+)',command,re.I)
                    if match:return field.key,match[1]
        return None
    def cancel(self): self.closed=True; self.values=copy.deepcopy(self.original)


def prompt_text(text,verbosity):
    if verbosity=='high':return text
    if text.startswith('Main menu.'):
        return 'Main menu.' if verbosity=='low' else 'Main menu. What would you like to do? Say next or previous, and OK to open.'
    # Only explicitly marked application guidance uses this function; read text never does.
    main,marker,hint=text.partition(' Say ')
    if verbosity=='low':return main
    if marker:
        return main+' Say '+hint.split('. ')[0]
    return text


def keyboard_request(key,mode):
    if mode=='sleep':return 'wake up' if key in ('Enter','Down','Up','Left','Right') else None
    if key=='Escape':
        if mode=='update_offer':return 'no'
        if mode=='update_download':return 'cancel update'
        if mode=='email_draft':return 'cancel'
        if mode=='document':return 'leave document'
        if mode=='web':return 'go back'
        if mode=='help':return 'back to topics'
        if mode=='tutorial':return 'main menu'
        return 'main menu'
    if mode=='document':return {'Up':'previous paragraph','Down':'next paragraph','Left':'previous character','Right':'next character','Enter':'new line'}.get(key)
    return {'Up':'previous','Left':'previous','Down':'next','Right':'next','Enter':'ok'}.get(key)
