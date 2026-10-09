"""Bridge between staged settings and existing Companion preferences."""
import json
from pathlib import Path
from settings_model import SettingsSession, DEFAULTS
from native_settings import NativeSettings

ACTIONS={'add_account':'add account','open_documents':'open documents folder','web_favorites':'list favorites','radio_presets':'list presets','radio_recordings':'list scheduled recordings','podcast_subscriptions':'list subscriptions','check_now':'check for updates'}

def read(path,default):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def read_text(path,default):
    try:return Path(path).read_text(encoding='utf-8').strip()
    except OSError:return default

def save_preferences(m):
    m.APP.mkdir(parents=True,exist_ok=True)
    target=m.APP/'preferences.json';temp=target.with_suffix('.writing')
    temp.write_text(json.dumps(m.PREFERENCES),encoding='utf-8');temp.replace(target)

def snapshot(m):
    from ai_voice_settings import VOICES
    voices=[] if m.TEXT_MODE else m.voice_choices()
    store=m.ProtectedStore(m.APP)
    accounts=store.accounts();selected=store.selected()
    subs=read(m.APP/'podcast-subscriptions.json',[])
    podcast_names={str(i+1)+'. '+s['name']:s['feed'] for i,s in enumerate(subs)}
    podcast_limits={str(i+1)+'. '+s['name']:s.get('download_limit','manual') for i,s in enumerate(subs)}
    engine='ai' if m.AI_SPEECH and m.AI_SPEECH.enabled else 'espeak' if m.ESPEAK_SPEECH and m.ESPEAK_SPEECH.enabled else 'windows'
    context={'engines':('windows',)+(('espeak',) if m.ESPEAK_SPEECH else ())+('ai',),'windows_voices':tuple(v[1] for v in voices if v[0]=='windows'),'espeak_voices':tuple(v[1] for v in voices if v[0]=='espeak'),'ai_voices':VOICES,'accounts':tuple(p+' '+a for p,a in accounts),'podcasts':tuple(podcast_names),'podcast_limits':podcast_limits,'podcast_feeds':podcast_names,'actions':ACTIONS}
    values=DEFAULTS|m.PREFERENCES|{'verbosity':m.VERBOSITY,'rate':str(m.SPEECH_RATE),'volume':str(m.SPEECH_VOLUME),'punctuation':m.PUNCTUATION_LEVEL,'input_mode':m.INPUT_MODE,'engine':engine,'windows_voice':m.voice.Voice.GetDescription() if not m.TEXT_MODE else '', 'espeak_voice':next((v[1] for v in voices if v[0]=='espeak' and v[2]==m.ESPEAK_VOICE_NAME),''),'ai_voice':m.AI_VOICE_NAME,'api_key':'','ai_consent':m.PREFERENCES.get('ai_consent',(m.APP/'ai-voice.account').exists() or bool(__import__('os').getenv('VOICE_COMPANION_OPENAI_KEY'))),'remove_ai':False,'email_account':(' '.join(selected) if selected else ''),'email_list_size':read_text(m.APP/'email-list-size.txt','10'),'browser':read_text(m.APP/'browser-choice.txt','firefox'),'radio_source':{'radio-browser':'radio browser','radiosure':'radio sure'}.get(read(m.APP/'radio-preferences.json',{}).get('source','all'),read(m.APP/'radio-preferences.json',{}).get('source','all')),'podcast_feed':next(iter(podcast_names),''),'podcast_limit':str(next(iter(podcast_limits.values()),'manual'))}
    return values,context

def open_settings(m,mode):
    if m.APP_WINDOW is None:
        m.speak('The settings panel requires the app window. Voice settings commands are still available.');return mode
    if m.SETTINGS_PANEL and not m.SETTINGS_PANEL.closed.is_set():return 'settings'
    values,context=snapshot(m);session=SettingsSession(values,context)
    m.SETTINGS_PANEL=NativeSettings(session,m.APP_WINDOW.settings_notices.put,lambda values:m.APP_WINDOW.settings_results.put(('general',values)),lambda key:m.APP_WINDOW.settings_results.put(('action',key))).start()
    return 'settings'

