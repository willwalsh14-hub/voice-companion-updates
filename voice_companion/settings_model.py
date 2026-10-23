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

CATEGORIES = tuple(name+' options' for name in ('Verbosity', 'Voice', 'Synthesizer', 'Input', 'Email', 'Word processor', 'Notepad', 'Web browser', 'Radio', 'Podcasts', 'Auto-update'))+('Startup and exit options',)
DEFAULTS = {'dictation_echo':'phrase by phrase','dictation_type':'old school','cloud_consent':False,'web_search_engine':'google','web_keep_open':False,'keep_audio_playing':False,'web_auto_read':True,'email_format':'html','pitch':'0','verbosity':'high','capital_letters':'do nothing','typing_echo':'characters','phonetic_enabled':True,'phonetic_delay':'0.5','duck_audio':True,'check_updates':True,'start_with_windows':False,'ask_before_exit':True,'ask_before_shutdown':True,'ask_before_restart':True,'email_header_names':False,'email_header_order':'from, subject, date','document_font':'Calibri','document_size':'11','document_spacing':'single','document_alignment':'left'}

# Fonts that come with Windows 11 or Microsoft Office, in alphabetical order so
# a first letter moves through them. A saved font outside this list is added.
DOCUMENT_FONTS = ('Aptos', 'Arial', 'Calibri', 'Cambria', 'Candara', 'Comic Sans MS', 'Consolas', 'Courier New',
                  'Georgia', 'Segoe UI', 'Tahoma', 'Times New Roman', 'Trebuchet MS', 'Verdana')


def font_choices(context):
    names = list(DOCUMENT_FONTS) + [str(x).strip() for x in context.get('document_fonts', ()) if str(x).strip()]
    unique = {name.casefold(): name for name in reversed(names)}
    return tuple(sorted(unique.values(), key=str.casefold))


def spoken_font(value, choices):
    """Match a spoken font name: any case, extra punctuation, or the first words only."""
    plain = lambda text: re.sub(r'[^0-9a-z]+', ' ', str(text).casefold()).strip()
    wanted = plain(value)
    if not wanted: return None
    exact = [c for c in choices if plain(c) == wanted or plain(c).replace(' ', '') == wanted.replace(' ', '')]
    if exact: return exact[0]
    starts = [c for c in choices if plain(c).startswith(wanted + ' ')]
    return starts[0] if len(starts) == 1 else None


def fields(category, context):
    if category!='Startup and exit options':category=category.removesuffix(' options')
    category={'Word processor':'Documents','Auto-update':'Updates','Notes':'Notepad'}.get(category,category)
    definitions = {
        'Verbosity':[Field('verbosity','Verbosity','choice',('high','medium','low')),Field('punctuation','Spoken punctuation','choice',('none','some','most','all')),Field('dictation_echo','Echo dictated text','choice',('phrase by phrase','at the end of a sentence','do not echo dictated text')),Field('typing_echo','Typing echo','choice',('words','characters','characters and words','none')),Field('phonetic_enabled','Delayed phonetic pronunciation','check'),Field('phonetic_delay','Phonetic delay in seconds','choice',('0.5','1','2')),Field('capital_letters','When encountering capital letters','choice',('raise pitch','lower pitch','play a sound','do nothing'))],
        'Voice':[Field('pitch','Voice pitch','choice',tuple(str(x) for x in range(-10,11))),Field('rate','Speech rate','choice',tuple(str(x) for x in range(-10,11))),Field('volume','Speech volume','choice',tuple(str(x) for x in range(101))),Field('duck_audio','Lower media volume while Companion speaks','check')],
        'Synthesizer':[Field('engine','Synthesizer','choice',tuple(context.get('engines',('SAPI',)))),Field('windows_voice','SAPI voice','choice',tuple(context.get('windows_voices',()))),Field('espeak_voice','eSpeak voice','choice',tuple(context.get('espeak_voices',()))),Field('ai_voice','AI voice','choice',tuple(context.get('ai_voices',()))),Field('api_key','AI API key; blank keeps the saved key','password'),Field('ai_consent','Allow narration text to be sent to OpenAI','check'),Field('remove_ai','Remove saved AI key','check')],
        'Input':[Field('input_mode','Command and dictation mode','choice',('mixed','commands','dictation')),Field('dictation_type','Dictation type','choice',('old school','ai-based')),Field('cloud_consent','Allow microphone audio to be sent to Azure for recognition; Old school only','check')],
        'Email':[Field('email_format','Email format','choice',('plain text','html')),Field('email_account','Sending account','choice',tuple(context.get('accounts',()))),Field('email_list_size','Messages per list','choice',('10','20','30','40','50','100','1000','all')),Field('email_header_names','Announce email header names','check'),Field('email_header_order','Spoken email headers; Space toggles; Alt+Up or Alt+Down moves','headers'),Field('add_account','Add an email account','action'),Field('google_contacts','Connect Google Contacts','action')],
        'Notepad':[Field('open_notepad','Open Notepad','action')],
        'Documents':[Field('document_font','Font for new documents','choice',font_choices(context)),Field('document_size','Font size for new documents','choice',tuple(str(x) for x in range(6,73))),Field('document_spacing','Line spacing for new documents','choice',('single','one and a half','double')),Field('document_alignment','Alignment for new documents','choice',('left','center','right','justify'))],
        'Web browser':[Field('web_search_engine','Default search engine','choice',('google','bing','brave','duckduckgo')),Field('web_keep_open','Keep browser window open','check'),Field('web_auto_read','Automatic page reading','check'),Field('browser','Guided browser','choice',('embedded Edge','Chrome')),Field('chrome_setup','Set up Chrome and the Voice Companion extension','action')],
        'Radio':[Field('keep_audio_playing','Keep audio playing in the background','check'),Field('radio_source','Station database','choice',('all','radio browser','iprd','radio sure')),Field('radio_presets','List radio presets','action'),Field('radio_recordings','List scheduled recordings','action')],
        'Podcasts':[Field('keep_audio_playing','Keep audio playing in the background','check'),Field('podcast_feed','Podcast subscription','choice',tuple(context.get('podcasts',()))),Field('podcast_limit','Automatic downloads for this subscription','combo',('manual','all','1','2','3','5','10','20','50','100','1000')),Field('podcast_subscriptions','List subscriptions','action')],
        'Startup and exit options':[Field('start_with_windows','Start with Windows','check'),Field('ask_before_exit','Ask before exiting Voice Companion','check'),Field('ask_before_shutdown','Ask before shutting down the computer','check'),Field('ask_before_restart','Ask before restarting the computer','check')],
        'Updates':[Field('check_updates','Check automatically at startup','check'),Field('check_now','Check for updates now','action')],
    }
    return definitions[category]