def apply_settings(m,values):
    from ai_voice_settings import load,save,remove
    values=dict(values)
    rate=int(values['rate']);volume=int(values['volume'])
    if not -10<=rate<=10 or not 0<=volume<=100:raise ValueError('Speech rate or volume is out of range.')
    choices=[] if m.TEXT_MODE else m.voice_choices()
    if values['engine']=='espeak' and m.ESPEAK_SPEECH is None:raise ValueError('eSpeak is unavailable.')
    ai=load(m.APP);key=values.get('api_key','').strip() or ai.get('key','')
    if values.get('remove_ai') and values['engine']=='ai':raise ValueError('Choose another synthesizer before removing the AI key.')
    if values['engine']=='ai' and not key:raise ValueError('Enter an AI key and enable permission to send narration to OpenAI.')
    if (values.get('api_key') or values['engine']=='ai') and not values.get('ai_consent'):raise ValueError('Confirm permission to send narration text to OpenAI.')
    if values['radio_source']=='radio sure' and not read(m.APP/'radiosure-stations.json',[]):raise ValueError('Import a RadioSure station database first.')
    session=SettingsSession(*snapshot(m));session.values.update(values)
    # Validate every populated field before any preference is applied.
    from settings_model import CATEGORIES,fields
    for category in CATEGORIES:
        for field in fields(category,session.context):
            if field.kind!='action' and field.key in values and not (field.kind=='choice' and not field.choices):session.set(field.key,values[field.key])
    if values.get('remove_ai'):
        remove(m.APP)
        if m.AI_SPEECH:m.AI_SPEECH.close()
        m.AI_SPEECH=None
    elif key and (values.get('api_key') or values['ai_voice']!=ai.get('voice','coral')):
        save(m.APP,key,values['ai_voice'],values.get('ai_consent',False))
    if values['engine']=='ai' and m.AI_SPEECH is None:m.configure_ai_speech()
    m.SPEECH_RATE=rate;m.SPEECH_VOLUME=volume;m.PUNCTUATION_LEVEL=values['punctuation'];m.VERBOSITY=values['verbosity'];m.INPUT_MODE=values['input_mode']
    if not m.TEXT_MODE:
        m.voice.Rate=rate;m.voice.Volume=volume
    m.AI_VOICE_NAME=values['ai_voice']
    selected=next((v for v in ([] if m.TEXT_MODE else m.voice_choices()) if v[0]==values['engine'] and v[1]==values[{'windows':'windows_voice','espeak':'espeak_voice','ai':'ai_voice'}[values['engine']]]),None)
    if values['engine']=='ai':selected=next((v for v in m.voice_choices('ai') if v[2]==values['ai_voice']),None) if not m.TEXT_MODE else None
    if selected:m.choose_voice(selected)
    elif not m.TEXT_MODE and values['engine']=='windows':
        if m.AI_SPEECH:m.AI_SPEECH.enabled=False
        if m.ESPEAK_SPEECH:m.ESPEAK_SPEECH.enabled=False
    for key in DEFAULTS:m.PREFERENCES[key]=values[key]
    m.PREFERENCES['ai_consent']=bool(values.get('ai_consent'))
    save_preferences(m);m.save_speech_settings()
    m.APP.mkdir(parents=True,exist_ok=True)
    (m.APP/'email-list-size.txt').write_text(values['email_list_size'],encoding='utf-8')
    if m.mail_session:
        m.mail_session.all_messages=values['email_list_size']=='all';m.mail_session.list_size=10 if m.mail_session.all_messages else int(values['email_list_size'])
    account=values.get('email_account','')
    if account:
        provider,address=account.split(' ',1);store=m.ProtectedStore(m.APP)
        if store.selected()!=(provider,address):
            store.select(provider,address);m.mail_session=None
            if m.SETTINGS_RETURN_MODE=='mailbox':m.SETTINGS_RETURN_MODE='awake'
    if m.document:m.document.dictating=m.INPUT_MODE!='commands'
    if m.email_draft:
        m.email_draft.dictating=m.INPUT_MODE!='commands';m.email_draft.automatic_dictation=m.INPUT_MODE!='commands'
    if m.web_session:
        m.web_session.choose_browser(values['browser']);m.web_session.input_mode=m.INPUT_MODE
    else:(m.APP/'browser-choice.txt').write_text(values['browser'],encoding='utf-8')
    m.media().choose_source(values['radio_source'])
    limits=values.get('podcast_limits',{})
    feed=session.context['podcast_feeds'].get(values.get('podcast_feed'))
    if feed or limits:
        subs=read(m.APP/'podcast-subscriptions.json',[]);changed=False
        for name,feed_url in session.context['podcast_feeds'].items():
            limit=str(limits.get(name,session.context['podcast_limits'].get(name,'manual')))
            if name==values.get('podcast_feed'):limit=values['podcast_limit']
            limit=int(limit) if limit.isdigit() else limit
            for show in subs:
                if show.get('feed')==feed_url and show.get('download_limit','manual')!=limit:show['download_limit']=limit;changed=True
        if changed:
            m.media()._save('podcast-subscriptions.json',subs)
            if any(show.get('download_limit','manual')!='manual' for show in subs):m.media().start_podcast_refresh()
    return True

def apply_document_defaults(m,document):
    p=m.PREFERENCES
    document.process('set font '+p.get('document_font','Calibri')+' '+str(p.get('document_size','11'))+' point')
    document.process({'single':'single space','double':'double space','one and a half':'set line spacing 1.5'}[p.get('document_spacing','single')])
    document.process('align '+('justified' if p.get('document_alignment')=='justify' else p.get('document_alignment','left')))