class SettingsSession:
    def __init__(self, values, context):
        self.original = copy.deepcopy(DEFAULTS | values)
        self.values = copy.deepcopy(DEFAULTS | values)
        # Keep the saved font in the list, even when it is not a common one.
        context = dict(context)
        saved_font = str(self.values.get('document_font') or '').strip()
        if saved_font and saved_font.casefold() not in (f.casefold() for f in DOCUMENT_FONTS):
            context['document_fonts'] = tuple(context.get('document_fonts', ())) + (saved_font,)
        self.values['capital_letters']={'with a sound':'play a sound','with a higher pitch':'raise pitch','with a lower pitch':'lower pitch'}.get(self.values['capital_letters'],self.values['capital_letters'])
        self.context = context
        if self.values.get('engine')=='windows' and 'SAPI' in context.get('engines',('SAPI',)):self.values['engine']='SAPI'
        self.values.setdefault('podcast_limits',copy.deepcopy(context.get('podcast_limits',{})))
        self.category = 0
        self.closed = False
    def category_name(self): return CATEGORIES[self.category]
    def current_fields(self): return fields(self.category_name(),self.context)
    def set(self,key,value):
        if key=='dictation_type':
            value={'ai':'ai-based','ai based':'ai-based','old-school':'old school','oldschool':'old school'}.get(str(value).lower(),str(value).lower())
        if key=='engine':
            available=next(f.choices for f in fields('Synthesizer',self.context) if f.key=='engine')
            if str(value).lower() in ('sapi','sapi voice','windows','windows speech','microsoft'):value='SAPI' if 'SAPI' in available else 'windows'
        if key=='capital_letters':value={'sound':'play a sound','with a sound':'play a sound','higher pitch':'raise pitch','with a higher pitch':'raise pitch','with a lower pitch':'lower pitch','nothing':'do nothing','off':'do nothing'}.get(str(value).lower(),str(value).lower())
        if key=='browser':
            # Stored as webview2 or chrome; older choices of Edge, Brave or Firefox are embedded Edge now.
            value={'webview2':'embedded Edge','embedded edge':'embedded Edge','edge webview2':'embedded Edge','edge':'embedded Edge','brave':'embedded Edge',
                   'firefox':'embedded Edge','chrome':'Chrome','google chrome':'Chrome'}.get(str(value).strip().lower(),value)
        if key=='web_search_engine':value={'brave search':'brave','duck duck go':'duckduckgo','google search':'google','bing search':'bing'}.get(str(value).lower(),str(value).lower())
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
            if key=='document_font' and field.kind=='choice' and value not in field.choices:
                found=spoken_font(value,field.choices)
                if found is None:raise ValueError('That font is not in the list. Choose a font such as Arial, Calibri, Times New Roman, or Verdana.')
                value=found
            if field.kind=='choice':
                found=next((c for c in field.choices if c.casefold()==value.casefold()),None)
                if found is None: raise ValueError('Choose one of the available '+field.label+' options.')
                value=found
            if key=='podcast_limit' and value not in ('manual','all') and not (value.isdigit() and 1<=int(value)<=10000): raise ValueError('Choose manual, all, or a number from 1 to 10000.')
            if key=='email_header_order':
                headers=[x.strip().lower() for x in value.split(',') if x.strip()]
                if not headers or len(headers)!=len(set(headers)) or any(x not in ('from','subject','date','size') for x in headers):raise ValueError('Enter unique headers separated by commas: from, subject, date, size. Include at least one.')
                value=', '.join(headers)
            if key=='document_font' and (not value or len(value)>100): raise ValueError('Enter a font name.')
        self.values[key]=value
        if key=='podcast_feed': self.values['podcast_limit']=str(self.values.get('podcast_limits',{}).get(value,'manual'))
        if key=='podcast_limit' and self.values.get('podcast_feed'):self.values['podcast_limits'][self.values['podcast_feed']]=value
        return field.label + (' hidden' if field.kind=='password' else ' '+('on' if value is True else 'off' if value is False else value))
    def voice_setting(self,command):
        command=command.strip().rstrip('.!?')
        aliases={'windows startup':'start with windows','start automatically with windows':'start with windows','phonetic pronunciation':'phonetic enabled','phonetics':'phonetic enabled','audio ducking':'duck audio','automatic updates':'check updates','default font size':'document size','default font':'document font','default line spacing':'document spacing','default alignment':'document alignment'}
        for alias,name in aliases.items():command=re.sub(r'^(?:set )?'+re.escape(alias)+r'\b',name,command,flags=re.I)
        # "Font Arial", "Font, Arial." or "set font to Verdana" chooses the font for new documents.
        command=re.sub(r'^(?:(?:set|use|choose|select) )?(?:the )?font size\b[\s,:;-]*','document size ',command,flags=re.I)
        command=re.sub(r'^(?:(?:set|use|choose|select) )?(?:the )?font(?: name)?(?! for new documents)\b[\s,:;-]*(?:to\b[\s,:;-]*)?','document font ',command,flags=re.I)
        command=re.sub(r'^((?:document|default) font|font for new documents)[\s,:;-]+',r'\1 ',command,flags=re.I)
        engine=re.fullmatch(r'(?:use|select|choose) (?:synthesizer )?(sapi|sapi voice|windows|windows speech|microsoft|espeak|espeak ng|ai voice)',command,re.I)
        if engine:return 'engine',{'sapi':'SAPI','sapi voice':'SAPI','windows speech':'windows','microsoft':'windows','espeak ng':'espeak','ai voice':'ai'}.get(engine[1].lower(),engine[1].lower())
        from speech_controls import request
        speech=request(command.lower())
        if speech and speech[2] is not None:
            key,operation,amount=speech
            value=int(self.values.get(key,0))
            value=amount if operation=='set' else value+amount
            return key,str(max(-10 if key in ('rate','pitch') else 0,min(10 if key in ('rate','pitch') else 100,value)))
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
